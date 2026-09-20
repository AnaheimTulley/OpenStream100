# macOS audio routing architecture

## Decision

OpenStream100 uses Core Audio process taps for per-application mixing on macOS
14.2 and later.

Apple's AudioDriverKit documentation says that virtual audio devices should use
an Audio Server Driver Plug-in rather than AudioDriverKit. A HAL plug-in is a
large, privileged installation surface for a feature that current macOS releases
can provide with process taps, so it is not the first implementation.

References:

- [Capturing system audio with Core Audio taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps)
- [Creating an audio device driver](https://developer.apple.com/documentation/audiodriverkit/creating-an-audio-device-driver)
- [Building an Audio Server Plug-in and Driver Extension](https://developer.apple.com/documentation/coreaudio/building-an-audio-server-plug-in-and-driver-extension)

## Data path

```text
Application output
        │
        ▼
Private CATapDescription (.mutedWhenTapped)
        │
        ▼
Private aggregate device input
        │
        ▼
Real-time gain stage ───── Stream 100 encoder / UI slider
        │
        ▼
Current default Core Audio output
```

`ProcessTapRouter` owns one route per assigned application. A route contains a
process tap, a private aggregate device, and an `AudioDeviceIOProcID`. Cleanup is
performed in reverse order so the original application output becomes audible
again even if route creation fails part-way through.

Assignments use bundle identifiers rather than process IDs. The app periodically
re-resolves the active Core Audio process objects, which lets a saved assignment
recover after its application restarts. Routes are also rebuilt after the default
output device changes.

## Privacy

The app bundle includes `NSAudioCaptureUsageDescription`, so macOS controls
access through its System Audio Recording permission. Samples are scaled inside
the real-time callback and immediately sent to the selected output. They are not
written to disk or sent over the network.

## Validation still required

- Measure round-trip latency on built-in, USB, Bluetooth, and AirPlay outputs.
- Verify planar and interleaved channel layouts beyond stereo.
- Exercise device switching and application restart recovery under load.
- Confirm behavior for applications that use helper processes for audio.
- Add peak metering without allocating or locking in the real-time callback.

If process taps prove unsuitable for a supported output class, the fallback is
an Audio Server Driver Plug-in packaged with a signed installer. AudioDriverKit
is not the fallback for the virtual device because Apple documents it for
physical audio hardware.
