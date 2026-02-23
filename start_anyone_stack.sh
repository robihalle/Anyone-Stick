#!/bin/bash
# ============================================================================
# Anyone Privacy Stick Master Startup Script
# Started by anyone-stick.service AFTER usb-gadget.service
# ============================================================================

echo timer | sudo tee /sys/class/leds/default-on/trigger >/dev/null

# 0. Ensure NORMAL mode on boot (internet passthrough by default)
#    Privacy should only be enabled via the web UI button.
if [ -x /usr/local/bin/mode_normal.sh ]; then
    /usr/local/bin/mode_normal.sh || true
fi

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

# 7. Default boot behavior: NORMAL MODE (allow all traffic)
#    This ensures the host PC has internet immediately.
if [ -x /usr/local/bin/mode_normal.sh ]; then
    /usr/local/bin/mode_normal.sh >/dev/null 2>&1 || true
fi

# Ensure KillSwitch is OFF by default (user enables it explicitly)
if [ -x /usr/local/bin/anyone_killswitch.sh ]; then
    /usr/local/bin/anyone_killswitch.sh off >/dev/null 2>&1 || true
fi

# 7. Start anon (blocks — keeps service alive)
#
# IMPORTANT: avoid double-start races.
# systemd may restart this service quickly; a previous anon instance can still
# hold ports or leave a stale lock -> new anon crashes (free(): invalid pointer).
echo "[boot] Ensuring no stale anon instance is running..."
pkill -f "^/usr/local/bin/anon($|[[:space:]])" 2>/dev/null || true

# Give kernel time to release listening sockets
for i in $(seq 1 10); do
    ss -lnt 2>/dev/null | grep -Eq ":(9040|9050|9051|9053)([[:space:]]|$)" || break
    sleep 0.3
done

# Remove stale lock if present (safe when no instance running)
rm -f /var/lib/anon/lock 2>/dev/null || true

echo "[boot] Starting anon..."
exec /usr/local/bin/anon -f /etc/anonrc

