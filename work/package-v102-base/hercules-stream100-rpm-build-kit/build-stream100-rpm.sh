#!/usr/bin/bash
set -euo pipefail

kit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_archive="$kit_dir/hercules-stream100-0.15.9.tar.gz"
spec_file="$kit_dir/hercules-stream100.spec"
output_dir="$kit_dir/dist"
install_after_build=0

if [[ "${1:-}" == "--install" ]]; then
    install_after_build=1
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--install]"
    exit 2
fi

for required in "$source_archive" "$spec_file"; do
    if [[ ! -f "$required" ]]; then
        echo "Missing RPM build input: $required"
        exit 1
    fi
done

build_packages=(
    appstream
    desktop-file-utils
    gcc
    libappstream-glib
    libusb1-devel
    rpm-build
    systemd-rpm-macros
)

if (( install_after_build )); then
    echo "Installing Fedora RPM build dependencies..."
    sudo dnf install -y "${build_packages[@]}"
fi

missing=()
for tool in rpmbuild gcc pkg-config desktop-file-validate appstream-util; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if (( ${#missing[@]} > 0 )) || ! pkg-config --exists libusb-1.0 2>/dev/null; then
    echo "RPM build dependencies are missing. Install them with:"
    echo "  sudo dnf install ${build_packages[*]}"
    exit 1
fi

topdir="$(mktemp -d -t hercules-stream100-rpmbuild.XXXXXX)"
trap 'rm -rf -- "$topdir"' EXIT
mkdir -p "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp -- "$source_archive" "$topdir/SOURCES/"
cp -- "$spec_file" "$topdir/SPECS/"

echo "Building the OpenStream100 RPM..."
rpmbuild --define "_topdir $topdir" -ba \
    "$topdir/SPECS/hercules-stream100.spec"

mkdir -p "$output_dir"
find "$topdir/RPMS" "$topdir/SRPMS" -type f -name '*.rpm' \
    -exec cp -- {} "$output_dir/" \;

mapfile -t installable_rpms < <(
    find "$output_dir" -maxdepth 1 -type f \
        -name 'hercules-stream100-0.15.9-1*.rpm' \
        ! -name '*.src.rpm' \
        ! -name '*-debuginfo-*' \
        ! -name '*-debugsource-*'
)
if (( ${#installable_rpms[@]} != 1 )); then
    echo "Could not identify the finished binary RPM in $output_dir."
    exit 1
fi

echo "Finished RPM: ${installable_rpms[0]}"
if (( install_after_build )); then
    echo "Installing the RPM and migrating the verified personal build..."
    mixer_was_active=0
    if systemctl --user is-active --quiet hercules-stream100.service; then
        mixer_was_active=1
        systemctl --user stop hercules-stream100.service
    fi
    sudo dnf install -y "${installable_rpms[0]}"
    hercules-stream100 --migrate-user-install
    systemctl --user daemon-reload
    systemctl --user restart hercules-stream100-display.service
    if (( mixer_was_active )); then
        systemctl --user start hercules-stream100.service
    fi
    echo "Installation complete. Open OpenStream100 from your app menu."
fi
