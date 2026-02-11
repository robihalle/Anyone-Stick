#!/usr/bin/env bash
# ============================================================================
# Anyone Privacy Stick Installer (File-based, Pi Zero 2 W)
# - Copies your current project files next to this install.sh
# - Auto-detects /boot vs /boot/firmware
# - Patches cmdline.txt with the real root PARTUUID (portable across SD cards)
# - Installs anon, portal, usb gadget, services, dnsmasq, NM settings
# - Copies optional static/logo.png if present
# ============================================================================
set -euo pipefail

# --- constants / paths ---
PORTAL_DIR="/home/pi/portal"
SCRIPT_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"
NM_DIR="/etc/NetworkManager/conf.d"
DNSMASQ_DIR="/etc/dnsmasq.d"

# Directory where this install.sh resides (expects the other files here too)
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- source files expected next to install.sh ---
F_APP="${SRC_DIR}/app.py"
F_USB_SETUP="${SRC_DIR}/usb_gadget_setup.sh"
F_START="${SRC_DIR}/start_anyone_stack.sh"
F_MODE_PRIV="${SRC_DIR}/mode_privacy.sh"
F_MODE_NORM="${SRC_DIR}/mode_normal.sh"
F_KILLSWITCH="${SRC_DIR}/anyone_killswitch.sh"
F_SVC_USB="${SRC_DIR}/usb-gadget.service"
F_SVC_MAIN="${SRC_DIR}/anyone-stick.service"
F_CONFIG_TXT="${SRC_DIR}/config.txt"
F_CMDLINE_TXT="${SRC_DIR}/cmdline.txt"
F_ANONRC="${SRC_DIR}/anonrc"         # optional (may be sample)
F_LOGO="${SRC_DIR}/logo.png"         # optional

# --- colors / helpers ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; exit 1; }

need_root() { [[ ${EUID:-0} -eq 0 ]] || err "Bitte als root ausführen: sudo bash install.sh"; }
have_file() { [[ -f "$1" ]]; }

require_file() {
  local f="$1"
  have_file "$f" || err "Fehlende Datei neben install.sh: $(basename "$f")"
}

detect_boot_dir() {
  if [[ -d /boot/firmware ]]; then echo "/boot/firmware"; return; fi
  if [[ -d /boot ]]; then echo "/boot"; return; fi
  err "Boot-Verzeichnis nicht gefunden (/boot/firmware oder /boot)."
}

get_root_partuuid() {
  local pu=""
  pu="$(findmnt / -no PARTUUID 2>/dev/null || true)"
  [[ -n "$pu" ]] || err "Konnte PARTUUID nicht ermitteln (findmnt / -no PARTUUID)."
  echo "$pu"
}

safe_install() {
  # install file with mode/owner
  local src="$1" dst="$2" mode="${3:-755}" owner="${4:-root:root}"
  install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dst"
}

# Validate anonrc contains required directives (uncommented, effective)
anonrc_is_valid() {
  local p="$1"
  grep -Eq '^[[:space:]]*ControlPort[[:space:]]+9051' "$p" || return 1
  grep -Eq '^[[:space:]]*SocksPort[[:space:]]+9050' "$p" || return 1
  grep -Eq '^[[:space:]]*TransPort[[:space:]]+0\.0\.0\.0:9040' "$p" || return 1
  grep -Eq '^[[:space:]]*DNSPort[[:space:]]+0\.0\.0\.0:9053' "$p" || return 1
  grep -Eq '^[[:space:]]*DataDirectory[[:space:]]+/var/lib/anon' "$p" || return 1
  return 0
}

write_default_anonrc() {
  cat > /etc/anonrc <<'EOF'
SocksPort 9050 IsolateDestAddr IsolateDestPort
ControlPort 9051
CookieAuthentication 1
CookieAuthFile /var/lib/anon/control_auth_cookie
CookieAuthFileGroupReadable 1
DNSPort 0.0.0.0:9053
TransPort 0.0.0.0:9040
User root
DataDirectory /var/lib/anon
AgreeToTerms 1
AutomapHostsOnResolve 1
VirtualAddrNetworkIPv4 10.192.0.0/10
Log notice file /var/log/anon/notices.log

# Performance tuning for Pi Zero 2W
MaxMemInQueues 128 MB
NumCPUs 4
CircuitBuildTimeout 30
LearnCircuitBuildTimeout 1
CircuitStreamTimeout 20
MaxClientCircuitsPending 16
EOF
}

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Anyone Privacy Stick Installer     ║"
echo "║   (file-based) Pi Zero 2 W           ║"
echo "╚══════════════════════════════════════╝"
echo ""

need_root

# --- hard-required source files ---
require_file "$F_APP"
require_file "$F_USB_SETUP"
require_file "$F_START"
require_file "$F_MODE_PRIV"
require_file "$F_MODE_NORM"
require_file "$F_KILLSWITCH"
require_file "$F_SVC_USB"
require_file "$F_SVC_MAIN"
require_file "$F_CONFIG_TXT"
require_file "$F_CMDLINE_TXT"

# --- optional files ---
have_file "$F_ANONRC" || warn "anonrc nicht gefunden (optional) – es wird eine Default-Konfiguration geschrieben."
have_file "$F_LOGO"  || warn "logo.png nicht gefunden (optional) – es wird kein Logo installiert."

# 1) system update & packages
export DEBIAN_FRONTEND=noninteractive
log "APT update/upgrade..."
apt-get update -qq
apt-get upgrade -y -qq

# prevent interactive prompts (best-effort)
echo iptables-persistent iptables-persistent/autosave_v4 boolean false | debconf-set-selections 2>/dev/null || true
echo iptables-persistent iptables-persistent/autosave_v6 boolean false | debconf-set-selections 2>/dev/null || true
echo macchanger macchanger/automatically_run boolean false | debconf-set-selections 2>/dev/null || true

log "Install packages..."
apt-get install -y -qq \
  python3-flask python3-pip \
  dnsmasq network-manager \
  iptables-persistent \
  curl unzip jq libcap2 macchanger \
  ca-certificates wget

# gunicorn (best effort)
pip3 install --break-system-packages gunicorn 2>/dev/null || pip3 install gunicorn 2>/dev/null || true

# 2) disable unneeded services (best-effort)
log "Disable unneeded services..."
systemctl disable --now bluetooth 2>/dev/null || true
systemctl disable --now avahi-daemon 2>/dev/null || true
systemctl disable --now triggerhappy 2>/dev/null || true
systemctl disable --now apt-daily.timer 2>/dev/null || true
systemctl disable --now apt-daily-upgrade.timer 2>/dev/null || true

# 3) install anon from Anyone apt repo
log "Install anon via Anyone APT repo..."
wget -qO- https://deb.en.anyone.tech/anon.asc | tee /etc/apt/trusted.gpg.d/anon.asc >/dev/null
echo "deb [signed-by=/etc/apt/trusted.gpg.d/anon.asc] https://deb.en.anyone.tech anon-live-bookworm main" \
  | tee /etc/apt/sources.list.d/anon.list >/dev/null
apt-get update -qq
apt-get install -y -qq anon
ln -sf /usr/bin/anon /usr/local/bin/anon
[[ -x /usr/local/bin/anon ]] || err "Anon konnte nicht installiert werden."

# 4) ensure user pi
if ! id pi &>/dev/null; then
  useradd -m -s /bin/bash pi
  log "User 'pi' created."
fi

# 5) anon directories
mkdir -p /var/lib/anon /var/log/anon
chown -R root:root /var/lib/anon /var/log/anon
chmod 750 /var/lib/anon
touch /var/log/anon/notices.log || true
chmod 640 /var/log/anon/notices.log || true
log "Anon DataDirectory ready."

# 6) anonrc (copy if valid else fallback)
if have_file "$F_ANONRC"; then
  if anonrc_is_valid "$F_ANONRC"; then
    safe_install "$F_ANONRC" /etc/anonrc 644 root:root
    log "Installed anonrc from $(basename "$F_ANONRC")."
  else
    safe_install "$F_ANONRC" /etc/anonrc.sample 644 root:root
    write_default_anonrc
    chmod 644 /etc/anonrc
    warn "Provided anonrc looked like a sample (missing active ControlPort/TransPort/DNSPort)."
    warn "Wrote working /etc/anonrc and saved your file as /etc/anonrc.sample"
  fi
else
  write_default_anonrc
  chmod 644 /etc/anonrc
  warn "No anonrc next to installer; wrote default /etc/anonrc."
fi

# 7) boot config files
BOOT_DIR="$(detect_boot_dir)"
ROOT_PARTUUID="$(get_root_partuuid)"

log "Write ${BOOT_DIR}/config.txt from provided file..."
safe_install "$F_CONFIG_TXT" "${BOOT_DIR}/config.txt" 644 root:root

log "Patch ${BOOT_DIR}/cmdline.txt with correct PARTUUID..."
tmp_cmd="$(mktemp)"
cp -f "$F_CMDLINE_TXT" "$tmp_cmd"
# replace any root=PARTUUID=... token with the real value
sed -i -E "s#root=PARTUUID=[^ ]+#root=PARTUUID=${ROOT_PARTUUID}#g" "$tmp_cmd"
safe_install "$tmp_cmd" "${BOOT_DIR}/cmdline.txt" 644 root:root
rm -f "$tmp_cmd"
log "cmdline.txt installed (PARTUUID=${ROOT_PARTUUID})."

# 8) sysctl (ip forward + ipv6 off)
cat > /etc/sysctl.d/99-anyone-stick.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.disable_ipv6=1
EOF
sysctl --system -q || true
log "sysctl set (ip_forward=1, ipv6 disabled)."

# 9) install scripts to /usr/local/bin
log "Install scripts to ${SCRIPT_DIR}..."
safe_install "$F_USB_SETUP"   "${SCRIPT_DIR}/usb_gadget_setup.sh" 755 root:root
safe_install "$F_START"       "${SCRIPT_DIR}/start_anyone_stack.sh" 755 root:root
safe_install "$F_MODE_PRIV"   "${SCRIPT_DIR}/mode_privacy.sh" 755 root:root
safe_install "$F_MODE_NORM"   "${SCRIPT_DIR}/mode_normal.sh" 755 root:root
safe_install "$F_KILLSWITCH"  "${SCRIPT_DIR}/anyone_killswitch.sh" 755 root:root

# 10) dnsmasq config (usb0 DHCP)
mkdir -p "$DNSMASQ_DIR"
cat > "${DNSMASQ_DIR}/usb0.conf" <<'EOF'
interface=usb0
bind-dynamic
dhcp-range=192.168.7.2,192.168.7.20,255.255.255.0,24h
dhcp-option=3,192.168.7.1
dhcp-option=6,192.168.7.1
dhcp-authoritative
leasefile-ro
address=/anyone.stick/192.168.7.1
EOF
log "dnsmasq configured for usb0 (192.168.7.0/24)."

# 11) NetworkManager unmanaged usb0
mkdir -p "$NM_DIR"
cat > "${NM_DIR}/unmanaged-usb0.conf" <<'EOF'
[keyfile]
unmanaged-devices=interface-name:usb0
EOF
log "NetworkManager: usb0 set unmanaged."

# 12) portal files
log "Install portal -> ${PORTAL_DIR} ..."
mkdir -p "${PORTAL_DIR}/static"
safe_install "$F_APP" "${PORTAL_DIR}/app.py" 755 pi:pi

# optional: logo.png -> /home/pi/portal/static/logo.png
if have_file "$F_LOGO"; then
  safe_install "$F_LOGO" "${PORTAL_DIR}/static/logo.png" 644 pi:pi
  log "logo.png installed -> ${PORTAL_DIR}/static/logo.png"
fi

chown -R pi:pi "${PORTAL_DIR}"
log "Portal installed."

# 13) systemd units
log "Install systemd services..."
safe_install "$F_SVC_USB"  "${SYSTEMD_DIR}/usb-gadget.service" 644 root:root
safe_install "$F_SVC_MAIN" "${SYSTEMD_DIR}/anyone-stick.service" 644 root:root
systemctl daemon-reload

systemctl enable usb-gadget.service
systemctl enable anyone-stick.service
log "Services enabled (usb-gadget.service, anyone-stick.service)."

# 14) modules
log "Register kernel modules (dwc2, libcomposite)..."
grep -qxF "dwc2" /etc/modules || echo "dwc2" >> /etc/modules
grep -qxF "libcomposite" /etc/modules || echo "libcomposite" >> /etc/modules

# 15) sudoers (best-effort)
cat > /etc/sudoers.d/anyone-stick <<'EOF'
pi ALL=(ALL) NOPASSWD: /usr/local/bin/mode_privacy.sh, /usr/local/bin/mode_normal.sh, /usr/local/bin/anyone_killswitch.sh, /usr/sbin/iptables, /usr/sbin/ip6tables, /usr/bin/tee, /usr/bin/pgrep
EOF
chmod 440 /etc/sudoers.d/anyone-stick
log "sudoers configured."

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Installation abgeschlossen!        ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1) Reboot:"
echo "     sudo reboot"
echo ""
echo "Nach Reboot: Stick per USB an PC -> http://192.168.7.1"
echo ""
