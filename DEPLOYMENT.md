# Deployment Guide

Production deployment instructions for running Stentor on an always-on Ubuntu server (e.g., HP EliteDesk G3 800) connected to a speaker.

The goal: power goes out, power comes back, everything works again with zero human intervention.

## 1. Install Dependencies

```bash
sudo apt install ffmpeg alsa-utils
```

## 2. Create a Dedicated User

```bash
sudo useradd -m -s /bin/bash broadcast
sudo usermod -aG audio broadcast

# Install uv for the broadcast user
sudo -u broadcast curl -LsSf https://astral.sh/uv/install.sh | sudo -u broadcast sh
```

## 3. Deploy the Application

```bash
sudo mkdir -p /opt/stentor
sudo chown broadcast:broadcast /opt/stentor
sudo -u broadcast git clone https://github.com/mglraimundo/stentor.git /opt/stentor
sudo -u broadcast cp /opt/stentor/.env.example /opt/stentor/.env
# Edit .env as needed — set AUDIO_DEVICE, PORT, etc.

# Install dependencies
sudo -u broadcast /home/broadcast/.local/bin/uv sync --project /opt/stentor
```

## 4. Configure Audio

The server uses ALSA directly (no PipeWire/PulseAudio needed). Audio settings are configured in `/opt/stentor/.env`.

### Find your audio device

```bash
aplay -l
```

Look for the analog output (e.g. `card 0, device 0` for the 3.5mm jack). Set it in `.env`:

```
AUDIO_DEVICE=hw:0,0
```

### Volume

Browser microphone gain is typically low. Set a volume multiplier in `.env`:

```
VOLUME_BOOST=3.0
```

Also max out the ALSA mixer levels:

```bash
amixer -c 0 set 'Master' 100%
amixer -c 0 set 'Headphone' unmute 100%
amixer -c 0 set 'Headphone',1 unmute 100%

# Persist across reboots
sudo alsactl store
```

### Route to the 3.5mm jack (not internal speaker)

On some machines (e.g. HP EliteDesk), the internal speaker is the default output. To route audio to the 3.5mm jack:

```bash
amixer -c 0 set 'Speaker' mute
sudo alsactl store
```

### Test audio

Verify sound comes out of the correct output:

```bash
speaker-test -D hw:0,0 -t sine -f 440 -l 1 -c 2
```

Then verify it also works as the broadcast user:

```bash
sudo -u broadcast AUDIODEV=hw:0,0 speaker-test -D hw:0,0 -t sine -f 440 -l 1 -c 2
```

> **Important:** If audio stops working after a reboot, the mixer settings were likely reset. Re-run the `amixer` commands above and `sudo alsactl store` again.

## 5. Create Systemd Service

Create `/etc/systemd/system/stentor.service`:

```ini
[Unit]
Description=Stentor — Push-to-Talk Broadcast Server
After=network-online.target sound.target alsa-restore.service
Wants=network-online.target

[Service]
Type=simple
User=broadcast
WorkingDirectory=/opt/stentor
ExecStart=/home/broadcast/.local/bin/uv run --project /opt/stentor uvicorn server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

> **Note:** If not using Caddy (see next step), change `--host 127.0.0.1` to `--host 0.0.0.0` so clients can connect directly.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stentor.service
```

## 6. HTTPS with Caddy (Required for Microphone Access)

Browsers require a secure context (HTTPS or `localhost`) to allow microphone access via `getUserMedia`. Without HTTPS, the push-to-talk button will be greyed out.

[Caddy](https://caddyserver.com/) handles HTTPS automatically with Let's Encrypt certificates. Since the server is on a LAN (not publicly reachable), it uses the DNS-01 challenge, which requires a real domain and DNS API access.

### Prerequisites

- A domain you control (e.g. `yourdomain.com`)
- API credentials for your DNS provider (e.g. Cloudflare API token with Zone:DNS:Edit permission)

### DNS setup

Add an A record pointing to the server's LAN IP:

```
stentor.yourdomain.com → 192.168.1.91
```

### Install Caddy with DNS plugin

Caddy provides pre-built binaries with plugins via a download API — no Go installation needed:

```bash
curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com%2Fcaddy-dns%2Fcloudflare" \
    -o /tmp/caddy
chmod +x /tmp/caddy
sudo mv /tmp/caddy /usr/bin/caddy
```

> For other DNS providers, find the plugin slug at [github.com/caddy-dns](https://github.com/caddy-dns) and substitute it in the URL's `p=` parameter.

### Create the Caddy user

```bash
sudo groupadd --system caddy
sudo useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy
sudo mkdir -p /etc/caddy
```

### Configure Caddy

Create `/etc/caddy/Caddyfile`:

```
stentor.yourdomain.com {
    reverse_proxy localhost:8000
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
}
```

Create `/etc/caddy/env` with your Cloudflare API token (create one at [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens) with Zone:DNS:Edit permission):

```
CLOUDFLARE_API_TOKEN=your-api-token-here
```

Protect it:

```bash
sudo chown root:caddy /etc/caddy/env
sudo chmod 640 /etc/caddy/env
```

### Create the Caddy systemd service

Create `/etc/systemd/system/caddy.service`:

```ini
[Unit]
Description=Caddy
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
EnvironmentFile=/etc/caddy/env
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
TimeoutStopSec=5s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
```

### Start Caddy

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now caddy
```

Caddy will automatically obtain and renew Let's Encrypt certificates. Clients connect at `https://stentor.yourdomain.com` with full browser trust — no certificates to install on any device.

## Networking

### Hostname (Recommended)

```bash
sudo hostnamectl set-hostname stentor-speaker
```

### Static IP (Recommended)

Set a DHCP reservation on your router for the server's MAC address, or configure a static IP in Netplan instead of `dhcp4: true`. This ensures clients always know where to connect.

### WiFi (If No Ethernet)

For machines without built-in WiFi (e.g., using a USB adapter):

1. Identify the interface: `ip link`
2. Install firmware if needed: `sudo apt install linux-firmware`
3. Configure via Netplan — create `/etc/netplan/01-wifi.yaml`:

```yaml
network:
  version: 2
  renderer: networkd
  wifis:
    wlan0:  # replace with actual interface name
      dhcp4: true
      access-points:
        "YourNetworkSSID":
          password: "YourWiFiPassword"
```

4. Apply: `sudo netplan apply`
5. Ensure wpasupplicant is installed: `sudo apt install wpasupplicant`

networkd + netplan will auto-reconnect on WiFi drops.

## Hardware Resilience

### BIOS: Auto Power-On After Power Loss

Most BIOS/UEFI firmwares have an option to automatically power on after a power loss. Example for HP EliteDesk G3 800:

1. Press F10 on boot to enter BIOS Setup
2. Navigate to **Advanced > Power Management**
3. Set **"After Power Loss"** to **"Power On"**
4. Save and exit

For other machines, look for similar settings under Power Management, often labeled "After Power Loss", "AC Power Recovery", or "Restore on AC Power Loss".

### Disable Sleep/Suspend

Ubuntu Server shouldn't sleep by default, but to be sure:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

### Filesystem Resilience

The app writes temporary audio files to `/tmp` during playback (auto-cleaned after each message). Corruption risk is minimal. Ensure ext4 journaling is enabled (default). Optionally add `noatime` to root partition mount options in `/etc/fstab` to reduce writes.

### Watchdog (Optional)

Auto-reboot if the system locks up:

```bash
sudo apt install watchdog
sudo systemctl enable watchdog
```

Configure `/etc/watchdog.conf`:

```
watchdog-device = /dev/watchdog
max-load-1 = 24
watchdog-timeout = 30
```

## Boot Sequence After Power Failure

1. Power returns → BIOS auto-powers on
2. Ubuntu boots via GRUB
3. Network connects (Ethernet/WiFi via Netplan)
4. Caddy starts → obtains/renews HTTPS cert automatically
5. `stentor.service` starts → server binds to `127.0.0.1:8000`
6. ALSA provides audio output directly
7. Ready — clients connect at `https://stentor.yourdomain.com` (~30-60s from power-on)

## Updating

```bash
cd /opt/stentor
sudo -u broadcast git pull
sudo -u broadcast /home/broadcast/.local/bin/uv sync --project /opt/stentor
sudo systemctl restart stentor.service
```

> **Note:** If `.env.example` has new variables, compare it with your `.env` and add any missing entries.

## Troubleshooting

### Checking Logs

```bash
# Last 50 lines
sudo journalctl -u stentor.service -n 50 --no-pager

# Follow in real time
sudo journalctl -u stentor.service -f
```
