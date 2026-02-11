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
