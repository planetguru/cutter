#!/bin/bash
# cutter residential download helper — run by launchd on a home/residential
# machine. Downloads the server's pending videos (YouTube blocks the server's
# datacenter IP) and pushes them back. Logs to fetch.log next to this script.
set -euo pipefail

# launchd runs with a minimal PATH; add Homebrew (ffmpeg) and system bins.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
source .venv/bin/activate

exec cutter fetch --server "${FETCH_SERVER:-root@chris.uk.com}"
