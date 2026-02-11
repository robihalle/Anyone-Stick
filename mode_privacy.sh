#!/bin/bash
# Anyone Privacy Stick – Privacy Mode (NO-LEAK + Portal always reachable)
set -euo pipefail

IN_IF="usb0"
PI_IP="192.168.7.1"
TRANS_PORT="9040"
DNS_PORT="9053"

# Reset
iptables -F
iptables -t nat -F
iptables -t mangle -F

# Privacy = no-leak forwarding
iptables -P FORWARD DROP
iptables -P INPUT ACCEPT
iptables -P OUTPUT ACCEPT

# IPv6 off
sysctl -w net.ipv4.ip_forward=1 >/dev/null
sysctl -w net.ipv6.conf.all.disable_ipv6=1 2>/dev/null || true
sysctl -w net.ipv6.conf.default.disable_ipv6=1 2>/dev/null || true
sysctl -w net.ipv6.conf.lo.disable_ipv6=1 2>/dev/null || true

# Exempt ONLY traffic destined to the Pi itself (portal + local services)
iptables -t nat -A PREROUTING -i "$IN_IF" -d "$PI_IP" -j RETURN

# Force DNS to anon DNSPort
iptables -t nat -A PREROUTING -i "$IN_IF" -p udp --dport 53 -j REDIRECT --to-ports "$DNS_PORT"
iptables -t nat -A PREROUTING -i "$IN_IF" -p tcp --dport 53 -j REDIRECT --to-ports "$DNS_PORT"

# Force ALL TCP to anon TransPort (no port-80 exemption!)
iptables -t nat -A PREROUTING -i "$IN_IF" -p tcp -j REDIRECT --to-ports "$TRANS_PORT"

# Prevent UDP leaks (QUIC/WebRTC etc.) – DNS is redirected anyway
iptables -A FORWARD -i "$IN_IF" -p udp -j DROP

# Optional MSS clamp
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

mkdir -p /var/lib/anyone-stick
touch /var/lib/anyone-stick/killswitch_active
echo heartbeat | tee /sys/class/leds/default-on/trigger >/dev/null 2>&1 || true
