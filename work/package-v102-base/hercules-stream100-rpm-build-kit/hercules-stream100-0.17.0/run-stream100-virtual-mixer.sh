#!/usr/bin/bash
set -euo pipefail

app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
virtual_mixer="$app_dir/stream100_virtual_mixer.py"

for required in python3 pw-dump wpctl; do
    if ! command -v "$required" >/dev/null 2>&1; then
        echo "Missing required command: $required"
        exit 1
    fi
done

if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1; then
    echo "The GTK desktop library is missing. Install python3-gobject and gtk4."
    exit 1
fi

if [[ ! -f "$virtual_mixer" ]]; then
    echo "Missing application file: $virtual_mixer"
    exit 1
fi

if [[ -z "${GSK_RENDERER:-}" ]]; then
    export GSK_RENDERER=gl
fi

exec python3 "$virtual_mixer" "$@"
