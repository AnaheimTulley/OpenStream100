#!/usr/bin/python3
"""OpenStream100: a four-channel PipeWire mixer for the Hercules hardware."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from queue import Empty, Full, Queue
from typing import Any

try:
    import usb.core
    import usb.util
except ImportError:
    print(
        "PyUSB is not installed. On Fedora, run:\n"
        "  sudo dnf install python3-pyusb",
        file=sys.stderr,
    )
    raise SystemExit(2)


VID = 0x06F8
PID = 0xE053
INTERFACE = 0
INPUT_ENDPOINT = 0x81
PACKET_SIZE = 64
DEFAULT_CONFIG = Path.home() / ".config" / "hercules-stream100" / "config.json"
DEFAULT_TARGET = "@DEFAULT_AUDIO_SINK@"
DEFAULT_KNOB_SENSITIVITY = 1.0
MIN_KNOB_SENSITIVITY = 0.5
MAX_KNOB_SENSITIVITY = 4.0
DEFAULT_DISPLAY_BRIGHTNESS = 100
MIN_DISPLAY_BRIGHTNESS = 10
MAX_DISPLAY_BRIGHTNESS = 100
DISPLAY_BRIGHTNESS_STEP = 5
BRIGHTNESS_REFRESH_SECONDS = 0.15
BASE_COUNTS_PER_PERCENT = 12.0
MAX_MIXER_PAGES = 8
VOLUME_METER_MODES = ("activity", "volume")
DEFAULT_VOLUME_METER_MODE = "activity"
METER_CHANNEL_MODES = ("stereo", "mono")
DEFAULT_METER_CHANNEL_MODE = "stereo"
METER_STYLES = ("classic", "segmented", "rounded", "slim")
DEFAULT_METER_STYLE = "classic"
CUSTOM_METER_STEPS = 15
CUSTOM_METER_PALETTE_BASE = 120
CUSTOM_METER_PALETTE_COLORS = 4 * 2 * CUSTOM_METER_STEPS
CUSTOM_METER_DYNAMIC_COLORS = CUSTOM_METER_PALETTE_BASE - 16
METER_UPDATE_SECONDS = 0.10
METER_CAPTURE_RATE = 8000
METER_CAPTURE_WINDOW_SECONDS = 0.04
MeterTarget = tuple[str, str, str, str]
StereoLevel = tuple[float, float]
APP_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_DISPLAY_HELPER = APP_DIRECTORY / "stream100-display-helper"
DEFAULT_DISPLAY_REPLAY = APP_DIRECTORY / "stream100-display-replay.bin"
DEFAULT_STARTUP_LOGO = APP_DIRECTORY / "openstream100-startup.png"
DISPLAY_SETTLE_SECONDS = 0.10

DEFAULT_BUTTON_MASKS = {
    1: 0x08,
    2: 0x01,
    3: 0x04,
    4: 0x02,
}
PROGRAMMABLE_BUTTON_MASKS = {
    # The hardware's input-bit order does not follow either the printed
    # button numbers or the native LED-object order.
    1: 0x10,
    2: 0x20,
    3: 0x40,
    4: 0x80,
}
BUTTON_ACTION_IDS = {
    "disabled",
    "microphone_mute",
    "speaker_mute",
    "play_pause",
    "previous_track",
    "next_track",
    "set_channel_volume",
    "next_page",
    "previous_page",
}

# Human-readable labels for button actions
BUTTON_ACTION_LABELS = {
    "disabled": "Disabled",
    "microphone_mute": "Mic Mute",
    "speaker_mute": "Speaker Mute",
    "play_pause": "Play/Pause",
    "previous_track": "Prev Track",
    "next_track": "Next Track",
    "set_channel_volume": "Volume",
    "next_page": "Next Page",
    "previous_page": "Prev Page",
}
DEFAULT_BUTTON_ACTIONS = ["disabled", "disabled", "disabled", "disabled"]
DEFAULT_BUTTON_VOLUME_PRESETS = [
    {"channel": 1, "percentage": 50},
    {"channel": 2, "percentage": 50},
    {"channel": 3, "percentage": 50},
    {"channel": 4, "percentage": 50},
]
BUTTON_OVERLAY_STYLES = ("boxes", "basic", "glass", "custom")
CUSTOM_BUTTON_OVERLAY_PATH = DEFAULT_CONFIG.with_name("button-overlay-custom.png")


def _load_show_channel_icons() -> bool:
    """Read the show_channel_icons setting from the user config."""
    try:
        payload = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        value = payload.get("show_channel_icons", True)
        return value if isinstance(value, bool) else True
    except (FileNotFoundError, json.JSONDecodeError):
        return True


def _load_button_overlay_style() -> str:
    """Read the button_overlay_style setting from the user config.

    Defaults to 'boxes' for backwards compatibility.
    """
    try:
        payload = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        value = payload.get("button_overlay_style", "boxes")
        if value in BUTTON_OVERLAY_STYLES:
            return value
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return "boxes"


_button_overlay_style_cache: tuple[str, str, int, int] | None = None


def _resolve_channel_streams(channels: list[dict], mixer_state: dict):
    """Map each channel dict to its matching PipeWire stream dicts."""
    all_streams = mixer_state.get("streams") or []
    result: list[list[dict]] = [[] for _ in channels]
    for i, ch in enumerate(channels):
        matches: list[dict] = []
        for s in all_streams:
            sv = str(s.get("value", ""))
            sp = str(s.get("property", ""))
            cv = str(ch.get("value", ""))
            cp = str(ch.get("property", ""))
            if sv == cv and sp == cp:
                matches.append(s)
            elif ch.get("kind") in ("application",) and sv == cv:
                props = s.get("props", {})
                app_id = str(props.get("application.id", "")).lower()
                app_name = str(props.get("application.name", "")).lower()
                label = ch.get("label", "").lower()
                if app_id and (app_id in label or label in app_id):
                    matches.append(s)
                elif app_name and (app_name in label or label in app_name):
                    matches.append(s)
        result[i] = matches
    return result


# ---------------------------------------------------------------------------
# Icon cache — retains one image and its full identity for each on-screen column.
# Keys are (channel_index, cache_slot) tuples; values are signatures or images.
# ---------------------------------------------------------------------------

_icon_cache: dict[tuple[int, int], Any] = {}
_icon_cache_version: int = 0


def _icon_cache_key(channel_idx: int, stream_id: int) -> tuple[int, int]:
    """Generate a cache key for icon resolution."""
    return (channel_idx, stream_id)


def _clear_icon_cache() -> None:
    """Clear the icon resolution cache — call when streams change."""
    global _icon_cache_version
    _icon_cache.clear()
    _icon_cache_version += 1


def _channel_icon_signature(ch: dict, streams: list[dict], icon_size: int) -> tuple:
    """Identify the channel assignment and applications an icon represents."""
    stream_signature = tuple(
        (
            stream.get("id"),
            stream.get("props", {}).get("application.icon-name"),
            stream.get("props", {}).get("application.id"),
            stream.get("props", {}).get("application.process.binary"),
        )
        for stream in streams
    )
    return (
        ch.get("kind"),
        ch.get("label"),
        ch.get("application_id"),
        ch.get("property"),
        ch.get("value"),
        stream_signature,
        icon_size,
        _icon_cache_version,
    )


def _cached_channel_icon(
    channel_idx: int,
    ch: dict,
    streams: list[dict],
    icon_size: int,
) -> tuple[Any, bool]:
    """Return only an icon whose cached identity matches the current page."""
    from stream100_channel_icons import (
        load_channel_fallback_icon,
        load_channel_icon,
    )

    signature_key = _icon_cache_key(channel_idx, -2)
    image_key = _icon_cache_key(channel_idx, -1)
    signature = _channel_icon_signature(ch, streams, icon_size)
    if _icon_cache.get(signature_key) == signature and image_key in _icon_cache:
        return _icon_cache[image_key], False

    icon_img = load_channel_icon(ch, streams, icon_size=icon_size)
    if icon_img is None:
        icon_img = load_channel_fallback_icon(ch, icon_size=icon_size)
    _icon_cache[signature_key] = signature
    _icon_cache[image_key] = icon_img
    return icon_img, True


def _resolve_channel_icons_for_streams(
    channels: list[dict],
    streams_by_ch: list[list[dict]],
    icon_size: int = 24,
) -> tuple[dict[int, Any], set[int]]:
    """Resolve icons for each channel's streams and update the icon cache.

    Returns a mapping of *channel_index → icon image* (or ``None`` when no
    icon is available) along with the set of channels whose icons changed
    since the previous call. Resolved icons are cached by channel assignment
    and stream identity so repeated renders do not re-resolve unchanged icons.

    :func:`_on_icon_cache_changed` is called with the set of channels whose
    icons changed since the previous call, enabling dynamic notification to
    the display socket when applications open or close.
    """
    icon_map: dict[int, Any] = {}
    changed_channels: set[int] = set()

    for i, ch in enumerate(channels):
        streams = streams_by_ch[i] if i < len(streams_by_ch) else []
        icon_img, icon_changed = _cached_channel_icon(i, ch, streams, icon_size)
        if icon_changed:
            changed_channels.add(i)

        icon_map[i] = icon_img

    return icon_map, changed_channels


def _on_icon_cache_changed(changed_channels: set[int]) -> None:
    """Callback invoked when icons change — sends notifications to display socket."""
    if changed_channels:
        channels_list = sorted(changed_channels)
        print(f"[icon_update] Icons changed for channels: {channels_list}", file=sys.stderr)


def _draw_channel_icons_on_mixer(
    image,
    draw,
    channels: list[dict],
    streams_by_ch: list[list[dict]],
    icon_size: int = 24,
    badge_fill=None,
    badge_outline=None,
):
    """Draw small icons on the top-right of each channel column."""
    for i, ch in enumerate(channels):
        col_right = i * 120 + 120  # each column is 120 px wide
        cx = col_right - 18
        cy_top = 10
        r = icon_size // 2

        streams = streams_by_ch[i] if i < len(streams_by_ch) else []
        icon_img, _icon_changed = _cached_channel_icon(i, ch, streams, icon_size)

        paste_area = (cx - r, cy_top + 1, cx + r, cy_top + 1 + icon_size)
        image.paste(
            icon_img.convert("RGBA"),
            (paste_area[0], paste_area[1]),
            icon_img,
        )




def handle_termination(_signal_number, _frame) -> None:
    """Let user-service shutdown follow the same safe cleanup path as Ctrl+C."""
    raise KeyboardInterrupt


def default_display_socket() -> Path | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return None
    return Path(runtime) / "openstream100" / "display.sock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenStream100 four-channel PipeWire mixer."
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="choose and save the application assigned to each encoder",
    )
    parser.add_argument(
        "--list-streams",
        action="store_true",
        help="list currently discovered playback applications and exit",
    )
    parser.add_argument(
        "--calibrate-buttons",
        action="store_true",
        help="learn and save the physical push-button bit for each encoder",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"configuration path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--counts-per-percent",
        type=float,
        default=None,
        help="override the saved knob sensitivity with raw encoder counts per 1%%",
    )
    parser.add_argument(
        "--invert",
        default="",
        help="comma-separated encoder numbers to reverse, such as 2,4",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="run the controls without driving the Stream 100 LCD",
    )
    parser.add_argument(
        "--display-solid-test",
        action="store_true",
        help="show a solid palette color to diagnose LCD pixel packing",
    )
    parser.add_argument(
        "--display-protocol-test",
        action="store_true",
        help="transplant the valid RGBW image into the valid white transfer",
    )
    parser.add_argument(
        "--display-object-test",
        action="store_true",
        help="map the native 0x35 image objects and 0x41 channel levels",
    )
    parser.add_argument(
        "--display-fullscreen-test",
        action="store_true",
        help="map native action-zone styles that cover the bottom area",
    )
    parser.add_argument(
        "--display-action-color-test",
        action="store_true",
        help="map action-zone colour packing against the Notepad card",
    )
    parser.add_argument(
        "--display-helper",
        type=Path,
        default=DEFAULT_DISPLAY_HELPER,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--display-replay",
        type=Path,
        default=DEFAULT_DISPLAY_REPLAY,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--display-socket",
        type=Path,
        default=default_display_socket(),
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--require-display-broker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


UI_COLORS: list[tuple[int, int, int]] = [
    (10, 14, 20),     # 0 background
    (24, 31, 42),     # 1 channel panel
    (239, 244, 249),  # 2 primary text
    (135, 148, 163),  # 3 secondary text
    (48, 204, 190),   # 4 cyan accent
    (54, 211, 128),   # 5 active green
    (246, 190, 64),   # 6 warning amber
    (245, 77, 91),    # 7 muted red
    (57, 69, 84),     # 8 outlines
    (255, 255, 255),  # 9 white
    (18, 77, 73),     # 10 dark cyan
    (22, 74, 48),     # 11 dark green
    (82, 34, 43),     # 12 dark red
    (44, 48, 56),     # 13 inactive bar
    (80, 92, 107),    # 14 tick marks
    (0, 0, 0),        # 15 black
]
DEFAULT_CHANNEL_COLORS: list[tuple[int, int, int]] = [
    (48, 204, 190),
    (54, 211, 128),
    (246, 190, 64),
    (91, 130, 246),
]
NOTEPAD_FONT_FAMILIES = ("sans", "serif", "monospace")
NOTEPAD_FONT_STYLES = ("regular", "bold", "italic", "bold-italic")
NOTEPAD_ALIGNMENTS = ("left", "center", "right")
DEFAULT_NOTEPAD_TEXT_COLOR = "#EFF4F9"
MIN_NOTEPAD_FONT_SIZE = 10
MAX_NOTEPAD_FONT_SIZE = 40
DEFAULT_NOTEPAD_STYLE: dict[str, object] = {
    "font_size": 0,
    "font_family": "sans",
    "font_style": "regular",
    "text_color": DEFAULT_NOTEPAD_TEXT_COLOR,
    "alignment": "left",
}

DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 272
DISPLAY_PALETTE_BYTES = 512
DISPLAY_FRAMEBUFFER_BYTES = DISPLAY_WIDTH * DISPLAY_HEIGHT
NATIVE_METADATA_MAGICS = (b"S1C2", b"S1C3")

# Button labels overlay image — cached until its selection or source file changes
_button_labels_overlay: Any = None


def _open_button_labels_overlay(path: Path) -> Any:
    from PIL import Image

    with Image.open(path) as opened:
        if opened.size != (DISPLAY_WIDTH, 80):
            raise ValueError(
                f"button overlay must be {DISPLAY_WIDTH}x80 pixels, got "
                f"{opened.size[0]}x{opened.size[1]}"
            )
        image = opened.convert("RGBA")
        image.load()
    return image


def _load_button_labels_overlay() -> Any:
    """Load the selected built-in or per-user button labels overlay.

    A missing or invalid custom overlay safely falls back to Boxes.
    """
    global _button_labels_overlay, _button_overlay_style_cache
    style = _load_button_overlay_style()
    built_in_paths = {
        "boxes": APP_DIRECTORY / "button_labels_overlay_boxes.png",
        "basic": APP_DIRECTORY / "button_labels_overlay_basic.png",
        "glass": APP_DIRECTORY / "button_labels_overlay_glass.png",
    }
    overlay_path = (
        CUSTOM_BUTTON_OVERLAY_PATH
        if style == "custom"
        else built_in_paths.get(style, built_in_paths["boxes"])
    )
    effective_style = style
    custom_missing = style == "custom" and not overlay_path.is_file()
    if custom_missing:
        overlay_path = built_in_paths["boxes"]
        effective_style = "boxes"

    cache_key: tuple[str, str, int, int] | None = None
    try:
        source_stat = overlay_path.stat()
        cache_key = (
            style,
            str(overlay_path),
            source_stat.st_mtime_ns,
            source_stat.st_size,
        )
        if (
            _button_labels_overlay is not None
            and _button_overlay_style_cache == cache_key
        ):
            return _button_labels_overlay
        if custom_missing:
            print(
                "[overlay] Custom button overlay is missing; using Boxes.",
                file=sys.stderr,
            )
        image = _open_button_labels_overlay(overlay_path)
    except Exception as err:
        if effective_style != "boxes":
            failed_cache_key = cache_key
            print(
                f"[overlay] Failed to load {style} button overlay: {err}; "
                "using Boxes.",
                file=sys.stderr,
            )
            overlay_path = built_in_paths["boxes"]
            try:
                fallback_stat = overlay_path.stat()
                image = _open_button_labels_overlay(overlay_path)
                effective_style = "boxes"
                cache_key = failed_cache_key or (
                    style,
                    str(overlay_path),
                    fallback_stat.st_mtime_ns,
                    fallback_stat.st_size,
                )
            except Exception as fallback_error:
                print(
                    f"[overlay] Failed to load Boxes fallback: {fallback_error}",
                    file=sys.stderr,
                )
                _button_labels_overlay = None
                _button_overlay_style_cache = None
                return None
        else:
            print(
                f"[overlay] Failed to load button labels overlay: {err}",
                file=sys.stderr,
            )
            _button_labels_overlay = None
            _button_overlay_style_cache = None
            return None

    _button_labels_overlay = image
    _button_overlay_style_cache = cache_key
    print(
        f"[overlay] Loaded button labels overlay ({effective_style}): "
        f"{image.size[0]}x{image.size[1]}",
        file=sys.stderr,
    )
    return _button_labels_overlay
DISPLAY_MESSAGE_BYTES = DISPLAY_PALETTE_BYTES + DISPLAY_FRAMEBUFFER_BYTES
DISPLAY_INIT_PACKETS = 192
DISPLAY_FRAME_PACKETS = 320
DISPLAY_FRAME_TEMPLATE_OFFSET_PACKETS = 43
DISPLAY_ISO_PACKET_BYTES = 952
DISPLAY_CHUNK_OFFSETS = (
    (0, 0), (4, 0), (0, 2), (4, 2),
    (2, 0), (6, 0), (2, 2), (6, 2),
    (0, 1), (2, 1), (4, 1), (6, 1),
    (0, 3), (2, 3), (4, 3), (6, 3),
    (1, 0), (3, 0), (5, 0), (7, 0),
    (1, 2), (3, 2), (5, 2), (7, 2),
    (1, 1), (3, 1), (5, 1), (7, 1),
    (1, 3), (3, 3), (5, 3), (7, 3),
)


class DisplayController:
    """Send the newest rendered screen to the native display helper."""

    def __init__(
        self,
        helper: Path,
        replay: Path,
        helper_arguments: tuple[str, ...] = (),
        display_socket: Path | None = None,
        require_broker: bool = False,
    ):
        self.process: subprocess.Popen[bytes] | None = None
        self.connection: socket.socket | None = None
        self.resident_display_mode: int | None = None
        if not helper_arguments and display_socket is not None:
            try:
                self._connect_broker(display_socket, require_broker)
            except (OSError, RuntimeError) as error:
                if require_broker:
                    raise RuntimeError(
                        f"persistent display broker is unavailable: {error}"
                    ) from error
        elif require_broker:
            raise RuntimeError("persistent display broker socket is unavailable")

        if self.connection is None:
            if not helper.exists():
                raise RuntimeError(f"display helper does not exist: {helper}")
            if not replay.exists():
                raise RuntimeError(f"display replay does not exist: {replay}")
            self.process = subprocess.Popen(
                [str(helper), str(replay), *helper_arguments],
                stdin=subprocess.PIPE,
                bufsize=0,
            )
        self.frames: Queue[tuple[bytes | None, threading.Event | None]] = Queue(
            maxsize=1
        )
        self.error: str | None = None
        self.thread = threading.Thread(
            target=self._write_frames,
            name="stream100-display",
            daemon=True,
        )
        self.thread.start()

    @staticmethod
    def _receive_exact(connection: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = connection.recv(size - len(data))
            if not chunk:
                raise RuntimeError("display broker closed the connection")
            data.extend(chunk)
        return bytes(data)

    def _connect_broker(self, display_socket: Path, wait: bool) -> None:
        deadline = time.monotonic() + (8.0 if wait else 0.0)
        last_error: OSError | RuntimeError | None = None
        while True:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(8.0)
            try:
                connection.connect(str(display_socket))
                handshake = self._receive_exact(connection, 6)
                if handshake[:4] != b"OSD1" or handshake[4] not in (0, 1):
                    raise RuntimeError("display broker returned an invalid handshake")
                self.connection = connection
                self.resident_display_mode = handshake[5] if handshake[4] else None
                return
            except (OSError, RuntimeError) as error:
                last_error = error
                connection.close()
            if time.monotonic() >= deadline:
                assert last_error is not None
                raise last_error
            time.sleep(0.10)

    def _write_frames(self) -> None:
        try:
            while True:
                frame, written = self.frames.get()
                if frame is None:
                    if written is not None:
                        written.set()
                    break
                try:
                    connection = getattr(self, "connection", None)
                    if connection is not None:
                        connection.sendall(frame)
                        if self._receive_exact(connection, 1) != b"\x06":
                            raise RuntimeError(
                                "display broker did not acknowledge the framebuffer"
                            )
                    else:
                        assert self.process is not None
                        assert self.process.stdin is not None
                        self.process.stdin.write(frame)
                        self.process.stdin.flush()
                finally:
                    if written is not None:
                        written.set()
        except (BrokenPipeError, ConnectionError, OSError, RuntimeError) as error:
            self.error = str(error)
        finally:
            connection = getattr(self, "connection", None)
            if connection is not None:
                try:
                    connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                connection.close()
            elif self.process is not None and self.process.stdin is not None:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass

    def submit(self, frame: bytes) -> None:
        if len(frame) != DISPLAY_MESSAGE_BYTES:
            raise RuntimeError(
                f"display frame has {len(frame)} bytes; expected {DISPLAY_MESSAGE_BYTES}"
            )
        if self.error is not None:
            return
        while True:
            try:
                self.frames.put_nowait((frame, None))
                return
            except Full:
                try:
                    _discarded, written = self.frames.get_nowait()
                    if written is not None:
                        written.set()
                except Empty:
                    pass

    def submit_ordered(self, frame: bytes, timeout: float = 8.0) -> None:
        """Write a frame before allowing a newer frame to replace it."""
        if len(frame) != DISPLAY_MESSAGE_BYTES:
            raise RuntimeError(
                f"display frame has {len(frame)} bytes; expected {DISPLAY_MESSAGE_BYTES}"
            )
        if self.error is not None:
            raise RuntimeError(self.error)

        written = threading.Event()
        while True:
            try:
                self.frames.put_nowait((frame, written))
                break
            except Full:
                try:
                    _discarded, discarded_written = self.frames.get_nowait()
                    if discarded_written is not None:
                        discarded_written.set()
                except Empty:
                    pass

        if not written.wait(timeout):
            raise RuntimeError("timed out while sending the startup display")
        if self.error is not None:
            raise RuntimeError(self.error)

    def problem(self) -> str | None:
        if self.error is not None:
            return self.error
        if self.process is None:
            return None
        return_code = self.process.poll()
        if return_code is not None:
            return f"native display helper exited with status {return_code}"
        return None

    def close(self) -> None:
        while True:
            try:
                self.frames.put_nowait((None, None))
                break
            except Full:
                try:
                    _discarded, written = self.frames.get_nowait()
                    if written is not None:
                        written.set()
                except Empty:
                    pass
        self.thread.join(timeout=3.0)
        connection = getattr(self, "connection", None)
        if self.thread.is_alive() and connection is not None:
            connection.close()
            self.thread.join(timeout=1.0)
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()


class PulsePeakReader:
    """Capture mono or stereo peaks from a PipeWire-Pulse stream or sink."""

    def __init__(
        self, target: MeterTarget, channel_mode: str = DEFAULT_METER_CHANNEL_MODE
    ) -> None:
        if channel_mode not in METER_CHANNEL_MODES:
            raise ValueError("unsupported activity monitoring mode")
        target_kind, identifier, monitor_source, label = target
        self.channel_mode = channel_mode
        self.channel_count = 2 if channel_mode == "stereo" else 1
        arguments = [
            "parec",
            "--raw",
            "--format=float32le",
            f"--rate={METER_CAPTURE_RATE}",
            f"--channels={self.channel_count}",
            "--latency-msec=40",
            f"--device={monitor_source}",
        ]
        if target_kind == "stream":
            arguments.append(f"--monitor-stream={identifier}")
        self.process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self.target = target
        self.label = label
        self.closing = False
        self.lock = threading.Lock()
        self.peaks: StereoLevel = (0.0, 0.0)
        self.updated_at = 0.0
        self.connected_reported = False
        self.thread = threading.Thread(
            target=self._read_samples,
            name="openstream100-level-reader",
            daemon=True,
        )
        self.thread.start()

    def _read_samples(self) -> None:
        assert self.process.stdout is not None
        pending = bytearray()
        window_frames = max(
            1, round(METER_CAPTURE_RATE * METER_CAPTURE_WINDOW_SECONDS)
        )
        window_bytes = window_frames * self.channel_count * 4
        try:
            while True:
                chunk = self.process.stdout.read(window_bytes - len(pending))
                if not chunk:
                    break
                pending.extend(chunk)
                while len(pending) >= window_bytes:
                    block = bytes(pending[:window_bytes])
                    del pending[:window_bytes]
                    left_peak = 0.0
                    right_peak = 0.0
                    if self.channel_count == 2:
                        for left, right in struct.iter_unpack("<ff", block):
                            if math.isfinite(left):
                                left_peak = max(left_peak, abs(left))
                            if math.isfinite(right):
                                right_peak = max(right_peak, abs(right))
                    else:
                        for (value,) in struct.iter_unpack("<f", block):
                            if math.isfinite(value):
                                left_peak = max(left_peak, abs(value))
                        right_peak = left_peak
                    with self.lock:
                        self.peaks = (
                            max(0.0, min(4.0, left_peak)),
                            max(0.0, min(4.0, right_peak)),
                        )
                        self.updated_at = time.monotonic()
                        if not self.connected_reported:
                            print(
                                "Audio visualizer connected to "
                                f"{self.label} through its playback monitor.",
                                flush=True,
                            )
                            self.connected_reported = True
        except (OSError, ValueError, struct.error):
            return
        finally:
            message = ""
            if self.process.poll() is not None and self.process.stderr is not None:
                try:
                    message = self.process.stderr.read().decode(
                        "utf-8", errors="replace"
                    ).strip()
                except OSError:
                    pass
            if not self.connected_reported and not self.closing:
                detail = message.splitlines()[-1] if message else "no peak data received"
                print(
                    f"Audio visualizer could not monitor {self.label}: {detail}",
                    file=sys.stderr,
                    flush=True,
                )
            elif self.connected_reported and not self.closing:
                detail = message.splitlines()[-1] if message else "capture stream ended"
                print(
                    f"Audio visualizer disconnected from {self.label}: {detail}",
                    file=sys.stderr,
                    flush=True,
                )

    def sample(self, now: float) -> StereoLevel | None:
        with self.lock:
            if now - self.updated_at > 0.75:
                return None
            return self.peaks

    def close(self) -> None:
        self.closing = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()
        self.thread.join(timeout=1.0)


class PipeWireLevelMonitor:
    """Aggregate and smooth peak readers for the four visible channels."""

    def __init__(
        self, channel_mode: str = DEFAULT_METER_CHANNEL_MODE
    ) -> None:
        if channel_mode not in METER_CHANNEL_MODES:
            raise ValueError("unsupported activity monitoring mode")
        self.channel_mode = channel_mode
        self.signature: tuple[tuple[str, ...], ...] = ((), (), (), ())
        self.readers: list[list[PulsePeakReader]] = [[], [], [], []]
        self.smoothed: list[StereoLevel] = [
            (0.0, 0.0),
            (0.0, 0.0),
            (0.0, 0.0),
            (0.0, 0.0),
        ]
        self.next_retry = 0.0

    def configure(self, targets: list[list[MeterTarget]]) -> None:
        signature = tuple(tuple(channel) for channel in targets)
        now = time.monotonic()
        if signature != self.signature:
            self.close_readers()
            self.signature = signature
            self.next_retry = 0.0

        # A paused or temporarily unmonitorable application must not tear down
        # the healthy readers for the other three controls. Retain every live
        # monitor process and retry only the individual targets that exited.
        live_targets: list[set[str]] = []
        for channel in range(4):
            live_readers = [
                reader
                for reader in self.readers[channel]
                if reader.process.poll() is None
            ]
            stopped_readers = [
                reader
                for reader in self.readers[channel]
                if reader.process.poll() is not None
            ]
            for reader in stopped_readers:
                reader.close()
            self.readers[channel] = live_readers
            live_targets.append({reader.target for reader in live_readers})

        if now < self.next_retry:
            return
        self.next_retry = now + 5.0
        for channel, channel_targets in enumerate(targets):
            for target in channel_targets:
                if target in live_targets[channel]:
                    continue
                try:
                    self.readers[channel].append(
                        PulsePeakReader(target, self.channel_mode)
                    )
                except OSError:
                    continue

    @staticmethod
    def _visual_level(peak: float) -> float:
        if peak <= 0.001:
            return 0.0
        decibels = 20.0 * math.log10(peak)
        return max(0.0, min(1.0, (decibels + 60.0) / 60.0))

    def levels(self, fallback: list[float]) -> list[StereoLevel]:
        del fallback  # The white volume marker is transported independently.
        now = time.monotonic()
        result: list[StereoLevel] = []
        for channel in range(4):
            samples = [
                sample
                for reader in self.readers[channel]
                if (sample := reader.sample(now)) is not None
            ]
            if samples:
                targets = (
                    self._visual_level(max(sample[0] for sample in samples)),
                    self._visual_level(max(sample[1] for sample in samples)),
                )
                smoothed: list[float] = []
                for side in range(2):
                    previous = self.smoothed[channel][side]
                    target = targets[side]
                    # Fast attack and a gentler release make speech and music
                    # readable without making either native bar look nervous.
                    smoothed.append(
                        target if target >= previous else max(target, previous * 0.72)
                    )
                self.smoothed[channel] = (smoothed[0], smoothed[1])
            else:
                # A missing monitor stream means there is no trustworthy live
                # activity value.  Leave the VU bars empty; the independent
                # white volume marker continues to show the saved level.
                self.smoothed[channel] = (0.0, 0.0)
            result.append(self.smoothed[channel])
        return result

    def close_readers(self) -> None:
        for channel_readers in self.readers:
            for reader in channel_readers:
                reader.close()
        self.readers = [[], [], [], []]

    def close(self) -> None:
        self.close_readers()


_FONT_CACHE: dict[tuple[int, str, bool, bool], Any] = {}
_FONT_PATHS: dict[tuple[str, bool, bool], str | None] = {}


def font_path(bold: bool, family: str = "sans", italic: bool = False) -> str | None:
    family = family if family in NOTEPAD_FONT_FAMILIES else "sans"
    key = (family, bold, italic)
    if key in _FONT_PATHS:
        return _FONT_PATHS[key]
    if shutil.which("fc-match") is None:
        return None
    generic_family = {
        "sans": "sans-serif",
        "serif": "serif",
        "monospace": "monospace",
    }[family]
    style = (
        "Bold Italic"
        if bold and italic
        else "Bold" if bold else "Italic" if italic else "Regular"
    )
    patterns = [f"{generic_family}:style={style}", generic_family]
    for pattern in patterns:
        result = command(["fc-match", "-f", "%{file}", pattern])
        candidate = result.stdout.strip()
        if result.returncode == 0 and candidate and Path(candidate).exists():
            _FONT_PATHS[key] = candidate
            return candidate
    _FONT_PATHS[key] = None
    return None


def ui_font(
    size: int,
    bold: bool = False,
    family: str = "sans",
    italic: bool = False,
):
    family = family if family in NOTEPAD_FONT_FAMILIES else "sans"
    key = (size, family, bold, italic)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    from PIL import ImageFont

    path = font_path(bold, family, italic)
    if path is not None:
        font = ImageFont.truetype(path, size=size)
    else:
        base_name = {
            "sans": "DejaVuSans",
            "serif": "DejaVuSerif",
            "monospace": "DejaVuSansMono",
        }[family]
        suffix = (
            "-BoldItalic" if family == "serif" and bold and italic
            else "-Italic" if family == "serif" and italic
            else "-BoldOblique" if bold and italic
            else "-Oblique" if italic
            else "-Bold" if bold
            else ""
        )
        try:
            font = ImageFont.truetype(f"{base_name}{suffix}.ttf", size)
        except OSError:
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def draw_centered(draw, text: str, y: int, font, fill, left: int, right: int) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    draw.text((left + (right - left - width) // 2, y), text, font=font, fill=fill)


def normalise_notepad_style(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    font_size = source.get("font_size", 0)
    if (
        isinstance(font_size, bool)
        or not isinstance(font_size, (int, float))
        or (font_size != 0 and not MIN_NOTEPAD_FONT_SIZE <= font_size <= MAX_NOTEPAD_FONT_SIZE)
    ):
        font_size = 0
    family = source.get("font_family", "sans")
    style = source.get("font_style", "regular")
    alignment = source.get("alignment", "left")
    text_color = str(source.get("text_color", "")).strip().upper()
    if not (
        len(text_color) == 7
        and text_color.startswith("#")
        and all(character in "0123456789ABCDEF" for character in text_color[1:])
    ):
        text_color = DEFAULT_NOTEPAD_TEXT_COLOR
    return {
        "font_size": int(font_size),
        "font_family": family if family in NOTEPAD_FONT_FAMILIES else "sans",
        "font_style": style if style in NOTEPAD_FONT_STYLES else "regular",
        "text_color": text_color,
        "alignment": alignment if alignment in NOTEPAD_ALIGNMENTS else "left",
    }


def notepad_text_color(style: dict[str, object]) -> tuple[int, int, int]:
    value = str(style["text_color"])
    return tuple(int(value[offset : offset + 2], 16) for offset in (1, 3, 5))


def fit_label(draw, label: str, font, width: int) -> list[str]:
    label = " ".join(label.split()) or "Disabled"
    words = label.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if not lines:
        lines = [label]
    lines = lines[:2]
    while draw.textlength(lines[-1], font=font) > width and len(lines[-1]) > 2:
        lines[-1] = lines[-1][:-2].rstrip() + "…"
    if len(words) > 1 and len(lines) == 2 and " ".join(lines) != label:
        while draw.textlength(lines[-1] + "…", font=font) > width and len(lines[-1]) > 2:
            lines[-1] = lines[-1][:-1].rstrip()
        if not lines[-1].endswith("…"):
            lines[-1] += "…"
    return lines


def wrap_note_text(draw, text: str, font, width: int) -> list[str]:
    """Wrap user-authored note text while preserving intentional line breaks."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    lines: list[str] = []
    for paragraph in normalized.strip("\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if draw.textlength(candidate, font=font) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            if draw.textlength(word, font=font) <= width:
                current = word
                continue
            fragment = ""
            for character in word:
                candidate = fragment + character
                if fragment and draw.textlength(candidate, font=font) > width:
                    lines.append(fragment)
                    fragment = character
                else:
                    fragment = candidate
            current = fragment
        if current:
            lines.append(current)
    return lines


def fit_note_text(
    draw,
    text: str,
    width: int,
    height: int,
    font_size: int = 0,
    font_family: str = "sans",
    font_style: str = "regular",
) -> tuple[Any, list[str], int, bool]:
    """Fit an automatic or fixed note font and ellipsize only when required."""
    family = font_family if font_family in NOTEPAD_FONT_FAMILIES else "sans"
    style = font_style if font_style in NOTEPAD_FONT_STYLES else "regular"
    bold = style in {"bold", "bold-italic"}
    italic = style in {"italic", "bold-italic"}
    sizes = (
        (max(MIN_NOTEPAD_FONT_SIZE, min(MAX_NOTEPAD_FONT_SIZE, font_size)),)
        if font_size
        else (26, 24, 22, 20, 18, 16, 14)
    )
    selected_font = ui_font(sizes[-1], bold, family, italic)
    selected_lines = wrap_note_text(draw, text, selected_font, width)
    selected_line_height = max(sizes[-1] + 3, 18)
    for size in sizes:
        font = ui_font(size, bold, family, italic)
        bounds = draw.textbbox((0, 0), "Ag", font=font)
        line_height = max(size + 3, bounds[3] - bounds[1] + 5)
        lines = wrap_note_text(draw, text, font, width)
        if len(lines) * line_height <= height:
            return font, lines, line_height, False
        selected_font = font
        selected_lines = lines
        selected_line_height = line_height

    visible_count = max(1, height // selected_line_height)
    visible_lines = selected_lines[:visible_count]
    if len(selected_lines) > visible_count:
        final_line = visible_lines[-1].rstrip()
        while (
            final_line
            and draw.textlength(final_line + "…", font=selected_font) > width
        ):
            final_line = final_line[:-1].rstrip()
        visible_lines[-1] = final_line + "…"
        return selected_font, visible_lines, selected_line_height, True
    return selected_font, visible_lines, selected_line_height, False


def note_line_x(
    draw,
    line: str,
    font,
    left: int,
    width: int,
    alignment: str,
) -> int:
    line_width = round(draw.textlength(line, font=font))
    if alignment == "center":
        return left + max(0, (width - line_width) // 2)
    if alignment == "right":
        return left + max(0, width - line_width)
    return left


def nearest_palette_indices(
    rgb_image, palette_colors: list[tuple[int, int, int]] | None = None
) -> bytes:
    active_colors = palette_colors if palette_colors is not None else UI_COLORS
    cache: dict[tuple[int, int, int], int] = {}
    indexed = bytearray(DISPLAY_FRAMEBUFFER_BYTES)
    if hasattr(rgb_image, "get_flattened_data"):
        pixels = rgb_image.get_flattened_data()
    else:
        pixels = rgb_image.getdata()
    for position, color in enumerate(pixels):
        index = cache.get(color)
        if index is None:
            index = min(
                range(len(active_colors)),
                key=lambda candidate: sum(
                    (color[channel] - active_colors[candidate][channel]) ** 2
                    for channel in range(3)
                ),
            )
            cache[color] = index
        indexed[position] = index
    return bytes(indexed)


def pack_device_framebuffer(row_major: bytes) -> bytes:
    """Pack 480x272 pixels into the controller's 32 interleaved planes."""
    if len(row_major) != DISPLAY_FRAMEBUFFER_BYTES:
        raise RuntimeError("invalid row-major display framebuffer")
    packed = bytearray()

    for x_offset, y_offset in DISPLAY_CHUNK_OFFSETS:
        chunk_start = len(packed)
        for y in range(y_offset, DISPLAY_HEIGHT, 4):
            row = y * DISPLAY_WIDTH
            for x in range(x_offset, DISPLAY_WIDTH, 8):
                packed.append(row_major[row + x])
        if len(packed) - chunk_start != 4080:
            raise RuntimeError("internal display chunk size error")

    if len(packed) != DISPLAY_FRAMEBUFFER_BYTES:
        raise RuntimeError("internal display packing error")
    return bytes(packed)


def palette_rgb565(
    palette_colors: list[tuple[int, int, int]] | None = None
) -> bytes:
    result = bytearray()
    active_colors = palette_colors if palette_colors is not None else UI_COLORS
    if len(active_colors) > 240:
        raise RuntimeError("display palette leaves no room for native metadata")
    colors = active_colors + [(0, 0, 0)] * (256 - len(active_colors))
    for red, green, blue in colors:
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        result.extend(value.to_bytes(2, "little"))
    return bytes(result)


def rich_palette_indices(
    rgb_image,
    fixed_colors: list[tuple[int, int, int]],
    palette_reference=None,
    dynamic_color_count: int = 224,
) -> tuple[bytes, list[tuple[int, int, int]]]:
    """Quantize an image without using metadata slots 240-255.

    ``palette_reference`` may omit page-specific text and other overlays.  This
    keeps the adaptive portion of the palette identical between mixer pages
    that share a background, allowing the controller to redraw them without a
    protected backlight-off palette replacement.
    """
    if len(fixed_colors) != 16:
        raise RuntimeError("the display UI requires exactly 16 fixed colors")
    if dynamic_color_count < 1 or len(fixed_colors) + dynamic_color_count > 240:
        raise RuntimeError("invalid adaptive display palette size")

    reference_image = palette_reference if palette_reference is not None else rgb_image
    reference_quantized = reference_image.quantize(
        colors=dynamic_color_count, method=0, dither=1
    )
    raw_palette = list(reference_quantized.getpalette() or [])
    raw_palette.extend([0] * (dynamic_color_count * 3 - len(raw_palette)))
    dynamic_colors = [
        tuple(raw_palette[offset : offset + 3])
        for offset in range(0, dynamic_color_count * 3, 3)
    ]
    active_colors = [*fixed_colors, *dynamic_colors]

    # Quantize the completed page quickly, then map its small colour table to
    # the stable palette derived above.  Doing the nearest-colour work for at
    # only the requested table entries avoids a slow per-pixel Python search.
    completed_quantized = rgb_image.quantize(
        colors=dynamic_color_count, method=0, dither=1
    )
    completed_palette = list(completed_quantized.getpalette() or [])
    completed_palette.extend(
        [0] * (dynamic_color_count * 3 - len(completed_palette))
    )
    table_map: list[int] = []
    for offset in range(0, dynamic_color_count * 3, 3):
        red, green, blue = completed_palette[offset : offset + 3]
        table_map.append(
            min(
                range(len(active_colors)),
                key=lambda index: (
                    (active_colors[index][0] - red) ** 2
                    + (active_colors[index][1] - green) ** 2
                    + (active_colors[index][2] - blue) ** 2
                ),
            )
        )
    completed_pixels = (
        completed_quantized.get_flattened_data()
        if hasattr(completed_quantized, "get_flattened_data")
        else completed_quantized.getdata()
    )
    indexed = bytearray(table_map[int(value)] for value in completed_pixels)

    # Preserve exact UI colors wherever ImageDraw produced a solid pixel. Text
    # antialiasing and photographic areas continue to use the adaptive palette.
    fixed_lookup = {color: index for index, color in enumerate(fixed_colors)}
    original_pixels = (
        rgb_image.get_flattened_data()
        if hasattr(rgb_image, "get_flattened_data")
        else rgb_image.getdata()
    )
    for position, color in enumerate(original_pixels):
        fixed_index = fixed_lookup.get(color)
        if fixed_index is not None:
            indexed[position] = fixed_index
    return bytes(indexed), active_colors


def custom_meter_palette_index(channel: int, side: int, step: int) -> int:
    """Return the reserved palette index for one custom stereo-meter band."""
    return CUSTOM_METER_PALETTE_BASE + (
        (channel * 2 + side) * CUSTOM_METER_STEPS + step
    )


def custom_meter_static_palette(
    channel_colors: list[tuple[int, int, int]], meter_style: str
) -> list[tuple[int, int, int]]:
    """Build restrained channel-colour tints for custom meter decoration."""
    if len(channel_colors) != 4 or meter_style not in METER_STYLES[1:]:
        raise RuntimeError("custom visualiser palette requires four channels")
    if meter_style == "segmented":
        base_scale, step_scale, floor = 22, 1, 7
    elif meter_style == "rounded":
        base_scale, step_scale, floor = 14, 1, 8
    else:
        base_scale, step_scale, floor = 28, 2, 6

    colors: list[tuple[int, int, int]] = []
    for channel_color_value in channel_colors:
        for _side in range(2):
            for step in range(CUSTOM_METER_STEPS):
                scale = base_scale + step * step_scale
                colors.append(
                    tuple(
                        min(255, floor + (component * scale + 50) // 100)
                        for component in channel_color_value
                    )
                )
    return colors


def draw_custom_meter_indices(row_major: bytearray, meter_style: str) -> None:
    """Stamp a distinct meter silhouette into the indexed framebuffer.

    These pixels form a static, channel-tinted track and surround. The proven
    native meter objects animate above it, avoiding continuous framebuffer
    traffic while preserving a visibly different treatment for each style.
    """
    if meter_style not in METER_STYLES[1:]:
        return

    def set_pixel(x: int, y: int, palette_index: int) -> None:
        if 0 <= x < DISPLAY_WIDTH and 0 <= y < DISPLAY_HEIGHT:
            row_major[y * DISPLAY_WIDTH + x] = palette_index

    def in_rounded_rectangle(
        x: int, y: int, left: int, top: int, right: int, bottom: int, radius: int
    ) -> bool:
        if x < left or x > right or y < top or y > bottom:
            return False
        if left + radius <= x <= right - radius or top + radius <= y <= bottom - radius:
            return True
        center_x = left + radius if x < left + radius else right - radius
        center_y = top + radius if y < top + radius else bottom - radius
        return (x - center_x) ** 2 + (y - center_y) ** 2 <= radius**2

    for channel in range(4):
        channel_left = channel * 120
        if meter_style == "segmented":
            columns = ((channel_left + 43, channel_left + 57),
                       (channel_left + 63, channel_left + 77))
            segment_height = 4
            segment_pitch = 7
            bottom = 189
            for side, (left, right) in enumerate(columns):
                for step in range(CUSTOM_METER_STEPS):
                    segment_bottom = bottom - step * segment_pitch
                    segment_top = segment_bottom - segment_height + 1
                    palette_index = custom_meter_palette_index(channel, side, step)
                    for y in range(segment_top, segment_bottom + 1):
                        for x in range(left, right + 1):
                            # One-pixel clipped corners make each segment a
                            # compact pill without spending extra palette slots.
                            if y in (segment_top, segment_bottom) and x in (left, right):
                                continue
                            set_pixel(x, y, palette_index)
                    # Short outer ticks remain visible around the native fill
                    # and reinforce the separated ladder treatment.
                    tick_y = (segment_top + segment_bottom) // 2
                    for y in (tick_y, min(segment_bottom, tick_y + 1)):
                        for x in range(left - 5, left - 1):
                            set_pixel(x, y, palette_index)
                        for x in range(right + 2, right + 6):
                            set_pixel(x, y, palette_index)
            continue

        if meter_style == "rounded":
            columns = ((channel_left + 42, channel_left + 58),
                       (channel_left + 62, channel_left + 78))
            top, bottom, radius = 82, 189, 8
        else:  # slim
            columns = ((channel_left + 49, channel_left + 55),
                       (channel_left + 65, channel_left + 71))
            top, bottom, radius = 80, 189, 3

        height = bottom - top + 1
        for side, (left, right) in enumerate(columns):
            for y in range(top, bottom + 1):
                # Step zero is the lowest band; fourteen is the highest.
                step = min(
                    CUSTOM_METER_STEPS - 1,
                    (bottom - y) * CUSTOM_METER_STEPS // height,
                )
                palette_index = custom_meter_palette_index(channel, side, step)
                for x in range(left, right + 1):
                    if in_rounded_rectangle(x, y, left, top, right, bottom, radius):
                        set_pixel(x, y, palette_index)

                # A slim outer capsule/guide stays outside the firmware's
                # moving center fill, keeping Rounded and Slim identifiable.
                outer_radius = radius + 3
                for x in range(left - 3, right + 4):
                    in_outer = in_rounded_rectangle(
                        x, y, left - 3, top, right + 3, bottom, outer_radius
                    )
                    in_inner = in_rounded_rectangle(
                        x, y, left - 1, top, right + 1, bottom, radius + 1
                    )
                    if in_outer and not in_inner:
                        set_pixel(x, y, palette_index)


def flatten_display_image(opened, background: tuple[int, int, int] = UI_COLORS[0]):
    """Composite transparent imports before converting them to indexed RGB565.

    A plain RGBA-to-RGB conversion discards alpha and exposes arbitrary hidden
    RGB values. The supplied OpenStream100 PNG is mostly transparent and stores
    bright gray/blue data under those pixels, which caused its washed-out LCD
    rendering in both mixer-background and startup modes.
    """
    from PIL import Image, ImageOps

    transposed = ImageOps.exif_transpose(opened)
    has_alpha = "A" in transposed.getbands() or "transparency" in transposed.info
    if not has_alpha:
        return transposed.convert("RGB")
    foreground = transposed.convert("RGBA")
    backdrop = Image.new("RGBA", foreground.size, (*background, 255))
    return Image.alpha_composite(backdrop, foreground).convert("RGB")


def load_display_background(path: Path):
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for the LCD. On Fedora, install python3-pillow."
        ) from error

    try:
        with Image.open(path) as opened:
            image = flatten_display_image(opened)
            image = ImageOps.fit(
                image,
                (DISPLAY_WIDTH, DISPLAY_HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (OSError, ValueError) as error:
        raise RuntimeError(f"could not prepare display background: {error}") from error
    image = ImageEnhance.Color(image).enhance(0.82)
    return ImageEnhance.Brightness(image).enhance(0.62)


def captured_rgbw_palette(replay_path: Path) -> bytes:
    # Four RGB565 entries captured from the official RGBW reference, followed
    # by the zero-filled unused palette entries.
    if not replay_path.exists():
        raise RuntimeError("display replay does not exist")
    return bytes.fromhex("1f 00 00 f8 ff ff e0 07") + bytes(
        DISPLAY_PALETTE_BYTES - 8
    )


def captured_rgbw_frame(replay_path: Path) -> bytes:
    # Every one of the 32 captured interleaved planes is identical for this
    # quadrant pattern: red/green above and blue/white below.
    upper = (bytes([1]) * 30 + bytes([3]) * 30) * 34
    lower = (bytes([0]) * 30 + bytes([2]) * 30) * 34
    framebuffer = (upper + lower) * 32
    if len(framebuffer) != DISPLAY_FRAMEBUFFER_BYTES:
        raise RuntimeError("internal RGBW reference size error")
    return captured_rgbw_palette(replay_path) + framebuffer


def captured_template_frame(replay_path: Path) -> bytes:
    """Extract the palette and pixel planes from the replay template."""
    replay = replay_path.read_bytes()
    template_start = DISPLAY_INIT_PACKETS * DISPLAY_ISO_PACKET_BYTES
    template_end = (
        DISPLAY_INIT_PACKETS + DISPLAY_FRAME_PACKETS
    ) * DISPLAY_ISO_PACKET_BYTES
    if len(replay) < template_end:
        raise RuntimeError("display replay is shorter than its frame template")
    template = replay[template_start:template_end]

    palette = b""
    framebuffer = bytearray()
    for chunk in range(32):
        cycle_offset = (
            DISPLAY_FRAME_TEMPLATE_OFFSET_PACKETS + chunk * 8
        ) * DISPLAY_ISO_PACKET_BYTES
        cycle = template[cycle_offset:]
        if cycle[:2] != b"SM":
            raise RuntimeError(f"captured template chunk {chunk} has no SM header")
        message_length = int.from_bytes(cycle[2:4], "little")
        pixel_offset = message_length - (4080 + 1)
        header_offset = pixel_offset - 9
        expected_header = bytes((0x37, 1, chunk, 0, 0, 0, 0, 0xF0, 0x0F))
        if cycle[header_offset:pixel_offset] != expected_header:
            raise RuntimeError(f"captured template chunk {chunk} header did not match")
        pixels = cycle[pixel_offset : pixel_offset + 4080]
        if len(pixels) != 4080:
            raise RuntimeError(f"captured template chunk {chunk} is incomplete")
        framebuffer.extend(pixels)
        if chunk == 0:
            if cycle[8] != 0x38 or cycle[24] != 0x33:
                raise RuntimeError("captured template palette command did not match")
            palette = cycle[31 : 31 + DISPLAY_PALETTE_BYTES]

    frame = palette + bytes(framebuffer)
    if len(frame) != DISPLAY_MESSAGE_BYTES:
        raise RuntimeError("captured template framebuffer has the wrong size")
    return frame


def chunk_mapping_frame() -> bytes:
    """Give each of the controller's 32 framebuffer chunks a unique color."""
    colors = [
        (0, 0, 0),
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
        (255, 128, 0),
        (128, 0, 255),
        (128, 255, 0),
        (0, 255, 128),
        (0, 128, 255),
        (255, 0, 128),
        (255, 255, 255),
        (128, 128, 128),
        (128, 0, 0),
        (0, 128, 0),
        (0, 0, 128),
        (128, 128, 0),
        (128, 0, 128),
        (0, 128, 128),
        (255, 64, 64),
        (64, 255, 64),
        (64, 64, 255),
        (255, 255, 64),
        (255, 64, 255),
        (64, 255, 255),
        (255, 176, 64),
        (176, 64, 255),
        (176, 255, 64),
        (64, 255, 176),
        (64, 176, 255),
        (255, 64, 176),
    ]
    palette = bytearray()
    for red, green, blue in colors + [(0, 0, 0)] * (256 - len(colors)):
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        palette.extend(value.to_bytes(2, "little"))
    framebuffer = b"".join(bytes([chunk + 1]) * 4080 for chunk in range(32))
    if len(framebuffer) != DISPLAY_FRAMEBUFFER_BYTES:
        raise RuntimeError("internal chunk-map framebuffer size error")
    return bytes(palette) + framebuffer


def hold_display_test(
    display: DisplayController,
    seconds: float,
) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        problem = display.problem()
        if problem is not None:
            raise RuntimeError(f"display helper stopped: {problem}")
        time.sleep(0.1)


def run_display_protocol_test(helper: Path, replay: Path) -> int:
    display = DisplayController(helper, replay)
    try:
        channels = [
            {"kind": "application", "label": "Firefox"},
            {"kind": "application", "label": "Discord Voice"},
            {"kind": "application", "label": "Spotify"},
            {"kind": "application", "label": "OBS Studio"},
        ]
        reference = render_mixer_display(
            channels,
            [[], [], [], []],
            [False, True, False, False],
            [0.74, 0.42, 0.87, 0.63],
            preview_levels=[0.74, 0.42, 0.87, 0.63],
        )
        print(
            "GENERATED MIXER UI v40: pixel-only established redraw",
            flush=True,
        )
        print(
            "The display should show four complete labeled application channels for 15 seconds.",
            flush=True,
        )
        display.submit(reference)
        hold_display_test(display, 15.0)
        print(
            "Generated mixer-UI test completed. The Hercules splash after this is expected.",
            flush=True,
        )
        return 0
    finally:
        display.close()


def run_display_object_test(helper: Path, replay: Path) -> int:
    display = DisplayController(helper, replay, ("--object-test",))
    try:
        channels = [
            {"kind": "application", "label": "Channel One"},
            {"kind": "application", "label": "Channel Two"},
            {"kind": "application", "label": "Channel Three"},
            {"kind": "application", "label": "Channel Four"},
        ]
        reference = render_mixer_display(
            channels,
            [[], [], [], []],
            [False, False, False, False],
            [0.5, 0.5, 0.5, 0.5],
            preview_levels=[0.5, 0.5, 0.5, 0.5],
        )
        print(
            "NATIVE COMPOSITOR MAP v41: 0x35 objects plus 0x41 levels",
            flush=True,
        )
        print(
            "The screen starts with four 50% channels. After about one second, "
            "watch for numbered badges and any native indicators moving to "
            "15%, 40%, 65%, and 90%.",
            flush=True,
        )
        display.submit(reference)
        hold_display_test(display, 15.0)
        print("Native compositor mapping test completed.", flush=True)
        return 0
    finally:
        display.close()


def run_display_fullscreen_test(helper: Path, replay: Path) -> int:
    display = DisplayController(helper, replay, ("--fullscreen-test",))
    try:
        channels = [
            {"kind": "application", "label": "MASK TEST ONE"},
            {"kind": "application", "label": "MASK TEST TWO"},
            {"kind": "application", "label": "MASK TEST THREE"},
            {"kind": "application", "label": "MASK TEST FOUR"},
        ]
        frame = render_mixer_display(
            channels,
            [[], [], [], []],
            [False, False, False, False],
            [0.5, 0.5, 0.5, 0.5],
            preview_levels=[0.5, 0.5, 0.5, 0.5],
            native_overlay=True,
            fullscreen_footer=True,
        )
        print("FULLSCREEN ACTION-ZONE MAP v45", flush=True)
        print(
            "The footer underneath the native action area is bright and labeled. "
            "Six phases test the SDK's action-zone styles 0 through 5 after "
            "clearing the now-mapped panel and meter indicators. Channel 1 "
            "shows the phase number.",
            flush=True,
        )
        display.submit(frame)
        hold_display_test(display, 22.0)
        print("Fullscreen action-zone mapping test completed.", flush=True)
        return 0
    finally:
        display.close()


def run_display_action_color_test(helper: Path, replay: Path) -> int:
    display = DisplayController(helper, replay, ("--action-color-test",))
    try:
        channels = [
            {"kind": "application", "label": f"Channel {index + 1}"}
            for index in range(4)
        ]
        frame = render_notepad_display(
            channels,
            [[], [], [], []],
            [False, False, False, False],
            [0.5, 0.5, 0.5, 0.5],
            "Separator colour test\n"
            "Watch the three lower divider lines.\n"
            "Channel 1 shows phases 0 through 5.",
        )
        print("NOTEPAD ACTION-ZONE COLOUR MAP", flush=True)
        print(
            "Six phases test the SDK colour encodings against the Notepad "
            "card. Channel 1 shows the phase number; note any phase where "
            "all three lower divider lines disappear.",
            flush=True,
        )
        display.submit(frame)
        hold_display_test(display, 22.0)
        print("Notepad action-zone colour test completed.", flush=True)
        return 0
    finally:
        display.close()


def run_display_solid_test(helper: Path, replay: Path) -> int:
    display = DisplayController(helper, replay)
    try:
        frame = bytes.fromhex("00 f8") * 256 + bytes(DISPLAY_FRAMEBUFFER_BYTES)
        display.submit(frame)
        print("Sending a red palette-flood test screen...")
        print("The generated background should turn red; native overlays may remain.")
        print("Holding the test for 20 seconds.")
        hold_display_test(display, 20.0)
        return 0
    finally:
        display.close()


def channel_levels(
    targets: list[list[str]], muted: list[bool], saved_levels: list[float]
) -> list[float]:
    levels: list[float] = []
    for index, channel_targets in enumerate(targets):
        if muted[index]:
            levels.append(saved_levels[index])
            continue
        values = [
            level
            for target in channel_targets
            if (level := read_volume(target)) is not None
        ]
        levels.append(max(values) if values else saved_levels[index])
    return levels


def native_display_metadata(
    channels: list[dict[str, str]],
    targets: list[list[str]],
    muted: list[bool],
    saved_levels: list[float],
    preview_levels: list[float] | None = None,
    level_override: list[float] | None = None,
    display_mode: str = "mixer",
    button_leds: list[int] | None = None,
    page_index: int = 0,
    page_count: int = 1,
    meter_style: str = DEFAULT_METER_STYLE,
    show_volume_meters: bool = False,
    volume_meter_mode: str = "volume",
    meter_levels: list[float | StereoLevel] | None = None,
    display_brightness: int = DEFAULT_DISPLAY_BRIGHTNESS,
) -> bytes:
    if level_override is not None and len(level_override) != 4:
        raise RuntimeError("native display override requires four levels")
    levels = (
        list(level_override)
        if level_override is not None
        else (
            list(preview_levels)
            if preview_levels is not None
            else channel_levels(targets, muted, saved_levels)
        )
    )
    channel_colors = [
        channel_color(channel, index) for index, channel in enumerate(channels)
    ]
    metadata = bytearray(32)
    metadata[:4] = b"S1C3"
    metadata[4:8] = bytes(
        max(0, min(100, round(level * 100))) for level in levels
    )
    metadata[8] = sum(1 << index for index, value in enumerate(muted) if value)
    online_mask = sum(
        1 << index
        for index in range(4)
        if preview_levels is not None or bool(targets[index])
    )
    if not MIN_DISPLAY_BRIGHTNESS <= display_brightness <= MAX_DISPLAY_BRIGHTNESS:
        raise RuntimeError("display brightness must be between 10 and 100 percent")
    # Preserve the established 32-byte metadata block by placing brightness+1
    # in the otherwise unused high nibbles of the online-mask and page-count
    # bytes. An encoded zero therefore means an older frame and defaults to
    # 100%, while all current frames carry the exact 10..100 setting.
    encoded_brightness = display_brightness + 1
    metadata[9] = online_mask | ((encoded_brightness & 0x0F) << 4)
    metadata[10] = {
        "mixer": 1,
        "image": 2,
        "notepad": 5,
        "startup": 3,
        "startup-primer": 4,
    }.get(display_mode, 1)
    metadata[12:24] = bytes(
        component for color in channel_colors for component in color
    )
    led_states = [0, 0, 0, 0] if button_leds is None else list(button_leds)
    if len(led_states) != 4 or not all(state in (0, 1, 2) for state in led_states):
        raise RuntimeError("programmable button LEDs require four valid states")
    if meter_style not in METER_STYLES:
        raise RuntimeError("unsupported visualiser style")
    # Keep Classic frames byte-compatible with v0.15.1. Styles 2-4 use the
    # previously empty high nibble so the helper can select the corresponding
    # firmware-owned 0x32 meter geometry; the page remains in the low nibble.
    encoded_meter_style = METER_STYLES.index(meter_style)
    metadata[28] = max(0, min(7, page_index)) | (encoded_meter_style << 4)
    metadata[29] = (
        max(1, min(MAX_MIXER_PAGES, page_count))
        | (encoded_brightness & 0xF0)
    )
    if volume_meter_mode not in VOLUME_METER_MODES:
        raise RuntimeError("unsupported volume meter mode")
    displayed_meter_levels: list[float | StereoLevel] = (
        list(levels) if meter_levels is None else list(meter_levels)
    )
    if len(displayed_meter_levels) != 4:
        raise RuntimeError("native meters require four levels")
    stereo_levels: list[StereoLevel] = []
    for level in displayed_meter_levels:
        if isinstance(level, (tuple, list)):
            if len(level) != 2:
                raise RuntimeError(
                    "each native stereo meter requires left and right levels"
                )
            stereo_levels.append((float(level[0]), float(level[1])))
        else:
            mono_level = float(level)
            stereo_levels.append((mono_level, mono_level))
    packed_left = [
        max(0, min(15, round(level[0] * 15))) for level in stereo_levels
    ]
    packed_right = [
        max(0, min(15, round(level[1] * 15))) for level in stereo_levels
    ]
    # S1C3 preserves the 32-byte transport: left levels retain the S1C2 meter
    # nibbles, while right levels use the previously unused high nibble of
    # each two-bit programmable-button LED state.
    metadata[11] = packed_left[0] | (packed_left[1] << 4)
    metadata[31] = packed_left[2] | (packed_left[3] << 4)
    metadata[24:28] = bytes(
        led_states[index] | (packed_right[index] << 4) for index in range(4)
    )
    # Meter mode 2 means the native 0x41 volume marker and 0x40 activity bars
    # are both active.  Older mode 1 frames remain readable by the helper, but
    # new OpenStream100 frames no longer make the user choose between them.
    metadata[30] = 2 if show_volume_meters else 0
    return bytes(metadata)


def update_native_display_metadata(
    base_frame: bytes,
    channels: list[dict[str, str]],
    targets: list[list[str]],
    muted: list[bool],
    saved_levels: list[float],
    level_override: list[float] | None = None,
    display_mode: str = "mixer",
    button_leds: list[int] | None = None,
    page_index: int = 0,
    page_count: int = 1,
    meter_style: str = DEFAULT_METER_STYLE,
    show_volume_meters: bool = False,
    volume_meter_mode: str = "volume",
    meter_levels: list[float | StereoLevel] | None = None,
    display_brightness: int = DEFAULT_DISPLAY_BRIGHTNESS,
) -> bytes:
    if len(base_frame) != DISPLAY_MESSAGE_BYTES:
        raise RuntimeError("cached display frame has an invalid size")
    result = bytearray(base_frame)
    result[DISPLAY_PALETTE_BYTES - 32 : DISPLAY_PALETTE_BYTES] = (
        native_display_metadata(
            channels,
            targets,
            muted,
            saved_levels,
            level_override=level_override,
            display_mode=display_mode,
            button_leds=button_leds,
            page_index=page_index,
            page_count=page_count,
            meter_style=meter_style,
            show_volume_meters=show_volume_meters,
            volume_meter_mode=volume_meter_mode,
            meter_levels=meter_levels,
            display_brightness=display_brightness,
        )
    )
    return bytes(result)


def render_mixer_display(
    channels: list[dict[str, str]],
    targets: list[list[str]],
    muted: list[bool],
    saved_levels: list[float],
    preview_levels: list[float] | None = None,
    native_overlay: bool = False,
    fullscreen_footer: bool = False,
    background_image: Path | None = None,
    button_leds: list[int] | None = None,
    page_index: int = 0,
    page_count: int = 1,
    meter_style: str = DEFAULT_METER_STYLE,
    show_volume_meters: bool = False,
    volume_meter_mode: str = "volume",
    meter_levels: list[float | StereoLevel] | None = None,
    display_brightness: int = DEFAULT_DISPLAY_BRIGHTNESS,
    streams_by_ch: list[list[dict]] | None = None,
    button_actions: list[str] | None = None,
    button_volume_presets: list[dict[str, int]] | None = None,
) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for the LCD. On Fedora, install python3-pillow."
        ) from error

    if preview_levels is not None and len(preview_levels) != 4:
        raise RuntimeError("display preview requires four levels")
    levels = (
        list(preview_levels)
        if preview_levels is not None
        else channel_levels(targets, muted, saved_levels)
    )
    channel_colors = [channel_color(channel, index) for index, channel in enumerate(channels)]
    # Keep channel accents in the four original, hardware-validated palette
    # slots. Some controller states did not reliably display newly introduced
    # slots even though the 0x33 command carries a full 256-entry palette.
    frame_palette = list(UI_COLORS)
    frame_palette[4:8] = channel_colors
    has_background = background_image is not None
    palette_reference = None
    if has_background:
        image = load_display_background(background_image)
        panel_overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(panel_overlay)
        for index in range(4):
            left = index * 120
            right = left + 120
            panel_draw.rectangle(
                (left + 3, 3, right - 3, 271),
                fill=(*UI_COLORS[1], 148),
            )
        image = Image.alpha_composite(image.convert("RGBA"), panel_overlay).convert("RGB")
        # The background and panel treatment are common to every mixer page.
        # Derive the adaptive palette here, before page-specific labels are
        # drawn, so a label-only page switch does not require a palette swap.
        palette_reference = image.copy()
    else:
        image = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), UI_COLORS[0])
    draw = ImageDraw.Draw(image)
    label_font = ui_font(14, bold=True)
    level_font = ui_font(29, bold=True)
    status_font = ui_font(12, bold=True)
    number_font = ui_font(12, bold=True)

    for index, channel in enumerate(channels):
        left = index * 120
        right = left + 120
        online = preview_levels is not None or bool(targets[index])
        is_muted = muted[index]
        level = max(0.0, min(1.0, levels[index]))
        # The selected colour identifies the assignment and remains visible
        # while it is muted or waiting. State is shown separately by the
        # percentage object and the status label.
        accent = channel_colors[index]

        if not has_background:
            draw.rectangle((left + 3, 3, right - 3, 271), fill=UI_COLORS[1])
        draw.rectangle((left + 3, 3, right - 3, 8), fill=accent)
        draw.text(
            (left + 9, 12),
            str(page_index * 4 + index + 1),
            font=number_font,
            fill=UI_COLORS[3],
        )

        lines = fit_label(draw, channel.get("label", "Disabled"), label_font, 98)
        label_y = 43 if native_overlay else 12
        for line in lines:
            draw_centered(draw, line, label_y, label_font, UI_COLORS[2], left, right)
            label_y += 16

        if not native_overlay:
            draw_centered(
                draw,
                f"{round(level * 100):d}%" if online else "--",
                53,
                level_font,
                UI_COLORS[2] if online else UI_COLORS[3],
                left,
                right,
            )

        if not native_overlay:
            bar_left = left + 42
            bar_right = left + 78
            bar_top = 96
            bar_bottom = 222
            draw.rounded_rectangle(
                (bar_left, bar_top, bar_right, bar_bottom),
                radius=7,
                fill=UI_COLORS[13],
                outline=UI_COLORS[8],
                width=2,
            )
            fill_height = round((bar_bottom - bar_top - 6) * level) if online else 0
            if fill_height:
                draw.rounded_rectangle(
                    (
                        bar_left + 4,
                        bar_bottom - 3 - fill_height,
                        bar_right - 4,
                        bar_bottom - 3,
                    ),
                    radius=4,
                    fill=accent,
                )
            for tick in range(1, 4):
                tick_y = bar_top + tick * (bar_bottom - bar_top) // 4
                draw.line(
                    (bar_left - 7, tick_y, bar_left - 2, tick_y),
                    fill=UI_COLORS[14],
                )
                draw.line(
                    (bar_right + 2, tick_y, bar_right + 7, tick_y),
                    fill=UI_COLORS[14],
                )

        # Status text removed — button labels overlay provides visual feedback instead


    if streams_by_ch and _load_show_channel_icons():
        try:
            _draw_channel_icons_on_mixer(image, draw, channels, streams_by_ch)
        except Exception as err:
            # Icon resolution is non-critical; fall through gracefully.
            pass

    # Draw button labels overlay at the bottom of the display
    overlay = _load_button_labels_overlay()
    if overlay:
        overlay_y = DISPLAY_HEIGHT - overlay.size[1]
        # Use alpha channel as mask to avoid blending artifacts
        overlay_rgb = overlay.convert("RGB")
        overlay_alpha = overlay.split()[3]
        image.paste(overlay_rgb, (0, overlay_y), overlay_alpha)
        
        # Draw button action labels on each box
        if button_actions:
            # Box layout: 4 boxes, each 120px wide, overlay is 80px tall at y=192
            # Number in top-left of each box (offset to clear border), action label centered
            button_label_font = label_font  # Use existing label font
            # Smaller font for button numbers
            num_font = ui_font(11, bold=True)
            for i, action in enumerate(button_actions):
                box_left = i * 120
                box_right = box_left + 120
                box_top = overlay_y
                box_bottom = overlay_y + 80
                box_center_y = overlay_y + 40  # Center of 80px box
                
                # Draw button number in top-left corner (offset from border)
                num_text = str(i + 1)
                draw.text((box_left + 10, box_top + 18), num_text, font=num_font, fill=(255, 255, 255))
                
                # Draw action label centered in box
                if action == "set_channel_volume" and button_volume_presets and i < len(button_volume_presets):
                    pct = button_volume_presets[i].get("percentage", 50)
                    # Split into two lines: "Set Volume" and "X%"
                    font_metrics = button_label_font.getmetrics()
                    font_height = font_metrics[0]
                    line1_y = box_center_y - (font_height // 2) - 6
                    line2_y = box_center_y - (font_height // 2) + font_height + 2
                    draw_centered(draw, "Set Volume", line1_y, button_label_font, (255, 255, 255), box_left, box_right)
                    draw_centered(draw, f"{pct}%", line2_y, button_label_font, (255, 255, 255), box_left, box_right)
                else:
                    label = BUTTON_ACTION_LABELS.get(action, action.replace("_", " ").title())
                    # Get font height to center properly
                    font_metrics = button_label_font.getmetrics()
                    font_height = font_metrics[0]
                    label_y = box_center_y - (font_height // 2)
                    draw_centered(draw, label, label_y, button_label_font, (255, 255, 255), box_left, box_right)

    if fullscreen_footer:
        footer_colors = [UI_COLORS[4], UI_COLORS[5], UI_COLORS[6], UI_COLORS[7]]
        for index, color in enumerate(footer_colors):
            left = index * 120
            right = left + 120
            draw.rectangle((left, 202, right - 1, 271), fill=color)
            draw_centered(
                draw,
                f"FULL {index + 1}",
                226,
                label_font,
                UI_COLORS[15],
                left,
                right,
            )

    # All selectable geometries are now native 0x32 styles. Never stamp the
    # rejected static masks into the framebuffer or reserve their palette.
    custom_meter_active = False
    if has_background:
        row_major, palette_colors = rich_palette_indices(
            image,
            frame_palette,
            palette_reference=palette_reference,
            dynamic_color_count=(
                CUSTOM_METER_DYNAMIC_COLORS if custom_meter_active else 224
            ),
        )
    else:
        row_major = nearest_palette_indices(image, frame_palette)
        palette_colors = frame_palette
    if custom_meter_active:
        row_major = bytearray(row_major)
        draw_custom_meter_indices(row_major, meter_style)
        palette_colors = [
            *palette_colors,
            *([(0, 0, 0)] * (CUSTOM_METER_PALETTE_BASE - len(palette_colors))),
            *custom_meter_static_palette(channel_colors, meter_style),
        ]
    palette = bytearray(palette_rgb565(palette_colors))
    if native_overlay:
        metadata = native_display_metadata(
            channels,
            targets,
            muted,
            saved_levels,
            preview_levels,
            button_leds=button_leds,
            page_index=page_index,
            page_count=page_count,
            meter_style=meter_style,
            show_volume_meters=show_volume_meters,
            volume_meter_mode=volume_meter_mode,
            meter_levels=meter_levels,
            display_brightness=display_brightness,
        )
        palette[-len(metadata):] = metadata
    return bytes(palette) + pack_device_framebuffer(row_major)


def render_fullscreen_image_display(
    channels: list[dict[str, str]],
    targets: list[list[str]],
    muted: list[bool],
    saved_levels: list[float],
    image_path: Path | None,
    button_leds: list[int] | None = None,
    page_index: int = 0,
    page_count: int = 1,
    display_brightness: int = DEFAULT_DISPLAY_BRIGHTNESS,
) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageOps
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for the LCD. On Fedora, install python3-pillow."
        ) from error

    if image_path is None:
        image = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), UI_COLORS[0])
        draw = ImageDraw.Draw(image)
        title_font = ui_font(28, bold=True)
        detail_font = ui_font(14, bold=False)
        draw_centered(
            draw, "OPENSTREAM100", 93, title_font, UI_COLORS[2], 0, 480
        )
        draw_centered(
            draw, "Choose a full-screen image in the control panel",
            145, detail_font, UI_COLORS[3], 0, 480
        )
    else:
        try:
            with Image.open(image_path) as opened:
                image = flatten_display_image(opened)
                image = ImageOps.fit(
                    image,
                    (DISPLAY_WIDTH, DISPLAY_HEIGHT),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.5),
                )
        except (OSError, ValueError) as error:
            raise RuntimeError(f"could not prepare full-screen image: {error}") from error

    channel_colors = [
        channel_color(channel, index) for index, channel in enumerate(channels)
    ]
    fixed_palette = list(UI_COLORS)
    fixed_palette[4:8] = channel_colors
    row_major, palette_colors = rich_palette_indices(image, fixed_palette)
    palette = bytearray(palette_rgb565(palette_colors))
    metadata = native_display_metadata(
        channels,
        targets,
        muted,
        saved_levels,
        display_mode="image",
        button_leds=button_leds,
        page_index=page_index,
        page_count=page_count,
        display_brightness=display_brightness,
    )
    palette[-len(metadata):] = metadata
    return bytes(palette) + pack_device_framebuffer(row_major)


def render_notepad_display(
    channels: list[dict[str, str]],
    targets: list[list[str]],
    muted: list[bool],
    saved_levels: list[float],
    note_text: str,
    notepad_style: dict[str, object] | None = None,
    button_leds: list[int] | None = None,
    page_index: int = 0,
    page_count: int = 1,
    display_brightness: int = DEFAULT_DISPLAY_BRIGHTNESS,
) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for the LCD. On Fedora, install python3-pillow."
        ) from error

    active_style = normalise_notepad_style(notepad_style)
    body_color = notepad_text_color(active_style)
    image = Image.new("RGB", (DISPLAY_WIDTH, DISPLAY_HEIGHT), UI_COLORS[0])
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (9, 9, DISPLAY_WIDTH - 10, DISPLAY_HEIGHT - 10),
        radius=13,
        fill=UI_COLORS[1],
        outline=UI_COLORS[8],
        width=2,
    )
    draw.rounded_rectangle(
        (10, 10, DISPLAY_WIDTH - 11, 47),
        radius=12,
        fill=UI_COLORS[10],
    )
    draw.rectangle((10, 35, DISPLAY_WIDTH - 11, 47), fill=UI_COLORS[10])
    draw.rectangle((10, 46, DISPLAY_WIDTH - 11, 48), fill=UI_COLORS[4])
    draw.text((23, 16), "NOTES", font=ui_font(18, bold=True), fill=UI_COLORS[2])

    normalized = note_text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized:
        body_left = 23
        body_top = 59
        body_width = DISPLAY_WIDTH - body_left - 23
        body_height = DISPLAY_HEIGHT - body_top - 17
        font, lines, line_height, _truncated = fit_note_text(
            draw,
            normalized,
            body_width,
            body_height,
            int(active_style["font_size"]),
            str(active_style["font_family"]),
            str(active_style["font_style"]),
        )
        y = body_top
        for line in lines:
            x = note_line_x(
                draw,
                line,
                font,
                body_left,
                body_width,
                str(active_style["alignment"]),
            )
            draw.text((x, y), line, font=font, fill=body_color)
            y += line_height
    else:
        prompt_font = ui_font(17)
        draw_centered(
            draw,
            "Type or paste a note in the control panel",
            126,
            prompt_font,
            UI_COLORS[3],
            16,
            DISPLAY_WIDTH - 16,
        )

    channel_colors = [
        channel_color(channel, index) for index, channel in enumerate(channels)
    ]
    fixed_palette = list(UI_COLORS)
    fixed_palette[4:8] = channel_colors
    fixed_palette[15] = body_color
    row_major, palette_colors = rich_palette_indices(image, fixed_palette)
    palette = bytearray(palette_rgb565(palette_colors))
    metadata = native_display_metadata(
        channels,
        targets,
        muted,
        saved_levels,
        display_mode="notepad",
        button_leds=button_leds,
        page_index=page_index,
        page_count=page_count,
        display_brightness=display_brightness,
    )
    palette[-len(metadata):] = metadata
    return bytes(palette) + pack_device_framebuffer(row_major)


def render_startup_display(
    final_frame: bytes, logo_path: Path = DEFAULT_STARTUP_LOGO
) -> bytes:
    """Build the supplied branded startup frame with a dedicated rich palette."""
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for the LCD. On Fedora, install python3-pillow."
        ) from error

    if len(final_frame) != DISPLAY_MESSAGE_BYTES:
        raise RuntimeError("final display frame has an invalid size")

    final_metadata = final_frame[DISPLAY_PALETTE_BYTES - 32 : DISPLAY_PALETTE_BYTES]
    metadata_offset = DISPLAY_PALETTE_BYTES - 32
    if final_metadata[:4] not in NATIVE_METADATA_MAGICS:
        raise RuntimeError("final display frame has no OpenStream100 metadata")

    try:
        with Image.open(logo_path) as opened:
            image = ImageOps.fit(
                flatten_display_image(opened),
                (DISPLAY_WIDTH, DISPLAY_HEIGHT),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
    except (OSError, ValueError) as error:
        raise RuntimeError(f"could not prepare the startup logo: {error}") from error

    row_major, palette_colors = rich_palette_indices(image, UI_COLORS)
    palette = bytearray(palette_rgb565(palette_colors))
    palette[metadata_offset:] = final_metadata
    palette[metadata_offset + 10] = 3
    palette[metadata_offset + 24 : metadata_offset + 28] = bytes(4)
    return bytes(palette) + pack_device_framebuffer(row_major)


def render_startup_primer(
    final_frame: bytes, resident_frame: bytes | None = None
) -> bytes:
    """Build a first-pass frame that visually matches the resident mixer."""
    if len(final_frame) != DISPLAY_MESSAGE_BYTES:
        raise RuntimeError("final display frame has an invalid size")
    metadata_offset = DISPLAY_PALETTE_BYTES - 32
    if (
        final_frame[metadata_offset : metadata_offset + 4]
        not in NATIVE_METADATA_MAGICS
    ):
        raise RuntimeError("final display frame has no OpenStream100 metadata")
    if resident_frame is None:
        resident_frame = final_frame
    if len(resident_frame) != DISPLAY_MESSAGE_BYTES:
        raise RuntimeError("resident display frame has an invalid size")
    if (
        resident_frame[metadata_offset : metadata_offset + 4]
        not in NATIVE_METADATA_MAGICS
    ):
        raise RuntimeError("resident display frame has no OpenStream100 metadata")

    # The controller cannot atomically hide or compose its first framebuffer:
    # brightness zero is commit-deferred until all 32 planes have arrived.
    # Redrawing the current saved screen instead of black makes that unavoidable
    # pass match the image already resident on a warm-started controller. Only
    # the reserved, non-visible metadata mode changes so the helper can still
    # perform the proven hidden primer -> logo -> saved-screen transitions.
    primer = bytearray(resident_frame)
    primer[metadata_offset:DISPLAY_PALETTE_BYTES] = final_frame[
        metadata_offset:DISPLAY_PALETTE_BYTES
    ]
    primer[metadata_offset + 10] = 4
    return bytes(primer)


def load_resident_display_frame(
    cache_path: Path, fallback_frame: bytes
) -> bytes:
    """Return the last static framebuffer left on the controller, if valid."""
    metadata_offset = DISPLAY_PALETTE_BYTES - 32
    try:
        cached = cache_path.read_bytes()
    except OSError:
        return fallback_frame
    if (
        len(cached) != DISPLAY_MESSAGE_BYTES
        or cached[metadata_offset : metadata_offset + 4]
        not in NATIVE_METADATA_MAGICS
        or cached[metadata_offset + 10] not in (1, 2, 3, 5)
    ):
        return fallback_frame
    return cached


def save_resident_display_frame(cache_path: Path, frame: bytes) -> None:
    """Atomically remember the static image most recently left on the LCD."""
    metadata_offset = DISPLAY_PALETTE_BYTES - 32
    if (
        len(frame) != DISPLAY_MESSAGE_BYTES
        or frame[metadata_offset : metadata_offset + 4]
        not in NATIVE_METADATA_MAGICS
        or frame[metadata_offset + 10] not in (1, 2, 3, 5)
    ):
        raise RuntimeError("cannot cache an invalid resident display frame")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_bytes(frame)
    os.replace(temporary, cache_path)


def discover_streams() -> list[dict[str, Any]]:
    result = command(["pw-dump"])
    if result.returncode != 0:
        message = result.stderr.strip() or "pw-dump could not read the PipeWire graph"
        raise RuntimeError(message)

    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"pw-dump returned invalid JSON: {error}") from error

    streams: list[dict[str, Any]] = []
    for item in objects:
        if not str(item.get("type", "")).endswith(":Node"):
            continue
        props = item.get("info", {}).get("props", {})
        if props.get("media.class") != "Stream/Output/Audio":
            continue

        match_property = "application.name"
        match_value = props.get(match_property)
        if not match_value:
            match_property = "application.process.binary"
            match_value = props.get(match_property)
        if not match_value:
            match_property = "node.name"
            match_value = props.get(match_property)
        if not match_value:
            continue

        label = (
            props.get("application.name")
            or props.get("node.description")
            or props.get("media.name")
            or str(match_value)
        )
        streams.append(
            {
                "id": int(item["id"]),
                "label": str(label),
                "property": match_property,
                "value": str(match_value),
                "props": props,
            }
        )
    return streams


def grouped_applications(streams: list[dict[str, Any]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str], dict[str, str]] = {}
    for stream in streams:
        key = (stream["property"], stream["value"])
        groups.setdefault(
            key,
            {
                "kind": "application",
                "label": stream["label"],
                "property": stream["property"],
                "value": stream["value"],
            },
        )
    return sorted(groups.values(), key=lambda item: item["label"].casefold())


def print_streams(streams: list[dict[str, Any]]) -> None:
    applications = grouped_applications(streams)
    if not applications:
        print("No active playback applications were found.")
        print("Start audio in an application and try again.")
        return
    print("Active PipeWire playback applications:")
    for index, application in enumerate(applications, 1):
        matching_ids = [
            str(stream["id"])
            for stream in streams
            if stream["property"] == application["property"]
            and stream["value"] == application["value"]
        ]
        print(f"  {index}. {application['label']} (nodes {', '.join(matching_ids)})")


def color_hex_to_rgb(value: object, index: int) -> tuple[int, int, int]:
    text = str(value).strip()
    if (
        len(text) == 7
        and text.startswith("#")
        and all(character in "0123456789abcdefABCDEF" for character in text[1:])
    ):
        return tuple(int(text[offset : offset + 2], 16) for offset in (1, 3, 5))
    return DEFAULT_CHANNEL_COLORS[index]


def channel_color(channel: dict[str, str], index: int) -> tuple[int, int, int]:
    return color_hex_to_rgb(channel.get("color"), index)


def normalise_channel_colors(channels: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, channel in enumerate(channels):
        item = dict(channel)
        red, green, blue = channel_color(item, index)
        item["color"] = f"#{red:02X}{green:02X}{blue:02X}"
        result.append(item)
    return result


def save_config(path: Path, channels: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    channels = normalise_channel_colors(channels)
    payload: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict):
                payload.update(existing)
        except json.JSONDecodeError:
            pass
    payload["version"] = 1
    payload["channels"] = channels
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def interactive_setup(path: Path, streams: list[dict[str, Any]]) -> list[dict[str, str]]:
    applications = grouped_applications(streams)
    choices: list[dict[str, str]] = [
        {"kind": "disabled", "label": "Disabled"},
        {"kind": "default", "label": "Default output device"},
        *applications,
    ]

    print("\nStream 100 channel setup")
    print("Applications must be playing audio to appear here.\n")
    for index, choice in enumerate(choices):
        print(f"  {index}. {choice['label']}")

    channels: list[dict[str, str]] = []
    for encoder in range(1, 5):
        default = 1 if encoder == 1 else 0
        while True:
            response = input(f"Encoder {encoder} selection [{default}]: ").strip()
            if not response:
                selection = default
            else:
                try:
                    selection = int(response)
                except ValueError:
                    print("Enter one of the numbers shown above.")
                    continue
            if 0 <= selection < len(choices):
                channels.append(dict(choices[selection]))
                break
            print("Enter one of the numbers shown above.")

    channels = normalise_channel_colors(channels)
    save_config(path, channels)
    print(f"\nSaved configuration to {path}")
    return channels


def load_config(path: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        raise RuntimeError(f"configuration does not exist: {path}")
    except json.JSONDecodeError as error:
        raise RuntimeError(f"configuration is invalid: {error}") from error

    if payload.get("version") != 1:
        raise RuntimeError("unsupported configuration version")
    channels = payload.get("channels")
    if not isinstance(channels, list) or len(channels) != 4:
        raise RuntimeError("configuration must contain exactly four channels")
    if not all(isinstance(channel, dict) for channel in channels):
        raise RuntimeError("configuration channels must be objects")
    return normalise_channel_colors(channels)


def load_background_path(path: Path) -> Path | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    value = payload.get("background_image") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    background = Path(value).expanduser()
    return background if background.is_file() else None


def load_fullscreen_image_path(path: Path) -> Path | None:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    value = payload.get("fullscreen_image") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value.strip():
        return None
    image = Path(value).expanduser()
    return image if image.is_file() else None


def load_display_mode(path: Path) -> str:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "mixer"
    value = payload.get("display_mode", "mixer") if isinstance(payload, dict) else "mixer"
    return value if value in {"mixer", "image", "notepad"} else "mixer"


def load_notepad_text(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""
    value = payload.get("notepad_text", "") if isinstance(payload, dict) else ""
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def load_notepad_style(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(DEFAULT_NOTEPAD_STYLE)
    value = payload.get("notepad_style") if isinstance(payload, dict) else None
    return normalise_notepad_style(value)


def load_show_volume_meters(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return True
    value = payload.get("show_volume_meters", True) if isinstance(payload, dict) else True
    return value if isinstance(value, bool) else True


def load_meter_channel_mode(path: Path) -> str:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return DEFAULT_METER_CHANNEL_MODE
    value = (
        payload.get("meter_channel_mode", DEFAULT_METER_CHANNEL_MODE)
        if isinstance(payload, dict)
        else DEFAULT_METER_CHANNEL_MODE
    )
    return value if value in METER_CHANNEL_MODES else DEFAULT_METER_CHANNEL_MODE


def load_meter_style(path: Path) -> str:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return DEFAULT_METER_STYLE
    value = (
        payload.get("meter_style", DEFAULT_METER_STYLE)
        if isinstance(payload, dict)
        else DEFAULT_METER_STYLE
    )
    return value if value in METER_STYLES else DEFAULT_METER_STYLE


def load_volume_meter_mode(path: Path) -> str:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return DEFAULT_VOLUME_METER_MODE
    # 0.11.3 separates the white volume marker from the paired activity bars,
    # so the old either/or preference is migrated to the combined live mode.
    return "activity"


def load_knob_sensitivity(path: Path) -> float:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return DEFAULT_KNOB_SENSITIVITY
    value = payload.get("knob_sensitivity", DEFAULT_KNOB_SENSITIVITY)
    try:
        sensitivity = float(value)
    except (TypeError, ValueError):
        return DEFAULT_KNOB_SENSITIVITY
    if not math.isfinite(sensitivity):
        return DEFAULT_KNOB_SENSITIVITY
    if not MIN_KNOB_SENSITIVITY <= sensitivity <= MAX_KNOB_SENSITIVITY:
        return DEFAULT_KNOB_SENSITIVITY
    return sensitivity


def load_display_brightness(path: Path) -> int:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return DEFAULT_DISPLAY_BRIGHTNESS
    value = payload.get("display_brightness", DEFAULT_DISPLAY_BRIGHTNESS)
    try:
        brightness = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DISPLAY_BRIGHTNESS
    if not math.isfinite(brightness):
        return DEFAULT_DISPLAY_BRIGHTNESS
    if not MIN_DISPLAY_BRIGHTNESS <= brightness <= MAX_DISPLAY_BRIGHTNESS:
        return DEFAULT_DISPLAY_BRIGHTNESS
    steps = round(brightness / DISPLAY_BRIGHTNESS_STEP)
    return int(steps * DISPLAY_BRIGHTNESS_STEP)


def counts_per_percent_for_sensitivity(sensitivity: float) -> float:
    return BASE_COUNTS_PER_PERCENT / sensitivity


def load_button_masks(path: Path) -> dict[int, int]:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_BUTTON_MASKS)

    raw_masks = payload.get("button_masks")
    if not isinstance(raw_masks, dict):
        return dict(DEFAULT_BUTTON_MASKS)
    try:
        masks = {int(key): int(value) for key, value in raw_masks.items()}
    except (TypeError, ValueError):
        return dict(DEFAULT_BUTTON_MASKS)

    valid = (
        set(masks) == {1, 2, 3, 4}
        and len(set(masks.values())) == 4
        and all(0 < value <= 0xFF and value & (value - 1) == 0 for value in masks.values())
    )
    return masks if valid else dict(DEFAULT_BUTTON_MASKS)


def load_button_actions(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return list(DEFAULT_BUTTON_ACTIONS)
    actions = payload.get("button_actions")
    if not isinstance(actions, list) or len(actions) != 4:
        return list(DEFAULT_BUTTON_ACTIONS)
    if not all(
        isinstance(action, str) and action in BUTTON_ACTION_IDS
        for action in actions
    ):
        return list(DEFAULT_BUTTON_ACTIONS)
    return list(actions)


def load_button_volume_presets(path: Path) -> list[dict[str, int]]:
    defaults = [dict(preset) for preset in DEFAULT_BUTTON_VOLUME_PRESETS]
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return defaults
    raw_presets = payload.get("button_volume_presets")
    if not isinstance(raw_presets, list) or len(raw_presets) != 4:
        return defaults
    presets: list[dict[str, int]] = []
    for item in raw_presets:
        if not isinstance(item, dict):
            return defaults
        channel = item.get("channel")
        percentage = item.get("percentage")
        if (
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or channel not in {1, 2, 3, 4}
            or not isinstance(percentage, int)
            or isinstance(percentage, bool)
            or not 0 <= percentage <= 100
        ):
            return defaults
        presets.append({"channel": channel, "percentage": percentage})
    return presets


def normalise_mixer_page(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    channels = value.get("channels")
    actions = value.get("button_actions")
    presets = value.get("button_volume_presets")
    if (
        not isinstance(channels, list)
        or len(channels) != 4
        or not all(isinstance(channel, dict) for channel in channels)
        or not isinstance(actions, list)
        or len(actions) != 4
        or not all(
            isinstance(action, str) and action in BUTTON_ACTION_IDS
            for action in actions
        )
        or not isinstance(presets, list)
        or len(presets) != 4
    ):
        return None
    normalised_presets: list[dict[str, int]] = []
    for preset in presets:
        if not isinstance(preset, dict):
            return None
        channel = preset.get("channel")
        percentage = preset.get("percentage")
        if (
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or channel not in {1, 2, 3, 4}
            or not isinstance(percentage, int)
            or isinstance(percentage, bool)
            or not 0 <= percentage <= 100
        ):
            return None
        normalised_presets.append(
            {"channel": channel, "percentage": percentage}
        )
    return {
        "channels": normalise_channel_colors(channels),
        "button_actions": list(actions),
        "button_volume_presets": normalised_presets,
    }


def load_mixer_pages(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    raw_pages = payload.get("pages") if isinstance(payload, dict) else None
    if isinstance(raw_pages, list) and 1 <= len(raw_pages) <= MAX_MIXER_PAGES:
        pages = [normalise_mixer_page(page) for page in raw_pages]
        if all(page is not None for page in pages):
            return [page for page in pages if page is not None]
    return [
        {
            "channels": load_config(path),
            "button_actions": load_button_actions(path),
            "button_volume_presets": load_button_volume_presets(path),
        }
    ]


def page_index_for_action(current: int, page_count: int, action: str) -> int:
    if page_count < 1:
        raise RuntimeError("mixer must contain at least one page")
    if action == "next_page":
        return (current + 1) % page_count
    if action == "previous_page":
        return (current - 1) % page_count
    return current


def button_led_states(actions: list[str]) -> list[int]:
    if len(actions) != 4:
        raise RuntimeError(
            "programmable button configuration must contain four actions"
        )
    return [0 if action == "disabled" else 1 for action in actions]


def pressed_programmable_buttons(previous_state: int, current_state: int) -> list[int]:
    return [
        button
        for button, mask in PROGRAMMABLE_BUTTON_MASKS.items()
        if current_state & mask and not previous_state & mask
    ]


def save_button_masks(path: Path, masks: dict[int, int]) -> None:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise RuntimeError("run --setup before calibrating buttons") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"configuration is invalid: {error}") from error

    payload["button_masks"] = {str(key): value for key, value in masks.items()}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def resolve_targets(
    channels: list[dict[str, str]], streams: list[dict[str, Any]]
) -> list[list[str]]:
    resolved: list[list[str]] = []
    for channel in channels:
        kind = channel.get("kind")
        if kind == "default":
            resolved.append([DEFAULT_TARGET])
        elif kind == "application":
            resolved.append(
                [
                    str(stream["id"])
                    for stream in streams
                    if stream["property"] == channel.get("property")
                    and stream["value"] == channel.get("value")
                ]
            )
        else:
            resolved.append([])
    return resolved


def pulse_sink_inputs() -> list[dict[str, Any]]:
    """Return PipeWire playback streams through its Pulse compatibility API."""
    result = command(["pactl", "-f", "json", "list", "sink-inputs"])
    if result.returncode != 0:
        message = result.stderr.strip() or "pactl could not list playback streams"
        raise RuntimeError(message)
    try:
        inputs = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"pactl returned invalid JSON: {error}") from error
    if not isinstance(inputs, list):
        raise RuntimeError("pactl returned an unexpected playback-stream list")
    return [item for item in inputs if isinstance(item, dict)]


def pulse_sink_monitors() -> dict[str, str]:
    """Map Pulse sink indices to their PipeWire monitor-source names."""
    result = command(["pactl", "-f", "json", "list", "sinks"])
    if result.returncode != 0:
        message = result.stderr.strip() or "pactl could not list output devices"
        raise RuntimeError(message)
    try:
        sinks = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"pactl returned invalid sink JSON: {error}") from error
    if not isinstance(sinks, list):
        raise RuntimeError("pactl returned an unexpected output-device list")
    monitors: dict[str, str] = {}
    for sink in sinks:
        if not isinstance(sink, dict):
            continue
        index = str(sink.get("index", ""))
        monitor = sink.get("monitor_source_name")
        if not monitor and sink.get("name"):
            monitor = f"{sink['name']}.monitor"
        if index and monitor:
            monitors[index] = str(monitor)
        if sink.get("name") and monitor:
            monitors[str(sink["name"])] = str(monitor)
    return monitors


def default_sink_monitor() -> str | None:
    result = command(["pactl", "get-default-sink"])
    if result.returncode != 0:
        return None
    sink = result.stdout.strip()
    return f"{sink}.monitor" if sink else None


def resolve_meter_targets(
    channels: list[dict[str, str]],
    targets: list[list[str]],
    streams: list[dict[str, Any]],
) -> list[list[MeterTarget]]:
    """Map assignments to Pulse sink-input monitors backed by PipeWire."""
    del streams  # volume-control node IDs and Pulse stream IDs are independent
    inputs = pulse_sink_inputs()
    sink_monitors = pulse_sink_monitors()
    default_monitor = None
    resolved: list[list[MeterTarget]] = []
    for channel, channel_targets in zip(channels, targets):
        label = str(channel.get("label", "Audio"))
        if channel.get("kind") == "default":
            if default_monitor is None:
                default_monitor = default_sink_monitor()
            resolved.append(
                [("device", "", default_monitor, label)]
                if default_monitor
                else []
            )
            continue
        if channel.get("kind") != "application" or not channel_targets:
            resolved.append([])
            continue
        match_property = channel.get("property")
        match_value = channel.get("value")
        meter_targets: list[MeterTarget] = []
        seen_indices: set[str] = set()
        for sink_input in inputs:
            properties = sink_input.get("properties", {})
            if not isinstance(properties, dict):
                continue
            if str(properties.get(match_property, "")) != str(match_value):
                continue
            index = str(sink_input.get("index", ""))
            monitor = sink_monitors.get(str(sink_input.get("sink", "")))
            if index and monitor and index not in seen_indices:
                meter_targets.append(("stream", index, monitor, label))
                seen_indices.add(index)
        resolved.append(meter_targets)
    return resolved


def wpctl(arguments: list[str]) -> bool:
    result = command(["wpctl", *arguments])
    if result.returncode == 0:
        return True
    message = result.stderr.strip() or result.stdout.strip() or "unknown wpctl error"
    print(f"wpctl failed: {message}", file=sys.stderr)
    return False


def run_programmable_button_action(button: int, action: str) -> bool:
    commands: dict[str, tuple[list[str], str]] = {
        "microphone_mute": (
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"],
            "microphone mute toggled",
        ),
        "speaker_mute": (
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
            "speaker mute toggled",
        ),
        "play_pause": (["playerctl", "play-pause"], "media play/pause"),
        "previous_track": (["playerctl", "previous"], "previous media track"),
        "next_track": (["playerctl", "next"], "next media track"),
    }
    if action == "disabled":
        return False
    selected = commands.get(action)
    if selected is None:
        print(
            f"Button {button}: unsupported saved action {action!r}",
            file=sys.stderr,
        )
        return False
    arguments, description = selected
    if shutil.which(arguments[0]) is None:
        print(
            f"Button {button}: {arguments[0]} is required for {description}",
            file=sys.stderr,
        )
        return False
    result = command(arguments)
    if result.returncode == 0:
        print(f"Button {button}: {description}")
        return True
    message = result.stderr.strip() or result.stdout.strip() or "command failed"
    print(f"Button {button}: {description} failed: {message}", file=sys.stderr)
    return False


def change_volume(targets: list[str], percentage: int) -> bool:
    if not targets:
        return False
    direction = "+" if percentage > 0 else "-"
    amount = f"{abs(percentage)}%{direction}"
    succeeded = False
    for target in targets:
        succeeded |= wpctl(["set-volume", target, amount, "--limit", "1.0"])
    return succeeded


def read_volume(target: str) -> float | None:
    result = command(["wpctl", "get-volume", target])
    if result.returncode != 0:
        return None
    match = re.search(r"\bVolume:\s*([0-9]+(?:\.[0-9]+)?)", result.stdout)
    return float(match.group(1)) if match else None


def set_absolute_volume(targets: list[str], level: float) -> bool:
    succeeded = False
    level = max(0.0, min(1.0, level))
    for target in targets:
        succeeded |= wpctl(
            ["set-volume", target, f"{level:.4f}", "--limit", "1.0"]
        )
    return succeeded


def apply_channel_volume_preset(
    button: int,
    preset: dict[str, int],
    targets: list[list[str]],
    muted: list[bool],
    saved_levels: list[float],
) -> bool:
    channel = preset["channel"] - 1
    percentage = preset["percentage"]
    channel_targets = targets[channel]
    if not channel_targets:
        print(
            f"Button {button}: Control {channel + 1} has no active audio target",
            file=sys.stderr,
        )
        return False
    level = percentage / 100.0
    if not set_absolute_volume(channel_targets, level):
        return False
    muted[channel] = False
    saved_levels[channel] = level
    print(f"Button {button}: Control {channel + 1} set to {percentage}%")
    return True


def soft_toggle_mute(
    targets: list[str], muted: bool, saved_level: float
) -> tuple[bool, float, bool]:
    """Mute through the volume path, which is verified on this hardware setup."""
    if not targets:
        return muted, saved_level, False

    if muted:
        succeeded = set_absolute_volume(targets, saved_level)
        return (False if succeeded else True), saved_level, succeeded

    levels = [level for target in targets if (level := read_volume(target)) is not None]
    if levels:
        # Matching application nodes normally share a volume. The highest level
        # is the least surprising restore value if they happen to differ.
        saved_level = max(levels)
    succeeded = set_absolute_volume(targets, 0.0)
    return (True if succeeded else False), saved_level, succeeded


def signed_position(packet: bytes, encoder: int) -> int:
    offset = 3 + (encoder - 1) * 2
    return int.from_bytes(packet[offset : offset + 2], "little", signed=True)


def wrapped_delta(before: int, after: int) -> int:
    return (after - before + 32768) % 65536 - 32768


def active_configuration(device: usb.core.Device):
    try:
        return device.get_active_configuration()
    except usb.core.USBError:
        device.set_configuration(1)
        return device.get_active_configuration()


def find_input_endpoint(device: usb.core.Device):
    configuration = active_configuration(device)
    interface = usb.util.find_descriptor(
        configuration, bInterfaceNumber=INTERFACE, bAlternateSetting=0
    )
    if interface is None:
        return None
    return usb.util.find_descriptor(
        interface,
        custom_match=lambda candidate: (
            candidate.bEndpointAddress == INPUT_ENDPOINT
            and usb.util.endpoint_type(candidate.bmAttributes)
            == usb.util.ENDPOINT_TYPE_INTR
        ),
    )


def calibrate_buttons(config_path: Path) -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("Run calibration as your normal desktop user, not with sudo.", file=sys.stderr)
        return 2

    device = usb.core.find(idVendor=VID, idProduct=PID)
    if device is None:
        print("Stream 100 (06f8:e053) was not found.", file=sys.stderr)
        return 1

    claimed = False
    try:
        endpoint = find_input_endpoint(device)
        if endpoint is None:
            raise RuntimeError("Stream 100 input endpoint 0x81 was not found")
        usb.util.claim_interface(device, INTERFACE)
        claimed = True

        print("Stream 100 encoder-button calibration")
        print("Release all buttons. Each requested encoder should be pressed once.\n")

        latest: bytes | None = None
        settle_ends = time.monotonic() + 0.5
        while time.monotonic() < settle_ends or latest is None or latest[1] != 0:
            try:
                latest = bytes(endpoint.read(PACKET_SIZE, timeout=250))
            except usb.core.USBTimeoutError:
                continue

        learned: dict[int, int] = {}
        for encoder in range(1, 5):
            while True:
                print(f"Press encoder {encoder} now...")
                deadline = time.monotonic() + 20.0
                previous_buttons = latest[1]
                detected: int | None = None

                while time.monotonic() < deadline:
                    try:
                        latest = bytes(endpoint.read(PACKET_SIZE, timeout=250))
                    except usb.core.USBTimeoutError:
                        continue
                    rising = latest[1] & (~previous_buttons & 0xFF)
                    previous_buttons = latest[1]
                    if rising and rising & (rising - 1) == 0:
                        detected = rising
                        break

                if detected is None:
                    raise RuntimeError(f"timed out waiting for encoder {encoder}")

                while latest[1] & detected:
                    latest = bytes(endpoint.read(PACKET_SIZE, timeout=1000))

                if detected in learned.values():
                    print(
                        f"Bit 0x{detected:02x} was already assigned; please press "
                        f"encoder {encoder} again."
                    )
                    continue

                learned[encoder] = detected
                print(f"  Encoder {encoder} = button bit 0x{detected:02x}\n")
                break

        save_button_masks(config_path, learned)
        print(f"Saved button mapping to {config_path}")
        return 0

    except usb.core.USBError as error:
        if getattr(error, "errno", None) in (1, 13):
            raise RuntimeError("USB access was denied; reinstall the udev rule") from error
        raise RuntimeError(f"USB error: {error}") from error
    finally:
        if claimed:
            try:
                usb.util.release_interface(device, INTERFACE)
            except usb.core.USBError:
                pass
        usb.util.dispose_resources(device)


def parse_inverted(value: str) -> set[int]:
    if not value.strip():
        return set()
    try:
        inverted = {int(part.strip()) for part in value.split(",")}
    except ValueError as error:
        raise RuntimeError("--invert must contain encoder numbers such as 2,4") from error
    if not inverted <= {1, 2, 3, 4}:
        raise RuntimeError("--invert accepts only encoder numbers 1 through 4")
    return inverted


def run_mixer(
    pages: list[dict[str, Any]],
    counts_per_percent: float,
    config_path: Path,
    display_brightness: int,
    inverted: set[int],
    button_masks: dict[int, int],
    display_helper: Path | None,
    display_replay: Path | None,
    background_image: Path | None,
    display_mode: str,
    fullscreen_image: Path | None,
    notepad_text: str,
    notepad_style: dict[str, object],
    show_volume_meters: bool,
    meter_channel_mode: str,
    meter_style: str,
    volume_meter_mode: str,
    display_cache: Path | None,
    display_socket: Path | None,
    require_display_broker: bool,
) -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print(
            "Run the mixer as your normal desktop user, not with sudo. Install the "
            "included udev rule first.",
            file=sys.stderr,
        )
        return 2

    device = usb.core.find(idVendor=VID, idProduct=PID)
    if device is None:
        print("Stream 100 (06f8:e053) was not found.", file=sys.stderr)
        return 1

    if not 1 <= len(pages) <= MAX_MIXER_PAGES:
        raise RuntimeError(f"mixer must contain 1 to {MAX_MIXER_PAGES} pages")
    current_page = 0
    channels = pages[0]["channels"]
    button_actions = pages[0]["button_actions"]
    button_volume_presets = pages[0]["button_volume_presets"]
    muted_pages = [[False, False, False, False] for _page in pages]
    saved_level_pages = [[0.5, 0.5, 0.5, 0.5] for _page in pages]
    muted = muted_pages[0]
    saved_levels = saved_level_pages[0]
    claimed = False
    targets: list[list[str]] = [[], [], [], []]
    display: DisplayController | None = None
    display_base_frame: bytes | None = None
    last_display_base_frame: bytes | None = None
    programmable_leds = button_led_states(button_actions)
    level_monitor: PipeWireLevelMonitor | None = None
    try:
        endpoint = find_input_endpoint(device)
        if endpoint is None:
            print("Stream 100 input endpoint 0x81 was not found.", file=sys.stderr)
            return 1

        usb.util.claim_interface(device, INTERFACE)
        claimed = True

        print("OpenStream100 four-channel mixer")
        print(f"  Mixer pages: {len(pages)}")
        for encoder, channel in enumerate(channels, 1):
            suffix = " (inverted)" if encoder in inverted else ""
            print(f"  Encoder {encoder}: {channel.get('label', 'Disabled')}{suffix}")
        display_label = {
            "image": "full-screen image",
            "notepad": "notepad",
        }.get(display_mode, "mixer")
        print(f"  Display: {display_label}")
        print("\nCalibrating for half a second...")

        previous: bytes | None = None
        calibration_ends = time.monotonic() + 0.5
        while time.monotonic() < calibration_ends:
            try:
                previous = bytes(endpoint.read(PACKET_SIZE, timeout=250))
            except usb.core.USBTimeoutError:
                continue
        if previous is None:
            print("The controller returned no input reports.", file=sys.stderr)
            return 1

        streams = discover_streams()
        targets = resolve_targets(channels, streams)
        display_volume_levels = channel_levels(targets, muted, saved_levels)
        volume_levels_dirty = False
        display_meter_levels: list[StereoLevel] = [
            (level, level) for level in display_volume_levels
        ]
        last_meter_values: tuple[int, ...] | None = None
        next_meter_update = 0.0
        next_meter_log = 0.0
        next_brightness_refresh = 0.0
        if (
            display_mode == "mixer"
            and show_volume_meters
            and shutil.which("parec") is not None
            and shutil.which("pactl") is not None
        ):
            level_monitor = PipeWireLevelMonitor(meter_channel_mode)
            level_monitor.configure(resolve_meter_targets(channels, targets, streams))
            if meter_channel_mode == "mono":
                print(
                    "  Meters: white marker shows volume; one mono activity "
                    "level is mirrored across both bars"
                )
            else:
                print(
                    "  Meters: white marker shows volume; independent left/right "
                    "bars show live PipeWire activity"
                )
            print(f"  Visualiser style: {meter_style.title()}")
        elif display_mode == "mixer" and show_volume_meters:
            print(
                "  Meters: white marker shows volume; live activity is "
                "unavailable because parec or pactl was not found"
            )
        previous_targets: list[list[str]] | None = None
        next_refresh = 0.0
        display_dirty = True
        display_due = 0.0
        display_error_reported = False
        prev_cached_streams_by_ch: list[list[dict]] | None = None
        # Pre-seed with empty lists; will be replaced by resolved streams once
        # the display controller block runs so that the first render pass draws
        # icons immediately rather than waiting for the next timer tick.
        cached_streams_by_ch: list[list[dict]] = [[] for _ in channels]
        accumulators = [0, 0, 0, 0]

        if display_helper is not None and display_replay is not None:
            try:
                display = DisplayController(
                    display_helper,
                    display_replay,
                    display_socket=display_socket,
                    require_broker=require_display_broker,
                )
                # Resolve streams immediately so the first render has icon data.
                if display_helper is not None:
                    initial_streams_by_ch = _resolve_channel_streams(
                        channels, {"streams": streams}
                    )
                else:
                    initial_streams_by_ch = [[] for _ in channels]
                if display_mode == "image":
                    display_base_frame = render_fullscreen_image_display(
                        channels,
                        targets,
                        muted,
                        saved_levels,
                        fullscreen_image,
                        button_leds=programmable_leds,
                        page_index=current_page,
                        page_count=len(pages),
                        display_brightness=display_brightness,
                    )
                elif display_mode == "notepad":
                    display_base_frame = render_notepad_display(
                        channels,
                        targets,
                        muted,
                        saved_levels,
                        notepad_text,
                        notepad_style,
                        button_leds=programmable_leds,
                        page_index=current_page,
                        page_count=len(pages),
                        display_brightness=display_brightness,
                    )
                else:
                    display_base_frame = render_mixer_display(
                        channels,
                        targets,
                        muted,
                        saved_levels,
                        native_overlay=True,
                        background_image=background_image,
                        button_leds=programmable_leds,
                        page_index=current_page,
                        page_count=len(pages),
                        meter_style=meter_style,
                        show_volume_meters=show_volume_meters,
                        volume_meter_mode=volume_meter_mode,
                        meter_levels=display_meter_levels,
                        display_brightness=display_brightness,
                        streams_by_ch=initial_streams_by_ch,
                        button_actions=button_actions,
                        button_volume_presets=button_volume_presets,
                    )
                # Sync the cached icon state so the hot-loop detects a change on
                # the first tick and forces a full rebuild with icons.
                if display_helper is not None:
                    cached_streams_by_ch = initial_streams_by_ch
                    prev_cached_streams_by_ch = None
                    display_dirty = True
                    display_due = 0.0
                last_display_base_frame = display_base_frame
                resident_frame = (
                    load_resident_display_frame(display_cache, display_base_frame)
                    if display_cache is not None
                    else display_base_frame
                )
                if display.resident_display_mode is None:
                    display.submit_ordered(
                        render_startup_primer(display_base_frame, resident_frame)
                    )
                    display.submit_ordered(
                        render_startup_display(
                            display_base_frame, DEFAULT_STARTUP_LOGO
                        )
                    )
                    print("Cold display session initialized through the startup logo.")
                elif display.resident_display_mode == 3:
                    print(
                        "Persistent display broker retained the OpenStream100 "
                        "logo; cold initialization skipped."
                    )
                else:
                    display.submit_ordered(
                        render_startup_display(
                            display_base_frame, DEFAULT_STARTUP_LOGO
                        )
                    )
                    print(
                        "Persistent display session recovered through the "
                        "OpenStream100 logo."
                    )
                display.submit(display_base_frame)
                if display_cache is not None:
                    save_resident_display_frame(display_cache, display_base_frame)
                display_dirty = False
            except (OSError, RuntimeError) as error:
                print(f"Display disabled: {error}", file=sys.stderr)
                if display is not None:
                    display.close()
                    display = None

        print(
            "Ready. Turn encoders for volume; press them for mute; "
            "use numbered buttons for saved actions. Ctrl+C stops.\n"
        )

        while True:
            now = time.monotonic()
            if now >= next_brightness_refresh:
                refreshed_brightness = load_display_brightness(config_path)
                if refreshed_brightness != display_brightness:
                    display_brightness = refreshed_brightness
                    display_dirty = True
                    display_due = now
                    print(
                        f"Screen brightness updated to {display_brightness}%.",
                        flush=True,
                    )
                next_brightness_refresh = now + BRIGHTNESS_REFRESH_SECONDS
            if now >= next_refresh:
                try:
                    streams = discover_streams()
                    targets = resolve_targets(channels, streams)

                    # Resolve icons per channel for the hot-loop render.
                    if display_helper is not None:
                        cached_streams_by_ch = _resolve_channel_streams(
                            channels, {"streams": streams}
                        )
                        # Detect icon changes and notify via stderr callback.
                        icon_map, changed = _resolve_channel_icons_for_streams(
                            channels, cached_streams_by_ch
                        )
                        if changed:
                            _on_icon_cache_changed(changed)
                    else:
                        cached_streams_by_ch = [[] for _ in channels]

                    # Force a full rebuild when icon state changes (apps open/close).
                    if cached_streams_by_ch is not None and cached_streams_by_ch != prev_cached_streams_by_ch:
                        display_dirty = True
                        display_due = now
                        display_base_frame = None  # force full rebuild next cycle
                        prev_cached_streams_by_ch = cached_streams_by_ch
                    refreshed_volume_levels = channel_levels(
                        targets, muted, saved_levels
                    )
                    if [round(level * 100) for level in refreshed_volume_levels] != [
                        round(level * 100) for level in display_volume_levels
                    ]:
                        display_dirty = True
                        display_due = now
                    display_volume_levels = refreshed_volume_levels
                    volume_levels_dirty = False
                    if level_monitor is not None:
                        level_monitor.configure(
                            resolve_meter_targets(channels, targets, streams)
                        )
                    if targets != previous_targets:
                        for index, (channel, found) in enumerate(zip(channels, targets), 1):
                            if channel.get("kind") == "application" and not found:
                                print(f"Encoder {index}: waiting for {channel.get('label')}")
                            elif muted[index - 1] and found:
                                # A newly created stream for an application that
                                # is already soft-muted must start silent too.
                                set_absolute_volume(found, 0.0)
                        previous_targets = [list(value) for value in targets]
                        display_dirty = True
                        display_due = now
                except RuntimeError as error:
                    print(f"PipeWire discovery failed: {error}", file=sys.stderr)
                next_refresh = now + 1.0

            if level_monitor is not None and now >= next_meter_update:
                display_meter_levels = level_monitor.levels(display_volume_levels)
                meter_values = tuple(
                    max(0, min(15, round(level * 15)))
                    for stereo_level in display_meter_levels
                    for level in stereo_level
                )
                meter_values_changed = meter_values != last_meter_values
                if meter_values_changed:
                    last_meter_values = meter_values
                # The controller's native VU layer is a live object rather
                # than a persistent framebuffer element. Feed it at the
                # sampling cadence even when 4-bit transport quantisation
                # leaves the encoded value unchanged; otherwise steady audio
                # can appear only as isolated flashes.
                display_dirty = True
                display_due = min(display_due, now) if display_due else now
                if now >= next_meter_log:
                    if meter_channel_mode == "mono":
                        message = "Audio visualizer mono levels: " + ", ".join(
                            str(round(level[0] * 100))
                            for level in display_meter_levels
                        )
                    else:
                        message = "Audio visualizer stereo levels (L/R): " + ", ".join(
                            f"{round(level[0] * 100)}/{round(level[1] * 100)}"
                            for level in display_meter_levels
                        )
                    print(message, flush=True)
                    next_meter_log = now + 1.0
                next_meter_update = now + METER_UPDATE_SECONDS

            if display is not None:
                if display.error is not None and not display_error_reported:
                    print(f"Display helper stopped: {display.error}", file=sys.stderr)
                    display_error_reported = True
                if display_dirty and now >= display_due:
                    try:
                        if volume_levels_dirty:
                            display_volume_levels = channel_levels(
                                targets, muted, saved_levels
                            )
                            volume_levels_dirty = False
                        rebuilt_base_frame = False
                        if display_base_frame is None:
                            if display_mode == "image":
                                display_frame = render_fullscreen_image_display(
                                    channels,
                                    targets,
                                    muted,
                                    saved_levels,
                                    fullscreen_image,
                                    button_leds=programmable_leds,
                                    page_index=current_page,
                                    page_count=len(pages),
                                    display_brightness=display_brightness,
                                )
                            elif display_mode == "notepad":
                                display_frame = render_notepad_display(
                                    channels,
                                    targets,
                                    muted,
                                    saved_levels,
                                    notepad_text,
                                    notepad_style,
                                    button_leds=programmable_leds,
                                    page_index=current_page,
                                    page_count=len(pages),
                                    display_brightness=display_brightness,
                                )
                            else:
                                display_frame = render_mixer_display(
                                    channels,
                                    targets,
                                    muted,
                                    saved_levels,
                                    native_overlay=True,
                                    background_image=background_image,
                                    button_leds=programmable_leds,
                                    page_index=current_page,
                                    page_count=len(pages),
                                    meter_style=meter_style,
                                    show_volume_meters=show_volume_meters,
                                    volume_meter_mode=volume_meter_mode,
                                    meter_levels=display_meter_levels,
                                    display_brightness=display_brightness,
                                    streams_by_ch=cached_streams_by_ch,
                                    button_actions=button_actions,
                                    button_volume_presets=button_volume_presets,
                                )
                            display_base_frame = display_frame
                            last_display_base_frame = display_base_frame
                            rebuilt_base_frame = True
                        else:
                            display_frame = update_native_display_metadata(
                                display_base_frame,
                                channels,
                                targets,
                                muted,
                                saved_levels,
                                level_override=display_volume_levels,
                                display_mode=display_mode,
                                button_leds=programmable_leds,
                                page_index=current_page,
                                page_count=len(pages),
                                meter_style=meter_style,
                                show_volume_meters=show_volume_meters,
                                volume_meter_mode=volume_meter_mode,
                                meter_levels=display_meter_levels,
                                display_brightness=display_brightness,
                            )
                        display.submit(display_frame)
                        if (
                            display_cache is not None
                            and last_display_base_frame is not None
                            and rebuilt_base_frame
                        ):
                            save_resident_display_frame(
                                display_cache, last_display_base_frame
                            )
                        display_dirty = False
                    except RuntimeError as error:
                        print(f"Display rendering failed: {error}", file=sys.stderr)
                        display.close()
                        display = None

            try:
                packet = bytes(endpoint.read(PACKET_SIZE, timeout=100))
            except usb.core.USBTimeoutError:
                continue

            for encoder in range(1, 5):
                channel_targets = targets[encoder - 1]
                if not channel_targets:
                    # Do not bank movements while an assigned application is
                    # closed; otherwise reopening it could cause a volume jump.
                    accumulators[encoder - 1] = 0

                before = signed_position(previous, encoder)
                after = signed_position(packet, encoder)
                delta = wrapped_delta(before, after)
                if encoder in inverted:
                    delta = -delta
                if abs(delta) > 2:
                    accumulators[encoder - 1] += delta

                accumulator = accumulators[encoder - 1]
                if abs(accumulator) >= counts_per_percent:
                    sign = 1 if accumulator > 0 else -1
                    steps = min(int(abs(accumulator) // counts_per_percent), 8)
                    if muted[encoder - 1] and channel_targets:
                        saved_levels[encoder - 1] = max(
                            0.0,
                            min(1.0, saved_levels[encoder - 1] + sign * steps / 100),
                        )
                        accumulators[encoder - 1] -= sign * steps * counts_per_percent
                        print(
                            f"Encoder {encoder}: muted restore level "
                            f"{saved_levels[encoder - 1]:.0%}"
                        )
                        display_dirty = True
                        volume_levels_dirty = True
                        display_due = time.monotonic() + DISPLAY_SETTLE_SECONDS
                    elif change_volume(channel_targets, sign * steps):
                        accumulators[encoder - 1] -= sign * steps * counts_per_percent
                        print(
                            f"Encoder {encoder}: volume "
                            f"{'+' if sign > 0 else '-'}{steps}%"
                        )
                        display_dirty = True
                        volume_levels_dirty = True
                        display_due = time.monotonic() + DISPLAY_SETTLE_SECONDS
                    elif channel_targets:
                        accumulators[encoder - 1] = 0

                mask = button_masks[encoder]
                if packet[1] & mask and not previous[1] & mask:
                    new_state, new_level, succeeded = soft_toggle_mute(
                        channel_targets,
                        muted[encoder - 1],
                        saved_levels[encoder - 1],
                    )
                    muted[encoder - 1] = new_state
                    saved_levels[encoder - 1] = new_level
                    if succeeded:
                        state = "muted" if new_state else "unmuted"
                        print(f"Encoder {encoder}: {state}")
                        display_dirty = True
                        volume_levels_dirty = True
                        display_due = time.monotonic() + DISPLAY_SETTLE_SECONDS

            for button in pressed_programmable_buttons(previous[1], packet[1]):
                action = button_actions[button - 1]
                if action in {"next_page", "previous_page"}:
                    new_page = page_index_for_action(
                        current_page, len(pages), action
                    )
                    if new_page != current_page:
                        current_page = new_page
                        channels = pages[current_page]["channels"]
                        button_actions = pages[current_page]["button_actions"]
                        button_volume_presets = pages[current_page][
                            "button_volume_presets"
                        ]
                        muted = muted_pages[current_page]
                        saved_levels = saved_level_pages[current_page]
                        programmable_leds = button_led_states(button_actions)
                        targets = resolve_targets(channels, streams)

                        # Immediately resolve icon data for the new page so the
                        # first render pass has up-to-date icons rather than
                        # waiting for the next timer tick.
                        if display_helper is not None:
                            page_streams_by_ch = _resolve_channel_streams(
                                channels, {"streams": streams}
                            )
                            _page_icons, changed_icons = (
                                _resolve_channel_icons_for_streams(
                                    channels, page_streams_by_ch
                                )
                            )
                            if changed_icons:
                                _on_icon_cache_changed(changed_icons)
                        else:
                            page_streams_by_ch = [[] for _ in channels]

                        display_volume_levels = channel_levels(
                            targets, muted, saved_levels
                        )
                        volume_levels_dirty = False
                        display_meter_levels = [
                            (level, level) for level in display_volume_levels
                        ]
                        last_meter_values = None
                        if level_monitor is not None:
                            level_monitor.configure(
                                resolve_meter_targets(channels, targets, streams)
                            )
                        previous_targets = None
                        accumulators = [0, 0, 0, 0]
                        display_base_frame = None
                        # Invalidate the cached icon set so the first render of
                        # the new page always does a full rebuild with icons.
                        prev_cached_streams_by_ch = None
                        cached_streams_by_ch = page_streams_by_ch
                        display_dirty = True
                        display_due = time.monotonic()
                        print(
                            f"Button {button}: mixer page {current_page + 1}"
                        )
                elif action == "set_channel_volume":
                    if apply_channel_volume_preset(
                        button,
                        button_volume_presets[button - 1],
                        targets,
                        muted,
                        saved_levels,
                    ):
                        display_dirty = True
                        volume_levels_dirty = True
                        display_due = time.monotonic() + DISPLAY_SETTLE_SECONDS
                else:
                    run_programmable_button_action(button, action)

            previous = packet

    except usb.core.USBError as error:
        if getattr(error, "errno", None) in (1, 13):
            print(
                "USB access was denied. Install the included udev rule and reconnect "
                "the controller.",
                file=sys.stderr,
            )
        else:
            print(f"USB error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        try:
            cleanup_streams = discover_streams()
        except RuntimeError:
            cleanup_streams = streams if "streams" in locals() else []
        for page_index, page in enumerate(pages):
            cleanup_targets = resolve_targets(page["channels"], cleanup_streams)
            for index, is_muted in enumerate(muted_pages[page_index]):
                if is_muted and cleanup_targets[index]:
                    set_absolute_volume(
                        cleanup_targets[index], saved_level_pages[page_index][index]
                    )
        if claimed:
            try:
                usb.util.release_interface(device, INTERFACE)
            except usb.core.USBError:
                pass
        usb.util.dispose_resources(device)
        if level_monitor is not None:
            level_monitor.close()
        if display is not None:
            if last_display_base_frame is not None:
                try:
                    resident_logo = render_startup_display(
                        last_display_base_frame, DEFAULT_STARTUP_LOGO
                    )
                    display.submit_ordered(resident_logo)
                    if display_cache is not None:
                        save_resident_display_frame(display_cache, resident_logo)
                    print("OpenStream100 logo left on the controller.")
                except (OSError, RuntimeError) as error:
                    print(
                        f"Could not leave the OpenStream100 logo on the "
                        f"controller: {error}",
                        file=sys.stderr,
                    )
            display.close()


def main() -> int:
    signal.signal(signal.SIGTERM, handle_termination)
    args = parse_args()
    if shutil.which("pw-dump") is None or shutil.which("wpctl") is None:
        print("PipeWire and WirePlumber command-line tools are required.", file=sys.stderr)
        return 2
    if args.counts_per_percent is not None and args.counts_per_percent <= 0:
        print("--counts-per-percent must be greater than zero.", file=sys.stderr)
        return 2

    try:
        if args.display_fullscreen_test:
            return run_display_fullscreen_test(args.display_helper, args.display_replay)
        if args.display_action_color_test:
            return run_display_action_color_test(args.display_helper, args.display_replay)
        if args.display_object_test:
            return run_display_object_test(args.display_helper, args.display_replay)
        if args.display_protocol_test:
            return run_display_protocol_test(args.display_helper, args.display_replay)
        if args.display_solid_test:
            return run_display_solid_test(args.display_helper, args.display_replay)
        if args.calibrate_buttons:
            return calibrate_buttons(args.config)
        inverted = parse_inverted(args.invert)
        streams = discover_streams()
        if args.list_streams:
            print_streams(streams)
            return 0
        if args.setup or not args.config.exists():
            interactive_setup(args.config, streams)
            if args.setup:
                return 0
        pages = load_mixer_pages(args.config)
        button_masks = load_button_masks(args.config)
        background_image = load_background_path(args.config)
        display_mode = load_display_mode(args.config)
        fullscreen_image = load_fullscreen_image_path(args.config)
        notepad_text = load_notepad_text(args.config)
        notepad_style = load_notepad_style(args.config)
        show_volume_meters = load_show_volume_meters(args.config)
        meter_channel_mode = load_meter_channel_mode(args.config)
        meter_style = load_meter_style(args.config)
        volume_meter_mode = load_volume_meter_mode(args.config)
        display_brightness = load_display_brightness(args.config)
        counts_per_percent = args.counts_per_percent
        if counts_per_percent is None:
            counts_per_percent = counts_per_percent_for_sensitivity(
                load_knob_sensitivity(args.config)
            )
        return run_mixer(
            pages,
            counts_per_percent,
            args.config,
            display_brightness,
            inverted,
            button_masks,
            None if args.no_display else args.display_helper,
            None if args.no_display else args.display_replay,
            background_image,
            display_mode,
            fullscreen_image,
            notepad_text,
            notepad_style,
            show_volume_meters,
            meter_channel_mode,
            meter_style,
            volume_meter_mode,
            args.config.with_name("last-display-frame.bin"),
            None if args.no_display else args.display_socket,
            args.require_display_broker,
        )
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
