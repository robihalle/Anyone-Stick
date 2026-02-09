from flask import Flask, request, jsonify, render_template_string, redirect
import subprocess, time, os, signal, re

app = Flask(__name__)
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
ANONRC_PATH = "/etc/anonrc"

# Verfügbare Exit-Länder (ISO 3166-1 alpha-2)
EXIT_COUNTRIES = [
    ("auto", "🌍 Automatic (Best Available)"),
    ("at", "🇦🇹 Austria"),
    ("be", "🇧🇪 Belgium"),
    ("bg", "🇧🇬 Bulgaria"),
    ("br", "🇧🇷 Brazil"),
    ("ca", "🇨🇦 Canada"),
    ("ch", "🇨🇭 Switzerland"),
    ("cz", "🇨🇿 Czech Republic"),
    ("de", "🇩🇪 Germany"),
    ("dk", "🇩🇰 Denmark"),
    ("es", "🇪🇸 Spain"),
    ("fi", "🇫🇮 Finland"),
    ("fr", "🇫🇷 France"),
    ("gb", "🇬🇧 United Kingdom"),
    ("hr", "🇭🇷 Croatia"),
    ("hu", "🇭🇺 Hungary"),
    ("ie", "🇮🇪 Ireland"),
    ("in", "🇮🇳 India"),
    ("is", "🇮🇸 Iceland"),
    ("it", "🇮🇹 Italy"),
    ("jp", "🇯🇵 Japan"),
    ("kr", "🇰🇷 South Korea"),
    ("lu", "🇱🇺 Luxembourg"),
    ("md", "🇲🇩 Moldova"),
    ("nl", "🇳🇱 Netherlands"),
    ("no", "🇳🇴 Norway"),
    ("nz", "🇳🇿 New Zealand"),
    ("pl", "🇵🇱 Poland"),
    ("pt", "🇵🇹 Portugal"),
    ("ro", "🇷🇴 Romania"),
    ("rs", "🇷🇸 Serbia"),
    ("se", "🇸🇪 Sweden"),
    ("sg", "🇸🇬 Singapore"),
    ("sk", "🇸🇰 Slovakia"),
    ("ua", "🇺🇦 Ukraine"),
    ("us", "🇺🇸 United States"),
]


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
    except:
        pass
    return stats


def get_current_exit_country():
    """Liest den aktuell konfigurierten ExitNodes-Ländercode aus der anonrc."""
    try:
        with open(ANONRC_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ExitNodes"):
                    # Format: ExitNodes {de}
                    m = re.search(r'\{(\w+)\}', line)
                    if m:
                        return m.group(1).lower()
    except FileNotFoundError:
        pass
    return "auto"


def set_exit_country(country_code):
    """
    Schreibt ExitNodes + StrictNodes in die anonrc und sendet SIGHUP an anon.
    country_code: ISO 3166 2-letter code oder 'auto' für keine Einschränkung.
    """
    try:
        with open(ANONRC_PATH, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    # Bestehende ExitNodes / StrictNodes Zeilen entfernen
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ExitNodes") or stripped.startswith("StrictNodes"):
            continue
        new_lines.append(line)

    # Trailing newline sicherstellen
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    # Neue Zeilen anfügen (nur wenn nicht "auto")
    if country_code != "auto":
        new_lines.append(f"ExitNodes {{{country_code}}}\n")
        new_lines.append("StrictNodes 1\n")

    with open(ANONRC_PATH, "w") as f:
        f.writelines(new_lines)

    # SIGHUP an anon-Prozess senden → Config wird live nachgeladen
    try:
        pid = subprocess.check_output("pgrep -f '/usr/local/bin/anon'", shell=True).decode().strip().split("\n")[0]
        os.kill(int(pid), signal.SIGHUP)
    except Exception:
        pass

    return True


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
    input, select { width: 100%; padding: 14px; background: #0d1117; border: 1px solid var(--border); border-radius: 8px; color: #fff; margin: 10px 0; box-sizing: border-box; font-family: inherit; font-size: 14px; }
    select { appearance: none; -webkit-appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%238b949e' d='M6 8L1 3h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 14px center; cursor: pointer; }
    select option { background: #0d1117; color: #fff; }
    .helper-text { font-size: 11px; color: var(--text-dim); margin-top: 4px; }
    .circuit-status { display: flex; align-items: center; gap: 8px; margin-bottom: 15px; padding: 10px; border-radius: 6px; font-size: 12px; font-weight: 600; }
    .circuit-status.active { background: rgba(3,189,197,0.08); color: var(--secondary); }
    .circuit-status.inactive { background: rgba(255,255,255,0.03); color: var(--text-dim); }
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
        <h3>Anyone Circuit</h3>
        <div class="circuit-status {{ 'active' if privacy and exit_country != 'auto' else 'inactive' }}" id="circuit-status">
            {{ '🔒 Exit: ' + exit_country.upper() if exit_country != 'auto' else '🌍 Automatic exit selection' }}
        </div>
        <label style="font-size:12px; color:var(--text-dim);">Exit Node Country</label>
        <select id="exit-country">
            {% for code, name in countries %}
            <option value="{{ code }}" {{ 'selected' if code == exit_country else '' }}>{{ name }}</option>
            {% endfor %}
        </select>
        <p class="helper-text" id="circuit-helper">
            {{ 'Select a country to route your traffic through.' if privacy else 'Enable Privacy Mode first to apply circuit changes.' }}
        </p>
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
</div>
<script>
let targetSSID = "";
const privacyActive = {{ 'true' if privacy else 'false' }};

// Exit-Country: automatisch speichern bei Änderung
document.getElementById('exit-country').addEventListener('change', async function() {
    if (!privacyActive) {
        alert("Enable Privacy Mode first to apply circuit changes.");
        this.value = "{{ exit_country }}";
        return;
    }
    const cc = this.value;
    const status = document.getElementById('circuit-status');
    status.innerText = "⏳ Applying...";
    try {
        const res = await fetch('/api/circuit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({exit_country: cc})
        });
        const data = await res.json();
        if (res.ok) {
            status.className = cc !== 'auto' ? 'circuit-status active' : 'circuit-status inactive';
            status.innerText = cc !== 'auto' ? '🔒 Exit: ' + cc.toUpperCase() : '🌍 Automatic exit selection';
        } else {
            alert(data.status || "Error applying circuit settings");
        }
    } catch(e) {
        alert("Error connecting to server");
    }
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
</script></body></html>
"""


@app.route('/')
def index():
    p = subprocess.run("sudo iptables -t nat -L PREROUTING -n | grep 9040",
                       shell=True, capture_output=True).returncode == 0
    ec = get_current_exit_country()
    return render_template_string(HTML, privacy=p, exit_country=ec, countries=EXIT_COUNTRIES)


@app.route('/api/traffic')
def traffic():
    return jsonify(update_stats())


@app.route('/api/circuit', methods=['GET'])
def get_circuit():
    return jsonify({"exit_country": get_current_exit_country()})


@app.route('/api/circuit', methods=['POST'])
def post_circuit():
    data = request.get_json()
    if not data or "exit_country" not in data:
        return jsonify({"status": "Missing exit_country"}), 400

    cc = data["exit_country"].lower().strip()

    # Validierung: nur bekannte Codes oder "auto"
    valid_codes = [c[0] for c in EXIT_COUNTRIES]
    if cc not in valid_codes:
        return jsonify({"status": f"Invalid country code: {cc}"}), 400

    # Privacy-Mode prüfen
    p = subprocess.run("sudo iptables -t nat -L PREROUTING -n | grep 9040",
                       shell=True, capture_output=True).returncode == 0
    if not p:
        return jsonify({"status": "Privacy Mode must be active"}), 400

    set_exit_country(cc)
    return jsonify({"status": "ok", "exit_country": cc})


@app.route('/wifi/scan')
def w_scan():
    raw = subprocess.check_output("nmcli -t -f SSID,ACTIVE dev wifi list",
                                  shell=True).decode('utf-8')
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
    data = request.get_json()
    ssid = data.get("ssid", "")
    pw = data.get("password", "")
    try:
        subprocess.run(f'nmcli dev wifi connect "{ssid}" password "{pw}"',
                       shell=True, check=True, capture_output=True, timeout=30)
        return jsonify({"status": "Connected!"})
    except subprocess.CalledProcessError:
        return jsonify({"status": "Connection failed"})
    except subprocess.TimeoutExpired:
        return jsonify({"status": "Timeout"})


@app.route('/mode/privacy', methods=['POST'])
def mode_privacy():
    subprocess.run("sudo /usr/local/bin/mode_privacy.sh", shell=True)
    return redirect('/')


@app.route('/mode/normal', methods=['POST'])
def mode_normal():
    subprocess.run("sudo /usr/local/bin/mode_normal.sh", shell=True)
    return redirect('/')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
