#!/bin/bash
# ============================================================================
# Anyone Privacy Stick Installer
# Verwandelt ein frisches Raspberry Pi OS Lite 64-bit in einen Anyone Stick
# Ziel-Hardware: Raspberry Pi Zero 2 W
# ============================================================================
set -e

ANON_VERSION="v0.4.9.11"
ANON_URL="https://github.com/anyone-protocol/ator-protocol/releases/download/${ANON_VERSION}"
PORTAL_DIR="/home/pi/portal"
SCRIPT_DIR="/usr/local/bin"

# Farben
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Root-Check
[[ $EUID -ne 0 ]] && err "Bitte als root ausführen: sudo bash install.sh"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Anyone Privacy Stick Installer     ║"
echo "║   Raspberry Pi Zero 2 W              ║"
echo "╚══════════════════════════════════════╝"
echo ""

# 1. System aktualisieren & Pakete installieren ─────────────────────────────
log "System aktualisieren..."
apt-get update -qq
apt-get upgrade -y -qq

log "Pakete installieren..."
apt-get install -y -qq \
    python3-flask \
    python3-pip \
    dnsmasq \
    network-manager \
    iptables-persistent \
    curl \
    unzip \
    jq \
    libcap2

# Python-Pakete für SSE/Threading-Support
pip3 install --break-system-packages gunicorn 2>/dev/null || pip3 install gunicorn 2>/dev/null || true

# 2. Unnötige Services deaktivieren ─────────────────────────────────────────
log "Unnötige Services deaktivieren..."
systemctl disable --now bluetooth 2>/dev/null || true
systemctl disable --now avahi-daemon 2>/dev/null || true
systemctl disable --now triggerhappy 2>/dev/null || true
systemctl disable --now apt-daily.timer 2>/dev/null || true
systemctl disable --now apt-daily-upgrade.timer 2>/dev/null || true

# 3. Anon-Binary herunterladen und installieren ─────────────────────────────
log "Anon-Binary ${ANON_VERSION} herunterladen..."

ANON_INSTALLED=false
for SUFFIX in "anon-linux-aarch64-${ANON_VERSION}.tar.gz" "anon_linux_aarch64.tar.gz" "anon-linux-arm64-${ANON_VERSION}.tar.gz"; do
    if curl -fsSL -o /tmp/anon.tar.gz "${ANON_URL}/${SUFFIX}" 2>/dev/null; then
        log "Download erfolgreich: ${SUFFIX}"
        ANON_INSTALLED=true
        break
    fi
done

# Fallback: .deb-Paket
if [ "$ANON_INSTALLED" = false ]; then
    for SUFFIX in "anon_aarch64.deb" "anon-${ANON_VERSION}_arm64.deb"; do
        if curl -fsSL -o /tmp/anon.deb "${ANON_URL}/${SUFFIX}" 2>/dev/null; then
            log "DEB-Paket gefunden: ${SUFFIX}"
            dpkg -i /tmp/anon.deb || apt-get install -f -y
            rm -f /tmp/anon.deb
            ANON_INSTALLED=true
            break
        fi
    done
fi

# tar.gz entpacken
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
        log "Anon installiert nach /usr/local/bin/anon"
    else
        warn "Anon-Binary nicht im Archiv gefunden — bitte manuell installieren!"
    fi
    rm -f /tmp/anon.tar.gz
fi

if [ "$ANON_INSTALLED" = false ]; then
    warn "Anon konnte nicht automatisch heruntergeladen werden."
    warn "Bitte manuell von https://github.com/anyone-protocol/ator-protocol/releases installieren."
    warn "Binary muss nach /usr/local/bin/anon kopiert werden."
fi

# 4. User 'pi' sicherstellen ────────────────────────────────────────────────
if ! id pi &>/dev/null; then
    useradd -m -s /bin/bash pi
    log "User 'pi' erstellt"
fi

# 5. Anon DataDirectory erstellen ───────────────────────────────────────────
mkdir -p /root/.anon
log "Anon DataDirectory erstellt"

# 6. anonrc schreiben ──────────────────────────────────────────────────────
cat > /etc/anonrc << 'EOF'
SocksPort 9050
ControlPort 9051
CookieAuthentication 1
CookieAuthFile /root/.anon/control_auth_cookie
DNSPort 0.0.0.0:9053
TransPort 0.0.0.0:9040
User root
DataDirectory /root/.anon
AgreeToTerms 1
AutomapHostsOnResolve 1
VirtualAddrNetworkIPv4 10.192.0.0/10
Log notice file /var/log/anon/notices.log

# Performance tuning for Pi Zero 2W
MaxMemInQueues 50 MB
NumCPUs 1
CircuitBuildTimeout 30
LearnCircuitBuildTimeout 1
CircuitStreamTimeout 20
MaxClientCircuitsPending 16
EOF
mkdir -p /var/log/anon
log "anonrc written → /etc/anonrc"

# 7. Boot-Config (config.txt) ──────────────────────────────────────────────
BOOT_DIR="/boot/firmware"
[ ! -d "$BOOT_DIR" ] && BOOT_DIR="/boot"

cat > ${BOOT_DIR}/config.txt << 'EOF'
# Anyone Privacy Stick config.txt (optimiert für headless USB-Betrieb)
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
log "config.txt geschrieben → ${BOOT_DIR}/config.txt"

# 8. cmdline.txt ───────────────────────────────────────────────────────────
ROOT_PARTUUID=$(findmnt / -no PARTUUID 2>/dev/null || echo "FIXME")
cat > ${BOOT_DIR}/cmdline.txt << EOF
console=serial0,115200 console=tty1 root=PARTUUID=${ROOT_PARTUUID} rootfstype=ext4 fsck.repair=yes rootwait quiet cfg80211.ieee80211_regdom=DE modules-load=dwc2,libcomposite ipv6.disable=1
EOF
log "cmdline.txt geschrieben → ${BOOT_DIR}/cmdline.txt (PARTUUID=${ROOT_PARTUUID})"

# 9. IP-Forwarding aktivieren ──────────────────────────────────────────────
cat > /etc/sysctl.d/99-anyone-stick.conf << 'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.disable_ipv6=1
EOF
sysctl --system -q
log "IP-Forwarding aktiviert"

# 10. USB Gadget Setup Script ──────────────────────────────────────────────
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
echo "ANYONE0002" > strings/0x409/serialnumber
echo "Anyone Foundation" > strings/0x409/manufacturer
echo "Privacy Stick NCM" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "NCM Network" > configs/c.1/strings/0x409/configuration
echo 0x80 > configs/c.1/bmAttributes
echo 250 > configs/c.1/MaxPower

mkdir -p functions/ncm.usb0
ln -s functions/ncm.usb0 configs/c.1/

ls /sys/class/udc | head -n 1 > UDC
SCRIPT
chmod +x ${SCRIPT_DIR}/usb_gadget_setup.sh
log "usb_gadget_setup.sh installiert"

# 11. Mode-Scripts ─────────────────────────────────────────────────────────
cat > ${SCRIPT_DIR}/mode_privacy.sh << 'SCRIPT'
#!/bin/bash
# Anyone Privacy Stick — Privacy Mode (Kill Switch enabled)

# 1. Firewall Reset
iptables -F
iptables -t nat -F
iptables -t mangle -F

# 2. Forwarding & IPv6 Kill
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.disable_ipv6=1
sysctl -w net.ipv6.conf.default.disable_ipv6=1
sysctl -w net.ipv6.conf.lo.disable_ipv6=1

# 3. DEFAULT POLICY: DROP everything (Kill Switch)
iptables -P FORWARD DROP

# 4. LOKALE AUSNAHMEN (Portal muss IMMER gehen)
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 80 -j RETURN
iptables -t nat -A PREROUTING -i usb0 -d 192.168.7.1 -j RETURN

# 5. DNS-FIX: Umleitung von 53 auf 9053 (Anyone DNS)
iptables -t nat -A PREROUTING -i usb0 -p udp --dport 53 -j REDIRECT --to-ports 9053
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 53 -j REDIRECT --to-ports 9053

# 6. TRANSPARENT PROXY: Alle anderen TCP-Anfragen in den Tunnel (9040)
iptables -t nat -A PREROUTING -i usb0 -p tcp --syn -j REDIRECT --to-ports 9040

# 7. FORWARD: Nur established/related traffic erlauben (Kill Switch hardened)
iptables -A FORWARD -i usb0 -o wlan0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT

# 8. ROUTING & MTU
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# 9. Block all non-tunnel UDP (except DNS redirect) — prevents UDP leaks
iptables -A FORWARD -i usb0 -p udp --dport 53 -j ACCEPT
iptables -A FORWARD -i usb0 -p udp -j DROP

# 10. MAC Randomization (if enabled)
if [ -f /var/lib/anyone-stick/mac_random_enabled ]; then
    ip link set wlan0 down
    macchanger -r wlan0 2>/dev/null || true
    ip link set wlan0 up
fi

# 11. Kill Switch marker
touch /var/lib/anyone-stick/killswitch_active

# 12. LED & Status
echo heartbeat | sudo tee /sys/class/leds/default-on/trigger
SCRIPT
chmod +x ${SCRIPT_DIR}/mode_privacy.sh

cat > ${SCRIPT_DIR}/mode_normal.sh << 'SCRIPT'
#!/bin/bash
# Anyone Privacy Stick — Normal Mode (simple NAT, no tunnel)

iptables -F
iptables -t nat -F
iptables -t mangle -F

# Default policy: ACCEPT (no kill switch)
iptables -P FORWARD ACCEPT

# Einfaches NAT ohne Tunnel
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Re-enable IPv6
sysctl -w net.ipv6.conf.all.disable_ipv6=0
sysctl -w net.ipv6.conf.default.disable_ipv6=0

# Exit-Country-Auswahl zurücksetzen
sed -i '/^ExitNodes/d' /etc/anonrc
sed -i '/^StrictNodes/d' /etc/anonrc

# Anon-Config neu laden (falls Prozess läuft)
pkill -SIGHUP -x anon 2>/dev/null || true

# Remove Kill Switch marker
rm -f /var/lib/anyone-stick/killswitch_active

# LED: dauerhaft an = Normal Mode
echo default-on | sudo tee /sys/class/leds/default-on/trigger
SCRIPT
chmod +x ${SCRIPT_DIR}/mode_normal.sh
log "mode_privacy.sh & mode_normal.sh installiert"

# 12. Start-Script ─────────────────────────────────────────────────────────
cat > ${SCRIPT_DIR}/start_anyone_stack.sh << 'SCRIPT'
#!/bin/bash
# ============================================================================
# Anyone Privacy Stick Master Startup Script
# Started by anyone-stick.service AFTER usb-gadget.service
# ============================================================================

echo timer | sudo tee /sys/class/leds/default-on/trigger >/dev/null

# 1. Wait for usb0 (max 5s)
for i in $(seq 1 10); do
    ip link show usb0 &>/dev/null && break
    sleep 0.5
done

if ! ip link show usb0 &>/dev/null; then
    echo "ERROR: usb0 not found after 5s — USB gadget broken?"
    exit 1
fi

# 2. Assign static IP IMMEDIATELY (Windows is waiting for DHCP!)
ip addr flush dev usb0 2>/dev/null
ip addr add 192.168.7.1/24 dev usb0
ip link set usb0 up
sleep 0.5

# 3. Start dnsmasq (DHCP for host PC)
systemctl restart dnsmasq

# 4. Start portal (Flask with threaded=True for SSE support)
#    The app.py calls anon_ctrl.start() at module level, so the
#    AnonController reconnect loop starts automatically.
if command -v gunicorn &>/dev/null; then
    gunicorn --bind 0.0.0.0:80 \
             --workers 1 \
             --threads 4 \
             --timeout 0 \
             --worker-class gthread \
             --chdir /home/pi/portal \
             app:app &
else
    python3 /home/pi/portal/app.py &
fi

# 5. Connect Wi-Fi in background (non-blocking)
nmcli con up "Stick-Gateway" 2>/dev/null &

# 6. Wait for Wi-Fi (max 30s)
for i in $(seq 1 30); do
    nmcli -t -f STATE general 2>/dev/null | grep -q "connected" && break
    sleep 1
done

# 7. Start anon (blocks — keeps service alive)
/usr/local/bin/anon -f /etc/anonrc
SCRIPT
chmod +x ${SCRIPT_DIR}/start_anyone_stack.sh
log "start_anyone_stack.sh installed"

# 13. Configure dnsmasq ────────────────────────────────────────────────────
cat > /etc/dnsmasq.d/usb0.conf << 'EOF'
interface=usb0
bind-dynamic
dhcp-range=192.168.7.2,192.168.7.20,255.255.255.0,24h
dhcp-option=3,192.168.7.1
dhcp-option=6,192.168.7.1
dhcp-authoritative
leasefile-ro
EOF
log "dnsmasq configured for usb0 (192.168.7.0/24)"

# 14. Static IP for usb0 ───────────────────────────────────────────────────
# IP is set dynamically in start_anyone_stack.sh via 'ip addr add'.
# No ifupdown config needed (Bookworm uses NetworkManager).
log "Static IP 192.168.7.1 will be set at boot via start_anyone_stack.sh"

# 15. NetworkManager: usb0 ignorieren (wird manuell verwaltet) ─────────────
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/unmanaged-usb0.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:usb0
EOF
log "NetworkManager: usb0 als unmanaged markiert"

# 16. Web-Portal (Flask app) installieren ──────────────────────────────────
mkdir -p ${PORTAL_DIR}/static
cat > ${PORTAL_DIR}/app.py << 'PYEOF'
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

class AnonController:
    """Persistent connection to the anon daemon ControlPort with event support.

    Architecture:
    - Single persistent TCP connection (re-established on failure)
    - Background listener thread separates replies (250) from events (650)
    - SETEVENTS CIRC STATUS_CLIENT for push-based monitoring
    - Thread-safe reply queue for command/response correlation
    - SSE broadcast to all connected frontend clients
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
        self._rfile = None                     # buffered reader for the socket
        self._lock = threading.Lock()          # protects _sock writes
        self._reply_queue = queue.Queue()      # 250/251/… replies
        self._connected = False
        self._authenticated = False

        # SSE client management
        self._sse_clients = []                 # list[queue.Queue] per SSE client
        self._sse_lock = threading.Lock()

        # Cached state from events (always up-to-date)
        self._bootstrap = {"progress": 0, "summary": "", "tag": ""}
        self._circuit_hops = []                # latest BUILT circuit hops
        self._last_circ_id = None
        self._state_lock = threading.RLock()   # protects cached state

        # Relay IP/country cache  {fingerprint: {ip, cc, name, ts}}
        self._relay_cache = {}
        self._relay_cache_ttl = 3600           # 1 h

        # Background threads
        self._reader_thread = None
        self._reconnect_thread = None
        self._stop_event = threading.Event()

    # ────────────────── Lifecycle ──────────────────

    def start(self):
        """Start the persistent connection (call once at app startup)."""
        self._stop_event.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True, name="anon-reconnect"
        )
        self._reconnect_thread.start()
        log.info("AnonController started (reconnect loop running)")

    def stop(self):
        """Shut down the persistent connection."""
        self._stop_event.set()
        self._disconnect()

    # ────────────────── Connection management ──────────────────

    def _reconnect_loop(self):
        """Keep trying to (re)connect to the ControlPort."""
        while not self._stop_event.is_set():
            if not self._connected:
                try:
                    self._connect()
                except Exception as e:
                    log.debug("ControlPort connect failed: %s", e)
            self._stop_event.wait(timeout=3)

    def _connect(self):
        """Establish TCP connection, authenticate, subscribe to events."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((self.host, self.port))
        sock.settimeout(None)
        self._sock = sock
        self._rfile = sock.makefile("r", encoding="utf-8", errors="replace")
        self._connected = True
        log.info("TCP connected to %s:%s", self.host, self.port)

        # Start reader thread BEFORE sending commands
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="anon-reader"
        )
        self._reader_thread.start()

        # Authenticate
        self._authenticate()

        # Subscribe to push events
        self._subscribe_events()

    def _disconnect(self):
        """Tear down the TCP connection."""
        self._connected = False
        self._authenticated = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._rfile:
            try:
                self._rfile.close()
            except Exception:
                pass
            self._rfile = None
        # Drain reply queue
        while not self._reply_queue.empty():
            try:
                self._reply_queue.get_nowait()
            except queue.Empty:
                break

    def _authenticate(self):
        """Authenticate via COOKIE or password."""
        cookie_hex = None
        if os.path.exists(self.cookie_path):
            try:
                with open(self.cookie_path, "rb") as f:
                    cookie_hex = f.read().hex()
            except Exception:
                pass

        if cookie_hex:
            resp = self.command(f"AUTHENTICATE {cookie_hex}")
        else:
            resp = self.command('AUTHENTICATE ""')

        if resp and "250" in resp:
            self._authenticated = True
            log.info("Authenticated to ControlPort")
        else:
            log.warning("Authentication failed: %s", resp)
            raise ConnectionError(f"Auth failed: {resp}")

    def _subscribe_events(self):
        """Subscribe to CIRC + STATUS_CLIENT events for push updates."""
        resp = self.command("SETEVENTS CIRC STATUS_CLIENT")
        if resp and "250" in resp:
            log.info("Subscribed to CIRC + STATUS_CLIENT events")
        else:
            log.warning("SETEVENTS failed: %s", resp)

    # ────────────────── Reader thread ──────────────────

    def _reader_loop(self):
        """Background thread: read lines from ControlPort, route to queues."""
        buf = []
        try:
            while self._connected and not self._stop_event.is_set():
                try:
                    line = self._rfile.readline()
                except Exception:
                    break
                if not line:
                    break  # EOF — connection lost

                line = line.rstrip("\r\n")

                # Async event lines start with "650"
                if line.startswith("650"):
                    # 650-continuation or 650 final
                    if line.startswith("650-"):
                        buf.append(line[4:])
                    else:
                        # "650 <payload>" — single-line or final line of multi
                        payload = line[4:] if line.startswith("650 ") else line
                        buf.append(payload)
                        full_event = " ".join(buf)
                        buf = []
                        self._handle_event(full_event)
                else:
                    # Regular reply (250, 251, 515, …)
                    self._reply_queue.put(line)

        except Exception as e:
            log.debug("Reader loop exception: %s", e)
        finally:
            log.info("Reader loop exited — marking disconnected")
            self._disconnect()

    # ────────────────── Command interface ──────────────────

    def command(self, cmd, timeout=10):
        """Send a command and wait for the reply (thread-safe)."""
        return self._command_raw(cmd, timeout)

    def _command_raw(self, cmd, timeout=10):
        """Low-level: send command, collect all reply lines until final 250/5xx."""
        if not self._connected or not self._sock:
            return None
        # Drain stale replies
        while not self._reply_queue.empty():
            try:
                self._reply_queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            try:
                self._sock.sendall((cmd + "\r\n").encode("utf-8"))
            except Exception:
                self._disconnect()
                return None

        # Collect multi-line reply
        lines = []
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                line = self._reply_queue.get(timeout=min(remaining, 1))
                lines.append(line)
                # A line like "250 OK" or "250 …" (without '-') is the final line
                if re.match(r"^\d{3} ", line):
                    break
                # "250-..." means more lines follow
            except queue.Empty:
                continue
        return "\r\n".join(lines) if lines else None

    # ────────────────── Event handling ──────────────────

    def _handle_event(self, event_line):
        """Process a 650 event from the anon daemon."""
        parts = event_line.split(" ", 1)
        if len(parts) < 2:
            return
        event_type = parts[0]
        payload = parts[1]

        if event_type == "STATUS_CLIENT":
            self._handle_status_event(payload)
        elif event_type == "CIRC":
            self._handle_circ_event(payload)

    def _handle_status_event(self, payload):
        """Handle STATUS_CLIENT events (bootstrap progress)."""
        m_prog = re.search(r"PROGRESS=(\d+)", payload)
        m_summ = re.search(r'SUMMARY="([^"]*)"', payload)
        m_tag = re.search(r"TAG=(\S+)", payload)

        with self._state_lock:
            if m_prog:
                self._bootstrap["progress"] = int(m_prog.group(1))
            if m_summ:
                self._bootstrap["summary"] = m_summ.group(1)
            if m_tag:
                self._bootstrap["tag"] = m_tag.group(1)

        self._broadcast_sse(self._build_status_dict())

    def _handle_circ_event(self, payload):
        """Handle CIRC events (circuit build / close)."""
        parts = payload.split()
        if len(parts) < 2:
            return
        circ_id = parts[0]
        status = parts[1]

        if status == "BUILT" and len(parts) >= 3:
            path_str = parts[2]
            # Check for PURPOSE=GENERAL (prefer general-purpose circuits)
            purpose = ""
            for p in parts:
                if p.startswith("PURPOSE="):
                    purpose = p.split("=", 1)[1]
            # Only update display for GENERAL circuits (not internal)
            if purpose and purpose != "GENERAL":
                return

            hops = self._parse_path(path_str)
            with self._state_lock:
                self._circuit_hops = hops
                self._last_circ_id = circ_id

            self._broadcast_sse({
                "type": "circuit",
                "status": "BUILT",
                "hops": hops,
            })
            log.info("Circuit %s BUILT (%d hops)", circ_id, len(hops))

        elif status == "CLOSED" or status == "FAILED":
            with self._state_lock:
                if self._last_circ_id == circ_id:
                    self._circuit_hops = []
                    self._last_circ_id = None
                    self._broadcast_sse({
                        "type": "circuit",
                        "status": status,
                        "hops": [],
                    })

    # ────────────────── Path parsing & relay resolution ──────────────────

    def _parse_path(self, path_str):
        """Parse '$fingerprint~nickname,$fp2~nick2,…' into a list of hop dicts."""
        hops = []
        roles = ["guard", "middle", "exit"]
        nodes = path_str.split(",")
        for i, node in enumerate(nodes):
            fingerprint, nickname = "", ""
            if "~" in node:
                fp_part, nickname = node.split("~", 1)
                fingerprint = fp_part.lstrip("$")
            else:
                fingerprint = node.lstrip("$")

            role = roles[i] if i < len(roles) else f"hop{i+1}"

            # Resolve IP + Country (cached)
            ip, country_code, country_name = self._resolve_relay(fingerprint)

            hops.append({
                "fingerprint": fingerprint,
                "nickname": nickname,
                "role": role,
                "ip": ip or "",
                "country_code": country_code or "",
                "country_name": country_name or "",
            })
        return hops

    def _resolve_relay(self, fingerprint):
        """Get IP and country for a relay via ControlPort (with cache)."""
        # Check cache first
        cached = self._relay_cache.get(fingerprint)
        if cached and (time.time() - cached["ts"]) < self._relay_cache_ttl:
            return cached["ip"], cached["cc"], cached["name"]

        ip, country_code, country_name = None, None, None
        try:
            resp = self.command(f"GETINFO ns/id/{fingerprint}")
            if resp:
                for line in resp.split("\r\n"):
                    if line.startswith("r "):
                        parts = line.split()
                        if len(parts) >= 7:
                            ip = parts[6]
                        break
            # Country via ControlPort
            if ip:
                cr = self.command(f"GETINFO ip-to-country/{ip}")
                if cr:
                    m = re.search(r"ip-to-country/\S+=(\S+)", cr)
                    if m:
                        country_code = m.group(1).upper()

            country_name = self.COUNTRY_NAMES.get(country_code, country_code)

            # Update cache
            self._relay_cache[fingerprint] = {
                "ip": ip, "cc": country_code, "name": country_name,
                "ts": time.time(),
            }
        except Exception as e:
            log.debug("Relay resolve failed for %s: %s", fingerprint, e)

        return ip, country_code, country_name

    # ────────────────── SSE broadcast ──────────────────

    def add_sse_client(self):
        """Register a new SSE client and return its queue."""
        q = queue.Queue(maxsize=50)
        with self._sse_lock:
            self._sse_clients.append(q)

        # Push current state immediately so client is up-to-date
        try:
            q.put_nowait(self._build_status_dict())
        except queue.Full:
            pass
        hops = self.get_circuit_detail()
        if hops:
            try:
                q.put_nowait({"type": "circuit", "status": "BUILT", "hops": hops})
            except queue.Full:
                pass
        return q

    def remove_sse_client(self, q):
        """Unregister an SSE client."""
        with self._sse_lock:
            try:
                self._sse_clients.remove(q)
            except ValueError:
                pass

    def _broadcast_sse(self, event_data):
        """Push event data to all connected SSE clients."""
        with self._sse_lock:
            dead = []
            for q in self._sse_clients:
                try:
                    q.put_nowait(event_data)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                try:
                    self._sse_clients.remove(q)
                except ValueError:
                    pass

    # ────────────────── Status helpers ──────────────────

    def _build_status_dict(self):
        """Build a status dict from cached state (no ControlPort call)."""
        if not self._connected:
            return {"type": "status", "state": "error", "progress": 0,
                    "summary": "Cannot reach control port"}

        with self._state_lock:
            bs = dict(self._bootstrap)

        if bs["progress"] >= 100:
            return {"type": "status", "state": "connected", "progress": 100,
                    "summary": bs["summary"] or "Connected"}
        elif bs["progress"] > 0:
            return {"type": "status", "state": "bootstrapping",
                    "progress": bs["progress"],
                    "summary": bs["summary"] or f"Bootstrapping {bs['progress']}%"}
        else:
            return {"type": "status", "state": "stopped", "progress": 0,
                    "summary": "Waiting for daemon\u2026"}

    def get_status(self):
        """Return current status dict (cached, event-driven)."""
        return self._build_status_dict()

    def get_bootstrap(self):
        """Return cached bootstrap state. Falls back to active query if needed."""
        with self._state_lock:
            if self._bootstrap["progress"] > 0:
                return dict(self._bootstrap)

        # Fallback: direct query (first startup before events arrive)
        resp = self.command("GETINFO status/bootstrap-phase")
        if not resp:
            return None
        progress, summary, tag = 0, "", ""
        m = re.search(r"PROGRESS=(\d+)", resp)
        if m:
            progress = int(m.group(1))
        m = re.search(r'SUMMARY="([^"]*)"', resp)
        if m:
            summary = m.group(1)
        m = re.search(r"TAG=(\S+)", resp)
        if m:
            tag = m.group(1)

        with self._state_lock:
            self._bootstrap = {"progress": progress, "summary": summary, "tag": tag}

        return self._bootstrap

    def get_circuit_detail(self):
        """Return cached circuit hops (event-driven). Falls back to active query."""
        with self._state_lock:
            if self._circuit_hops:
                return list(self._circuit_hops)

        # Fallback: active query (for first load before any CIRC event)
        return self._query_circuit_detail()

    def _query_circuit_detail(self):
        """Active circuit query (fallback only)."""
        try:
            raw = self.command("GETINFO circuit-status")
            if not raw:
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
                return None

            hops = self._parse_path(built_path)

            with self._state_lock:
                self._circuit_hops = hops

            return hops
        except Exception:
            return None

    def new_circuit(self):
        """Signal NEWNYM to build fresh circuits."""
        resp = self.command("SIGNAL NEWNYM")

        # Clear cached circuit so UI shows "building…"
        with self._state_lock:
            self._circuit_hops = []
            self._last_circ_id = None

        # Notify SSE clients immediately
        self._broadcast_sse({
            "type": "circuit",
            "status": "NEWNYM",
            "hops": [],
        })

        return resp


anon_ctrl = AnonController()


# ============================================================================
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
  <img src="/static/logo.jpg" onerror="this.src='/static/logo.png'" class="logo-img">

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
  const btn=document.getElementById('scan-btn'); btn.disabled=true; btn.innerText='Scanning\u2026';
  document.getElementById('list').innerHTML='';
  try {
    const r=await fetch('/wifi/scan'); const d=await r.json();
    let h='';
    d.networks.forEach(n=>{
      const tag=n.connected?'<span class="connected-label">CONNECTED</span>':'<span>\u203A</span>';
      h+="<div class='wifi-item' onclick=\"sel('"+n.ssid.replace(/'/g,"\\\\'")+"')\"><span>"+n.ssid+"</span>"+tag+"</div>";
    });
    document.getElementById('list').innerHTML=h;
  } finally { btn.disabled=false; btn.innerText='Scan Networks'; }
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
anon_ctrl.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, threaded=True)
PYEOF
chown -R pi:pi ${PORTAL_DIR}
log "Web-Portal installiert → ${PORTAL_DIR}/app.py"

# 17. Systemd Services ────────────────────────────────────────────────────
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
Restart=on-failure
RestartSec=5
User=root
KillSignal=SIGINT
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable usb-gadget.service
systemctl enable anyone-stick.service
systemctl disable dnsmasq  # started manually by start_anyone_stack.sh
log "Systemd Services aktiviert"

# 18. Kernel-Module laden ──────────────────────────────────────────────────
echo "dwc2" >> /etc/modules
echo "libcomposite" >> /etc/modules
sort -u /etc/modules -o /etc/modules
log "Kernel-Module registriert (dwc2, libcomposite)"

# 19. sudoers für pi (damit Flask iptables aufrufen kann) ──────────────────
cat > /etc/sudoers.d/anyone-stick << 'EOF'
pi ALL=(ALL) NOPASSWD: /usr/local/bin/mode_privacy.sh, /usr/local/bin/mode_normal.sh, /usr/sbin/iptables, /usr/bin/tee /sys/class/leds/*, /usr/bin/pgrep
EOF
chmod 440 /etc/sudoers.d/anyone-stick
log "sudoers konfiguriert"

# 20. Abschluss ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Installation abgeschlossen!        ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Nächste Schritte:"
echo ""
echo "  1. WLAN einrichten (einmalig):"
echo "     nmcli dev wifi connect 'SSID' password 'PASSWORT'"
echo "     nmcli con mod 'SSID' connection.id 'Stick-Gateway'"
echo ""
echo "  2. Optional: Logo für Portal ablegen:"
echo "     cp logo.jpg /home/pi/portal/static/"
echo ""
echo "  3. Neustart:"
echo "     sudo reboot"
echo ""
echo "  Nach dem Reboot den Stick per USB an den PC"
echo "  anschließen → http://192.168.7.1 öffnen"
echo ""
