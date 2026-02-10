#!/bin/bash
# ============================================================================
# Anyone Privacy Stick – Master Startup Script
# Wird von anyone-stick.service gestartet, NACHDEM usb-gadget.service lief.
# ============================================================================

echo timer | sudo tee /sys/class/leds/default-on/trigger >/dev/null

# State Directory sicherstellen
mkdir -p /var/lib/anyone-stick

# ── 1. Warten auf usb0 (max 5s) ──────────────────────────────────────
for i in $(seq 1 10); do
    ip link show usb0 &>/dev/null && break
    sleep 0.5
done

if ! ip link show usb0 &>/dev/null; then
    echo "FEHLER: usb0 nicht gefunden nach 5s – USB Gadget defekt?"
    exit 1
fi

# ── 2. Statische IP setzen (SOFORT – Windows wartet auf DHCP!) ───────
ip addr flush dev usb0 2>/dev/null
ip addr add 192.168.7.1/24 dev usb0
ip link set usb0 up

# Kurz warten bis IP wirklich gebunden
sleep 0.5

# ── 3. dnsmasq starten (DHCP für den Host-PC) ────────────────────────
# dnsmasq ist beim Boot disabled, wird hier manuell gestartet,
# damit usb0 + IP garantiert existieren.
systemctl restart dnsmasq

# ── 4. Portal starten (sofort erreichbar unter 192.168.7.1) ──────────
python3 /home/pi/portal/app.py &

# ── 5. WLAN im Hintergrund verbinden (blockiert nicht) ────────────────
nmcli con up "Stick-Gateway" 2>/dev/null &

# ── 6. Auf WLAN warten (max 30s) ─────────────────────────────────────
for i in $(seq 1 30); do
    nmcli -t -f STATE general 2>/dev/null | grep -q "connected" && break
    sleep 1
done

# ── 7. Anon starten (blockiert – hält den Service am Leben) ──────────
/usr/local/bin/anon -f /etc/anonrc
