# Anyone Privacy Stick (Pi Zero 2 W)

> Turn a Raspberry Pi Zero 2 W into a plug-and-play USB privacy stick that routes all traffic through the Anyone network.

## Overview

The Pi Zero 2 W acts as a **USB privacy stick** with an integrated web portal. Using a USB gadget (NCM), it exposes a network interface (`usb0`) to the connected host and can switch between a normal NAT mode and a privacy mode that transparently routes all TCP traffic through the `anon` stack.

### Key Features

| Feature | Description |
|---|---|
| **USB Gadget (NCM)** | Creates the `usb0` network interface for the host |
| **Web Portal (Flask)** | Status view, traffic stats, Wi-Fi scan/connect, mode switch, exit country selection |
| **Normal Mode** | Standard NAT from `usb0` → `wlan0` |
| **Privacy Mode** | Transparent TCP proxy (port `9040`) + DNS redirect (port `9053`) through `anon` |
| **Kill Switch** | `FORWARD DROP` policy – no traffic leaks if the tunnel goes down |
| **MAC Randomization** | Optional random MAC address on `wlan0` via `macchanger` |
| **Stream Isolation** | `IsolateDestAddr` / `IsolateDestPort` on SOCKS & DNS ports |
| **DNS Leak Protection** | All DNS forced through the tunnel, UDP leak prevention |
| **Exit Country Selection** | Choose exit node country via the web portal |
| **Circuit Rotation** | Configurable circuit lifetime (`MaxCircuitDirtiness`) |
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
   ```

### Step 1 – Clone or Copy the Project Files

Copy all project files to the Pi. The expected file structure:

```
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
├── usb-gadget.service         # systemd: USB gadget
├── anyone-stick.service       # systemd: main service
├── prepare_for_image.sh       # Gold master image preparation
└── static/
    └── logo.png               # Portal logo (optional)
```

You can use `scp` or `rsync`:

```bash
# From your local machine:
scp -r ./anyone-stick/ pi@anyone-stick.local:~/anyone-stick/
```

### Step 2 – Run the Installer

```bash
ssh pi@anyone-stick.local
cd ~/anyone-stick
sudo bash install.sh
```

The installer performs the following steps automatically:

| # | Step | What it does |
|---|---|---|
| 1 | System update | `apt-get update && upgrade` |
| 2 | Package install | Flask, dnsmasq, NetworkManager, iptables-persistent, macchanger, etc. |
| 3 | Disable services | Bluetooth, Avahi, triggerhappy, apt timers |
| 4 | Anon binary | Downloads `anon` from GitHub releases (aarch64) |
| 5 | User setup | Ensures user `pi` exists |
| 6 | Directories | Creates `/root/.anon` and `/var/lib/anyone-stick` |
| 7 | anonrc | Writes optimized config with Stream Isolation, DNS Leak Protection, etc. |
| 8 | config.txt | Headless boot config (no GPU, no audio, USB OTG) |
| 9 | cmdline.txt | Kernel params: `dwc2`, `libcomposite`, `ipv6.disable=1` |
| 10 | sysctl | IP forwarding on, IPv6 fully disabled |
| 11 | USB Gadget | Installs `usb_gadget_setup.sh` → `/usr/local/bin/` |
| 12 | Mode scripts | Installs `mode_privacy.sh` & `mode_normal.sh` → `/usr/local/bin/` |
| 13 | Start script | Installs `start_anyone_stack.sh` → `/usr/local/bin/` |
| 14 | dnsmasq | Configures DHCP on `usb0` (192.168.7.0/24) |
| 15 | Static IP | Sets `usb0` to `192.168.7.1` |
| 16 | NetworkManager | Marks `usb0` as unmanaged |
| 17 | Web portal | Copies `app.py` → `/home/pi/portal/` |
| 18 | systemd | Enables `usb-gadget.service` + `anyone-stick.service` + `dnsmasq` |
| 19 | Kernel modules | Registers `dwc2` + `libcomposite` in `/etc/modules` |
| 20 | sudoers | Grants `pi` passwordless access to mode scripts, iptables, macchanger |
| 21 | macchanger | Disables automatic prompts |

### Step 3 – Add the Portal Logo (Optional)

```bash
sudo cp logo.png /home/pi/portal/static/logo.png
# or logo.jpg – the portal tries both
```

### Step 4 – Reboot

```bash
sudo reboot
```

After reboot, the stick is ready. Connect it to a host computer via USB.

---

## Post-Installation: Connecting the Stick

### On the Host Computer

1. **Plug in** the Pi Zero 2 W via USB (use the **data** micro-USB port, not the power-only port).
2. Wait ~30 seconds for the Pi to boot and the NCM gadget to initialize.
3. A new network interface should appear on your host.
4. The Pi assigns an IP via DHCP (range: `192.168.7.2` – `192.168.7.20`).
5. Open the web portal: **http://192.168.7.1**

### First-Time Wi-Fi Setup

1. Open **http://192.168.7.1** in your browser.
2. Click **Scan Networks** in the Wi-Fi section.
3. Select your Wi-Fi network and enter the password.
4. Click **Connect Now**.
5. Once connected, click **Enable Privacy** to activate the tunnel.

---

## File Reference

### Scripts (installed to `/usr/local/bin/`)

| File | Purpose |
|---|---|
| `usb_gadget_setup.sh` | Creates the USB NCM gadget (`usb0`) via configfs |
| `start_anyone_stack.sh` | Master boot script: waits for USB, starts Wi-Fi, dnsmasq, portal, anon |
| `mode_privacy.sh` | Activates privacy mode: Kill Switch, DNS redirect, transparent proxy, MAC randomization |
| `mode_normal.sh` | Activates normal mode: simple NAT, no tunnel, removes Kill Switch |

### Configuration Files

| File | Location | Purpose |
|---|---|---|
| `anonrc` | `/etc/anonrc` | Anon daemon configuration (ports, isolation, circuit settings) |
| `config.txt` | `/boot/firmware/config.txt` | Raspberry Pi boot configuration |
| `cmdline.txt` | `/boot/firmware/cmdline.txt` | Kernel command line parameters |
| `usb0.conf` | `/etc/dnsmasq.d/usb0.conf` | dnsmasq DHCP config for USB interface |
| `usb0` | `/etc/network/interfaces.d/usb0` | Static IP for USB interface |

### systemd Services

| Service | Purpose |
|---|---|
| `usb-gadget.service` | Runs USB gadget setup early in boot (before network) |
| `anyone-stick.service` | Main service: starts the full stack after USB gadget is ready |

### Web Portal

| File | Location | Purpose |
|---|---|---|
| `app.py` | `/home/pi/portal/app.py` | Flask application (dashboard, API, Wi-Fi management) |
| `logo.png` | `/home/pi/portal/static/logo.png` | Portal logo (optional) |

---

## Network Architecture

```
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
```

## Ports & Network Logic

| Port | Protocol | Purpose |
|---|---|---|
| `80` | TCP | Web portal (Flask) |
| `9040` | TCP | Transparent proxy (Privacy Mode) |
| `9050` | TCP | SOCKS5 proxy (stream-isolated) |
| `9051` | TCP | Anon control port |
| `9053` | UDP/TCP | DNS through tunnel |

---

## Privacy Mode Details

When Privacy Mode is activated (`mode_privacy.sh`):

1. **Kill Switch**: Default `FORWARD` policy set to `DROP` – if the tunnel dies, no traffic leaks
2. **DNS Redirect**: All DNS (port 53) redirected to anon's DNS port (9053)
3. **Transparent Proxy**: All TCP SYN packets redirected to anon's TransPort (9040)
4. **UDP Block**: All non-DNS UDP traffic is dropped (prevents UDP leaks)
5. **IPv6 Disabled**: Fully disabled at kernel and sysctl level
6. **MAC Randomization**: If enabled, `wlan0` MAC is randomized before connecting
7. **Stream Isolation**: Each destination gets its own circuit (configured in `anonrc`)

When Normal Mode is activated (`mode_normal.sh`):

1. Default `FORWARD` policy set to `ACCEPT`
2. Simple NAT masquerade from `usb0` → `wlan0`
3. Kill Switch marker removed
4. Exit country settings cleared from `anonrc`

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard (HTML) |
| `/api/traffic` | GET | Live traffic stats (JSON) |
| `/api/circuit` | GET | Current exit country |
| `/api/circuit` | POST | Set exit country `{"exit_country": "de"}` |
| `/wifi/scan` | GET | Scan available Wi-Fi networks |
| `/wifi/connect` | POST | Connect to Wi-Fi `{"ssid": "...", "password": "..."}` |
| `/mode/privacy` | POST | Activate Privacy Mode |
| `/mode/normal` | POST | Activate Normal Mode |

---

## Creating a Gold Master Image

Once the stick is fully configured and tested, you can create a distributable SD card image:

```bash
# On the Pi – clean up before imaging:
sudo bash ~/anyone-stick/prepare_for_image.sh
```

This script:
- Removes all Wi-Fi profiles (except `Stick-Gateway`)
- Sets hostname to `anyone-stick`
- Clears logs, caches, and bash history
- Syncs filesystem and powers off

Then, on your computer, create the image from the SD card:

```bash
# Linux/macOS (replace /dev/sdX with your SD card device):
sudo dd if=/dev/sdX of=anyone-stick-v1.0.img bs=4M status=progress
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `usb0` not appearing on host | Check USB cable (must be data cable). Use the **data** micro-USB port on the Pi (not PWR). |
| No IP assigned to host | Check dnsmasq: `sudo systemctl status dnsmasq` |
| Portal not reachable | Check service: `sudo systemctl status anyone-stick` |
| Anon not starting | Check binary: `anon --version`. Check config: `cat /etc/anonrc` |
| Wi-Fi not connecting | Check NetworkManager: `nmcli dev status` |
| Privacy mode but no internet | Check anon logs: `journalctl -u anyone-stick -f` |
| DNS leaks in Privacy Mode | Verify iptables: `sudo iptables -t nat -L -n` – port 53 must redirect to 9053 |

---

## Dependencies

### System Packages (APT)

| Package | Purpose |
|---|---|
| `python3-flask` | Web portal |
| `dnsmasq` | DHCP & DNS for `usb0` |
| `network-manager` | Wi-Fi management via `nmcli` |
| `iptables-persistent` | Persistent firewall rules |
| `macchanger` | MAC address randomization |
| `curl` | Download anon binary |
| `unzip` | Extract archives |
| `jq` | JSON processing |
| `libcap2` | Linux capabilities |

### Kernel Modules

- `dwc2` – USB OTG controller
- `libcomposite` – USB gadget framework

### External Binary

- `anon` – Anyone Protocol client ([GitHub Releases](https://github.com/anyone-protocol/ator-protocol/releases))

---

## License

Anyone Foundation – Privacy Stick Project
