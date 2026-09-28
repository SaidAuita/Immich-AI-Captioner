#!/usr/bin/env bash
# ==============================================================================
# Immich Metadata ExifTool Worker — Linux Systemd Service Installer
# ==============================================================================
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

echo "=========================================================="
echo " Installing Immich Metadata ExifTool Worker Service"
echo " Working directory: $DIR"
echo "=========================================================="

echo "[1/4] Checking and installing dependencies (Python 3 & ExifTool)..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y libimage-exiftool-perl python3
elif command -v yum &> /dev/null; then
    sudo yum install -y perl-Image-ExifTool python3
elif command -v dnf &> /dev/null; then
    sudo dnf install -y perl-Image-ExifTool python3
elif command -v pacman &> /dev/null; then
    sudo pacman -Sy --noconfirm perl-image-exiftool python
else
    echo "[!] Package manager not recognized. Please ensure 'exiftool' and 'python3' are installed manually."
fi

if ! command -v exiftool &> /dev/null; then
    echo "[-] Error: exiftool is not installed or not in PATH."
    exit 1
fi

echo "[2/4] Initializing queue directories..."
mkdir -p "$DIR/queue" "$DIR/done" "$DIR/errors" "$DIR/commands"
chmod -R 777 "$DIR/queue" "$DIR/done" "$DIR/errors" "$DIR/commands" 2>/dev/null || true

echo "[3/4] Registering systemd service..."
SERVICE_DEST="/etc/systemd/system/immich-metadata-worker.service"
sudo cp "$DIR/immich-metadata-worker.service" "$SERVICE_DEST"
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$DIR|g" "$SERVICE_DEST"

sudo systemctl daemon-reload
sudo systemctl enable immich-metadata-worker.service
sudo systemctl restart immich-metadata-worker.service

echo "[4/4] Checking service status..."
echo "----------------------------------------------------------"
sudo systemctl status immich-metadata-worker.service --no-pager
echo "----------------------------------------------------------"
echo ""
echo "[✓] Installation complete!"
echo "Useful commands:"
echo "  - View live logs: sudo journalctl -u immich-metadata-worker -f"
echo "  - Restart service: sudo systemctl restart immich-metadata-worker"
echo "  - Stop service:   sudo systemctl stop immich-metadata-worker"
