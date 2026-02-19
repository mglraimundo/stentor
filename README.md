# Stentor

Push-to-talk broadcast system for waiting rooms. Multiple PCs on a local network record voice messages that play sequentially through a server-connected speaker.

> *In Greek mythology, **Stentor** was a herald of the Greek forces during the Trojan War, whose voice was as powerful as fifty men.*

## How It Works

- A Python server runs on a machine connected to a speaker
- Staff open `http://<server-ip-or-hostname>:<port>` in their browser
- Press and hold the button (or Space bar) to record a voice message
- Release to send — the message is queued for playback
- Multiple people can record simultaneously — messages play in arrival order
- Each message is preceded by a chime and followed by a 2-second gap

## Quick Start

**Requirements:** Python 3.11+, [uv](https://docs.astral.sh/uv/), ffmpeg (for `ffplay`)

```bash
# Clone the repository
git clone https://github.com/mglraimundo/stentor.git
cd stentor

# Install ffmpeg (provides ffplay for audio playback)
sudo apt install ffmpeg

# Copy and edit configuration
cp .env.example .env

# Run the server
uv run server.py
```

`uv run` automatically creates a virtual environment and installs dependencies from `pyproject.toml` on first run.

Open `http://<server-ip-or-hostname>:<port>` on any device on the same network (default port: `8000`).

## Configuration

All settings are configured via a `.env` file. See `.env.example` for defaults.

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `Área A` | Display name in the UI and page title |
| `FAVICON_LETTER` | `S` | Letter shown in the browser tab favicon |
| `FAVICON_BG_COLOR` | `#2563EB` | Favicon background color |
| `FAVICON_TEXT_COLOR` | `#FFFFFF` | Favicon text color |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8000` | Server port |
| `MAX_RECORDING_SECONDS` | `20` | Max recording duration in seconds |
| `AUDIO_DEVICE` | *(empty)* | ALSA audio device for ffplay output (e.g. `hw:0,0`) |
| `VOLUME_BOOST` | `1.0` | Volume multiplier for audio output (e.g. `3.0` = 3x louder) |
| `DRY_RUN` | `0` | Set to `1` to skip audio playback (for testing) |

## Project Structure

```
stentor/
├── server.py          # FastAPI server, audio queue, ffplay playback
├── static/
│   └── index.html     # Single-file web UI (HTML + CSS + JS)
├── scripts/
│   ├── install.sh     # Automated production installer
│   └── update.sh      # Pull latest code and restart service
├── .env               # Configuration
├── .env.example       # Example config with defaults
├── pyproject.toml     # Project metadata and dependencies
├── QUICKSTART.md      # One-command production install guide
├── DEPLOYMENT.md      # Full manual deployment guide
├── LICENSE            # MIT License
└── README.md
```

## Production Deployment

- **[QUICKSTART.md](QUICKSTART.md)** — One-command automated install via `scripts/install.sh`
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — Full manual guide: systemd, HTTPS with Caddy, ALSA, hardware resilience

## Usage

- **Mouse/touch:** Press and hold the button to talk, release to stop
- **Keyboard:** Hold Space bar to talk, release to stop
- The UI is in European Portuguese (pt-PT)
- No authentication — any device on the network can connect

## Network Requirements

- All devices must be on the same local network
- Server firewall must allow inbound traffic on the configured port
- No internet required after setup
- Recommended: set a recognizable hostname (e.g., `stentor-speaker`) so clients can reach the server at `http://stentor-speaker:<port>`
- Recommended: set a DHCP reservation or static IP for the server so clients always know where to connect

## Author

Created by [Miguel Raimundo](https://github.com/mglraimundo).

## License

This project is licensed under the [MIT License](LICENSE).
