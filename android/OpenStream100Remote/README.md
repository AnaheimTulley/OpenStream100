# OpenStream100 Remote for Android

This is the first Android client for the OpenStream100 remote mixer protocol.
It provides a landscape four-channel mixer with live stereo meters, responsive
touch faders, mute controls, synchronized page navigation, programmable buttons,
application icons, connection recovery, mDNS discovery, QR pairing, and saved
pairing details. Its adaptive launcher icon reuses the Linux OpenStream100 mixer
artwork.

## Development setup

Open this directory in Android Studio with Android SDK 37 installed. The project
uses Android Gradle Plugin 9.3.0, Java 17, Kotlin/Compose compiler 2.3.21, and the
stable Compose BOM 2026.08.00. A Gradle 9.5.0 wrapper is included.

Enable the Android remote from the OpenStream100 Linux control panel. On the
phone, choose the automatically discovered computer. A six-digit PIN appears in
the desktop GUI for first-time pairing; enter it on the phone. Previously paired
phones reconnect automatically, even when the computer's LAN address changes.
QR pairing remains available as a fallback.

Both devices must be on the same trusted local network, and TCP port `47680`
must be allowed through the Linux firewall. Protocol v1 uses authenticated
cleartext HTTP; encrypted transport is subsequent work.
