# OpenStream100
A linux compatible driver for the Hercules Stream 100.

This is an unofficial, community-driven project and is not affiliated with, authorized, or endorsed by Hercules. Hercules and its logo are registered trademarks and copyright properties of Guillemot Corporation S.A.

This has currently only been tested in Fedora 44.

# Hercules Stream 100 for Fedora Linux

This beta provides four per-application PipeWire volume controls, four soft-mute
buttons, saved channel assignments, and a live 480×272 mixer display.

## 1. Install the Fedora dependencies

```bash
sudo dnf install gcc libusb1-devel python3-pyusb python3-pillow pipewire-utils wireplumber fontconfig
```

## 2. Install USB access (once)

From this folder, run:

```bash
sudo cp 70-hercules-stream100.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug and reconnect the Stream 100 afterward. Run the mixer as your normal
desktop user, never with `sudo`.

## 3. Start the mixer

```bash
chmod +x run-stream100-mixer.sh
./run-stream100-mixer.sh
```

The launcher compiles the small native display helper automatically each time,
ensuring an older experimental helper cannot be reused after an update. Existing
channel and button settings are read from:

```text
~/.config/hercules-stream100/config.json
```

The LCD takes about five seconds to initialize. Normal operation uses the same
one-second compact-renewal cadence validated by the hardware tests.

## Configuration commands

Choose the four applications again:

```bash
./run-stream100-mixer.sh --setup
```

Relearn the four physical mute buttons:

```bash
./run-stream100-mixer.sh --calibrate-buttons
```

Show currently available PipeWire playback applications:

```bash
./run-stream100-mixer.sh --list-streams
```

Run the controls without the LCD:

```bash
./run-stream100-mixer.sh --no-display
```

Show the diagnostic solid-color screen:

```bash
./run-stream100-mixer.sh --display-solid-test
```

This diagnostic floods all 256 framebuffer palette entries with red. The
generated background should therefore become red even if the controller remaps
pixel indices; native hardware-composited faders or icons may remain above it.

Run the generated mixer-UI test and save its terminal log:

```bash
bash run-stream100-mixer.sh --display-protocol-test 2>&1 | tee stream100-display-test.log
```


