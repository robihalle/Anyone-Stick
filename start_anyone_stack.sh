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
  nohup gunicorn \
    --bind 0.0.0.0:80 \
    --workers 1 \
    --threads 4 \
    --timeout 0 \
    --worker-class gthread \
    --chdir "$PORTAL_DIR" \
    app:app >/var/log/anyone-stick-gunicorn.log 2>&1 &
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
