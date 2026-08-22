# OpenStream100 Remote Mixer Protocol v1

The remote mixer API is an opt-in local-network interface intended for the
OpenStream100 Android application. The Linux mixer remains authoritative for
PipeWire, hardware display, page, volume, and soft-mute state.

## Enable the remote service

Use **Android remote control → Enable local-network remote** in the OpenStream100
control panel. The saved setting follows the normal background mixer service.
For development, it can still be overridden from the command line:

```text
./run-stream100-mixer.sh --remote
```

The default address is TCP port `47680` on the machine's local interfaces. A
QR fallback token is created at `~/.config/hercules-stream100/remote-token`
with user-only file permissions. PIN-paired phones each receive a separate token;
only its SHA-256 hash is saved in `remote-devices.json`. Use this API only on a
trusted local network; version 1 uses token-authenticated HTTP and does not yet
encrypt LAN traffic.

While running, OpenStream100 advertises `_openstream100._tcp` with mDNS so the
Android app can find it automatically. Tapping an unpaired discovered mixer
sends a non-secret pairing request to the host; the desktop GUI automatically
displays a six-digit PIN for that phone. Enter the PIN in Android and the host
issues that phone its individual credential. The rate-limited PIN is
single-use, expires after two minutes, and closes after five failed attempts.
**Pair new phone** can still open the same PIN window from the desktop first.

Tapping a mixer whose fingerprint matches an existing saved Android credential
tests and reuses that credential. It does not open a new PIN window.

As a fallback, **Show QR fallback** opens
`http://127.0.0.1:47680/api/v1/pair`. The QR page and all device-administration
endpoints are restricted to loopback clients. No PIN or private token is ever
included in mDNS metadata.

The two unauthenticated, rate-limited pairing endpoints are:

```text
POST /api/v1/pair/request
```

This request contains only protocol version `1`, `device_id`, and
`device_name`; its response never includes the PIN. The PIN is visible only in
the local desktop GUI.

```text
POST /api/v1/pair/complete
```

The body contains protocol version `1`, the six-digit `pin`, `device_id`, and
`device_name`. A successful response returns that device's bearer token. The
desktop GUI can list and revoke each issued device independently.

The unauthenticated health response exposes only service identity and the
non-secret token fingerprint:

```text
GET /api/v1/health
```

Mixer state, icons, and commands require:

```text
Authorization: Bearer <remote-token>
```

## Read synchronized mixer state

```text
GET /api/v1/state
```

The response contains `protocol`, a monotonically increasing `revision`, the
active zero-based `page`, page metadata, four `channels`, and four programmable
`actions`. Channel levels and stereo meters use floating-point values from
`0.0` to `1.0`.
Each channel may also include an authenticated, revisioned `icon` path. Clients
should cache the returned 64-pixel PNG by its complete URL.

## Submit a command

```text
POST /api/v1/command
Content-Type: application/json
```

Every command contains protocol version `1`, a client-generated `request_id`,
and a `command`. The server returns HTTP `202` after validation and queues the
command onto the authoritative mixer thread. The resulting state includes a
`last_command` object with the matching request ID, success flag, and message.
Recent request IDs are deduplicated, so retrying a timed-out request cannot
double-activate a programmable button.

Set Control 1 to 65%:

```json
{
  "protocol": 1,
  "request_id": "phone-1042",
  "command": "set_volume",
  "channel": 0,
  "value": 0.65
}
```

Other commands are:

- `toggle_mute`, with `channel`
- `set_mute`, with `channel` and boolean `value`
- `select_page`, with zero-based `page`
- `press_button`, with zero-based `channel` identifying buttons 1 through 4

Volume, mute, and button commands may include `page`. If it differs from the
active page, the mixer switches pages before applying the command so every
connected surface observes the same state.
