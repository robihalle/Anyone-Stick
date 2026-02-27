#!/bin/bash
set -euo pipefail

# =============================================================================
# Anyone Stick — Installer
# Target: Raspberry Pi Zero 2W | Raspberry Pi OS Lite 64-bit (Bookworm)
# Usage:  sudo bash install.sh
# Prereq: Pi is connected to WiFi with internet access
# =============================================================================

REPO_RAW="https://raw.githubusercontent.com/robihalle/Anyone-Stick/feature/2-hops"

CM_DIR="/opt/anyone-stick/circuit-manager"
PORTAL_DIR="/home/pi/portal"
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
section() { echo -e "\n${BLUE}══════════════════════════════════════${NC}"; \
            echo -e "${BLUE}  $*${NC}"; \
            echo -e "${BLUE}══════════════════════════════════════${NC}"; }

# =============================================================================
# 0 — Preflight
# =============================================================================
section "0/9 · Preflight Checks"

[[ $EUID -ne 0 ]] && error "Please run as root: sudo bash install.sh"

ARCH=$(uname -m)
log "Architecture: $ARCH"
[[ "$ARCH" != "aarch64" && "$ARCH" != "armv7l" ]] && warn "Unknown architecture — proceed at your own risk."

ping -c1 -W5 1.1.1.1 &>/dev/null || error "No internet connection. Please configure WiFi first."
ok "Internet connection confirmed"

# Script lives in repo root — reference files relative to it
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log "Installer directory: $SCRIPT_DIR"

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
# 1 — System Packages
# =============================================================================
section "1/9 · System Packages"

apt-get update -qq
apt-get install -y --no-install-recommends \
  git curl wget \
  python3 python3-pip python3-venv \
  network-manager dnsmasq \
  iptables ip6tables iproute2 \
  jq usbutils dnsutils \
  libcomposite
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

# --- config.txt: dtoverlay=dwc2 ---
# Enables the OTG hardware port — do NOT overwrite the file (it contains board-specific settings)
if ! grep -q 'dtoverlay=dwc2' "$CONFIG_TXT"; then
  echo '' >> "$CONFIG_TXT"
  echo '# Anyone Stick — USB Gadget' >> "$CONFIG_TXT"
  echo 'dtoverlay=dwc2' >> "$CONFIG_TXT"
  ok "dtoverlay=dwc2 appended to $CONFIG_TXT"
else
  ok "dtoverlay=dwc2 already present"
fi

# --- cmdline.txt: modules-load=dwc2 ---
# IMPORTANT: cmdline.txt must remain a single line — never append a newline!
# Do NOT replace this file: it contains the root PARTUUID which is unique per SD card.
CMDLINE_TXT="/boot/firmware/cmdline.txt"
[[ ! -f "$CMDLINE_TXT" ]] && CMDLINE_TXT="/boot/cmdline.txt"

if ! grep -q 'modules-load=dwc2' "$CMDLINE_TXT"; then
  # Insert before 'rootwait' if present, otherwise append at end of line
  if grep -q 'rootwait' "$CMDLINE_TXT"; then
    sed -i 's/rootwait/modules-load=dwc2 rootwait/' "$CMDLINE_TXT"
  else
    sed -i 's/$/ modules-load=dwc2/' "$CMDLINE_TXT"
  fi
  ok "modules-load=dwc2 injected into $CMDLINE_TXT"
else
  ok "modules-load=dwc2 already present"
fi

# Load libcomposite at boot (required for configfs USB gadget via usb_gadget_setup.sh)
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

if ! command -v anon &>/dev/null; then
  log "Adding Anyone repository..."
  curl -fsSL https://deb.anyone.io/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/anyone.gpg
  echo "deb [signed-by=/usr/share/keyrings/anyone.gpg] https://deb.anyone.io bookworm main" \
    > /etc/apt/sources.list.d/anyone.list
  apt-get update -qq
  apt-get install -y anon
  ok "anon installed: $(anon --version 2>&1 | head -1)"
else
  ok "anon already installed: $(anon --version 2>&1 | head -1)"
fi

# Verify debian-anon user and /var/lib/anon exist (created by package postscript)
id debian-anon &>/dev/null || error "debian-anon user missing — is the anon package installed correctly?"
[[ -d /var/lib/anon ]] || error "/var/lib/anon missing — is the anon package installed correctly?"

# Deploy anonrc
mkdir -p /etc/anon
copy_file "anonrc" "/etc/anon/anonrc" 644

mkdir -p /var/log/anon
chown debian-anon:debian-anon /var/log/anon

# Enable anon at boot — required so control_auth_cookie exists when circuit-manager starts
systemctl enable anon
ok "anon configuration complete"

# =============================================================================
# 4 — Node.js & Circuit Manager
# =============================================================================
section "4/9 · Node.js & Circuit Manager"

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

# anon cache directory (used by ExecStartPre in service)
mkdir -p /root/.anon-cache

# Syntax check
node --check "$CM_DIR/server.mjs" \
  && ok "server.mjs syntax OK" \
  || error "server.mjs has syntax errors!"

copy_file "anyone-stick-circuit-manager.service" \
  "$SYSTEMD_DIR/anyone-stick-circuit-manager.service" 644

# =============================================================================
# 5 — Portal (Flask / Gunicorn)
# =============================================================================
section "5/9 · Portal (Flask / Gunicorn)"

mkdir -p "$PORTAL_DIR"
copy_file "app.py" "$PORTAL_DIR/app.py" 644

# Optional: deploy static folder if present in repo
[[ -d "$SCRIPT_DIR/static" ]] && cp -r "$SCRIPT_DIR/static" "$PORTAL_DIR/"

# Ensure static dir exists before deploying logo (optional — warn only if missing)
mkdir -p "$PORTAL_DIR/static"
if [[ -f "$SCRIPT_DIR/logo.png" ]]; then
  copy_file "logo.png" "$PORTAL_DIR/static/logo.png" 644
elif curl -fsSL --head "$REPO_RAW/logo.png" &>/dev/null; then
  copy_file "logo.png" "$PORTAL_DIR/static/logo.png" 644
else
  warn "logo.png not found in repo — skipping (portal will work without it)"
fi

python3 -m venv "$PORTAL_DIR/venv"
"$PORTAL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$PORTAL_DIR/venv/bin/pip" install --quiet \
  flask "gunicorn[gthread]" requests
ok "Python dependencies installed"

# Symlink venv's gunicorn into system PATH so start_anyone_stack.sh finds it
# (start_anyone_stack.sh uses 'command -v gunicorn' which checks system PATH)
ln -sf "$PORTAL_DIR/venv/bin/gunicorn" /usr/local/bin/gunicorn
ok "gunicorn symlinked → /usr/local/bin/gunicorn"

chown -R pi:pi "$PORTAL_DIR"

# =============================================================================
# 6 — Shell Scripts
# =============================================================================
section "6/9 · Shell Scripts"

for script in \
  "start_anyone_stack.sh" \
  "mode_normal.sh" \
  "mode_privacy.sh" \
  "anyone_killswitch.sh"
do
  copy_file "$script" "$BIN_DIR/$script" 755
done

# State directory for privacy marker, killswitch flag etc.
mkdir -p "$STATE_DIR"
ok "State directory: $STATE_DIR"

# =============================================================================
# 7 — dnsmasq for USB Interface
# =============================================================================
section "7/9 · dnsmasq (DHCP for USB client)"

# Enable conf-dir inclusion in main config to avoid conflicts
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

# dnsmasq is managed by start_anyone_stack.sh — do not autostart
systemctl disable dnsmasq 2>/dev/null || true
ok "dnsmasq configured (started manually by stack script)"

# =============================================================================
# 8 — nginx (disable — portal binds port 80 directly via gunicorn)
# =============================================================================
section "8/9 · nginx"

if systemctl is-enabled --quiet nginx 2>/dev/null; then
  systemctl disable --now nginx
  ok "nginx disabled (portal uses port 80 directly via gunicorn)"
else
  ok "nginx was not active"
fi

# =============================================================================
# 9 — Enable Systemd Services
# =============================================================================
section "9/9 · Enable Systemd Services"

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
echo -e "${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Anyone Stick — Installation complete     ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  Please reboot now:                          ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  ${YELLOW}sudo reboot${NC}                                   ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                              ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  After reboot, verify:                       ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  ${BLUE}sudo systemctl status anyone-stick${NC}          ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  ${BLUE}curl http://192.168.7.1/api/anyone/proof${NC}     ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════╝${NC}"
