# Quick Install

```bash
sudo bash scripts/install.sh
```

Prompts for:
- **Domain** — the hostname you'll use to access Stentor (e.g. `stentor.example.com`)
- **Cloudflare API token** — Zone:DNS:Edit permission ([create one here](https://dash.cloudflare.com/profile/api-tokens))
- **ALSA audio device** — the output device for audio playback (e.g. `hw:0,0`)
- **Wi-Fi interface** — the network interface for DDNS IP detection (e.g. `wlan0`; find with `ip -br link`)

**Non-interactive** (for scripted or repeated installs):

```bash
sudo STENTOR_DOMAIN=stentor.example.com \
     STENTOR_CF_TOKEN=your-token-here \
     STENTOR_AUDIO_DEVICE=hw:0,0 \
     STENTOR_WIFI_IFACE=wlan0 \
     bash scripts/install.sh
```

## Before running

Do these manually first — the script can't automate them:

1. **Find your audio device:** `aplay -l` (pick the analog output, e.g. card 0, device 0)
2. **DNS A record:** point your domain to the server's LAN IP — set to **"DNS Only"** (grey cloud) and **TTL 1 min** in Cloudflare
3. **DHCP reservation:** assign the server a static IP on your router
4. **BIOS auto-power-on:** set "After Power Loss" to "Power On" in BIOS

## Updating

```bash
sudo bash scripts/update.sh
```

Pulls latest code, syncs dependencies, restarts the service, and reports any new `.env` variables.

## Full manual guide

See [DEPLOYMENT.md](DEPLOYMENT.md) for step-by-step instructions, troubleshooting, and networking/hardware details.
