#!/bin/bash
# 1. Firewall Reset
iptables -F
iptables -t nat -F
iptables -t mangle -F

# 2. Forwarding & IPv6 Kill
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.disable_ipv6=1

# 3. LOKALE AUSNAHMEN (Portal muss IMMER gehen)
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 80 -j RETURN
iptables -t nat -A PREROUTING -i usb0 -d 192.168.7.1 -j RETURN

# 4. DNS-FIX: Umleitung von 53 auf 9053 (Anyone DNS)
# Wir erzwingen hier sowohl UDP als auch TCP
iptables -t nat -A PREROUTING -i usb0 -p udp --dport 53 -j REDIRECT --to-ports 9053
iptables -t nat -A PREROUTING -i usb0 -p tcp --dport 53 -j REDIRECT --to-ports 9053

# 5. TRANSPARENT PROXY: Alle anderen TCP-Anfragen in den Tunnel (9040)
iptables -t nat -A PREROUTING -i usb0 -p tcp --syn -j REDIRECT --to-ports 9040

# 6. ROUTING & MTU
iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu

# 7. LED & Status
echo heartbeat | sudo tee /sys/class/leds/default-on/trigger
