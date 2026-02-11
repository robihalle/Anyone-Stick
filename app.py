#!/usr/bin/env python3
# ============================================================================
# Anyone Privacy Stick — Portal (app.py)
# AnonController v2: persistent ControlPort + SSE push events
# ============================================================================

from flask import Flask, request, jsonify, render_template_string, redirect, Response
import subprocess, time, os, signal, re, socket, threading, queue, json, logging

app = Flask(__name__, static_folder="static")
log = logging.getLogger("anyone-stick")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

# ──────────────────────────────────────────────
# Global state
# ──────────────────────────────────────────────
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


# ============================================================================
# AnonController v2 — Persistent connection with event support
# ============================================================================



# ============================================================================
# ============================================================================
# AnonController
# ============================================================================
class AnonController:
    def __init__(self):
        self._connected = False
        self._sse_clients = []
        self._lock = threading.Lock()

    def start(self):
        log.info("AnonController started")

    def get_status(self):
        import os
        running = os.system("pgrep -x anon > /dev/null") == 0
        if running:
            return {"progress": 100, "state": "connected", "summary": "Connected"}
        else:
            return {"progress": 0, "state": "stopped", "summary": "Stopped"}
    def get_circuit_detail(self):
        return []

    def new_circuit(self):
        return "250 OK"

    def add_sse_client(self):
        q = queue.Queue()
        with self._lock:
            self._sse_clients.append(q)
        return q

    def remove_sse_client(self, q):
        with self._lock:
            if q in self._sse_clients:
                self._sse_clients.remove(q)


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


# ============================================================================
# HTML Template
# ============================================================================

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
</style>
</head>
<body>
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

// ──── Start: SSE for status+circuit, polling only for traffic ────
connectSSE();
pollTraffic();
setInterval(pollTraffic, 1000);
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


# ============================================================================
# Startup
# ============================================================================

# Start persistent ControlPort connection


# ============================================================================
# Startup
# ============================================================================

anon_ctrl = AnonController()
anon_ctrl.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, threaded=True)

# ---- SIMPLE STATUS PUSH LOOP ----

def _status_push_loop():
    while True:
        try:
            status = anon_ctrl.get_status()
            event = {
                "type": "status",
                "state": status["state"],
                "progress": status["progress"],
                "summary": status["summary"]
            }
            with anon_ctrl._lock:
                for q in anon_ctrl._sse_clients:
                    q.put(event)
        except Exception:
            pass
        time.sleep(2)

import threading, time
status_thread = threading.Thread(target=_status_push_loop, daemon=True)
status_thread.start()

