#!/bin/bash
# Anyone Privacy Stick – Normal Mode
# Internet directly via wlan0 (no tunnel)

set -euo pipefail

IN_IF="usb0"
OUT_IF="wlan0"
PI_IP="192.168.7.1"

# Reset firewall
iptables -F
iptables -t nat -F
iptables -t mangle -F

# Default policies
iptables -P INPUT ACCEPT
iptables -P OUTPUT ACCEPT
iptables -P FORWARD ACCEPT

# Enable forwarding
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Re-enable IPv6 (if kernel allows it)
sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null || true
sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null || true
sysctl -w net.ipv6.conf.lo.disable_ipv6=0 >/dev/null || true

# NAT for internet access
iptables -t nat -A POSTROUTING -o "$OUT_IF" -j MASQUERADE

# Allow forwarding
iptables -A FORWARD -i "$IN_IF" -o "$OUT_IF" -j ACCEPT
iptables -A FORWARD -i "$OUT_IF" -o "$IN_IF" -m state --state RELATED,ESTABLISHED -j ACCEPT

# TCP MSS clamp
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Remove Kill Switch marker
rm -f /var/lib/anyone-stick/killswitch_active || true

# LED solid (best effort)
echo default-on | tee /sys/class/leds/default-on/trigger >/dev/null 2>&1 || true

echo "OK: Normal Mode enabled (direct internet)."
