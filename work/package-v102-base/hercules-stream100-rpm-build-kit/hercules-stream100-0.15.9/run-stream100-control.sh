#!/usr/bin/bash
set -euo pipefail

app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
control_panel="$app_dir/stream100-control.py"

if ! command -v python3 >/dev/null 2>&1 || ! command -v pw-dump >/dev/null 2>&1; then
    echo "The Stream 100 desktop dependencies are missing. Install them with:"
    echo "  sudo dnf install python3-gobject gtk4 pipewire-utils"
    exit 1
fi

if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1; then
    echo "The GTK desktop library is missing. Install it with:"
    echo "  sudo dnf install python3-gobject gtk4"
    exit 1
fi

if [[ ! -f "$control_panel" ]]; then
    echo "Missing application file: $control_panel"
    exit 1
fi

if [[ -z "${GSK_RENDERER:-}" ]]; then
    export GSK_RENDERER=gl
fi

exec python3 "$control_panel" "$@"
