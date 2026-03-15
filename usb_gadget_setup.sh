#!/bin/bash
set -euo pipefail

log() { echo "[usb-gadget] $*"; }

mountpoint -q /sys/kernel/config || mount -t configfs none /sys/kernel/config
modprobe libcomposite
modprobe dwc2 2>/dev/null || true

GADGET_DIR="/sys/kernel/config/usb_gadget/g1"
UDC_NAME=""

if [[ -d "$GADGET_DIR" ]]; then
  if [[ -w "$GADGET_DIR/UDC" ]]; then
    echo "" > "$GADGET_DIR/UDC" 2>/dev/null || true
  fi
  rm -rf "$GADGET_DIR"
fi

mkdir -p "$GADGET_DIR"
cd "$GADGET_DIR"

echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

SERIAL="ANYONE0002"
if [[ -r /proc/device-tree/serial-number ]]; then
  SERIAL="$(tr -d '\0' < /proc/device-tree/serial-number | tail -c 16)"
fi

mkdir -p strings/0x409
echo "$SERIAL" > strings/0x409/serialnumber
echo "Anyone Foundation" > strings/0x409/manufacturer
echo "Privacy Stick NCM" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "NCM Network" > configs/c.1/strings/0x409/configuration
echo 0x80 > configs/c.1/bmAttributes
echo 250 > configs/c.1/MaxPower

mkdir -p functions/ncm.usb0
echo "02:11:22:33:44:55" > functions/ncm.usb0/dev_addr
echo "02:11:22:33:44:56" > functions/ncm.usb0/host_addr
ln -sfn functions/ncm.usb0 configs/c.1/ncm.usb0

for i in $(seq 1 30); do
  UDC_NAME="$(ls /sys/class/udc 2>/dev/null | head -n 1 || true)"
  [[ -n "$UDC_NAME" ]] && break
  sleep 1
done

[[ -n "$UDC_NAME" ]] || { log "No UDC found after 30s - dwc2/peripheral mode not ready"; exit 1; }

echo "$UDC_NAME" > UDC
udevadm settle 2>/dev/null || true

for i in $(seq 1 20); do
  ip link show usb0 >/dev/null 2>&1 && break
  sleep 0.5
done

if ip link show usb0 >/dev/null 2>&1; then
  ip link set usb0 up || true
  log "Gadget bound to UDC $UDC_NAME; usb0 present"
else
  log "Gadget bound to UDC $UDC_NAME; usb0 not visible yet"
fi
