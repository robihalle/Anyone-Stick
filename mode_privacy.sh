#!/bin/bash
# Anyone Privacy Stick – Privacy Mode (Kill Switch enabled)

# 1. Firewall Reset
iptables -F
iptables -t nat -F
iptables -t mangle -F

# 2. Forwarding & IPv6 Kill
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.disable_ipv6=1
sysctl -w net.ipv6.conf.default.disable_ipv6=1
sysctl -w net.ipv6.conf.lo.disable_ipv6=1

# 3. DEFAULT POLICY: DROP everything (Kill Switch)
iptables -P FORWARD DROP

# 4. LOKALE AUSNAHMEN (Portal muss IMMER gehen)
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 80 -j RETURN
iptables -t nat -A PREROUTING -i usb0 -d 192.168.7.1 -j RETURN

# 5. DNS-FIX: Umleitung von 53 auf 9053 (Anyone DNS)
iptables -t nat -A PREROUTING -i usb0 -p udp --dport 53 -j REDIRECT --to-ports 9053
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 53 -j REDIRECT --to-ports 9053

# 6. TRANSPARENT PROXY: Alle anderen TCP-Anfragen in den Tunnel (9040)
iptables -t nat -A PREROUTING -i usb0 -p tcp --syn -j REDIRECT --to-ports 9040

# 7. FORWARD: Nur established/related traffic erlauben (Kill Switch)
iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT

# 8. ROUTING & MTU
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# 9. Block all non-tunnel UDP (except DNS redirect) – prevents UDP leaks
iptables -A FORWARD -i usb0 -p udp --dport 53 -j ACCEPT
iptables -A FORWARD -i usb0 -p udp -j DROP

# 10. MAC Randomization (if enabled)
if [ -f /var/lib/anyone-stick/mac_random_enabled ]; then
    ip link set wlan0 down
    macchanger -r wlan0 2>/dev/null || true
    ip link set wlan0 up
fi

# 11. Kill Switch marker
touch /var/lib/anyone-stick/killswitch_active

# 12. LED & Status
echo heartbeat | sudo tee /sys/class/leds/default-on/trigger
