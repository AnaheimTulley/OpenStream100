#!/usr/bin/bash
set -euo pipefail

kit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$kit_dir/hercules-stream100-0.17.0"
output_dir="$kit_dir/dist"
install_after_build=0

if [[ "${1:-}" == "--install" ]]; then
    install_after_build=1
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--install]"
    exit 2
fi

# --- Verify source tree ---
for required in \
    "$source_dir/stream100-display-helper.c" \
    "$source_dir/stream100-mixer.py" \
    "$source_dir/stream100_virtual_mixer.py" \
    "$source_dir/stream100-test-virtual-mixer.py" \
    "$source_dir/stream100-display-service.py" \
    "$source_dir/stream100-control.py" \
    "$source_dir/packaging/hercules-stream100" \
    "$source_dir/packaging/hercules-stream100.service" \
    "$source_dir/packaging/hercules-stream100-display.service"; do
    if [[ ! -f "$required" ]]; then
        echo "Missing source file: $required"
        exit 1
    fi
done

# --- Build dependencies check ---
build_packages=(
    build-essential
    dpkg-dev
    debhelper-compat
    dh-systemd
    libusb-1.0-0-dev
    libappstream-dev
    desktop-file-utils
    python3
    python3-pil
    python3-usb
)

if (( install_after_build )); then
    echo "Installing Debian build dependencies..."
    sudo apt-get update
    sudo apt-get install -y "${build_packages[@]}"
fi

missing=()
for tool in dpkg-deb gcc pkg-config desktop-file-validate; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
# Check libusb-1.0 dev headers
if ! pkg-config --exists libusb-1.0 2>/dev/null; then
    missing+=("libusb-1.0-dev (pkg-config)")
fi
if (( ${#missing[@]} > 0 )); then
    echo "Build dependencies are missing. Install them with:"
    echo "  sudo apt-get install ${build_packages[*]}"
    exit 1
fi

# --- Create temporary build tree ---
build_root="$(mktemp -d -t hercules-stream100-deb.XXXXXX)"
trap 'rm -rf -- "$build_root"' EXIT

# Standard Debian package directory layout
pkg_dir="$build_root/hercules-stream100-0.17.0"
mkdir -p "$pkg_dir/usr/libexec/hercules-stream100" \
    "$pkg_dir/usr/bin" \
    "$pkg_dir/usr/lib/systemd/user" \
    "$pkg_dir/usr/lib/udev/rules.d" \
    "$pkg_dir/usr/share/applications" \
    "$pkg_dir/usr/share/icons/hicolor/scalable/apps" \
    "$pkg_dir/usr/share/metainfo" \
    "$pkg_dir/usr/share/man/man1" \
    "$pkg_dir/DEBIAN"

# --- Compile the C display helper ---
echo "Compiling stream100-display-helper..."
gcc -O2 -std=c11 -Wall -Wextra \
    "$source_dir/stream100-display-helper.c" \
    -o "$pkg_dir/usr/libexec/hercules-stream100/stream100-display-helper" \
    $(pkg-config --cflags --libs libusb-1.0)

# --- Install Python scripts and data files ---
echo "Installing application files..."
install -pm0755 "$source_dir/run-stream100-control.sh" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/run-stream100-mixer.sh" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/run-stream100-virtual-mixer.sh" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/stream100-control.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/stream100-display-service.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/stream100-mixer.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/stream100-mixer-alpha.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0755 "$source_dir/stream100_virtual_mixer.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/stream100_channel_icons.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/stream100_version.py" \
    "$pkg_dir/usr/libexec/hercules-stream100/"

# --- Install image and data resources ---
install -pm0644 "$source_dir/button_labels_overlay_boxes.png" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/button_labels_overlay_basic.png" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/button_labels_overlay_glass.png" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/button_labels_overlay_template.png" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/stream100-display-replay.bin" \
    "$pkg_dir/usr/libexec/hercules-stream100/"
install -pm0644 "$source_dir/openstream100-startup.png" \
    "$pkg_dir/usr/libexec/hercules-stream100/"

# --- Install wrapper binary ---
install -Dpm0755 "$source_dir/packaging/hercules-stream100" \
    "$pkg_dir/usr/bin/hercules-stream100"

# --- Install systemd user services ---
install -Dpm0644 "$source_dir/packaging/hercules-stream100.service" \
    "$pkg_dir/usr/lib/systemd/user/hercules-stream100.service"
install -Dpm0644 "$source_dir/packaging/hercules-stream100-display.service" \
    "$pkg_dir/usr/lib/systemd/user/hercules-stream100-display.service"
# Use the root-level mixer service if present, else fall back to packaging/
if [[ -f "$source_dir/hercules-stream100-mixer.service" ]]; then
    install -Dpm0644 "$source_dir/hercules-stream100-mixer.service" \
        "$pkg_dir/usr/lib/systemd/user/hercules-stream100-mixer.service"
else
    install -Dpm0644 "$source_dir/packaging/hercules-stream100-mixer.service" \
        "$pkg_dir/usr/lib/systemd/user/hercules-stream100-mixer.service"
fi

# --- Install udev rules ---
install -Dpm0644 "$source_dir/70-hercules-stream100.rules" \
    "$pkg_dir/usr/lib/udev/rules.d/70-hercules-stream100.rules"

# --- Install desktop entry, icon, metainfo, man page ---
desktop-file-install \
    --dir="$pkg_dir/usr/share/applications" \
    "$source_dir/packaging/com.hercules.Stream100.desktop"
install -Dpm0644 "$source_dir/com.hercules.Stream100.svg" \
    "$pkg_dir/usr/share/icons/hicolor/scalable/apps/com.hercules.Stream100.svg"
install -Dpm0644 "$source_dir/packaging/com.hercules.Stream100.metainfo.xml" \
    "$pkg_dir/usr/share/metainfo/com.hercules.Stream100.metainfo.xml"
install -Dpm0644 "$source_dir/packaging/hercules-stream100.1" \
    "$pkg_dir/usr/share/man/man1/hercules-stream100.1"

# --- Install license and docs ---
install -Dpm0644 "$source_dir/LICENSE" \
    "$pkg_dir/usr/share/doc/hercules-stream100/LICENSE"
install -Dpm0644 "$source_dir/README-STREAM100.md" \
    "$pkg_dir/usr/share/doc/hercules-stream100/README-STREAM100.md"

# --- Generate control file ---
echo "Generating package control metadata..."
cat > "$pkg_dir/DEBIAN/control" <<'EOF'
Package: hercules-stream100
Version: 0.17.0-3
Section: sound
Priority: optional
Architecture: amd64
Depends: bash, fontconfig, gir1.2-gtk-4.0, hicolor-icon-theme,
         pipewire, pipewire-pulse, python3, python3-gi,
         python3-pil, python3-usb, systemd, wireplumber,
         libusb-1.0-0
Recommends: playerctl
Suggests: appstream
Installed-Size: 0
Maintainer: OpenStream100 contributors <openstream100@users.noreply.github.com>
Description: OpenStream100 PipeWire controller for Hercules Stream 100
 OpenStream100 provides up to eight pages of four per-application PipeWire
 volume controls for the Hercules Stream 100 hardware. It includes four
 soft-mute buttons, programmable action buttons with LEDs, saved assignments,
 channel colours, mixer backgrounds, and separate full-screen image and notepad modes.
 .
 Optional native meters keep a white marker at the current volume while
 independent left/right coloured bars follow live PipeWire audio activity.
 The controller screen brightness can be adjusted live and saved independently.
 Per-channel application icons and on-screen button labels provide clear visual
 feedback. It includes a GTK4 configuration panel and drives the full 480x272
 display on Debian-based Linux distributions.
Homepage: https://github.com/openstream100/openstream100
EOF

# --- Generate postinst / postrm maintainer scripts ---
cat > "$pkg_dir/DEBIAN/postinst" <<'POSTINST'
#!/bin/bash
set -e

if [ "$1" = "configure" ]; then
    # Reload systemd user units so the new services are available
    # We can't reliably restart user services from a system-level script,
    # so just notify the user
    echo "hercules-stream100: package configured successfully."
    echo "hercules-stream100: Run 'systemctl --user daemon-reload' to activate services."
fi

#DEBHELPER#

exit 0
POSTINST
chmod 0755 "$pkg_dir/DEBIAN/postinst"

cat > "$pkg_dir/DEBIAN/postrm" <<'POSTRM'
#!/bin/bash
set -e

if [ "$1" = "purge" ]; then
    # User config in ~/.config/hercules-stream100 is intentionally preserved
    :
fi

#DEBHELPER#

exit 0
POSTRM
chmod 0755 "$pkg_dir/DEBIAN/postrm"

# --- Validate desktop file ---
echo "Validating desktop entry..."
desktop-file-validate \
    "$pkg_dir/usr/share/applications/com.hercules.Stream100.desktop"

# --- Validate Python syntax ---
echo "Validating Python source files..."
pushd "$source_dir"
python3 -m py_compile \
    stream100-control.py \
    stream100-display-service.py \
    stream100-mixer.py \
    stream100-mixer-alpha.py \
    stream100_virtual_mixer.py \
    stream100-test-virtual-mixer.py \
    stream100-test-notepad.py \
    stream100_channel_icons.py \
    stream100_version.py
python3 stream100-test-notepad.py
python3 stream100-test-virtual-mixer.py
popd

# --- Build the .deb ---
echo "Building .deb package..."
mkdir -p "$output_dir"
dpkg-deb --build --root-owner-group "$pkg_dir" \
    "$output_dir/hercules-stream100_0.17.0-3_amd64.deb"

echo ""
echo "============================================"
echo " Build complete!"
echo " Package: $output_dir/hercules-stream100_0.17.0-3_amd64.deb"
echo "============================================"
echo ""

# --- Optional: install ---
if (( install_after_build )); then
    echo "Installing the .deb package..."
    sudo dpkg -i "$output_dir/hercules-stream100_0.17.0-3_amd64.deb"

    # Fix any missing dependencies
    echo "Checking for missing dependencies..."
    sudo apt-get install -f -y

    # Migrate any previous user installation
    echo "Migrating previous user installation (if any)..."
    hercules-stream100 --migrate-user-install || true

    # Reload user systemd
    systemctl --user daemon-reload

    # Restart services
    echo "Starting OpenStream100 display service..."
    systemctl --user restart hercules-stream100-display.service || true

    echo ""
    echo "Installation complete. Open OpenStream100 from your application menu."
fi
