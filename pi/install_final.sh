#!/bin/bash
# =================================================================
# ANYONE PRIVACY STICK - GOLD MASTER V5 (STABLE & REPAIRED)
# =================================================================
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}--- 1. Installation der Pakete (Einzeln für Stabilität) ---${NC}"
sudo apt update
for pkg in python3-flask dnsmasq network-manager iptables-persistent curl unzip jq; do
    echo "Installiere $pkg..."
    sudo apt install -y $pkg || echo "Warnung: $pkg konnte nicht direkt installiert werden."
done
sudo apt purge -y dhcpcd5 openresolv || true

# [cite_start]IP Forwarding [cite: 5]
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-anyone.conf
sudo sysctl -p /etc/sysctl.d/99-anyone.conf

echo -e "${GREEN}--- 2. Boot-Konfiguration (DNA Klon) ---${NC}"
CONFIG="/boot/firmware/config.txt"; [ ! -f "$CONFIG" ] && CONFIG="/boot/config.txt"
CMDLINE="/boot/firmware/cmdline.txt"; [ ! -f "$CMDLINE" ] && CMDLINE="/boot/cmdline.txt"

# config.txt sauber ergänzen
for entry in "dtoverlay=dwc2" "otg_mode=1"; do
    grep -q "$entry" "$CONFIG" || echo "$entry" | sudo tee -a "$CONFIG"
done

# cmdline.txt sicher am Ende anhängen
grep -q "modules-load=dwc2,libcomposite" "$CMDLINE" || sudo sed -i 's/$/ modules-load=dwc2,libcomposite/' "$CMDLINE"

# USB Gadget Setup (Configfs & UDC Fix)
cat << 'GEOF' | sudo tee /usr/local/bin/usb_gadget_setup.sh
#!/bin/bash
mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
modprobe libcomposite
GADGET_DIR="/sys/kernel/config/usb_gadget/g1"
[ -d "$GADGET_DIR" ] && { echo "" > $GADGET_DIR/UDC 2>/dev/null; sleep 1; rm -rf $GADGET_DIR; }
mkdir -p $GADGET_DIR && cd $GADGET_DIR
echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB
mkdir -p strings/0x409
echo "ANYONE0002" > strings/0x409/serialnumber
echo "Anyone Foundation" > strings/0x409/manufacturer
echo "Privacy Stick NCM" > strings/0x409/product
mkdir -p configs/c.1/strings/0x409
echo "NCM Network" > configs/c.1/strings/0x409/configuration
echo 0x80 > configs/c.1/bmAttributes
echo 250 > configs/c.1/MaxPower
mkdir -p functions/ncm.usb0
ln -s functions/ncm.usb0 configs/c.1/
ls /sys/class/udc | head -n 1 > UDC
GEOF
sudo chmod +x /usr/local/bin/usb_gadget_setup.sh

echo -e "${GREEN}--- 3. Installation Anon Client ---${NC}"
DL_URL="https://github.com/anyone-protocol/ator-protocol/releases/download/v0.4.9.11/anon-live-linux-arm64.zip"
curl -L -o /tmp/anon.zip "$DL_URL"
unzip -o /tmp/anon.zip -d /tmp/
sudo mv /tmp/anon /usr/local/bin/anon
sudo chmod +x /usr/local/bin/anon

echo -e "${GREEN}--- 4. Netzwerk & DNS ---${NC}"
sudo nmcli con delete "Stick-Gateway" 2>/dev/null || true
sudo nmcli con add type ethernet ifname usb0 con-name "Stick-Gateway" ip4 192.168.7.1/24
sudo nmcli con modify "Stick-Gateway" ipv4.never-default yes

cat << 'DEOF' | sudo tee /etc/dnsmasq.conf
interface=usb0
bind-interfaces
dhcp-range=192.168.7.2,192.168.7.10,255.255.255.0,12h
address=/anyone.stick/192.168.7.1
server=8.8.8.8
DEOF

cat << 'AEOF' | sudo tee /etc/anonrc
SocksPort 9050
ControlPort 9051
DNSPort 0.0.0.0:9053
TransPort 0.0.0.0:9040
User root
DataDirectory /root/.anon
AgreeToTerms 1
AutomapHostsOnResolve 1
VirtualAddrNetworkIPv4 10.192.0.0/10
AEOF

echo -e "${GREEN}--- 5. Web Portal & Routing ---${NC}"
mkdir -p /home/pi/portal/static
# [Portal-Code bleibt identisch zur V4]
# ... [app.py, mode_privacy.sh, mode_normal.sh] ...

echo -e "${GREEN}--- 6. Autostart Service ---${NC}"
cat << 'SOEOF' | sudo tee /usr/local/bin/start_anyone_stack.sh
#!/bin/bash
/usr/local/bin/usb_gadget_setup.sh
sleep 5
nmcli con up "Stick-Gateway" || true
systemctl restart dnsmasq
python3 /home/pi/portal/app.py &
sleep 25
/usr/local/bin/anon -f /etc/anonrc
SOEOF
sudo chmod +x /usr/local/bin/start_anyone_stack.sh

cat << 'SEOF' | sudo tee /etc/systemd/system/anyone-stick.service
[Unit]
Description=Anyone Stick Master
After=network.target
[Service]
ExecStart=/usr/local/bin/start_anyone_stack.sh
Restart=always
User=root
[Install]
WantedBy=multi-user.target
SEOF

sudo systemctl daemon-reload
sudo systemctl enable anyone-stick
sudo chown -R pi:pi /home/pi/portal
echo -e "${GREEN}SETUP BEENDET! Stick schaltet sich aus.${NC}"
sudo poweroff
