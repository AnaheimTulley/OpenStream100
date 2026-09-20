# OpenStream100 Remote for iOS

Native SwiftUI port of the OpenStream100 Android remote. It implements remote
protocol v1 with Bonjour discovery, PIN and QR pairing, Keychain credential
storage, live four-channel meters, touch faders, mute, page navigation,
programmable buttons, and authenticated application icons.

## Install from the repository with Xtool

The app can be built, signed with your own Apple Account, and installed directly
from Linux, Windows, or macOS without an App Store release. This is personal
development signing, not permanent public distribution.

You need:

- iOS 17 or newer
- A USB-connected iPhone or iPad with Developer Mode enabled
- A free Apple Account
- [Xtool](https://xtool.sh) with its Darwin SDK installed (`xtool setup`)
- An OpenStream100 Linux computer on the same trusted LAN

On Linux or macOS, clone the repository and run:

```bash
cd ios/OpenStream100Remote
./install-ios.sh
```

On Windows, open a terminal in `ios/OpenStream100Remote` and run:

```powershell
xtool dev run --usb
```

After installation, open **OpenStream100 Remote** manually. If iOS reports an
untrusted developer, go to **Settings > General > VPN & Device Management** and
trust the Apple Account used by Xtool.

Apple's free personal provisioning expires after seven days. Rerun the same
command to rebuild and reinstall the app. Apple currently limits free accounts
to 10 temporary App IDs, three devices, and three installed development apps per
device. A prebuilt IPA cannot avoid these signing requirements; every user must
sign for their own device.

## Build with Xcode

Requirements:

- Xcode 16 or newer
- iOS 17 or newer
- An OpenStream100 Linux computer with local-network remote control enabled
- The iPhone/iPad and computer on the same trusted LAN

Open `OpenStream100Remote.xcodeproj`, select your development team, connect an
iPhone or iPad, and run the `OpenStream100Remote` scheme. The interface is
landscape-only to match the four-channel Android layout.

For a command-line compile check that does not require signing:

```bash
xcodebuild \
  -project OpenStream100Remote.xcodeproj \
  -scheme OpenStream100Remote \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

Protocol v1 uses authenticated cleartext HTTP and is intended only for a
trusted local network. Do not expose TCP port 47680 to the internet.
