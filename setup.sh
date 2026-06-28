#!/usr/bin/env bash
# One-shot setup for a new machine.
# Usage: bash setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== cutter setup ==="

# Python check
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.11+ and try again."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 11 ]; then
    echo "Error: Python 3.11+ required (found 3.${PYTHON_VERSION})."
    exit 1
fi

# ffmpeg check
if ! command -v ffmpeg &>/dev/null; then
    echo "Warning: ffmpeg not found. Install it before running cutter:"
    echo "  macOS:  brew install ffmpeg"
    echo "  Ubuntu: sudo apt install ffmpeg"
fi

# Create venv and install
echo "Installing dependencies..."
python3 -m venv .venv
source .venv/bin/activate
pip install -e . -q
echo "✓ Dependencies installed"

# Copy .env if not present
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "✓ Created .env from .env.example"
    echo "  → Fill in your credentials before running cutter"
else
    echo "✓ .env already exists"
fi

# Install daily cron job (9am by default)
CUTTER_BIN="$SCRIPT_DIR/.venv/bin/cutter"
LOG_FILE="$SCRIPT_DIR/cutter.log"
CRON_MARKER="$CUTTER_BIN daily"
CRON_LINE="0 9 * * * $CRON_MARKER >> $LOG_FILE 2>&1"

if crontab -l 2>/dev/null | grep -qF "$CUTTER_BIN daily"; then
    echo "✓ Cron job already installed"
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "✓ Cron job installed (runs daily at 9:00am local time)"
    echo "  → To change the time: crontab -e"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API credentials"
echo "  2. Authenticate with platforms:"
echo "       source .venv/bin/activate"
echo "       cutter auth youtube"
echo "       cutter auth tiktok"
echo "       cutter auth instagram"
echo "  3. Add a video to the queue:"
echo "       cutter queue add https://www.youtube.com/watch?v=..."
echo "  4. Test the daily run:"
echo "       cutter daily"
echo ""
echo "  Or queue videos from WhatsApp by sending: queue:https://youtube.com/..."
