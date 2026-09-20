# OpenStream100 Remote for iOS

Native SwiftUI port of the OpenStream100 Android remote. It implements remote
protocol v1 with Bonjour discovery, PIN and QR pairing, Keychain credential
storage, live four-channel meters, touch faders, mute, page navigation,
programmable buttons, and authenticated application icons.

## Requirements

- Xcode 16 or newer
- iOS 17 or newer
- An OpenStream100 Linux computer with local-network remote control enabled
- The iPhone/iPad and computer on the same trusted LAN

## Build

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
