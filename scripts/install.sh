#!/usr/bin/env bash
# Stentor installer — idempotent, run as root
# Usage: sudo bash scripts/install.sh
# Non-interactive: sudo STENTOR_DOMAIN=... STENTOR_CF_TOKEN=... STENTOR_AUDIO_DEVICE=hw:0,0 bash scripts/install.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this script must be run as root (sudo bash scripts/install.sh)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Config input — env vars with prompt fallback
# ---------------------------------------------------------------------------
DOMAIN="${STENTOR_DOMAIN:-}"
if [ -z "$DOMAIN" ]; then
    read -rp "Domain (e.g. stentor.example.com): " DOMAIN
fi

CF_TOKEN="${STENTOR_CF_TOKEN:-}"
if [ -z "$CF_TOKEN" ]; then
    read -rsp "Cloudflare API token (Zone:DNS:Edit): " CF_TOKEN
    echo
fi

AUDIO_DEVICE="${STENTOR_AUDIO_DEVICE:-}"
if [ -z "$AUDIO_DEVICE" ]; then
    echo "Available audio devices:"
    aplay -l 2>/dev/null || echo "  (aplay not found yet — will install alsa-utils)"
    read -rp "ALSA audio device (e.g. hw:0,0): " AUDIO_DEVICE
fi

# ---------------------------------------------------------------------------
# [1/8] System packages
# ---------------------------------------------------------------------------
echo "[1/8] Installing system packages..."
apt-get install -y ffmpeg alsa-utils curl git

# ---------------------------------------------------------------------------
# [2/8] User setup
# ---------------------------------------------------------------------------
echo "[2/8] Creating broadcast user..."
useradd -m -s /usr/sbin/nologin broadcast 2>/dev/null || true
usermod -aG audio broadcast

if [ ! -f /home/broadcast/.local/bin/uv ]; then
    echo "  Installing uv for broadcast user..."
    sudo -u broadcast curl -LsSf https://astral.sh/uv/install.sh | sudo -u broadcast sh
else
    echo "  uv already installed, skipping."
fi

# ---------------------------------------------------------------------------
# [3/8] Deploy application
# ---------------------------------------------------------------------------
echo "[3/8] Deploying application..."
mkdir -p /opt/stentor
chown broadcast:broadcast /opt/stentor

if [ ! -d /opt/stentor/.git ]; then
    echo "  Cloning repository..."
    sudo -u broadcast git clone https://github.com/mglraimundo/stentor.git /opt/stentor
else
    echo "  Repository already cloned, skipping."
fi

echo "  Running uv sync..."
sudo -u broadcast /home/broadcast/.local/bin/uv sync --project /opt/stentor

if [ ! -f /opt/stentor/.env ]; then
    echo "  Writing .env..."
    cat > /opt/stentor/.env << EOF
# Display name shown in the UI, page title, and favicon
APP_NAME=Área A

# Favicon letter (defaults to "S" for Stentor if not set)
FAVICON_LETTER=A

# Favicon colors
FAVICON_BG_COLOR=#2563EB
FAVICON_TEXT_COLOR=#FFFFFF

# Server
HOST=127.0.0.1
PORT=8000

# Maximum recording duration in seconds
MAX_RECORDING_SECONDS=20

# ALSA audio device for ffplay output (find with: aplay -l)
# e.g. hw:0,0 for the first card's analog output
AUDIO_DEVICE=${AUDIO_DEVICE}

# Volume multiplier for audio output (1.0 = no change, 3.0 = 3x louder)
VOLUME_BOOST=3.0

# Normalize audio loudness across messages (EBU R128 via ffmpeg loudnorm)
NORMALIZE_VOLUME=0

# Set to 1 to disable ffplay audio output (for testing on WSL or machines without audio)
DRY_RUN=0
EOF
    chown broadcast:broadcast /opt/stentor/.env
    chmod 600 /opt/stentor/.env
    echo "  .env written."
else
    echo "  .env already exists, skipping (preserving manual edits)."
fi

# ---------------------------------------------------------------------------
# [4/8] ALSA mixer levels (non-fatal — controls are device-specific)
# ---------------------------------------------------------------------------
echo "[4/8] Configuring ALSA mixer..."
CARD="${AUDIO_DEVICE%%,*}"
CARD="${CARD#hw:}"
CARD="${CARD:-0}"

amixer -c "$CARD" set 'Master' 100% 2>/dev/null || true
amixer -c "$CARD" set 'Headphone' unmute 100% 2>/dev/null || true
amixer -c "$CARD" set 'Headphone' 1 unmute 100% 2>/dev/null || true
amixer -c "$CARD" set 'Speaker' mute 2>/dev/null || true
alsactl store 2>/dev/null || true
echo "  ALSA configured (errors above are normal — controls vary by device)."

# ---------------------------------------------------------------------------
# [5/8] Stentor systemd service
# ---------------------------------------------------------------------------
echo "[5/8] Installing stentor.service..."
cat > /etc/systemd/system/stentor.service << 'EOF'
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
EOF

systemctl daemon-reload
systemctl enable --now stentor.service
echo "  stentor.service enabled and started."

# ---------------------------------------------------------------------------
# [6/8] Caddy with Cloudflare DNS plugin
# ---------------------------------------------------------------------------
echo "[6/8] Installing Caddy..."
echo "  Downloading Caddy binary with Cloudflare DNS plugin..."
curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com%2Fcaddy-dns%2Fcloudflare" \
    -o /tmp/caddy
chmod +x /tmp/caddy
mv /tmp/caddy /usr/bin/caddy

echo "  Creating caddy user/group..."
groupadd --system caddy 2>/dev/null || true
useradd --system --gid caddy --create-home --home-dir /var/lib/caddy --shell /usr/sbin/nologin caddy 2>/dev/null || true
mkdir -p /etc/caddy

echo "  Writing Caddyfile..."
cat > /etc/caddy/Caddyfile << EOF
${DOMAIN} {
    reverse_proxy localhost:8000
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
}
EOF

echo "  Writing Caddy env file..."
cat > /etc/caddy/env << EOF
CLOUDFLARE_API_TOKEN=${CF_TOKEN}
EOF
chown root:caddy /etc/caddy/env
chmod 640 /etc/caddy/env

echo "  Writing caddy.service..."
cat > /etc/systemd/system/caddy.service << 'EOF'
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
EOF

systemctl daemon-reload
systemctl enable --now caddy.service
echo "  caddy.service enabled and started."

# ---------------------------------------------------------------------------
# [7/8] Manual steps (DNS, DHCP, WiFi) — cannot be automated
# ---------------------------------------------------------------------------
echo "[7/8] Skipped (manual steps required):"
echo "  - DNS A record: ${DOMAIN} → <server LAN IP>"
echo "  - DHCP reservation on router"
echo "  - BIOS: set 'After Power Loss' to 'Power On'"
echo "  - WiFi configuration (if no Ethernet)"
echo "  See DEPLOYMENT.md for details."

# ---------------------------------------------------------------------------
# [7.5/8] DDNS for Local Network Hopping
# ---------------------------------------------------------------------------
echo "[7.5/8] Configuring ddclient for local IP updates..."
apt-get install -y ddclient libjson-any-perl

# Extract root zone from domain (assumes standard subdomain.domain.tld structure)
ROOT_ZONE=$(echo "$DOMAIN" | cut -d'.' -f2-)

cat > /etc/ddclient.conf << EOF
daemon=300
syslog=yes
ssl=yes
use=if, if=wlan0

protocol=cloudflare
zone=${ROOT_ZONE}
login=token
password=${CF_TOKEN}
${DOMAIN}
EOF

chown root:root /etc/ddclient.conf
chmod 600 /etc/ddclient.conf

systemctl restart ddclient
systemctl enable ddclient
echo "  ddclient configured and enabled."

# ---------------------------------------------------------------------------
# [8/8] System hardening
# ---------------------------------------------------------------------------
echo "[8/8] Applying system settings..."

systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null || true
echo "  Sleep targets masked."

if apt-get install -y watchdog 2>/dev/null; then
    systemctl enable watchdog 2>/dev/null || true
    echo "  watchdog installed and enabled."
else
    echo "  watchdog not available, skipping."
fi

CURRENT_HOSTNAME="$(hostname)"
if [ "$CURRENT_HOSTNAME" = "ubuntu" ] || [ "$CURRENT_HOSTNAME" = "localhost" ]; then
    hostnamectl set-hostname stentor-speaker
    echo "  Hostname set to stentor-speaker."
else
    echo "  Hostname is '${CURRENT_HOSTNAME}', skipping."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "Installation complete."
echo "  Stentor: sudo journalctl -u stentor.service -f"
echo "  Caddy:   sudo journalctl -u caddy.service -f"
echo "  Access:  https://${DOMAIN}"
echo ""
echo "Remember to complete the manual steps listed in step [7/8] above."
