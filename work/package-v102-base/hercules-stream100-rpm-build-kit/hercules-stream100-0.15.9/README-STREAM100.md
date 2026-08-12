# OpenStream100 for Fedora Linux

OpenStream100 provides up to eight pages of four per-application PipeWire volume controls for the
Hercules Stream 100, four soft-mute buttons, four programmable action buttons
with LEDs, saved channel assignments and colours, custom display images, and a
live full-screen 480x272 display with Mixer and Full-screen image modes.

Hercules is a trademark of Guillemot Corporation. This independent Linux
project is not affiliated with or endorsed by Guillemot Corporation.

## Fedora RPM

The RPM edition installs the application, precompiled display helper, desktop
entry, mixer and persistent display-broker user services, icon, manual page,
AppStream metadata, and USB access rule
in standard Fedora system locations. After building the supplied RPM kit, the
following single command builds, installs, and safely migrates the earlier
personal installation:

```bash
./build-stream100-rpm.sh --install
```

The migration moves the previous application directory into a recoverable
backup under `~/.local/share`; it retains assignments, colours, imported
images, the selected display mode, and button calibration.
The sections below describe the portable beta installer, which remains useful
as a rollback and development build.

### Important: Mixer Service Management

The RPM installs two systemd user services:
- **`hercules-stream100.service`** — the primary mixer service managed by the GUI control panel.
- **`hercules-stream100-mixer.service`** — a duplicate unit kept for legacy/manual use only.

**Always use the Start/Stop mixer buttons in the OpenStream100 GUI control panel** to manage the mixer.
Do not manually start `hercules-stream100-mixer.service` via `systemctl` or other scripts, as it will
claim the USB device and prevent the GUI from controlling `hercules-stream100.service`.
If this occurs, stop the conflicting service with:

```bash
systemctl --user stop hercules-stream100-mixer.service
```

Then use the GUI button to start the mixer normally.

## 1. Install the Fedora dependencies

```bash
sudo dnf install gcc libusb1-devel python3-pyusb python3-pillow python3-gobject gtk4 pipewire-utils pulseaudio-utils wireplumber fontconfig playerctl
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

## 3. Install and open the desktop app

```bash
chmod +x install-stream100-app.sh
./install-stream100-app.sh
```

The installer copies the application into your personal applications folder,
adds **OpenStream100** to Fedora's app launcher, and opens its control
panel. It does not need administrator access. The control panel provides:

- Four application assignment menus
- Up to eight saved mixer pages with four controls and actions per page
- A custom display colour for each control
- Adjustable 0.5% to 4.0% knob sensitivity
- Optional live meters with selectable Mono/Stereo monitoring and four visualiser styles
- Four programmable buttons with automatic LED illumination
- Mixer and Full-screen image display modes
- Separate imported images for the mixer background and full-screen artwork
- Built-in and user-designed button-label overlays with an exportable template
- Controller and mixer status
- **Start mixer** and **Stop mixer** buttons
- An **Apply changes** button
- Optional automatic startup when you sign in

Start audio in an application and select **Refresh applications** if it is not
shown yet. Applying new assignments restarts a running mixer automatically.
The colour buttons beside the four assignment menus change both the channel
accent and its fast percentage badge on the controller. Existing configurations
receive the original cyan, green, amber, and blue defaults automatically.
The percentage is rendered as a compact 24x24 badge inside the controller's
fixed native object. Its surrounding pixels are sampled from the active mixer
background so the carrier blends into plain panels and imported artwork. The
object stays fully opaque because the firmware cannot reliably mix transparent
and opaque runs in one live object.
At startup, OpenStream100 clears the mapped firmware panel and meter layers as
soon as initialization permits, primes the controller with a black frame, then
shows the packaged OpenStream100 logo before revealing the completed saved
screen. The logo uses its own colour palette, which is replaced during the
hidden final handoff. A very early Hercules firmware flash can still occur
before software display commands are accepted.
Choose a PNG, JPEG, WebP, or BMP under **Mixer background**. The application
keeps its own copy, crops it to fill 480x272, and darkens it beneath translucent
channel panels so labels remain readable. Select **Remove** to restore the
original dark background. Backgrounds are committed only at startup or after
**Apply changes**; knob and mute updates continue using compact native objects.

Under **Button overlay style**, choose Boxes, Basic, Glass, or Custom. Select
**Save template…** to save the supplied transparent 480x80 PNG design guide,
edit a copy in an image editor without changing its pixel dimensions, then use
**Import custom overlay…**. OpenStream100 validates the PNG, stores its own copy
at `~/.config/hercules-stream100/button-overlay-custom.png`, selects Custom, and
keeps it across restarts. Select **Apply changes** to show it on the controller.
Removing the custom overlay safely switches the selection back to Boxes.

Under **Screen content**, choose **Full-screen image** to replace the mixer UI
with separate edge-to-edge artwork. Select an image in the section that appears,
then choose **Apply changes**. The four encoders and mute buttons continue to
control their assigned audio, but mixer labels and percentage badges stay hidden
so the artwork owns the entire display. Switch back to **Mixer** and apply the
change to restore the live mixer screen. Image mode also disables the firmware's
three lower action-zone dividers, leaving a clean edge-to-edge picture.
Existing channel and button settings remain in:

```text
~/.config/hercules-stream100/config.json
```

Under **Mixer pages**, add up to eight pages and select which page to edit.
Each page stores its own four application assignments, colours, programmable
button actions, and preset-volume targets. Assign **Next mixer page** or
**Previous mixer page** to a hardware button on each page where navigation is
needed. Page changes wrap around, and the display numbers its tracks 1–4, 5–8,
and onward. Page-specific palettes and labels are replaced while the panel
remains visible. Existing single-page configurations become Page 1
automatically.

Under **Screen content**, enable **Show volume and activity meters** to restore
the controller's original native meters below each application name. The white
marker always shows the selected volume setting. Use **Activity monitoring** to
choose how the coloured bars react. **Stereo** independently follows the left
and right channels produced by the assigned PipeWire application. **Mono** asks
PipeWire-Pulse for one mixed monitor channel and mirrors its level across both
bars. Stereo is the default for existing configurations, and the selection is
saved across restarts. Live monitoring uses low-rate peak streams; an unavailable or
unmonitorable assignment leaves its activity bars empty without changing the
volume marker. Muted assignments also show no activity. These are native panel
objects, so movement does not redraw the framebuffer. OpenStream100 refreshes
the activity objects continuously at the meter sampling rate, including while
the decoded value remains steady, so sustained audio remains visible instead
of appearing only when it crosses a display quantisation boundary.
Application activity is captured through the per-stream monitor exposed by
PipeWire's PulseAudio-compatible service; OpenStream100 calculates either
separate left/right or mixed mono 40 ms peaks from that raw monitor signal. This avoids asking
WirePlumber to attach a normal capture client directly to another application's
playback node.

Use **Visualiser style** to select Classic, Segmented, Rounded, or Slim. All
four are the controller's resident full-height meter geometries, selected by
the native style field in each `0x32` panel record. They preserve the separate
32x32 percentage badges, independently animated left/right activity, and white
saved-volume markers. Style changes do not stamp static artwork into the mixer
framebuffer and do not redraw the framebuffer at audio rate.

Use the **Screen brightness** slider to set the hardware display from 10% to
100% in five-percent steps. The setting is saved immediately and a running
mixer applies it live through the controller's native backlight command, without
redrawing or blanking the screen. Mixer and Full-screen image modes share the
setting. The branded startup logo deliberately retains its separate
hardware-validated brightness so startup remains clean and legible.

Under **Knob sensitivity**, choose how quickly all four controls change volume.
The default 1.0% setting preserves OpenStream100's original response; lower
values provide finer adjustment and higher values make larger changes from the
same movement. Select **Apply changes** to save the setting and restart a
running mixer.

Under **Programmable buttons**, assign microphone mute, speaker mute,
play/pause, previous track, next track, page navigation, or an exact channel-volume preset to
each of the four numbered hardware buttons. For a volume preset, choose Control
1–4 and any level from 0% to 100%. Pressing the button immediately applies that
level and restores the channel if it was soft-muted. A button's LED stays
steadily illuminated when a function is assigned and remains off when set to
**Do nothing**. Media controls use `playerctl`, which is installed automatically
by the RPM package.

The controller's native display setup takes a few seconds. OpenStream100 keeps
the backlight off while the captured initialization and complete branded frame
are prepared, preventing old firmware layers or partially written pixels from
flashing on screen. The controller pauses its first-frame compositor at zero
brightness. OpenStream100 keeps the captured initialization setting at zero,
then primes a framebuffer whose pixels and entire 256-colour palette are black.
The controller can only finish its inherited native-layer convergence while the
display is active, so that firmware-owned interval may still be briefly visible
before the blank primer settles. OpenStream100 then hides the panel again. The
branded framebuffer can replace
the palette and pixels through the controller's proven hidden redraw path and
appear complete at normal brightness as a clean **Starting** screen
with an initialization bar. Because
this controller exposes full-frame planes while
they upload, OpenStream100 briefly darkens the panel for the final handoff and
reveals the completed saved Mixer or Full-screen image. Both frames deliberately
share one hardware palette. Normal operation keeps the LCD active with
the native two-packet heartbeat captured from the Windows driver.
It uses the hardware-validated action-zone style 1 and resets the inherited
panel and meter surfaces, allowing the generated interface to use the entire
screen.

The background mixer is managed as a user service, so it runs with the same
PipeWire session as your desktop and never as root. Stopping it from the control
panel safely restores any applications soft-muted through the hardware. The
persistent display broker retains the clean USB session and OpenStream100 logo
across mixer restarts, and exits with the user session.
The service launches the mixer through Fedora's `/usr/bin/bash`, avoiding
systemd executable validation problems with scripts stored in a user's
application directory. The desktop launcher selects GTK's OpenGL renderer to
avoid harmless Vulkan swapchain warnings seen on some Fedora graphics setups.

## Command-line start

The original launcher remains available for testing or troubleshooting:

```bash
chmod +x run-stream100-mixer.sh
./run-stream100-mixer.sh
```

It compiles the small native display helper automatically each time, ensuring
an older experimental helper cannot be reused after an update.

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

Map the controller's native compositor surfaces:

```bash
bash run-stream100-mixer.sh --display-object-test 2>&1 | tee stream100-display-test.log
```

The v41 mapping test first shows four static channels at 50%. After about one
second it sends numbered, colored 32x32 badges through the native `0x35` image
path for object channels 0-3. It then sends native `0x41` values of 15%, 40%,
65%, and 90%. Note where badges 1-4 appear and whether any bars, faders, rings,
or numbers move without the rest of the screen redrawing. The normal mixer path
is unchanged during this diagnostic.

Hardware mapping showed all four `0x35` badges cleanly at the top of their
channels. The `0x41` values did not visibly move the direct-framebuffer bars or
percentage text. The normal v42 mixer therefore draws its labels, scale, and
panel decoration once, then displays each live percentage inside that confirmed
32x32 native object. Ordinary knob updates transmit only the changed channel's
compact `0x35` object; they do not retransmit any of the 32 interleaved
framebuffer planes. A red percentage badge means muted, and a gray `--` badge
means the assigned application is not currently available.

Map the firmware layer that currently covers the bottom of the LCD:

```bash
bash run-stream100-mixer.sh --display-fullscreen-test 2>&1 | tee stream100-display-test.log
```

The v45 diagnostic places four bright blocks labelled `FULL 1` through
`FULL 4` underneath the native action area. It keeps the inherited panel and
meter objects cleared while cycling the SDK's six supported action-zone styles
from 0 through 5. Channel 1 shows the current style number. The captured style
and native objects are restored at the end.

Recording `IMG_1259.MOV` confirmed style 1 as the clean full-screen mode: it
reveals all four footer blocks without adding a native border, and the compact
percentage object remains visible. v46 now applies that configuration during
ordinary mixer operation. The known-good v42 package remains available
separately as a rollback baseline.

The v40 test uses transfer boundaries captured directly from the official
Windows driver. Each isochronous transfer contains exactly one `SM` message: a
952-byte `HERCULES` framing packet followed by the minimum number of 952-byte
message slices. A 9-byte heartbeat therefore uses two packets, while a
4,098-byte framebuffer-plane message uses six. The helper waits briefly between
messages to reproduce the native driver's approximately 20 ms submission
cadence.

The captured controller-state sweep, compact 32-command renewal, display
configuration, and 512-byte palette are sent once at startup to activate and
configure the display. Established updates then send only one correctly framed
32-plane pixel pass and one final latch. They retain the palette already in the
controller rather than reapplying global display state whenever one percentage
changes. While the image is unchanged, a native-format two-packet heartbeat is
sent roughly 50 times per second instead of redrawing the whole framebuffer.
Knob-driven display updates wait 100 ms for a turn to settle. The test should show four complete
channels labelled Firefox, Discord Voice, Spotify, and OBS Studio for 15 seconds,
with Discord shown muted. Idle heartbeats are intentionally silent in the log.

Press `Ctrl+C` to stop. Any channel soft-muted by the mixer is restored before
the program exits.
