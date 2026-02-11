#!/usr/bin/env python3
# ============================================================================
# Anyone Privacy Stick — Portal (app.py)
# AnonController v2: persistent ControlPort + SSE push events
# ============================================================================

from flask import Flask, request, jsonify, render_template_string, redirect, Response
import subprocess, time, os, signal, re, socket, threading, queue, json, logging, random

from pathlib import Path
app = Flask(__name__, static_folder="static")
log = logging.getLogger("anyone-stick")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# ──────────────────────────────────────────────
# Global state
# ──────────────────────────────────────────────
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
ANONRC_PATH = "/etc/anonrc"
rotation_mgr = None

EXIT_COUNTRIES = [
    ("auto", "\U0001f30d Automatic (Best Available)"),
    ("at", "\U0001f1e6\U0001f1f9 Austria"), ("be", "\U0001f1e7\U0001f1ea Belgium"),
    ("bg", "\U0001f1e7\U0001f1ec Bulgaria"), ("br", "\U0001f1e7\U0001f1f7 Brazil"),
    ("ca", "\U0001f1e8\U0001f1e6 Canada"), ("ch", "\U0001f1e8\U0001f1ed Switzerland"),
    ("cz", "\U0001f1e8\U0001f1ff Czech Republic"), ("de", "\U0001f1e9\U0001f1ea Germany"),
    ("dk", "\U0001f1e9\U0001f1f0 Denmark"), ("es", "\U0001f1ea\U0001f1f8 Spain"),
    ("fi", "\U0001f1eb\U0001f1ee Finland"), ("fr", "\U0001f1eb\U0001f1f7 France"),
    ("gb", "\U0001f1ec\U0001f1e7 United Kingdom"), ("hr", "\U0001f1ed\U0001f1f7 Croatia"),
    ("hu", "\U0001f1ed\U0001f1fa Hungary"), ("ie", "\U0001f1ee\U0001f1ea Ireland"),
    ("in", "\U0001f1ee\U0001f1f3 India"), ("is", "\U0001f1ee\U0001f1f8 Iceland"),
    ("it", "\U0001f1ee\U0001f1f9 Italy"), ("jp", "\U0001f1ef\U0001f1f5 Japan"),
    ("kr", "\U0001f1f0\U0001f1f7 South Korea"), ("lu", "\U0001f1f1\U0001f1fa Luxembourg"),
    ("md", "\U0001f1f2\U0001f1e9 Moldova"), ("nl", "\U0001f1f3\U0001f1f1 Netherlands"),
    ("no", "\U0001f1f3\U0001f1f4 Norway"), ("nz", "\U0001f1f3\U0001f1ff New Zealand"),
    ("pl", "\U0001f1f5\U0001f1f1 Poland"), ("pt", "\U0001f1f5\U0001f1f9 Portugal"),
    ("ro", "\U0001f1f7\U0001f1f4 Romania"), ("rs", "\U0001f1f7\U0001f1f8 Serbia"),
    ("se", "\U0001f1f8\U0001f1ea Sweden"), ("sg", "\U0001f1f8\U0001f1ec Singapore"),
    ("sk", "\U0001f1f8\U0001f1f0 Slovakia"), ("ua", "\U0001f1fa\U0001f1e6 Ukraine"),
    ("us", "\U0001f1fa\U0001f1f8 United States"),
]


# ISO country code → English name
COUNTRY_NAME_MAP = {
    "DE": "Germany",
    "NL": "Netherlands",
    "US": "United States",
    "FR": "France",
    "GB": "United Kingdom",
    "ES": "Spain",
    "IT": "Italy",
    "PL": "Poland",
    "SE": "Sweden",
    "NO": "Norway",
    "FI": "Finland",
    "CH": "Switzerland",
    "AT": "Austria",
    "CZ": "Czech Republic",
    "RO": "Romania",
    "BG": "Bulgaria",
    "HU": "Hungary",
    "PT": "Portugal",
    "CA": "Canada",
    "AU": "Australia",
    "JP": "Japan",
    "SG": "Singapore"
}


# ============================================================================
# AnonController v2 — Persistent connection with event support
# ============================================================================



# ============================================================================
# ============================================================================
# AnonController
# ============================================================================
class AnonController:
    """
    Robust ControlPort controller:
    - One persistent socket for async events (SETEVENTS CIRC STATUS_CLIENT)
    - Separate one-shot socket for GETINFO / SIGNAL to avoid races with event reader
    """
    CONTROL_HOST = "127.0.0.1"
    CONTROL_PORT = 9051
    COOKIEFILE   = "/var/lib/anon/control_auth_cookie"

    def __init__(self):
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._sse_clients = []

        self._connected = False
        self._bootstrap = {"progress": 0, "summary": "Starting…"}
        self._circuit_hops = []
        self._last_circ_ts = 0.0

        self._stop = threading.Event()
        self._thr = None

        self._ns_cache = {}   # fp -> (nickname, ip)
        self._geo_cache = {}  # ip -> (cc, country_name)

    def start(self):
        if self._thr and self._thr.is_alive():
            return
        self._thr = threading.Thread(target=self._run, name="anonctl", daemon=True)
        self._thr.start()
        log.info("AnonController started (ControlPort reconnect loop running)")

    def get_status(self):
        try:
            running = (subprocess.run(["pgrep","-x","anon"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0)
        except Exception:
            running = False

        with self._state_lock:
            hops = list(self._circuit_hops)
            bs = dict(self._bootstrap)

        if running and hops:
            return {"type":"status","state":"connected","progress":100,"summary":"Connected"}

        if not running:
            return {"type":"status","state":"stopped","progress":0,"summary":"Stopped"}

        p = int(bs.get("progress") or 0)
        p = 1 if p <= 0 else p
        p = max(1, min(p, 99)) if p < 100 else 100
        summ = bs.get("summary") or ("Bootstrapping %d%%" % p)
        return {"type":"status","state":"bootstrapping" if p < 100 else "connected",
                "progress": p, "summary": summ}

    def get_circuit_detail(self):
        # Ensure UI gets hops even if no CIRC events arrive
        now = time.time()
        with self._state_lock:
            hops = list(self._circuit_hops)
            last = float(self._last_circ_ts or 0.0)
        if (not hops) or (now - last > 5.0):
            try:
                self._refresh_circuit_from_getinfo()
            except Exception:
                pass
            with self._state_lock:
                hops = list(self._circuit_hops)
        return hops

    def _parse_path_to_hops(self, path: str):
        # PATH token from ControlPort can be like:
        #   =Nick,=Nick,=Nick
        # or (your case):
        #   ~Nick,~Nick,~Nick
        items = [x.strip() for x in (path or "").split(",") if x.strip()]
        roles = ["entry", "middle", "exit"]
        hops = []
        for idx, it in enumerate(items[:3]):
            fp = ""; nick = ""
            it = it.strip()
            if it.startswith(""):
                it2 = it[1:]
                if "=" in it2:
                    fp, nick = it2.split("=", 1)
                elif "~" in it2:
                    fp, nick = it2.split("~", 1)
                else:
                    fp = it2
            else:
                # fallback if format changes
                fp = it

            fp = fp.strip()
            nick = (nick or "").strip()
            # Some formats may embed nick into fp accidentally
            if "~" in fp and not nick:
                fp, nick = fp.split("~", 1)
                fp = fp.strip(); nick = nick.strip()

            hops.append({
                "role": roles[idx] if idx < len(roles) else f"hop{idx+1}",
                "fingerprint": fp,
                "nickname": nick,
                "ip": "",
                "country_code": "",
                "country_name": "",
            })
        return hops

    def _refresh_circuit_from_getinfo(self):
        # Pull latest built circuit from ControlPort even without events
        rep = self._one_shot(["GETINFO circuit-status"], timeout=3)
        # Expect lines like: 250+circuit-status=
        # <id> BUILT =nick,=nick PURPOSE=GENERAL
        # .
        best_path = ""
        for ln in rep.splitlines():
            ln = ln.strip()
            if (" BUILT " in ln) and ("" in ln):
                # take the PATH token right after BUILT
                try:
                    rest = ln.split(" BUILT ", 1)[1]
                    best_path = rest.split(" ", 1)[0]
                except Exception:
                    continue
        if not best_path:
            return
        hops = self._parse_path_to_hops(best_path)
        hops = self._enrich_hops(hops)
        with self._state_lock:
            self._circuit_hops = hops
            self._last_circ_ts = time.time()
        self._push({"type":"circuit","status":"REFRESH","hops":hops})

    def new_circuit(self):
        r = self.command("SIGNAL NEWNYM")
        self._push({"type":"circuit", "status":"NEWNYM", "hops":[]})
        return r

    def add_sse_client(self):
        q = queue.Queue()
        with self._lock:
            self._sse_clients.append(q)
        try:
            q.put_nowait(self.get_status())
            q.put_nowait({"type":"circuit","status":"SNAPSHOT","hops":self.get_circuit_detail()})
            try:
                if rotation_mgr:
                    q.put_nowait(rotation_mgr.get_state())
            except Exception:
                pass
        except Exception:
            pass
        return q

    def remove_sse_client(self, q):
        with self._lock:
            if q in self._sse_clients:
                self._sse_clients.remove(q)

    def _push(self, evt: dict):
        with self._lock:
            for q in list(self._sse_clients):
                try:
                    q.put_nowait(evt)
                except Exception:
                    pass

    def _read_cookie_hex(self) -> str:
        return Path(self.COOKIEFILE).read_bytes().hex()

    def _read_reply_lines(self, f) -> str:
        out = []
        while True:
            try:
                line = f.readline()
            except TimeoutError:
                # ControlPort can be quiet; a socket timeout is not a disconnect.
                continue

            if not line:
                break

            sline = line.decode("utf-8", errors="replace").rstrip("\r\n")
            out.append(sline)

            # 250-... multi-line replies end with a final "250 ..."
            if sline.startswith("250-"):
                continue
            if sline.startswith(("250 ", "250 OK", "250 closing", "4", "5", "515 ")):
                break

        return "\n".join(out)

    def _one_shot(self, cmds, timeout=3) -> str:
        import socket
        cookie = self._read_cookie_hex()
        with socket.create_connection((self.CONTROL_HOST, self.CONTROL_PORT), timeout=timeout) as sock:
            sock.settimeout(timeout)
            f = sock.makefile("rwb", buffering=0)
            f.write(("AUTHENTICATE %s\r\n" % cookie).encode("utf-8"))
            f.flush()
            auth = self._read_reply_lines(f)
            if "250" not in auth:
                return auth

            rep = ""
            for c in cmds:
                f.write((c + "\r\n").encode("utf-8"))
                f.flush()
                rep = self._read_reply_lines(f)

            f.write(b"QUIT\r\n")
            f.flush()
            return rep

    def command(self, cmd: str, timeout=3) -> str:
        return self._one_shot([cmd], timeout=timeout)

    def _run(self):
        import socket
        while not self._stop.is_set():
            sock = None
            try:
                sock = socket.create_connection((self.CONTROL_HOST, self.CONTROL_PORT), timeout=3)
                sock.settimeout(30)
                f = sock.makefile("rwb", buffering=0)

                cookie = self._read_cookie_hex()
                f.write(("AUTHENTICATE %s\r\n" % cookie).encode("utf-8"))
                f.flush()
                auth = self._read_reply_lines(f)
                if "250" not in auth:
                    log.warning("AUTH failed: %s", auth)
                    time.sleep(1.5)
                    continue

                f.write(b"SETEVENTS CIRC STATUS_CLIENT\r\n")
                f.flush()
                rep = self._read_reply_lines(f)
                if "250" not in rep:
                    log.warning("SETEVENTS failed: %s", rep)
                else:
                    log.info("Subscribed to CIRC + STATUS_CLIENT events")

                self._push(self.get_status())
                self._push({"type":"circuit","status":"SNAPSHOT","hops":self.get_circuit_detail()})

                while not self._stop.is_set():
                    try:
                        line = f.readline()
                    except TimeoutError:
                        # No events right now (socket timeout). Keep the reader loop alive.
                        continue
                    if not line:
                        raise RuntimeError("EOF from ControlPort")
                    sline = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if sline.startswith("650 "):
                        self._handle_async(sline)

            except Exception as e:
                log.exception("Reader loop exited — marking disconnected: %s", e)
                time.sleep(1.5)
            finally:
                try:
                    if sock: sock.close()
                except Exception:
                    pass

    def _handle_async(self, sline: str):
        try:
            if sline.startswith("650 STATUS_CLIENT") and " BOOTSTRAP " in sline:
                m1 = re.search(r"PROGRESS=(\d+)", sline)
                m2 = re.search(r'SUMMARY="([^"]+)"', sline)
                p = int(m1.group(1)) if m1 else 1
                summ = m2.group(1) if m2 else ("Bootstrapping %d%%" % p)
                with self._state_lock:
                    self._bootstrap = {"progress": p, "summary": summ}
                self._push({"type":"status","state":"bootstrapping" if p < 100 else "connected",
                            "progress": max(1, min(p, 100)), "summary": summ})
                return

            if sline.startswith("650 CIRC ") and " BUILT " in sline:
                parts = sline.split(" BUILT ", 1)
                if len(parts) < 2:
                    return
                rest = parts[1]
                path = rest.split(" ", 1)[0]
                items = [x for x in path.split(",") if x]
                roles = ["entry", "middle", "exit"]
                hops = []
                for idx, it in enumerate(items[:3]):
                    fp = ""
                    nick = ""
                    if it.startswith("$"):
                        it2 = it[1:]
                        if "=" in it2:
                            fp, nick = it2.split("=", 1)
                        else:
                            fp = it2
                    hops.append({
                        "role": roles[idx],
                        "fingerprint": fp,
                        "nickname": nick,
                        "ip": "",
                        "country_code": "",
                        "country_name": "",
                    })

                hops = self._enrich_hops(hops)
                with self._state_lock:
                    self._circuit_hops = hops

                log.info("Circuit BUILT (%d hops)", len(hops))
                self._push({"type":"circuit","status":"BUILT","hops":hops})
                self._push(self.get_status())

                try:
                    if rotation_mgr:
                        rotation_mgr.note_circuit_built()
                except Exception:
                    pass
                return

        except Exception:
            pass

    def _enrich_hops(self, hops):
        for h in hops:
            fp = (h.get("fingerprint") or "").strip()
            if not fp:
                continue

            if fp in self._ns_cache:
                nick, ip = self._ns_cache[fp]
            else:
                nick, ip = (h.get("nickname") or ""), ""
                try:
                    rep = self._one_shot([f"GETINFO ns/id/{fp}"], timeout=3)
                    for ln in rep.splitlines():
                        ln = ln.strip()
                        if ln.startswith("r "):
                            toks = ln.split()
                            if len(toks) >= 7:
                                nick = toks[1]
                                ip = toks[-3]
                            break
                    self._ns_cache[fp] = (nick, ip)
                except Exception:
                    pass

            if nick:
                h["nickname"] = nick
            if ip:
                h["ip"] = ip
                if ip in self._geo_cache:
                    cc, cn = self._geo_cache[ip]
                else:
                    cc, cn = "", ""
                    try:
                        rep2 = self._one_shot([f"GETINFO ip-to-country/{ip}"], timeout=3)
                        for ln in rep2.splitlines():
                            if "ip-to-country/" in ln and "=" in ln:
                                cc = ln.split("=", 1)[1].strip()
                                break
                    except Exception:
                        pass
                    if not cn and cc:
                        cn = COUNTRY_NAME_MAP.get(cc.upper(), "")
                    self._geo_cache[ip] = (cc, cn)

                h["country_code"] = cc or ""
                h["country_name"] = cn or ""
        return hops


# Helper functions
# ============================================================================

def update_stats():
    global stats
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if "usb0" in line:
                    d = line.split()
                    rx, tx, t = int(d[1]), int(d[9]), time.time()
                    if stats["time"] > 0:
                        dt = max(t - stats["time"], 0.001)
                        stats["speed_rx"] = (rx - stats["rx"]) / dt
                        stats["speed_tx"] = (tx - stats["tx"]) / dt
                    stats.update({"rx": rx, "tx": tx, "time": t})
    except Exception:
        pass
    return stats


def get_current_exit_country():
    try:
        with open(ANONRC_PATH) as f:
            for line in f:
                if line.strip().startswith("ExitNodes"):
                    m = re.search(r"\{(\w+)\}", line)
                    if m:
                        return m.group(1).lower()
    except FileNotFoundError:
        pass
    return "auto"


def set_exit_country(country_code):
    try:
        with open(ANONRC_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    new = [l for l in lines if not l.strip().startswith(("ExitNodes", "StrictNodes"))]
    if new and not new[-1].endswith("\n"):
        new[-1] += "\n"
    if country_code != "auto":
        new.append(f"ExitNodes {{{country_code}}}\n")
        new.append("StrictNodes 1\n")

    with open(ANONRC_PATH, "w") as f:
        f.writelines(new)

    # Reload anon config via SIGHUP
    try:
        pid = subprocess.check_output("pgrep -x anon", shell=True).decode().strip().split("\n")[0]
        os.kill(int(pid), signal.SIGHUP)
    except Exception:
        pass
    return True




# ──────────────────────────────────────────────
# Anyone Proof + Leak Detection
# ──────────────────────────────────────────────
ANYONE_CHECK_URL = "https://check.en.anyone.tech/"
ANYONE_PROOF_TTL_SECONDS = 8

_anyone_cache = {"ts": 0.0, "connected": False, "ip": "", "reason": "not_checked", "leak_ok": None, "leak_issues": []}

def _privacy_mode_active() -> bool:
    try:
        return subprocess.run("sudo iptables -t nat -S PREROUTING | grep -q -- '--to-ports 9040'",
                              shell=True, capture_output=True).returncode == 0
    except Exception:
        return False


def _anyone_proof_check():
    """
    Proof via anon SOCKS (127.0.0.1:9050) against check.en.anyone.tech.
    """
    global _anyone_cache
    now = time.time()

    if (now - float(_anyone_cache.get("ts", 0.0))) < ANYONE_PROOF_TTL_SECONDS:
        return _anyone_cache

    cmd = [
        "curl", "-sS",
        "--max-time", "6",
        "--connect-timeout", "4",
        "--socks5-hostname", "127.0.0.1:9050",
        ANYONE_CHECK_URL
    ]

    connected = False
    ip = ""
    reason = "unknown"

    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        body = (r.stdout or "") + "\n" + (r.stderr or "")

        # CONNECTED (observed wording)
        if re.search(r"congratulations\.|you\s+can\s+be\s+anyone", body, re.IGNORECASE):
            connected = True
            reason = "connected"
        elif re.search(r"connected\s+to\s+anyone", body, re.IGNORECASE):
            connected = True
            reason = "connected"
        # NOT CONNECTED
        elif re.search(r"(sorry\.|not\s+connected).*anyone", body, re.IGNORECASE):
            connected = False
            reason = "not_connected"
        elif r.returncode != 0:
            connected = False
            reason = f"curl_rc_{r.returncode}"
        else:
            connected = False
            reason = "unrecognized_response"

        m = re.search(r"ip address appears to be:\s*([0-9a-fA-F\.:]+)", body, re.IGNORECASE)
        if m:
            ip = m.group(1).strip()

    except Exception as e:
        connected = False
        reason = f"exception:{e.__class__.__name__}"

    _anyone_cache.update({
        "ts": now,
        "connected": bool(connected),
        "ip": ip,
        "reason": reason
    })

    return _anyone_cache


# ──────────────────────────────────────────────
# Circuit Rotation (timer-based NEWNYM)
# ──────────────────────────────────────────────
ROTATION_STATE_PATH = "/var/lib/anyone-stick/rotation.json"
DEFAULT_ROTATE_SECONDS = 600
DEFAULT_VARIANCE_PERCENT = 0
MIN_ROTATE_SECONDS = 60
MAX_ROTATE_SECONDS = 86400
ROTATION_BUILD_TIMEOUT = 15

def _load_rotation_state():
    try:
        with open(ROTATION_STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        enabled = bool(d.get("enabled", False))
        interval = int(d.get("interval_seconds", DEFAULT_ROTATE_SECONDS))
        interval = max(MIN_ROTATE_SECONDS, min(interval, MAX_ROTATE_SECONDS))
        variance = int(d.get("variance_percent", DEFAULT_VARIANCE_PERCENT))
        variance = max(0, min(variance, 50))
        return {"enabled": enabled, "interval_seconds": interval, "variance_percent": variance}
    except Exception:
        return {"enabled": False, "interval_seconds": DEFAULT_ROTATE_SECONDS, "variance_percent": DEFAULT_VARIANCE_PERCENT}

def _save_rotation_state(enabled: bool, interval_seconds: int, variance_percent: int):
    os.makedirs(os.path.dirname(ROTATION_STATE_PATH), exist_ok=True)
    tmp = ROTATION_STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {
                "enabled": bool(enabled),
                "interval_seconds": int(interval_seconds),
                "variance_percent": int(variance_percent),
            },
            f,
        )
    os.replace(tmp, ROTATION_STATE_PATH)

class RotationManager(threading.Thread):
    """
    Timer-based circuit rotation:
    - When enabled and privacy mode is active, triggers NEWNYM on schedule.
    - Smooth: existing streams keep running; new streams use new circuits once built.
    - Watchdog prevents UI from getting stuck in 'rotating'.
    """

    def __init__(self, ctrl):
        super().__init__(daemon=True)
        self.ctrl = ctrl
        st = _load_rotation_state()
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()

        self.enabled = bool(st.get("enabled", False))
        self.interval_seconds = int(st.get("interval_seconds", DEFAULT_ROTATE_SECONDS))
        self.variance_percent = int(st.get("variance_percent", DEFAULT_VARIANCE_PERCENT))

        self.next_rotate_at = 0
        self.last_rotated_at = 0
        self.phase = "disabled" if not self.enabled else "idle"
        self.rotation_pending = False

        self._schedule_next(time.time())

    def stop(self):
        self._stop_evt.set()

    def _privacy_active(self) -> bool:
        # Prefer app's own mode flag if present; fallback to iptables.
        try:
            for k in ("CURRENT_MODE", "MODE", "mode"):
                if k in globals():
                    return str(globals().get(k, "")).lower() == "privacy"
        except Exception:
            pass
        try:
            cmd = "sudo iptables -t nat -S | grep -E '(REDIRECT|DNAT).*9040|--to-ports 9040' >/dev/null"
            return subprocess.run(cmd, shell=True).returncode == 0
        except Exception:
            return False

    def _effective_interval(self) -> int:
        base = max(MIN_ROTATE_SECONDS, min(int(self.interval_seconds), MAX_ROTATE_SECONDS))
        var = max(0, min(int(self.variance_percent), 50))
        if var <= 0:
            return base
        delta = base * (var / 100.0)
        jitter = random.uniform(-delta, +delta)
        eff = int(base + jitter)
        return max(MIN_ROTATE_SECONDS, min(eff, MAX_ROTATE_SECONDS))

    def _schedule_next(self, now_ts: float):
        with self._lock:
            if not self.enabled:
                self.next_rotate_at = 0
                self.phase = "disabled"
                return
            eff = self._effective_interval()
            self.next_rotate_at = int(now_ts + eff)
            if self.phase == "disabled":
                self.phase = "idle"

    def get_state(self):
        with self._lock:
            now = int(time.time())
            remaining = max(0, int(self.next_rotate_at - now)) if self.next_rotate_at else 0
            return {
                "type": "rotation",
                "enabled": bool(self.enabled),
                "interval_seconds": int(self.interval_seconds),
                "variance_percent": int(self.variance_percent),
                "next_rotate_at": int(self.next_rotate_at) if self.next_rotate_at else 0,
                "last_rotated_at": int(self.last_rotated_at) if self.last_rotated_at else 0,
                "remaining_seconds": int(remaining),
                "phase": self.phase,
                "pending": bool(self.rotation_pending),
            }

    def update(self, enabled: bool, interval_seconds: int, variance_percent: int = 0):
        interval_seconds = max(MIN_ROTATE_SECONDS, min(int(interval_seconds), MAX_ROTATE_SECONDS))
        variance_percent = max(0, min(int(variance_percent), 50))
        with self._lock:
            self.enabled = bool(enabled)
            self.interval_seconds = interval_seconds
            self.variance_percent = variance_percent
            self.rotation_pending = False
            self.phase = "disabled" if not self.enabled else "idle"
            _save_rotation_state(self.enabled, self.interval_seconds, self.variance_percent)
        self._schedule_next(time.time())
        self.ctrl._push(self.get_state())

    def trigger(self, reason: str = "timer"):
        with self._lock:
            if not self.enabled or self.rotation_pending:
                return False
            if not self._privacy_active():
                self.phase = "waiting_for_privacy"
                self.ctrl._push(self.get_state())
                return False
            self.rotation_pending = True
            self.phase = "rotating"
            self.last_rotated_at = int(time.time())

        st = self.get_state()
        st["reason"] = reason
        self.ctrl._push(st)

        try:
            self.ctrl.new_circuit()
        except Exception:
            with self._lock:
                self.phase = "error"
                self.rotation_pending = False
            self.ctrl._push(self.get_state())
            return False

        def _watchdog():
            time.sleep(ROTATION_BUILD_TIMEOUT)
            with self._lock:
                if self.rotation_pending:
                    self.rotation_pending = False
                    self.phase = "idle" if self.enabled else "disabled"
            self._schedule_next(time.time())
            self.ctrl._push(self.get_state())

        threading.Thread(target=_watchdog, daemon=True).start()
        return True

    def note_circuit_built(self):
        with self._lock:
            if self.rotation_pending:
                self.rotation_pending = False
                self.phase = "swapped"
        self._schedule_next(time.time())
        self.ctrl._push(self.get_state())

        def _idle_later():
            time.sleep(2)
            with self._lock:
                if self.enabled and not self.rotation_pending and self.phase == "swapped":
                    self.phase = "idle"
            self.ctrl._push(self.get_state())

        threading.Thread(target=_idle_later, daemon=True).start()

    def run(self):
        self.ctrl._push(self.get_state())
        while not self._stop_evt.is_set():
            time.sleep(0.5)
            if not self.enabled:
                continue
            if not self.next_rotate_at:
                self._schedule_next(time.time())
                continue
            if int(time.time()) >= int(self.next_rotate_at):
                self.trigger("timer")

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anyone Privacy Stick</title>
<link href="https://fonts.googleapis.com/css2?family=Mona+Sans:wght@200..900&display=swap" rel="stylesheet">
<style>
  :root { --primary:#0280AF; --secondary:#03BDC5; --gradient:linear-gradient(90deg,#0280AF 0%,#03BDC5 100%); --bg:#0b1116; --card:#151b23; --text:#FFF; --dim:#8b949e; --border:#30363d; }
  * { box-sizing:border-box; }
  body { font-family:"Mona Sans",sans-serif; background:var(--bg); color:var(--text); margin:0; padding:20px; display:flex; flex-direction:column; align-items:center; }
  .container { width:100%; max-width:420px; }
  .logo-img { max-width:180px; height:auto; display:block; margin:0 auto 20px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:24px; margin-bottom:20px; }
  h3 { font-size:11px; text-transform:uppercase; color:var(--secondary); margin:0 0 15px; font-weight:800; }

  /* Status badge */
  .status-indicator { display:flex; align-items:center; justify-content:center; padding:15px; border-radius:8px; font-weight:600; margin-bottom:20px; background:rgba(255,255,255,0.03); }
  .dot { height:8px; width:8px; border-radius:50%; margin-right:12px; background:#555; flex-shrink:0; }
  .active .dot { background:var(--secondary); box-shadow:0 0 12px var(--secondary); }

  /* Connection status */
  .conn-badge { display:inline-flex; align-items:center; gap:8px; padding:8px 14px; border-radius:8px; font-weight:700; font-size:13px; }
  .conn-badge.stopped { background:rgba(248,81,73,0.12); color:#f85149; }
  .conn-badge.bootstrapping { background:rgba(210,153,34,0.12); color:#d2992a; }
  .conn-badge.connected { background:rgba(3,189,197,0.12); color:var(--secondary); }
  .conn-badge.error { background:rgba(248,81,73,0.12); color:#f85149; }
  .conn-dot { height:8px; width:8px; border-radius:50%; flex-shrink:0; }
  .stopped .conn-dot { background:#f85149; }
  .bootstrapping .conn-dot { background:#d2992a; animation:pulse 1.2s infinite; }
  .connected .conn-dot { background:var(--secondary); box-shadow:0 0 10px var(--secondary); }
  .error .conn-dot { background:#f85149; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .progress-bar-bg { width:100%; height:6px; background:rgba(255,255,255,0.06); border-radius:3px; margin-top:14px; overflow:hidden; }
  .progress-bar-fill { height:100%; border-radius:3px; background:var(--gradient); transition:width .6s ease; }
  .conn-summary { font-size:12px; color:var(--dim); margin-top:8px; }

  /* Circuit chain */
  .circuit-chain { display:flex; align-items:stretch; justify-content:center; gap:0; margin:10px 0; }
  .circuit-node { flex:1; background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:10px; padding:12px 8px; text-align:center; min-width:0; }
  .circuit-node.active-node { border-color:var(--secondary); background:rgba(3,189,197,0.06); }
  .node-role { font-size:9px; font-weight:800; text-transform:uppercase; color:var(--secondary); margin-bottom:6px; letter-spacing:0.5px; }
  .node-flag { font-size:26px; line-height:1; margin-bottom:4px; }
  .node-name { font-size:11px; font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .node-ip { font-size:10px; color:var(--dim); font-family:monospace; margin-top:2px; }
  .node-country { font-size:10px; color:var(--dim); margin-top:1px; }
  .circuit-arrow { display:flex; align-items:center; padding:0 4px; font-size:16px; color:var(--secondary); font-weight:700; }
  .circuit-empty { text-align:center; color:var(--dim); font-size:12px; padding:20px 0; }
  .btn-sm { padding:10px 16px; font-size:12px; border-radius:6px; font-weight:700; cursor:pointer; border:none; font-family:inherit; margin-top:12px; }

  /* Traffic */
  .traffic-grid { display:grid; grid-template-columns:1fr 1fr; gap:15px; }
  .traffic-val { font-size:18px; font-weight:700; }
  .traffic-speed { font-size:11px; color:var(--secondary); font-weight:600; }

  /* Buttons */
  button { width:100%; padding:16px; border:none; border-radius:8px; font-size:14px; font-weight:700; cursor:pointer; transition:.2s; font-family:inherit; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .btn-primary { background:var(--gradient); color:#fff; }
  .btn-secondary { background:#21262d; color:#fff; border:1px solid var(--border); }

  /* Wi-Fi / Forms */
  .wifi-item { padding:12px; border-bottom:1px solid var(--border); cursor:pointer; display:flex; justify-content:space-between; font-size:14px; }
  .connected-label { color:var(--secondary); font-weight:800; font-size:10px; border:1px solid var(--secondary); padding:2px 6px; border-radius:4px; }
  input,select { width:100%; padding:14px; background:#0d1117; border:1px solid var(--border); border-radius:8px; color:#fff; margin:10px 0; font-family:inherit; font-size:14px; }
  select { appearance:none; -webkit-appearance:none; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%238b949e' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 14px center; cursor:pointer; }
  select option { background:#0d1117; color:#fff; }
  .helper-text { font-size:11px; color:var(--dim); margin-top:4px; }
  .circuit-status { display:flex; align-items:center; gap:8px; margin-bottom:15px; padding:10px; border-radius:6px; font-size:12px; font-weight:600; }
  .circuit-status.active { background:rgba(3,189,197,0.08); color:var(--secondary); }
  .circuit-status.inactive { background:rgba(255,255,255,0.03); color:var(--dim); }

  /* SSE connection indicator */
  .sse-dot { display:inline-block; width:6px; height:6px; border-radius:50%; margin-left:8px; vertical-align:middle; }
  .sse-dot.live { background:#03BDC5; box-shadow:0 0 6px #03BDC5; }
  .sse-dot.dead { background:#f85149; }

  /* Anyone proof banner */
  .proof-banner {
    position: fixed;
    left: 0; right: 0; bottom: 0;
    z-index: 9999;
    padding: 12px 14px;
    font-weight: 900;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    text-align: center;
    border-top: 1px solid rgba(255,255,255,0.10);
    backdrop-filter: blur(8px);
  }
  .proof-banner.disconnected {
    background: rgba(248,81,73,0.18);
    color: #ff8a84;
  }
  .proof-banner.connected {
    background: rgba(63,185,80,0.18);
    color: #7ee787;
  }
  .proof-sub {
    display:block;
    margin-top: 4px;
    font-size: 11px;
    font-weight: 700;
    opacity: 0.95;
    text-transform: none;
    letter-spacing: 0;
  }
  body { padding-bottom: 70px; } /* keep content above banner */

</style>
</head>
<body>

  <div id="killswitch-panel" style="position:sticky;top:0;z-index:9999;padding:10px 12px;background:rgba(20,20,20,.95);border-bottom:1px solid rgba(255,255,255,.08);">
    <div style="display:flex;gap:12px;align-items:center;justify-content:space-between;max-width:1100px;margin:0 auto;">
      <div style="display:flex;flex-direction:column;gap:2px;">
        <div style="font-weight:700;font-size:14px;letter-spacing:.2px;">Kill Switch</div>
        <div id="killswitch-sub" style="opacity:.85;font-size:12px;">Blocks all non-local traffic from the Stick (Normal + Privacy mode).</div>
      </div>
      <button id="killswitch-btn" type="button"
        style="padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.15);font-weight:700;cursor:pointer;min-width:170px;">
        Kill Switch: …
      

</button>
<script id="ks-js-proof">
(function(){
  const btn = document.getElementById('killswitch-btn');
  const sub = document.getElementById('killswitch-sub');
  if(!btn) return;

  function uiTemp(){
    btn.textContent = 'Kill Switch: …';
    btn.style.background = 'rgba(255,255,255,.08)';
    btn.style.borderColor = 'rgba(255,255,255,.15)';
    btn.style.color = '#fff';
    if(sub) sub.textContent = 'Loading status…';
  }

  function uiErr(msg){
    btn.textContent = 'Kill Switch: error';
    btn.style.background = 'rgba(255,255,255,.08)';
    btn.style.borderColor = 'rgba(255,255,255,.15)';
    btn.style.color = '#fff';
    if(sub) sub.textContent = 'Kill Switch error: ' + msg;
  }

  function uiOk(on){
    btn.textContent = on ? 'Kill Switch: ON' : 'Kill Switch: OFF';
    btn.style.background = on ? 'rgba(220, 38, 38, .95)' : 'rgba(34, 197, 94, .20)';
    btn.style.borderColor = on ? 'rgba(220, 38, 38, .8)' : 'rgba(34, 197, 94, .35)';
    btn.style.color = '#fff';
    if(sub){
      sub.textContent = on
        ? 'Egress is blocked. Only local management traffic is allowed.'
        : 'Blocks all non-local traffic from the Stick (Normal + Privacy mode).';
    }
  }

  async function fetchJson(url, timeoutMs){
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try{
      const r = await fetch(url, { cache:'no-store', signal: ctl.signal });
      const ct = (r.headers.get('content-type') || '').toLowerCase();
      const txt = await r.text();
      if(!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
      if(!ct.includes('application/json')) {
        // show first chars to debug
        throw new Error('Non-JSON response (' + ct + '): ' + txt.slice(0,120));
      }
      return JSON.parse(txt);
    } finally {
      clearTimeout(t);
    }
  }

  async function refresh(){
    try{
      const st = await fetchJson('/api/killswitch/status', 2500);
      uiOk(!!st.enabled);
      return !!st.enabled;
    } catch(e){
      uiErr((e && e.name === 'AbortError') ? 'timeout fetching /api/killswitch/status' : (e && e.message ? e.message : String(e)));
      return null;
    }
  }

  async function setEnabled(enabled){
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2500);
    try{
      const r = await fetch('/api/killswitch/set', {
        method:'POST',
        headers:{ 'Content-Type':'application/json' },
        body: JSON.stringify({ enabled }),
        signal: ctl.signal
      });
      if(!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
    } finally {
      clearTimeout(t);
    }
  }

  function bindOnce(){
    if(btn.dataset.ksBound === "1") return;
    btn.dataset.ksBound = "1";

    btn.addEventListener('click', async () => {
      const cur = await refresh();
      const target = !cur;

      if(target){
        const ok = confirm('Enable Kill Switch?

This blocks all non-local traffic from the Stick (Normal + Privacy mode).');
        if(!ok) return;
      }

      uiTemp();
      try{ await setEnabled(target); } catch(e){
        uiErr((e && e.name === 'AbortError') ? 'timeout calling /api/killswitch/set' : (e && e.message ? e.message : String(e)));
        return;
      }
      await refresh();
    });
  }

  uiTemp();
  bindOnce();
  refresh();
  setInterval(refresh, 2500);
})();
</script>

</div>
  </div>
<div class="container">
  <img src="/static/logo.png" onerror="this.src='/static/logo.png'" class="logo-img">

  <!-- Connection Status -->
  <div class="card">
    <h3>Anyone Connection <span class="sse-dot dead" id="sse-dot" title="Live Events"></span></h3>
    <div id="conn-badge" class="conn-badge stopped">
      <div class="conn-dot"></div>
      <span id="conn-label">Checking\u2026</span>
    </div>
    <div class="progress-bar-bg"><div class="progress-bar-fill" id="conn-progress" style="width:0%"></div></div>
    <div class="conn-summary" id="conn-summary">Waiting for status\u2026</div>
  </div>

  <!-- Circuit Chain -->
  <div class="card">
    <h3>Circuit Chain</h3>
    <div id="circuit-container">
      <div class="circuit-empty">No circuit available</div>
    </div>
    <button class="btn-sm btn-secondary" style="width:auto" onclick="newCircuit()" id="newnym-btn">&#x1f504; New Circuit</button>
  </div>

  <!-- Mode Switch -->
  <div class="card">
    <h3>Mode</h3>
    <div class="status-indicator {{ 'active' if privacy else '' }}"><div class="dot"></div>{{ 'PRIVACY ACTIVE' if privacy else 'NORMAL MODE' }}</div>
    <form action="/mode/{{ 'normal' if privacy else 'privacy' }}" method="post"><button class="{{ 'btn-secondary' if privacy else 'btn-primary' }}">{{ 'Switch to Normal' if privacy else 'Enable Privacy' }}</button></form>
  </div>

  <!-- Live Traffic -->
  <div class="card">
    <h3>Live Traffic</h3>
    <div class="traffic-grid">
      <div><div style="font-size:10px;color:var(--dim)">DOWNLOAD</div><div class="traffic-val" id="rx">0 MB</div><div class="traffic-speed" id="s_rx">0 KB/s</div></div>
      <div><div style="font-size:10px;color:var(--dim)">UPLOAD</div><div class="traffic-val" id="tx">0 MB</div><div class="traffic-speed" id="s_tx">0 KB/s</div></div>
    </div>
  </div>

  <!-- Exit Country -->
  <div class="card">
    <h3>Exit Country</h3>
    <div class="circuit-status {{ 'active' if privacy and exit_country != 'auto' else 'inactive' }}" id="circuit-status">
      {{ '\U0001f512 Exit: ' + exit_country.upper() if exit_country != 'auto' else '\U0001f30d Automatic exit selection' }}
    </div>
    <select id="exit-select" onchange="setExit(this.value)">
      {% for code, name in countries %}
      <option value="{{ code }}" {{ 'selected' if code == exit_country else '' }}>{{ name }}</option>
      {% endfor %}
    </select>
    <div class="helper-text">Requires Privacy Mode. Changing country rebuilds circuits.</div>
  </div>

  <!-- Wi-Fi -->
  <div class="card">
    <h3>Wi-Fi</h3>
    <button class="btn-secondary" id="scan-btn" onclick="scan()">Scan Networks</button>
    <div id="list" style="margin-top:10px"></div>
    <div id="connect" style="display:none;margin-top:15px">
      <div style="font-weight:600" id="ssid-name"></div>
      <input type="password" id="pw" placeholder="Password">
      <button class="btn-primary" id="conn-btn" onclick="connectWifi()">Connect Now</button>
    </div>
  </div>
</div>

<script>
let targetSSID = '';

function flag(cc) {
  if (!cc || cc.length !== 2) return '\u2014';
  return String.fromCodePoint(...[...cc.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
}

// ──────── SSE — Server-Sent Events (replaces polling for status + circuit) ────────

let evtSource = null;
let sseRetryTimeout = null;

function connectSSE() {
  if (evtSource) { evtSource.close(); }

  evtSource = new EventSource('/api/events');
  const dot = document.getElementById('sse-dot');

  evtSource.onopen = () => {
    dot.className = 'sse-dot live';
    if (sseRetryTimeout) { clearTimeout(sseRetryTimeout); sseRetryTimeout = null; }
  };

  evtSource.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.type === 'status') updateStatus(d);
      else if (d.type === 'circuit') updateCircuit(d);
      else if (d.type === 'rotation') applyRotationState(d);
    } catch(err) {}
  };

  evtSource.onerror = () => {
    dot.className = 'sse-dot dead';
    evtSource.close();
    sseRetryTimeout = setTimeout(connectSSE, 3000);
  };
}

// Status update (from SSE event)

function updateStatus(d) {
  const badge = document.getElementById('conn-badge');
  const label = document.getElementById('conn-label');
  const bar   = document.getElementById('conn-progress');
  const summ  = document.getElementById('conn-summary');

  badge.className = 'conn-badge ' + d.state;

  const labels = {stopped:'STOPPED', bootstrapping:'BOOTSTRAPPING', connected:'CONNECTED', error:'ERROR'};
  let lt = labels[d.state] || d.state.toUpperCase();
  if (d.state === 'bootstrapping') lt += ' ' + d.progress + '%';
  label.innerText = lt;
  bar.style.width = d.progress + '%';
  summ.innerText = d.summary || '';
}

// Circuit update (from SSE event)

function updateCircuit(d) {
  const c = document.getElementById('circuit-container');

  if (!d.hops || d.hops.length === 0) {
    if (d.status === 'NEWNYM') {
      c.innerHTML = '<div class="circuit-empty">\u23f3 Building new circuit\u2026</div>';
    } else {
      c.innerHTML = '<div class="circuit-empty">No circuit established yet</div>';
    }
    return;
  }

  let html = '<div class="circuit-chain">';
  d.hops.forEach((hop, i) => {
    if (i > 0) html += '<div class="circuit-arrow">\u279C</div>';
    const cc = hop.country_code || '';
    html += '<div class="circuit-node' + (hop.role === 'exit' ? ' active-node' : '') + '">'
      + '<div class="node-role">' + hop.role + '</div>'
      + '<div class="node-flag">' + flag(cc) + '</div>'
      + '<div class="node-name">' + (hop.nickname || '\u2014') + '</div>'
      + '<div class="node-ip">' + (hop.ip || '\u2014') + '</div>'
      + '<div class="node-country">' + (hop.country_name || cc || '\u2014') + '</div>'
      + '</div>';
  });
  html += '</div>';
  c.innerHTML = html;
}

// New circuit request

async function newCircuit() {
  const btn = document.getElementById('newnym-btn');
  btn.disabled = true; btn.innerText = '\u23F3 Requesting\u2026';
  try {
    await fetch('/api/anon/newcircuit', {method:'POST'});
  } finally {
    setTimeout(() => { btn.disabled = false; btn.innerHTML = '&#x1f504; New Circuit'; }, 2000);
  }
}

// Exit country

async function setExit(cc) {
  try {
    const r = await fetch('/api/circuit', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({exit_country: cc})
    });
    const d = await r.json();
    if (d.status !== 'ok') alert(d.status);
    else location.reload();
  } catch(e) { alert('Error'); }
}

// Traffic polling (1s)

async function pollTraffic() {
  try {
    const r = await fetch('/api/traffic'); const d = await r.json();
    document.getElementById('rx').innerText = (d.rx/1048576).toFixed(1)+' MB';
    document.getElementById('tx').innerText = (d.tx/1048576).toFixed(1)+' MB';
    document.getElementById('s_rx').innerText = d.speed_rx>1048576?(d.speed_rx/1048576).toFixed(1)+' MB/s':(d.speed_rx/1024).toFixed(1)+' KB/s';
    document.getElementById('s_tx').innerText = d.speed_tx>1048576?(d.speed_tx/1048576).toFixed(1)+' MB/s':(d.speed_tx/1024).toFixed(1)+' KB/s';
  } catch(e) {}
}

// Wi-Fi

async function scan() {
  const btn = document.getElementById("scan-btn");
  btn.disabled = true;
  btn.innerText = "Scanning…";
  const container = document.getElementById("list");
  container.innerHTML = "";
  try {
    const r = await fetch("/wifi/scan");
    const d = await r.json();
    d.networks.forEach(n => {
      const div = document.createElement("div");
      div.className = "wifi-item";
      const span = document.createElement("span");
      span.textContent = n.ssid;
      const tag = document.createElement("span");
      tag.innerHTML = n.connected
        ? `<span class="connected-label">CONNECTED</span>`
        : `›`;
      div.appendChild(span);
      div.appendChild(tag);
      div.addEventListener("click", () => { sel(n.ssid); });
      container.appendChild(div);
    });
  } catch (e) {
    console.error(e);
  } finally {
    btn.disabled = false;
    btn.innerText = "Scan Networks";
  }
}
function sel(s){ targetSSID=s; document.getElementById('ssid-name').innerText=s; document.getElementById('connect').style.display='block'; }
async function connectWifi(){
  const btn=document.getElementById('conn-btn'), pw=document.getElementById('pw').value;
  btn.disabled=true; btn.innerText='Connecting\u2026';
  try {
    const r=await fetch('/wifi/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid:targetSSID,password:pw})});
    const d=await r.json(); alert(d.status);
    if(d.status==='Connected!') location.reload();
  } catch(e){alert('Error');}
  finally { btn.disabled=false; btn.innerText='Connect Now'; }
}


// Circuit polling fallback (ensures auto-refresh even if no CIRC events arrive)
async function pollCircuit() {
  try {
    const r = await fetch('/api/anon/circuit');
    const d = await r.json();
    // Reuse the same renderer used by SSE events
    updateCircuit({ type: 'circuit', status: 'POLL', hops: (d.hops || []) });
  } catch(e) {}
}



// ──────────────────────────────────────────────
// Anyone proof banner (connected/disconnected) + leak indicator
// ──────────────────────────────────────────────
let lastProof = null;

function updateProofBanner(st) {
  const b = document.getElementById('proof-banner');
  const sub = document.getElementById('proof-sub');
  if (!b || !sub) return;

  const privacy = !!(st && st.privacy);

  // Backward compatible:
  // - preferred: st.display_connected (server says UI-safe connected)
  // - fallback: st.connected && st.privacy
  const connected = !!(st && (st.display_connected ?? (st.connected && privacy)));

  const ip = (st && st.ip) ? st.ip : '';

  b.className = 'proof-banner ' + (connected ? 'connected' : 'disconnected');
  b.firstChild.nodeValue = connected ? 'Connected to Anyone' : 'Not connected to Anyone';

  let msg = '';
  if (!privacy) msg += 'Privacy mode is OFF. ';
  if (privacy && ip) msg += `Exit IP: ${ip}. `;
  
  sub.textContent = msg.trim();
}


async function pollProof() {
  try {
    const r = await fetch('/api/anyone/proof', { cache: 'no-store' });
    const st = await r.json();
    lastProof = st;
    updateProofBanner(st);
  } catch (e) {
    updateProofBanner({connected:false, ip:'', reason:'portal_error'});
  }
}

// ──── Start: SSE for status+circuit, polling only for traffic ────
connectSSE();
pollProof();
setInterval(pollProof, 5000);

pollCircuit();
setInterval(pollCircuit, 3000);
pollTraffic();
setInterval(pollTraffic, 1000);


// ──────────────────────────────────────────────
// Circuit rotation UI
// ──────────────────────────────────────────────
let rotationState = {
  enabled: false,
  interval_seconds: 600,
  variance_percent: 0,
  next_rotate_at: 0,
  remaining_seconds: 0,
  phase: "disabled",
  pending: false
};

function fmtSeconds(sec) {
  sec = Math.max(0, parseInt(sec || 0, 10));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function applyRotationState(st) {
  if (!st) return;
  rotationState = { ...rotationState, ...st };
  const enabledEl = document.getElementById("rotation-enabled");
  const minutesEl = document.getElementById("rotation-minutes");
  const phaseEl = document.getElementById("rotation-phase");
  const remainEl = document.getElementById("rotation-remaining");
  const varEl = document.getElementById("rotation-variance");

  if (enabledEl) enabledEl.checked = !!rotationState.enabled;
  if (minutesEl) minutesEl.value = Math.max(1, Math.round((rotationState.interval_seconds || 600) / 60));
  if (varEl) varEl.value = Math.max(0, Math.min(50, parseInt(rotationState.variance_percent || 0, 10) || 0));

  if (phaseEl) phaseEl.textContent = rotationState.phase || "—";

  // remaining based on next_rotate_at
  if (remainEl) {
    if (!rotationState.enabled || !rotationState.next_rotate_at) {
      remainEl.textContent = "—";
    } else {
      const now = Math.floor(Date.now() / 1000);
      const rem = Math.max(0, (rotationState.next_rotate_at || 0) - now);
      remainEl.textContent = fmtSeconds(rem);
    }
  }
}

async function fetchRotation() {
  try {
    const r = await fetch("/api/rotation");
    const st = await r.json();
    applyRotationState(st);
  } catch (e) {}
}

async function saveRotation() {
  const enabled = document.getElementById("rotation-enabled")?.checked || false;
  let minutes = parseInt(document.getElementById("rotation-minutes")?.value || "10", 10);
  if (!Number.isFinite(minutes) || minutes < 1) minutes = 10;
  if (minutes > 1440) minutes = 1440;
  const interval_seconds = minutes * 60;

  let variance_percent = parseInt(document.getElementById("rotation-variance")?.value || "0", 10);
  if (!Number.isFinite(variance_percent) || variance_percent < 0) variance_percent = 0;
  if (variance_percent > 50) variance_percent = 50;

  try {
    const r = await fetch("/api/rotation", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ enabled, interval_seconds, variance_percent })
    });
    const st = await r.json();
    applyRotationState(st);
  } catch (e) {}
}

async function triggerRotationNow() {
  try {
    const r = await fetch("/api/rotation/trigger", { method: "POST" });
    const st = await r.json();
    applyRotationState(st);
  } catch (e) {}
}

function bindRotationUI() {
  document.getElementById("rotation-save")?.addEventListener("click", saveRotation);
  document.getElementById("rotation-trigger")?.addEventListener("click", triggerRotationNow);
}

setInterval(() => {
  // client-side countdown tick
  if (!rotationState.enabled || !rotationState.next_rotate_at) return;
  const now = Math.floor(Date.now() / 1000);
  const rem = Math.max(0, rotationState.next_rotate_at - now);
  const remainEl = document.getElementById("rotation-remaining");
  if (remainEl) remainEl.textContent = fmtSeconds(rem);
}, 1000);

// Hook SSE: your existing SSE handler should call onMessage.
// We patch in a tiny adapter if not present:
try {
  // When your SSE handler receives message data (already JSON parsed),
  // it typically checks msg.type. We add handling for type === "rotation".
} catch (e) {}

document.addEventListener("DOMContentLoaded", () => {
  bindRotationUI();
  fetchRotation();
});

</script>

  <!-- ────────────────────────────────────────────── -->
  <!-- Circuit rotation -->
  <!-- ────────────────────────────────────────────── -->
  <div class="card" style="margin-top:14px;">
    <h3 style="margin:0 0 10px 0;">Circuit Rotation</h3>

    <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
      <label style="display:flex; align-items:center; gap:8px;">
        <input id="rotation-enabled" type="checkbox" />
        <span>Auto-rotate circuit</span>
      </label>

      <label style="display:flex; align-items:center; gap:8px;">
        <span>Interval (minutes)</span>
        <input id="rotation-minutes" type="number" min="1" max="1440" step="1" value="10"
               style="width:100px; padding:6px 8px; border-radius:10px; border:1px solid rgba(255,255,255,0.15);" />
      </label>

      <label style="display:flex; align-items:center; gap:8px;">
        <span>Variance (%)</span>
        <input id="rotation-variance" type="number" min="0" max="50" step="1" value="0"
               style="width:90px; padding:6px 8px; border-radius:10px; border:1px solid rgba(255,255,255,0.15);" />
      </label>

      <button id="rotation-save" class="btn" type="button">Save</button>
      <button id="rotation-trigger" class="btn" type="button" title="Trigger now">Rotate now</button>
    </div>

    <div style="margin-top:10px; display:flex; gap:12px; flex-wrap:wrap; align-items:center;">
      <div>State: <b id="rotation-phase">—</b></div>
      <div>Next rotation in: <b id="rotation-remaining">—</b></div>
      <div style="opacity:0.8;">(Existing connections stay alive; new ones use the new circuit)</div>
    </div>
  </div>

<div id="proof-banner" class="proof-banner disconnected">
  Not connected to Anyone
  <span class="proof-sub" id="proof-sub">Checking…</span>
</div>



<script>
// -----------------------------
// Kill Switch UI (robust init)
// -----------------------------
async function refreshKillSwitch__disabled() {
  const btn = document.getElementById('killswitch-btn');
  const sub = document.getElementById('killswitch-sub');
  if (!btn) return;

  try {
    const r = await fetch('/api/killswitch/status', { cache: 'no-store' });
    const st = await r.json();
    const on = !!st.enabled;

    btn.textContent = on ? 'Kill Switch: ON' : 'Kill Switch: OFF';
    btn.style.background = on ? 'rgba(220, 38, 38, .95)' : 'rgba(34, 197, 94, .20)';
    btn.style.borderColor = on ? 'rgba(220, 38, 38, .8)' : 'rgba(34, 197, 94, .35)';
    btn.style.color = '#fff';

    if (sub) {
      sub.textContent = on
        ? 'Egress is blocked. Only local management traffic is allowed.'
        : 'Blocks all non-local traffic from the Stick (Normal + Privacy mode).';
    }
  } catch (e) {
    btn.textContent = 'Kill Switch: error';
    btn.style.background = 'rgba(255, 255, 255, .08)';
  }
}

async function setKillSwitch(enabled) {
  const r = await fetch('/api/killswitch/set', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled })
  });
  return await r.json();
}

function initKillSwitchUI() {
  const btn = document.getElementById('killswitch-btn');
  if (!btn) return;

  // avoid double-binding if init runs multiple times
  if (btn.dataset.ksBound === "1") {
    refreshKillSwitch();
    return;
  }
  btn.dataset.ksBound = "1";

  btn.addEventListener('click', async () => {
    let cur = false;
    try {
      const r = await fetch('/api/killswitch/status', { cache: 'no-store' });
      const st = await r.json();
      cur = !!st.enabled;
    } catch (e) {}

    const target = !cur;

    if (target) {
      const ok = confirm('Enable Kill Switch?\n\nThis blocks all non-local traffic from the Stick (Normal + Privacy mode).');
      if (!ok) return;
    }

    try { await setKillSwitch(target); } catch (e) {}
    await refreshKillSwitch();
  });

  refreshKillSwitch();
  setInterval(refreshKillSwitch, 2500);
}

// Robust init: if DOMContentLoaded already fired, run immediately
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initKillSwitchUI);
} else {
  initKillSwitchUI();
}
</script>

<script id="ks-js2-proof">
(function(){
  function run(){
    const btn = document.getElementById('killswitch-btn');
    const sub = document.getElementById('killswitch-sub');
    if(!btn) return false;

    async function refresh(){
      try{
        const url = (location && location.origin ? location.origin : '') + '/api/killswitch/status';
        const r = await fetch(url, { cache: 'no-store' });
        const txt = await r.text();
        if(!r.ok) throw new Error('HTTP ' + r.status);
        const st = JSON.parse(txt);
        const on = !!st.enabled;

        btn.textContent = on ? 'Kill Switch: ON' : 'Kill Switch: OFF';
        btn.style.background = on ? 'rgba(220, 38, 38, .95)' : 'rgba(34, 197, 94, .20)';
        btn.style.borderColor = on ? 'rgba(220, 38, 38, .8)' : 'rgba(34, 197, 94, .35)';
        btn.style.color = '#fff';

        if(sub){
          sub.textContent = on
            ? 'Egress is blocked. Only local management traffic is allowed.'
            : 'Blocks all non-local traffic from the Stick (Normal + Privacy mode).';
        }
      } catch(e){
        btn.textContent = 'Kill Switch: error';
        if(sub) sub.textContent = 'JS error: ' + (e && e.message ? e.message : String(e));
      }
    }

    // bind click once
    if(btn.dataset.ksBound !== "1"){
      btn.dataset.ksBound = "1";
      btn.addEventListener('click', async () => {
        // read current
        let cur = false;
        try{
          const r = await fetch((location.origin || '') + '/api/killswitch/status', { cache:'no-store' });
          const st = await r.json();
          cur = !!st.enabled;
        } catch(e){}

        const target = !cur;
        if(target){
          const ok = confirm('Enable Kill Switch?

This blocks all non-local traffic from the Stick (Normal + Privacy mode).');
          if(!ok) return;
        }

        try{
          await fetch((location.origin || '') + '/api/killswitch/set', {
            method:'POST',
            headers:{ 'Content-Type':'application/json' },
            body: JSON.stringify({ enabled: target })
          });
        } catch(e){}

        await refresh();
      });
    }

    refresh();
    setInterval(refresh, 2500);
    return true;
  }

  // Wait until button exists (DOM timing safe)
  let tries = 0;
  const t = setInterval(() => {
    tries++;
    if (run() || tries > 100) clearInterval(t); // ~10s max
  }, 100);
})();
</script>

</body></html>
"""


# ============================================================================
# Flask Routes
# ============================================================================

@app.route("/")
def index():
    p = subprocess.run("sudo iptables -t nat -L PREROUTING -n | grep 9040",
                       shell=True, capture_output=True).returncode == 0
    ec = get_current_exit_country()
    return render_template_string(HTML, privacy=p, exit_country=ec, countries=EXIT_COUNTRIES)


# ──── SSE endpoint (replaces status + circuit polling) ────

@app.route("/api/events")
def sse_events():
    """Server-Sent Events stream — pushes status + circuit updates in real-time."""
    def generate():
        q = anon_ctrl.add_sse_client()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    # Send keepalive comment to prevent proxy/browser timeout
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            anon_ctrl.remove_sse_client(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ──── REST API ────

@app.route("/api/status")
def api_status():
    return jsonify(anon_ctrl.get_status())


@app.route("/api/traffic")
def api_traffic():
    return jsonify(update_stats())


@app.route("/api/anon/circuit")
def api_anon_circuit():
    hops = anon_ctrl.get_circuit_detail()
    return jsonify({"hops": hops or []})


@app.route("/api/anon/newcircuit", methods=["POST"])
def api_new_circuit():
    resp = anon_ctrl.new_circuit()
    ok = resp is not None and "250" in resp
    return jsonify({"status": "ok" if ok else "failed"})


# ──── Circuit rotation (timer) ────

@app.route("/api/rotation", methods=["GET"])
def api_rotation_get():
    if not rotation_mgr:
        return jsonify({
            "type": "rotation",
            "enabled": False,
            "interval_seconds": DEFAULT_ROTATE_SECONDS,
            "variance_percent": DEFAULT_VARIANCE_PERCENT,
            "next_rotate_at": 0,
            "last_rotated_at": 0,
            "remaining_seconds": 0,
            "phase": "disabled",
            "pending": False,
        }), 200
    return jsonify(rotation_mgr.get_state()), 200


@app.route("/api/rotation", methods=["POST"])
def api_rotation_set():
    if not rotation_mgr:
        return jsonify({"ok": False, "error": "rotation manager not ready"}), 500
    d = request.get_json(silent=True) or {}
    enabled = bool(d.get("enabled", False))
    interval = d.get("interval_seconds", d.get("interval", DEFAULT_ROTATE_SECONDS))
    variance = d.get("variance_percent", DEFAULT_VARIANCE_PERCENT)
    try:
        interval = int(interval)
    except Exception:
        interval = DEFAULT_ROTATE_SECONDS
    try:
        variance = int(variance)
    except Exception:
        variance = DEFAULT_VARIANCE_PERCENT

    interval = max(MIN_ROTATE_SECONDS, min(interval, MAX_ROTATE_SECONDS))
    variance = max(0, min(variance, 50))

    rotation_mgr.update(enabled, interval, variance)
    return jsonify({"ok": True, **rotation_mgr.get_state()}), 200


@app.route("/api/rotation/trigger", methods=["POST"])
def api_rotation_trigger():
    if not rotation_mgr:
        return jsonify({"ok": False, "error": "rotation manager not ready"}), 500
    ok = rotation_mgr.trigger("manual")
    return jsonify({"ok": bool(ok), **rotation_mgr.get_state()}), 200




# ──── Anyone Proof + Leakcheck ────

@app.route("/api/anyone/proof", methods=["GET"])
def api_anyone_proof():
    st = _anyone_proof_check()
    privacy = _privacy_mode_active()
    st2 = dict(st)
    st2.update({"privacy": bool(privacy)})
    # UI must only claim 'Connected to Anyone' when Privacy Mode is actually active
    st2["display_connected"] = bool(st2.get("connected")) and bool(privacy)
    return jsonify(st2), 200

# ──── Debug helpers ────
@app.route("/api/debug/routes", methods=["GET"])
def api_debug_routes():
    try:
        import app as app_module
        mod_file = getattr(app_module, "__file__", "unknown")
    except Exception as e:
        mod_file = f"import_error:{e}"

    rules = []
    try:
        for r in sorted(app.url_map.iter_rules(), key=lambda x: x.rule):
            rules.append({"rule": r.rule, "methods": sorted([m for m in r.methods if m not in ("HEAD","OPTIONS")])})
    except Exception as e:
        rules.append({"error": str(e)})

    return jsonify({
        "module_file": mod_file,
        "gunicorn_target": "app:app",
        "rules_count": len(rules),
        "rules": rules,
    }), 200


@app.route("/api/circuit", methods=["GET"])
def get_circuit():
    return jsonify({"exit_country": get_current_exit_country()})


@app.route("/api/circuit", methods=["POST"])
def post_circuit():
    data = request.get_json()
    if not data or "exit_country" not in data:
        return jsonify({"status": "Missing exit_country"}), 400
    cc = data["exit_country"].lower().strip()
    valid = [c[0] for c in EXIT_COUNTRIES]
    if cc not in valid:
        return jsonify({"status": f"Invalid country code: {cc}"}), 400
    p = subprocess.run("sudo iptables -t nat -L PREROUTING -n | grep 9040",
                       shell=True, capture_output=True).returncode == 0
    if not p:
        return jsonify({"status": "Privacy Mode must be active"}), 400
    set_exit_country(cc)
    return jsonify({"status": "ok", "exit_country": cc})


# ──── Wi-Fi ────

@app.route("/wifi/scan")
def w_scan():
    raw = subprocess.check_output("nmcli -t -f SSID,ACTIVE dev wifi list",
                                  shell=True).decode("utf-8")
    nets, seen = [], set()
    for line in raw.split("\r\n"):
        if line.strip():
            parts = line.split(":", 1)
            ssid = parts[0]
            if ssid and ssid not in seen:
                nets.append({"ssid": ssid, "connected": parts[1].strip() == "yes" if len(parts) > 1 else False})
                seen.add(ssid)
    return jsonify({"networks": nets})


@app.route("/wifi/connect", methods=["POST"])
def w_conn():
    data = request.get_json()
    ssid, pw = data.get("ssid", ""), data.get("password", "")
    try:
        subprocess.run(f'nmcli dev wifi connect "{ssid}" password "{pw}"',
                       shell=True, check=True, capture_output=True, timeout=30)
        return jsonify({"status": "Connected!"})
    except subprocess.CalledProcessError:
        return jsonify({"status": "Connection failed"})
    except subprocess.TimeoutExpired:
        return jsonify({"status": "Timeout"})


@app.route("/mode/privacy", methods=["POST"])
def mode_privacy():
    subprocess.run("sudo /usr/local/bin/mode_privacy.sh", shell=True)
    return redirect("/")


@app.route("/mode/normal", methods=["POST"])
def mode_normal():
    subprocess.run("sudo /usr/local/bin/mode_normal.sh", shell=True)
    return redirect("/")


# ============================================================================
# Startup
# ============================================================================

# Start persistent ControlPort connection


# ============================================================================
# Startup
# ============================================================================

anon_ctrl = AnonController()
anon_ctrl.start()

rotation_mgr = RotationManager(anon_ctrl)
rotation_mgr.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, threaded=True)



# -----------------------------
# Kill Switch (block all egress)
# -----------------------------
KILLSWITCH_SCRIPT = "/usr/local/bin/anyone_killswitch.sh"

def _killswitch_get():
    try:
        import subprocess
        # run as root via sudo (web app user is typically www-data)
        out = subprocess.check_output(["sudo", KILLSWITCH_SCRIPT, "status"], stderr=subprocess.STDOUT, text=True).strip()
        return (out.upper() == "ON")
    except Exception:
        return False

def _killswitch_set(enabled: bool):
    import subprocess
    cmd = "on" if enabled else "off"
    out = subprocess.check_output(["sudo", KILLSWITCH_SCRIPT, cmd], stderr=subprocess.STDOUT, text=True).strip()
    return (out.upper() == "ON")

@app.route("/api/killswitch/status", methods=["GET"])
def api_killswitch_status():
    return jsonify({"enabled": _killswitch_get()}), 200

@app.route("/api/killswitch/set", methods=["POST"])
def api_killswitch_set():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    state = _killswitch_set(enabled)
    return jsonify({"enabled": state}), 200
