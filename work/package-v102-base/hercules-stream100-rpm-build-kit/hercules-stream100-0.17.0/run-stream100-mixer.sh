#!/usr/bin/bash
set -euo pipefail

app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mixer="$app_dir/stream100-mixer.py"
mixer_implementation="$app_dir/stream100-mixer-alpha.py"
helper_source="$app_dir/stream100-display-helper.c"
helper="$app_dir/stream100-display-helper"
replay="$app_dir/stream100-display-replay.bin"

missing=()
command -v python3 >/dev/null 2>&1 || missing+=(python3)
command -v pw-dump >/dev/null 2>&1 || missing+=(pipewire-utils)
command -v wpctl >/dev/null 2>&1 || missing+=(wireplumber)

if (( ${#missing[@]} > 0 )); then
    echo "A few application dependencies are missing. Install them with:"
    echo "  sudo dnf install python3-pyusb python3-pillow pipewire-utils wireplumber fontconfig"
    exit 1
fi

if ! python3 -c 'import usb, PIL' >/dev/null 2>&1; then
    echo "The Python USB or image library is missing. Install them with:"
    echo "  sudo dnf install python3-pyusb python3-pillow"
    exit 1
fi

for required in "$mixer" "$mixer_implementation" "$replay"; do
    if [[ ! -f "$required" ]]; then
        echo "Missing application file: $required"
        echo "Keep all Stream 100 application files together in one folder."
        exit 1
    fi
done

needs_helper_build=0
if [[ ! -x "$helper" ]]; then
    needs_helper_build=1
elif [[ -f "$helper_source" && "$helper_source" -nt "$helper" ]]; then
    needs_helper_build=1
fi

if (( needs_helper_build )); then
    if [[ ! -f "$helper_source" ]] \
        || ! command -v cc >/dev/null 2>&1 \
        || ! command -v pkg-config >/dev/null 2>&1 \
        || ! pkg-config --exists libusb-1.0 2>/dev/null; then
        echo "The display helper is missing and cannot be rebuilt. Install:"
        echo "  sudo dnf install gcc libusb1-devel pkgconf-pkg-config"
        exit 1
    fi
    echo "Building the Stream 100 display helper..."
    cc -std=c11 -O2 -Wall -Wextra "$helper_source" -o "$helper" \
        $(pkg-config --cflags --libs libusb-1.0)
fi

exec python3 "$mixer" \
    --display-helper "$helper" \
    --display-replay "$replay" \
    "$@"
