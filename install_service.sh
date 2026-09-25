#!/usr/bin/env bash
# Install Immich Metadata Worker systemd service
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

echo "=== Installing Immich Metadata Worker ==="
sudo cp "$DIR/immich-metadata-worker.service" /etc/systemd/system/
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$DIR|g" /etc/systemd/system/immich-metadata-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now immich-metadata-worker.service

echo ""
echo "=== Service installed and started successfully! ==="
sudo systemctl status immich-metadata-worker.service --no-pager
