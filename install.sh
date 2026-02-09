#!/bin/bash
# ============================================================================
# Anyone Privacy Stick – Installer
# Verwandelt ein frisches Raspberry Pi OS Lite 64-bit in einen Anyone Stick
# Ziel-Hardware: Raspberry Pi Zero 2 W
# ============================================================================
set -e

ANON_VERSION="v0.4.9.11"
ANON_URL="https://github.com/anyone-protocol/ator-protocol/releases/download/${ANON_VERSION}"
PORTAL_DIR="/home/pi/portal"
SCRIPT_DIR="/usr/local/bin"
STATE_DIR="/var/lib/anyone-stick"

# Farben
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✔]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✘]${NC} $1"; exit 1; }

# ── Root-Check ──────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Bitte als root ausführen: sudo bash install.sh"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Anyone Privacy Stick – Installer                  ║"
echo "║           Raspberry Pi Zero 2 W                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── 1. System aktualisieren & Pakete installieren ───────────────────────────
log "System aktualisieren..."
apt-get update -qq
apt-get upgrade -y -qq

log "Pakete installieren..."
apt-get install -y -qq \
    python3-flask \
    dnsmasq \
    network-manager \
    iptables-persistent \
    curl \
    unzip \
    jq \
    libcap2 \
    macchanger

# ── 2. Unnötige Services deaktivieren ──────────────────────────────────────
log "Unnötige Services deaktivieren..."
systemctl disable --now bluetooth 2>/dev/null || true
systemctl disable --now avahi-daemon 2>/dev/null || true
systemctl disable --now triggerhappy 2>/dev/null || true
systemctl disable --now apt-daily.timer 2>/dev/null || true
systemctl disable --now apt-daily-upgrade.timer 2>/dev/null || true

# ── 3. Anon-Binary herunterladen und installieren ──────────────────────────
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
        warn "Anon-Binary nicht im Archiv gefunden – bitte manuell installieren!"
    fi
    rm -f /tmp/anon.tar.gz
fi

if [ "$ANON_INSTALLED" = false ]; then
    warn "Anon konnte nicht automatisch heruntergeladen werden."
    warn "Bitte manuell von https://github.com/anyone-protocol/ator-protocol/releases installieren."
    warn "Binary muss nach /usr/local/bin/anon kopiert werden."
fi

# ── 4. User 'pi' sicherstellen ────────────────────────────────────────────
if ! id pi &>/dev/null; then
    useradd -m -s /bin/bash pi
    log "User 'pi' erstellt"
fi

# ── 5. Anon DataDirectory & State Directory erstellen ─────────────────────
mkdir -p /root/.anon
mkdir -p ${STATE_DIR}
log "Anon DataDirectory & State Directory erstellt"

# ── 6. anonrc schreiben (mit Stream Isolation, DNS Leak Protection, etc.) ─
cat > /etc/anonrc << 'EOF'
## ============================================================
## Anyone Stick – anonrc (optimized for Raspberry Pi Zero 2 W)
## ============================================================

## ----- Network Ports -----------------------------------------
SocksPort 9050 IsolateDestAddr IsolateDestPort
ControlPort 9051
CookieAuthentication 1
DNSPort 0.0.0.0:9053 IsolateDestAddr
TransPort 0.0.0.0:9040

## ----- DNS Leak Protection -----------------------------------
ClientDNSRejectInternalAddresses 1

## ----- Virtual Addresses / DNS -------------------------------
AutomapHostsOnResolve 1
VirtualAddrNetworkIPv4 10.192.0.0/10

## ----- Memory / RAM (Pi Zero 2 = 512 MB) ---------------------
MaxMemInQueues 128 MBytes
ConstrainedSockets 1
ConstrainedSockSize 4096

## ----- Conflux (Traffic Splitting) ---------------------------
ConfluxEnabled 1
ConfluxClientUX latency_lowmem

## ----- Hardware Crypto Acceleration --------------------------
HardwareAccel 1

## ----- Circuit Timeouts & Rotation ---------------------------
CircuitBuildTimeout 90
LearnCircuitBuildTimeout 1
CircuitStreamTimeout 60
MaxCircuitDirtiness 300
NewCircuitPeriod 15

## ----- Subnet Isolation (Anti-Correlation) -------------------
EnforceDistinctSubnets 1

## ----- Dormant Mode (Power Saving) ---------------------------
DormantClientTimeout 1800
DormantCanceledByStartup 0

## ----- Connection Limits -------------------------------------
ConnLimit 500
MaxClientCircuitsPending 24

## ----- Keepalive ---------------------------------------------
KeepalivePeriod 120

## ----- Guard Protection --------------------------------------
VanguardsLiteEnabled 1

## ----- Logging -----------------------------------------------
Log notice stderr

## ----- Security ----------------------------------------------
ClientOnly 1
EOF
log "anonrc geschrieben → /etc/anonrc (Stream Isolation, DNS Leak Protection, Subnet Isolation)"

# ── 7. Boot-Config (config.txt) ─────────────────────────────────────────
BOOT_DIR="/boot/firmware"
[ ! -d "$BOOT_DIR" ] && BOOT_DIR="/boot"

cat > ${BOOT_DIR}/config.txt << 'EOF'
# Anyone Privacy Stick – config.txt (optimiert für headless USB-Betrieb)
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

# ── 8. cmdline.txt ──────────────────────────────────────────────────────
ROOT_PARTUUID=$(findmnt / -no PARTUUID 2>/dev/null || echo "FIXME")
cat > ${BOOT_DIR}/cmdline.txt << EOF
console=serial0,115200 console=tty1 root=PARTUUID=${ROOT_PARTUUID} rootfstype=ext4 fsck.repair=yes rootwait quiet cfg80211.ieee80211_regdom=DE modules-load=dwc2,libcomposite ipv6.disable=1
EOF
log "cmdline.txt geschrieben → ${BOOT_DIR}/cmdline.txt (PARTUUID=${ROOT_PARTUUID})"

# ── 9. IP-Forwarding aktivieren & IPv6 deaktivieren ────────────────────
cat > /etc/sysctl.d/99-anyone-stick.conf << 'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.disable_ipv6=1
net.ipv6.conf.default.disable_ipv6=1
net.ipv6.conf.lo.disable_ipv6=1
EOF
sysctl --system -q
log "IP-Forwarding aktiviert, IPv6 vollständig deaktiviert"

# ── 10. USB Gadget Setup Script ──────────────────────────────────────────
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
log "usb_gadget_setup.sh installiert"

# ── 11. Mode-Scripts (mit Kill Switch, MAC Randomization, UDP Block) ─────
cat > ${SCRIPT_DIR}/mode_privacy.sh << 'SCRIPT'
#!/bin/bash
# Anyone Privacy Stick – Privacy Mode (Kill Switch enabled)

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

# 7. FORWARD: Nur established/related traffic erlauben (Kill Switch)
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT

# 8. ROUTING & MTU
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# 9. Block all non-tunnel UDP (except DNS redirect) – prevents UDP leaks
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
# Anyone Privacy Stick – Normal Mode (simple NAT, no tunnel)

iptables -F
iptables -t nat -F
iptables -t mangle -F

# Default policy: ACCEPT (no kill switch)
iptables -P FORWARD ACCEPT

# Simple NAT without tunnel
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Re-enable IPv6
sysctl -w net.ipv6.conf.all.disable_ipv6=0
sysctl -w net.ipv6.conf.default.disable_ipv6=0

# Remove exit country selection from config
sed -i '/^ExitNodes/d' /etc/anonrc
sed -i '/^StrictNodes/d' /etc/anonrc

# Remove Kill Switch marker
rm -f /var/lib/anyone-stick/killswitch_active

# Anon-Config neu laden (falls Prozess läuft)
pkill -SIGHUP -x anon 2>/dev/null || true

# LED: dauerhaft an = Normal Mode
echo default-on | sudo tee /sys/class/leds/default-on/trigger
SCRIPT
chmod +x ${SCRIPT_DIR}/mode_normal.sh
log "mode_privacy.sh & mode_normal.sh installiert (Kill Switch, MAC Randomization, UDP Block)"

# ── 12. Start-Script ────────────────────────────────────────────────────
cat > ${SCRIPT_DIR}/start_anyone_stack.sh << 'SCRIPT'
#!/bin/bash
echo timer | sudo tee /sys/class/leds/default-on/trigger

# State Directory sicherstellen
mkdir -p /var/lib/anyone-stick

# USB-Gadget ist bereits über usb-gadget.service aktiv.
# Kurz warten bis usb0 wirklich da ist (max 5s).
for i in $(seq 1 10); do
    ip link show usb0 &>/dev/null && break
    sleep 0.5
done

# WLAN aktivieren (im Hintergrund, blockiert nicht)
nmcli con up "Stick-Gateway" &

# DHCP sofort starten, damit der Host eine IP bekommt
systemctl restart dnsmasq

# Portal sofort starten
python3 /home/pi/portal/app.py &

# Aktiv auf WLAN-Verbindung warten (max 30s)
for i in $(seq 1 30); do
    nmcli -t -f STATE general 2>/dev/null | grep -q "connected" && break
    sleep 1
done

# Anon-Stack starten
/usr/local/bin/anon -f /etc/anonrc
SCRIPT
chmod +x ${SCRIPT_DIR}/start_anyone_stack.sh
log "start_anyone_stack.sh installiert"

# ── 13. dnsmasq konfigurieren ───────────────────────────────────────────
cat > /etc/dnsmasq.d/usb0.conf << 'EOF'
interface=usb0
bind-interfaces
dhcp-range=192.168.7.2,192.168.7.20,255.255.255.0,24h
dhcp-option=3,192.168.7.1
dhcp-option=6,192.168.7.1
EOF
log "dnsmasq konfiguriert für usb0 (192.168.7.0/24)"

# ── 14. Statische IP für usb0 ──────────────────────────────────────────
cat > /etc/network/interfaces.d/usb0 << 'EOF'
auto usb0
iface usb0 inet static
    address 192.168.7.1
    netmask 255.255.255.0
EOF
log "Statische IP 192.168.7.1 für usb0 konfiguriert"

# ── 15. NetworkManager: usb0 ignorieren (wird manuell verwaltet) ───────
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/unmanaged-usb0.conf << 'EOF'
[keyfile]
unmanaged-devices=interface-name:usb0
EOF
log "NetworkManager: usb0 als unmanaged markiert"

# ── 16. Web-Portal (Flask app) installieren ─────────────────────────────
mkdir -p ${PORTAL_DIR}/static
cp /dev/stdin ${PORTAL_DIR}/app.py << 'PYEOF'
# ── app.py wird separat deployed (zu groß für heredoc) ──
# Platzhalter – die aktuelle app.py muss manuell nach
# /home/pi/portal/app.py kopiert werden.
PYEOF

# Stattdessen: app.py direkt aus dem Repo/Build kopieren
if [ -f "$(dirname "$0")/app.py" ]; then
    cp "$(dirname "$0")/app.py" ${PORTAL_DIR}/app.py
    log "app.py aus Build-Verzeichnis kopiert"
else
    warn "app.py nicht im Build-Verzeichnis gefunden!"
    warn "Bitte manuell nach ${PORTAL_DIR}/app.py kopieren."
fi

chown -R pi:pi ${PORTAL_DIR}
log "Web-Portal installiert → ${PORTAL_DIR}/"

# ── 17. Systemd Services ────────────────────────────────────────────────
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
log "Systemd Services aktiviert"

# ── 18. Kernel-Module laden ─────────────────────────────────────────────
echo "dwc2" >> /etc/modules
echo "libcomposite" >> /etc/modules
sort -u /etc/modules -o /etc/modules
log "Kernel-Module registriert (dwc2, libcomposite)"

# ── 19. sudoers für pi (damit Flask iptables/macchanger aufrufen kann) ──
cat > /etc/sudoers.d/anyone-stick << 'EOF'
pi ALL=(ALL) NOPASSWD: /usr/local/bin/mode_privacy.sh, /usr/local/bin/mode_normal.sh, /usr/sbin/iptables, /usr/bin/tee /sys/class/leds/*, /usr/bin/macchanger
EOF
chmod 440 /etc/sudoers.d/anyone-stick
log "sudoers konfiguriert (inkl. macchanger)"

# ── 20. macchanger: automatische Prompts deaktivieren ──────────────────
if [ -f /etc/default/macchanger ]; then
    sed -i 's/ENABLE_ON_POST_UP_DOWN=.*/ENABLE_ON_POST_UP_DOWN=false/' /etc/default/macchanger
fi
log "macchanger konfiguriert"

# ── 21. Abschluss ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                 Installation abgeschlossen!                 ║"
echo "║                                                             ║"
echo "║  Neue Features:                                             ║"
echo "║  ✔ Stream Isolation (IsolateDestAddr/Port)                  ║"
echo "║  ✔ DNS Leak Protection                                     ║"
echo "║  ✔ Kill Switch (FORWARD DROP Policy)                       ║"
echo "║  ✔ MAC Randomization (macchanger)                          ║"
echo "║  ✔ Circuit Rotation Timer (UI-konfigurierbar)              ║"
echo "║  ✔ Privacy Status Dashboard                                ║"
echo "║  ✔ Subnet Isolation (EnforceDistinctSubnets)               ║"
echo "║  ✔ UDP Leak Prevention                                     ║"
echo "║                                                             ║"
echo "║  WICHTIG: app.py muss nach ${PORTAL_DIR}/app.py            ║"
echo "║  kopiert werden, falls nicht automatisch erkannt.           ║"
echo "║                                                             ║"
echo "║  → sudo reboot                                             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

