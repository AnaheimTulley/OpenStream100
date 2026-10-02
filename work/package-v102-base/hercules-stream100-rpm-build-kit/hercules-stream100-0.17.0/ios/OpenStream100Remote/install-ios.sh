#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if ! command -v xtool >/dev/null 2>&1; then
    echo "xtool is required. Install it from https://xtool.sh and run: xtool setup" >&2
    exit 1
fi

if ! xtool auth status >/dev/null 2>&1; then
    echo "No Xtool Apple account is configured. Run: xtool setup" >&2
    exit 1
fi

if ! xtool sdk status >/dev/null 2>&1; then
    echo "The Xtool Darwin SDK is missing. Run 'xtool setup' or 'xtool sdk install' first." >&2
    exit 1
fi

echo "Connect and unlock the iPhone, tap Trust if prompted, and keep it connected over USB."
echo "Building, signing, and installing OpenStream100 Remote..."
xtool dev run --usb "$@"

cat <<'EOF'

Installation finished.

On the iPhone:
  1. Enable Settings > Privacy & Security > Developer Mode if requested.
  2. After the restart, confirm Developer Mode and enter the device passcode.
  3. If shown as untrusted, open Settings > General > VPN & Device Management
     and trust the Apple ID used by Xtool.
  4. Open "OpenStream100 Remote" manually from the Home Screen or App Library.

Free Apple accounts must rerun this installer every seven days.
EOF
