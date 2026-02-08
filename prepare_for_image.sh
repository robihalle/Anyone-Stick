#!/bin/bash
echo "Starte finale Systemreinigung für Gold Master..."

# 1. WLAN-Profile neutralisieren (außer dem USB-Gateway)
for con in $(nmcli -t -f NAME con show); do
    if [ "$con" != "Stick-Gateway" ] && [ "$con" != "lo" ]; then
        echo "Lösche Profil: $con"
        sudo nmcli con delete "$con"
    fi
done

# 2. Hostname auf Standard setzen
sudo hostnamectl set-hostname anyone-stick

# 3. Logs, Cache und temporäre Daten leeren
sudo systemctl stop anyone-stick 2>/dev/null
sudo rm -rf /var/log/*.log
sudo rm -rf /var/log/journal/*
sudo rm -rf /root/.anon/cached-*
sudo apt-get clean

# 4. Bash-History für alle User (pi & root) leeren
cat /dev/null > ~/.bash_history
history -c

# 5. Dateisystem-Sync und harter Shutdown
echo "Bereinigung abgeschlossen. Der Pi schaltet sich jetzt aus..."
sudo sync && sudo poweroff -f
