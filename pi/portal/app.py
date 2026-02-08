from flask import Flask, request, jsonify, render_template_string, redirect
import json, subprocess, time

app = Flask(__name__)
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
CIRCUIT_CONFIG_PATH = "/etc/anyone-circuit.json"
DEFAULT_CIRCUIT_CONFIG = {
    "exit_country": "auto",
    "notes": "Managed by portal; SDK integration pending."
}

def update_stats():
    global stats
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                if "usb0" in line:
                    d = line.split()
                    curr_rx, curr_tx, curr_t = int(d[1]), int(d[9]), time.time()
                    if stats["time"] > 0:
                        dt = curr_t - stats["time"]
                        stats["speed_rx"] = (curr_rx - stats["rx"]) / dt
                        stats["speed_tx"] = (curr_tx - stats["tx"]) / dt
                    stats.update({"rx": curr_rx, "tx": curr_tx, "time": curr_t})
    except: pass
    return stats

def load_circuit_config():
    try:
        with open(CIRCUIT_CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_CIRCUIT_CONFIG.copy()

def save_circuit_config(cfg):
    with open(CIRCUIT_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Anyone Privacy Stick</title>
<link href="https://fonts.googleapis.com/css2?family=Mona+Sans:wght@200..900&display=swap" rel="stylesheet">
<style>
    :root { --primary: #0280AF; --secondary: #03BDC5; --gradient: linear-gradient(90deg, #0280AF 0%, #03BDC5 100%); --bg-color: #0b1116; --card-bg: #151b23; --text-main: #FFFFFF; --text-dim: #8b949e; --border: #30363d; }
    body { font-family: "Mona Sans", sans-serif; background-color: var(--bg-color); color: var(--text-main); margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; }
    .container { width: 100%; max-width: 420px; }
    .logo-img { max-width: 180px; height: auto; display: block; margin: 0 auto 20px auto; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 20px; }
    h3 { font-size: 11px; text-transform: uppercase; color: var(--secondary); margin: 0 0 15px 0; font-weight: 800; }
    .status-indicator { display: flex; align-items: center; justify-content: center; padding: 15px; border-radius: 8px; font-weight: 600; margin-bottom: 20px; background: rgba(255,255,255,0.03); }
    .dot { height: 8px; width: 8px; border-radius: 50%; margin-right: 12px; background: #555; }
    .active .dot { background: var(--secondary); box-shadow: 0 0 12px var(--secondary); }
    .traffic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
    .traffic-val { font-size: 18px; font-weight: 700; color: #fff; }
    .traffic-speed { font-size: 11px; color: var(--secondary); font-weight: 600; }
    button { width: 100%; padding: 16px; border: none; border-radius: 8px; font-size: 14px; font-weight: 700; cursor: pointer; transition: 0.2s; font-family: inherit; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary { background: var(--gradient); color: #fff; }
    .btn-secondary { background: #21262d; color: #fff; border: 1px solid var(--border); }
    .wifi-item { padding: 12px; border-bottom: 1px solid var(--border); cursor: pointer; display: flex; justify-content: space-between; font-size: 14px; }
    .connected-label { color: var(--secondary); font-weight: 800; font-size: 10px; border: 1px solid var(--secondary); padding: 2px 6px; border-radius: 4px; }
    input { width: 100%; padding: 14px; background: #0d1117; border: 1px solid var(--border); border-radius: 8px; color: #fff; margin: 10px 0; box-sizing: border-box; }
    select { width: 100%; padding: 14px; background: #0d1117; border: 1px solid var(--border); border-radius: 8px; color: #fff; margin: 10px 0; box-sizing: border-box; }
    .helper { font-size: 11px; color: var(--text-dim); margin-top: 6px; }
</style>
</head>
<body>
<div class="container">
    <img src="/static/logo.jpg" onerror="this.src='/static/logo.png'" class="logo-img">
    <div class="card">
        <h3>System Status</h3>
        <div class="status-indicator {{ 'active' if privacy else '' }}"><div class="dot"></div>{{ 'PRIVACY ACTIVE' if privacy else 'NORMAL MODE' }}</div>
        <form action="/mode/{{ 'normal' if privacy else 'privacy' }}" method="post"><button class="{{ 'btn-secondary' if privacy else 'btn-primary' }}">{{ 'Switch to Normal' if privacy else 'Enable Privacy' }}</button></form>
    </div>
    <div class="card">
        <h3>Live Traffic</h3>
        <div class="traffic-grid">
            <div><div style="font-size:10px;color:var(--text-dim)">DOWNLOAD</div><div class="traffic-val" id="rx">0 MB</div><div class="traffic-speed" id="s_rx">0 KB/s</div></div>
            <div><div style="font-size:10px;color:var(--text-dim)">UPLOAD</div><div class="traffic-val" id="tx">0 MB</div><div class="traffic-speed" id="s_tx">0 KB/s</div></div>
        </div>
    </div>
    <div class="card">
        <h3>Wi-Fi</h3>
        <button id="scan-btn" class="btn-secondary" onclick="scan()">Scan Networks</button>
        <div id="list" style="margin-top:10px"></div>
        <div id="connect" style="display:none; margin-top:20px; border-top:1px solid var(--border); padding-top:10px;">
            <p style="font-size:12px; color:var(--secondary)">Connecting to: <b id="ssid-name"></b></p>
            <input type="password" id="pw" placeholder="Enter Password">
            <button id="conn-btn" class="btn-primary" onclick="connect()">Connect Now</button>
        </div>
    </div>
    <div class="card">
        <h3>Anyone Circuit</h3>
        <label for="exit-country" style="font-size:12px; color:var(--text-dim)">Exit Country</label>
        <select id="exit-country">
            <option value="auto">Auto (best available)</option>
            <option value="de">Germany</option>
            <option value="nl">Netherlands</option>
            <option value="ch">Switzerland</option>
            <option value="us">United States</option>
            <option value="gb">United Kingdom</option>
            <option value="fr">France</option>
            <option value="se">Sweden</option>
            <option value="no">Norway</option>
            <option value="es">Spain</option>
        </select>
        <button id="circuit-btn" class="btn-primary" onclick="saveCircuit()">Apply Circuit Settings</button>
        <div class="helper" id="circuit-helper">Changes apply immediately in Privacy Mode.</div>
    </div>
</div>
<script>
let targetSSID = "";
const circuitConfig = {{ circuit | tojson }};
const privacyActive = {{ 'true' if privacy else 'false' }};
document.addEventListener('DOMContentLoaded', () => {
    if (circuitConfig.exit_country) {
        document.getElementById('exit-country').value = circuitConfig.exit_country;
    }
    const exitCountrySelect = document.getElementById('exit-country');
    if (!privacyActive) {
        document.getElementById('circuit-helper').innerText = "Enable Privacy Mode to apply changes.";
    }
    exitCountrySelect.addEventListener('change', () => saveCircuit());
});
async function updateTraffic(){
    try {
        const res = await fetch('/api/traffic'); const d = await res.json();
        document.getElementById('rx').innerText = (d.rx / 1048576).toFixed(1) + " MB";
        document.getElementById('tx').innerText = (d.tx / 1048576).toFixed(1) + " MB";
        document.getElementById('s_rx').innerText = d.speed_rx > 1048576 ? (d.speed_rx/1048576).toFixed(1)+" MB/s" : (d.speed_rx/1024).toFixed(1)+" KB/s";
        document.getElementById('s_tx').innerText = d.speed_tx > 1048576 ? (d.speed_tx/1048576).toFixed(1)+" MB/s" : (d.speed_tx/1024).toFixed(1)+" KB/s";
    } catch(e) {}
}
setInterval(updateTraffic, 1000);

async function scan(){
    const btn = document.getElementById('scan-btn');
    btn.disabled = true; btn.innerText = "Scanning...";
    document.getElementById('list').innerHTML = "";
    try {
        const res = await fetch('/wifi/scan'); const data = await res.json();
        let h = "";
        data.networks.forEach(n => {
            const isConn = n.connected ? '<span class="connected-label">CONNECTED</span>' : '<span>›</span>';
            h += `<div class='wifi-item' onclick="sel('${n.ssid}')"><span>${n.ssid}</span>${isConn}</div>`;
        });
        document.getElementById('list').innerHTML = h;
    } finally { btn.disabled = false; btn.innerText = "Scan Networks"; }
}

function sel(s){
    targetSSID = s;
    document.getElementById('ssid-name').innerText = s;
    document.getElementById('connect').style.display = 'block';
}

async function connect(){
    const btn = document.getElementById('conn-btn');
    const pw = document.getElementById('pw').value;
    btn.disabled = true; btn.innerText = "Connecting... Please wait";
    try {
        const res = await fetch('/wifi/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid:targetSSID,password:pw})});
        const d = await res.json();
        alert(d.status);
        if(d.status === "Connected!") { location.reload(); }
    } catch(e) { alert("Error connecting"); }
    finally { btn.disabled = false; btn.innerText = "Connect Now"; }
}

async function saveCircuit(){
    const btn = document.getElementById('circuit-btn');
    const exitCountry = document.getElementById('exit-country').value;
    if (!privacyActive) {
        alert("Enable Privacy Mode to apply circuit changes.");
        return;
    }
    btn.disabled = true; btn.innerText = "Saving...";
    try {
        const res = await fetch('/api/circuit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({exit_country: exitCountry})
        });
        const data = await res.json();
        if (!res.ok) {
            alert(data.status || "Error saving settings");
            return;
        }
        alert("Circuit settings saved");
    } catch (e) {
        alert("Error saving settings");
    } finally {
        btn.disabled = false; btn.innerText = "Apply Circuit Settings";
    }
}
</script></body></html>
"""
@app.route('/')
def index():
    p = subprocess.run("sudo iptables -t nat -L PREROUTING -n | grep 9040", shell=True, capture_output=True).returncode == 0
    return render_template_string(HTML, privacy=p, circuit=load_circuit_config())

@app.route('/api/traffic')
def traffic(): return jsonify(update_stats())

@app.route('/wifi/scan')
def w_scan():
    # Liste der SSIDs und deren Status holen
    raw = subprocess.check_output("nmcli -t -f SSID,ACTIVE dev wifi list", shell=True).decode('utf-8')
    networks = []
    seen = set()
    for line in raw.split('\n'):
        if line.strip():
            parts = line.split(':')
            ssid = parts[0]
            if ssid and ssid not in seen:
                networks.append({"ssid": ssid, "connected": parts[1] == "yes"})
                seen.add(ssid)
    return jsonify({"networks": networks})

@app.route('/wifi/connect', methods=['POST'])
def w_conn():
    d = request.json
    ssid = d.get('ssid')
    # Bestehendes Profil löschen, um Credentials-Fehler zu vermeiden
    subprocess.run(["sudo", "nmcli", "con", "delete", ssid], capture_output=True)
    res = subprocess.run(["sudo", "nmcli", "dev", "wifi", "connect", ssid, "password", d.get('password')], capture_output=True, text=True)
    return jsonify({"status": "Connected!" if res.returncode==0 else res.stderr.strip()})

@app.route('/api/circuit', methods=['GET', 'POST'])
def circuit_config():
    if request.method == 'GET':
        return jsonify(load_circuit_config())
    data = request.json or {}
    exit_country = (data.get("exit_country") or "auto").lower()
    allowed = {"auto", "de", "nl", "ch", "us", "gb", "fr", "se", "no", "es"}
    if exit_country not in allowed:
        return jsonify({"status": "Invalid country code"}), 400
    cfg = load_circuit_config()
    cfg["exit_country"] = exit_country
    save_circuit_config(cfg)
    return jsonify({"status": "Saved", "config": cfg})

@app.route('/mode/privacy', methods=['POST'])
def mode_p():
    subprocess.run("sudo /usr/local/bin/mode_privacy.sh", shell=True)
    return redirect('/')

@app.route('/mode/normal', methods=['POST'])
def mode_n():
    subprocess.run("sudo /usr/local/bin/mode_normal.sh", shell=True)
    return redirect('/')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80, threaded=True)
