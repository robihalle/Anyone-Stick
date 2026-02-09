from flask import Flask, request, jsonify, render_template_string, redirect
import subprocess, time, os, signal, re, socket, json

app = Flask(__name__)

# ─── Traffic stats ──────────────────────────────────────────────────────
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
ANONRC_PATH = "/etc/anonrc"
STATE_DIR = "/var/lib/anyone-stick"

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

ROTATION_PRESETS = [
    ("60",   "\U0001f525 Max Privacy (1 min)"),
    ("180",  "\U0001f512 Paranoid (3 min)"),
    ("300",  "\u2696\ufe0f Balanced (5 min)"),
    ("600",  "\U0001f422 Stable (10 min)"),
    ("1800", "\U0001f4a4 Relaxed (30 min)"),
]


# ═══════════════════════════════════════════════════════════════════════
#  Anon Control-Port Interface (Tor-compatible control protocol)
# ═══════════════════════════════════════════════════════════════════════

class AnonController:
    """Communicates with the anon daemon via its control port."""

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

    def _recv(self, sock):
        buf = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                text = buf.decode("utf-8", errors="replace")
                for line in text.rstrip("\r\n").split("\r\n"):
                    if len(line) >= 4 and line[3] == " " and line[:3].isdigit():
                        return text
            except socket.timeout:
                break
        return buf.decode("utf-8", errors="replace")

    def _connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((self.host, self.port))
        methods = []
        try:
            with open(self.cookie_path, "rb") as f:
                cookie = f.read().hex()
            methods.append(f"AUTHENTICATE {cookie}")
        except FileNotFoundError:
            pass
        methods += ['AUTHENTICATE ""', "AUTHENTICATE"]
        for m in methods:
            sock.sendall(f"{m}\r\n".encode())
            resp = self._recv(sock)
            if resp.startswith("250"):
                return sock
        sock.close()
        return None

    def _cmd(self, sock, command):
        sock.sendall(f"{command}\r\n".encode())
        return self._recv(sock)

    def command(self, cmd):
        try:
            sock = self._connect()
            if not sock:
                return None
            resp = self._cmd(sock, cmd)
            self._cmd(sock, "QUIT")
            sock.close()
            return resp
        except Exception:
            return None

    def is_running(self):
        try:
            subprocess.check_output(["pgrep", "-x", "anon"])
            return True
        except subprocess.CalledProcessError:
            return False

    def get_bootstrap(self):
        resp = self.command("GETINFO status/bootstrap-phase")
        if not resp:
            return None
        progress = 0
        summary = ""
        tag = ""
        m = re.search(r"PROGRESS=(\d+)", resp)
        if m:
            progress = int(m.group(1))
        m = re.search(r'SUMMARY="([^"]*)"', resp)
        if m:
            summary = m.group(1)
        m = re.search(r"TAG=(\S+)", resp)
        if m:
            tag = m.group(1)
        return {"progress": progress, "summary": summary, "tag": tag}

    def get_status(self):
        if not self.is_running():
            return {"state": "stopped", "progress": 0, "summary": "Anon is not running"}
        bs = self.get_bootstrap()
        if bs is None:
            return {"state": "error", "progress": 0,
                    "summary": "Cannot reach control port"}
        if bs["progress"] >= 100:
            return {"state": "connected", "progress": 100,
                    "summary": bs["summary"] or "Connected"}
        else:
            return {"state": "bootstrapping", "progress": bs["progress"],
                    "summary": bs["summary"] or f"Bootstrapping {bs['progress']}%"}

    def get_circuit_detail(self):
        try:
            sock = self._connect()
            if not sock:
                return None
            raw = self._cmd(sock, "GETINFO circuit-status")
            if not raw:
                self._cmd(sock, "QUIT"); sock.close()
                return None
            built_path = None
            for line in raw.split("\r\n"):
                line = line.strip()
                if not line or line.startswith("250") or line == ".":
                    continue
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "BUILT":
                    built_path = parts[2]
                    if "PURPOSE=GENERAL" in line:
                        break
            if not built_path:
                self._cmd(sock, "QUIT"); sock.close()
                return None
            hops = []
            for relay in built_path.split(","):
                m = re.match(r"\$([0-9A-Fa-f]+)~(\S+)", relay)
                if m:
                    hops.append({
                        "fingerprint": m.group(1),
                        "nickname": m.group(2),
                        "ip": "",
                        "country_code": "",
                        "country_name": "",
                    })
            roles = ["entry", "middle", "exit"]
            if len(hops) == 1:
                roles = ["exit"]
            elif len(hops) == 2:
                roles = ["entry", "exit"]
            elif len(hops) >= 3:
                roles = ["entry"] + ["middle"] * (len(hops) - 2) + ["exit"]
            for i, hop in enumerate(hops):
                hop["role"] = roles[i] if i < len(roles) else "relay"
                ns = self._cmd(sock, f'GETINFO ns/id/{hop["fingerprint"]}')
                if ns:
                    for nsline in ns.split("\r\n"):
                        if nsline.startswith("r "):
                            fields = nsline.split()
                            if len(fields) >= 7:
                                hop["ip"] = fields[6]
                            break
                if hop["ip"]:
                    cc_resp = self._cmd(sock, f'GETINFO ip-to-country/{hop["ip"]}')
                    if cc_resp:
                        cm = re.search(r"ip-to-country/\S+=(\S+)", cc_resp)
                        if cm:
                            cc = cm.group(1).upper()
                            hop["country_code"] = cc
                            hop["country_name"] = self.COUNTRY_NAMES.get(cc, cc)
            self._cmd(sock, "QUIT")
            sock.close()
            return hops
        except Exception:
            return None

    def new_circuit(self):
        return self.command("SIGNAL NEWNYM")


anon_ctrl = AnonController()


# ═══════════════════════════════════════════════════════════════════════
#  Helper functions
# ═══════════════════════════════════════════════════════════════════════

def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)

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
    _reload_anon()
    return True


def get_circuit_rotation():
    """Read MaxCircuitDirtiness from anonrc."""
    try:
        with open(ANONRC_PATH) as f:
            for line in f:
                if line.strip().startswith("MaxCircuitDirtiness"):
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        return parts[1]
    except FileNotFoundError:
        pass
    return "300"


def set_circuit_rotation(seconds):
    """Update MaxCircuitDirtiness in anonrc."""
    try:
        with open(ANONRC_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    new = [l for l in lines if not l.strip().startswith("MaxCircuitDirtiness")]
    if new and not new[-1].endswith("\n"):
        new[-1] += "\n"
    new.append(f"MaxCircuitDirtiness {seconds}\n")
    with open(ANONRC_PATH, "w") as f:
        f.writelines(new)
    _reload_anon()
    return True


def get_killswitch_status():
    return os.path.exists(os.path.join(STATE_DIR, "killswitch_active"))


def get_mac_random_status():
    return os.path.exists(os.path.join(STATE_DIR, "mac_random_enabled"))


def set_mac_random(enabled):
    ensure_state_dir()
    flag = os.path.join(STATE_DIR, "mac_random_enabled")
    if enabled:
        with open(flag, "w") as f:
            f.write("1")
    else:
        if os.path.exists(flag):
            os.remove(flag)
    return True


def get_privacy_checks():
    """Run leak/privacy checks and return status dict."""
    checks = {}
    # IPv6 disabled?
    try:
        with open("/proc/sys/net/ipv6/conf/all/disable_ipv6") as f:
            checks["ipv6_disabled"] = f.read().strip() == "1"
    except Exception:
        checks["ipv6_disabled"] = False

    # DNS via Anon? (check iptables for port 53 redirect)
    try:
        out = subprocess.check_output("iptables -t nat -L PREROUTING -n", shell=True).decode()
        checks["dns_tunneled"] = "9053" in out
    except Exception:
        checks["dns_tunneled"] = False

    # Kill Switch active?
    checks["killswitch"] = get_killswitch_status()

    # Forward policy DROP?
    try:
        out = subprocess.check_output("iptables -L FORWARD -n", shell=True).decode()
        checks["forward_drop"] = "DROP" in out.split("\n")[0]
    except Exception:
        checks["forward_drop"] = False

    # Stream isolation?
    try:
        with open(ANONRC_PATH) as f:
            content = f.read()
        checks["stream_isolation"] = "IsolateDestAddr" in content
    except Exception:
        checks["stream_isolation"] = False

    return checks


def _reload_anon():
    """Send SIGHUP to anon daemon to reload config."""
    try:
        pid = subprocess.check_output("pgrep -x anon", shell=True).decode().strip().split("\n")[0]
        os.kill(int(pid), signal.SIGHUP)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
#  HTML Template
# ═══════════════════════════════════════════════════════════════════════

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anyone Privacy Stick</title>
<link href="https://fonts.googleapis.com/css2?family=Mona+Sans:wght@200..900&display=swap" rel="stylesheet">
<style>
    :root { --primary:#0280AF; --secondary:#03BDC5; --gradient:linear-gradient(90deg,#0280AF 0%,#03BDC5 100%); --bg:#0b1116; --card:#151b23; --text:#FFF; --dim:#8b949e; --border:#30363d; --green:#3fb950; --red:#f85149; --yellow:#d2992a; }
    * { box-sizing:border-box; }
    body { font-family:"Mona Sans",sans-serif; background:var(--bg); color:var(--text); margin:0; padding:20px; display:flex; flex-direction:column; align-items:center; }
    .container { width:100%; max-width:420px; }
    .logo-img { max-width:180px; height:auto; display:block; margin:0 auto 20px; }
    .card { background:var(--card); border:1px solid var(--border); border-radius:12px; padding:24px; margin-bottom:20px; }
    h3 { font-size:11px; text-transform:uppercase; color:var(--secondary); margin:0 0 15px; font-weight:800; }
    /* ── Status badge ────────────────────────────── */
    .status-indicator { display:flex; align-items:center; justify-content:center; padding:15px; border-radius:8px; font-weight:600; margin-bottom:20px; background:rgba(255,255,255,0.03); }
    .dot { height:8px; width:8px; border-radius:50%; margin-right:12px; background:#555; flex-shrink:0; }
    .active .dot { background:var(--secondary); box-shadow:0 0 12px var(--secondary); }
    /* ── Connection status ───────────────────────── */
    .conn-badge { display:inline-flex; align-items:center; gap:8px; padding:8px 14px; border-radius:8px; font-weight:700; font-size:13px; }
    .conn-badge.stopped    { background:rgba(248,81,73,0.12); color:#f85149; }
    .conn-badge.bootstrapping { background:rgba(210,153,34,0.12); color:#d2992a; }
    .conn-badge.connected  { background:rgba(3,189,197,0.12); color:var(--secondary); }
    .conn-badge.error      { background:rgba(248,81,73,0.12); color:#f85149; }
    .conn-dot { height:8px; width:8px; border-radius:50%; flex-shrink:0; }
    .stopped .conn-dot     { background:#f85149; }
    .bootstrapping .conn-dot { background:#d2992a; animation:pulse 1.2s infinite; }
    .connected .conn-dot   { background:var(--secondary); box-shadow:0 0 10px var(--secondary); }
    .error .conn-dot       { background:#f85149; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
    .progress-bar-bg { width:100%; height:6px; background:rgba(255,255,255,0.06); border-radius:3px; margin-top:14px; overflow:hidden; }
    .progress-bar-fill { height:100%; border-radius:3px; background:var(--gradient); transition:width .6s ease; }
    .conn-summary { font-size:12px; color:var(--dim); margin-top:8px; }
    /* ── Circuit chain ───────────────────────────── */
    .circuit-chain { display:flex; align-items:stretch; justify-content:center; gap:0; margin:10px 0; }
    .circuit-node { flex:1; background:rgba(255,255,255,0.03); border:1px solid var(--border); border-radius:10px; padding:12px 8px; text-align:center; min-width:0; }
    .circuit-node.active-node { border-color:var(--secondary); }
    .node-role { font-size:9px; text-transform:uppercase; font-weight:800; color:var(--secondary); margin-bottom:4px; }
    .node-name { font-size:11px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .node-ip { font-size:10px; color:var(--dim); margin-top:2px; }
    .node-country { font-size:10px; color:var(--dim); }
    .circuit-arrow { display:flex; align-items:center; color:var(--secondary); font-size:16px; padding:0 2px; }
    .circuit-empty { text-align:center; color:var(--dim); font-size:12px; padding:20px; }
    /* ── Countdown ───────────────────────────────── */
    .countdown-bar { display:flex; align-items:center; gap:10px; margin-top:12px; padding:10px; background:rgba(255,255,255,0.03); border-radius:8px; }
    .countdown-text { font-size:12px; color:var(--dim); }
    .countdown-time { font-size:14px; font-weight:700; color:var(--secondary); font-variant-numeric:tabular-nums; }
    .countdown-progress { flex:1; height:4px; background:rgba(255,255,255,0.06); border-radius:2px; overflow:hidden; }
    .countdown-fill { height:100%; background:var(--gradient); transition:width 1s linear; }
    /* ── Privacy checks ──────────────────────────── */
    .check-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .check-item { display:flex; align-items:center; gap:6px; font-size:12px; padding:8px; background:rgba(255,255,255,0.02); border-radius:6px; }
    .check-icon { font-size:14px; }
    .check-ok { color:var(--green); }
    .check-warn { color:var(--red); }
    /* ── Toggle switch ───────────────────────────── */
    .toggle-row { display:flex; align-items:center; justify-content:space-between; padding:10px 0; }
    .toggle-label { font-size:13px; font-weight:600; }
    .toggle-sub { font-size:11px; color:var(--dim); }
    .toggle { position:relative; width:44px; height:24px; cursor:pointer; }
    .toggle input { opacity:0; width:0; height:0; }
    .toggle .slider { position:absolute; top:0; left:0; right:0; bottom:0; background:var(--border); border-radius:12px; transition:.3s; }
    .toggle .slider:before { content:""; position:absolute; height:18px; width:18px; left:3px; bottom:3px; background:#fff; border-radius:50%; transition:.3s; }
    .toggle input:checked + .slider { background:var(--secondary); }
    .toggle input:checked + .slider:before { transform:translateX(20px); }
    /* ── Buttons ─────────────────────────────────── */
    .btn-primary { width:100%; padding:14px; border:none; border-radius:8px; background:var(--gradient); color:#fff; font-weight:700; font-size:14px; cursor:pointer; }
    .btn-secondary { width:100%; padding:14px; border:1px solid var(--border); border-radius:8px; background:transparent; color:var(--text); font-weight:600; font-size:14px; cursor:pointer; }
    .btn-sm { padding:8px 16px; font-size:12px; border-radius:6px; }
    input[type="password"] { width:100%; padding:12px 14px; border:1px solid var(--border); border-radius:8px; background:var(--bg); color:#fff; font-size:14px; margin-bottom:10px; }
    select { width:100%; padding:12px 14px; border:1px solid var(--border); border-radius:8px; background:var(--bg); color:#fff; font-size:14px; appearance:none; -webkit-appearance:none; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%238b949e' viewBox='0 0 16 16'%3E%3Cpath d='M1.5 5.5l6.5 6.5 6.5-6.5'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 14px center; cursor:pointer; }
    select option { background:#0d1117; color:#fff; }
    .helper-text { font-size:11px; color:var(--dim); margin-top:4px; }
    .circuit-status { display:flex; align-items:center; gap:8px; margin-bottom:15px; padding:10px; border-radius:6px; font-size:12px; font-weight:600; }
    .circuit-status.active   { background:rgba(3,189,197,0.08); color:var(--secondary); }
    .circuit-status.inactive { background:rgba(255,255,255,0.03); color:var(--dim); }
    /* ── Wi-Fi ───────────────────────────────────── */
    .wifi-item { display:flex; justify-content:space-between; align-items:center; padding:10px; border-bottom:1px solid var(--border); cursor:pointer; font-size:13px; }
    .wifi-item:hover { background:rgba(255,255,255,0.03); }
    .connected-label { font-size:10px; color:var(--green); font-weight:700; }
    /* ── Traffic ──────────────────────────────────── */
    .traffic-grid { display:grid; grid-template-columns:1fr 1fr; gap:15px; text-align:center; }
    .traffic-val { font-size:20px; font-weight:700; margin-top:4px; }
    .traffic-speed { font-size:11px; color:var(--dim); margin-top:2px; }
</style>
</head>
<body>
<div class="container">
    <img src="/static/logo.jpg" onerror="this.src='/static/logo.png'" class="logo-img">

    <!-- ═══ Connection Status ═══ -->
    <div class="card">
        <h3>Anyone Connection</h3>
        <div id="conn-badge" class="conn-badge stopped">
            <div class="conn-dot"></div>
            <span id="conn-label">Checking…</span>
        </div>
        <div class="progress-bar-bg"><div class="progress-bar-fill" id="conn-progress" style="width:0%"></div></div>
        <div class="conn-summary" id="conn-summary">Waiting for status…</div>
    </div>

    <!-- ═══ Circuit Chain ═══ -->
    <div class="card">
        <h3>Circuit Chain</h3>
        <div id="circuit-container">
            <div class="circuit-empty">No circuit available</div>
        </div>
        <div class="countdown-bar">
            <span class="countdown-text">Next rotation:</span>
            <span class="countdown-time" id="countdown-time">--:--</span>
            <div class="countdown-progress"><div class="countdown-fill" id="countdown-fill" style="width:100%"></div></div>
        </div>
        <button class="btn-sm btn-secondary" style="width:auto;margin-top:10px" onclick="newCircuit()" id="newnym-btn">&#x1f504; New Circuit</button>
    </div>

    <!-- ═══ Mode Switch ═══ -->
    <div class="card">
        <h3>Mode</h3>
        <div class="status-indicator {{ 'active' if privacy else '' }}"><div class="dot"></div>{{ 'PRIVACY ACTIVE' if privacy else 'NORMAL MODE' }}</div>
        <form action="/mode/{{ 'normal' if privacy else 'privacy' }}" method="post"><button class="{{ 'btn-secondary' if privacy else 'btn-primary' }}">{{ 'Switch to Normal' if privacy else 'Enable Privacy' }}</button></form>
    </div>

    <!-- ═══ Privacy Status ═══ -->
    <div class="card">
        <h3>Privacy Status</h3>
        <div class="check-grid" id="privacy-checks">
            <div class="check-item"><span class="check-icon">⏳</span> Loading…</div>
        </div>
    </div>

    <!-- ═══ Privacy Settings ═══ -->
    <div class="card">
        <h3>Privacy Settings</h3>

        <!-- Kill Switch -->
        <div class="toggle-row">
            <div>
                <div class="toggle-label">🛡️ Kill Switch</div>
                <div class="toggle-sub">Block all traffic if tunnel drops</div>
            </div>
            <div class="check-item" style="padding:0;background:none;">
                <span id="ks-status" class="check-icon">{{ '✅' if killswitch else '❌' }}</span>
                <span style="font-size:11px;color:var(--dim)">{{ 'Active' if killswitch else 'Inactive' }}</span>
            </div>
        </div>

        <!-- MAC Randomization -->
        <div class="toggle-row" style="border-top:1px solid var(--border);padding-top:14px;margin-top:4px;">
            <div>
                <div class="toggle-label">🎲 MAC Randomization</div>
                <div class="toggle-sub">Randomize MAC on connect</div>
            </div>
            <label class="toggle">
                <input type="checkbox" id="mac-toggle" {{ 'checked' if mac_random else '' }} onchange="toggleMAC(this.checked)">
                <span class="slider"></span>
            </label>
        </div>

        <!-- Circuit Rotation -->
        <div style="border-top:1px solid var(--border);padding-top:14px;margin-top:14px;">
            <label style="font-size:12px;color:var(--dim)">⏱️ Circuit Rotation Interval</label>
            <select id="rotation-select" onchange="setRotation(this.value)" style="margin-top:6px">
                {% for val, name in rotation_presets %}
                <option value="{{ val }}" {{ 'selected' if val == rotation_val else '' }}>{{ name }}</option>
                {% endfor %}
            </select>
        </div>
    </div>

    <!-- ═══ Live Traffic ═══ -->
    <div class="card">
        <h3>Live Traffic</h3>
        <div class="traffic-grid">
            <div><div style="font-size:10px;color:var(--dim)">DOWNLOAD</div><div class="traffic-val" id="rx">0 MB</div><div class="traffic-speed" id="s_rx">0 KB/s</div></div>
            <div><div style="font-size:10px;color:var(--dim)">UPLOAD</div><div class="traffic-val" id="tx">0 MB</div><div class="traffic-speed" id="s_tx">0 KB/s</div></div>
        </div>
    </div>

    <!-- ═══ Exit Country ═══ -->
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

    <!-- ═══ Wi-Fi ═══ -->
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
let targetSSID = '';
let circuitDirtiness = {{ rotation_val }};
let countdownRemaining = circuitDirtiness;

/* ── Connection status polling ──────────────────── */
async function pollStatus() {
    try {
        const r = await fetch('/api/anon/status'); const d = await r.json();
        const badge = document.getElementById('conn-badge');
        const label = document.getElementById('conn-label');
        const bar   = document.getElementById('conn-progress');
        const sum   = document.getElementById('conn-summary');
        badge.className = 'conn-badge ' + d.state;
        const labels = {stopped:'Disconnected',bootstrapping:'Connecting…',connected:'Connected',error:'Error'};
        label.innerText = labels[d.state] || d.state;
        bar.style.width = d.progress + '%';
        sum.innerText = d.summary || '';
    } catch(e) {}
}

/* ── Circuit detail polling ─────────────────────── */
async function pollCircuit() {
    try {
        const r = await fetch('/api/anon/circuit'); const d = await r.json();
        const c = document.getElementById('circuit-container');
        if (!d.circuit || d.circuit.length === 0) { c.innerHTML = '<div class="circuit-empty">No circuit available</div>'; return; }
        let html = '<div class="circuit-chain">';
        d.circuit.forEach((hop, i) => {
            if (i > 0) html += '<div class="circuit-arrow">→</div>';
            const cc = hop.country_code ? hop.country_code : '';
            html += '<div class="circuit-node' + (hop.role==='exit'?' active-node':'') + '">'
                + '<div class="node-role">' + hop.role + '</div>'
                + '<div class="node-name">' + (hop.nickname || '\u2014') + '</div>'
                + '<div class="node-ip">'   + (hop.ip || '\u2014') + '</div>'
                + '<div class="node-country">' + (hop.country_name || cc || '\u2014') + '</div>'
                + '</div>';
        });
        html += '</div>';
        c.innerHTML = html;
    } catch(e) {}
}

/* ── New circuit ────────────────────────────────── */
async function newCircuit() {
    const btn = document.getElementById('newnym-btn');
    btn.disabled = true; btn.innerText = '\u23F3 Requesting…';
    try {
        await fetch('/api/anon/newcircuit', {method:'POST'});
        countdownRemaining = circuitDirtiness;
        await new Promise(r => setTimeout(r, 2000));
        pollCircuit();
    } finally { btn.disabled = false; btn.innerHTML = '\u{1F504} New Circuit'; }
}

/* ── Circuit countdown timer ────────────────────── */
function updateCountdown() {
    countdownRemaining--;
    if (countdownRemaining <= 0) {
        countdownRemaining = circuitDirtiness;
        pollCircuit();
    }
    const mins = Math.floor(countdownRemaining / 60);
    const secs = countdownRemaining % 60;
    document.getElementById('countdown-time').innerText = mins + ':' + (secs < 10 ? '0' : '') + secs;
    const pct = (countdownRemaining / circuitDirtiness) * 100;
    document.getElementById('countdown-fill').style.width = pct + '%';
}

/* ── Traffic polling ────────────────────────────── */
async function pollTraffic() {
    try {
        const r = await fetch('/api/traffic'); const d = await r.json();
        document.getElementById('rx').innerText = (d.rx/1048576).toFixed(1)+' MB';
        document.getElementById('tx').innerText = (d.tx/1048576).toFixed(1)+' MB';
        document.getElementById('s_rx').innerText = d.speed_rx>1048576?(d.speed_rx/1048576).toFixed(1)+' MB/s':(d.speed_rx/1024).toFixed(1)+' KB/s';
        document.getElementById('s_tx').innerText = d.speed_tx>1048576?(d.speed_tx/1048576).toFixed(1)+' MB/s':(d.speed_tx/1024).toFixed(1)+' KB/s';
    } catch(e) {}
}

/* ── Privacy checks polling ─────────────────────── */
async function pollPrivacyChecks() {
    try {
        const r = await fetch('/api/privacy/checks'); const d = await r.json();
        const grid = document.getElementById('privacy-checks');
        const items = [
            {key:'ipv6_disabled', ok:'IPv6 Disabled', warn:'IPv6 Active!'},
            {key:'dns_tunneled', ok:'DNS Tunneled', warn:'DNS Exposed!'},
            {key:'killswitch', ok:'Kill Switch On', warn:'Kill Switch Off'},
            {key:'stream_isolation', ok:'Stream Isolated', warn:'No Isolation'},
        ];
        let html = '';
        items.forEach(item => {
            const ok = d[item.key];
            html += '<div class="check-item"><span class="check-icon ' + (ok?'check-ok':'check-warn') + '">' + (ok?'✅':'⚠️') + '</span><span>' + (ok?item.ok:item.warn) + '</span></div>';
        });
        grid.innerHTML = html;
    } catch(e) {}
}

/* ── Exit country selector ──────────────────────── */
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

/* ── Circuit rotation selector ──────────────────── */
async function setRotation(val) {
    try {
        const r = await fetch('/api/privacy/rotation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({seconds:parseInt(val)})});
        if (r.ok) { circuitDirtiness = parseInt(val); countdownRemaining = circuitDirtiness; }
    } catch(e) { alert('Error setting rotation'); }
}

/* ── MAC toggle ─────────────────────────────────── */
async function toggleMAC(enabled) {
    try {
        await fetch('/api/privacy/mac',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enabled})});
    } catch(e) { alert('Error toggling MAC randomization'); }
}

/* ── Wi-Fi ──────────────────────────────────────── */
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

/* ── Start polling ──────────────────────────────── */
pollStatus(); pollCircuit(); pollTraffic(); pollPrivacyChecks();
setInterval(pollStatus, 5000);
setInterval(pollCircuit, 15000);
setInterval(pollTraffic, 2000);
setInterval(pollPrivacyChecks, 10000);
setInterval(updateCountdown, 1000);
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════
#  Flask Routes
# ═══════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    privacy = os.path.exists("/tmp/privacy_mode") or get_killswitch_status()
    try:
        with open("/tmp/privacy_mode"):
            privacy = True
    except FileNotFoundError:
        # Check if iptables has transparent proxy rules
        try:
            out = subprocess.check_output("iptables -t nat -L PREROUTING -n", shell=True).decode()
            privacy = "9040" in out
        except Exception:
            privacy = False

    return render_template_string(HTML,
        privacy=privacy,
        exit_country=get_current_exit_country(),
        countries=EXIT_COUNTRIES,
        killswitch=get_killswitch_status(),
        mac_random=get_mac_random_status(),
        rotation_presets=ROTATION_PRESETS,
        rotation_val=get_circuit_rotation(),
    )


# ── Anon status APIs ───────────────────────────────────────────────────

@app.route("/api/anon/status")
def api_anon_status():
    return jsonify(anon_ctrl.get_status())


@app.route("/api/anon/circuit")
def api_anon_circuit():
    hops = anon_ctrl.get_circuit_detail()
    return jsonify({"circuit": hops or []})


@app.route("/api/anon/newcircuit", methods=["POST"])
def api_anon_newcircuit():
    r = anon_ctrl.new_circuit()
    return jsonify({"status": "ok" if r else "error"})


@app.route("/api/traffic")
def api_traffic():
    return jsonify(update_stats())


@app.route("/api/circuit", methods=["POST"])
def api_circuit():
    data = request.get_json()
    cc = data.get("exit_country", "auto").lower().strip()
    if cc != "auto" and (len(cc) != 2 or not cc.isalpha()):
        return jsonify({"status": "Invalid country code"}), 400
    set_exit_country(cc)
    return jsonify({"status": "ok", "exit_country": cc})


# ── Privacy APIs ────────────────────────────────────────────────────────

@app.route("/api/privacy/checks")
def api_privacy_checks():
    return jsonify(get_privacy_checks())


@app.route("/api/privacy/rotation", methods=["POST"])
def api_privacy_rotation():
    data = request.get_json()
    seconds = data.get("seconds", 300)
    if not isinstance(seconds, int) or seconds < 30 or seconds > 3600:
        return jsonify({"status": "Invalid value (30-3600)"}), 400
    set_circuit_rotation(seconds)
    return jsonify({"status": "ok", "seconds": seconds})


@app.route("/api/privacy/mac", methods=["POST"])
def api_privacy_mac():
    data = request.get_json()
    enabled = data.get("enabled", False)
    set_mac_random(enabled)
    return jsonify({"status": "ok", "enabled": enabled})


# ── Wi-Fi ───────────────────────────────────────────────────────────────

@app.route("/wifi/scan")
def w_scan():
    try:
        raw = subprocess.check_output(
            "nmcli -t -f SSID,ACTIVE dev wifi list", shell=True
        ).decode()
    except Exception:
        return jsonify({"networks": []})
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
    ensure_state_dir()
    app.run(host="0.0.0.0", port=80)
