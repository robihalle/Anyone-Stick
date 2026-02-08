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
