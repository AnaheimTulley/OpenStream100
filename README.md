# OpenStream100

OpenStream100 is an unofficial Linux driver and PipeWire mixer for the
[Hercules Stream 100](https://www.hercules.com/). It turns the controller into a
native Linux per-application mixer, drives its full 480×272 display, and adds
desktop and Android virtual mixers for controlling the same audio setup without
the hardware in front of you.

[Download the latest release](https://github.com/AnaheimTulley/OpenStream100/releases/latest)
· [Report a problem](https://github.com/AnaheimTulley/OpenStream100/issues)
· [Android source](android/OpenStream100Remote)

## What it can do

### Mixer controls

- Control four PipeWire applications, outputs, or inputs at a time.
- Create up to eight mixer pages, each with its own assignments and colours.
- Adjust volume with the four hardware encoders and soft-mute with the four
  buttons beneath them.
- Show live mono or stereo activity meters with Classic, Segmented, Rounded, or
  Slim visualiser styles.
- Display the assigned applications' icons, volume markers, and percentage
  badges directly on the controller.
- Adjust encoder sensitivity and screen brightness, with settings retained
  between sessions.

### Programmable buttons

The four numbered buttons can be assigned to:

- Microphone or speaker mute
- Play/pause, previous track, or next track
- Previous or next mixer page
- An exact volume preset for any of the four channels
- No action

Assigned buttons illuminate automatically. Media actions use `playerctl`.

### Controller display

OpenStream100 supports three display modes:

- **Mixer** — live channel names, icons, volume, mute state, and audio meters.
- **Full-screen image** — edge-to-edge PNG, JPEG, WebP, or BMP artwork while the
  physical audio controls continue working.
- **Notepad** — typed or pasted reference text with saved font size, family,
  weight, colour, and alignment.

The mixer can use a custom background and one of several button-label overlays.
A transparent 480×80 template can also be exported and edited to create a
custom overlay.

### Virtual mixers

The desktop virtual mixer mirrors the Stream 100 layout in a resizable GTK4
window. It provides faders, mute controls, activity meters, page navigation,
programmable actions, and application icons, and it remains usable when the
physical controller is disconnected.

The companion **OpenStream100 Remote** Android app provides a landscape
four-channel mixer with smooth touch faders, live meters, mute controls, page
navigation, programmable buttons, and assigned application icons.

## System requirements

| Component | Requirement |
| --- | --- |
| Hardware | Hercules Stream 100, USB ID `06f8:e053`, for physical controls; the desktop mixer can run without it |
| Linux audio | PipeWire with its PulseAudio-compatible service |
| Desktop | A GTK4-capable Linux desktop with systemd user services |
| Packaged Linux builds | Fedora 44 x86_64, Debian/Ubuntu amd64, and Arch Linux x86_64 |
| Android remote | Android 8.0 (API 26) or later |
| Network pairing | Linux computer and phone on the same trusted local network |

The Linux packages install the application, required runtime dependencies,
desktop entry, icon, AppStream metadata, systemd user services, and the udev rule
needed to access the controller as a normal desktop user.

## Installation

Download the appropriate files from the
[latest GitHub release](https://github.com/AnaheimTulley/OpenStream100/releases/latest).
The release includes `SHA256SUMS` for verifying downloaded files.

### Fedora

```bash
sudo dnf install ./hercules-stream100-0.17.1-1.fc44.x86_64.rpm
```

### Debian or Ubuntu

```bash
sudo apt install ./hercules-stream100_0.17.1-1_amd64.deb
```

### Arch Linux and derivatives

```bash
sudo pacman -U ./hercules-stream100-0.17.1-1-x86_64.pkg.tar.zst
```

The maintained `PKGBUILD` and its build instructions are available in
[packaging/arch](packaging/arch).

### Android

Download `openstream100remote.apk` on the phone and approve installation from
the browser or file manager when Android asks. The APK is currently distributed
directly through GitHub rather than an app store.

After installing or updating the Linux package, reconnect the Stream 100 if the
new USB access rule has not taken effect. OpenStream100 must run as your normal
desktop user—never with `sudo`.

## First run

1. Connect the Hercules Stream 100 by USB.
2. Open **OpenStream100** from the desktop application menu, or run
   `hercules-stream100` in a terminal.
3. Select **Start mixer** in the control panel.
4. Start audio in the applications you want to control.
5. Select **Refresh applications**, assign the four channels, and choose
   **Apply changes**.
6. Configure additional pages, display options, button actions, and automatic
   startup as required.

Applying new assignments restarts an active mixer automatically. Settings are
stored per user in:

```text
~/.config/hercules-stream100/
```

## Pairing the Android remote

1. Enable **Android remote control** in the Linux control panel and start the
   mixer.
2. Open OpenStream100 Remote on the phone.
3. Select the automatically discovered Linux computer.
4. Enter the temporary six-digit PIN that appears in the desktop GUI.

Pairing is normally required only once. The phone stores its device credential
and reconnects automatically afterward, including when the computer's local IP
address changes. Paired phones can be revoked individually from the Linux
control panel. QR pairing remains available as a fallback.

If the phone cannot connect, confirm that both devices are on the same LAN and
allow TCP port `47680` through the Linux firewall. The current protocol uses
authenticated cleartext HTTP and is intended only for a trusted local network;
do not expose the remote port to the internet. Technical details are documented
in the [remote protocol specification](work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.17.0/REMOTE-PROTOCOL.md).

## Service management

Use the **Start mixer** and **Stop mixer** buttons in the control panel for
normal operation. The primary mixer runs as the per-user service
`hercules-stream100.service`; the separate display broker retains the USB and
display session while the mixer restarts.

An older compatibility unit named `hercules-stream100-mixer.service` is also
installed. Do not start it alongside the primary service because both processes
would compete for the same USB device. If it was started manually, stop it with:

```bash
systemctl --user stop hercules-stream100-mixer.service
```

Useful diagnostics include:

```bash
systemctl --user status hercules-stream100.service
journalctl --user -u hercules-stream100.service -f
```

## Troubleshooting

### The controller is not detected

- Unplug and reconnect it after package installation.
- Confirm it appears in `lsusb` as `06f8:e053`.
- Run OpenStream100 as the logged-in desktop user, not as root.
- Check that the legacy mixer service is not holding the USB device.

### An application is missing from the assignment list

Start audio playback in that application, then select **Refresh applications**.
PipeWire applications generally become assignable only after creating an active
audio stream.

### The Android app says it failed to connect

- Confirm the phone and computer are on the same local network.
- Check that Android remote control is enabled and the Linux mixer is running.
- Allow inbound TCP port `47680` in the Linux firewall.
- Disable VPN or guest-network isolation temporarily when testing discovery.
- Use QR pairing if mDNS discovery is unavailable on the network.

### A previously paired phone cannot reconnect

Confirm that the paired device still appears in the Linux control panel. If its
credential was cleared from the phone or revoked on Linux, remove the saved
connection and pair the phone again using a new PIN.

## Building from source

The current Linux source and package build kit live under:

```text
work/package-v102-base/hercules-stream100-rpm-build-kit/
```

Build an RPM:

```bash
cd work/package-v102-base/hercules-stream100-rpm-build-kit
./build-stream100-rpm.sh
```

Build a Debian package:

```bash
cd work/package-v102-base/hercules-stream100-rpm-build-kit
./build-stream100-deb.sh
```

Build an Arch package on Arch Linux:

```bash
cd packaging/arch
./build-stream100-arch.sh
```

Build the Android app with Java 17 and Android SDK 37:

```bash
cd android/OpenStream100Remote
./gradlew lintDebug assembleDebug
```

The package builds run the Python regression tests, virtual-mixer tests, remote
protocol tests, native meter checks, desktop-entry validation, and AppStream
validation. Android builds run the project's lint checks through Gradle.

## Contributing

Bug reports and pull requests are welcome. When reporting a problem, include
the Linux distribution, package version, whether the physical controller is
connected, the relevant user-service logs, and clear reproduction steps. Please
avoid attaching credentials or the contents of paired-device configuration
files.

See the [issue tracker](https://github.com/AnaheimTulley/OpenStream100/issues)
for existing reports and planned work.

## License and trademark notice

OpenStream100 is released under the
[MIT License](work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.17.0/LICENSE).

This is an unofficial, community-driven project. It is not affiliated with,
authorized by, or endorsed by Hercules or Guillemot Corporation S.A. Hercules
and its logo are trademarks and copyrighted property of Guillemot Corporation
S.A.
