#!/bin/bash
# ============================================================================
# Anyone Privacy Stick – Installer
# Turns a fresh Raspberry Pi OS Lite 64-bit into an Anyone Privacy Stick
# Target hardware: Raspberry Pi Zero 2 W
# Repository: https://github.com/robihalle/Anyone-Stick
# ============================================================================
set -e

ANON_VERSION="v0.4.9.11"
ANON_URL="https://github.com/anyone-protocol/ator-protocol/releases/download/${ANON_VERSION}"
PORTAL_DIR="/home/pi/portal"
SCRIPT_DIR="/usr/local/bin"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✔]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✘]${NC} $1"; exit 1; }

# ── Root check ──────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Please run as root: sudo bash install.sh"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Anyone Privacy Stick – Installer                  ║"
echo "║           Raspberry Pi Zero 2 W                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Update system & install packages ─────────────────────────────────
log "Updating system..."
apt-get update -qq
apt-get upgrade -y -qq

log "Installing packages..."
apt-get install -y -qq \
    python3-flask \
    dnsmasq \
    network-manager \
    iptables-persistent \
    curl \
    unzip \
    jq \
    libcap2

# ── 2. Disable unnecessary services ────────────────────────────────────
log "Disabling unnecessary services..."
systemctl disable --now bluetooth 2>/dev/null || true
systemctl disable --now avahi-daemon 2>/dev/null || true
systemctl disable --now triggerhappy 2>/dev/null || true
systemctl disable --now apt-daily.timer 2>/dev/null || true
systemctl disable --now apt-daily-upgrade.timer 2>/dev/null || true

# ── 3. Download and install anon binary ─────────────────────────────────
log "Downloading anon binary ${ANON_VERSION}..."

ANON_INSTALLED=false
for SUFFIX in "anon-linux-aarch64-${ANON_VERSION}.tar.gz" "anon_linux_aarch64.tar.gz" "anon-linux-arm64-${ANON_VERSION}.tar.gz"; do
    if curl -fsSL -o /tmp/anon.tar.gz "${ANON_URL}/${SUFFIX}" 2>/dev/null; then
        log "Download successful: ${SUFFIX}"
        ANON_INSTALLED=true
        break
    fi
done

# Fallback: .deb package
if [ "$ANON_INSTALLED" = false ]; then
    for SUFFIX in "anon_aarch64.deb" "anon-${ANON_VERSION}_arm64.deb"; do
        if curl -fsSL -o /tmp/anon.deb "${ANON_URL}/${SUFFIX}" 2>/dev/null; then
            log "DEB package found: ${SUFFIX}"
            dpkg -i /tmp/anon.deb || apt-get install -f -y
            rm -f /tmp/anon.deb
            ANON_INSTALLED=true
            break
        fi
    done
fi

# Extract tar.gz
if [ -f /tmp/anon.tar.gz ]; then
    cd /tmp
    tar xzf anon.tar.gz
    ANON_BIN=$(find /tmp -name "anon" -type f -executable 2>/dev/null | head -1)
    if [ -z "$ANON_BIN" ]; then
        ANON_BIN=$(find /tmp -name "anon" -type f 2>/dev/null | head -1)
    fi
    if [ -n "$ANON_BIN" ]; then
        cp "$ANON_BIN" /usr/local/bin/anon
        chmod +x /usr/local/bin/anon
        log "Anon installed to /usr/local/bin/anon"
    else
        warn "Anon binary not found in archive – please install manually!"
    fi
    rm -f /tmp/anon.tar.gz
fi

if [ "$ANON_INSTALLED" = false ]; then
    warn "Anon could not be downloaded automatically."
    warn "Please install manually from https://github.com/anyone-protocol/ator-protocol/releases"
    warn "Copy the binary to /usr/local/bin/anon"
fi

# ── 4. Ensure user 'pi' exists ─────────────────────────────────────────
if ! id pi &>/dev/null; then
    useradd -m -s /bin/bash pi
    log "User 'pi' created"
fi

# ── 5. Create anon data directory ──────────────────────────────────────
mkdir -p /root/.anon
log "Anon data directory created"

# ── 6. Write anonrc ────────────────────────────────────────────────────
cat > /etc/anonrc << 'EOF'
SocksPort 9050
ControlPort 9051
DNSPort 0.0.0.0:9053
TransPort 0.0.0.0:9040
User root
DataDirectory /root/.anon
AgreeToTerms 1
AutomapHostsOnResolve 1
VirtualAddrNetworkIPv4 10.192.0.0/10
EOF
log "anonrc written → /etc/anonrc"

# ── 7. Boot config (config.txt) ────────────────────────────────────────
BOOT_DIR="/boot/firmware"
[ ! -d "$BOOT_DIR" ] && BOOT_DIR="/boot"

cat > ${BOOT_DIR}/config.txt << 'EOF'
# Anyone Privacy Stick – config.txt (optimized for headless USB operation)
dtparam=audio=off
disable_fw_kms_setup=1
arm_64bit=1
disable_overscan=1
arm_boost=1
disable_splash=1
boot_delay=0
gpu_mem=16

[cm4]
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[all]
enable_uart=1
dtoverlay=dwc2
EOF
log "config.txt written → ${BOOT_DIR}/config.txt"

# ── 8. cmdline.txt ─────────────────────────────────────────────────────
ROOT_PARTUUID=$(findmnt / -no PARTUUID 2>/dev/null || echo "FIXME")
cat > ${BOOT_DIR}/cmdline.txt << EOF
console=serial0,115200 console=tty1 root=PARTUUID=${ROOT_PARTUUID} rootfstype=ext4 fsck.repair=yes rootwait quiet cfg80211.ieee80211_regdom=DE modules-load=dwc2,libcomposite ipv6.disable=1
EOF
log "cmdline.txt written → ${BOOT_DIR}/cmdline.txt (PARTUUID=${ROOT_PARTUUID})"

# ── 9. Enable IP forwarding ────────────────────────────────────────────
cat > /etc/sysctl.d/99-anyone-stick.conf << 'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.disable_ipv6=1
EOF
sysctl --system -q
log "IP forwarding enabled"

# ── 10. USB gadget setup script ─────────────────────────────────────────
cat > ${SCRIPT_DIR}/usb_gadget_setup.sh << 'SCRIPT'
#!/bin/bash
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
modprobe libcomposite

GADGET_DIR="/sys/kernel/config/usb_gadget/g1"

if [ -d "$GADGET_DIR" ]; then
    echo "" > $GADGET_DIR/UDC 2>/dev/null
    rm -rf $GADGET_DIR
fi

mkdir -p $GADGET_DIR && cd $GADGET_DIR

echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "ANYONE0002"          > strings/0x409/serialnumber
echo "Anyone Foundation"   > strings/0x409/manufacturer
echo "Privacy Stick NCM"   > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "NCM Network" > configs/c.1/strings/0x409/configuration
echo 0x80          > configs/c.1/bmAttributes
echo 250           > configs/c.1/MaxPower

mkdir -p functions/ncm.usb0
ln -s functions/ncm.usb0 configs/c.1/

ls /sys/class/udc | head -n 1 > UDC
SCRIPT
chmod +x ${SCRIPT_DIR}/usb_gadget_setup.sh
log "usb_gadget_setup.sh installed"

# ── 11. Mode scripts ───────────────────────────────────────────────────
cat > ${SCRIPT_DIR}/mode_privacy.sh << 'SCRIPT'
#!/bin/bash
# 1. Firewall reset
iptables -F
iptables -t nat -F
iptables -t mangle -F

# 2. Forwarding & disable IPv6
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.disable_ipv6=1

# 3. Local exceptions (portal must always be reachable)
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 80 -j RETURN
iptables -t nat -A PREROUTING -i usb0 -d 192.168.7.1 -j RETURN

# 4. DNS redirect: port 53 → 9053 (Anyone DNS)
iptables -t nat -A PREROUTING -i usb0 -p udp --dport 53 -j REDIRECT --to-ports 9053
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 53 -j REDIRECT --to-ports 9053

# 5. Transparent proxy: all other TCP → 9040
iptables -t nat -A PREROUTING -i usb0 -p tcp --syn -j REDIRECT --to-ports 9040

# 6. Routing & MTU
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# 7. LED: heartbeat = privacy mode
echo heartbeat | sudo tee /sys/class/leds/default-on/trigger
SCRIPT
chmod +x ${SCRIPT_DIR}/mode_privacy.sh

cat > ${SCRIPT_DIR}/mode_normal.sh << 'SCRIPT'
#!/bin/bash
iptables -F
iptables -t nat -F
iptables -t mangle -F

# Simple NAT without tunnel
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Reset exit country selection
sed -i '/^ExitNodes/d' /etc/anonrc
sed -i '/^StrictNodes/d' /etc/anonrc

# Reload anon config (if process is running)
pkill -SIGHUP -x anon 2>/dev/null || true

# LED: steady on = normal mode
echo default-on | sudo tee /sys/class/leds/default-on/trigger
SCRIPT
chmod +x ${SCRIPT_DIR}/mode_normal.sh
log "mode_privacy.sh & mode_normal.sh installed"

# ── 12. Startup script ─────────────────────────────────────────────────
cat > ${SCRIPT_DIR}/start_anyone_stack.sh << 'SCRIPT'
#!/bin/bash
echo timer | sudo tee /sys/class/leds/default-on/trigger

# USB gadget is already active via usb-gadget.service.
# Wait until usb0 is available (max 5s instead of fixed sleep).
for i in $(seq 1 10); do
    ip link show usb0 &>/dev/null && break
    sleep 0.5
done

# Activate Wi-Fi (background, non-blocking)
nmcli con up "Stick-Gateway" &

# Start DHCP immediately so the host gets an IP
systemctl restart dnsmasq

# Start portal immediately – the host can reach the dashboard
# even before Wi-Fi and anon are ready.
python3 /home/pi/portal/app.py &

# Actively wait for Wi-Fi connection (max 30s instead of fixed sleep)
for i in $(seq 1 30); do
    nmcli -t -f STATE general 2>/dev/null | grep -q "connected" && break
    sleep 1
done

# Start anon stack
/usr/local/bin/anon -f /etc/anonrc
SCRIPT
chmod +x ${SCRIPT_DIR}/start_anyone_stack.sh
log "start_anyone_stack.sh installed"

# ── 13. Configure dnsmasq ──────────────────────────────────────────────
cat > /etc/dnsmasq.d/usb0.conf << 'EOF'
interface=usb0
bind-interfaces
dhcp-range=192.168.7.2,192.168.7.20,255.255.255.0,24h
dhcp-option=3,192.168.7.1
dhcp-option=6,192.168.7.1
EOF
log "dnsmasq configured for usb0 (192.168.7.0/24)"

# ── 14. Static IP for usb0 ─────────────────────────────────────────────
mkdir -p /etc/network/interfaces.d
cat > /etc/network/interfaces.d/usb0 << 'EOF'
auto usb0
iface usb0 inet static
    address 192.168.7.1
    netmask 255.255.255.0
EOF
log "Static IP 192.168.7.1 configured for usb0"

# ── 15. NetworkManager: mark usb0 as unmanaged ─────────────────────────
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/unmanaged-usb0.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:usb0
EOF
log "NetworkManager: usb0 marked as unmanaged"

# ── 16. Web portal (Flask app) ─────────────────────────────────────────
mkdir -p ${PORTAL_DIR}/static
cat > ${PORTAL_DIR}/app.py << 'PYEOF'
from flask import Flask, request, jsonify, render_template_string, redirect
import subprocess, time, os, signal, re

app = Flask(__name__)
stats = {"rx": 0, "tx": 0, "time": 0, "speed_rx": 0, "speed_tx": 0}
ANONRC_PATH = "/etc/anonrc"

EXIT_COUNTRIES = [
    ("auto", "\U0001f30d Automatic (Best Available)"),
    ("at", "\U0001f1e6\U0001f1f9 Austria"),
    ("be", "\U0001f1e7\U0001f1ea Belgium"),
    ("bg", "\U0001f1e7\U0001f1ec Bulgaria"),
    ("br", "\U0001f1e7\U0001f1f7 Brazil"),
    ("ca", "\U0001f1e8\U0001f1e6 Canada"),
    ("ch", "\U0001f1e8\U0001f1ed Switzerland"),
    ("cz", "\U0001f1e8\U0001f1ff Czech Republic"),
    ("de", "\U0001f1e9\U0001f1ea Germany"),
    ("dk", "\U0001f1e9\U0001f1f0 Denmark"),
    ("es", "\U0001f1ea\U0001f1f8 Spain"),
    ("fi", "\U0001f1eb\U0001f1ee Finland"),
    ("fr", "\U0001f1eb\U0001f1f7 France"),
    ("gb", "\U0001f1ec\U0001f1e7 United Kingdom"),
    ("hr", "\U0001f1ed\U0001f1f7 Croatia"),
    ("hu", "\U0001f1ed\U0001f1fa Hungary"),
    ("ie", "\U0001f1ee\U0001f1ea Ireland"),
    ("in", "\U0001f1ee\U0001f1f3 India"),
    ("is", "\U0001f1ee\U0001f1f8 Iceland"),
    ("it", "\U0001f1ee\U0001f1f9 Italy"),
    ("jp", "\U0001f1ef\U0001f1f5 Japan"),
    ("kr", "\U0001f1f0\U0001f1f7 South Korea"),
    ("lu", "\U0001f1f1\U0001f1fa Luxembourg"),
    ("md", "\U0001f1f2\U0001f1e9 Moldova"),
    ("nl", "\U0001f1f3\U0001f1f1 Netherlands"),
    ("no", "\U0001f1f3\U0001f1f4 Norway"),
    ("nz", "\U0001f1f3\U0001f1ff New Zealand"),
    ("pl", "\U0001f1f5\U0001f1f1 Poland"),
    ("pt", "\U0001f1f5\U0001f1f9 Portugal"),
    ("ro", "\U0001f1f7\U0001f1f4 Romania"),
    ("rs", "\U0001f1f7\U0001f1f8 Serbia"),
    ("se", "\U0001f1f8\U0001f1ea Sweden"),
    ("sg", "\U0001f1f8\U0001f1ec Singapore"),
    ("sk", "\U0001f1f8\U0001f1f0 Slovakia"),
    ("ua", "\U0001f1fa\U0001f1e6 Ukraine"),
    ("us", "\U0001f1fa\U0001f1f8 United States"),
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
    try:
        with open(ANONRC_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ExitNodes"):
                    m = re.search(r'\{(\w+)\}', line)
                    if m:
                        return m.group(1).lower()
    except FileNotFoundError:
        pass
    return "auto"


def set_exit_country(country_code):
    try:
        with open(ANONRC_PATH, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ExitNodes") or stripped.startswith("StrictNodes"):
            continue
        new_lines.append(line)

    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    if country_code != "auto":
        new_lines.append(f"ExitNodes {{{country_code}}}\n")
        new_lines.append("StrictNodes 1\n")

    with open(ANONRC_PATH, "w") as f:
        f.writelines(new_lines)

    try:
        pid = subprocess.check_output("pgrep -x anon", shell=True).decode().strip().split("\n")[0]
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
    :root { --primary: #0280AF; --secondary: #03BDC5; --gradient: linear-gradient(90deg, #0280AF 0%%, #03BDC5 100%%); --bg-color: #0b1116; --card-bg: #151b23; --text-main: #FFFFFF; --text-dim: #8b949e; --border: #30363d; }
    body { font-family: "Mona Sans", sans-serif; background-color: var(--bg-color); color: var(--text-main); margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; }
    .container { width: 100%%; max-width: 420px; }
    .logo-img { max-width: 180px; height: auto; display: block; margin: 0 auto 20px auto; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 20px; }
    h3 { font-size: 11px; text-transform: uppercase; color: var(--secondary); margin: 0 0 15px 0; font-weight: 800; }
    .status-indicator { display: flex; align-items: center; justify-content: center; padding: 15px; border-radius: 8px; font-weight: 600; margin-bottom: 20px; background: rgba(255,255,255,0.03); }
    .dot { height: 8px; width: 8px; border-radius: 50%%; margin-right: 12px; background: #555; }
    .active .dot { background: var(--secondary); box-shadow: 0 0 12px var(--secondary); }
    .traffic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
    .traffic-val { font-size: 18px; font-weight: 700; color: #fff; }
    .traffic-speed { font-size: 11px; color: var(--secondary); font-weight: 600; }
    button { width: 100%%; padding: 16px; border: none; border-radius: 8px; font-size: 14px; font-weight: 700; cursor: pointer; transition: 0.2s; font-family: inherit; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary { background: var(--gradient); color: #fff; }
    .btn-secondary { background: #21262d; color: #fff; border: 1px solid var(--border); }
    .wifi-item { padding: 12px; border-bottom: 1px solid var(--border); cursor: pointer; display: flex; justify-content: space-between; font-size: 14px; }
    .connected-label { color: var(--secondary); font-weight: 800; font-size: 10px; border: 1px solid var(--secondary); padding: 2px 6px; border-radius: 4px; }
    input, select { width: 100%%; padding: 14px; background: #0d1117; border: 1px solid var(--border); border-radius: 8px; color: #fff; margin: 10px 0; box-sizing: border-box; font-family: inherit; font-size: 14px; }
    select { appearance: none; -webkit-appearance: none; background-image: url("data:image/svg+xml,%%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%%3E%%3Cpath fill='%%238b949e' d='M6 8L1 3h10z'/%3E%%3C/svg%%3E"); background-repeat: no-repeat; background-position: right 14px center; cursor: pointer; }
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
            {%% for code, name in countries %%}
            <option value="{{ code }}" {{ 'selected' if code == exit_country else '' }}>{{ name }}</option>
            {%% endfor %%}
        </select>
        <p class="helper-text">{{ 'Select a country to route your traffic through.' if privacy else 'Enable Privacy Mode first to apply circuit changes.' }}</p>
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

document.getElementById('exit-country').addEventListener('change', async function() {
    if (!privacyActive) { alert("Enable Privacy Mode first."); this.value = "{{ exit_country }}"; return; }
    const cc = this.value, status = document.getElementById('circuit-status');
    status.innerText = "⏳ Applying...";
    try {
        const res = await fetch('/api/circuit', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({exit_country: cc}) });
        const data = await res.json();
        if (res.ok) { status.className = cc !== 'auto' ? 'circuit-status active' : 'circuit-status inactive'; status.innerText = cc !== 'auto' ? '🔒 Exit: ' + cc.toUpperCase() : '🌍 Automatic exit selection'; }
        else { alert(data.status || "Error"); }
    } catch(e) { alert("Error connecting to server"); }
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
            h += "<div class='wifi-item' onclick=\\"sel('"+n.ssid+"')\\"><span>"+n.ssid+"</span>"+isConn+"</div>";
        });
        document.getElementById('list').innerHTML = h;
    } finally { btn.disabled = false; btn.innerText = "Scan Networks"; }
}

function sel(s){ targetSSID = s; document.getElementById('ssid-name').innerText = s; document.getElementById('connect').style.display = 'block'; }

async function connect(){
    const btn = document.getElementById('conn-btn'), pw = document.getElementById('pw').value;
    btn.disabled = true; btn.innerText = "Connecting...";
    try {
        const res = await fetch('/wifi/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ssid:targetSSID,password:pw})});
        const d = await res.json(); alert(d.status);
        if(d.status === "Connected!") { location.reload(); }
    } catch(e) { alert("Error"); }
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
    valid_codes = [c[0] for c in EXIT_COUNTRIES]
    if cc not in valid_codes:
        return jsonify({"status": f"Invalid country code: {cc}"}), 400
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
    networks, seen = [], set()
    for line in raw.split('\n'):
        if line.strip():
            parts = line.split(':', 1)
            ssid = parts[0]
            if ssid and ssid not in seen:
                networks.append({"ssid": ssid, "connected": parts[1].strip() == "yes" if len(parts) > 1 else False})
                seen.add(ssid)
    return jsonify({"networks": networks})

@app.route('/wifi/connect', methods=['POST'])
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
PYEOF
chown -R pi:pi ${PORTAL_DIR}
log "Web portal installed → ${PORTAL_DIR}/app.py"

# ── 17. Systemd services ───────────────────────────────────────────────
cat > /etc/systemd/system/usb-gadget.service << 'EOF'
[Unit]
Description=USB Gadget Setup
DefaultDependencies=no
After=sysinit.target
Before=network.target anyone-stick.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/usb_gadget_setup.sh
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
EOF

cat > /etc/systemd/system/anyone-stick.service << 'EOF'
[Unit]
Description=Anyone Stick Master
After=usb-gadget.service
Requires=usb-gadget.service

[Service]
ExecStart=/usr/local/bin/start_anyone_stack.sh
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable usb-gadget.service
systemctl enable anyone-stick.service
systemctl enable dnsmasq
log "Systemd services enabled"

# ── 18. Register kernel modules ─────────────────────────────────────────
echo "dwc2" >> /etc/modules
echo "libcomposite" >> /etc/modules
sort -u /etc/modules -o /etc/modules
log "Kernel modules registered (dwc2, libcomposite)"

# ── 19. Configure sudoers for pi ────────────────────────────────────────
cat > /etc/sudoers.d/anyone-stick << 'EOF'
pi ALL=(ALL) NOPASSWD: /usr/local/bin/mode_privacy.sh, /usr/local/bin/mode_normal.sh, /usr/sbin/iptables, /usr/bin/tee /sys/class/leds/*
EOF
chmod 440 /etc/sudoers.d/anyone-stick
log "sudoers configured"

# ── 20. Done ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✅ Installation complete!                                  ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                            ║"
echo "║  Next steps:                                               ║"
echo "║                                                            ║"
echo "║  1. Set up Wi-Fi (once):                                   ║"
echo "║     nmcli dev wifi connect 'SSID' password 'PASSWORD'      ║"
echo "║     nmcli con mod 'SSID' connection.id 'Stick-Gateway'     ║"
echo "║                                                            ║"
echo "║  2. Optional: add logo for portal:                         ║"
echo "║     cp logo.jpg /home/pi/portal/static/                    ║"
echo "║                                                            ║"
echo "║  3. Reboot:                                                ║"
echo "║     sudo reboot                                            ║"
echo "║                                                            ║"
echo "║  After reboot, plug the stick into your PC via USB         ║"
echo "║  and open → http://192.168.7.1                             ║"
echo "║                                                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
