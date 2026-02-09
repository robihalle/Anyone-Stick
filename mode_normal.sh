#!/bin/bash
iptables -F
iptables -t nat -F
iptables -t mangle -F

# Einfaches NAT ohne Tunnel
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Exit-Country-Auswahl zurücksetzen
sed -i '/^ExitNodes/d' /etc/anonrc
sed -i '/^StrictNodes/d' /etc/anonrc

# Anon-Config neu laden (falls Prozess läuft)
pkill -SIGHUP -x anon 2>/dev/null || true

# LED: dauerhaft an = Normal Mode
echo default-on | sudo tee /sys/class/leds/default-on/trigger
