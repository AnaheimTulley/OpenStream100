# OpenStream100 Remote for Android

OpenStream100 Remote is the native Android companion for
[OpenStream100](../../README.md). It mirrors the Linux mixer's active four-channel
page on a phone or tablet, so the mixer remains controllable away from the
physical Hercules Stream 100.

The app speaks version 1 of the OpenStream100 remote protocol and is intended to
be used with an OpenStream100 Linux host on the same local network. It is an
unofficial, community-developed app and is not affiliated with Hercules.

## Features

- Four touch-controlled channel faders in a landscape layout
- Live stereo activity meters and current volume values
- Per-channel mute and unmute controls
- Mixer page navigation synchronized with the Linux host
- The four programmable OpenStream100 actions
- Channel colours, labels, availability, and application icons from the host
- Automatic mixer discovery over mDNS/DNS-SD
- Six-digit PIN pairing, QR-code pairing, and manual credentials
- Saved pairing and automatic recovery when a paired host's LAN address changes
- Visible connection and reconnection state

The physical Stream 100 is not required to use the remote, provided the Linux
mixer and its remote server are running.

## Requirements

### To use the app

- Android 8.0 (API 26) or later
- An OpenStream100 Linux installation with **Android remote control** enabled
- The Android device and Linux host on the same trusted local network
- TCP port `47680` reachable from the Android device
- Google Play services if you want to use the built-in QR scanner

The interface is landscape-only. Android 17/API 37 and later also ask for local
network access; discovery and mixer connections will not work if that permission
is denied.

### To build the app

- JDK 17
- Android SDK 37
- Internet access for the first Gradle dependency download

The repository includes the Gradle wrapper, so a separate Gradle installation is
not needed. The current project uses Android Gradle Plugin 9.3.0, Gradle 9.5.0,
Kotlin/Compose compiler 2.3.21, and Compose BOM 2026.08.00.

## Install a release APK

Download `openstream100remote.apk` from the
[latest OpenStream100 release](https://github.com/AnaheimTulley/OpenStream100/releases/latest)
on the Android device. Open the download and allow installation from that browser
or file manager when Android prompts. The app is currently distributed through
GitHub rather than an app store.

Installing a newer APK signed by the same publisher over the existing app retains
the saved pairing. Clearing the app's storage removes its local credential; the
corresponding paired-device entry can then be revoked separately on the Linux
host.

## Pair with OpenStream100

1. On the Linux host, open **OpenStream100**, enable **Android remote control**,
   and start the mixer.
2. Open **OpenStream100 Remote** on the Android device and grant local-network
   access if prompted.
3. Select the discovered OpenStream100 host. The phone asks the host to display
   a temporary six-digit PIN.
4. Enter that PIN on the phone and select **Pair phone**.

The app stores the issued device token, host address, and host fingerprint in its
private app preferences. On later launches it reconnects with that credential.
If the host receives a different LAN address, discovery uses its fingerprint to
update the saved address automatically.

Paired devices can be revoked individually from the Linux control panel. If a
credential has been revoked, open **Pairing**, choose **Forget saved pairing**,
and pair again with a new PIN.

### Alternative pairing methods

- **QR code:** Select **Scan QR fallback** and scan the code shown by the Linux
  control panel. QR scanning is provided by Google Play services and may require
  its scanner module to download the first time it is used.
- **Manual:** Enter the host as `192.168.1.20:47680` (or a complete `http://` or
  `https://` URL) and paste a valid pairing token, then select **Open mixer**.
  Manual entry is mainly useful when mDNS or QR scanning is unavailable. Treat
  the token like a password.

## Using the mixer

- Drag vertically anywhere on a channel's meter/fader to set its level.
- Select **MUTE** or **UNMUTE** at the bottom of a channel strip.
- Use the arrow buttons in the header to move between configured mixer pages.
- Use the four buttons along the bottom to invoke the actions configured for the
  current page on the Linux host. Disabled actions cannot be selected.
- Select **Pairing** to inspect or replace the saved connection.

Channels without an active or assigned audio stream show **Waiting for audio**
and cannot be adjusted. Page changes and mixer state are shared with the host and
other connected controls.

## Build from source

Open this `android/OpenStream100Remote` directory in Android Studio, allow Gradle
sync to finish, select the `app` configuration, and run it on an Android 8.0+
device or emulator.

For a command-line debug build and lint check:

```bash
cd android/OpenStream100Remote
./gradlew lintDebug assembleDebug
```

On Windows, use `gradlew.bat` instead of `./gradlew`. The debug APK is written to:

```text
app/build/outputs/apk/debug/app-debug.apk
```

Install it on a connected device with Android Debug Bridge:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

A physical device on the same LAN as the Linux host gives the most representative
test environment. An emulator must be able to route to the host, and multicast
discovery may not traverse its virtual network; manual pairing can still work.

Release APKs require an external signing configuration. No signing keys or
credentials are stored in this repository.

## Project layout

```text
app/src/main/
├── AndroidManifest.xml
├── java/org/openstream100/remote/
│   ├── MainActivity.kt         # Compose pairing and mixer interface
│   ├── OpenStreamDiscovery.kt # mDNS/DNS-SD host discovery
│   └── RemoteApi.kt           # Protocol v1 HTTP client and pairing parser
└── res/                       # Theme and adaptive launcher icon resources
```

The app is a single-activity Jetpack Compose project. It discovers
`_openstream100._tcp` services, authenticates requests with the paired bearer
token, polls `/api/v1/state`, sends changes to `/api/v1/command`, and fetches
authenticated channel icons from the Linux host.

See the [remote protocol specification](../../work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.17.0/REMOTE-PROTOCOL.md)
for the request, response, discovery, and pairing formats.

## Network and security

Protocol v1 uses bearer-token authentication over cleartext HTTP by default.
Use it only on a trusted private LAN:

- Do not expose or forward TCP port `47680` to the internet.
- Do not post pairing QR codes, tokens, or paired-device configuration in bug
  reports.
- Prefer a private Wi-Fi network without client isolation.
- Revoke a phone from the Linux control panel if it is lost or no longer used.

Android cleartext traffic is deliberately enabled because the current Linux
remote server does not provide TLS. Transport encryption is not yet implemented.

## Troubleshooting

### The Linux host is not discovered

- Confirm **Android remote control** is enabled and the mixer is running.
- Confirm both devices are on the same LAN and not separated by guest-network or
  wireless client isolation.
- Grant the app local-network access when Android requests it.
- Temporarily disable a VPN on the phone and retry.
- Check that multicast DNS is allowed on the network.
- Use QR or manual pairing if discovery remains unavailable.

### Pairing or connection fails

- Allow inbound TCP port `47680` in the Linux firewall.
- Enter the PIN before it expires and request a new one if necessary.
- For a manual address, include port `47680`; a scheme is optional.
- Check whether the phone was revoked in the Linux control panel. Forget the
  saved pairing and pair again if its credential is no longer valid.
- Make sure the app and Linux host both support remote protocol v1.

### The QR scanner does not open

Update or enable Google Play services and try again. Devices without Google Play
services can use discovered-host PIN pairing or enter the address and token
manually; camera permission is not required directly by this app.

### The mixer connects but a channel cannot be controlled

The channel must be assigned and its audio stream must be available on the Linux
host. Start playback in the application, refresh applications in OpenStream100,
assign the channel, and apply the changes.

## Version information

- Application ID: `org.openstream100.remote`
- App version: `0.3.1` (`versionCode` 5)
- Minimum Android version: API 26
- Target and compile SDK: API 37
- Remote protocol: version 1

OpenStream100 is released under the [MIT License](../../work/package-v102-base/hercules-stream100-rpm-build-kit/hercules-stream100-0.17.0/LICENSE).
