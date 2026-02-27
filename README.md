# Anyone Privacy Stick

> Turn a Raspberry Pi Zero 2 W into a plug-and-play USB privacy stick that routes all traffic through the [Anyone](https://anyone.io) network.

![Anyone Stick](logo.png)

## Overview

The Pi Zero 2 W acts as a **USB privacy stick** with an integrated web portal.
Using a USB gadget (NCM), it exposes a network interface (`usb0`) to the
connected host and can switch between **Normal Mode** (standard NAT) and
**Privacy Mode** (transparent TCP/DNS routing through the `anon` network).

## Features

| Feature | Description |
|---|---|
| **USB Gadget (NCM)** | Creates `usb0` for the host - plug-and-play, no drivers needed |
| **Web Portal** | Status dashboard, Wi-Fi management, mode switch, exit country selector, circuit visualization |
| **Normal Mode** | Standard NAT - `usb0` to `wlan0` |
| **Privacy Mode** | Transparent TCP proxy (`:9040`) + DNS redirect (`:9053`) through `anon` |
| **Kill Switch** | `FORWARD DROP` - no traffic leaks if the tunnel goes down |
| **2-Hop / 3-Hop** | Switch circuit length via the dashboard |
| **Exit Country** | Choose exit node country from the portal |
| **Circuit Rotation** | Automatic rotation with configurable interval and variance |
| **New Identity** | Request a fresh circuit on demand (NewNym) |
| **Circuit Visualization** | Real-time display of relay hops (role, flags, nickname, IP, country) |
| **Anyone Proof** | Verifies connectivity through the Anyone network via SOCKS5 |
| **Stream Isolation** | `IsolateDestAddr` / `IsolateDestPort` on SOCKS and DNS ports |
| **DNS Leak Protection** | All DNS forced through the tunnel; UDP leak prevention |

## Architecture

```
Host Computer
  usb0 (NCM) - DHCP: 192.168.7.x
         |
         | USB Cable
         |
Pi Zero 2 W
  usb0: 192.168.7.1 (static)
    |- dnsmasq (DHCP + DNS for host)
    |- Flask Portal (:80 via gunicorn)
    |- Circuit Manager (:8787 - Node.js sidecar)
         |
         | iptables
         |
  anon (Privacy Mode)
    |- TransPort  :9040  (transparent TCP proxy)
    |- DNSPort    :9053  (tunnel DNS)
    |- SocksPort  :9050  (stream-isolated SOCKS)
         |
  wlan0 -> Internet (via Wi-Fi)
```

## Requirements

- Raspberry Pi Zero 2 W
- microSD card (>= 8 GB)
- Raspberry Pi OS Lite (Bookworm, 64-bit)
- USB data cable (not charge-only)
- Wi-Fi network with internet access

## Installation

### 1 - Prepare the SD card

Flash **Raspberry Pi OS Lite (Bookworm 64-bit)** with Raspberry Pi Imager.
Enable SSH and configure Wi-Fi in the imager settings.

### 2 - Connect and run the installer

```bash
ssh pi@<ip-address>
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/robihalle/Anyone-Stick.git
cd Anyone-Stick
sudo bash installer.sh
```

The installer runs **9 automated sections**:

1. System packages and boot config (`cmdline.txt`, `config.txt`)
2. USB Gadget setup (NCM on `usb0`)
3. Anyone Protocol (`anon` package + `anonrc`)
4. Node.js and Circuit Manager (`server.mjs` -> `/opt/anyone-stick/circuit-manager/`)
5. Portal (`app.py` -> `/home/pi/portal/`)
6. Shell scripts (`mode_normal.sh`, `mode_privacy.sh`, `anyone_killswitch.sh`, `start_anyone_stack.sh`)
7. dnsmasq (DHCP for USB host)
8. nginx (disabled - portal binds port 80 directly)
9. Systemd services (enable all units)

After installation the Pi reboots automatically.

### 3 - Connect and use

1. Plug the Pi into your computer via USB
2. Wait ~60 seconds for boot
3. Open **http://192.168.7.1** in your browser

## Usage

### Web Portal

| Page | URL |
|---|---|
| Dashboard | `http://192.168.7.1/` |
| Wi-Fi Settings | `http://192.168.7.1/wifi` |

### Switching Modes

- **Normal Mode** - standard internet via NAT (no privacy routing)
- **Privacy Mode** - all TCP traffic transparently routed through `anon`; DNS via tunnel

Switch via the dashboard toggle. The kill switch activates automatically in Privacy Mode.

### Exit Country

Select a preferred exit country from the dropdown. The circuit manager rebuilds circuits through relay nodes in the chosen country.

### 2-Hop vs 3-Hop

- **2-Hop** - faster, lower latency (Guard -> Exit)
- **3-Hop** - stronger anonymity (Guard -> Middle -> Exit)

Switch via the dashboard. Requires circuit rebuild.

## Repository Structure

```
Anyone-Stick/
|- installer.sh                          # Main installer (9 sections)
|- app.py                                # Flask portal -> /home/pi/portal/
|- server.mjs                            # Circuit manager (Node.js) -> /opt/anyone-stick/circuit-manager/
|- anonrc                                # anon config -> /etc/anonrc
|- start_anyone_stack.sh                 # Boots anon + dnsmasq + mode
|- mode_normal.sh                        # iptables for NAT mode
|- mode_privacy.sh                       # iptables for transparent proxy mode
|- anyone_killswitch.sh                  # FORWARD DROP leak prevention
|- usb_gadget_setup.sh                   # NCM gadget on usb0
|- cmdline.txt                           # Boot cmdline (dwc2 module)
|- config.txt                            # Boot config (dwc2 overlay)
|- anyone-stick.service                  # systemd: portal (gunicorn)
|- anyone-stick-circuit-manager.service  # systemd: circuit manager (node)
|- usb-gadget.service                    # systemd: USB gadget setup
|- logo.png                              # Portal logo
|- README.md                             # This file
```

## Systemd Services

| Service | Description | Runs |
|---|---|---|
| `usb-gadget` | Sets up NCM gadget on boot | once |
| `anon` | Anyone relay daemon | always |
| `anyone-stick-circuit-manager` | Node.js circuit manager sidecar (`:8787`) | always |
| `anyone-stick` | Flask portal via gunicorn (`:80`) | always |

### Useful commands

```bash
# Service status
sudo systemctl status anyone-stick
sudo systemctl status anyone-stick-circuit-manager

# Logs
sudo journalctl -u anyone-stick -f
sudo journalctl -u anyone-stick-circuit-manager -f

# Circuit manager API (local)
curl -s http://127.0.0.1:8787/status | jq .
curl -s http://127.0.0.1:8787/circuit | jq .
```

## API Endpoints

### Portal (`:80`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Connection state, mode, circuits |
| GET | `/api/cm/status` | Proxied circuit-manager status |
| GET | `/api/anyone/proof` | Anyone network proof check |
| POST | `/api/mode` | Switch Normal / Privacy |
| POST | `/api/country` | Set exit country |

### Circuit Manager (`:8787`)

| Method | Path | Description |
|---|---|---|
| GET | `/status` | Bootstrapping state, cached circuits |
| GET | `/circuit` | Active circuit details with relay info |
| POST | `/country` | Set preferred exit country |
| POST | `/newnym` | Request new identity (circuit rotation) |
| GET | `/wait-ready` | Block until first circuit is BUILT |

## License

MIT