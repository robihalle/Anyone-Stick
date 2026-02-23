#!/usr/bin/env python3
# ============================================================================
# Anyone Privacy Stick — Portal (slim)
# UI + Wi‑Fi + Mode + Kill Switch + Proof
# Circuits are managed by the Node circuit-manager (VPN/StateManager).
# ============================================================================

from flask import Flask, request, jsonify, render_template_string, redirect
from pathlib import Path
import subprocess, time, os, json, re, signal, uuid
import urllib.parse

app = Flask(__name__, static_folder="static")

# ──────────────────────────────────────────────
# Node circuit-manager base URL
# ──────────────────────────────────────────────
CIRCUIT_MGR_BASE = os.environ.get("CIRCUIT_MGR_BASE", "http://127.0.0.1:8787").rstrip("/")

def _exit_country_from_manager() -> str:
    """
    Authoritative exit country comes from the Node circuit-manager (/status).
    Falls back to AUTO if unavailable.
    """
    try:
        # Prefer internal helper if present
        if "_cm_request" in globals():
            r = _cm_request("/status", method="GET", payload=None, timeout=2.0)
            if isinstance(r, dict):
                ecs = r.get("exitCountries") or []
                if isinstance(ecs, list) and ecs:
                    cc = str(ecs[0] or "").strip().upper()
                    return cc or "AUTO"
                return "AUTO"
        # Fallback: stdlib urllib (no requests dependency)
        import urllib.request, json as _json
        base = globals().get("CIRCUIT_MGR_BASE", "http://127.0.0.1:8787").rstrip("/")
        with urllib.request.urlopen(base + "/status", timeout=2.0) as resp:
            data = _json.loads(resp.read().decode("utf-8","replace"))
        ecs = data.get("exitCountries") or []
        if isinstance(ecs, list) and ecs:
            cc = str(ecs[0] or "").strip().upper()
            return cc or "AUTO"
    except Exception:
        pass
    return "AUTO"


# ──────────────────────────────────────────────
# Files / scripts
# ──────────────────────────────────────────────
ANONRC_PATH = os.environ.get("ANONRC_PATH", "/etc/anonrc")
KILLSWITCH_SCRIPT = os.environ.get("KILLSWITCH_SCRIPT", "/usr/local/bin/anyone_killswitch.sh")

# ──────────────────────────────────────────────
# Mode switch runner (async, non-blocking)
# ──────────────────────────────────────────────
MODE_STATE_PATH = os.environ.get("MODE_STATE_PATH", "/run/anyone-stick-mode.json")

def _mode_write(state: dict):
    try:
        Path(MODE_STATE_PATH).write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass

def _mode_read():
    try:
        return json.loads(Path(MODE_STATE_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {"running": False}

def _run_mode_async(kind: str):
    """
    Runs mode_privacy.sh / mode_normal.sh asynchronously using systemd-run.
    Returns run_id immediately. UI can poll /api/mode/switch to see progress.
    """
    run_id = str(uuid.uuid4())[:8]
    unit = f"anyone-stick-mode-{kind}-{run_id}"
    script = "/usr/local/bin/mode_privacy.sh" if kind == "privacy" else "/usr/local/bin/mode_normal.sh"
    _mode_write({"running": True, "kind": kind, "run_id": run_id, "unit": unit, "ts": time.time(), "exit": None})

    # Start transient unit; do NOT block request
    # We rely on systemd to capture logs and exit status.
    try:
        subprocess.Popen([
            "sudo", "systemd-run",
            "--unit", unit,
            "--collect",
            "--no-ask-password",
            script
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _mode_write({"running": False, "kind": kind, "run_id": run_id, "unit": unit, "ts": time.time(), "exit": -1, "error": str(e)})
    return run_id


# ──────────────────────────────────────────────
# Tiny HTTP client (stdlib) to talk to Node
# ── Status cache (avoid hammering circuit-manager) ──────────
_status_cache = {"data": None, "ts": 0}

def _cm_status_cached(max_age=3.0):
    import time as _t
    if _t.time() - _status_cache["ts"] < max_age and _status_cache["data"]:
        return _status_cache["data"]
    result = _cm_request("/status", method="GET", payload=None, timeout=2.0)
    if isinstance(result, dict) and result.get("ok"):
        _status_cache["data"] = result
        _status_cache["ts"] = _t.time()
    return result

# ──────────────────────────────────────────────
def _cm_request(path: str, method: str = "GET", payload: dict | None = None, timeout: float = 2.5):
    import urllib.request
    import urllib.error

    url = CIRCUIT_MGR_BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}

# ──────────────────────────────────────────────
# Traffic (usb0) — totals are resettable via offsets
# ──────────────────────────────────────────────
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
_traffic_offset = {"rx": 0, "tx": 0}
_traffic_raw_prev = {"rx": 0, "tx": 0, "time": 0.0}

def update_stats():
    global stats, _traffic_offset, _traffic_raw_prev
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if "usb0" in line:
                    d = line.split()
                    raw_rx, raw_tx = int(d[1]), int(d[9])
                    t = time.time()

                    prev_t = float(_traffic_raw_prev.get("time") or 0.0)
                    if prev_t > 0:
                        dt = max(t - prev_t, 0.001)
                        stats["speed_rx"] = (raw_rx - int(_traffic_raw_prev.get("rx") or 0)) / dt
                        stats["speed_tx"] = (raw_tx - int(_traffic_raw_prev.get("tx") or 0)) / dt

                    _traffic_raw_prev.update({"rx": raw_rx, "tx": raw_tx, "time": t})

                    off_rx = int(_traffic_offset.get("rx") or 0)
                    off_tx = int(_traffic_offset.get("tx") or 0)
                    stats.update({
                        "rx": max(0, raw_rx - off_rx),
                        "tx": max(0, raw_tx - off_tx),
                        "time": t
                    })
                    break
    except Exception:
        pass
    return stats

# ──────────────────────────────────────────────
# Privacy mode detection + (optional) anonrc ExitNodes helper
# ──────────────────────────────────────────────
def _privacy_mode_active() -> bool:
    """
    Privacy mode is considered ACTIVE only when:
    1) the DNAT to 127.0.0.1:9040 is present, AND
    2) privacy has been VERIFIED (marker file exists).
    This prevents UI claiming privacy ON when anon isn't ready, and avoids leaving host without internet.
    """
    try:
        verified = os.path.exists("/var/lib/anyone-stick/privacy_verified")
        if not verified:
            return False
        return subprocess.run(
            "sudo iptables -t nat -S PREROUTING | grep -q -- '--to-destination 127.0.0.1:9040'",
            shell=True, capture_output=True
        ).returncode == 0
    except Exception:
        return False
def get_current_exit_country():
    return _exit_country_from_manager()

def set_exit_country_anonrc(country_code_upper: str):
    """Legacy helper: updates /etc/anonrc and SIGHUPs anon.
    Keep only if you still want anonrc to mirror the UI; Node manager is authoritative.
    """
    cc = (country_code_upper or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}|AUTO", cc or ""):
        raise ValueError("exit country must be 2-letter ISO or AUTO")

    try:
        with open(ANONRC_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        lines = []

    new = [l for l in lines if not l.strip().startswith(("ExitNodes", "StrictNodes"))]
    if new and not new[-1].endswith("\n"):
        new[-1] += "\n"
    if cc != "AUTO":
        new.append(f"ExitNodes {{{cc.lower()}}}\n")
        new.append("StrictNodes 1\n")

    with open(ANONRC_PATH, "w", encoding="utf-8") as f:
        f.writelines(new)

    # Reload anon config via SIGHUP (best-effort)
    try:
        pid = subprocess.check_output("pgrep -x anon", shell=True, text=True).strip().split("\n")[0]
        os.kill(int(pid), signal.SIGHUP)
    except Exception:
        pass

# ──────────────────────────────────────────────
# Anyone proof (socks) — lightweight, cached
# ──────────────────────────────────────────────
ANYONE_CHECK_URL = "https://check.en.anyone.tech/"
ANYONE_PROOF_TTL_SECONDS = 30
_anyone_cache = {"ts": 0.0, "connected": False, "ip": "", "reason": "not_checked"}

def _anyone_proof_check():
    global _anyone_cache
    now = time.time()
    if (now - float(_anyone_cache.get("ts", 0.0))) < ANYONE_PROOF_TTL_SECONDS:
        return _anyone_cache

    def run_curl(url: str, max_time: str = "10", connect_timeout: str = "5"):
        cmd = [
            "curl", "-sS",
            "--max-time", max_time,
            "--connect-timeout", connect_timeout,
            "--socks5-hostname", "127.0.0.1:9050",
            url
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        body = (r.stdout or "") + "\n" + (r.stderr or "")
        return r.returncode, body, (r.stdout or "")

    connected = False
    ip = ""
    reason = "unknown"

    # 1) Primary check page (nice UX when reachable)
    rc, body, out = run_curl(ANYONE_CHECK_URL, max_time="10", connect_timeout="5")
    if rc == 0:
        if re.search(r"congratulations\.|you\s+can\s+be\s+anyone|connected\s+to\s+anyone", body, re.I):
            connected = True
            reason = "connected"
        elif re.search(r"(sorry\.|not\s+connected).*anyone", body, re.I):
            connected = False
            reason = "not_connected"
        else:
            # If the page loads via SOCKS, we treat that as connectivity OK
            connected = True
            reason = "socks_ok_checkpage"

        m = re.search(r"ip address appears to be:\s*([0-9a-fA-F\.:]+)", body, re.I)
        if m:
            ip = m.group(1).strip()
    else:
        reason = f"curl_rc_{rc}"

    # 2) Fallback: fetch public IP via SOCKS (more reliable than a branded check page)
    if not connected or not ip:
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
            rc2, body2, out2 = run_curl(url, max_time="8", connect_timeout="4")
            cand = (out2 or "").strip()
            if rc2 == 0 and re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}", cand):
                ip = cand
                connected = True
                reason = "socks_ok_ip"
                break
            if rc2 != 0 and reason.startswith("curl_rc_"):
                reason = f"curl_rc_{rc2}"

    _anyone_cache.update({"ts": now, "connected": bool(connected), "ip": ip, "reason": reason})
    return _anyone_cache

# ──────────────────────────────────────────────
# Kill switch helpers
# ──────────────────────────────────────────────
def _killswitch_get():
    try:
        out = subprocess.check_output(["sudo", KILLSWITCH_SCRIPT, "status"], stderr=subprocess.STDOUT, text=True).strip()
        return (out.upper() == "ON")
    except Exception:
        return False

def _killswitch_set(enabled: bool):
    cmd = "on" if enabled else "off"
    out = subprocess.check_output(["sudo", KILLSWITCH_SCRIPT, cmd], stderr=subprocess.STDOUT, text=True).strip()
    return (out.upper() == "ON")

# ──────────────────────────────────────────────
# UI (kept compact; JS polls Node + proof + traffic)
# ──────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anyone Privacy Stick</title>
<link href="https://fonts.googleapis.com/css2?family=Mona+Sans:wght@200..900&display=swap" rel="stylesheet">
<style>
  :root { --primary:#0280AF; --secondary:#03BDC5; --gradient:linear-gradient(90deg,#0280AF 0%,#03BDC5 100%); --bg:#0b1116; --card:#151b23; --text:#FFF; --dim:#8b949e; --border:#30363d; }
  * { box-sizing:border-box; }
  body { font-family:"Mona Sans",sans-serif; background:var(--bg); color:var(--text); margin:0; padding:20px 20px 80px; display:flex; flex-direction:column; align-items:center; }
  .container { width:100%; max-width:420px; }
  .logo-img { max-width:180px; height:auto; display:block; margin:0 auto 20px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:16px; }
  h3 { font-size:11px; text-transform:uppercase; color:var(--secondary); margin:0 0 14px; font-weight:800; letter-spacing:.6px; }

  .row { display:flex; gap:10px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
  .muted { color:var(--dim); font-size:12px; }
  .mono { font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono","Courier New", monospace; }

  button { width:100%; padding:14px; border:none; border-radius:10px; font-size:14px; font-weight:800; cursor:pointer; transition:.2s; font-family:inherit; }
  button:disabled { opacity:.55; cursor:not-allowed; }
  .btn-primary { background:var(--gradient); color:#fff; }
  .btn-secondary { background:#21262d; color:#fff; border:1px solid var(--border); }

  input,select { width:100%; padding:12px; background:#0d1117; border:1px solid var(--border); border-radius:10px; color:#fff; margin:10px 0 0; font-family:inherit; font-size:14px; }

  .conn-badge { display:inline-flex; align-items:center; gap:8px; padding:8px 12px; border-radius:10px; font-weight:900; font-size:13px; }
  .conn-dot { height:8px; width:8px; border-radius:50%; flex-shrink:0; }
  .stopped { background:rgba(248,81,73,0.12); color:#f85149; }
  .stopped .conn-dot { background:#f85149; }
  .bootstrapping { background:rgba(210,153,34,0.12); color:#d2992a; }
  .bootstrapping .conn-dot { background:#d2992a; animation:pulse 1.2s infinite; }
  .connected { background:rgba(3,189,197,0.12); color:var(--secondary); }
  .connected .conn-dot { background:var(--secondary); box-shadow:0 0 10px var(--secondary); }
  .error { background:rgba(248,81,73,0.12); color:#f85149; }
  .error .conn-dot { background:#f85149; }

  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }

  .progress-bar-bg { width:100%; height:6px; background:rgba(255,255,255,0.06); border-radius:3px; margin-top:10px; overflow:hidden; }
  .progress-bar-fill { height:100%; border-radius:3px; background:var(--gradient); transition:width .6s ease; }

  .circuit-chain { display:flex; align-items:stretch; justify-content:center; gap:0; margin-top:10px; }
  .circuit-node { flex:1; background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:10px; padding:10px 8px; text-align:center; min-width:0; min-height:126px; }
  .circuit-node.active-node { border-color:var(--secondary); background:rgba(3,189,197,0.06); }
  .node-role { font-size:9px; font-weight:900; text-transform:uppercase; color:var(--secondary); margin-bottom:6px; letter-spacing:0.6px; }
  .node-flag { font-size:26px; line-height:1; margin-bottom:4px; }
  .node-name { font-size:11px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .node-ip, .node-country { font-size:10px; color:var(--dim); margin-top:2px; }
  .circuit-arrow { display:flex; align-items:center; padding:0 4px; font-size:16px; color:var(--secondary); font-weight:900; }

  .proof-banner {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 9999;
    padding: 12px 14px; font-weight: 900; letter-spacing: 0.3px; text-transform: uppercase;
    text-align: center; border-top: 1px solid rgba(255,255,255,0.10); backdrop-filter: blur(8px);
  }
  .proof-banner.disconnected { background: rgba(248,81,73,0.18); color: #ff8a84; }
  .proof-banner.connected { background: rgba(63,185,80,0.18); color: #7ee787; }
  .proof-sub { display:block; margin-top: 4px; font-size: 11px; font-weight: 800; opacity: 0.95; text-transform: none; letter-spacing: 0; }

  .wifi-item { padding:12px; border-bottom:1px solid var(--border); cursor:pointer; display:flex; justify-content:space-between; font-size:14px; }
  .connected-label { color:var(--secondary); font-weight:900; font-size:10px; border:1px solid var(--secondary); padding:2px 6px; border-radius:6px; }

  /* MODE BOX (LOUD) */
  .mode-box {
    display:flex;
    gap:14px;
    align-items:center;
    padding:16px 14px;
    border-radius:14px;
    border:1px solid rgba(255,255,255,0.16);
    background:rgba(255,255,255,0.04);
    margin-bottom:14px;
  }
  .mode-icon {
    width:44px;
    height:44px;
    border-radius:12px;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:22px;
    font-weight:900;
    border:1px solid rgba(255,255,255,0.14);
    background:rgba(0,0,0,0.10);
    flex:0 0 auto;
  }
  .mode-title {
    font-size:16px;
    font-weight:950;
    letter-spacing:0.6px;
    text-transform:uppercase;
    line-height:1.05;
    margin:0;
  }
  .mode-sub {
    margin-top:4px;
    font-size:12px;
    color:var(--dim);
    line-height:1.25;
  }
  .mode-route {
    margin-top:8px;
    display:inline-flex;
    gap:8px;
    align-items:center;
    padding:6px 10px;
    border-radius:999px;
    font-size:12px;
    font-weight:900;
    letter-spacing:0.4px;
    text-transform:uppercase;
    border:1px solid rgba(255,255,255,0.14);
    background:rgba(255,255,255,0.04);
  }
  .mode-box.normal {
    border-color: rgba(34,197,94,0.35);
    background: rgba(34,197,94,0.10);
  }
  .mode-box.normal .mode-icon {
    border-color: rgba(34,197,94,0.45);
    background: rgba(34,197,94,0.18);
  }
  .mode-box.normal .mode-title { color: #b7f7c9; }
  .mode-box.normal .mode-route { border-color: rgba(34,197,94,0.35); }

  .mode-box.privacy {
    border-color: rgba(3,189,197,0.45);
    background: rgba(3,189,197,0.10);
    box-shadow: 0 0 0 1px rgba(3,189,197,0.10), 0 0 18px rgba(3,189,197,0.10);
    animation: modeGlow 1.6s ease-in-out infinite;
  }
  .mode-box.privacy .mode-icon {
    border-color: rgba(3,189,197,0.55);
    background: rgba(3,189,197,0.18);
    box-shadow: 0 0 14px rgba(3,189,197,0.18);
  }
  .mode-box.privacy .mode-title { color: var(--secondary); }
  .mode-box.privacy .mode-route { border-color: rgba(3,189,197,0.45); }

  @keyframes modeGlow {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-1px); }
  }

</style>
</head>
<body>
<div class="container">
  {% if mode_error %}
  <div class="card" style="border-color: rgba(248,81,73,0.55); background: rgba(248,81,73,0.10);">
    <h3 style="color:#ff8a84;">Mode switch failed</h3>
    <div class="muted" style="color:#ffb4b0; font-weight:800;">
      Enable Privacy did not complete successfully. Your routing was not changed.
      Please check Wi-Fi / anon bootstrap and try again.
    </div>
    <div class="muted" style="margin-top:8px;">Details: <span class="mono">{{ mode_error }}</span></div>
  </div>
  {% endif %}

  <img src="/static/logo.png" class="logo-img">

  <div class="card">
    <h3>Kill Switch</h3>
    <div class="row">
      <div class="muted" id="ks-sub">Blocks all non-local traffic from the Stick.</div>
      <button class="btn-secondary" style="width:auto" id="ks-btn">Kill Switch: …</button>
    </div>
  </div>

  <div class="card">
    <h3>Anyone Connection</h3>
    <div id="conn-badge" class="conn-badge stopped"><div class="conn-dot"></div><span id="conn-label">Checking…</span></div>
    <div class="progress-bar-bg"><div class="progress-bar-fill" id="conn-progress" style="width:0%"></div></div>
    <div class="muted" id="conn-summary" style="margin-top:8px">—</div>
    <div class="muted" style="margin-top:10px">Hop mode: <b id="hopmode">—</b></div>
    <div class="row" style="margin-top:10px">
      <button class="btn-secondary" style="width:auto" id="hop2">2-hop</button>
      <button class="btn-secondary" style="width:auto" id="hop3">3-hop</button>
      <button class="btn-primary" style="width:auto" id="newnym">New Circuit</button>
    </div>
  </div>

  <div class="card" id="rot-card">
    <h3>Circuit Rotation</h3>

    <!-- Status Banner -->
    <div id="rot-status-banner" style="
      padding:10px 14px; border-radius:8px; margin-bottom:12px;
      font-weight:700; font-size:13px; text-align:center;
      transition: background .3s, color .3s;
      background:#2a2a2e; color:#888;
    ">
      <span id="rot-status-icon">⏳</span>
      <span id="rot-status-text">Loading…</span>
    </div>

    <!-- Privacy hint -->
    <div class="muted" id="rot-privacy" style="font-size:11px; margin-bottom:8px;">Checking privacy…</div>

    <!-- Toggle ON/OFF -->
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px;">
      <button id="rot-toggle" style="
        min-width:130px; height:38px; border:none; border-radius:8px;
        font-weight:700; font-size:13px; cursor:pointer;
        transition: background .25s, transform .1s;
      " class="btn-secondary">Loading…</button>
      <span class="muted" id="rot-toggle-hint" style="font-size:11px;"></span>
    </div>

    <!-- Countdown (big & visible when active) -->
    <div id="rot-countdown-box" style="
      display:none; padding:12px; border-radius:8px;
      background:rgba(46,204,113,.08); border:1px solid rgba(46,204,113,.25);
      text-align:center; margin-bottom:12px;
    ">
      <div style="font-size:11px; color:#888; margin-bottom:4px;">Next rotation in</div>
      <div id="rot-countdown" class="mono" style="font-size:28px; font-weight:900; color:#2ecc71;">—</div>
    </div>

    <!-- Last rotation info -->
    <div id="rot-last-info" style="display:none; font-size:11px; color:#888; margin-bottom:10px; text-align:center;">
      Last rotation: <span id="rot-last-time" class="mono">—</span>
    </div>

    <!-- Config fields -->
    <div id="rot-config-section" style="
      padding:10px; border-radius:8px; background:rgba(255,255,255,.03);
      border:1px solid rgba(255,255,255,.06); margin-bottom:10px;
    ">
      <div style="display:flex; gap:10px;">
        <div style="flex:1">
          <div class="muted" style="font-size:11px; margin-bottom:3px;">Interval (sec)</div>
          <input type="number" id="rot-interval" min="60" value="600" style="width:100%">
        </div>
        <div style="flex:1">
          <div class="muted" style="font-size:11px; margin-bottom:3px;">Variance (%)</div>
          <input type="number" id="rot-variance" min="0" max="80" value="20" style="width:100%">
        </div>
      </div>
    </div>

    <!-- Action buttons -->
    <div style="display:flex; gap:8px;">
      <button class="btn-primary" style="flex:1; position:relative;" id="rot-save">
        <span id="rot-save-label">💾 Save</span>
      </button>
      <button class="btn-secondary" style="flex:1; position:relative;" id="rot-trigger">
        <span id="rot-trigger-label">🔄 Rotate Now</span>
      </button>
    </div>

    <!-- Feedback toast -->
    <div id="rot-toast" style="
      display:none; margin-top:8px; padding:8px 12px; border-radius:6px;
      font-size:12px; font-weight:600; text-align:center;
      transition: opacity .3s;
    "></div>
  </div>



  <div class="card">
    <h3>Circuit Chain</h3>
    <div id="circuit-container"><div class="muted">Loading…</div></div>
    <details id="circuits-details" style="margin-top:14px;">
      <summary style="cursor:pointer; font-weight:900; color:var(--secondary); font-size:12px; user-select:none;">Show all circuits</summary>
      <div class="row" style="margin-top:10px">
        <select id="circuits-sort" style="margin:0; width:auto">
          <option value="desc" selected>Young → Old</option>
          <option value="asc">Old → Young</option>
        </select>
        <button class="btn-secondary" style="width:auto" id="circuits-refresh">↻ Refresh</button>
      </div>
      <div id="all-circuits" style="margin-top:12px"><div class="muted">Open to load…</div></div>
    </details>
  </div>

      <div class="card">
      <h3>Mode</h3>

      <div class="mode-box {{ 'privacy' if privacy else 'normal' }}">
        <div class="mode-icon">{{ '🔒' if privacy else '🌐' }}</div>
        <div style="min-width:0;">
          <div class="mode-title">{{ 'PRIVACY MODE — ACTIVE' if privacy else 'NORMAL MODE — ACTIVE' }}</div>
          <div class="mode-sub">
            {{ 'All client traffic is routed through Anyone (transparent proxy).' if privacy else 'Traffic goes directly to the internet (no Anyone routing).' }}
          </div>
          <div class="mode-route">
            {{ 'ROUTING: VIA ANYONE' if privacy else 'ROUTING: DIRECT' }}
          </div>
        </div>
      </div>

      <form action="/mode/{{ 'normal' if privacy else 'privacy' }}" method="post">
        <button class="{{ 'btn-secondary' if privacy else 'btn-primary' }}">
          {{ 'Switch to Normal (disable privacy)' if privacy else 'Enable Privacy (route via Anyone)' }}
        </button>
      </form>

      <div class="helper-text" style="margin-top:10px;">
        Mode affects routing. Kill Switch is separate and can block all egress in both modes.
      </div>
    </div>

<div class="card">
    <h3>Exit Country</h3>
    <div class="muted">This now configures the Node circuit-manager (authoritative). Anonrc can optionally mirror it.</div>
    <select id="exit-select">
      <option value="AUTO">🌍 Automatic (Best Available)</option>
      <option value="DE">🇩🇪 Germany</option>
      <option value="NL">🇳🇱 Netherlands</option>
      <option value="US">🇺🇸 United States</option>
      <option value="FR">🇫🇷 France</option>
      <option value="GB">🇬🇧 United Kingdom</option>
      <option value="ES">🇪🇸 Spain</option>
      <option value="IT">🇮🇹 Italy</option>
      <option value="PL">🇵🇱 Poland</option>
      <option value="SE">🇸🇪 Sweden</option>
      <option value="NO">🇳🇴 Norway</option>
      <option value="FI">🇫🇮 Finland</option>
      <option value="CH">🇨🇭 Switzerland</option>
      <option value="AT">🇦🇹 Austria</option>
      <option value="CZ">🇨🇿 Czech Republic</option>
      <option value="RO">🇷🇴 Romania</option>
      <option value="BG">🇧🇬 Bulgaria</option>
      <option value="HU">🇭🇺 Hungary</option>
      <option value="PT">🇵🇹 Portugal</option>
      <option value="CA">🇨🇦 Canada</option>
      <option value="AU">🇦🇺 Australia</option>
      <option value="JP">🇯🇵 Japan</option>
      <option value="SG">🇸🇬 Singapore</option>
    </select>
    <button class="btn-secondary" style="margin-top:10px" id="exit-apply">Apply Exit Country</button>
    <div class="muted" style="margin-top:8px">Configured (manager): <span class="mono" id="exit-current">{{ exit_country }}</span></div>
  </div>

  <div class="card">
    <h3>Live Traffic</h3>
    <div class="row">
      <div><div class="muted">DOWNLOAD</div><div style="font-size:18px;font-weight:900" id="rx">0 MB</div><div class="muted" id="s_rx">0 KB/s</div></div>
      <div><div class="muted">UPLOAD</div><div style="font-size:18px;font-weight:900" id="tx">0 MB</div><div class="muted" id="s_tx">0 KB/s</div></div>
    </div>
    <button class="btn-secondary" style="margin-top:10px" id="traffic-reset">⟲ Reset totals</button>
  </div>

  <div class="card">
    <h3>Wi‑Fi</h3>
    <button class="btn-secondary" id="scan-btn">Scan Networks</button>
    <div id="list" style="margin-top:10px"></div>
    <div id="connect" style="display:none;margin-top:14px">
      <div style="font-weight:800" id="ssid-name"></div>
      <input type="password" id="pw" placeholder="Password">
      <button class="btn-primary" id="conn-btn">Connect Now</button>
    </div>
  </div>
</div>

<div id="proof-banner" class="proof-banner disconnected">
  <span id="proof-main">Not connected to Anyone</span>
  <span class="proof-sub" id="proof-sub">Checking…</span>
</div>

<script>
let targetSSID = '';

function safeBind(id, evt, fn){
  const el = document.getElementById(id);
  if(!el) return false;
  el.addEventListener(evt, fn);
  return true;
}

function showJsError(msg){
  try{
    let box = document.getElementById('js-err');
    if(!box){
      const cc = document.getElementById('circuit-container');
      if(!cc) return;
      box = document.createElement('div');
      box.id = 'js-err';
      box.style.marginTop = '10px';
      box.style.padding = '10px';
      box.style.border = '1px solid var(--border)';
      box.style.borderRadius = '10px';
      box.style.background = 'rgba(248,81,73,0.10)';
      box.style.color = '#ff8a84';
      box.style.fontWeight = '800';
      cc.prepend(box);
    }
    box.textContent = 'JS error: ' + msg;
  }catch(e){}
}

window.addEventListener('error', (e)=>{
  const m = (e && (e.message || e.error && e.error.message)) ? (e.message || e.error.message) : 'unknown';
  showJsError(m);
});


function flag(cc) {
  if (!cc || cc.length !== 2) return '—';
  return String.fromCodePoint(...[...cc.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
}

async function jget(url, ms=2500){
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try{
    const r = await fetch(url, { cache:'no-store', signal: ctl.signal });
    const txt = await r.text();
    if(!r.ok) throw new Error('HTTP ' + r.status);
    return txt ? JSON.parse(txt) : {};
  } finally { clearTimeout(t); }
}

async function jpost(url, body, ms=3500){
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try{
    const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{}), signal: ctl.signal });
    const txt = await r.text();
    if(!r.ok) throw new Error('HTTP ' + r.status);
    return txt ? JSON.parse(txt) : {};
  } finally { clearTimeout(t); }
}


function setHopUiBusy(on, label){
  const b2 = document.getElementById('hop2');
  const b3 = document.getElementById('hop3');
  const newnym = document.getElementById('newnym');
  if(!b2 || !b3) return;

  if(on){
    uiSetSwitching(true, 15000);
    b2.disabled = true; b3.disabled = true;
    if (newnym) newnym.disabled = true;
    b2.textContent = (label || 'Switching…');
    b3.textContent = (label || 'Switching…');

    // show bootstrapping state immediately
    const badge = document.getElementById('conn-badge');
    const labelEl = document.getElementById('conn-label');
    const bar = document.getElementById('conn-progress');
    const summ = document.getElementById('conn-summary');
    if (badge && labelEl && bar && summ){
      badge.className = 'conn-badge bootstrapping';
      labelEl.textContent = 'SWITCHING';
      __uiSwitchingUntil = Date.now() + 15000;
      setConnProgressPct(55);
      startConnProgressPulse(55, 88, 1, 90);
      summ.textContent = 'Rebuilding circuit…';
    }
  } else {
    uiSetSwitching(false);
    b2.disabled = false; b3.disabled = false;
    stopConnProgressPulse();
    __uiSwitchingUntil = 0;
    if (newnym) newnym.disabled = false;
    b2.textContent = '2-hop';
    b3.textContent = '3-hop';
  }
}

async function waitForCircuitChange(prevKey, timeoutMs=15000){
  const t0 = Date.now();
  while ((Date.now() - t0) < timeoutMs){
    const d = await jget('/api/cm/circuit', 2500).catch(()=>({hops:[]}));
    const hops = (d && d.hops) ? d.hops : [];
    if (hops && hops.length){
      const key = hops.map(h => [
        h.role || "",
        h.fingerprint || "",
        h.nickname || "",
        h.country_code || "",
        h.country_name || "",
        h.ip || ""
      ].join("|")).join(";");

      if (key && key !== (prevKey || "")) return { ok:true, hops, key };
    }
    await new Promise(r => setTimeout(r, 250));
  }
  return { ok:false, hops:[], key: prevKey || "" };
}


let __connPulseTimer = null;
let __lastCircuitViewKey = '';
let __uiSwitchingUntil = 0; // ms timestamp
let __uiConnectedArmed = false; // becomes true only after a successful rebuild/ready state


function stopConnProgressPulse(){
  if (__connPulseTimer){
    clearInterval(__connPulseTimer);
    __connPulseTimer = null;
  }
}

function setConnProgressPct(pct){
  const bar = document.getElementById('conn-progress');
  if (!bar) return;
  const x = Math.max(0, Math.min(100, Number(pct) || 0));
  bar.style.width = `${x}%`;
}

// Monotone ramp: only moves to the right (no wiggling back)
function startConnProgressPulse(startPct=55, targetPct=88, step=1, intervalMs=90){
  stopConnProgressPulse();
  const bar = document.getElementById('conn-progress');
  if (!bar) return;

  let cur = Math.max(0, Math.min(100, Number(startPct) || 0));
  const target = Math.max(cur, Math.min(100, Number(targetPct) || 88));
  bar.style.width = `${cur}%`;

  __connPulseTimer = setInterval(() => {
    cur = Math.min(target, cur + Math.max(1, Number(step) || 1));
    bar.style.width = `${cur}%`;
    if (cur >= target) stopConnProgressPulse();
  }, intervalMs);
}

function uiSetSwitching(on, ttlMs=15000){
  __uiSwitchingUntil = on ? (Date.now() + ttlMs) : 0;
}


function updateConn(st){
  const badge = document.getElementById('conn-badge');
  const label = document.getElementById('conn-label');
  const bar = document.getElementById('conn-progress');
  const summ = document.getElementById('conn-summary');
  const hop = document.getElementById('hopmode');

  const hopCount = st?.hopCount ?? null;
  hop.textContent = hopCount ? String(hopCount) : '—';
  window.__hopCount = hopCount ? Number(hopCount) : (window.__hopCount || 3);

  const switching = (typeof __uiSwitchingUntil === "number") && (Date.now() < __uiSwitchingUntil);
  if (switching){
    badge.className = 'conn-badge bootstrapping';
    label.textContent = 'SWITCHING';
    summ.textContent = 'Rebuilding circuit…';
    return; // keep progress controlled by ramp
  }

  if (st && st.ok){
    // If we're not switching and CM is reachable, arm CONNECTED (avoids endless 'SWITCHING' after page reload)
    if (!__uiConnectedArmed){
      __uiConnectedArmed = true;
    }
    badge.className = 'conn-badge connected';
    label.textContent = 'CONNECTED';
    bar.style.width = '100%';
    summ.textContent = 'Circuit manager online';
    return;
  }

  badge.className = 'conn-badge error';
  label.textContent = 'ERROR';
  bar.style.width = '0%';
  summ.textContent = st?.error || 'circuit manager unreachable';
}

function renderCircuit(hops){
  const c = document.getElementById('circuit-container');
  const hc = Number(window.__hopCount || 3);

  if(!hops || !hops.length){
    let html = '<div class="circuit-chain">';
    const roles = (hc === 2) ? ['entry','exit'] : ['entry','middle','exit'];
    roles.forEach((role,i)=>{
      if(i>0) html += '<div class="circuit-arrow">➜</div>';
      html += '<div class="circuit-node' + (role === 'exit' ? ' active-node' : '') + '">'
        + '<div class="node-role">' + role + '</div>'
        + '<div class="node-flag">⏳</div>'
        + '<div class="node-name">building…</div>'
        + '<div class="node-ip mono">—</div>'
        + '<div class="node-country">—</div>'
        + '</div>';
    });
    html += '</div>';
    c.innerHTML = html;
    return;
  }

  // UI semantics:
  // 2-hop => show first + last (entry/exit), even if backend returns 3 relays
  let view = hops;
  if (hc === 2 && hops.length >= 2){
    view = [hops[0], hops[hops.length - 1]];
  } else if (hc === 3 && hops.length >= 3){
    view = [hops[0], hops[1], hops[hops.length - 1]];
  }

  let html = '<div class="circuit-chain">';
  view.forEach((hop,i)=>{
    if(i>0) html += '<div class="circuit-arrow">➜</div>';
    const cc = hop.country_code || '';
    const role = (i === 0) ? 'entry' : (i === view.length - 1) ? 'exit' : 'middle';

    html += '<div class="circuit-node' + (role === 'exit' ? ' active-node' : '') + '">'
      + '<div class="node-role">' + role + '</div>'
      + '<div class="node-flag">' + flag(cc) + '</div>'
      + '<div class="node-name">' + (hop.nickname || '—') + '</div>'
      + '<div class="node-ip mono">' + (hop.ip || '—') + '</div>'
      + '<div class="node-country">' + (hop.country_name || cc || '—') + '</div>'
      + '</div>';
  });
  html += '</div>';
  c.innerHTML = html;
}

function fmtAge(sec){
  sec = Math.max(0, Math.floor(sec || 0));
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return m + 'm ' + s + 's';
  const h = Math.floor(m / 60), mm = m % 60;
  return h + 'h ' + mm + 'm';
}

let __circuitAgeData = {};

// --- TS unit helper (accept seconds or milliseconds) ---
function __isMsTs(x){
  const n = Number(x);
  return Number.isFinite(n) && n > 1e12; // ms timestamps are usually > 1e12
}
function __toMsTs(x){
  const n = Number(x);
  if (!Number.isFinite(n)) return 0;
  return __isMsTs(n) ? n : Math.round(n * 1000);
}
// --- /TS unit helper ---
function renderAllCircuits(list){
  const box = document.getElementById('all-circuits');
  if(!list || !list.length){
    box.innerHTML = '<div class="muted">No BUILT GENERAL circuits</div>';
    __circuitAgeData = {};
    return;
  }
  __circuitAgeData = {};
  list.forEach(c => {
    if(c.id && c.first_seen_ts){ __circuitAgeData[c.id] = __toMsTs(c.first_seen_ts); }
  });
  let out = '';
  list.forEach(c=>{
    const hops = c.hops || [];
    if(!hops.length) return;
    out += '<div style="padding:10px;border:1px solid var(--border);border-radius:10px;background:rgba(255,255,255,0.02);margin-bottom:10px">';
    out += '<div class="row" style="margin-bottom:8px">'
         + '<div class="muted mono">Circuit ' + (c.id||'?') + '</div>'
         + '<div class="muted">Age: <b id="age-' + c.id + '" style="color:var(--text)">' + fmtAge(c.age_seconds) + '</b></div>'
         + '</div>';
    out += '<div class="circuit-chain">';
    hops.forEach((hop,i)=>{
      if(i>0) out += '<div class="circuit-arrow">➜</div>';
      const cc = hop.country_code || '';
      out += '<div class="circuit-node' + (hop.role === 'exit' ? ' active-node' : '') + '">'
        + '<div class="node-role">' + (hop.role || '') + '</div>'
        + '<div class="node-flag">' + flag(cc) + '</div>'
        + '<div class="node-name">' + (hop.nickname || '—') + '</div>'
        + '<div class="node-ip mono">' + (hop.ip || '—') + '</div>'
        + '<div class="node-country">' + (hop.country_name || cc || '—') + '</div>'
        + '</div>';
    });
    out += '</div></div>';
  });
  box.innerHTML = out || '<div class="muted">No circuits</div>';
}
setInterval(function(){
  const now = Date.now();
  for(const cid in __circuitAgeData){
    const el = document.getElementById('age-' + cid);
    if(el){
      const firstSeen = __circuitAgeData[cid];
      const ageSec = Math.floor((now - firstSeen) / 1000);
      el.textContent = fmtAge(ageSec);
    }
  }
}, 1000);

async function refreshStatus(){
  const st = await jget('/api/cm/status', 2000).catch(e=>({ok:false,error:String(e)}));
  updateConn(st);
}

async function refreshCircuit(){
  window.__lastGoodHops = window.__lastGoodHops || [];
  window.__lastGoodTs = window.__lastGoodTs || 0;

  const d = await jget('/api/cm/circuit', 8000).catch(()=>({hops:[]}));
  const hops = (d && d.hops) ? d.hops : [];

  const now = Date.now();
  const switching = (typeof __uiSwitchingUntil === "number") && (Date.now() < __uiSwitchingUntil);
  const hc = Number(window.__hopCount || 3);
  const wantLens = (hc === 2) ? new Set([2,3,4,5,6]) : new Set([2,3,4,5,6]);

  // During hop switch: never show wrong-length circuit (prevents 3-hop flashing in 2-hop mode etc.)
  if (hops.length && !wantLens.has(hops.length)){
    renderCircuit([]); // placeholders
    return;
  }

  // Empty -> placeholders; keep last-good only when NOT switching
  if (!hops.length){
    if (!switching && window.__lastGoodHops.length && (now - window.__lastGoodTs) < 12000){
      return; // keep last good (avoid flicker)
    }
    renderCircuit([]); // placeholders
    return;
  }

  // Got hops -> accept & render
  window.__lastGoodHops = hops;
  window.__lastGoodTs = now;

  // Key includes hopCount view mode, so 2<->3 always re-renders even if circuit is same
  const baseKey = hops.map(h => [
    h.role || "",
    h.fingerprint || "",
    h.nickname || "",
    h.country_code || "",
    h.country_name || "",
    h.ip || ""
  ].join("|")).join(";");

  const viewKey = baseKey + "|hc=" + String(hc);

  const container = document.getElementById('circuit-container');
  const isPlaceholder = container ? (container.textContent || '').includes('building') : false;

  if (viewKey === (__lastCircuitViewKey || '') && !isPlaceholder) return;

  __lastCircuitViewKey = viewKey;
  renderCircuit(hops);
}

async function refreshAllCircuits(){
  const details = document.getElementById('circuits-details');
  if(!details || !details.open) return;
  const order = document.getElementById('circuits-sort')?.value || 'desc';
  const d = await jget('/api/cm/circuits?order=' + encodeURIComponent(order), 3000).catch(()=>({circuits:[]}));
  renderAllCircuits(d.circuits || []);
}

async function setHopmode(hopCount){
  const hc = (Number(hopCount) === 2) ? 2 : 3;

  window.__hopCount = hc;
  __uiConnectedArmed = false;

  // IMPORTANT: on hop switch, never keep last-good circuit (prevents showing 3-hop while switching to 2-hop and vice versa)
  window.__lastGoodHops = [];
  window.__lastGoodTs = 0;

  renderCircuit([]);               // immediate feedback
  setHopUiBusy(true, 'Switching…');

  __uiSwitchingUntil = Date.now() + 25000;
  setConnProgressPct(55);
  startConnProgressPulse(55, 88, 1, 90);

  try{
    // Backend hopmode can block while rebuilding
    await jpost('/api/cm/hopmode', { hopCount: hc }, 22000).catch(()=>({ok:false}));

    // Now wait until UI can fetch a circuit at least once (no empty)
    const t0 = Date.now();
    while ((Date.now() - t0) < 22000){
      const d = await jget('/api/cm/circuit', 8000).catch(()=>({hops:[]}));
      const hops = (d && d.hops) ? d.hops : [];
      const acceptLen = (hc === 2) ? new Set([2,3]) : new Set([3]);
      if (acceptLen.has(hops.length)){
        renderCircuit(hops);
        break;
      }
      await new Promise(r => setTimeout(r, 5000));
    }

    stopConnProgressPulse();
    setConnProgressPct(100);
    await new Promise(r => setTimeout(r, 250));

    __uiSwitchingUntil = 0;
    __uiConnectedArmed = true;

    await refreshStatus();
    await refreshCircuit();
    await refreshAllCircuits();
  } finally {
    setHopUiBusy(false);
  }
}

async function newnym(){
  const btn = document.getElementById('newnym');
  btn.disabled = true; btn.textContent = '⏳';
  try { await jpost('/api/cm/newnym', {}, 4000); }
  finally { setTimeout(()=>{ btn.disabled=false; btn.textContent='New Circuit'; }, 5000); }
  await refreshCircuit();
}

async function updateProof(){
  const st = await jget('/api/anyone/proof', 18000).catch(()=>({connected:false, ip:'', privacy:false}));
  const b = document.getElementById('proof-banner');
  const sub = document.getElementById('proof-sub');
  const connected = !!(st && st.display_connected);
  b.className = 'proof-banner ' + (connected ? 'connected' : 'disconnected');
  const main = document.getElementById('proof-main');
  if (main) main.textContent = connected ? 'Connected to Anyone' : 'Not connected to Anyone';
  let msg = '';
  if (!st.privacy) msg += 'Privacy mode is OFF. ';
  if (st.privacy && st.ip) msg += 'Exit IP: ' + st.ip + '.';
  sub.textContent = msg.trim() || '—';
}

async function pollTraffic(){
  const d = await jget('/api/traffic', 2000).catch(()=>null);
  if(!d) return;
  document.getElementById('rx').textContent = (d.rx/1048576).toFixed(1)+' MB';
  document.getElementById('tx').textContent = (d.tx/1048576).toFixed(1)+' MB';
  document.getElementById('s_rx').textContent = d.speed_rx>1048576?(d.speed_rx/1048576).toFixed(1)+' MB/s':(d.speed_rx/1024).toFixed(1)+' KB/s';
  document.getElementById('s_tx').textContent = d.speed_tx>1048576?(d.speed_tx/1048576).toFixed(1)+' MB/s':(d.speed_tx/1024).toFixed(1)+' KB/s';
}

async function resetTraffic(){
  await jpost('/api/traffic/reset', {}, 2500).catch(()=>null);
  await pollTraffic();
}

// Kill Switch UI
async function refreshKillSwitch(){
  const st = await jget('/api/killswitch/status', 2000).catch(()=>({enabled:false}));
  const btn = document.getElementById('ks-btn');
  const sub = document.getElementById('ks-sub');
  const on = !!st.enabled;
  btn.textContent = on ? 'Kill Switch: ON' : 'Kill Switch: OFF';
  btn.className = on ? 'btn-primary' : 'btn-secondary';
  sub.textContent = on ? 'Egress is blocked. Only local management traffic is allowed.' : 'Blocks all non-local traffic from the Stick.';
}

async function toggleKillSwitch(){
  const cur = await jget('/api/killswitch/status', 2000).catch(()=>({enabled:false}));
  const target = !cur.enabled;
  await jpost('/api/killswitch/set', {enabled: target}, 2500).catch(()=>null);
  await refreshKillSwitch();
}

// Wi‑Fi
async function scan(){
  const btn = document.getElementById('scan-btn');
  btn.disabled = true; btn.textContent = 'Scanning…';
  const container = document.getElementById('list');
  container.innerHTML = '';
  try{
    const d = await jget('/wifi/scan', 15000);
    (d.networks||[]).forEach(n=>{
      const div = document.createElement('div');
      div.className = 'wifi-item';
      const left = document.createElement('span');
      left.textContent = n.ssid;
      const right = document.createElement('span');
      right.innerHTML = n.connected ? '<span class="connected-label">CONNECTED</span>' : '›';
      div.appendChild(left); div.appendChild(right);
      div.addEventListener('click', ()=>sel(n.ssid));
      container.appendChild(div);
    });
  } catch(e){} finally {
    btn.disabled = false; btn.textContent = 'Scan Networks';
  }
}
function sel(ssid){
  targetSSID = ssid;
  document.getElementById('ssid-name').textContent = ssid;
  document.getElementById('connect').style.display = 'block';
}
async function connectWifi(){
  const btn = document.getElementById('conn-btn');
  btn.disabled = true; btn.textContent = 'Connecting…';
  const pw = document.getElementById('pw').value || '';
  try{
    const d = await jpost('/wifi/connect', {ssid: targetSSID, password: pw}, 30000);
    alert(d.status || 'OK');
    if(String(d.status||'').toLowerCase().includes('connected')) location.reload();
  } catch(e){
    alert('Error');
  } finally {
    btn.disabled = false; btn.textContent = 'Connect Now';
  }
}

// Exit country -> Node manager (and optional anonrc mirror)

async function waitForExit(targetCC, timeoutMs=12000){
  const want = String(targetCC || '').toUpperCase();
  const t0 = Date.now();
  while ((Date.now() - t0) < timeoutMs){
    const d = await jget('/api/cm/circuit', 2500).catch(()=>({hops:[]}));
    const hops = (d && d.hops) ? d.hops : [];
    if (hops.length){
      const exitHop = hops.find(h => (h.role || '').toLowerCase() === 'exit') || hops[hops.length-1];
      const got = String(exitHop?.country_code || '').toUpperCase();
      if (want === 'AUTO'){
        // any exit is acceptable; just require a built circuit
        return true;
      }
      if (got && got === want) return true;
    }
    await new Promise(r => setTimeout(r, 5000));
  }
  return false;
}


async function applyExit(){
  const cc = document.getElementById('exit-select')?.value || 'AUTO';
  const btn = document.getElementById('exit-apply');
  btn.disabled = true; btn.textContent = '⏳ Applying…';
  try{
    const resp = await jpost('/api/cm/exit', { exitCountry: cc }, 20000).catch(e=>({ok:false,error:String(e)}));
    if(!resp || !resp.ok){
      const msg = (resp && resp.error) ? String(resp.error) : 'unknown error';
      showJsError('Exit change failed: ' + msg);
      // refresh UI from authoritative source
      await initExitUi();
      await refreshStatus();
      return;
    }

    await refreshStatus();
    // wait for circuit to be BUILT (and optionally match exit)
    await waitForExit(cc, 20000);
    await refreshCircuit();

    // authoritative display
    await initExitUi();
  } finally {
    btn.disabled = false; btn.textContent = 'Apply Exit Country';
  }
}



async function initExitUi(){
  try{
    const cur = await jget('/api/exit/current', 2000).catch(()=>({exit_country:'AUTO'}));
    const cc = String(cur.exit_country || 'AUTO').toUpperCase();
    const sel = document.getElementById('exit-select');
    const curEl = document.getElementById('exit-current');
    if (sel) sel.value = cc;
    if (curEl) curEl.textContent = cc;
  }catch(e){}
}

// Bind
safeBind('hop2','click', ()=>setHopmode(2));
safeBind('hop3','click', ()=>setHopmode(3));
safeBind('newnym','click', newnym);
safeBind('circuits-refresh','click', refreshAllCircuits);
safeBind('circuits-details','toggle', ()=>{ const d=document.getElementById('circuits-details'); if(d && d.open) refreshAllCircuits(); });
safeBind('traffic-reset','click', resetTraffic);
safeBind('scan-btn','click', scan);
safeBind('conn-btn','click', connectWifi);
safeBind('ks-btn','click', toggleKillSwitch);
safeBind('exit-apply','click', applyExit);

// Timers


// ================= Mode switch polling (async systemd-run) =================
function qs(name){
  try { return new URLSearchParams(window.location.search).get(name); } catch(e){ return null; }
}

async function pollModeSwitch(){
  // If we have ?mode_switch=... OR backend says a switch is running, keep UI in switching state and reload when done.
  const rid = qs('mode_switch');
  const st = await jget('/api/mode/switch', 1500).catch(()=>({running:false}));

  const active = !!(st && st.running);
  const shouldPoll = !!rid || active;

  if (!shouldPoll) return;

  // show switching for longer (mode switch may take > 15s on cold start)
  uiSetSwitching(true, 60000);
  try{
    let tries = 0;
    while (tries++ < 240){ // ~120s at 500ms
      const cur = await jget('/api/mode/switch', 1500).catch(()=>({running:false}));
      if (!cur || !cur.running){
        // switch finished -> reload clean URL without query params
        window.location.href = '/';
        return;
      }
      await new Promise(r => setTimeout(r, 500));
    }
    // timeout -> reload anyway (shows error card if backend wrote one)
    window.location.href = '/';
  } finally {
    // UI will reset on reload
  }
}
// Kick off early on load
pollModeSwitch().catch(()=>{});
refreshKillSwitch(); setInterval(refreshKillSwitch, 4000);
refreshStatus(); setInterval(refreshStatus, 4000);
refreshCircuit(); setInterval(refreshCircuit, 4000);
updateProof(); setInterval(updateProof, 7000);
pollTraffic(); setInterval(pollTraffic, 5000);

// ================= Rotation =================
let __rotNextTs = 0;
let __rotEnabled = false;

function fmtCountdown(sec){
  sec = Math.max(0, Math.floor(sec||0));
  if(sec < 60) return sec + 's';
  const m = Math.floor(sec/60);
  const s = sec % 60;
  return m + 'm ' + s + 's';
}

async function refreshRotation(){
  const d = await jget('/api/cm/rotation', 2500).catch(()=>null);
  if(!d) return;

  document.getElementById('rot-privacy').textContent =
    d.privacy ? 'Privacy mode active' : 'Privacy mode OFF';

  const toggle = document.getElementById('rot-toggle');
  toggle.textContent = d.enabled ? 'Enabled' : 'Disabled';
  toggle.className = d.enabled ? 'btn-primary' : 'btn-secondary';

  document.getElementById('rot-interval').value = d.intervalSeconds || 600;
  document.getElementById('rot-variance').value = d.variancePercent || 20;

  __rotNextTs = d.nextRotationTs || 0;
  __rotEnabled = d.enabled;
  rotUpdateBanner();
}

function updateRotationCountdown(){
  const cd = document.getElementById('rot-countdown');
  const cbox = document.getElementById('rot-countdown-box');
  if(!cd) return;

  if(!__rotEnabled || !__rotNextTs){
    cd.textContent = '—';
    if(cbox) cbox.style.display = __rotEnabled ? 'block' : 'none';
    return;
  }
  const now = Math.floor(Date.now()/1000);
  const diff = __rotNextTs - now;
  if(diff > 0){
    cd.textContent = fmtCountdown(diff);
    cd.style.color = '#2ecc71';
  } else {
    cd.textContent = 'rotating…';
    cd.style.color = '#f1c40f';
  }
}

function rotToast(msg, type){
  const t = document.getElementById('rot-toast');
  if(!t) return;
  t.style.display = 'block';
  t.textContent = msg;
  t.style.background = type === 'ok' ? 'rgba(46,204,113,.15)' : type === 'err' ? 'rgba(231,76,60,.15)' : 'rgba(241,196,15,.15)';
  t.style.color = type === 'ok' ? '#2ecc71' : type === 'err' ? '#e74c3c' : '#f1c40f';
  clearTimeout(t._tid);
  t._tid = setTimeout(()=>{ t.style.display='none'; }, 3500);
}

function rotUpdateBanner(){
  const banner = document.getElementById('rot-status-banner');
  const icon = document.getElementById('rot-status-icon');
  const txt = document.getElementById('rot-status-text');
  const cbox = document.getElementById('rot-countdown-box');
  const hint = document.getElementById('rot-toggle-hint');
  if(!banner) return;

  if(__rotEnabled){
    banner.style.background = 'rgba(46,204,113,.12)';
    banner.style.color = '#2ecc71';
    banner.style.border = '1px solid rgba(46,204,113,.3)';
    icon.textContent = '🟢';
    txt.textContent = 'Rotation ACTIVE';
    if(cbox) cbox.style.display = 'block';
    if(hint) hint.textContent = 'Click to stop rotation';
  } else {
    banner.style.background = 'rgba(255,255,255,.04)';
    banner.style.color = '#888';
    banner.style.border = '1px solid rgba(255,255,255,.08)';
    icon.textContent = '⏸️';
    txt.textContent = 'Rotation INACTIVE';
    if(cbox) cbox.style.display = 'none';
    if(hint) hint.textContent = 'Click to start rotation';
  }
}

async function btnFeedback(btnId, labelId, action){
  const btn = document.getElementById(btnId);
  const lbl = document.getElementById(labelId);
  if(!btn || !lbl) return;
  const origTxt = lbl.textContent;
  btn.disabled = true;
  lbl.textContent = '⏳ Working…';
  btn.style.opacity = '0.7';
  try {
    await action();
    lbl.textContent = '✅ Done!';
    btn.style.opacity = '1';
    rotToast(btnId === 'rot-save' ? 'Settings saved' : 'Rotation triggered!', 'ok');
    setTimeout(()=>{ lbl.textContent = origTxt; }, 1800);
  } catch(e) {
    lbl.textContent = '❌ Failed';
    btn.style.opacity = '1';
    rotToast('Error: ' + (e.message || String(e)), 'err');
    setTimeout(()=>{ lbl.textContent = origTxt; }, 2500);
  } finally {
    btn.disabled = false;
    await refreshRotation();
  }
}

async function saveRotation(){
  await btnFeedback('rot-save', 'rot-save-label', async ()=>{
    const interval = parseInt(document.getElementById('rot-interval').value) || 600;
    const variance = parseInt(document.getElementById('rot-variance').value) || 20;
    await jpost('/api/cm/rotation', {
      enabled: __rotEnabled,
      intervalSeconds: interval,
      variancePercent: variance
    }, 4000);
  });
}

async function toggleRotation(){
  const toggle = document.getElementById('rot-toggle');
  toggle.disabled = true;
  toggle.textContent = '⏳ …';
  try {
    const d = await jget('/api/cm/rotation');
    const newEnabled = !d.enabled;
    const interval = parseInt(document.getElementById('rot-interval').value) || d.intervalSeconds || 600;
    const variance = parseInt(document.getElementById('rot-variance').value) || d.variancePercent || 20;
    await jpost('/api/cm/rotation', {
      enabled: newEnabled,
      intervalSeconds: interval,
      variancePercent: variance
    });
    rotToast(newEnabled ? 'Rotation enabled — timer started' : 'Rotation disabled — timer stopped', 'ok');
  } catch(e) {
    rotToast('Toggle failed: ' + (e.message||String(e)), 'err');
  } finally {
    toggle.disabled = false;
    await refreshRotation();
  }
}

async function triggerRotation(){
  await btnFeedback('rot-trigger', 'rot-trigger-label', async ()=>{
    await jpost('/api/cm/rotation/trigger', {}, 8000);
    await refreshCircuit();
  });
}

safeBind('rot-toggle','click', toggleRotation);
safeBind('rot-save','click', saveRotation);
safeBind('rot-trigger','click', triggerRotation);

refreshRotation();
setInterval(refreshRotation, 5000);
setInterval(updateRotationCountdown, 1000);
// ============================================================
</script>
</body></html>
"""

# ============================================================================
# Routes
# ============================================================================

@app.route("/")
def index():
    return render_template_string(HTML, privacy=_privacy_mode_active(), exit_country=get_current_exit_country(), mode_error=(request.args.get("mode_error") or ""))

# ---- Node circuit-manager proxy endpoints ----

@app.get("/api/cm/status")
def api_cm_status():
    return jsonify(_cm_status_cached())

@app.get("/api/cm/circuit")
def api_cm_circuit():
    return jsonify(_cm_request("/circuit", "GET", None, timeout=12.0))

@app.get("/api/cm/circuits")
def api_cm_circuits():
    order = (request.args.get("order") or "desc").strip().lower()
    return jsonify(_cm_request(f"/circuits?order={order}", "GET", None, timeout=6.0))

@app.post("/api/cm/hopmode")
def api_cm_hopmode():
    d = request.get_json(silent=True) or {}
    hopCount = d.get("hopCount", d.get("hops"))
    try:
        hopCount = int(hopCount)
    except Exception:
        hopCount = 3
    return jsonify(_cm_request("/hopmode", "POST", {"hopCount": hopCount}, timeout=15.0))

@app.post("/api/cm/newnym")
def api_cm_newnym():
    return jsonify(_cm_request("/newnym", "POST", {}, timeout=6.0))

@app.post("/api/cm/exit")
def api_cm_exit():
    d = request.get_json(silent=True) or {}
    cc = str(d.get("exitCountry", "AUTO")).strip().upper()

    # Client JS polls /api/cm/circuit after changing the exit.
    # Keep this call fast to avoid fetch timeouts in the browser.
    wait = bool(d.get("wait", False))
    timeout_ms = int(d.get("timeoutMs", 15000))

    if not re.fullmatch(r"[A-Z]{2}|AUTO", cc or ""):
        return jsonify({"ok": False, "error": "exitCountry must be ISO-2 or AUTO"}), 400

    resp = _cm_request(
        "/exit",
        "POST",
        {"exitCountry": cc, "wait": wait, "timeoutMs": timeout_ms},
        timeout=min(8.0, (timeout_ms / 1000.0) + 1.0),
    )

    # Do NOT mirror into /etc/anonrc by default — it can break explicit circuit building.
    # If you really need legacy mirroring, set MIRROR_EXIT_TO_ANONRC=1.
    if os.environ.get("MIRROR_EXIT_TO_ANONRC", "").strip() == "1":
        try:
            set_exit_country_anonrc(cc)
        except Exception:
            pass

    return jsonify(resp)

@app.get("/api/cm/rotation")
def api_cm_rotation():
    return jsonify(_cm_request("/rotation", "GET", None, timeout=3.0))

@app.post("/api/cm/rotation")
def api_cm_rotation_set():
    d = request.get_json(silent=True) or {}
    payload = {
        "enabled": bool(d.get("enabled")),
        "intervalSeconds": int(d.get("intervalSeconds", 600)),
        "variancePercent": int(d.get("variancePercent", 20)),
    }
    return jsonify(_cm_request("/rotation", "POST", payload, timeout=5.0))

@app.post("/api/cm/rotation/trigger")
def api_cm_rotation_trigger():
    return jsonify(_cm_request("/rotation/trigger", "POST", {}, timeout=10.0))


# ---- Proof + traffic ----

@app.get("/api/traffic")
def api_traffic():
    return jsonify(update_stats())

@app.post("/api/traffic/reset")
def api_traffic_reset():
    global _traffic_offset, _traffic_raw_prev, stats
    try:
        raw_rx = raw_tx = None
        with open("/proc/net/dev") as f:
            for line in f:
                if "usb0" in line:
                    d = line.split()
                    raw_rx, raw_tx = int(d[1]), int(d[9])
                    break
        if raw_rx is None or raw_tx is None:
            return jsonify({"ok": False, "error": "usb0 not found"}), 404
        _traffic_offset["rx"] = int(raw_rx)
        _traffic_offset["tx"] = int(raw_tx)
        now = time.time()
        _traffic_raw_prev.update({"rx": int(raw_rx), "tx": int(raw_tx), "time": now})
        stats.update({"rx": 0, "tx": 0, "time": now, "speed_rx": 0, "speed_tx": 0})
        return jsonify({"ok": True, "rx": 0, "tx": 0}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/api/anyone/proof")
def api_anyone_proof():
    st = _anyone_proof_check()
    privacy = _privacy_mode_active()
    st2 = dict(st)
    st2.update({"privacy": bool(privacy)})
    st2["display_connected"] = bool(st2.get("connected")) and bool(privacy)
    return jsonify(st2), 200

# ---- Exit country (local display) ----
@app.get("/api/exit/current")
def api_exit_current():
    return jsonify({"exit_country": _exit_country_from_manager(), "source": "manager"}), 200

# ---- Kill switch ----
@app.get("/api/killswitch/status")
def api_killswitch_status():
    return jsonify({"enabled": _killswitch_get()}), 200

@app.post("/api/killswitch/set")
def api_killswitch_set():
    d = request.get_json(silent=True) or {}
    enabled = bool(d.get("enabled"))
    state = _killswitch_set(enabled)
    return jsonify({"enabled": state}), 200

# ---- Wi‑Fi ----
@app.get("/wifi/scan")
def w_scan():
    raw = subprocess.check_output("nmcli -t -f SSID,ACTIVE dev wifi list", shell=True).decode("utf-8", errors="replace")
    nets, seen = [], set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        ssid, *rest = line.split(":", 1)
        if not ssid or ssid in seen:
            continue
        active = (rest[0].strip() == "yes") if rest else False
        nets.append({"ssid": ssid, "connected": active})
        seen.add(ssid)
    return jsonify({"networks": nets})

@app.post("/wifi/connect")
def w_conn():
    data = request.get_json(silent=True) or {}
    ssid = str(data.get("ssid", "")).strip()
    pw = str(data.get("password", "")).strip()
    if not ssid:
        return jsonify({"status": "Missing SSID"}), 400
    try:
        subprocess.run(f'nmcli dev wifi connect "{ssid}" password "{pw}"', shell=True, check=True, capture_output=True, timeout=30)
        return jsonify({"status": "Connected!"})
    except subprocess.CalledProcessError:
        return jsonify({"status": "Connection failed"})
    except subprocess.TimeoutExpired:
        return jsonify({"status": "Timeout"})

# ---- Mode scripts ----
@app.post("/mode/privacy")
def mode_privacy():
    # Non-blocking: start switch in background, return immediately.
    run_id = _run_mode_async("privacy")
    return redirect("/?mode_switch=" + urllib.parse.quote(run_id))

@app.post("/mode/normal")
def mode_normal():
    # Non-blocking: start switch in background, return immediately.
    run_id = _run_mode_async("normal")
    return redirect("/?mode_switch=" + urllib.parse.quote(run_id))

@app.get("/api/mode")
def api_mode_get():
    return jsonify({
        "ok": True,
        "privacy": bool(_privacy_mode_active()),
        "routing": "ANYONE" if _privacy_mode_active() else "DIRECT"
    }), 200

@app.post("/api/mode/privacy")
def api_mode_privacy():
    # Reuse the same implementation as the form POST route
    return mode_privacy()

@app.post("/api/mode/normal")
def api_mode_normal():
    return mode_normal()


@app.get("/api/mode/switch")
def api_mode_switch_status():
    st = _mode_read()
    # If a unit is running, ask systemd for its status (best-effort)
    unit = st.get("unit") if isinstance(st, dict) else None
    if unit:
        try:
            rc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
            active = (rc.stdout or "").strip()
            if active in ("inactive", "failed", "active"):
                st["systemd"] = active
            # If not active anymore, read exit code
            if active in ("inactive", "failed"):
                show = subprocess.run(["systemctl", "show", unit, "-p", "ExecMainStatus", "-p", "Result"], capture_output=True, text=True)
                exec_status = None
                result = None
                for ln in (show.stdout or "").splitlines():
                    if ln.startswith("ExecMainStatus="):
                        try: exec_status = int(ln.split("=",1)[1].strip() or "0")
                        except: exec_status = None
                    if ln.startswith("Result="):
                        result = ln.split("=",1)[1].strip()
                st["exit"] = exec_status
                st["result"] = result
                st["running"] = False
                _mode_write(st)
        except Exception as e:
            st["systemd_error"] = str(e)
    return jsonify(st), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, threaded=True)
