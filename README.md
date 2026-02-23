# Anyone Privacy Stick (Pi Zero 2 W)

> Turn a Raspberry Pi Zero 2 W into a plug-and-play USB privacy stick that routes all traffic through the Anyone network.

## Overview

The Pi Zero 2 W acts as a **USB privacy stick** with an integrated web portal. Using a USB gadget (NCM), it exposes a network interface (`usb0`) to the connected host and can switch between a normal NAT mode and a privacy mode that transparently routes all TCP traffic through the `anon` stack.

### Key Features

| Feature | Description |
|---|---|
| **USB Gadget (NCM)** | Creates the `usb0` network interface for the host |
| **Web Portal (Flask)** | Status view, traffic stats, Wi-Fi scan/connect, mode switch, exit country selection, circuit chain visualization |
| **Normal Mode** | Standard NAT from `usb0` → `wlan0` |
| **Privacy Mode** | Transparent TCP proxy (port `9040`) + DNS redirect (port `9053`) through `anon` |
| **Kill Switch** | `FORWARD DROP` policy – no traffic leaks if the tunnel goes down |
| **MAC Randomization** | Optional random MAC address on `wlan0` via `macchanger` |
| **Stream Isolation** | `IsolateDestAddr` / `IsolateDestPort` on SOCKS & DNS ports |
| **DNS Leak Protection** | All DNS forced through the tunnel, UDP leak prevention |
| **Exit Country Selection** | Choose exit node country via the web portal (delegated to Node circuit-manager) |
| **2-Hop / 3-Hop Mode** | Switch between 2-hop and 3-hop circuits via the dashboard |
| **Circuit Rotation** | Configurable automatic circuit rotation with interval and variance settings |
| **New Identity (NewNym)** | Request a new circuit identity on demand |
| **Circuit Chain Visualization** | Real-time display of all relay hops (role, flag, nickname, IP, country) |
| **Anyone Proof Check** | Verifies connectivity through the Anyone network via SOCKS5 |
| **Privacy Status Dashboard** | Real-time checks for Kill Switch, DNS, IPv6, Stream Isolation |

---

## Hardware Requirements

- **Raspberry Pi Zero 2 W** (512 MB RAM, aarch64)
- **microSD card** (8 GB minimum, 16 GB recommended)
- **USB-A to Micro-USB cable** (data cable, not charge-only)
- A host computer (Linux, macOS, or Windows)

---

## Installation

### Prerequisites

1. **Flash Raspberry Pi OS Lite (64-bit)** onto the microSD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
   - Choose: **Raspberry Pi OS Lite (64-bit)** (Bookworm or newer)
   - In the Imager settings (⚙️), configure:
     - **Hostname:** `anyone-stick`
     - **Enable SSH:** Yes (password or key)
     - **Username:** `pi`
     - **Password:** *(your choice)*
     - **Wi-Fi:** Configure your local Wi-Fi (SSID + password) for initial setup
     - **Locale:** Your timezone & keyboard layout

2. **Boot the Pi** with the flashed SD card and connect via SSH:
```bash
   ssh pi@anyone-stick.local

Step 1 – Clone or Copy the Project Files
Copy all project files to the Pi. The expected file structure:

~/anyone-stick/
├── install.sh                 # Main installer
├── app.py                     # Flask web portal
├── anonrc                     # Anon configuration (reference)
├── config.txt                 # Boot config (reference)
├── cmdline.txt                # Kernel cmdline (reference)
├── usb_gadget_setup.sh        # USB NCM gadget setup
├── mode_privacy.sh            # Privacy mode iptables rules
├── mode_normal.sh             # Normal mode iptables rules
├── start_anyone_stack.sh      # Master startup script
├── anyone_killswitch.sh       # Kill switch control script
├── usb-gadget.service         # systemd: USB gadget
├── anyone-stick.service       # systemd: main service
├── anyone-stick-circuit-manager.service  # systemd: Node circuit-manager
├── anyone-stick-cm-warmcheck.service     # systemd: warmcheck oneshot
├── anyone-stick-cm-warmcheck.timer       # systemd: warmcheck timer
├── prepare_for_image.sh       # Gold master image preparation
└── static/
    └── logo.png               # Portal logo (optional)

    # From your local machine:
scp -r ./anyone-stick/ pi@anyone-stick.local:~/anyone-stick/

Step 2 – Run the Installer

ssh pi@anyone-stick.local
cd ~/anyone-stick
sudo bash install.sh

Step 3 – Add the Portal Logo (Optional)
sudo cp logo.png /home/pi/portal/static/logo.png

Step 4 – Reboot

sudo reboot

After reboot, the stick is ready. Connect it to a host computer via USB.

Post-Installation: Connecting the Stick
Plug in the Pi Zero 2 W via USB (use the data micro-USB port).
Wait ~30 seconds for the Pi to boot and the NCM gadget to initialize.
A new network interface should appear on your host.
The Pi assigns an IP via DHCP (range: 192.168.7.2 – 192.168.7.20).
Open the web portal: 
http://192.168.7.1 or anyone.stick

Network Architecture
┌──────────────────────────────────────────────────────────┐
│  Host Computer                                           │
│  ┌────────────────────────────────────────────────────┐  │
│  │  usb0 (NCM) ← DHCP: 192.168.7.x                  │  │
│  └──────────────────────┬─────────────────────────────┘  │
└─────────────────────────┼────────────────────────────────┘
                          │ USB Cable
┌─────────────────────────┼────────────────────────────────┐
│  Pi Zero 2 W            │                                │
│  ┌──────────────────────┴─────────────────────────────┐  │
│  │  usb0: 192.168.7.1 (static)                        │  │
│  │  ├─ dnsmasq (DHCP + DNS)                           │  │
│  │  └─ Flask Portal (:80)                             │  │
│  └──────────────────────┬─────────────────────────────┘  │
│                         │ iptables                        │
│  ┌──────────────────────┴─────────────────────────────┐  │
│  │  anon (Privacy Mode)                               │  │
│  │  ├─ TransPort :9040 (transparent TCP proxy)        │  │
│  │  ├─ DNSPort   :9053 (tunnel DNS)                   │  │
│  │  └─ SocksPort :9050 (stream-isolated SOCKS)        │  │
│  └──────────────────────┬─────────────────────────────┘  │
│                         │                                │
│  ┌──────────────────────┴─────────────────────────────┐  │
│  │  wlan0 → Internet (via Wi-Fi)                      │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘

Port	Protocol	Purpose
80	TCP	Web portal (Flask)
9040	TCP	Transparent proxy (Privacy Mode)
9050	TCP	SOCKS5 proxy (stream-isolated)
9051	TCP	Anon control port
9053	UDP/TCP	DNS through tunnel


Privacy Mode Details
When Privacy Mode is activated (mode_privacy.sh):

Kill Switch: Default FORWARD policy set to DROP – if the tunnel dies, no traffic leaks
DNS Redirect: All DNS (port 53) redirected to anon's DNS port (9053)
Transparent Proxy: All TCP SYN packets redirected to anon's TransPort (9040)
UDP Block: All non-DNS UDP traffic is dropped (prevents UDP leaks)
IPv6 Disabled: Fully disabled at kernel and sysctl level
MAC Randomization: If enabled, wlan0 MAC is randomized before connecting
Stream Isolation: Each destination gets its own circuit (configured in anonrc)
When Normal Mode is activated (mode_normal.sh):

Default FORWARD policy set to ACCEPT
Simple NAT masquerade from usb0 → wlan0
Kill Switch marker removed
Exit country settings cleared from anonrc



