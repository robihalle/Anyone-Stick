from flask import Flask, request, jsonify, render_template_string, redirect, Response
import subprocess, time, os, signal, re, socket, threading, queue, json, logging

app = Flask(__name__, static_folder="static")
log = logging.getLogger("anyone-stick")

# ════════════════════════════════════════════════════════════════════
# Traffic stats
# ════════════════════════════════════════════════════════════════════
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
ANONRC_PATH = "/etc/anonrc"

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


# ════════════════════════════════════════════════════════════════════
# AnonController v2 — Persistent connection + Event system
# ════════════════════════════════════════════════════════════════════

class AnonController:
    """Persistent connection to the anon daemon via ControlPort 9051.

    Key improvements over v1:
      - Single persistent TCP connection (no connect/auth per command)
      - Background reader thread splits replies (250) from async events (650)
      - SETEVENTS CIRC STREAM STATUS_CLIENT for push-based monitoring
      - Thread-safe command() with reply queue
      - In-memory circuit & bootstrap state (no polling needed)
      - SSE broadcast to all connected browser clients
    """

    COUNTRY_NAMES = {
        "AD": "Andorra", "AE": "UAE", "AT": "Austria", "AU": "Australia",
        "BE": "Belgium", "BG": "Bulgaria", "BR": "Brazil", "CA": "Canada",
        "CH": "Switzerland", "CL": "Chile", "CN": "China", "CO": "Colombia",
        "CZ": "Czech Republic", "DE": "Germany", "DK": "Denmark",
        "EE": "Estonia", "ES": "Spain", "FI": "Finland", "FR": "France",
        "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong",
        "HR": "Croatia", "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland",
        "IL": "Israel", "IN": "India", "IS": "Iceland", "IT": "Italy",
        "JP": "Japan", "KR": "South Korea", "LT": "Lithuania",
        "LU": "Luxembourg", "LV": "Latvia", "MD": "Moldova", "MX": "Mexico",
        "MY": "Malaysia", "NL": "Netherlands", "NO": "Norway",
        "NZ": "New Zealand", "PA": "Panama", "PL": "Poland",
        "PT": "Portugal", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
        "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia",
        "SK": "Slovakia", "TH": "Thailand", "TR": "Turkey", "TW": "Taiwan",
        "UA": "Ukraine", "US": "United States", "ZA": "South Africa",
    }

    def __init__(self, host="127.0.0.1", port=9051):
        self.host = host
        self.port = port
        self.cookie_path = "/root/.anon/control_auth_cookie"

        # Connection state
        self._sock = None
        self._lock = threading.Lock()          # serialize command()
        self._reply_queue = queue.Queue()       # 250/5xx replies land here
        self._reader_thread = None
        self._connected = False
        self._shutting_down = False

        # ── Cached state (updated by events) ─────────────────────
        self._bootstrap = {"progress": 0, "summary": "Starting…", "state": "stopped"}
        self._circuits = {}                    # circuit_id → {status, path_raw, hops:[...]}
        self._bootstrap_lock = threading.Lock()
        self._circuit_lock = threading.Lock()

        # ── SSE subscribers ───────────────────────────────────────
        self._sse_queues = []                  # list of queue.Queue per browser tab
        self._sse_lock = threading.Lock()

        # Auto-connect in background
        self._connect_thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._connect_thread.start()

    # ──────────────────────────────────────────────────────────────
    # SSE subscriber management
    # ──────────────────────────────────────────────────────────────
    def sse_subscribe(self):
        """Register a new SSE client, returns its queue."""
        q = queue.Queue(maxsize=50)
        with self._sse_lock:
            self._sse_queues.append(q)
        return q

    def sse_unsubscribe(self, q):
        """Remove an SSE client."""
        with self._sse_lock:
            try:
                self._sse_queues.remove(q)
            except ValueError:
                pass

    def _sse_broadcast(self, event_type, data):
        """Push an event to all connected browser tabs."""
        msg = json.dumps({"type": event_type, **data})
        with self._sse_lock:
            dead = []
            for q in self._sse_queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._sse_queues.remove(q)

    # ──────────────────────────────────────────────────────────────
    # Connection lifecycle
    # ──────────────────────────────────────────────────────────────
    def _connect_loop(self):
        """Keep trying to connect (handles anon restarts)."""
        while not self._shutting_down:
            if not self._connected:
                try:
                    self._connect()
                except Exception as e:
                    log.debug("Control connect failed: %s", e)
                    time.sleep(3)
                    continue
            time.sleep(1)

    def _connect(self):
        """Open TCP, authenticate, subscribe to events."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((self.host, self.port))
        sock.settimeout(None)   # blocking reads in reader thread
        self._sock = sock

        # Authenticate
        if not self._authenticate():
            sock.close()
            raise ConnectionError("Authentication failed")

        self._connected = True
        log.info("Control port connected & authenticated")

        # Subscribe to events
        self._send("SETEVENTS CIRC STATUS_CLIENT")
        self._read_reply()  # consume 250 OK

        # Start reader thread
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        # Seed initial state
        self._seed_state()

    def _authenticate(self):
        """Cookie auth → empty password → bare auth."""
        methods = []
        try:
            with open(self.cookie_path, "rb") as f:
                cookie = f.read().hex()
            methods.append(f"AUTHENTICATE {cookie}")
        except FileNotFoundError:
            pass
        methods += ['AUTHENTICATE ""', "AUTHENTICATE"]

        for m in methods:
            self._send(m)
            resp = self._read_reply_raw()
            if resp and resp.startswith("250"):
                return True
        return False

    def _seed_state(self):
        """Pull current bootstrap + circuits right after connect."""
        # Bootstrap
        try:
            resp = self.command("GETINFO status/bootstrap-phase")
            if resp:
                self._parse_bootstrap_event(resp)
        except Exception:
            pass

        # Existing circuits
        try:
            resp = self.command("GETINFO circuit-status")
            if resp:
                self._parse_circuit_status_bulk(resp)
        except Exception:
            pass

    def _close(self):
        """Close the connection (will auto-reconnect)."""
        self._connected = False
        try:
            self._sock.close()
        except Exception:
            pass
        self._sock = None

    # ──────────────────────────────────────────────────────────────
    # Low-level I/O
    # ──────────────────────────────────────────────────────────────
    def _send(self, line):
        """Send a single command line."""
        self._sock.sendall(f"{line}\r\n".encode())

    def _read_reply_raw(self):
        """Read until we see a final status line (NNN SP ...).
        Used only during auth before the reader thread runs."""
        buf = b""
        while True:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    return None
                buf += chunk
                text = buf.decode("utf-8", errors="replace")
                for line in text.rstrip("\r\n").split("\r\n"):
                    if len(line) >= 4 and line[3] == " " and line[:3].isdigit():
                        return text
            except socket.timeout:
                break
        return buf.decode("utf-8", errors="replace")

    def _read_reply(self):
        """Read a reply from the reply queue (used after reader thread is live)."""
        try:
            return self._reply_queue.get(timeout=10)
        except queue.Empty:
            return None

    # ──────────────────────────────────────────────────────────────
    # Reader thread: routes replies vs. async events
    # ──────────────────────────────────────────────────────────────
    def _reader_loop(self):
        """Continuously reads from socket, splits 250/650 lines."""
        buf = ""
        while self._connected and not self._shutting_down:
            try:
                chunk = self._sock.recv(8192)
                if not chunk:
                    log.warning("Control port connection closed")
                    self._close()
                    return
                buf += chunk.decode("utf-8", errors="replace")

                # Process complete lines
                while "\r\n" in buf:
                    line, buf = buf.split("\r\n", 1)
                    self._route_line(line)

            except OSError:
                if not self._shutting_down:
                    log.warning("Control port read error, reconnecting")
                    self._close()
                return

    def _route_line(self, line):
        """Route a single line to event handler or reply queue."""
        if not line:
            return

        # Async event (650 = async notification)
        if line.startswith("650"):
            self._handle_event(line)
            return

        # Mid-reply line (NNN-...) or final line (NNN ...) → reply queue
        if len(line) >= 3 and line[:3].isdigit():
            self._reply_queue.put(line)
            return

        # Data continuation line (e.g. circuit-status bulk)
        # Append to last reply
        self._reply_queue.put(line)

    # ──────────────────────────────────────────────────────────────
    # Event handling (CIRC, STATUS_CLIENT)
    # ──────────────────────────────────────────────────────────────
    def _handle_event(self, line):
        """Dispatch a 650 event line."""
        # 650 CIRC <id> <status> [path] [flags...]
        if " CIRC " in line:
            self._handle_circ_event(line)
        # 650 STATUS_CLIENT NOTICE BOOTSTRAP ...
        elif "STATUS_CLIENT" in line and "BOOTSTRAP" in line:
            self._parse_bootstrap_event(line)

    def _handle_circ_event(self, line):
        """Parse: 650 CIRC <id> <status> [$fp~name,...] [BUILD_FLAGS=...] [PURPOSE=...]"""
        parts = line.split()
        # find CIRC keyword position
        try:
            idx = parts.index("CIRC")
        except ValueError:
            return

        if len(parts) < idx + 3:
            return

        circ_id_str = parts[idx + 1]
        status = parts[idx + 2]

        try:
            circ_id = int(circ_id_str)
        except ValueError:
            return

        # Path is the next part if it contains $ (fingerprints)
        path_raw = ""
        if len(parts) > idx + 3 and "$" in parts[idx + 3]:
            path_raw = parts[idx + 3]

        # Extract PURPOSE
        purpose = ""
        for p in parts:
            if p.startswith("PURPOSE="):
                purpose = p.split("=", 1)[1]

        with self._circuit_lock:
            if status in ("CLOSED", "FAILED"):
                self._circuits.pop(circ_id, None)
            else:
                self._circuits[circ_id] = {
                    "id": circ_id,
                    "status": status,
                    "path_raw": path_raw,
                    "purpose": purpose,
                    "updated": time.time(),
                }

        # Broadcast to SSE clients
        self._sse_broadcast("circuit", {
            "circuit_id": circ_id,
            "status": status,
            "purpose": purpose,
        })

    def _parse_bootstrap_event(self, text):
        """Extract PROGRESS, SUMMARY, TAG from bootstrap status."""
        progress = 0
        summary = ""
        m = re.search(r"PROGRESS=(\d+)", text)
        if m:
            progress = int(m.group(1))
        m = re.search(r'SUMMARY="([^"]*)"', text)
        if m:
            summary = m.group(1)

        if progress >= 100:
            state = "connected"
        elif progress > 0:
            state = "bootstrapping"
        else:
            state = "stopped"

        with self._bootstrap_lock:
            old_progress = self._bootstrap.get("progress", 0)
            self._bootstrap = {
                "progress": progress,
                "summary": summary or ("Connected" if progress >= 100 else f"Bootstrapping {progress}%"),
                "state": state,
            }

        # Only broadcast if progress actually changed
        if progress != old_progress:
            self._sse_broadcast("bootstrap", self._bootstrap)

    def _parse_circuit_status_bulk(self, raw):
        """Parse bulk GETINFO circuit-status response."""
        with self._circuit_lock:
            for line in raw.split("\r\n"):
                line = line.strip()
                if not line or line.startswith("250") or line == ".":
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    circ_id = int(parts[0])
                except ValueError:
                    continue
                status = parts[1]
                path_raw = parts[2] if len(parts) > 2 and "$" in parts[2] else ""
                purpose = ""
                for p in parts:
                    if p.startswith("PURPOSE="):
                        purpose = p.split("=", 1)[1]

                if status in ("CLOSED", "FAILED"):
                    self._circuits.pop(circ_id, None)
                else:
                    self._circuits[circ_id] = {
                        "id": circ_id,
                        "status": status,
                        "path_raw": path_raw,
                        "purpose": purpose,
                        "updated": time.time(),
                    }

    # ──────────────────────────────────────────────────────────────
    # Thread-safe command interface
    # ──────────────────────────────────────────────────────────────
    def command(self, cmd):
        """Send a command and wait for the reply. Thread-safe."""
        if not self._connected:
            return None
        with self._lock:
            try:
                # Drain stale replies
                while not self._reply_queue.empty():
                    try:
                        self._reply_queue.get_nowait()
                    except queue.Empty:
                        break
                self._send(cmd)
                return self._read_reply()
            except Exception as e:
                log.warning("command(%s) failed: %s", cmd, e)
                self._close()
                return None

    # ──────────────────────────────────────────────────────────────
    # High-level queries (used by Flask routes)
    # ──────────────────────────────────────────────────────────────
    def is_running(self):
        try:
            subprocess.check_output(["pgrep", "-x", "anon"])
            return True
        except Exception:
            return False

    def get_status(self):
        """Return bootstrap state (from cache — no control port call)."""
        if not self.is_running():
            return {"state": "stopped", "progress": 0, "summary": "Anon is not running"}
        if not self._connected:
            return {"state": "error", "progress": 0, "summary": "Cannot reach control port"}
        with self._bootstrap_lock:
            return dict(self._bootstrap)

    def get_circuit_detail(self):
        """Return enriched hop list for the best BUILT circuit (from cache)."""
        with self._circuit_lock:
            # Pick best circuit: prefer BUILT + PURPOSE=GENERAL
            best = None
            for c in self._circuits.values():
                if c["status"] != "BUILT":
                    continue
                if best is None or c.get("purpose") == "GENERAL":
                    best = c
                    if c.get("purpose") == "GENERAL":
                        break
            if not best:
                return None
            path_raw = best.get("path_raw", "")

        if not path_raw:
            return None

        # Parse hops: $FP~Name,$FP~Name,...
        hops = []
        for relay in path_raw.split(","):
            m = re.match(r"\$([0-9A-Fa-f]+)~(\S+)", relay)
            if m:
                hops.append({
                    "fingerprint": m.group(1),
                    "nickname": m.group(2),
                    "ip": "",
                    "country_code": "",
                    "country_name": "",
                })

        # Assign roles
        if len(hops) == 1:
            roles = ["exit"]
        elif len(hops) == 2:
            roles = ["entry", "exit"]
        elif len(hops) >= 3:
            roles = ["entry"] + ["middle"] * (len(hops) - 2) + ["exit"]
        else:
            roles = []

        # Enrich with IP + country (single persistent connection)
        for i, hop in enumerate(hops):
            hop["role"] = roles[i] if i < len(roles) else "relay"

            # Get IP via ns/id/<fingerprint>
            ns = self.command(f'GETINFO ns/id/{hop["fingerprint"]}')
            if ns:
                for nsline in ns.split("\r\n") if "\r\n" in ns else ns.split("\n"):
                    if nsline.startswith("r "):
                        fields = nsline.split()
                        if len(fields) >= 7:
                            hop["ip"] = fields[6]
                        break

            # Get country via ip-to-country/<ip>
            if hop["ip"]:
                cc_resp = self.command(f'GETINFO ip-to-country/{hop["ip"]}')
                if cc_resp:
                    cm = re.search(r"ip-to-country/\S+=(\S+)", cc_resp)
                    if cm:
                        cc = cm.group(1).upper()
                        hop["country_code"] = cc
                        hop["country_name"] = self.COUNTRY_NAMES.get(cc, cc)

        return hops

    def new_circuit(self):
        """Signal NEWNYM to build fresh circuits."""
        return self.command("SIGNAL NEWNYM")

    def get_bootstrap(self):
        """Return bootstrap dict (from cache)."""
        with self._bootstrap_lock:
            return dict(self._bootstrap)

    def shutdown(self):
        """Clean shutdown."""
        self._shutting_down = True
        self._close()


# Instantiate global controller
anon_ctrl = AnonController()


# ════════════════════════════════════════════════════════════════════
# Helper functions
# ════════════════════════════════════════════════════════════════════

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


# ════════════════════════════════════════════════════════════════════
# HTML Template
# ════════════════════════════════════════════════════════════════════

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

 /* Exit country */
 .circuit-status { padding:10px; border-radius:8px; font-size:12px; font-weight:600; text-align:center; margin-bottom:15px; }
 .circuit-status.active { background:rgba(3,189,197,0.1); color:var(--secondary); }
 .circuit-status.inactive { background:rgba(255,255,255,0.03); color:var(--dim); }

 /* Buttons */
 .btn-primary { background:var(--gradient); color:#FFF; border:none; padding:14px; border-radius:8px; font-weight:700; cursor:pointer; width:100%; font-size:14px; font-family:inherit; }
 .btn-secondary { background:rgba(255,255,255,0.06); color:var(--text); border:1px solid var(--border); padding:14px; border-radius:8px; font-weight:700; cursor:pointer; width:100%; font-size:14px; font-family:inherit; }
 .helper-text { font-size:11px; color:var(--dim); margin-top:6px; }
 select { width:100%; padding:10px; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:6px; font-family:inherit; margin-top:6px; }
 input[type=password] { width:100%; padding:10px; background:var(--bg); color:var(--text); border:1px solid var(--border); border-radius:6px; font-family:inherit; margin-bottom:8px; }

 /* Wi-Fi */
 .wifi-item { display:flex; justify-content:space-between; align-items:center; padding:10px; cursor:pointer; border-bottom:1px solid var(--border); font-size:13px; }
 .wifi-item:hover { background:rgba(255,255,255,0.03); }
 .connected-label { color:var(--secondary); font-size:11px; font-weight:700; }

 /* SSE indicator */
 .sse-dot { display:inline-block; height:6px; width:6px; border-radius:50%; margin-right:6px; background:#555; }
 .sse-dot.live { background:#03BDC5; box-shadow:0 0 6px #03BDC5; }
</style>
</head>
<body>
<div class="container">
 <img src="/static/logo.png" alt="Anyone" class="logo-img" onerror="this.style.display='none'">

 <!-- Connection Status -->
 <div class="card">
  <h3><span class="sse-dot" id="sse-dot"></span>Anyone Connection</h3>
  <div id="conn-badge" class="conn-badge stopped">
   <div class="conn-dot"></div>
   <span id="conn-label">Checking…</span>
  </div>
  <div class="progress-bar-bg"><div class="progress-bar-fill" id="conn-progress" style="width:0%"></div></div>
  <div class="conn-summary" id="conn-summary">Waiting for status…</div>
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
  <label style="font-size:12px;color:var(--dim)">Exit Node Country</label>
  <select id="exit-country">
   {% for code, name in countries %}
   <option value="{{ code }}" {{ 'selected' if code == exit_country else '' }}>{{ name }}</option>
   {% endfor %}
  </select>
  <p class="helper-text">{{ 'Select a country to route your traffic through.' if privacy else 'Enable Privacy Mode first.' }}</p>
 </div>

 <!-- Wi-Fi -->
 <div class="card">
  <h3>Wi-Fi</h3>
  <button id="scan-btn" class="btn-secondary" onclick="scan()">Scan Networks</button>
  <div id="list" style="margin-top:10px"></div>
  <div id="connect" style="display:none;margin-top:20px;border-top:1px solid var(--border);padding-top:10px;">
   <p style="font-size:12px;color:var(--secondary)">Connecting to: <b id="ssid-name"></b></p>
   <input type="password" id="pw" placeholder="Enter Password">
   <button id="conn-btn" class="btn-primary" onclick="connectWifi()">Connect Now</button>
  </div>
 </div>
</div>

<script>
const privacyActive = {{ 'true' if privacy else 'false' }};
let targetSSID = "";

/* Country flag emoji from 2-letter code */
function flag(cc) {
 if (!cc || cc.length !== 2) return '\u{1F30D}';
 return String.fromCodePoint(...[...cc.toUpperCase()].map(c => 0x1F1E6 + c.charCodeAt(0) - 65));
}

/* ══════════════════════════════════════════════════════════════════
   SSE — Server-Sent Events (replaces polling for status & circuit)
   ══════════════════════════════════════════════════════════════════ */
let evtSource = null;
let sseRetryTimer = null;

function connectSSE() {
 if (evtSource) { evtSource.close(); }
 evtSource = new EventSource('/api/events');
 const dot = document.getElementById('sse-dot');

 evtSource.onopen = () => {
  dot.classList.add('live');
  if (sseRetryTimer) { clearTimeout(sseRetryTimer); sseRetryTimer = null; }
 };

 evtSource.onmessage = (e) => {
  try {
   const d = JSON.parse(e.data);
   if (d.type === 'bootstrap') updateBootstrapUI(d);
   else if (d.type === 'circuit') fetchCircuitDetail();
  } catch(err) {}
 };

 evtSource.onerror = () => {
  dot.classList.remove('live');
  evtSource.close();
  // Reconnect after 3s
  sseRetryTimer = setTimeout(connectSSE, 3000);
 };
}

function updateBootstrapUI(d) {
 const badge = document.getElementById('conn-badge');
 const label = document.getElementById('conn-label');
 const bar = document.getElementById('conn-progress');
 const summ = document.getElementById('conn-summary');

 badge.className = 'conn-badge ' + d.state;
 const labels = {stopped:'STOPPED', bootstrapping:'BOOTSTRAPPING', connected:'CONNECTED', error:'ERROR'};
 let lt = labels[d.state] || d.state.toUpperCase();
 if (d.state === 'bootstrapping') lt += ' ' + d.progress + '%';
 label.innerText = lt;
 bar.style.width = d.progress + '%';
 summ.innerText = d.summary || '';
}

/* ══════════════════════════════════════════════════════════════════
   Circuit detail (fetched on-demand when SSE says circuit changed)
   ══════════════════════════════════════════════════════════════════ */
async function fetchCircuitDetail() {
 try {
  const r = await fetch('/api/anon/circuit');
  const d = await r.json();
  const c = document.getElementById('circuit-container');

  if (!d.hops || d.hops.length === 0) {
   c.innerHTML = '<div class="circuit-empty">No circuit established yet</div>';
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
 } catch(e) {}
}

/* ══════════════════════════════════════════════════════════════════
   Fallback polling (status + circuit) — only when SSE is down
   ══════════════════════════════════════════════════════════════════ */
async function pollStatus() {
 // Skip if SSE is connected
 if (evtSource && evtSource.readyState === EventSource.OPEN) return;
 try {
  const r = await fetch('/api/anon/status');
  const d = await r.json();
  updateBootstrapUI(d);
 } catch(e) {}
}

async function pollCircuit() {
 if (evtSource && evtSource.readyState === EventSource.OPEN) return;
 fetchCircuitDetail();
}

/* ══════════════════════════════════════════════════════════════════
   Actions
   ══════════════════════════════════════════════════════════════════ */
async function newCircuit() {
 const btn = document.getElementById('newnym-btn');
 btn.disabled = true; btn.innerText = '\u23F3 Requesting…';
 try {
  await fetch('/api/anon/newcircuit', {method:'POST'});
  // SSE will push the new circuit event — just wait a moment for it
  await new Promise(r => setTimeout(r, 2000));
  fetchCircuitDetail();
 } finally { btn.disabled = false; btn.innerHTML = '\u{1F504} New Circuit'; }
}

/* Traffic polling (stays at 1s — this is local /proc, not control port) */
async function pollTraffic() {
 try {
  const r = await fetch('/api/traffic'); const d = await r.json();
  document.getElementById('rx').innerText = (d.rx/1048576).toFixed(1)+' MB';
  document.getElementById('tx').innerText = (d.tx/1048576).toFixed(1)+' MB';
  document.getElementById('s_rx').innerText = d.speed_rx>1048576?(d.speed_rx/1048576).toFixed(1)+' MB/s':(d.speed_rx/1024).toFixed(1)+' KB/s';
  document.getElementById('s_tx').innerText = d.speed_tx>1048576?(d.speed_tx/1048576).toFixed(1)+' MB/s':(d.speed_tx/1024).toFixed(1)+' KB/s';
 } catch(e) {}
}

/* Exit country selector */
document.getElementById('exit-country').addEventListener('change', async function() {
 if (!privacyActive) { alert('Enable Privacy Mode first.'); this.value='{{ exit_country }}'; return; }
 const cc = this.value, st = document.getElementById('circuit-status');
 st.innerText = '\u23F3 Applying…';
 try {
  const r = await fetch('/api/circuit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exit_country:cc})});
  const d = await r.json();
  if (r.ok) { st.className = cc!=='auto'?'circuit-status active':'circuit-status inactive'; st.innerText = cc!=='auto'?'\u{1F512} Exit: '+cc.toUpperCase():'\u{1F30D} Automatic exit selection'; }
  else alert(d.status||'Error');
 } catch(e) { alert('Connection error'); }
});

/* Wi-Fi scan & connect */
async function scan() {
 const btn=document.getElementById('scan-btn'); btn.disabled=true; btn.innerText='Scanning…';
 document.getElementById('list').innerHTML='';
 try {
  const r=await fetch('/wifi/scan'); const d=await r.json();
  let h='';
  d.networks.forEach(n=>{
   const tag=n.connected?'<span class="connected-label">CONNECTED</span>':'<span>\u203A</span>';
   h+="<div class='wifi-item' onclick=\"sel('"+n.ssid.replace(/'/g,"\\'")+"')\"><span>"+n.ssid+"</span>"+tag+"</div>";
  });
  document.getElementById('list').innerHTML=h;
 } finally { btn.disabled=false; btn.innerText='Scan Networks'; }
}
function sel(s){ targetSSID=s; document.getElementById('ssid-name').innerText=s; document.getElementById('connect').style.display='block'; }
async function connectWifi(){
 const btn=document.getElementById('conn-btn'), pw=document.getElementById('pw').value;
 btn.disabled=true; btn.innerText='Connecting…';
 try {
  const r=await fetch('/wifi/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid:targetSSID,password:pw})});
  const d=await r.json(); alert(d.status);
  if(d.status==='Connected!') location.reload();
 } catch(e){alert('Error');}
 finally { btn.disabled=false; btn.innerText='Connect Now'; }
}

/* ══════════════════════════════════════════════════════════════════
   Startup: SSE first, polling as fallback
   ══════════════════════════════════════════════════════════════════ */
connectSSE();

// Initial fetches
pollStatus();
fetchCircuitDetail();
pollTraffic();

// Fallback polling (only fires when SSE is disconnected)
setInterval(pollStatus, 5000);
setInterval(pollCircuit, 8000);
setInterval(pollTraffic, 1000);
</script>
</body></html>
"""


# ════════════════════════════════════════════════════════════════════
# Routes
# ════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    p = subprocess.run("sudo iptables -t nat -L PREROUTING -n | grep 9040",
                       shell=True, capture_output=True).returncode == 0
    ec = get_current_exit_country()
    return render_template_string(HTML, privacy=p, exit_country=ec, countries=EXIT_COUNTRIES)


@app.route("/api/traffic")
def traffic():
    return jsonify(update_stats())


@app.route("/api/anon/status")
def api_anon_status():
    return jsonify(anon_ctrl.get_status())


@app.route("/api/anon/circuit")
def api_anon_circuit():
    hops = anon_ctrl.get_circuit_detail()
    return jsonify({"hops": hops or []})


@app.route("/api/anon/newcircuit", methods=["POST"])
def api_new_circuit():
    resp = anon_ctrl.new_circuit()
    ok = resp is not None and "250" in resp
    return jsonify({"status": "ok" if ok else "failed"})


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


# ════════════════════════════════════════════════════════════════════
# SSE endpoint — Server-Sent Events
# ════════════════════════════════════════════════════════════════════

@app.route("/api/events")
def sse_stream():
    """Server-Sent Events endpoint.
    Pushes bootstrap & circuit events in real-time to the browser.
    """
    def generate():
        q = anon_ctrl.sse_subscribe()
        try:
            # Send current state immediately on connect
            status = anon_ctrl.get_status()
            yield f"data: {json.dumps({'type': 'bootstrap', **status})}\n\n"

            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    # Keepalive comment (prevents proxy/browser timeout)
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            anon_ctrl.sse_unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-Accel-Buffering": "no",   # nginx compatibility
                        "Connection": "keep-alive",
                    })


# ════════════════════════════════════════════════════════════════════
# Wi-Fi & Mode routes
# ════════════════════════════════════════════════════════════════════

@app.route("/wifi/scan")
def w_scan():
    raw = subprocess.check_output("nmcli -t -f SSID,ACTIVE dev wifi list",
                                  shell=True).decode("utf-8")
    nets, seen = [], set()
    for line in raw.split("\n"):
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, threaded=True)
