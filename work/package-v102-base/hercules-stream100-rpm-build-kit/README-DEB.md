# OpenStream100 Debian/Ubuntu .deb build kit

This kit builds the native display helper with the system compiler and then
creates a binary `.deb` package for Debian-based distributions (Debian, Ubuntu,
Linux Mint, Pop!_OS, elementary OS, etc.).

The `.deb` installs separate mixer and display-broker user services. The display
broker keeps the USB display session and resident OpenStream100 logo alive when
the mixer is stopped or restarted, avoiding cold display initialization during
ordinary configuration changes and service restarts.

## Quick build

```bash
chmod +x build-stream100-deb.sh
./build-stream100-deb.sh
```

The finished `.deb` is written to this kit's `dist` directory.

## Build and install

```bash
./build-stream100-deb.sh --install
```

This will install the build dependencies (via `apt`), build the package, and
install it. The migration step moves any earlier personal installation files to
a recoverable backup under `~/.local/share/hercules-stream100-pre-deb-backup`.
Saved assignments, colours, imported images, notepad text, display mode, and button calibration
in `~/.config/hercules-stream100` are **not** moved or deleted.

## Manual install after building

```bash
sudo dpkg -i dist/hercules-stream100_0.17.0-3_amd64.deb
sudo apt-get install -f   # resolve any missing dependencies
```

## Build dependencies

The script will attempt to install these automatically. You can also install
them manually:

```bash
sudo apt-get update
sudo apt-get install -y build-essential dpkg-dev debhelper \
    dh-systemd libusb-1.0-0-dev libappstream-dev \
    desktop-file-utils python3
```

## Architecture

This kit builds for **amd64** (x86_64). The C display helper is compiled
natively; Python components are architecture-independent.
