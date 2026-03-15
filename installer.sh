#!/bin/bash
set -euo pipefail

# =============================================================================
# Anyone Stick -- Installer
# Target: Raspberry Pi Zero 2W | Raspberry Pi OS Lite 64-bit (Bookworm)
# Usage:  sudo bash installer.sh
# Prereq: Pi is connected to WiFi with internet access
# =============================================================================

REPO_RAW="https://raw.githubusercontent.com/robihalle/Anyone-Stick/feature/2-hops"
APP_ROOT="/opt/anyone-stick"
CM_DIR="$APP_ROOT/circuit-manager"
PORTAL_LINK="$APP_ROOT/portal"
BIN_DIR="/usr/local/bin"
SYSTEMD_DIR="/etc/systemd/system"
STATE_DIR="/var/lib/anyone-stick"
DEFAULTS_FILE="/etc/default/anyone-stick"
AUTO_REBOOT="${AUTO_REBOOT:-1}"
REBOOT_REQUIRED=0

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

copy_file() {
  local src="$1"
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

section "0/9 - Preflight Checks"

[[ $EUID -ne 0 ]] && error "Please run as root: sudo bash installer.sh"

ARCH="$(uname -m)"
log "Architecture: $ARCH"
[[ "$ARCH" != "aarch64" && "$ARCH" != "armv7l" ]] && warn "Unknown architecture -- proceed at your own risk."

ping -c1 -W5 1.1.1.1 &>/dev/null || error "No internet connection. Please configure WiFi first."
ok "Internet connection confirmed"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log "Installer directory: $SCRIPT_DIR"

PRIMARY_USER="${SUDO_USER:-}"
if [[ -z "$PRIMARY_USER" || "$PRIMARY_USER" == "root" ]]; then
  PRIMARY_USER="$(awk -F: '$3 >= 1000 && $3 < 65534 && $1 != "nobody" { print $1; exit }' /etc/passwd)"
fi
[[ -n "$PRIMARY_USER" ]] || error "Could not determine the non-root user. Create a normal user first."

USER_HOME="$(getent passwd "$PRIMARY_USER" | cut -d: -f6)"
[[ -n "$USER_HOME" && -d "$USER_HOME" ]] || error "Could not determine home directory for user $PRIMARY_USER"
PORTAL_DIR="$USER_HOME/portal"

log "Primary user: $PRIMARY_USER"
log "Portal directory: $PORTAL_DIR"

section "1/9 - System Packages"

apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates gnupg git curl wget \
  python3 python3-pip python3-venv \
  network-manager dnsmasq \
  iptables iproute2 \
  jq usbutils dnsutils netcat-openbsd
ok "Packages installed"

if systemctl is-active --quiet systemd-resolved; then
  systemctl disable --now systemd-resolved
  rm -f /etc/resolv.conf
  echo 'nameserver 1.1.1.1' > /etc/resolv.conf
  ok "systemd-resolved disabled"
fi

systemctl enable --now NetworkManager
ok "NetworkManager active"

section "2/9 - USB Gadget"

CONFIG_TXT="/boot/firmware/config.txt"
[[ ! -f "$CONFIG_TXT" ]] && CONFIG_TXT="/boot/config.txt"
CMDLINE_TXT="/boot/firmware/cmdline.txt"
[[ ! -f "$CMDLINE_TXT" ]] && CMDLINE_TXT="/boot/cmdline.txt"

[[ -f "$CONFIG_TXT" ]] || error "config.txt not found"
[[ -f "$CMDLINE_TXT" ]] || error "cmdline.txt not found"

sed -i '/^dtoverlay=dwc2$/d' "$CONFIG_TXT"
if ! grep -q '^dtoverlay=dwc2,dr_mode=peripheral$' "$CONFIG_TXT"; then
  if grep -q '^\[all\]' "$CONFIG_TXT"; then
    sed -i '/^\[all\]/a dtoverlay=dwc2,dr_mode=peripheral' "$CONFIG_TXT"
  else
    cat >> "$CONFIG_TXT" <<'DTOVERLAY'

# Anyone Stick - USB Gadget
[all]
enable_uart=1
dtoverlay=dwc2,dr_mode=peripheral
DTOVERLAY
  fi
  REBOOT_REQUIRED=1
  ok "dtoverlay=dwc2,dr_mode=peripheral configured"
else
  ok "dtoverlay=dwc2,dr_mode=peripheral already present"
fi

if ! grep -q 'modules-load=dwc2' "$CMDLINE_TXT"; then
  if grep -q 'rootwait' "$CMDLINE_TXT"; then
    sed -i 's/rootwait/modules-load=dwc2 rootwait/' "$CMDLINE_TXT"
  else
    sed -i 's/$/ modules-load=dwc2/' "$CMDLINE_TXT"
  fi
  REBOOT_REQUIRED=1
  ok "modules-load=dwc2 injected into $CMDLINE_TXT"
else
  ok "modules-load=dwc2 already present"
fi

mkdir -p /etc/modules-load.d
cat > /etc/modules-load.d/usb-gadget.conf <<'MODS'
libcomposite
MODS
ok "libcomposite added to modules-load"

copy_file "usb_gadget_setup.sh" "$BIN_DIR/usb_gadget_setup.sh" 755
copy_file "usb-gadget.service" "$SYSTEMD_DIR/usb-gadget.service" 644

section "3/9 - Anyone Protocol (anon)"

if [[ ! -f /usr/share/keyrings/anyone.gpg ]]; then
  log "Adding Anyone APT repository (official source)..."
  curl -fsSL https://deb.anyone.io/gpg.key | gpg --dearmor -o /usr/share/keyrings/anyone.gpg
fi

echo "deb [signed-by=/usr/share/keyrings/anyone.gpg] https://deb.anyone.io bookworm main" \
  > /etc/apt/sources.list.d/anyone.list

apt-get update -qq

if ! command -v anon &>/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  echo "anon anon/terms boolean true" | debconf-set-selections
  apt-get install -y anon || error "Failed to install anon."
fi
ok "anon installed: $(anon --version 2>&1 | head -1)"

id debian-anon &>/dev/null || error "debian-anon user missing - is the anon package installed correctly?"
[[ -d /var/lib/anon ]] || error "/var/lib/anon missing - is the anon package installed correctly?"

copy_file "anonrc" "/etc/anonrc" 644
mkdir -p /var/log/anon
chown -R debian-anon:debian-anon /var/lib/anon /var/log/anon

systemctl enable anon
systemctl restart anon

log "Waiting for control_auth_cookie (max 60s)..."
for i in $(seq 1 60); do
  [[ -f /var/lib/anon/control_auth_cookie ]] && break
  sleep 1
done

if [[ -f /var/lib/anon/control_auth_cookie ]]; then
  ok "control_auth_cookie ready (${i}s)"
else
  warn "control_auth_cookie not found after 60s - circuit-manager may fail until next boot"
fi

section "4/9 - Node.js & Circuit Manager"

if ! command -v node &>/dev/null || [[ $(node --version | cut -d. -f1 | tr -d 'v') -lt 20 ]]; then
  log "Installing Node.js 20 LTS..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
ok "Node.js: $(node --version)"

mkdir -p "$CM_DIR"
copy_file "server.mjs" "$CM_DIR/server.mjs" 644

cat > "$CM_DIR/package.json" <<'PKGJSON'
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
(
  cd "$CM_DIR"
  npm install --omit=dev
)

mkdir -p /root/.anon-cache
node --check "$CM_DIR/server.mjs" || error "server.mjs has syntax errors"
copy_file "anyone-stick-circuit-manager.service" "$SYSTEMD_DIR/anyone-stick-circuit-manager.service" 644
ok "Circuit manager deployed"

section "5/9 - Portal (Flask / Gunicorn)"

mkdir -p "$PORTAL_DIR" "$PORTAL_DIR/static" "$APP_ROOT"
copy_file "app.py" "$PORTAL_DIR/app.py" 644

if [[ -f "$SCRIPT_DIR/logo.png" ]]; then
  copy_file "logo.png" "$PORTAL_DIR/static/logo.png" 644
elif curl -fsSL --head "$REPO_RAW/logo.png" &>/dev/null; then
  copy_file "logo.png" "$PORTAL_DIR/static/logo.png" 644
else
  warn "logo.png not found - skipping"
fi

python3 -m venv "$PORTAL_DIR/venv"
"$PORTAL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$PORTAL_DIR/venv/bin/pip" install --quiet flask "gunicorn[gthread]" requests
ln -sf "$PORTAL_DIR/venv/bin/gunicorn" /usr/local/bin/gunicorn
ln -sfn "$PORTAL_DIR" "$PORTAL_LINK"

chown -R "$PRIMARY_USER:$PRIMARY_USER" "$PORTAL_DIR"
chown -h "$PRIMARY_USER:$PRIMARY_USER" "$PORTAL_LINK" 2>/dev/null || true

cat > "$DEFAULTS_FILE" <<EOF_DEFAULTS
ANYONE_STICK_USER="$PRIMARY_USER"
ANYONE_STICK_PORTAL_DIR="$PORTAL_LINK"
EOF_DEFAULTS
chmod 644 "$DEFAULTS_FILE"

ok "Portal deployed"

section "6/9 - Shell Scripts"

for script in \
  start_anyone_stack.sh \
  mode_normal.sh \
  mode_privacy.sh \
  anyone_killswitch.sh
  do
    copy_file "$script" "$BIN_DIR/$script" 755
  done

mkdir -p "$STATE_DIR"
ok "State directory: $STATE_DIR"

section "7/9 - dnsmasq (DHCP for USB client)"

mkdir -p /etc/dnsmasq.d
cat > /etc/dnsmasq.d/usb0.conf <<'DNSMASQ'
interface=usb0
bind-interfaces
dhcp-range=192.168.7.100,192.168.7.200,12h
dhcp-option=3,192.168.7.1
dhcp-option=6,192.168.7.1
log-queries
DNSMASQ

systemctl disable dnsmasq 2>/dev/null || true
ok "dnsmasq configured (started by anyone-stick at boot)"

section "8/9 - nginx"

if systemctl is-enabled --quiet nginx 2>/dev/null; then
  systemctl disable --now nginx
  ok "nginx disabled (portal uses port 80 directly via gunicorn)"
else
  ok "nginx was not active"
fi

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

echo ""
echo -e "${GREEN}+==============================================+${NC}"
echo -e "${GREEN}|     Anyone Stick -- Installation complete     |${NC}"
echo -e "${GREEN}+==============================================+${NC}"
echo -e "${GREEN}|${NC}  User: ${BLUE}${PRIMARY_USER}${NC}                                      ${GREEN}|${NC}"
echo -e "${GREEN}|${NC}  Portal: ${BLUE}${PORTAL_LINK}${NC}                    ${GREEN}|${NC}"
echo -e "${GREEN}+==============================================+${NC}"

if [[ "$AUTO_REBOOT" == "1" ]]; then
  if [[ "$REBOOT_REQUIRED" == "1" ]]; then
    warn "USB gadget boot settings changed. Rebooting automatically in 5 seconds..."
  else
    warn "Rebooting automatically in 5 seconds to ensure a clean first boot..."
  fi
  sleep 5
  systemctl reboot
else
  warn "AUTO_REBOOT=0 set - reboot skipped. Please run: sudo reboot"
fi
