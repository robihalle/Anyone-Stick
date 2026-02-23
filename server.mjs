#!/usr/bin/env node
// Anyone Stick — Circuit Manager sidecar
// Implements VPNManager/StateManager based circuit pools (feature/vpn-state-manager)

import express from "express";

// ISO country-code → human name (built-in Intl API)
function _isoName(cc) {
  if (!cc) return "";
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(cc.toUpperCase()); }
  catch { return ""; }
}
import fs from "fs";
import net from "net";

import * as Anyone from "@anyone-protocol/anyone-client";

// -----------------------------------------------------------------------------
// Config (env)
// -----------------------------------------------------------------------------
const HOST = process.env.MGR_HOST || "127.0.0.1";
const PORT = Number(process.env.MGR_PORT || 8787);

const COOKIE_PATH = process.env.MGR_COOKIE_PATH || "/var/lib/anon/control_auth_cookie";
const CONTROL_HOST = process.env.MGR_CONTROL_HOST || "127.0.0.1";
const CONTROL_PORT = Number(process.env.MGR_CONTROL_PORT || 9051);

// --- Persistent ControlPort (reusable authenticated socket) ---
let _pCtrl = null;
function _getPersistentCtrl() {
  if (_pCtrl && _pCtrl._authenticated) return _pCtrl;
  const PCtrl = {
    _host: process.env.MGR_CONTROL_HOST || "127.0.0.1",
    _port: Number(process.env.MGR_CONTROL_PORT || 9051),
    _cookiePath: process.env.MGR_COOKIE_PATH || "/var/lib/anon/control_auth_cookie",
    _sock: null, _authenticated: false, _queue: [], _buf: "", _connecting: false,
    async ensureConnected() {
      if (this._sock && !this._sock.destroyed && this._authenticated) return;
      if (this._connecting) {
        await new Promise(r => {
          const iv = setInterval(() => {
            if (this._authenticated) { clearInterval(iv); r(); }
          }, 50);
        });
        return;
      }
      this._connecting = true; this._authenticated = false; this._buf = "";
      await new Promise((resolve, reject) => {
        const sock = net.connect({ host: this._host, port: this._port });
        this._sock = sock;
        const authTimer = setTimeout(() => {
          this._connecting = false;
          reject(new Error("pCtrl auth timeout"));
          try { sock.destroy(); } catch {}
        }, 8000);
        sock.on("connect", () => {
          try {
            const hex = fs.readFileSync(this._cookiePath).toString("hex");
            sock.write("AUTHENTICATE " + hex + "\r\n");
          } catch(e) { clearTimeout(authTimer); this._connecting = false; reject(e); }
        });
        sock.on("data", (chunk) => {
          this._buf += chunk.toString("utf-8");
          if (!this._authenticated) {
            if (this._buf.includes("250 OK")) {
              this._authenticated = true;
              this._connecting = false;
              this._buf = "";
              clearTimeout(authTimer);
              resolve();
            } else if (this._buf.includes("515") || this._buf.includes("550")) {
              this._connecting = false;
              clearTimeout(authTimer);
              reject(new Error("pCtrl auth failed: " + this._buf.trim()));
            }
            return;
          }
          this._processReplies();
        });
        sock.on("error", (e) => {
          this._authenticated = false;
          this._connecting = false;
          for (const q of this._queue) { clearTimeout(q.timer); q.reject(e); }
          this._queue = [];
        });
        sock.on("close", () => {
          this._authenticated = false;
          this._connecting = false;
          this._sock = null;
        });
      });
    },
    _processReplies() {
      while (this._queue.length > 0) {
        const endOk = this._buf.indexOf("250 OK\r\n");
        if (endOk < 0) break;
        const endIdx = endOk + "250 OK\r\n".length;
        const reply = this._buf.slice(0, endIdx);
        this._buf = this._buf.slice(endIdx);
        const q = this._queue.shift();
        clearTimeout(q.timer);
        q.resolve(reply);
      }
    },
    async cmd(cmdLine, timeoutMs) {
      timeoutMs = timeoutMs || 5000;
      await this.ensureConnected();
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          const idx = this._queue.findIndex(q2 => q2.timer === timer);
          if (idx >= 0) this._queue.splice(idx, 1);
          reject(new Error("pCtrl timeout: " + cmdLine));
        }, timeoutMs);
        this._queue.push({ cmd: cmdLine, resolve, reject, timer });
        try { this._sock.write(cmdLine + "\r\n"); }
        catch(e) { clearTimeout(timer); this._queue.pop(); this._authenticated = false; reject(e); }
      });
    }
  };
  _pCtrl = PCtrl;
  return _pCtrl;
}


const DEFAULT_HOPCOUNT = Number(process.env.MGR_HOPCOUNT || 3);

// IMPORTANT: internal representation is lower-case (matches StateManager keys and VPNManager comparisons)
const DEFAULT_EXIT_COUNTRIES = String(process.env.MGR_EXIT_COUNTRIES || "")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean);

const TARGETS = String(process.env.MGR_TARGETS || "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const MIN_CIRCS = Math.max(1, Number(process.env.MGR_MIN_CIRCS || 1));
const MAX_CIRCS = Math.max(MIN_CIRCS, Number(process.env.MGR_MAX_CIRCS || 3));

// -----------------------------------------------------------------------------
// State
// -----------------------------------------------------------------------------
let control = null;
let stateManager = null;
let vpnManager = null;

let READY = false;
let INIT_ERROR = "";
let INIT_LAST_TS = 0;

let CURRENT_HOPCOUNT = null; // number|null
let CURRENT_EXIT_COUNTRIES = null; // string[]|null   (empty array == AUTO)

let _relayByFp = new Map();
let _countryByIp = new Map();


// -----------------------------------------------------------------------------
// Direct ControlPort GETINFO helper (bypasses library quirks)
// -----------------------------------------------------------------------------
async function _cpGetInfo(cmdLine, timeoutMs=1500){
  return await new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: CONTROL_HOST, port: CONTROL_PORT });
    const chunks = [];
    let done = false;

    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch {}
      reject(new Error(`ControlPort timeout after ${timeoutMs}ms`));
    }, timeoutMs);

    function finish(err, data){
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { sock.destroy(); } catch {}
      if (err) reject(err);
      else resolve(data);
    }

    sock.on("error", (e) => finish(e));
    sock.on("data", (d) => chunks.push(d));

    sock.on("connect", () => {
      try{
        const cookieHex = fs.readFileSync(COOKIE_PATH).toString("hex");
        sock.write(`AUTHENTICATE ${cookieHex}\r\n`);
        sock.write(`${cmdLine}\r\n`);
        sock.write("QUIT\r\n");
      } catch(e){
        finish(e);
      }
    });

    sock.on("end", () => {
      const txt = Buffer.concat(chunks).toString("utf8");
      finish(null, txt);
    });

    sock.on("close", () => {
      if (done) return;
      const txt = Buffer.concat(chunks).toString("utf8");
      finish(null, txt);
    });
  });
}

function _parseIpToCountry(txt){
  const t = String(txt || "");
  let m = t.match(/ip-to-country\/[0-9a-fA-F\.:]+=\s*([A-Za-z]{2})/);
  if (m && m[1]) return m[1].toUpperCase();

  m = t.match(/ip-to-country[^=\r\n]*=\s*([A-Za-z]{2})/);
  if (m && m[1]) return m[1].toUpperCase();

  for (const line0 of t.split(/\r?\n/)){
    const line = String(line0||"").trim();
    const mm = line.match(/ip-to-country\/[0-9a-fA-F\.:]+=\s*([A-Za-z]{2})/);
    if (mm && mm[1]) return mm[1].toUpperCase();
    if (/^[A-Za-z]{2}$/.test(line)) return line.toUpperCase();
  }
  return "";
}

async function _countryForIp(ip){
  if (!ip) return "";
  const now = Date.now();
  const hit = _countryByIp.get(ip);
  if (hit && (now - hit.ts) < 600000) return hit.code || "";

  // Try persistent control first (fast, no new socket)
  try {
    const pc = _getPersistentCtrl();
    const txt = await pc.cmd("GETINFO ip-to-country/" + ip, 3000);
    const cc = _parseIpToCountry(txt);
    if (cc) { _countryByIp.set(ip, { code: cc, ts: now }); return cc; }
  } catch {}

  // Fallback: SDK control.msg
  try {
    const cc = await _countryFromControlAsync(ip);
    if (cc) { _countryByIp.set(ip, { code: cc, ts: now }); return cc; }
  } catch {}

  // Fallback: raw socket (original)
  try {
    const raw = await _cpGetInfo("GETINFO ip-to-country/" + ip);
    const cc = _parseIpToCountry(raw);
    if (cc) { _countryByIp.set(ip, { code: cc, ts: now }); return cc; }
  } catch {}
  return "";
}

// Batch-resolve multiple IPs via persistent control (much faster than serial)
async function _batchResolveCountries(ips) {
  if (!ips || !ips.length) return;
  const unknown = ips.filter(ip => {
    const hit = _countryByIp.get(ip);
    return !hit || (Date.now() - hit.ts > 600000);
  });
  if (!unknown.length) return;
  const now = Date.now();
  try {
    const pc = _getPersistentCtrl();
    // Process in batches of 20 to avoid overwhelming the socket
    for (let i = 0; i < unknown.length; i += 20) {
      const batch = unknown.slice(i, i + 20);
      const promises = batch.map(ip =>
        pc.cmd("GETINFO ip-to-country/" + ip, 4000)
          .then(txt => {
            const cc = _parseIpToCountry(txt);
            if (cc) _countryByIp.set(ip, { code: cc, ts: now });
          })
          .catch(() => {})
      );
      await Promise.all(promises);
    }
  } catch(e) {
    console.error("[batch-resolve] error:", e?.message || e);
  }
}



// Resolve country synchronously via ControlPort
function _countryFromControl(ip) {
  if (!ip) return "";
  try {
    const rep = control.msg(`GETINFO ip-to-country/${ip}`);
    const txt = String(rep || "");

    // Typical reply lines:
    // 250-ip-to-country/<ip>=at
    // 250 ip-to-country/<ip>=at
    // Sometimes just "at" on a line (rare)
    const m1 = txt.match(/ip-to-country\/[0-9a-fA-F\.:]+\s*=\s*([A-Za-z]{2})/);
    if (m1 && m1[1]) return m1[1].toUpperCase();

    const m2 = txt.match(/ip-to-country\/[0-9a-fA-F\.:]+=\s*([A-Za-z]{2})/);
    if (m2 && m2[1]) return m2[1].toUpperCase();

    const m3 = txt.match(/ip-to-country[^=\r\n]*=\s*([A-Za-z]{2})/);
    if (m3 && m3[1]) return m3[1].toUpperCase();

    const lines = txt.split(/\r?\n/);
    for (const line0 of lines) {
      const line = String(line0 || "").trim();
      // e.g. "250-ip-to-country/1.2.3.4=at"
      const mm = line.match(/ip-to-country\/[0-9a-fA-F\.:]+=\s*([A-Za-z]{2})/);
      if (mm && mm[1]) return mm[1].toUpperCase();
      if (/^[A-Za-z]{2}$/.test(line)) return line.toUpperCase();
    }
  } catch {}
  return "";
}


// Async country resolver (handles Promise-returning control.msg)
async function _countryFromControlAsync(ip){
  if (!ip) return "";
  try{
    const rep = control.msg(`GETINFO ip-to-country/${ip}`);
    const txt = String(await Promise.resolve(rep) || "");

    // Typical reply: 250-ip-to-country/<ip>=at
    const m1 = txt.match(/ip-to-country\/[0-9a-fA-F\.:]+=\s*([A-Za-z]{2})/);
    if (m1 && m1[1]) return m1[1].toUpperCase();

    const m2 = txt.match(/ip-to-country[^=\r\n]*=\s*([A-Za-z]{2})/);
    if (m2 && m2[1]) return m2[1].toUpperCase();

    for (const line0 of txt.split(/\r?\n/)){
      const line = String(line0||"").trim();
      const mm = line.match(/ip-to-country\/[0-9a-fA-F\.:]+=\s*([A-Za-z]{2})/);
      if (mm && mm[1]) return mm[1].toUpperCase();
      if (/^[A-Za-z]{2}$/.test(line)) return line.toUpperCase();
    }
  } catch {}
  return "";
}



// ------------------------------
// VPN Circuit → Hops → (IP, Country) helpers
// ------------------------------
function _normFp(fp){
  let x = String(fp || "").trim();
  if (x.startsWith("$")) x = x.slice(1);
  return x.toUpperCase();
}

function _hopRole(i, n){
  if (i === 0) return "entry";
  if (i === n - 1) return "exit";
  return "middle";
}

function _isMapLike(x){
  return x && typeof x === "object"
    && typeof x.size === "number"
    && typeof x.get === "function"
    && typeof x.entries === "function";
}

function _mapValues(x){
  try {
    if (_isMapLike(x)) return Array.from(x.values());
  } catch {}
  return [];
}

function _parsePathString(pathStr){
  const parts = String(pathStr || "").split(",").map(x => x.trim()).filter(Boolean);
  return parts.map(tok => {
    let fp = tok, nickname = "";
    if (tok.includes("~")){
      const a = tok.split("~");
      fp = a[0];
      nickname = a.slice(1).join("~") || "";
    }
    return { fingerprint: _normFp(fp), nickname: String(nickname || "") };
  }).filter(r => r.fingerprint);
}

function _extractRelaysFromCircuit(c){
  if (!c) return [];
  if (Array.isArray(c.relays) && c.relays.length) return c.relays;
  if (Array.isArray(c.hops) && c.hops.length) return c.hops;

  if (typeof c.path === "string" && c.path.trim()) return _parsePathString(c.path);
  if (Array.isArray(c.path) && c.path.length){
    if (typeof c.path[0] === "string") return _parsePathString(c.path.join(","));
    return c.path;
  }

  if (typeof c.route === "string" && c.route.trim()) return _parsePathString(c.route);
  if (Array.isArray(c.route) && c.route.length) return c.route;

  return [];
}

function _relayIpFromObj(r){
  if (!r) return "";
  return String(r.ip || r.address || r.ipv4 || r.ipv4Address || r.or_address || r.orAddr || "").trim();
}

function _buildRelayIndex(){
  // Build fp -> relay object map from stateManager.allRelays (Map or array)
  const idx = new Map();
  try{
    const sm = stateManager || (vpnManager ? vpnManager.stateManager : null);
    const all = sm ? sm.allRelays : null;

    const arr = [];
    if (_isMapLike(all)) arr.push(...Array.from(all.values()));
    else if (Array.isArray(all)) arr.push(...all);

    for (const r of arr){
      const fp = _normFp(r?.fingerprint || r?.fp || "");
      if (!fp) continue;
      idx.set(fp, r);
    }
  } catch {}
  return idx;
}

function _pickNewestCircuit(){
  // Prefer stateManager.circuits, fallback vpnManager.circuits
  const sm = stateManager || (vpnManager ? vpnManager.stateManager : null);
  const cs1 = sm ? sm.circuits : null;
  const cs2 = vpnManager ? vpnManager.circuits : null;

  const values = [];
  values.push(..._mapValues(cs1));
  if (!values.length) values.push(..._mapValues(cs2));

  if (!values.length) return null;

  // Heuristic: prefer BUILT/READY, else take last element
  function stateStr(c){
    return String(c?.state || c?.status || c?.circState || "").toUpperCase();
  }
  const built = values.filter(c => ["BUILT","READY","ESTABLISHED","OPEN"].includes(stateStr(c)));
  const pool = built.length ? built : values;

  // Prefer newest by timeCreated/createdAt if present
  pool.sort((a,b) => {
    const ta = +new Date(a?.timeCreated || a?.createdAt || a?.created_at || 0);
    const tb = +new Date(b?.timeCreated || b?.createdAt || b?.created_at || 0);
    return tb - ta;
  });

  return pool[0] || null;
}

async function _enrichHopsAsync(hops){
  const relayIdx = _buildRelayIndex();
  const out = (Array.isArray(hops) ? hops : []).map(h => ({...h}));

  for (const h of out){
    h.fingerprint = _normFp(h.fingerprint || "");

    // Fill IP from relay index if missing
    if ((!h.ip || h.ip === "") && h.fingerprint){
      const r = relayIdx.get(h.fingerprint);
      const ip = _relayIpFromObj(r);
      if (ip) h.ip = ip;
    }

    // Fill country via controlport (async-safe)
    if ((!h.country_code || h.country_code === "") && h.ip){
      const cc = await _countryForIp(h.ip);
      if (cc) h.country_code = cc;
    }
  }
  return out;
}


async function _circuitToHops(c){
  const relays = _extractRelaysFromCircuit(c);
  const hops = relays.map((r, i, arr) => ({
    role: _hopRole(i, arr.length),
    fingerprint: _normFp(r?.fingerprint || r?.fp || ""),
    nickname: String(r?.nickname || r?.name || ""),
    ip: String(r?.ip || r?.address || r?.ipv4 || ""),
    country_code: String(r?.country_code || r?.countryCode || r?.country || "").toUpperCase()
  }));
  return await _enrichHopsAsync(hops);
}



// Resolve country via ControlPort GETINFO ip-to-country/<ip>



let _cacheCircuits = [];
let _cacheRefreshing = false;
let _cacheTs = 0;
let _cacheTimer = null;
let _circuitFirstSeen = new Map();  // Track when circuits were first seen
let _rebuilding = false;
let _pendingRebuild = null;  // queued rebuild reason when one is already running
let _bootstrapping = false;

// Rotation
let rotation = {
  enabled: true,
  intervalSeconds: 600,
  variancePercent: 20,
};
let _rotationTimer = null;
  rotation.nextRotationTs = 0;

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function effectiveHopCount() {
  const hc = CURRENT_HOPCOUNT ?? DEFAULT_HOPCOUNT;
  return hc === 2 ? 2 : 3;
}

function effectiveExitCountriesInternal() {
  if (Array.isArray(CURRENT_EXIT_COUNTRIES)) return CURRENT_EXIT_COUNTRIES;
  return DEFAULT_EXIT_COUNTRIES;
}

function effectiveExitCountriesForStatus() {
  const eff = effectiveExitCountriesInternal();
  return (eff || []).map((c) => String(c || "").toUpperCase()).filter(Boolean);
}

function exitCountriesForBuilding() {
  const eff = effectiveExitCountriesInternal();
  if (eff && eff.length) return eff; // already lower-case

  // AUTO / unrestricted → use all available countries (exits)
  try {
    if (stateManager && typeof stateManager.getAvailableCountries === "function") {
      const avail = stateManager.getAvailableCountries() || [];
      return avail.map((c) => String(c || "").trim().toLowerCase()).filter(Boolean);
    }
  } catch {}

  // Fallback until StateManager is ready (keeps VPNManager from stalling)
  return ["us", "de", "nl", "fr"].filter(Boolean);
}

function buildTargets(hops){
  // Determine hopCount
  const hopCount = (hops === 2 ? 2 : 3);

  // Determine desired exits (LOWERCASE for VPNManager matching)
  let exits = [];

  // CURRENT_EXIT_COUNTRIES: [] means AUTO (unrestricted)
  if (Array.isArray(CURRENT_EXIT_COUNTRIES)) {
    exits = CURRENT_EXIT_COUNTRIES.map(x => String(x || "").trim().toLowerCase()).filter(Boolean);
  } else if (Array.isArray(DEFAULT_EXIT_COUNTRIES)) {
    exits = DEFAULT_EXIT_COUNTRIES.map(x => String(x || "").trim().toLowerCase()).filter(Boolean);
  }

  // AUTO: expand to all currently available exit countries (StateManager keys are lower-case)
  if (exits.length === 0 && stateManager && typeof stateManager.getAvailableCountries === "function") {
    exits = (stateManager.getAvailableCountries() || []).map(c => String(c || "").trim().toLowerCase()).filter(Boolean);
  }

  // Fallback to prevent empty list (VPNManager requires non-empty exitCountries to pick exits)
  if (exits.length === 0) exits = ["us","de","nl","fr"];

  return (TARGETS.length ? TARGETS : ["ip-api.com","api.ipify.org","ipinfo.io"]).map(address => ({
    address,
    exitCountries: exits,
    minCircuits: MIN_CIRCS,
    maxCircuits: MAX_CIRCS,
    hopCount
  }));
}

async function waitForPort(host, port, timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const ok = await new Promise((resolve) => {
      const sock = net.createConnection({ host, port });
      sock.once("connect", () => {
        sock.end();
        resolve(true);
      });
      sock.once("error", () => resolve(false));
      sock.setTimeout(1500, () => {
        try { sock.destroy(); } catch {}
        resolve(false);
      });
    });
    if (ok) return true;
    await sleep(300);
  }
  throw new Error(`ControlPort not reachable at ${host}:${port}`);
}

async function waitForCookie(path, timeoutMs = 60000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      fs.accessSync(path, fs.constants.R_OK);
      return true;
    } catch {}
    await sleep(300);
  }
  throw new Error(`Cookie not readable: ${path}`);
}

async function authenticateWithCookie(ctrl){
  const cookie = fs.readFileSync(COOKIE_PATH);
  const hex = cookie.toString("hex");

  // msgAsync() only WRITES. We MUST CONSUME the auth reply from defaultQueue,
  // otherwise RelayManager.getRelays() will pop the leftover "250 OK" and crash.
  await ctrl.msgAsync(`AUTHENTICATE ${hex}`);

  const response = await Promise.race([
    ctrl.defaultQueue?.pop?.(),
    new Promise((_, reject) => setTimeout(() => reject(new Error("Timeout waiting for AUTHENTICATE response")), 10000))
  ]);

  if (typeof response === "string" && response.startsWith("250 OK")) {
    ctrl.isAuthenticated = true;
    console.log("ControlPort authentication successful");
    return;
  }
  if (typeof response === "string" && response.startsWith("515")) {
    throw new Error("ControlPort authentication failed");
  }
  throw new Error(`Unexpected AUTHENTICATE response: ${response}`);
}


// -----------------------------------------------------------------------------
// Robust fallback: GETINFO circuit-status (multiline) via raw socket
// Many control libs time out on multiline replies (250+ ... 250 OK).
// -----------------------------------------------------------------------------
function _parseCircuitStatusLines(lines){
  const out = [];
  for (const ln of (lines || [])){
    const line = String(ln || "").trim();
    if (!line) continue;

    // Expected: "<id> <STATUS> <PATH> KEY=VAL KEY=VAL ..."
    const parts = line.split(/\s+/);
    if (parts.length < 2) continue;

    const id = parts[0];
    const status = parts[1] || "";
    const pathTok = (parts[2] || "");

    let purpose = "";
    for (let i=3;i<parts.length;i++){
      const kv = parts[i];
      const eq = kv.indexOf("=");
      if (eq > 0){
        const k = kv.slice(0,eq).toUpperCase();
        const v = kv.slice(eq+1);
        if (k === "PURPOSE") purpose = v;
      }
    }

    // Parse relays from PATH token
    let relays = [];
    if (pathTok && pathTok !== "0" && pathTok !== "UNKNOWN"){
      relays = String(pathTok)
        .split(",")
        .map(x => x.trim())
        .filter(Boolean)
        .map(x => {
          let y = x;
          if (y.startsWith("$")) y = y.slice(1);
          const til = y.indexOf("~");
          const fp = (til >= 0 ? y.slice(0,til) : y).trim();
          const nick0 = (til >= 0 ? y.slice(til+1) : "").trim();

          // best-effort enrich nickname from relay index if missing
          let nick = nick0;
          if (!nick && fp && _relayByFp && typeof _relayByFp.get === "function"){
            const r = _relayByFp.get(fp);
            if (r && r.nickname) nick = String(r.nickname);
          }

          return { fingerprint: fp, nickname: nick };
        });
    }

    out.push({
      id,
      status,
      purpose: purpose || "GENERAL",
      relays
    });
  }
  return out;
}

async function rawCircuitStatus(timeoutMs=12000){
  const cookie = fs.readFileSync(COOKIE_PATH);
  const hex = cookie.toString("hex");

  const cmd =
    `AUTHENTICATE ${hex}\r\n` +
    `GETINFO circuit-status\r\n` +
    `QUIT\r\n`;

  return await new Promise((resolve, reject) => {
    const sock = net.connect({ host: CONTROL_HOST, port: CONTROL_PORT });
    let buf = "";
    let done = false;

    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch {}
      reject(new Error("Timeout waiting for raw circuit-status"));
    }, timeoutMs);

    sock.on("connect", () => {
      try { sock.write(cmd); } catch {}
    });

    sock.on("data", (chunk) => {
      buf += chunk.toString("utf-8");
      // Once we see 250 OK, we can end
      if (buf.includes("\r\n250 OK\r\n") || buf.endsWith("\n250 OK\n") || buf.includes("\n250 OK\r\n")){
        try { sock.end(); } catch {}
      }
    });

    sock.on("error", (e) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      reject(e);
    });

    sock.on("close", () => {
      if (done) return;
      done = true;
      clearTimeout(timer);

      const lines = buf.split(/\r?\n/);
      const data = [];
      let inBlock = false;

      for (const ln of lines){
        if (ln.startsWith("250+circuit-status=")){
          inBlock = true;
          continue;
        }
        if (inBlock){
          if (ln.startsWith("250 OK")) break;
          // Some control ports may prefix lines with "250-" in other GETINFOs; be tolerant:
          const clean = ln.startsWith("250-") ? ln.slice(4) : ln;
          if (clean.trim()) data.push(clean);
        }
      }

      resolve(data);
    });
  });
}

async function controlCircuitStatusSafe(){
  // Try library first
  try{
    if (control && typeof control.circuitStatus === "function"){
      return await control.circuitStatus();
    }
  } catch (e){
    console.error("[cache] control.circuitStatus failed:", e?.message || e);
  }

  // Fallback: raw GETINFO circuit-status
  try{
    const lines = await rawCircuitStatus(12000);
    return _parseCircuitStatusLines(lines);
  } catch (e){
    console.error("[cache] rawCircuitStatus failed:", e?.message || e);
    return [];
  }
}


// -----------------------------------------------------------------------------
// ControlPort command helper: msgAsync + consume defaultQueue reply (prevents queue clog)
// -----------------------------------------------------------------------------
async function _ctrlCmd(cmdLine, timeoutMs=5000){
  if (!control) throw new Error("control_not_ready");
  await control.msgAsync(cmdLine);

  // Pop reply so the queue never clogs (important for later SETCONF etc.)
  const reply = await Promise.race([
    control.defaultQueue?.pop?.(),
    new Promise((_, reject) => setTimeout(() => reject(new Error(`Timeout waiting for reply: ${cmdLine}`)), timeoutMs))
  ]);

  return reply;
}

function _enrichCircuitSync(c) {
  if (!c || !Array.isArray(c.hops)) return c;
  const hops = c.hops.map((h) => ({ ...h }));
  for (const h of hops) {
    const fp = String(h.fingerprint || "").toUpperCase();
    const r = _relayByFp.get(fp);
    if (r) {
      h.nickname = h.nickname || r.nickname || "";
      h.ip = h.ip || r.ip || "";
      const cc = (r.country || r.country_code || "").toUpperCase();
      if (cc && cc.length === 2) {
        h.country_code = cc;
        h.country_name = h.country_name || "";
      }
    }
    if ((!h.country_code || h.country_code === "") && h.ip) {
      h.country_code = _countryFromControl(h.ip);
      const _cv = _countryByIp.get(h.ip); const cc2 = String((_cv && typeof _cv === "object" && _cv.code) ? _cv.code : (_cv || "")).toUpperCase();
      if (cc2 && cc2.length === 2) h.country_code = cc2;
    }
  }
  c.hops = hops;
  return c;
}

function _refreshRelayIndex() {
  if (!stateManager || typeof stateManager.getRelays !== "function") return;
  try {
    const relays = stateManager.getRelays() || [];
    const byFp = new Map();
    for (const r of relays) {
      if (!r) continue;
      const fp = String(r.fingerprint || "").toUpperCase();
      if (!fp) continue;
      byFp.set(fp, r);
      if (r.ip) {
        const cc =
          (r.country ||
           r.country_code ||
           r.countryCode ||
           r?.location?.countryCode ||
           "").toString().toUpperCase();
        if (cc && cc.length === 2) {
          _countryByIp.set(r.ip, { code: cc, ts: Date.now() });
        }
      }
    }
    _relayByFp = byFp;
  } catch {
    // best-effort
  }
}

function _isBuiltLikeStatus(st){
  const u = String(st || "").toUpperCase();
  return u.startsWith("BUILT") || u.startsWith("READY");
}

function hopRole(i, n){
  if (!n || n <= 1) return "exit";
  if (i === 0) return "entry";
  if (i === n - 1) return "exit";
  return "middle";
}

function circuitsToApi(circuits){
  const now = Date.now()/1000;
  const out = [];

  const getId = (c) => (c?.circuitId ?? c?.id ?? c?.circuit_id ?? c?.circuitID ?? c?.CircuitID);
  const getState = (c) => (c?.state ?? c?.status ?? c?.State ?? c?.Status ?? "");
  const getPurpose = (c) => (c?.purpose ?? c?.Purpose ?? "");
  const getCreated = (c) => (c?.timeCreated ?? c?.time_created ?? c?.createdAt ?? c?.created_at ?? c?.time_created_at);
  const getRelays = (c) => {
    // Accept arrays, arrays-of-strings, or path/route strings like "fp~nick,fp~nick"
    const v = (c?.relays ?? c?.hops ?? c?.circuitPath ?? c?.circuit_path ?? c?.path ?? c?.route ?? []);
    if (Array.isArray(v)) {
      // If it is an array of strings, parse them into {fingerprint,nickname}
      if (v.length && typeof v[0] === "string") return _parsePathString(v.join(","));
      return v;
    }
    if (typeof v === "string" && v.trim()) return _parsePathString(v);
    return [];
  };

  for (const c of (circuits || [])) {
    const purpose = String(getPurpose(c) || "").toUpperCase();
    // keep old behavior: include empty/unknown purpose, but filter non-GENERAL if explicitly present
    if (purpose && purpose !== "GENERAL") continue;

    const st = String(getState(c) || "");
    const idv = getId(c);
    const idStr = (idv === undefined || idv === null) ? "" : String(idv);

    // Track first-seen time for this circuit (for age calculation)
    if (idStr && !_circuitFirstSeen.has(idStr)) {
      _circuitFirstSeen.set(idStr, now);
    }
    const firstSeen = idStr ? _circuitFirstSeen.get(idStr) : now;

    // Try to get actual creation time from circuit, fallback to first-seen
    const tcRaw = getCreated(c);
    let tc = firstSeen;
    try{
      if (tcRaw instanceof Date && !isNaN(tcRaw.valueOf())){
        tc = tcRaw.getTime()/1000;
      } else if (typeof tcRaw === "number" && isFinite(tcRaw)){
        tc = (tcRaw > 10**12) ? (tcRaw/1000) : tcRaw;
      } else if (typeof tcRaw === "string" && tcRaw){
        const d = new Date(tcRaw);
        if (!isNaN(d.valueOf())) tc = d.getTime()/1000;
      }
    } catch {}

    const relays = getRelays(c);

    out.push({
      id: idStr,
      status: st,
      purpose: purpose || "GENERAL",
      first_seen_ts: tc,
      age_seconds: Math.max(0, now - tc),
      hops: (relays || []).map((r, idx, arr) => ({
        role: hopRole(idx, arr.length),
        fingerprint: r?.fingerprint || r?.fp || r?.Fingerprint || "",
        nickname: r?.nickname || r?.name || r?.Nickname || "",
        ip: "",
        country_code: "",
        country_name: ""
      }))
    });
  }
  return out;
}




async function DEBUG_CIRCUIT_STATUS(){
  try {
    const st = await controlCircuitStatusSafe();
    console.log("=== DEBUG circuitStatus RAW ===");
    console.log(JSON.stringify(st, null, 2));
  } catch(e){
    console.log("DEBUG error:", e);
  }
}

async function _refreshCircuitCache(){
  console.error("[cache] refresh begin");
  if (_cacheRefreshing) return;
  _cacheRefreshing = true;

  try{
    _refreshRelayIndex();

    // Pre-warm country cache for all relay IPs in bulk
    try {
      const allIps = [];
      const tmpCircs2 = [];
      try {
        if (vpnManager) {
          if (typeof vpnManager.listCircuits === "function") tmpCircs2.push(...(await vpnManager.listCircuits()));
          else if (typeof vpnManager.getCircuits === "function") tmpCircs2.push(...(await vpnManager.getCircuits()));
          else if (Array.isArray(vpnManager.circuits)) tmpCircs2.push(...vpnManager.circuits);
        }
      } catch {}
      for (const c of tmpCircs2) {
        const r = c.relays || c.path || c.hops || [];
        if (!Array.isArray(r)) continue;
        for (const relay of r) {
          const ip = relay.ip || relay.address || "";
          if (ip && !_countryByIp.has(ip)) allIps.push(ip);
        }
      }
      if (allIps.length > 0) await _batchResolveCountries(allIps);
    } catch {}

    // 1) Get circuits from the most reliable source in vpn-state-manager:
    //    Prefer vpnManager/state, fallback to control.circuitStatus if available.
    let circs = [];
    try{
      if (vpnManager){
        // common names across implementations
        if (typeof vpnManager.listCircuits === "function") circs = await vpnManager.listCircuits();
        else if (typeof vpnManager.getCircuits === "function") circs = await vpnManager.getCircuits();
        else if (Array.isArray(vpnManager.circuits)) circs = vpnManager.circuits;
        else if (vpnManager.state && Array.isArray(vpnManager.state.circuits)) circs = vpnManager.state.circuits;
        else if (vpnManager._state && Array.isArray(vpnManager._state.circuits)) circs = vpnManager._state.circuits;
      }
    } catch (e){
      console.error("[cache] vpnManager circuits fetch failed:", e?.message || e);
    }

    try{
      if ((!Array.isArray(circs) || !circs.length) && control && typeof control.circuitStatus === "function"){
        circs = await controlCircuitStatusSafe();
      }
    } catch (e){
      console.error("[cache] control.circuitStatus failed:", e?.message || e);
    }

    // Ensure array
    if (!Array.isArray(circs)) circs = [];

    // 2) Normalize -> API format (your circuitsToApi is already robust)
    let out = [];
    try{
      out = circuitsToApi(circs) || [];
    } catch (e){
      console.error("[cache] circuitsToApi failed:", e?.message || e);
      out = [];
    }

    // 3) Accept READY as "built-like" too (vpn-state-manager often uses READY)
    out = out.filter(c => {
      const u = String(c?.status || "").toUpperCase();
      // Some implementations report BUILT/READY, others OPEN/ESTABLISHED.
      return (
        u.startsWith("BUILT") ||
        u.startsWith("READY") ||
        u === "OPEN" ||
        u === "ESTABLISHED" ||
        u.startsWith("OPEN") ||
        u.startsWith("ESTABLISH")
      );
    });

    out.sort((a,b) => (a.first_seen_ts||0) - (b.first_seen_ts||0));

    // 4) Enrich ALL circuits (ip/country/nickname)
    for (let i = 0; i < out.length; i++) out[i] = _enrichCircuitSync(out[i]);

    for (let i = 0; i < out.length; i++) {
      const c = out[i];
      if (!c || !Array.isArray(c.hops)) continue;
      for (let h = 0; h < c.hops.length; h++) {
        const hop = c.hops[h];
        if (!hop) continue;
        if ((!hop.country_code || hop.country_code === "") && hop.ip) {
          const cc = await _countryForIp(hop.ip);
          if (cc) {
            hop.country_code = cc;
            hop.country_name = hop.country_name || _isoName(cc) || "";
          }
        }
        if (hop.country_code && (!hop.country_name || hop.country_name === "")) {
          hop.country_name = _isoName(hop.country_code) || "";
        }
      }
    }

    _cacheCircuits = out;
    _cacheTs = Date.now()/1000;
    console.error("[cache] refresh done; cached=" + (_cacheCircuits?.length||0));
  } catch (e){
    // IMPORTANT: log, don't swallow silently
    console.error("[cache] _refreshCircuitCache crashed:", e?.message || e);
  } finally {
    _cacheRefreshing = false;
  }
}


function _startCircuitCache() {
  if (_cacheTimer) return;
  _cacheTimer = setInterval(async () => {
    try {
      _refreshRelayIndex();
      await _refreshCircuitCache();
    } catch {}
  }, 10000);
}

// --- Fast background relay IP → country resolver ---
// Replaces the SDK's slow 1-per-5s background resolution.
// Uses our PersistentControl to batch-resolve all relay IPs in minutes.
let _fastBgRunning = false;
async function _fastBackgroundResolve() {
  if (_fastBgRunning) return;
  _fastBgRunning = true;
  console.error("[fast-bg] Starting fast background IP→Country resolution...");

  try {
    // Collect all relay IPs from stateManager
    const allIps = new Set();
    if (stateManager && typeof stateManager.getRelays === "function") {
      const relays = stateManager.getRelays() || [];
      for (const r of relays) {
        if (r && r.ip) allIps.add(r.ip);
      }
    }

    // Filter out IPs we already have cached
    const needed = [];
    for (const ip of allIps) {
      const hit = _countryByIp.get(ip);
      if (!hit || (typeof hit === "object" && (!hit.code || (Date.now() - hit.ts > 600000)))) {
        needed.push(ip);
      }
    }

    console.error(`[fast-bg] ${allIps.size} total relay IPs, ${needed.length} need resolution`);
    if (!needed.length) { _fastBgRunning = false; return; }

    const pc = _getPersistentCtrl();
    const BATCH = 10;       // 10 parallel requests
    const DELAY = 200;      // 200ms between batches → ~50 IPs/sec
    let resolved = 0;
    const now = Date.now();

    for (let i = 0; i < needed.length; i += BATCH) {
      const batch = needed.slice(i, i + BATCH);
      const promises = batch.map(ip =>
        pc.cmd("GETINFO ip-to-country/" + ip, 4000)
          .then(txt => {
            const cc = _parseIpToCountry(txt);
            if (cc) {
              _countryByIp.set(ip, { code: cc, ts: now });
              resolved++;
            }
          })
          .catch(() => {})
      );
      await Promise.all(promises);

      // Brief pause to avoid overwhelming the ControlPort
      if (i + BATCH < needed.length) {
        await new Promise(r => setTimeout(r, DELAY));
      }

      // Progress log every 500 IPs
      if ((i + BATCH) % 500 < BATCH) {
        console.error(`[fast-bg] Progress: ${Math.min(i + BATCH, needed.length)}/${needed.length} (${resolved} resolved)`);
      }
    }

    console.error(`[fast-bg] Done! Resolved ${resolved}/${needed.length} IPs in ${((Date.now() - now) / 1000).toFixed(1)}s`);
    // Persist resolved IP→Country mappings to disk (SDK-compatible format)
    try {
      const CACHE_FILE = "/root/.anon-cache/ip-country-cache.json";

      // Start from existing file (best-effort), then merge in-memory map.
      let existing = {};
      try {
        existing = JSON.parse(fs.readFileSync(CACHE_FILE, "utf8") || "{}");
      } catch {}

      const out = { ...existing };

      for (const [ip, v] of _countryByIp.entries()) {
        if (!ip) continue;

        // Support multiple in-memory formats:
        // - "de"
        // - { code: "de", ts: 123 }
        // - { country: "de", timestamp: 123 }
        if (typeof v === "string") {
          out[ip] = { country: v.toLowerCase(), timestamp: Date.now() };
          continue;
        }
        if (v && typeof v === "object") {
          const cc = (v.country ?? v.code);
          const ts = (v.timestamp ?? v.ts ?? Date.now());
          if (cc) out[ip] = { country: String(cc).toLowerCase(), timestamp: Number(ts) || Date.now() };
        }
      }

      fs.writeFileSync(CACHE_FILE, JSON.stringify(out), "utf8");
      console.error(`[fast-bg] Persisted ${Object.keys(out).length} entries to ${CACHE_FILE}`);
    } catch (e) {
      console.error("[fast-bg] Persist error:", e?.message || e);
    }

  } catch (e) {
    console.error("[fast-bg] Error:", e?.message || e);
  } finally {
    _fastBgRunning = false;
  }
}



// === PATCH: Manual circuit-status via raw ControlPort (bypasses broken SDK) ===
async function manualCircuitStatus() {
  try {
    const resp = await control.msgAsync("GETINFO circuit-status");
    // resp can be: { data: "..." } or just a string, or { type: "...", data: "..." }
    let text = "";
    if (typeof resp === "string") text = resp;
    else if (resp && typeof resp.data === "string") text = resp.data;
    else if (resp && typeof resp === "object") text = JSON.stringify(resp);
    else text = String(resp || "");

    const lines = text.split("\n");
    const circuits = [];
    for (const line of lines) {
      let t = line.trim();
      // strip common prefixes from control protocol
      t = t.replace(/^250[+\-]circuit-status=\s*/i, "").trim();
      if (!t || t === "." || /^250\s/.test(t) || t === "OK") continue;
      const parts = t.split(/\s+/);
      if (parts.length < 2) continue;
      const circuitId = parseInt(parts[0], 10);
      if (isNaN(circuitId)) continue;
      const state = parts[1]; // BUILT, EXTENDED, etc.
      // Path format: $FINGERPRINT~nickname,$FINGERPRINT~nickname,...
      const pathStr = (parts.length > 2 && !parts[2].includes("=")) ? parts[2] : "";
      const relays = pathStr.split(",").filter(Boolean).map(entry => {
        const m = entry.match(/^\$?([A-Fa-f0-9]{8,})(?:[~=](.+))?$/);
        return m ? { fingerprint: m[1], nickname: m[2] || "" } : null;
      }).filter(Boolean);
      let purpose = "GENERAL";
      let timeCreated = new Date();
      for (const p of parts.slice(2)) {
        if (p.startsWith("PURPOSE=")) purpose = p.slice(8);
        if (p.startsWith("TIME_CREATED=")) {
          try { timeCreated = new Date(p.slice(13).replace("T"," ")); } catch {}
        }
      }
      circuits.push({ circuitId, state, relays, purpose, timeCreated });
    }
    return circuits;
  } catch (e) {
    console.error("[manualCircuitStatus] error:", e?.message || e);
    return [];
  }
}
// === END PATCH ===

async function _closeGeneralCircuits() {
  if (!control) return;
  try {
    const status = await controlCircuitStatusSafe();
    const ids = (status || [])
      .filter((c) => (c.purpose || "").toUpperCase() === "GENERAL")
      .map((c) => c.circuitId)
      .filter((n) => Number.isFinite(n));
    for (const id of ids) {
      try { await control.closeCircuit(id); } catch {}
    }
  } catch {}
}

function _ensureRotationTimer() {
  if (!rotation.enabled) return;
  _stopRotationTimer();

  const baseMs = Math.max(60, Number(rotation.intervalSeconds || 600)) * 1000;
  const variance = Math.max(0, Math.min(80, Number(rotation.variancePercent || 20))) / 100;

  const scheduleOnce = () => {
    if (!rotation.enabled) return;
    const jitter = baseMs * variance;
    const nextMs = Math.max(30000, Math.round(baseMs + (Math.random() * 2 - 1) * jitter));
    rotation.nextRotationTs = Math.floor(Date.now() / 1000 + nextMs / 1000);
    _rotationTimer = setTimeout(async () => {
      try { await rebuildVPNManager("rotation"); } catch {}
      scheduleOnce();
    }, nextMs);
  };

  scheduleOnce();
}

function _stopRotationTimer() {
  if (_rotationTimer) {
    clearTimeout(_rotationTimer);
    _rotationTimer = null;
  rotation.nextRotationTs = 0;
  }
}

async function rebuildVPNManager(reason = "manual") {
  console.error("[rebuildVPNManager] ENTER reason=" + reason + " _rebuilding=" + _rebuilding + " READY=" + READY);
  if (_rebuilding) {
    console.error("[rebuildVPNManager] QUEUED: rebuild already in progress, will re-run for reason=" + reason);
    _pendingRebuild = reason;
    return { ok: false, error: "rebuild_in_progress", queued: true };
  }
  if (!READY || !control || !stateManager) {
    console.error("[rebuildVPNManager] BLOCKED: not ready (READY=" + READY + " control=" + !!control + " stateManager=" + !!stateManager + ")");
    return { ok: false, error: "not_ready" };
  }

  _rebuilding = true;
  try {
    _stopRotationTimer();

    try {
      if (vpnManager && typeof vpnManager.shutdown === "function") {
        await vpnManager.shutdown();
      }
    } catch {}
    vpnManager = null;

    try { await _ctrlCmd("SIGNAL NEWNYM", 6000); } catch {}
    await _closeGeneralCircuits();

    try {
      if (typeof stateManager.refreshRelays === "function") {
        await stateManager.refreshRelays();
      }
    } catch {}

    // DEBUG: Log exit countries being used
    const _rebuiltTargets = buildTargets(effectiveHopCount());
    console.error("[rebuildVPNManager] CURRENT_EXIT_COUNTRIES =", JSON.stringify(CURRENT_EXIT_COUNTRIES));
    console.error("[rebuildVPNManager] targets[0].exitCountries =", JSON.stringify(_rebuiltTargets[0]?.exitCountries));
    
    vpnManager = new Anyone.VPNManager(stateManager, {
      targets: _rebuiltTargets,
      healthMonitorInterval: 15000,
      disablePredictedCircuits: true,
      disableConflux: true,
    });
    await vpnManager.initialize();

    _refreshRelayIndex();
    await _refreshCircuitCache();

    INIT_ERROR = "";
    _ensureRotationTimer();
    return { ok: true, reason };
  } catch (e) {
    INIT_ERROR = String(e && (e.stack || e.message) ? (e.stack || e.message) : e);
    return { ok: false, error: INIT_ERROR };
  } finally {
    _rebuilding = false;
    console.error("[rebuildVPNManager] EXIT reason=" + reason);

    // If another rebuild was requested while we were busy, run it now
    if (_pendingRebuild) {
      const nextReason = _pendingRebuild;
      _pendingRebuild = null;
      console.error("[rebuildVPNManager] Running queued rebuild reason=" + nextReason);
      setTimeout(() => rebuildVPNManager(nextReason).catch(e =>
        console.error("[rebuildVPNManager] queued rebuild failed:", e?.message || e)
      ), 100);
    }
  }
}

// -----------------------------------------------------------------------------
// Bootstrap loop
// -----------------------------------------------------------------------------
async function initManagersOnce() {
  READY = false;
  _bootstrapping = true;
  INIT_ERROR = "";
  INIT_LAST_TS = Date.now();

  await waitForPort(CONTROL_HOST, CONTROL_PORT, 120000);
  await waitForCookie(COOKIE_PATH, 120000);

  control = new Anyone.Control(CONTROL_HOST, CONTROL_PORT);
  await authenticateWithCookie(control);

  stateManager = new Anyone.StateManager(control);
  await stateManager.initialize();

  _refreshRelayIndex();

  // DEBUG: Log exit countries being used  
  const _initTargets = buildTargets(effectiveHopCount());
  console.error("[initManagersOnce] CURRENT_EXIT_COUNTRIES =", JSON.stringify(CURRENT_EXIT_COUNTRIES));
  console.error("[initManagersOnce] targets[0].exitCountries =", JSON.stringify(_initTargets[0]?.exitCountries));

  vpnManager = new Anyone.VPNManager(stateManager, {
    targets: _initTargets,
    healthMonitorInterval: 15000,
    disablePredictedCircuits: true,
    disableConflux: true,
  });
  await vpnManager.initialize();

  _startCircuitCache();
  await _refreshCircuitCache();

  INIT_ERROR = "";
  READY = true;
  _bootstrapping = false;
  _ensureRotationTimer();
}

async function bootstrapLoop() {
  for (;;) {
    try {
      await initManagersOnce();
      return;
    } catch (e) {
      READY = false;
      _bootstrapping = false;
      INIT_ERROR = String(e && (e.stack || e.message) ? (e.stack || e.message) : e);
      try { control?.end?.(); } catch {}
      control = null;
      stateManager = null;
      vpnManager = null;
      await sleep(2500);
    }
  }
}

// -----------------------------------------------------------------------------
// API server
// -----------------------------------------------------------------------------
const app = express();

function _availableExitCountriesLower(){
  try{
    // Preferred if present
    if (stateManager && typeof stateManager.getAvailableCountries === "function") {
      return (stateManager.getAvailableCountries() || []).map(x => String(x||"").toLowerCase()).filter(x => x.length === 2);
    }
    // Common internal structure: exitsByCountry Map
    const m = stateManager && stateManager.exitsByCountry;
    if (m && typeof m.keys === "function") {
      return Array.from(m.keys()).map(k => String(k||"").toLowerCase()).filter(x => x.length === 2);
    }
    // Fallback: derive from relays list
    const relays = stateManager && typeof stateManager.getRelays === "function" ? (stateManager.getRelays() || []) : [];
    const set = new Set();
    for (const r of relays){
      const cc = (r && (r.country || r.country_code || r.countryCode || (r.location && r.location.countryCode) || ""))?.toString().toLowerCase() || "";
      if (cc.length === 2) set.add(cc);
    }
    return Array.from(set);
  } catch {
    return [];
  }
}

app.get("/available-exits", (_req, res) => {
  const list = _availableExitCountriesLower().map(x => x.toUpperCase()).sort();
  res.json({ ok: true, countries: list });
});



// --- DEBUG: direct ip->country via ControlPort (GETINFO ip-to-country/<ip>) ---
app.get("/debug/ip2cc", async (req, res) => {
  const ip = String(req.query.ip || "").trim();
  if (!ip) return res.status(400).json({ ok:false, error:"missing ip" });
  try{
    const raw = await _cpGetInfo(`GETINFO ip-to-country/${ip}`);
    const parsed = _parseIpToCountry(raw);
    return res.json({ ok:true, ip, parsed, raw: String(raw||"") });
  } catch(e){
    return res.status(500).json({ ok:false, error: String(e?.message || e) });
  }
});
// --- /DEBUG ---



// --- DEBUG: inspect vpnManager/stateManager live (no side effects) ---
app.get("/debug/vpn", (req, res) => {
  function keys(x){
    try { return x ? Object.keys(x).sort() : []; } catch { return []; }
  }
  function safe(fn){
    try { return fn(); } catch(e){ return { __error: String(e?.message || e) }; }
  }

  const vm = (typeof vpnManager !== "undefined") ? vpnManager : null;
  const sm = (typeof stateManager !== "undefined") ? stateManager : (vm ? (vm.stateManager || vm.state || null) : null);

  const snap =
    safe(() => sm?.getSnapshot?.()) ||
    safe(() => sm?.snapshot?.()) ||
    safe(() => sm?.getState?.()) ||
    safe(() => vm?.getSnapshot?.()) ||
    safe(() => vm?.snapshot?.()) ||
    safe(() => vm?.getState?.()) ||
    null;

  const candidates = {
    vm_targets: safe(() => vm?.targets),
    vm_pools: safe(() => vm?.pools),
    vm_circuits: safe(() => vm?.circuits),
    sm_targets: safe(() => sm?.targets),
    sm_circuits: safe(() => sm?.circuits),
    snap_targets: safe(() => snap?.targets),
    snap_circuits: safe(() => snap?.circuits),
  };

  res.json({
    ok: true,
    vpnManagerKeys: keys(vm),
    stateManagerKeys: keys(sm),
    snapshot: snap,
    candidates
  });
});

// --- DEBUG: show circuits data structures (Map-aware) ---
app.get("/debug/circuits", (req, res) => {
  function isMapLike(x){
    return x && typeof x === "object"
      && typeof x.size === "number"
      && typeof x.get === "function"
      && typeof x.values === "function"
      && typeof x.entries === "function";
  }
  function toPreview(x, limit=3){
    try{
      if (isMapLike(x)){
        const arr = Array.from(x.entries()).slice(0, limit).map(([k,v]) => ({
          key: String(k),
          valueType: typeof v,
          valueKeys: (v && typeof v === "object") ? Object.keys(v).sort() : [],
          value: v
        }));
        return { type: "Map", size: x.size, sample: arr };
      }
      if (Array.isArray(x)){
        return { type: "Array", length: x.length, sample: x.slice(0, limit) };
      }
      if (x && typeof x === "object"){
        const keys = Object.keys(x);
        const sampleKeys = keys.slice(0, limit);
        const sample = sampleKeys.map(k => ({
          key: k,
          valueType: typeof x[k],
          valueKeys: (x[k] && typeof x[k] === "object") ? Object.keys(x[k]).sort() : [],
          value: x[k]
        }));
        return { type: "Object", keysCount: keys.length, sample };
      }
      return { type: typeof x, value: x };
    } catch(e){
      return { __error: String(e?.message || e) };
    }
  }

  const vm = (typeof vpnManager !== "undefined") ? vpnManager : null;
  const sm = (typeof stateManager !== "undefined") ? stateManager : (vm ? (vm.stateManager || null) : null);

  res.json({
    ok: true,
    vpnManager: {
      circuits: toPreview(vm ? vm.circuits : null),
      pendingCircuitTargets: toPreview(vm ? vm.pendingCircuitTargets : null),
      targets: toPreview(vm ? vm.targets : null),
    },
    stateManager: {
      circuits: toPreview(sm ? sm.circuits : null),
      allRelays: toPreview(sm ? sm.allRelays : null),
      exitsByCountry: toPreview(sm ? sm.exitsByCountry : null),
    }
  });
});
// --- /DEBUG ---

app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true, ready: READY, bootstrapping: _bootstrapping, error: INIT_ERROR || null });
});


app.get("/circuit", (_req, res) => {
  try {
    // _cacheCircuits is stored old->new (see _refreshCircuitCache sort).
    // /circuit should return the newest entry to match /circuits?order=desc and SOCKS usage.
    const c = (Array.isArray(_cacheCircuits) && _cacheCircuits.length)
      ? _cacheCircuits[_cacheCircuits.length - 1]
      : null;
    const hops = c && Array.isArray(c.hops) ? c.hops : [];
    res.json({ ok: true, hops });
  } catch (e) {
    res.json({ ok: false, error: String(e?.message || e), hops: [] });
  }
});

app.get("/status", (_req, res) => {
  res.json({
    ok: true,
    ready: READY,
    bootstrapping: _bootstrapping,
    lastInitTs: INIT_LAST_TS,
    error: INIT_ERROR || null,
    hopCount: effectiveHopCount(),
    exitCountries: effectiveExitCountriesForStatus(), // uppercase for portal select compatibility
    rotation,
    cacheTs: _cacheTs,
    circuitsCached: _cacheCircuits.length,
  });
});



app.get("/circuits", (req, res) => {
  try {
    const order = String(req.query.order || "desc").toLowerCase(); // desc=newest first
    const list = Array.isArray(_cacheCircuits) ? _cacheCircuits.slice() : [];

    // cache currently sorted old->new in _refreshCircuitCache; provide UI order
    if (order === "asc") {
      // Old -> Young
      list.sort((a,b) => (a.first_seen_ts||0) - (b.first_seen_ts||0)); // oldest first
    } else {
      // Young -> Old (default)
      list.sort((a,b) => (b.first_seen_ts||0) - (a.first_seen_ts||0)); // newest first
    }

    return res.json({ ok: true, circuits: list, ts: Date.now() });
  } catch (e) {
    return res.json({ ok: false, error: String(e?.message || e), circuits: [] });
  }
});

app.post("/hopmode", async (req, res) => {
  const hopCount = Number(req.body?.hopCount || req.body?.hops || 3);
  CURRENT_HOPCOUNT = hopCount === 2 ? 2 : 3;
  const r = await rebuildVPNManager("hopmode");
  res.json({ ...r, hopCount: effectiveHopCount() });
});

app.post("/exit", async (req, res) => {
  const cc = String(req.body?.exitCountry || "AUTO").trim().toUpperCase();
  const wait = Boolean(req.body?.wait);
  const timeoutMs = Math.max(2000, Number(req.body?.timeoutMs || 30000));

  // Desired exit filter (internal is lower-case)
  const desired = (cc === "AUTO" || !cc) ? [] : [cc.toLowerCase()];

  // Validate against available exit countries (best-effort)
  const avail = _availableExitCountriesLower();
  if (desired.length && avail.length && !avail.includes(desired[0])) {
    CURRENT_EXIT_COUNTRIES = []; // fallback to AUTO
    const r0 = await rebuildVPNManager("exit_invalid_fallback");
    return res.json({
      ok: true,
      warning: `Exit ${cc} not available right now; falling back to AUTO`,
      exitCountries: effectiveExitCountriesForStatus(),
      rebuild: r0
    });
  }

  CURRENT_EXIT_COUNTRIES = desired;
  console.error("[/exit] Set CURRENT_EXIT_COUNTRIES to:", JSON.stringify(desired));

  const r = await rebuildVPNManager("exit");

  if (!wait || !r.ok) {
    res.json({ ...r, exitCountries: effectiveExitCountriesForStatus() });
    return;
  }

  // best-effort wait for circuit cache to show a matching exit (UI expects uppercase)
  const t0 = Date.now();
  let matched = false;
  while (Date.now() - t0 < timeoutMs) {
    await sleep(600);
    const cur = (_cacheCircuits[0]?.hops || []).slice(-1)[0];
    const curCC = String(cur?.country_code || "").toUpperCase();
    if (cc === "AUTO") {
      if ((_cacheCircuits[0]?.hops || []).length) { matched = true; break; }
    } else {
      if (curCC === cc) { matched = true; break; }
    }
  }

  // If a specific cc was requested but no circuits exist, auto-fallback so UI never gets stuck on "building"
  if (desired.length && _cacheCircuits.length === 0) {
    CURRENT_EXIT_COUNTRIES = []; // AUTO
    const r2 = await rebuildVPNManager("exit_timeout_fallback");
    return res.json({
      ok: true,
      warning: `No circuits could be built for ${cc}; falling back to AUTO`,
      exitCountries: effectiveExitCountriesForStatus(),
      rebuild: r2
    });
  }

  res.json({ ok: true, exitCountries: effectiveExitCountriesForStatus() });
});



app.post("/newnym", async (_req, res) => {
  if (!control) return res.json({ ok: false, error: "not_ready" });
  try {
    await _ctrlCmd("SIGNAL NEWNYM", 6000);
    res.json({ ok: true });
  } catch (e) {
    res.json({ ok: false, error: String(e) });
  }
});

app.get("/rotation", (_req, res) => {
  res.json({
    ok: true,
    enabled: !!rotation.enabled,
    intervalSeconds: Number(rotation.intervalSeconds || 600),
    variancePercent: Number(rotation.variancePercent || 20),
    nextRotationTs: rotation.nextRotationTs || 0, // keep the original object too
    rotation
  });
});

app.post("/rotation", (req, res) => {
  // Accept either top-level fields OR {rotation:{...}} (future-proof)
  const src = (req.body && req.body.rotation && typeof req.body.rotation === "object") ? req.body.rotation : (req.body || {});
  const enabled = Boolean(src.enabled);
  const intervalSeconds = Math.max(60, Number(src.intervalSeconds || 600));
  const variancePercent = Math.max(0, Math.min(80, Number(src.variancePercent || 20)));

  rotation = { enabled, intervalSeconds, variancePercent };
  if (rotation.enabled) _ensureRotationTimer();
  else _stopRotationTimer();

  res.json({ ok: true, enabled, intervalSeconds, variancePercent, nextRotationTs: rotation.nextRotationTs || 0, rotation });
});

app.post("/rotation/trigger", async (_req, res) => {
  const r = await rebuildVPNManager("rotation_trigger");
  res.json(r);
});



// ---- DEBUG: relay sample ----
app.get("/debug/relay-sample", async (req, res) => {
  try {
    if (!stateManager) return res.json({ error: "no stateManager" });

    const relays = stateManager.getRelays() || [];
    if (!relays.length) return res.json({ error: "no relays" });

    res.json({
      count: relays.length,
      keys: Object.keys(relays[0] || {}),
      sample: relays[0]
    });
  } catch (e) {
    res.json({ error: String(e) });
  }
});
app.listen(PORT, HOST, () => {
  console.log(`Circuit manager API on http://${HOST}:${PORT}`);
});

// Start bootstrapping asynchronously (keep API available)
bootstrapLoop().catch((e) => {
  INIT_ERROR = String(e);
});

// Trigger fast background IP→Country resolution 10s after startup
setTimeout(() => _fastBackgroundResolve(), 10000);

// Graceful shutdown
process.on("SIGTERM", async () => {
  try { _stopRotationTimer(); } catch {}
  try { if (_cacheTimer) clearInterval(_cacheTimer); } catch {}
  try { if (vpnManager?.shutdown) await vpnManager.shutdown(); } catch {}
  try { control?.end?.(); } catch {}
  process.exit(0);
});