#!/usr/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${script_dir}/dist"
source_cache="${script_dir}/.sources"
build_root="${script_dir}/.build"
install_after_build=0

if [[ "${1:-}" == "--install" ]]; then
    install_after_build=1
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--install]"
    exit 2
fi

for tool in makepkg pacman gcc pkg-config desktop-file-validate; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing required Arch build command: $tool"
        echo "Install base-devel and desktop-file-utils, then try again."
        exit 1
    fi
done

mkdir -p "$output_dir" "$source_cache" "$build_root"
makepkg_args=(
    --syncdeps
    --cleanbuild
    --clean
    --force
    --noconfirm
)
if (( install_after_build )); then
    makepkg_args+=(--install)
fi

echo "Building the OpenStream100 Arch package..."
cd "$script_dir"
makepkg --printsrcinfo > .SRCINFO
PKGDEST="$output_dir" \
SRCDEST="$source_cache" \
BUILDDIR="$build_root" \
    makepkg "${makepkg_args[@]}"

mapfile -t built_packages < <(
    PKGDEST="$output_dir" makepkg --packagelist | while IFS= read -r package; do
        [[ -f "$package" ]] && printf '%s\n' "$package"
    done
)
if (( ${#built_packages[@]} == 0 )); then
    echo "The build completed without producing an Arch package."
    exit 1
fi

echo "Finished Arch package: ${built_packages[0]}"
if (( install_after_build )); then
    systemctl --user daemon-reload
    systemctl --user restart hercules-stream100-display.service || true
    echo "Installation complete. Open OpenStream100 from your app menu."
fi
