#!/bin/bash
echo timer | sudo tee /sys/class/leds/default-on/trigger

# USB-Gadget ist bereits über usb-gadget.service aktiv.
# Kurz warten bis usb0 wirklich da ist (max 5s statt pauschaler sleep 5).
for i in $(seq 1 10); do
    ip link show usb0 &>/dev/null && break
    sleep 0.5
done

# WLAN aktivieren (im Hintergrund, blockiert nicht)
nmcli con up "Stick-Gateway" &

# DHCP sofort starten, damit der Host eine IP bekommt
systemctl restart dnsmasq

# Portal sofort starten – der Host kann das Dashboard bereits erreichen,
# auch wenn WLAN und Anon noch nicht bereit sind.
python3 /home/pi/portal/app.py &

# Aktiv auf WLAN-Verbindung warten (max 30s statt pauschaler sleep 25)
for i in $(seq 1 30); do
    nmcli -t -f STATE general 2>/dev/null | grep -q "connected" && break
    sleep 1
done

# Anon-Stack starten
/usr/local/bin/anon -f /etc/anonrc
