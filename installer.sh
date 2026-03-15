#!/bin/bash
set -euo pipefail

# =============================================================================
# Anyone Stick -- Installer
# Target: Raspberry Pi Zero 2W | Raspberry Pi OS Lite 64-bit (Bookworm/Trixie)
# Usage:  sudo bash install.sh
# Prereq: Pi is connected to WiFi with internet access
# =============================================================================

REPO_RAW="https://raw.githubusercontent.com/robihalle/Anyone-Stick/feature/2-hops"

CM_DIR="/opt/anyone-stick/circuit-manager"
PRIMARY_USER="${SUDO_USER:-$(awk -F: '$3 == 1000 {print $1; exit}' /etc/passwd 2>/dev/null || true)}"
PORTAL_DIR="/opt/anyone-stick/portal"
BIN_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"
STATE_DIR="/var/lib/anyone-stick"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERR ]${NC}  $*" >&2; exit 1; }
section() { echo -e "\n${BLUE}======================================${NC}"; \
            echo -e "${BLUE}  $*${NC}"; \
            echo -e "${BLUE}======================================${NC}"; }

# =============================================================================
# 0 -- Preflight
# =============================================================================
section "0/9 - Preflight Checks"

[[ $EUID -ne 0 ]] && error "Please run as root: sudo bash install.sh"

ARCH=$(uname -m)
log "Architecture: $ARCH"
[[ "$ARCH" != "aarch64" && "$ARCH" != "armv7l" ]] && warn "Unknown architecture -- proceed at your own risk."

ping -c1 -W5 1.1.1.1 &>/dev/null || error "No internet connection. Please configure WiFi first."
ok "Internet connection confirmed"

# Script lives in repo root -- reference files relative to it
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log "Installer directory: $SCRIPT_DIR"
[[ -n "${PRIMARY_USER:-}" ]] && log "Primary user: $PRIMARY_USER" || warn "Could not determine primary user; continuing as root"
log "Portal directory: $PORTAL_DIR"

# Helper: copy file from local repo OR download from remote
copy_file() {
  local src="$1"   # relative path within repo
  local dst="$2"
  local mode="${3:-644}"

  mkdir -p "$(dirname "$dst")"

  if [[ -f "$SCRIPT_DIR/$src" ]]; then
    cp "$SCRIPT_DIR/$src" "$dst"
  else
    curl -fsSL "$REPO_RAW/$src" -o "$dst" \
      || error "Failed to download $src."
  fi

  chmod "$mode" "$dst"
  ok "Deployed: $dst"
}

# =============================================================================
# 1 -- System Packages
# =============================================================================
section "1/9 - System Packages"

apt-get update -qq
apt-get install -y --no-install-recommends \
  git curl wget gnupg netcat-openbsd \
  python3 python3-pip python3-venv \
  network-manager dnsmasq \
  iptables iproute2 \
  jq usbutils dnsutils
ok "Packages installed"

# systemd-resolved conflicts with dnsmasq on port 53
if systemctl is-active --quiet systemd-resolved; then
  systemctl disable --now systemd-resolved
  rm -f /etc/resolv.conf
  echo 'nameserver 1.1.1.1' > /etc/resolv.conf
  ok "systemd-resolved disabled"
fi

# NetworkManager as default network backend
systemctl enable --now NetworkManager
ok "NetworkManager active"

# =============================================================================
# 2 — USB Gadget (dwc2 + configfs/NCM)
# =============================================================================
section "2/9 · USB Gadget"

CONFIG_TXT="/boot/firmware/config.txt"
[[ ! -f "$CONFIG_TXT" ]] && CONFIG_TXT="/boot/config.txt"  # fallback for older images

# --- config.txt: dtoverlay=dwc2,dr_mode=peripheral ---
# Do NOT overwrite the whole file — it contains board-specific settings.
# We need dr_mode=peripheral to force gadget mode (not host mode).

# Remove any bare "dtoverlay=dwc2" (without dr_mode) to avoid conflicts
sed -i '/^dtoverlay=dwc2$/d' "$CONFIG_TXT"

# Ensure [all] section has the correct overlay
if ! grep -q 'dtoverlay=dwc2,dr_mode=peripheral' "$CONFIG_TXT"; then
  if grep -q '^\[all\]' "$CONFIG_TXT"; then
    sed -i '/^\[all\]/a dtoverlay=dwc2,dr_mode=peripheral' "$CONFIG_TXT"
  else
    cat >> "$CONFIG_TXT" << 'DTOVERLAY'

# Anyone Stick — USB Gadget
[all]
enable_uart=1
dtoverlay=dwc2,dr_mode=peripheral
DTOVERLAY
  fi
  ok "dtoverlay=dwc2,dr_mode=peripheral added to [all] in $CONFIG_TXT"
else
  ok "dtoverlay=dwc2,dr_mode=peripheral already present"
fi

# --- cmdline.txt: modules-load=dwc2 ---
# IMPORTANT: cmdline.txt must remain a single line — never append a newline!
# Do NOT replace this file: it contains the root PARTUUID which is unique per SD card.
CMDLINE_TXT="/boot/firmware/cmdline.txt"
[[ ! -f "$CMDLINE_TXT" ]] && CMDLINE_TXT="/boot/cmdline.txt"

if ! grep -q 'modules-load=dwc2' "$CMDLINE_TXT"; then
  if grep -q 'rootwait' "$CMDLINE_TXT"; then
    sed -i 's/rootwait/modules-load=dwc2 rootwait/' "$CMDLINE_TXT"
  else
    sed -i 's/$/ modules-load=dwc2/' "$CMDLINE_TXT"
  fi
  ok "modules-load=dwc2 injected into $CMDLINE_TXT"
else
  ok "modules-load=dwc2 already present"
fi

# Load libcomposite kernel module at boot (required for configfs USB gadget)
if ! grep -q 'libcomposite' /etc/modules-load.d/*.conf 2>/dev/null; then
  echo 'libcomposite' > /etc/modules-load.d/usb-gadget.conf
  ok "libcomposite added to modules-load"
fi

copy_file "usb_gadget_setup.sh"  "$BIN_DIR/usb_gadget_setup.sh"  755
copy_file "usb-gadget.service"   "$SYSTEMD_DIR/usb-gadget.service" 644

# =============================================================================
# 3 — Anyone Protocol (anon)
# =============================================================================
section "3/9 · Anyone Protocol (anon)"

. /etc/os-release

ANYONE_REPO_BASE="https://deb.en.anyone.tech"
ANYONE_KEYRING="/etc/apt/trusted.gpg.d/anon.asc"
ANYONE_LIST="/etc/apt/sources.list.d/anon.list"
ANYONE_SUITE_PRIMARY="anon-live-${VERSION_CODENAME}"
ANYONE_SUITE_FALLBACK="anon-live-bookworm"

configure_anyone_repo() {
  local suite="$1"

  echo "deb [signed-by=${ANYONE_KEYRING}] ${ANYONE_REPO_BASE} ${suite} main" \
    > "$ANYONE_LIST"

  if apt-get update \
      -o Dir::Etc::sourcelist="$ANYONE_LIST" \
      -o Dir::Etc::sourceparts="-" \
      -o APT::Get::List-Cleanup="0" \
      >/tmp/anyone-stick-apt-check.log 2>&1; then
    return 0
  fi

  warn "Anyone repo suite ${suite} could not be used on this image."
  tail -n 20 /tmp/anyone-stick-apt-check.log || true
  return 1
}

if ! command -v anon &>/dev/null; then
  log "Adding Anyone APT repository (official source)..."

  getent hosts deb.en.anyone.tech >/dev/null 2>&1 \
    || error "Could not resolve deb.en.anyone.tech. Check DNS/network and try again."

  wget -qO- "${ANYONE_REPO_BASE}/anon.asc" > "$ANYONE_KEYRING" \
    || error "Failed to download Anyone signing key from ${ANYONE_REPO_BASE}/anon.asc"

  chmod 644 "$ANYONE_KEYRING"

  if configure_anyone_repo "$ANYONE_SUITE_PRIMARY"; then
    ok "Anyone APT repo configured: $ANYONE_SUITE_PRIMARY"
  elif [[ "$ANYONE_SUITE_PRIMARY" != "$ANYONE_SUITE_FALLBACK" ]] && configure_anyone_repo "$ANYONE_SUITE_FALLBACK"; then
    ok "Anyone APT repo configured via fallback: $ANYONE_SUITE_FALLBACK"
  else
    error "Could not configure the Anyone APT repository for ${VERSION_CODENAME} or fallback ${ANYONE_SUITE_FALLBACK}."
  fi

  apt-get update -qq

  # Pre-accept the Anyone license agreement (required for non-interactive install)
  export DEBIAN_FRONTEND=noninteractive
  echo "anon anon/terms boolean true" | debconf-set-selections

  apt-get install -y anon \
    || error "Failed to install anon."

  ok "anon installed: $(anon --version 2>&1 | head -1)"
else
  ok "anon already installed: $(anon --version 2>&1 | head -1)"
fi

# Verify debian-anon user and /var/lib/anon exist (created by package postinst)
id debian-anon &>/dev/null \
  || error "debian-anon user missing — is the anon package installed correctly?"
[[ -d /var/lib/anon ]] \
  || error "/var/lib/anon missing — is the anon package installed correctly?"

# Deploy anonrc to /etc/ (not /etc/anon/)
copy_file "anonrc" "/etc/anonrc" 644

# Ensure correct ownership of anon data + log directories
chown -R debian-anon:debian-anon /var/lib/anon
mkdir -p /var/log/anon
chown debian-anon:debian-anon /var/log/anon

# Enable AND start anon now so control_auth_cookie is created
# before circuit-manager's ExecStartPre checks for it
systemctl enable anon
systemctl start anon

# Wait up to 30s for the cookie to appear
log "Waiting for control_auth_cookie (max 30s)..."
for i in $(seq 1 30); do
  [[ -f /var/lib/anon/control_auth_cookie ]] && break
  sleep 1
done

if [[ -f /var/lib/anon/control_auth_cookie ]]; then
  ok "control_auth_cookie ready (${i}s)"
else
  warn "control_auth_cookie not found after 30s — circuit-manager may fail until reboot"
fi

ok "anon configuration complete"

# =============================================================================
# 4 -- Node.js & Circuit Manager
# =============================================================================
section "4/9 - Node.js & Circuit Manager"

if ! command -v node &>/dev/null || [[ $(node --version | cut -d. -f1 | tr -d 'v') -lt 20 ]]; then
  log "Installing Node.js 20 LTS..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
ok "Node.js: $(node --version)"

mkdir -p "$CM_DIR"

copy_file "server.mjs" "$CM_DIR/server.mjs" 644

cat > "$CM_DIR/package.json" << 'PKGJSON'
{
  "name": "anyone-circuit-manager",
  "version": "1.0.0",
  "type": "module",
  "main": "server.mjs",
  "dependencies": {
    "express": "^4.18.2",
    "@anyone-protocol/anyone-client": "latest"
  }
}
PKGJSON

log "Running npm install in circuit manager directory..."
cd "$CM_DIR" && npm install --omit=dev 2>&1 | tail -5
cd - > /dev/null

# anon cache directory
mkdir -p /root/.anon-cache

# Syntax check
node --check "$CM_DIR/server.mjs" \
  && ok "server.mjs syntax OK" \
  || error "server.mjs has syntax errors!"

copy_file "anyone-stick-circuit-manager.service" \
  "$SYSTEMD_DIR/anyone-stick-circuit-manager.service" 644

# =============================================================================
# 5 -- Portal (Flask / Gunicorn)
# =============================================================================
section "5/9 - Portal (Flask / Gunicorn)"

mkdir -p "$PORTAL_DIR"
copy_file "app.py" "$PORTAL_DIR/app.py" 644

mkdir -p "$PORTAL_DIR/static"
if [[ -f "$SCRIPT_DIR/logo.png" ]]; then
  copy_file "logo.png" "$PORTAL_DIR/static/logo.png" 644
elif curl -fsSL --head "$REPO_RAW/logo.png" &>/dev/null; then
  copy_file "logo.png" "$PORTAL_DIR/static/logo.png" 644
else
  warn "logo.png not found in repo -- skipping (portal will work without it)"
fi

python3 -m venv "$PORTAL_DIR/venv"
"$PORTAL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$PORTAL_DIR/venv/bin/pip" install --quiet \
  flask "gunicorn[gthread]" requests
ok "Python dependencies installed"

ln -sf "$PORTAL_DIR/venv/bin/gunicorn" /usr/local/bin/gunicorn
ok "gunicorn symlinked -> /usr/local/bin/gunicorn"

if [[ -n "${PRIMARY_USER:-}" ]] && id "$PRIMARY_USER" >/dev/null 2>&1; then
  chown -R "$PRIMARY_USER":"$PRIMARY_USER" "$PORTAL_DIR"
  ok "Portal ownership set to ${PRIMARY_USER}:${PRIMARY_USER}"
else
  warn "Could not set portal ownership to a primary user; keeping current ownership"
fi

# =============================================================================
# 6 -- Shell Scripts
# =============================================================================
section "6/9 - Shell Scripts"

for script in \
  "mode_normal.sh" \
  "mode_privacy.sh" \
  "anyone_killswitch.sh"
do
  copy_file "$script" "$BIN_DIR/$script" 755
done

cat > "$BIN_DIR/start_anyone_stack.sh" <<'STARTSCRIPT'
#!/bin/bash
set -euo pipefail

log() { echo "[boot] $*"; }

PORTAL_DIR="/opt/anyone-stick/portal"
if [[ ! -f "$PORTAL_DIR/app.py" && -f "/home/pi/portal/app.py" ]]; then
  PORTAL_DIR="/home/pi/portal"
fi

if [[ -e /sys/class/leds/default-on/trigger ]]; then
  echo timer > /sys/class/leds/default-on/trigger 2>/dev/null || true
fi

if [[ ! -f /var/lib/anyone-stick/privacy_verified ]]; then
  if [[ -x /usr/local/bin/mode_normal.sh ]]; then
    /usr/local/bin/mode_normal.sh >/dev/null 2>&1 || true
  fi
fi

for i in $(seq 1 60); do
  ip link show usb0 &>/dev/null && break
  sleep 0.5
done

if ! ip link show usb0 &>/dev/null; then
  log "ERROR: usb0 not found after 30s"
  exit 1
fi

ip addr flush dev usb0 2>/dev/null || true
ip addr add 192.168.7.1/24 dev usb0 2>/dev/null || true
ip link set usb0 up
systemctl restart dnsmasq
log "usb0 configured and dnsmasq restarted"

if [[ ! -f "$PORTAL_DIR/app.py" ]]; then
  log "ERROR: portal app missing at $PORTAL_DIR/app.py"
  exit 1
fi

pkill -f "gunicorn.*app:app" 2>/dev/null || true
sleep 0.5

if command -v gunicorn >/dev/null 2>&1; then
  nohup gunicorn --bind 0.0.0.0:80     --workers 1     --threads 4     --timeout 0     --worker-class gthread     --chdir "$PORTAL_DIR"     app:app >/var/log/anyone-stick-gunicorn.log 2>&1 &
elif [[ -x "$PORTAL_DIR/venv/bin/python" ]]; then
  nohup "$PORTAL_DIR/venv/bin/python" "$PORTAL_DIR/app.py" >/var/log/anyone-stick-portal.log 2>&1 &
else
  nohup python3 "$PORTAL_DIR/app.py" >/var/log/anyone-stick-portal.log 2>&1 &
fi
log "portal started from $PORTAL_DIR"

for i in $(seq 1 30); do
  if nmcli -t -f STATE general 2>/dev/null | grep -q '^connected$'; then
    log "NetworkManager reports connected"
    break
  fi
  sleep 1
done

if [[ -f /var/lib/anyone-stick/privacy_verified ]]; then
  log "Restoring PRIVACY mode"
  if [[ -x /usr/local/bin/mode_privacy.sh ]]; then
    nohup /usr/local/bin/mode_privacy.sh >/var/log/anyone-stick-privacy-restore.log 2>&1 &
  fi
else
  log "Normal mode (no privacy marker)"
  if [[ -x /usr/local/bin/anyone_killswitch.sh ]]; then
    /usr/local/bin/anyone_killswitch.sh off >/dev/null 2>&1 || true
  fi
fi

exec bash -c 'trap : TERM INT; while true; do sleep 3600; done'
STARTSCRIPT
chmod 755 "$BIN_DIR/start_anyone_stack.sh"
ok "Deployed: $BIN_DIR/start_anyone_stack.sh"

mkdir -p "$STATE_DIR"
ok "State directory: $STATE_DIR"

# =============================================================================
# 7 -- dnsmasq for USB Interface
# =============================================================================
section "7/9 - dnsmasq (DHCP for USB client)"

if [[ -f /etc/dnsmasq.conf ]]; then
  sed -i 's/^#conf-dir/conf-dir/' /etc/dnsmasq.conf 2>/dev/null || true
fi

cat > /etc/dnsmasq.d/usb0.conf << 'DNSMASQ'
interface=usb0
bind-interfaces
dhcp-range=192.168.7.100,192.168.7.200,12h
dhcp-option=3,192.168.7.1
dhcp-option=6,192.168.7.1
log-queries
DNSMASQ

systemctl disable dnsmasq 2>/dev/null || true
ok "dnsmasq configured (started manually by stack script)"

# =============================================================================
# 8 -- nginx (disable -- portal binds port 80 directly via gunicorn)
# =============================================================================
section "8/9 - nginx"

if systemctl is-enabled --quiet nginx 2>/dev/null; then
  systemctl disable --now nginx
  ok "nginx disabled (portal uses port 80 directly via gunicorn)"
else
  ok "nginx was not active"
fi

# =============================================================================
# 9 -- Enable Systemd Services
# =============================================================================
section "9/9 - Enable Systemd Services"

copy_file "anyone-stick.service" "$SYSTEMD_DIR/anyone-stick.service" 644

systemctl daemon-reload

for svc in \
  usb-gadget.service \
  anon.service \
  anyone-stick-circuit-manager.service \
  anyone-stick.service
do
  systemctl enable "$svc"
  ok "Enabled: $svc"
done

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${GREEN}+==============================================+${NC}"
echo -e "${GREEN}|     Anyone Stick -- Installation complete     |${NC}"
echo -e "${GREEN}+==============================================+${NC}"
echo -e "${GREEN}|${NC}  Please reboot now:                          ${GREEN}|${NC}"
echo -e "${GREEN}|${NC}  ${YELLOW}sudo reboot${NC}                                   ${GREEN}|${NC}"
echo -e "${GREEN}|${NC}                                              ${GREEN}|${NC}"
echo -e "${GREEN}|${NC}  After reboot, verify:                       ${GREEN}|${NC}"
echo -e "${GREEN}|${NC}  ${BLUE}sudo systemctl status anyone-stick${NC}          ${GREEN}|${NC}"
echo -e "${GREEN}|${NC}  ${BLUE}curl http://192.168.7.1/api/anyone/proof${NC}     ${GREEN}|${NC}"
echo -e "${GREEN}+==============================================+${NC}"
