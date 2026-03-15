#!/bin/bash
set -euo pipefail

STATE_DIR="/var/lib/anyone-stick"
ENV_FILE="/etc/default/anyone-stick"
PRIV_MARKER="$STATE_DIR/privacy_verified"
PORTAL_DIR="/opt/anyone-stick/portal"

log() { echo "[boot] $*"; }
led_set() { echo "$1" > /sys/class/leds/default-on/trigger 2>/dev/null || true; }

if [[ -r "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  PORTAL_DIR="${ANYONE_STICK_PORTAL_DIR:-$PORTAL_DIR}"
fi

PORTAL_APP="$PORTAL_DIR/app.py"
mkdir -p "$STATE_DIR"
led_set timer

wait_for_if() {
  local ifname="$1" timeout="${2:-30}" elapsed=0
  while (( elapsed < timeout * 2 )); do
    if ip link show "$ifname" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
    elapsed=$((elapsed + 1))
  done
  return 1
}

if [[ -f "$PRIV_MARKER" ]]; then
  log "Privacy marker found - enabling kill switch until privacy mode is restored"
  if [[ -x /usr/local/bin/anyone_killswitch.sh ]]; then
    /usr/local/bin/anyone_killswitch.sh on >/dev/null 2>&1 || true
  fi
else
  if [[ -x /usr/local/bin/mode_normal.sh ]]; then
    /usr/local/bin/mode_normal.sh >/dev/null 2>&1 || true
  fi
fi

wait_for_if usb0 30 || { log "ERROR: usb0 not found after 30s"; exit 1; }

ip addr flush dev usb0 2>/dev/null || true
ip addr replace 192.168.7.1/24 dev usb0
ip link set usb0 up
systemctl restart dnsmasq
log "usb0 configured and dnsmasq restarted"

(
  for i in $(seq 1 60); do
    if nmcli -t -f STATE general 2>/dev/null | grep -q '^connected$'; then
      log "NetworkManager reports connected"
      break
    fi
    sleep 1
  done

  if [[ -f "$PRIV_MARKER" ]]; then
    log "Restoring privacy mode in background..."
    if [[ -x /usr/local/bin/mode_privacy.sh ]]; then
      /usr/local/bin/mode_privacy.sh >> /var/log/anyone-stick-privacy.log 2>&1 || log "mode_privacy.sh failed"
    else
      log "mode_privacy.sh not found; leaving kill switch enabled"
    fi
  else
    if [[ -x /usr/local/bin/anyone_killswitch.sh ]]; then
      /usr/local/bin/anyone_killswitch.sh off >/dev/null 2>&1 || true
    fi
  fi
) &

[[ -f "$PORTAL_APP" ]] || { log "ERROR: portal app missing at $PORTAL_APP"; exit 1; }

if command -v gunicorn >/dev/null 2>&1; then
  exec gunicorn \
    --bind 0.0.0.0:80 \
    --workers 1 \
    --threads 4 \
    --timeout 0 \
    --worker-class gthread \
    --chdir "$PORTAL_DIR" \
    app:app
else
  exec python3 "$PORTAL_APP"
fi
