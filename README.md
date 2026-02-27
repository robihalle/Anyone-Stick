# Anyone Privacy Stick

> Turn a **Raspberry Pi Zero 2 W** into a plug-and-play USB privacy stick that routes all your traffic through the [Anyone](https://anyone.io) network -- in under 15 minutes.

![Anyone Stick](logo.png)

---

## Quick Start (TL;DR)

```
 What you need          What you do
 --------------         --------------------------------------
 1x Pi Zero 2 W        1. Flash Raspberry Pi OS Lite (64-bit)
 1x microSD >= 8 GB    2. SSH in and run the installer
 1x USB data cable      3. Plug the Pi into your laptop
 1x Wi-Fi network       4. Open http://192.168.7.1 -- done!
```

---

## What You Need (Shopping List)

| Item | Notes |
|---|---|
| **Raspberry Pi Zero 2 W** | Must be the **2 W** model (quad-core, Wi-Fi) |
| **microSD card** (>= 8 GB) | 16 GB recommended |
| **USB data cable** | **WARNING:** Must be a **data** cable, not charge-only! If the Pi does not show up as a network device, try a different cable. |
| **Computer** | Windows, macOS, or Linux -- anything with a USB port |
| **Wi-Fi network** | The Pi needs internet access during installation and operation |

Optional but helpful:
- **microSD card reader** (if your computer does not have one built-in)
- **USB-A to Micro-USB adapter** (if your computer only has USB-C, use a hub)

---

## Step-by-Step Installation

### Step 1 -- Flash the SD Card

1. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit, Bookworm)** -- no desktop needed
3. Click the gear icon (or Ctrl+Shift+X) and configure:
   - **Enable SSH** (use password authentication)
   - **Set username**: `pi` / password: (your choice)
   - **Configure Wi-Fi**: enter your SSID and password
   - **Set locale**: your timezone and keyboard layout
4. Flash the SD card and insert it into the Pi

### Step 2 -- Run the Installer

Power the Pi (via any USB power source or your computer), wait ~60 seconds for boot, then:

```bash
# Find your Pi on the network (or use your router's DHCP list)
# Then SSH in:
ssh pi@<your-pi-ip-address>

# Install git and clone the repo
sudo apt-get update && sudo apt-get install -y git
git clone -b feature/2-hops https://github.com/robihalle/Anyone-Stick.git
cd Anyone-Stick

# Run the installer (takes ~10-15 minutes on Pi Zero 2W)
sudo bash installer.sh
```

The installer will:
1. Install all system packages
2. Configure the USB gadget (NCM network interface)
3. Install the Anyone Protocol (`anon`)
4. Set up the Circuit Manager (Node.js)
5. Set up the Web Portal (Flask/Gunicorn)
6. Deploy all shell scripts and iptables rules
7. Configure dnsmasq (DHCP for your computer)
8. Disable nginx (not needed)
9. Enable all systemd services

**After installation, the Pi reboots automatically.**

### Step 3 -- Plug In and Use

1. **Plug the Pi into your computer** via the USB **data** port (the one closest to the center of the board, not the one on the edge labeled "PWR")
2. **Wait ~60 seconds** for the Pi to boot
3. **Open your browser** and go to: **http://192.168.7.1**
4. You will see the Anyone Stick dashboard!

> **First-time note**: The Anyone network needs 1-2 minutes to build its first circuit. The dashboard will show "Bootstrapping..." until the first circuit is ready.

---

## How It Works

```
Your Computer                        Pi Zero 2 W
+----------+    USB Cable    +-----------------------------+
|          |<--------------->|  usb0: 192.168.7.1          |
|  Browser |  NCM network    |    +- Web Portal (:80)      |
|  Traffic |  192.168.7.x    |    +- Circuit Manager (:8787)|
|          |                 |    +- dnsmasq (DHCP+DNS)    |
+----------+                 |                             |
                             |  iptables (NAT or Proxy)    |
                             |         |                   |
                             |  anon (Anyone Protocol)     |
                             |    +- TransPort  :9040      |
                             |    +- DNSPort    :9053      |
                             |    +- SocksPort  :9050      |
                             |         |                   |
                             |  wlan0 --> Wi-Fi --> Internet|
                             +-----------------------------+
```

---

## Features

| Feature | Description |
|---|---|
| **Plug and Play** | Just plug the Pi into USB -- no drivers needed (NCM gadget) |
| **Web Portal** | Dashboard with status, Wi-Fi settings, mode switch, circuit visualization |
| **Normal Mode** | Standard internet via NAT -- no privacy routing |
| **Privacy Mode** | All TCP traffic transparently routed through the Anyone network |
| **Kill Switch** | `FORWARD DROP` -- zero traffic leaks if the tunnel drops |
| **2-Hop / 3-Hop** | Toggle circuit length: faster (2-hop) or more anonymous (3-hop) |
| **Exit Country** | Choose which country your traffic exits from |
| **Circuit Rotation** | Automatic rotation with configurable interval |
| **New Identity** | Request a fresh circuit on demand |
| **Circuit Visualization** | See your relay chain in real-time (flags, nickname, IP, country) |
| **Anyone Proof** | Verify your connection through the Anyone network |
| **Stream Isolation** | Separate circuits per destination |
| **DNS Leak Protection** | All DNS queries forced through the tunnel |

---

## Using the Dashboard

### Switching Modes

- **Normal Mode** = standard internet via NAT (no privacy routing)
- **Privacy Mode** = all TCP traffic transparently routed through `anon`; DNS via tunnel

Switch using the toggle on the dashboard. The kill switch activates automatically in Privacy Mode.

### Exit Country

Select a preferred exit country from the dropdown. The circuit manager rebuilds circuits through relay nodes in the chosen country.

### 2-Hop vs 3-Hop

| Mode | Path | Tradeoff |
|---|---|---|
| **2-Hop** | Guard -> Exit | Faster, lower latency |
| **3-Hop** | Guard -> Middle -> Exit | Stronger anonymity |

Switch via the dashboard. Requires a circuit rebuild (~30 seconds).

---

## Troubleshooting

### "I plugged in the Pi but I cannot reach 192.168.7.1"

1. **Check the cable** -- it must be a data cable, not charge-only. If in doubt, try a different cable.
2. **Check the port** -- use the **data** USB port on the Pi (center), not the PWR port (edge).
3. **Wait longer** -- the Pi Zero 2W takes ~60 seconds to fully boot.
4. **Check your network settings** -- look for a new network interface (e.g., "RNDIS" on Windows, "USB Ethernet" on macOS/Linux). It should get a `192.168.7.x` IP via DHCP.
5. **Windows users**: If the Pi shows up as a COM port instead of a network device, you may need to install RNDIS drivers or switch to NCM mode.

### "Dashboard says Bootstrapping... and never finishes"

- The Anyone network needs time to build circuits (typically 30-90 seconds after boot).
- Check if `anon` is running: `ssh pi@192.168.7.1` then `sudo systemctl status anon`
- Check circuit manager: `curl -s http://192.168.7.1:8787/status | jq .`

### "Privacy Mode is on but websites do not load"

- Some sites block known exit nodes. Try switching the exit country.
- DNS may be slow on first use. Wait a few seconds and retry.
- Check if anon has built a circuit: look for `circuitsCached > 0` in the dashboard.

### "Wi-Fi lost after reboot"

- SSH in via USB: `ssh pi@192.168.7.1`
- Reconnect Wi-Fi: use the Wi-Fi page at `http://192.168.7.1/wifi` or via CLI:
  ```bash
  sudo nmcli dev wifi connect "YourSSID" password "YourPassword"
  ```

---

## Useful Commands (via SSH)

```bash
# SSH into the Pi (via USB network)
ssh pi@192.168.7.1

# Service status
sudo systemctl status anyone-stick                    # Portal
sudo systemctl status anyone-stick-circuit-manager     # Circuit Manager
sudo systemctl status anon                             # Anyone Protocol

# Live logs
sudo journalctl -u anyone-stick -f
sudo journalctl -u anyone-stick-circuit-manager -f
sudo journalctl -u anon -f

# Circuit manager API (from the Pi)
curl -s http://127.0.0.1:8787/status | jq .
curl -s http://127.0.0.1:8787/circuit | jq .

# Restart everything
sudo systemctl restart anon anyone-stick-circuit-manager anyone-stick
```

---

## API Reference

### Portal (`:80`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Connection state, mode, circuit count |
| GET | `/api/cm/status` | Proxied circuit-manager status |
| GET | `/api/anyone/proof` | Anyone network connectivity proof |
| POST | `/api/mode` | Switch between Normal / Privacy mode |
| POST | `/api/country` | Set exit country |

### Circuit Manager (`:8787`)

| Method | Path | Description |
|---|---|---|
| GET | `/status` | Bootstrapping state, cached circuits |
| GET | `/circuit` | Active circuit details with relay info |
| POST | `/country` | Set preferred exit country |
| POST | `/newnym` | Request new identity (circuit rotation) |
| GET | `/wait-ready` | Block until first circuit is BUILT |

---

## Repository Structure

```
Anyone-Stick/
+-- installer.sh                          # One-command installer (9 steps)
+-- app.py                                # Flask portal -> /home/pi/portal/
+-- server.mjs                            # Circuit manager (Node.js) -> /opt/anyone-stick/circuit-manager/
+-- anonrc                                # anon config -> /etc/anon/anonrc
+-- start_anyone_stack.sh                 # Boot script: anon + dnsmasq + mode
+-- mode_normal.sh                        # iptables rules for NAT mode
+-- mode_privacy.sh                       # iptables rules for transparent proxy
+-- anyone_killswitch.sh                  # FORWARD DROP leak prevention
+-- usb_gadget_setup.sh                   # NCM gadget setup on usb0
+-- cmdline.txt                           # Boot cmdline (dwc2 module)
+-- config.txt                            # Boot config (dwc2 overlay)
+-- anyone-stick.service                  # systemd: portal (gunicorn)
+-- anyone-stick-circuit-manager.service  # systemd: circuit manager (node)
+-- usb-gadget.service                    # systemd: USB gadget setup
+-- logo.png                              # Portal logo
+-- README.md                             # This file
```

## Systemd Services

| Service | Description | Runs |
|---|---|---|
| `usb-gadget` | Sets up NCM gadget on boot | once |
| `anon` | Anyone relay daemon | always |
| `anyone-stick-circuit-manager` | Node.js circuit manager sidecar (`:8787`) | always |
| `anyone-stick` | Flask portal via gunicorn (`:80`) | always |

---

## License

MIT
