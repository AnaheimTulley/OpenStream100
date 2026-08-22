#!/usr/bin/python3
"""Authenticated local-network control transport for OpenStream100."""

from __future__ import annotations

from collections import deque
import ctypes
import ctypes.util
from dataclasses import dataclass
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from queue import Empty, Full, Queue
import secrets
import shutil
import socket
import subprocess
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlsplit


PROTOCOL_VERSION = 1
DEFAULT_REMOTE_PORT = 47680
REMOTE_SERVICE_TYPE = "_openstream100._tcp"
MAX_REQUEST_BYTES = 16 * 1024
PAIRING_TTL_SECONDS = 120
PAIRING_MAX_ATTEMPTS = 5
ALLOWED_COMMANDS = {
    "select_page",
    "set_volume",
    "set_mute",
    "toggle_mute",
    "press_button",
}


class RemoteProtocolError(ValueError):
    """A remote request did not match the versioned mixer protocol."""


@dataclass(frozen=True)
class RemoteCommand:
    """A validated command waiting for the mixer thread to apply it."""

    request_id: str
    name: str
    page: int | None = None
    channel: int | None = None
    value: float | bool | None = None


def load_or_create_token(path: Path) -> str:
    """Return the persistent remote token, creating it with private permissions."""
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    except OSError as error:
        raise RuntimeError(f"could not read remote token: {error}") from error
    if len(token) >= 32:
        return token

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(token + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
    except OSError as error:
        raise RuntimeError(f"could not create remote token: {error}") from error
    return token


def token_fingerprint(token: str) -> str:
    """Return a short, non-secret identifier useful during pairing."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


class PairedDeviceStore:
    """Persistent per-device credentials, stored only as token hashes."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        devices = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(devices, list):
            return
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id = item.get("id")
            name = item.get("name")
            digest = item.get("token_hash")
            paired_at = item.get("paired_at")
            if (
                isinstance(device_id, str)
                and device_id
                and isinstance(name, str)
                and name
                and isinstance(digest, str)
                and len(digest) == 64
                and isinstance(paired_at, int)
            ):
                self._devices[device_id] = {
                    "id": device_id,
                    "name": name,
                    "token_hash": digest,
                    "paired_at": paired_at,
                }

    def _save_locked(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "devices": list(self._devices.values())}
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.path.chmod(0o600)

    def authorize(self, token: str) -> bool:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            return any(
                hmac.compare_digest(digest, str(device["token_hash"]))
                for device in self._devices.values()
            )

    def pair(self, device_id: object, name: object) -> tuple[str, dict[str, Any]]:
        identifier = str(device_id).strip() if isinstance(device_id, str) else ""
        label = str(name).strip() if isinstance(name, str) else ""
        if not identifier or len(identifier) > 128:
            raise RemoteProtocolError("device_id must be a non-empty identifier")
        if not label or len(label) > 80:
            raise RemoteProtocolError("device_name must be between 1 and 80 characters")
        token = secrets.token_urlsafe(32)
        device = {
            "id": identifier,
            "name": label,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "paired_at": int(time.time()),
        }
        with self._lock:
            self._devices[identifier] = device
            self._save_locked()
        return token, {key: value for key, value in device.items() if key != "token_hash"}

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(
                (
                    {key: value for key, value in device.items() if key != "token_hash"}
                    for device in self._devices.values()
                ),
                key=lambda device: (str(device["name"]).casefold(), str(device["id"])),
            )

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            removed = self._devices.pop(device_id, None) is not None
            if removed:
                self._save_locked()
            return removed


class PinPairingManager:
    """One short-lived, rate-limited PIN window controlled by the desktop GUI."""

    def __init__(
        self,
        devices: PairedDeviceStore,
        ttl_seconds: int = PAIRING_TTL_SECONDS,
        max_attempts: int = PAIRING_MAX_ATTEMPTS,
    ) -> None:
        self.devices = devices
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._pin: str | None = None
        self._expires_at = 0.0
        self._attempts = 0
        self._device_id: str | None = None
        self._device_name: str | None = None

    def _active_locked(self) -> bool:
        if self._pin is not None and time.time() < self._expires_at:
            return True
        self._pin = None
        self._expires_at = 0.0
        self._attempts = 0
        self._device_id = None
        self._device_name = None
        return False

    @staticmethod
    def _device_identity(device_id: object, device_name: object) -> tuple[str, str]:
        identifier = str(device_id).strip() if isinstance(device_id, str) else ""
        label = str(device_name).strip() if isinstance(device_name, str) else ""
        if not identifier or len(identifier) > 128:
            raise RemoteProtocolError("device_id must be a non-empty identifier")
        if not label or len(label) > 80:
            raise RemoteProtocolError("device_name must be between 1 and 80 characters")
        return identifier, label

    def _start_locked(
        self,
        device_id: str | None = None,
        device_name: str | None = None,
    ) -> dict[str, Any]:
        self._pin = f"{secrets.randbelow(1_000_000):06d}"
        self._expires_at = time.time() + self.ttl_seconds
        self._attempts = 0
        self._device_id = device_id
        self._device_name = device_name
        return self._status_locked(include_pin=True)

    def start(self) -> dict[str, Any]:
        with self._lock:
            return self._start_locked()

    def request(self, device_id: object, device_name: object) -> dict[str, Any]:
        identifier, label = self._device_identity(device_id, device_name)
        with self._lock:
            if self._active_locked():
                if self._device_id not in {None, identifier}:
                    raise RemoteProtocolError("another phone is already being paired")
                if self._device_id is None:
                    self._device_id = identifier
                    self._device_name = label
                return self._status_locked(include_pin=False)
            self._start_locked(identifier, label)
            return self._status_locked(include_pin=False)

    def cancel(self) -> None:
        with self._lock:
            self._pin = None
            self._expires_at = 0.0
            self._attempts = 0
            self._device_id = None
            self._device_name = None

    def _status_locked(self, include_pin: bool) -> dict[str, Any]:
        active = self._active_locked()
        status: dict[str, Any] = {
            "active": active,
            "expires_in": max(0, int(self._expires_at - time.time())) if active else 0,
            "attempts_remaining": max(0, self.max_attempts - self._attempts),
        }
        if include_pin and active:
            status["pin"] = self._pin
        if active and self._device_name:
            status["device_name"] = self._device_name
        return status

    def status(self, include_pin: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._status_locked(include_pin)

    def complete(
        self,
        supplied_pin: object,
        device_id: object,
        device_name: object,
    ) -> tuple[str, dict[str, Any]]:
        pin = str(supplied_pin).strip() if isinstance(supplied_pin, str) else ""
        identifier, label = self._device_identity(device_id, device_name)
        with self._lock:
            if not self._active_locked():
                raise RemoteProtocolError(
                    "pairing is not active; choose Pair new phone on the computer"
                )
            if self._device_id is not None and identifier != self._device_id:
                raise RemoteProtocolError("this pairing PIN belongs to another phone")
            if not hmac.compare_digest(pin, str(self._pin)):
                self._attempts += 1
                if self._attempts >= self.max_attempts:
                    self._pin = None
                    self._expires_at = 0.0
                    self._device_id = None
                    self._device_name = None
                raise RemoteProtocolError("incorrect pairing PIN")
            paired_name = self._device_name or label
            self._pin = None
            self._expires_at = 0.0
            self._attempts = 0
            self._device_id = None
            self._device_name = None
        return self.devices.pair(identifier, paired_name)


def pairing_uri(server: str, token: str) -> str:
    """Build the private URI encoded by the desktop pairing QR."""
    return "openstream100://pair?" + urlencode(
        {
            "server": server,
            "token": token,
            "protocol": PROTOCOL_VERSION,
            "fingerprint": token_fingerprint(token),
        }
    )


def preferred_lan_address(port: int) -> str:
    """Return the routed LAN address used by a phone on the same network."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        host = str(probe.getsockname()[0])
    except OSError:
        host = socket.gethostname() + ".local"
    finally:
        probe.close()
    return f"{host}:{port}"


class _QRcode(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_int),
        ("width", ctypes.c_int),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def pairing_qr_svg(value: str) -> bytes:
    """Encode a pairing URI as SVG through the small system libqrencode."""
    library_name = ctypes.util.find_library("qrencode")
    if not library_name:
        raise RuntimeError("libqrencode is required for QR pairing")
    library = ctypes.CDLL(library_name)
    library.QRcode_encodeString8bit.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_int,
    ]
    library.QRcode_encodeString8bit.restype = ctypes.POINTER(_QRcode)
    library.QRcode_free.argtypes = [ctypes.POINTER(_QRcode)]
    library.QRcode_free.restype = None
    code = library.QRcode_encodeString8bit(value.encode("utf-8"), 0, 1)
    if not code:
        raise RuntimeError("QR generation failed")
    try:
        width = int(code.contents.width)
        data = code.contents.data
        quiet = 4
        view_size = width + quiet * 2
        paths: list[str] = []
        for row in range(width):
            column = 0
            while column < width:
                if not data[row * width + column] & 1:
                    column += 1
                    continue
                start = column
                while column < width and data[row * width + column] & 1:
                    column += 1
                paths.append(
                    f"M{start + quiet},{row + quiet}h{column - start}v1h-{column - start}z"
                )
        document = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view_size} {view_size}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path d="{"".join(paths)}" fill="#000"/></svg>'
        )
        return document.encode("utf-8")
    finally:
        library.QRcode_free(code)


def parse_command(payload: object) -> RemoteCommand:
    """Validate one protocol-v1 command without applying it."""
    if not isinstance(payload, dict):
        raise RemoteProtocolError("the request body must be a JSON object")
    if payload.get("protocol") != PROTOCOL_VERSION:
        raise RemoteProtocolError(f"protocol must be {PROTOCOL_VERSION}")

    name = payload.get("command")
    if name not in ALLOWED_COMMANDS:
        raise RemoteProtocolError("unsupported command")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 80:
        raise RemoteProtocolError("request_id must be a non-empty string")

    page = payload.get("page")
    if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 0):
        raise RemoteProtocolError("page must be a zero-based integer")

    channel = payload.get("channel")
    if channel is not None and (
        not isinstance(channel, int)
        or isinstance(channel, bool)
        or channel not in range(4)
    ):
        raise RemoteProtocolError("channel must be an integer from 0 to 3")

    value: float | bool | None = payload.get("value")
    if name == "select_page":
        if page is None:
            raise RemoteProtocolError("select_page requires page")
    elif name in {"set_volume", "set_mute", "toggle_mute"}:
        if channel is None:
            raise RemoteProtocolError(f"{name} requires channel")
        if name == "set_volume":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= float(value) <= 1
            ):
                raise RemoteProtocolError("set_volume value must be between 0 and 1")
            value = float(value)
        elif name == "set_mute" and not isinstance(value, bool):
            raise RemoteProtocolError("set_mute value must be true or false")
    elif name == "press_button":
        if channel is None:
            raise RemoteProtocolError("press_button requires channel")

    return RemoteCommand(
        request_id=request_id.strip(),
        name=str(name),
        page=page,
        channel=channel,
        value=value,
    )


class RemoteBridge:
    """Thread-safe boundary between HTTP workers and the real mixer loop."""

    def __init__(self, queue_size: int = 128) -> None:
        self._commands: Queue[RemoteCommand] = Queue(maxsize=queue_size)
        self._command_lock = threading.Lock()
        self._request_ids: deque[str] = deque()
        self._request_id_set: set[str] = set()
        self._snapshot_lock = threading.Lock()
        self._asset_lock = threading.Lock()
        self._assets: dict[str, tuple[bytes, str, str]] = {}
        self._snapshot: dict[str, Any] = {
            "protocol": PROTOCOL_VERSION,
            "revision": 0,
            "connected": False,
            "page": 0,
            "page_count": 0,
            "channels": [],
        }

    def enqueue(self, command: RemoteCommand) -> bool:
        """Queue a command once, returning false for a safe client retry."""
        with self._command_lock:
            if command.request_id in self._request_id_set:
                return False
            try:
                self._commands.put_nowait(command)
            except Full as error:
                raise RemoteProtocolError("the mixer command queue is full") from error
            if len(self._request_ids) >= 512:
                expired = self._request_ids.popleft()
                self._request_id_set.discard(expired)
            self._request_ids.append(command.request_id)
            self._request_id_set.add(command.request_id)
            return True

    def drain(self, limit: int = 32) -> list[RemoteCommand]:
        commands: list[RemoteCommand] = []
        for _unused in range(max(0, limit)):
            try:
                commands.append(self._commands.get_nowait())
            except Empty:
                break
        return commands

    def publish(self, snapshot: dict[str, Any]) -> None:
        with self._snapshot_lock:
            revision = int(self._snapshot.get("revision", 0)) + 1
            self._snapshot = {
                **snapshot,
                "protocol": PROTOCOL_VERSION,
                "revision": revision,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._snapshot_lock:
            # JSON round-tripping also prevents callers mutating nested state.
            return json.loads(json.dumps(self._snapshot))

    def publish_asset(self, path: str, body: bytes, content_type: str) -> str:
        """Publish a small authenticated asset and return its cache revision."""
        revision = hashlib.sha256(body).hexdigest()[:12]
        with self._asset_lock:
            self._assets[path] = (bytes(body), content_type, revision)
        return revision

    def asset(self, path: str) -> tuple[bytes, str, str] | None:
        with self._asset_lock:
            return self._assets.get(path)


class RemoteHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        bridge: RemoteBridge,
        token: str,
        pairing_server: str | None = None,
        device_store_path: Path | None = None,
    ) -> None:
        self.bridge = bridge
        self.token = token
        self.devices = PairedDeviceStore(device_store_path)
        self.pin_pairing = PinPairingManager(self.devices)
        super().__init__(address, RemoteRequestHandler)
        port = int(self.server_address[1])
        self.pairing_server = pairing_server or preferred_lan_address(port)
        self.pairing_uri = pairing_uri(self.pairing_server, token)
        try:
            self.pairing_svg = pairing_qr_svg(self.pairing_uri)
        except RuntimeError:
            self.pairing_svg = None


class RemoteRequestHandler(BaseHTTPRequestHandler):
    """Small JSON API deliberately limited to the mixer protocol."""

    server: RemoteHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *arguments: object) -> None:
        return

    def _reply(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _reply_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        cache_control: str = "no-store",
        etag: str | None = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        if etag is not None:
            self.send_header("ETag", f'"{etag}"')
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not supplied.startswith(prefix):
            return False
        token = supplied[len(prefix):]
        return hmac.compare_digest(token, self.server.token) or self.server.devices.authorize(
            token
        )

    def _require_authorization(self) -> bool:
        if self._authorized():
            return True
        self._reply(
            HTTPStatus.UNAUTHORIZED,
            {"protocol": PROTOCOL_VERSION, "error": "authorization required"},
        )
        return False

    def _is_loopback(self) -> bool:
        return self.client_address[0] in {"127.0.0.1", "::1"}

    def _require_loopback(self) -> bool:
        if self._is_loopback():
            return True
        self._reply(HTTPStatus.FORBIDDEN, {"error": "desktop administration is local only"})
        return False

    def _read_json(self) -> object:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise RemoteProtocolError("invalid request size") from error
        if not 0 < content_length <= MAX_REQUEST_BYTES:
            raise RemoteProtocolError("invalid request size")
        try:
            return json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RemoteProtocolError("invalid JSON") from error

    def _admin_status(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "server": self.server.pairing_server,
            "pairing": self.server.pin_pairing.status(include_pin=True),
            "devices": self.server.devices.devices(),
            "qr_available": self.server.pairing_svg is not None,
        }

    def _pairing_page(self) -> None:
        if not self._is_loopback():
            self._reply(HTTPStatus.FORBIDDEN, {"error": "pairing page is local only"})
            return
        qr_available = self.server.pairing_svg is not None
        qr_markup = (
            '<img src="/api/v1/pairing.svg" alt="OpenStream100 pairing QR">'
            if qr_available
            else "<p><strong>libqrencode is required to display the pairing QR.</strong></p>"
        )
        document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>OpenStream100 pairing</title>
<style>body{{background:#090e14;color:#edf4f9;font:18px sans-serif;text-align:center;
padding:2rem}}main{{max-width:560px;margin:auto;background:#182332;padding:2rem;
border-radius:24px}}img{{width:min(72vw,420px);background:white;padding:16px;
border-radius:16px}}code{{color:#30ccbe;overflow-wrap:anywhere}}</style></head>
<body><main><h1>OpenStream100 Remote</h1><p>Scan this code in the Android app.</p>
{qr_markup}<p>Server: <code>{self.server.pairing_server}</code></p>
<p>The QR contains the private pairing token. Keep this page on your computer.</p>
</main></body></html>""".encode("utf-8")
        self._reply_bytes(HTTPStatus.OK, document, "text/html; charset=utf-8")

    def _pairing_qr(self) -> None:
        if not self._is_loopback():
            self._reply(HTTPStatus.FORBIDDEN, {"error": "pairing QR is local only"})
            return
        if self.server.pairing_svg is None:
            self._reply(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "libqrencode is required for QR pairing"},
            )
            return
        self._reply_bytes(HTTPStatus.OK, self.server.pairing_svg, "image/svg+xml")

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/v1/health":
            self._reply(
                HTTPStatus.OK,
                {
                    "protocol": PROTOCOL_VERSION,
                    "service": "OpenStream100",
                    "status": "ready",
                    "token_fingerprint": token_fingerprint(self.server.token),
                    "pin_pairing": self.server.pin_pairing.status()["active"],
                },
            )
            return
        if path == "/api/v1/admin/remote":
            if self._require_loopback():
                self._reply(HTTPStatus.OK, self._admin_status())
            return
        if path == "/api/v1/pair":
            self._pairing_page()
            return
        if path == "/api/v1/pairing.svg":
            self._pairing_qr()
            return
        asset = self.server.bridge.asset(path)
        if asset is not None:
            if not self._require_authorization():
                return
            body, content_type, revision = asset
            self._reply_bytes(
                HTTPStatus.OK,
                body,
                content_type,
                cache_control="private, max-age=3600",
                etag=revision,
            )
            return
        if path != "/api/v1/state":
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._require_authorization():
            return
        self._reply(HTTPStatus.OK, self.server.bridge.snapshot())

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/v1/admin/pairing/start":
            if not self._require_loopback():
                return
            self._reply(
                HTTPStatus.OK,
                {"protocol": PROTOCOL_VERSION, "pairing": self.server.pin_pairing.start()},
            )
            return
        if path == "/api/v1/admin/pairing/cancel":
            if not self._require_loopback():
                return
            self.server.pin_pairing.cancel()
            self._reply(HTTPStatus.OK, {"protocol": PROTOCOL_VERSION, "cancelled": True})
            return
        if path == "/api/v1/pair/request":
            try:
                payload = self._read_json()
                if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_VERSION:
                    raise RemoteProtocolError(f"protocol must be {PROTOCOL_VERSION}")
                pairing = self.server.pin_pairing.request(
                    payload.get("device_id"), payload.get("device_name")
                )
            except RemoteProtocolError as error:
                self._reply(HTTPStatus.CONFLICT, {"error": str(error)})
                return
            self._reply(
                HTTPStatus.ACCEPTED,
                {"protocol": PROTOCOL_VERSION, "pairing": pairing},
            )
            return
        if path == "/api/v1/pair/complete":
            try:
                payload = self._read_json()
                if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_VERSION:
                    raise RemoteProtocolError(f"protocol must be {PROTOCOL_VERSION}")
                token, device = self.server.pin_pairing.complete(
                    payload.get("pin"), payload.get("device_id"), payload.get("device_name")
                )
            except RemoteProtocolError as error:
                self._reply(HTTPStatus.FORBIDDEN, {"error": str(error)})
                return
            except OSError as error:
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
                return
            self._reply(
                HTTPStatus.CREATED,
                {
                    "protocol": PROTOCOL_VERSION,
                    "server": self.server.pairing_server,
                    "token": token,
                    "device": device,
                    "token_fingerprint": token_fingerprint(self.server.token),
                },
            )
            return
        if path != "/api/v1/command":
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._require_authorization():
            return
        try:
            payload = self._read_json()
            command = parse_command(payload)
            queued = self.server.bridge.enqueue(command)
        except RemoteProtocolError as error:
            status = (
                HTTPStatus.SERVICE_UNAVAILABLE
                if "queue is full" in str(error)
                else HTTPStatus.BAD_REQUEST
            )
            self._reply(status, {"error": str(error)})
            return
        self._reply(
            HTTPStatus.ACCEPTED,
            {
                "protocol": PROTOCOL_VERSION,
                "accepted": True,
                "duplicate": not queued,
                "request_id": command.request_id,
            },
        )

    def do_DELETE(self) -> None:
        path = urlsplit(self.path).path
        prefix = "/api/v1/admin/devices/"
        if not path.startswith(prefix):
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._require_loopback():
            return
        device_id = path[len(prefix):]
        if not device_id:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "device id is required"})
            return
        removed = self.server.devices.revoke(device_id)
        self._reply(
            HTTPStatus.OK if removed else HTTPStatus.NOT_FOUND,
            {"protocol": PROTOCOL_VERSION, "revoked": removed},
        )


class RemoteServer:
    """Lifecycle wrapper used by the mixer service and tests."""

    def __init__(
        self,
        bridge: RemoteBridge,
        token: str,
        host: str = "0.0.0.0",
        port: int = DEFAULT_REMOTE_PORT,
        advertise: bool = False,
        device_store_path: Path | None = None,
    ) -> None:
        try:
            self._server = RemoteHTTPServer(
                (host, port), bridge, token, device_store_path=device_store_path
            )
        except OSError as error:
            raise RuntimeError(f"could not start remote mixer server: {error}") from error
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="openstream100-remote",
            daemon=True,
        )
        self._advertise = advertise
        self._advertiser: subprocess.Popen[bytes] | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self._thread.start()
        executable = shutil.which("avahi-publish-service")
        if self._advertise and executable is not None:
            host_name = socket.gethostname().split(".")[0]
            self._advertiser = subprocess.Popen(
                [
                    executable,
                    "--no-fail",
                    f"OpenStream100 on {host_name}",
                    REMOTE_SERVICE_TYPE,
                    str(self.address[1]),
                    f"protocol={PROTOCOL_VERSION}",
                    f"fingerprint={token_fingerprint(self._server.token)}",
                    f"server={self._server.pairing_server}",
                    "pairing=pin,qr",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def close(self) -> None:
        if self._advertiser is not None:
            self._advertiser.terminate()
            try:
                self._advertiser.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._advertiser.kill()
                self._advertiser.wait(timeout=2)
            self._advertiser = None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
