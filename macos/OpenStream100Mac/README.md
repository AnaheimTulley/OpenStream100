# OpenStream100 for macOS

This directory contains the native macOS port. The first milestone provides:

- a SwiftUI four-channel mixer;
- discovery of Core Audio input and output devices;
- volume and mute control for devices that expose software controls;
- per-application volume routing through private Core Audio process taps;
- Hercules Stream 100 connection monitoring and input-report decoding;
- encoder volume changes and knob-press mute actions; and
- persistent JSON settings in `~/Library/Application Support/OpenStream100/`.

## Audio routing approach

On macOS 14.2 and later, OpenStream100 uses Apple's Core Audio process-tap API
instead of installing a kernel or DriverKit extension. Each assigned application
gets a private tap. Its original output is muted only while OpenStream100 reads
the tap, applies the selected gain, and writes the result to the current system
output. The app automatically rebuilds a route when the source process restarts
or the default output device changes.

The first routed application triggers macOS's System Audio Recording permission
prompt. OpenStream100 processes the samples in memory and does not record them to
disk. See [Audio routing architecture](docs/AUDIO-ROUTING.md) for the design and
fallback plan.

The process-tap backend is experimental and still needs latency and multi-device
testing with real workloads. The controller LCD is not yet driven by the native app. The Linux implementation
uses a libusb isochronous display protocol that still needs to be moved behind a
macOS USB transport.

## Build and test

Xcode 16 or a compatible Swift 6 toolchain is required.

```bash
cd macos/OpenStream100Mac
swift test
swift run OpenStream100Mac
```

To create an unsigned `.app` bundle:

```bash
./scripts/build-app.sh
open .build/app/OpenStream100.app
```

The generated app is intended for local development. Distribution requires an
Apple Developer identity, hardened-runtime signing, and notarization.
