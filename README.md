# Anyone Privacy Stick (Pi Zero 2 W)

## Overview
The Pi Zero 2 W acts as a **USB privacy stick** with an integrated web portal. Using a USB gadget (NCM), it exposes a network interface (`usb0`) to the connected host and can switch between a normal NAT mode and a privacy mode that transparently routes all TCP traffic through the `anon` stack.

### Key Features
- **USB Gadget (NCM)**: Creates the `usb0` network interface for the host.
- **Web Portal (Flask)**: Status view, traffic stats, Wi-Fi scan/connect, and mode switch.
- **Normal Mode**: Standard NAT from `usb0` to `wlan0`.
- **Privacy Mode**: DNS redirection to port `9053` and transparent TCP proxy on port `9040`.

## Dependencies
### System Packages (APT)
- `python3-flask` (web portal)
- `dnsmasq` (DHCP/DNS for `usb0`)
- `network-manager` (Wi-Fi management via `nmcli`)
- `iptables-persistent` (iptables rules)
- `curl` (download the `anon` client)
- `unzip` (extract the `anon` client)
- `jq` (installed, not currently used directly)

### Services & Tools
- `systemd` (service `anyone-stick`)
- `dnsmasq` (restarted at boot)
- `nmcli` / `NetworkManager`
- `iptables`
- `sysctl`

### Kernel / USB Gadget
- Kernel modules: `dwc2`, `libcomposite`

### Application
- `anon` (installed as a binary from GitHub releases)

## Ports / Network Logic
- **DNS**: UDP/TCP port `9053` (redirected in privacy mode)
- **Transparent Proxy**: TCP port `9040`
- **Portal**: HTTP port `80`

## Anyone SDK Circuit Configuration (Portal)
The portal includes an "Anyone Circuit" section that allows users to select a preferred exit country for their circuit. The current selection is stored locally in `/etc/anyone-circuit.json` via the `/api/circuit` endpoint. This is intended to be wired into the Anyone SDK so circuit settings can be applied dynamically.

## Startup Flow
The `anyone-stick` service runs the startup script on boot, which:
1. Initializes the USB gadget
2. Activates the `Stick-Gateway` connection
3. Restarts `dnsmasq`
4. Starts the Flask portal
5. Starts the `anon` stack

---

> Note: Details for setup and configuration live in the shell scripts (`start_anyone_stack.sh`, `mode_privacy.sh`, `mode_normal.sh`, `usb_gadget_setup.sh`).
