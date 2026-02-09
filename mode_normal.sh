#!/bin/bash
# Anyone Privacy Stick – Normal Mode (simple NAT, no tunnel)

iptables -F
iptables -t nat -F
iptables -t mangle -F

# Default policy: ACCEPT (no kill switch)
iptables -P FORWARD ACCEPT

# Simple NAT without tunnel
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Re-enable IPv6
sysctl -w net.ipv6.conf.all.disable_ipv6=0
sysctl -w net.ipv6.conf.default.disable_ipv6=0

# Remove exit country selection from config
sed -i '/^ExitNodes/d' /etc/anonrc
sed -i '/^StrictNodes/d' /etc/anonrc

# Remove Kill Switch marker
rm -f /var/lib/anyone-stick/killswitch_active

# LED solid
echo default-on | sudo tee /sys/class/leds/default-on/trigger
