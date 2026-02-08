#!/bin/bash
echo timer | sudo tee /sys/class/leds/default-on/trigger
/usr/local/bin/usb_gadget_setup.sh
sleep 5
nmcli con up "Stick-Gateway" || true
systemctl restart dnsmasq
python3 /home/pi/portal/app.py &
sleep 25
/usr/local/bin/anon -f /etc/anonrc
