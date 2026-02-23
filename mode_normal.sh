#!/bin/bash
# Anyone Privacy Stick – Normal Mode
# Internet directly via wlan0 (no tunnel)
set -euo pipefail
# --- reset ipv6tables (usb0) ---
ip6tables -P INPUT ACCEPT >/dev/null 2>&1 || true
ip6tables -P OUTPUT ACCEPT >/dev/null 2>&1 || true
ip6tables -P FORWARD ACCEPT >/dev/null 2>&1 || true
ip6tables -F >/dev/null 2>&1 || true
ip6tables -t nat -F >/dev/null 2>&1 || true


IN_IF="usb0"
OUT_IF="wlan0"
PI_IP="192.168.7.1"
KS="/usr/local/bin/anyone_killswitch.sh"

# Ensure Kill Switch is OFF in normal mode (otherwise FORWARD/OUTPUT can be blocked)
if [ -x "$KS" ]; then
  "$KS" off || true
fi

# Reset IPv4 firewall
iptables -F
iptables -t nat -F
iptables -t mangle -F

# Reset IPv6 firewall as well (best effort)
ip6tables -F 2>/dev/null || true
ip6tables -t nat -F 2>/dev/null || true
ip6tables -t mangle -F 2>/dev/null || true

# Default policies (open)
iptables -P INPUT ACCEPT
iptables -P OUTPUT ACCEPT
iptables -P FORWARD ACCEPT
ip6tables -P INPUT ACCEPT 2>/dev/null || true
ip6tables -P OUTPUT ACCEPT 2>/dev/null || true
ip6tables -P FORWARD ACCEPT 2>/dev/null || true

# Enable forwarding
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Re-enable IPv6 (if kernel allows it)
sysctl -w net.ipv6.conf.all.disable_ipv6=0 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.default.disable_ipv6=0 >/dev/null 2>&1 || true
sysctl -w net.ipv6.conf.lo.disable_ipv6=0 >/dev/null 2>&1 || true

# Disable route_localnet (was enabled for privacy-mode DNAT to 127.0.0.1)
sysctl -w net.ipv4.conf.usb0.route_localnet=0 >/dev/null 2>&1 || true
sysctl -w net.ipv4.conf.all.route_localnet=0 >/dev/null 2>&1 || true

# NAT for internet access
iptables -t nat -A POSTROUTING -o "$OUT_IF" -j MASQUERADE

# Allow forwarding
iptables -A FORWARD -i "$IN_IF" -o "$OUT_IF" -j ACCEPT
iptables -A FORWARD -i "$OUT_IF" -o "$IN_IF" -m state --state RELATED,ESTABLISHED -j ACCEPT

# TCP MSS clamp
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# Remove Kill Switch marker
rm -f /var/lib/anyone-stick/killswitch_active || true

# Ensure kernel-level KillSwitch is OFF
/usr/local/bin/anyone_killswitch.sh off >/dev/null 2>&1 || true 2>/dev/null || true

# LED solid (best effort)
echo default-on | tee /sys/class/leds/default-on/trigger >/dev/null 2>&1 || true

echo "OK: Normal Mode enabled (direct internet)."
