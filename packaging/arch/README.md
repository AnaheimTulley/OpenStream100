# OpenStream100 Arch Linux package

This directory builds OpenStream100 for Arch Linux and Arch-based distributions
such as EndeavourOS, CachyOS, and Manjaro. The PKGBUILD downloads the official
`v0.17.0` GitHub tag and verifies its SHA-256 checksum before building.

## Build

Install the standard Arch build tools, clone this repository, and run:

```bash
sudo pacman -S --needed base-devel desktop-file-utils
cd packaging/arch
./build-stream100-arch.sh
```

The finished `hercules-stream100-0.17.0-1-<architecture>.pkg.tar.zst` is written
to `packaging/arch/dist/`. To build and install it in one operation, use:

```bash
./build-stream100-arch.sh --install
```

You can also install a previously built package directly:

```bash
sudo pacman -U dist/hercules-stream100-0.17.0-1-x86_64.pkg.tar.zst
systemctl --user daemon-reload
systemctl --user enable --now hercules-stream100-display.service
```

The first build asks `pacman` to install any missing dependencies declared by
the PKGBUILD. Runtime dependencies include GTK4, PipeWire/Pulse, WirePlumber,
libusb, PyGObject, Pillow, and PyUSB. `playerctl` is optional and enables the
programmable media-control actions.

The package installs the application under `/usr/lib/hercules-stream100`,
the launcher under `/usr/bin`, user services under `/usr/lib/systemd/user`, and
the device-access rule under `/usr/lib/udev/rules.d`. Reconnect the controller
after the initial installation if the new udev permission has not taken effect.
