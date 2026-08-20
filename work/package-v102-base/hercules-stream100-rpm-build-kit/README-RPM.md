# OpenStream100 Fedora RPM build kit

This kit builds the native display helper with Fedora's own compiler and then
creates both a binary RPM and a source RPM.

The RPM installs separate mixer and display-broker user services. The display
broker keeps the USB display session and resident OpenStream100 logo alive when
the mixer is stopped or restarted, avoiding cold display initialization during
ordinary configuration changes and service restarts.

To build, install, and migrate the current personal installation in one step:

```bash
chmod +x build-stream100-rpm.sh
./build-stream100-rpm.sh --install
```

The migration is recoverable: the earlier application files are moved to a
folder named `hercules-stream100-pre-rpm-backup` under `~/.local/share`. Saved
assignments, colours, imported images, notepad text, display mode, and button calibration in
`~/.config/hercules-stream100` are not moved or deleted.

The finished RPMs are written to this kit's `dist` directory. To build without
installing anything, first install the build dependencies printed by the script,
then run:

```bash
./build-stream100-rpm.sh
```
