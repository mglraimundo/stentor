#!/usr/bin/env bash
# Stentor updater — pull latest code, sync deps, restart service, diff .env
# Usage: sudo bash scripts/update.sh

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: this script must be run as root (sudo bash scripts/update.sh)" >&2
    exit 1
fi

echo "Pulling latest code..."
sudo -u broadcast git -C /opt/stentor pull

echo "Syncing dependencies..."
sudo -u broadcast /home/broadcast/.local/bin/uv sync --project /opt/stentor

echo "Restarting stentor.service..."
systemctl restart stentor.service
echo "  Done."

# Check for new .env keys
EXAMPLE=/opt/stentor/.env.example
LIVE=/opt/stentor/.env

if [ -f "$EXAMPLE" ] && [ -f "$LIVE" ]; then
    MISSING=()
    while IFS= read -r line; do
        # Extract key from non-comment, non-empty lines containing '='
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            key="${line%%=*}"
            if ! grep -q "^${key}=" "$LIVE"; then
                MISSING+=("$key")
            fi
        fi
    done < "$EXAMPLE"

    if [ ${#MISSING[@]} -gt 0 ]; then
        echo ""
        echo "New variables in .env.example not present in .env:"
        for key in "${MISSING[@]}"; do
            val="$(grep "^${key}=" "$EXAMPLE" | head -1)"
            echo "  ${val}"
        done
        echo "Edit /opt/stentor/.env to add them if needed."
    else
        echo ".env is up to date with .env.example."
    fi
fi

echo ""
echo "Update complete. Check logs with: sudo journalctl -u stentor.service -f"
