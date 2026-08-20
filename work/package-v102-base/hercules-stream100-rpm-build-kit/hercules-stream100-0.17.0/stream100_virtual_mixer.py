#!/usr/bin/python3
"""Mouse-controlled desktop mixer for OpenStream100."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


APP_ID = "com.hercules.Stream100.VirtualMixer"
APP_NAME = "OpenStream100 Virtual Mixer"
CONFIG_PATH = Path.home() / ".config" / "hercules-stream100" / "config.json"
BACKGROUND_PATH = CONFIG_PATH.with_name("background.png")
APP_DIRECTORY = Path(__file__).resolve().parent
MIXER_IMPLEMENTATION = APP_DIRECTORY / "stream100-mixer-alpha.py"
DEFAULT_TARGET = "@DEFAULT_AUDIO_SINK@"
MAX_MIXER_PAGES = 8
HARDWARE_DISPLAY_WIDTH = 480
HARDWARE_DISPLAY_HEIGHT = 272
DEFAULT_WINDOW_WIDTH = 960
MINIMUM_WINDOW_WIDTH = 720
DEFAULT_CHANNEL_COLOURS = ("#30CCBE", "#36D380", "#F6BE40", "#5B82F6")
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
BUTTON_ACTION_LABELS = {
    "disabled": "Do nothing",
    "microphone_mute": "Mic mute",
    "speaker_mute": "Speaker mute",
    "play_pause": "Play / pause",
    "previous_track": "Previous track",
    "next_track": "Next track",
    "set_channel_volume": "Set volume",
    "next_page": "Next page",
    "previous_page": "Previous page",
}
DEFAULT_BUTTON_ACTIONS = ["disabled", "disabled", "disabled", "disabled"]
DEFAULT_BUTTON_VOLUME_PRESETS = [
    {"channel": 1, "percentage": 50},
    {"channel": 2, "percentage": 50},
    {"channel": 3, "percentage": 50},
    {"channel": 4, "percentage": 50},
]
DEFAULT_CHANNELS = [
    {
        "kind": "default",
        "label": "Default output device",
        "color": DEFAULT_CHANNEL_COLOURS[0],
    },
    {"kind": "disabled", "label": "Disabled", "color": DEFAULT_CHANNEL_COLOURS[1]},
    {"kind": "disabled", "label": "Disabled", "color": DEFAULT_CHANNEL_COLOURS[2]},
    {"kind": "disabled", "label": "Disabled", "color": DEFAULT_CHANNEL_COLOURS[3]},
]
_METER_BACKEND: Any | None = None


def aspect_locked_size(width: int) -> tuple[int, int]:
    """Return a usable window size with the hardware display's 30:17 ratio."""
    locked_width = max(MINIMUM_WINDOW_WIDTH, int(round(width)))
    locked_height = round(
        locked_width * HARDWARE_DISPLAY_HEIGHT / HARDWARE_DISPLAY_WIDTH
    )
    return locked_width, locked_height


def resize_width_from_drag(
    start_width: int,
    start_height: int,
    offset_x: float,
    offset_y: float,
) -> int:
    """Choose the dominant resize axis while preserving the display ratio."""
    width_from_x = start_width + offset_x
    width_from_y = (
        start_height + offset_y
    ) * HARDWARE_DISPLAY_WIDTH / HARDWARE_DISPLAY_HEIGHT
    if abs(offset_x) >= abs(offset_y) * HARDWARE_DISPLAY_WIDTH / HARDWARE_DISPLAY_HEIGHT:
        return round(width_from_x)
    return round(width_from_y)


def command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def read_config_payload(path: Path = CONFIG_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_config_payload(payload: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_background_path(
    config_path: Path = CONFIG_PATH,
) -> Path | None:
    value = read_config_payload(config_path).get("background_image")
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    return path if path.is_file() else None


def save_background_path(
    background: Path | None,
    config_path: Path = CONFIG_PATH,
) -> None:
    payload = read_config_payload(config_path)
    if background is None:
        payload.pop("background_image", None)
    else:
        payload["background_image"] = str(background)
    write_config_payload(payload, config_path)


def import_background(
    source: Path,
    destination: Path = BACKGROUND_PATH,
    config_path: Path = CONFIG_PATH,
) -> Path:
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise RuntimeError("Image support is missing. Install python3-pillow.") from error
    try:
        with Image.open(source) as opened:
            transposed = ImageOps.exif_transpose(opened)
            preserves_alpha = (
                "A" in transposed.getbands() or "transparency" in transposed.info
            )
            image = transposed.convert("RGBA" if preserves_alpha else "RGB")
            image.thumbnail((1920, 1088), Image.Resampling.LANCZOS)
    except (OSError, ValueError) as error:
        raise RuntimeError("That file could not be opened as an image.") from error
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(destination)
    save_background_path(destination, config_path)
    return destination


def prepare_virtual_background(path: Path):
    """Load the saved artwork using the hardware mixer's readability treatment."""
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError as error:
        raise RuntimeError("Image support is missing. Install python3-pillow.") from error
    try:
        with Image.open(path) as opened:
            transposed = ImageOps.exif_transpose(opened)
            has_alpha = (
                "A" in transposed.getbands() or "transparency" in transposed.info
            )
            if has_alpha:
                foreground = transposed.convert("RGBA")
                backdrop = Image.new("RGBA", foreground.size, (10, 14, 20, 255))
                image = Image.alpha_composite(backdrop, foreground).convert("RGB")
            else:
                image = transposed.convert("RGB")
            image.load()
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Could not prepare the mixer background: {error}") from error
    image = ImageEnhance.Color(image).enhance(0.82)
    return ImageEnhance.Brightness(image).enhance(0.62).convert("RGBA")


def load_meter_backend():
    """Load the hardware-tested PipeWire peak monitor without starting USB code."""
    global _METER_BACKEND
    if _METER_BACKEND is not None:
        return _METER_BACKEND
    if not MIXER_IMPLEMENTATION.is_file():
        raise RuntimeError("The OpenStream100 mixer backend is missing.")
    spec = importlib.util.spec_from_file_location(
        "openstream100_meter_backend", MIXER_IMPLEMENTATION
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("The OpenStream100 meter backend could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _METER_BACKEND = module
    return module


def normalise_colour(value: object, index: int) -> str:
    text = str(value).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        return text.upper()
    return DEFAULT_CHANNEL_COLOURS[index]


def normalise_channels(value: object) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    channels: list[dict[str, str]] = []
    for index, raw_channel in enumerate(value):
        if not isinstance(raw_channel, dict):
            return None
        kind = raw_channel.get("kind")
        if kind not in {"default", "application", "disabled"}:
            return None
        channel = {
            str(key): str(item)
            for key, item in raw_channel.items()
            if isinstance(key, str) and isinstance(item, (str, int, float))
        }
        channel["kind"] = str(kind)
        channel["label"] = str(raw_channel.get("label") or "Disabled")
        channel["color"] = normalise_colour(raw_channel.get("color"), index)
        channels.append(channel)
    return channels


def normalise_presets(value: object) -> list[dict[str, int]] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    presets: list[dict[str, int]] = []
    for raw_preset in value:
        if not isinstance(raw_preset, dict):
            return None
        channel = raw_preset.get("channel")
        percentage = raw_preset.get("percentage")
        if (
            not isinstance(channel, int)
            or isinstance(channel, bool)
            or channel not in {1, 2, 3, 4}
            or not isinstance(percentage, int)
            or isinstance(percentage, bool)
            or not 0 <= percentage <= 100
        ):
            return None
        presets.append({"channel": channel, "percentage": percentage})
    return presets


def normalise_page(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    channels = normalise_channels(value.get("channels"))
    actions = value.get("button_actions")
    presets = normalise_presets(value.get("button_volume_presets"))
    if (
        channels is None
        or presets is None
        or not isinstance(actions, list)
        or len(actions) != 4
        or not all(isinstance(action, str) and action in BUTTON_ACTION_IDS for action in actions)
    ):
        return None
    return {
        "channels": channels,
        "button_actions": list(actions),
        "button_volume_presets": presets,
    }


def default_page() -> dict[str, Any]:
    return {
        "channels": [dict(channel) for channel in DEFAULT_CHANNELS],
        "button_actions": list(DEFAULT_BUTTON_ACTIONS),
        "button_volume_presets": [
            dict(preset) for preset in DEFAULT_BUTTON_VOLUME_PRESETS
        ],
    }


def load_mixer_pages(path: Path = CONFIG_PATH) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return [default_page()]
    if not isinstance(payload, dict):
        return [default_page()]
    raw_pages = payload.get("pages")
    if isinstance(raw_pages, list) and 1 <= len(raw_pages) <= MAX_MIXER_PAGES:
        pages = [normalise_page(page) for page in raw_pages]
        if all(page is not None for page in pages):
            return [page for page in pages if page is not None]
    legacy_page = normalise_page(
        {
            "channels": payload.get("channels"),
            "button_actions": payload.get(
                "button_actions", DEFAULT_BUTTON_ACTIONS
            ),
            "button_volume_presets": payload.get(
                "button_volume_presets", DEFAULT_BUTTON_VOLUME_PRESETS
            ),
        }
    )
    return [legacy_page or default_page()]


def load_meter_preferences(
    path: Path = CONFIG_PATH,
) -> tuple[bool, str, str]:
    payload = read_config_payload(path)
    show = payload.get("show_volume_meters", True)
    mode = payload.get("meter_channel_mode", "stereo")
    style = payload.get("meter_style", "classic")
    return (
        show if isinstance(show, bool) else True,
        mode if mode in {"stereo", "mono"} else "stereo",
        style if style in {"classic", "segmented", "rounded", "slim"} else "classic",
    )


def parse_pipewire_streams(document: str) -> list[dict[str, Any]]:
    try:
        objects = json.loads(document)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"pw-dump returned invalid JSON: {error}") from error
    if not isinstance(objects, list):
        raise RuntimeError("pw-dump returned an unexpected response")
    streams: list[dict[str, Any]] = []
    for item in objects:
        if not isinstance(item, dict) or not str(item.get("type", "")).endswith(":Node"):
            continue
        props = item.get("info", {}).get("props", {})
        if not isinstance(props, dict) or props.get("media.class") != "Stream/Output/Audio":
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
        streams.append(
            {
                "id": int(item["id"]),
                "label": str(
                    props.get("application.name")
                    or props.get("node.description")
                    or props.get("media.name")
                    or match_value
                ),
                "property": match_property,
                "value": str(match_value),
                "props": props,
            }
        )
    return streams


def discover_streams() -> list[dict[str, Any]]:
    result = command(["pw-dump"])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "pw-dump could not read the PipeWire graph"
        )
    return parse_pipewire_streams(result.stdout)


def resolve_targets(
    channels: list[dict[str, str]], streams: list[dict[str, Any]]
) -> list[list[str]]:
    targets: list[list[str]] = []
    for channel in channels:
        if channel.get("kind") == "default":
            targets.append([DEFAULT_TARGET])
        elif channel.get("kind") == "application":
            targets.append(
                [
                    str(stream["id"])
                    for stream in streams
                    if stream.get("property") == channel.get("property")
                    and stream.get("value") == channel.get("value")
                ]
            )
        else:
            targets.append([])
    return targets


def parse_wpctl_volume(document: str) -> float | None:
    match = re.search(r"\bVolume:\s*([0-9]+(?:\.[0-9]+)?)", document)
    return float(match.group(1)) if match else None


def read_volume(target: str) -> float | None:
    result = command(["wpctl", "get-volume", target])
    return parse_wpctl_volume(result.stdout) if result.returncode == 0 else None


def set_absolute_volume(targets: list[str], level: float) -> bool:
    level = max(0.0, min(1.0, level))
    succeeded = False
    for target in targets:
        result = command(
            ["wpctl", "set-volume", target, f"{level:.4f}", "--limit", "1.0"]
        )
        succeeded |= result.returncode == 0
    return succeeded


def run_programmable_action(action: str) -> tuple[bool, str]:
    commands: dict[str, tuple[list[str], str]] = {
        "microphone_mute": (
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"],
            "Microphone mute toggled.",
        ),
        "speaker_mute": (
            ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
            "Speaker mute toggled.",
        ),
        "play_pause": (["playerctl", "play-pause"], "Play / pause sent."),
        "previous_track": (["playerctl", "previous"], "Previous track sent."),
        "next_track": (["playerctl", "next"], "Next track sent."),
    }
    selected = commands.get(action)
    if selected is None:
        return False, "This button has no action."
    arguments, message = selected
    if shutil.which(arguments[0]) is None:
        return False, f"{arguments[0]} is required for this action."
    result = command(arguments)
    if result.returncode == 0:
        return True, message
    return False, result.stderr.strip() or result.stdout.strip() or "The action failed."


def action_label(action: str, preset: dict[str, int]) -> str:
    label = BUTTON_ACTION_LABELS.get(action, "Unknown action")
    if action == "set_channel_volume":
        return f"{label}: C{preset['channel']} · {preset['percentage']}%"
    return label


def compact_action_label(action: str, preset: dict[str, int]) -> str:
    labels = {
        "disabled": "Off",
        "microphone_mute": "Mic mute",
        "speaker_mute": "Spkr mute",
        "play_pause": "Play / pause",
        "previous_track": "Previous",
        "next_track": "Next",
        "next_page": "Next page",
        "previous_page": "Prev page",
    }
    if action == "set_channel_volume":
        return f"C{preset['channel']} · {preset['percentage']}%"
    return labels.get(action, "Action")


def matching_channel_streams(
    channel: dict[str, str], streams: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return live streams belonging to a saved application channel."""
    if channel.get("kind") != "application":
        return []
    property_name = channel.get("property")
    value = str(channel.get("value", ""))
    matches: list[dict[str, Any]] = []
    for stream in streams:
        props = stream.get("props", {})
        if (
            stream.get("property") == property_name
            and str(stream.get("value", "")) == value
        ) or (
            isinstance(props, dict)
            and property_name
            and str(props.get(property_name, "")) == value
        ):
            matches.append(stream)
    return matches


def icon_candidates(
    channel: dict[str, str], streams: list[dict[str, Any]]
) -> list[str]:
    """Return the icon candidates shared with the hardware mixer."""
    from stream100_channel_icons import channel_icon_candidates

    return channel_icon_candidates(channel, streams)


def make_meter_class(Gtk, Gdk):
    class StereoMeter(Gtk.DrawingArea):
        """Hardware-inspired stereo activity bars with a volume marker."""

        def __init__(self):
            super().__init__()
            self.left_level = 0.0
            self.right_level = 0.0
            self.volume = 0.0
            self.accent = DEFAULT_CHANNEL_COLOURS[0]
            self.online = False
            self.muted = False
            self.meter_style = "classic"
            self.set_size_request(54, 128)
            self.set_vexpand(True)
            self.set_draw_func(self.draw_meter)

        def set_state(
            self,
            left: float,
            right: float,
            volume: float,
            accent: str,
            online: bool,
            muted: bool,
            style: str,
        ) -> None:
            state = (
                max(0.0, min(1.0, left)),
                max(0.0, min(1.0, right)),
                max(0.0, min(1.0, volume)),
                accent,
                online,
                muted,
                style,
            )
            previous = (
                self.left_level,
                self.right_level,
                self.volume,
                self.accent,
                self.online,
                self.muted,
                self.meter_style,
            )
            if state == previous:
                return
            (
                self.left_level,
                self.right_level,
                self.volume,
                self.accent,
                self.online,
                self.muted,
                self.meter_style,
            ) = state
            self.queue_draw()

        @staticmethod
        def set_source(cairo_context, colour: str, alpha: float = 1.0) -> None:
            rgba = Gdk.RGBA()
            if not rgba.parse(colour):
                rgba.parse("#30CCBE")
            cairo_context.set_source_rgba(
                rgba.red, rgba.green, rgba.blue, rgba.alpha * alpha
            )

        def draw_meter(self, _area, cairo_context, width, height) -> None:
            top = 12.0
            bottom = max(top + 20.0, height - 12.0)
            centre = width / 2.0
            style = self.meter_style
            bar_width = {"classic": 15.0, "segmented": 14.0, "rounded": 17.0, "slim": 7.0}.get(style, 15.0)
            gap = {"classic": 10.0, "segmented": 9.0, "rounded": 9.0, "slim": 8.0}.get(style, 10.0)
            centres = (
                centre - gap / 2.0 - bar_width / 2.0,
                centre + gap / 2.0 + bar_width / 2.0,
            )
            levels = (self.left_level, self.right_level)
            if self.muted or not self.online:
                levels = (0.0, 0.0)

            for x, level in zip(centres, levels):
                self.set_source(cairo_context, "#2C3038", 0.92 if self.online else 0.48)
                if style == "segmented":
                    segment_count = 14
                    segment_gap = 3.0
                    segment_height = (
                        bottom - top - segment_gap * (segment_count - 1)
                    ) / segment_count
                    active_count = round(level * segment_count)
                    for segment in range(segment_count):
                        y = bottom - (segment + 1) * segment_height - segment * segment_gap
                        cairo_context.rectangle(
                            x - bar_width / 2.0, y, bar_width, segment_height
                        )
                        cairo_context.fill()
                        if segment < active_count:
                            self.set_source(cairo_context, self.accent)
                            cairo_context.rectangle(
                                x - bar_width / 2.0, y, bar_width, segment_height
                            )
                            cairo_context.fill()
                            self.set_source(cairo_context, "#2C3038")
                    continue

                cairo_context.set_line_width(bar_width)
                cairo_context.set_line_cap(
                    1 if style in {"rounded", "slim"} else 0
                )
                cairo_context.move_to(x, top)
                cairo_context.line_to(x, bottom)
                cairo_context.stroke()
                if level > 0:
                    self.set_source(cairo_context, self.accent)
                    active_top = bottom - (bottom - top) * level
                    cairo_context.move_to(x, bottom)
                    cairo_context.line_to(x, active_top)
                    cairo_context.stroke()

            if self.online:
                marker_y = bottom - (bottom - top) * self.volume
                marker_left = centres[0] - bar_width / 2.0 - 5.0
                marker_right = centres[1] + bar_width / 2.0 + 5.0
                cairo_context.set_line_cap(1)
                cairo_context.set_line_width(5.0)
                self.set_source(cairo_context, "#0A0E14", 0.8)
                cairo_context.move_to(marker_left, marker_y)
                cairo_context.line_to(marker_right, marker_y)
                cairo_context.stroke()
                cairo_context.set_line_width(2.0)
                self.set_source(cairo_context, "#FFFFFF")
                cairo_context.move_to(marker_left, marker_y)
                cairo_context.line_to(marker_right, marker_y)
                cairo_context.stroke()

    return StereoMeter


def make_window_class(Gtk, GLib, Gdk):
    StereoMeter = make_meter_class(Gtk, Gdk)
    class VirtualMixerWindow(Gtk.ApplicationWindow):
        def __init__(self, application):
            super().__init__(application=application)
            self.set_title(APP_NAME)
            default_width, default_height = aspect_locked_size(DEFAULT_WINDOW_WIDTH)
            minimum_width, minimum_height = aspect_locked_size(MINIMUM_WINDOW_WIDTH)
            # The smaller default remains GTK's shrink floor; the explicit
            # request below gives the mixer its comfortable initial size.
            self.set_default_size(minimum_width, minimum_height)
            # Wayland does not provide GTK4 with native aspect-ratio hints. A
            # dedicated resize grip changes both dimensions together instead.
            self.set_resizable(False)
            self.set_size_request(default_width, default_height)
            self.resize_start_width = default_width
            self.resize_start_height = default_height
            self.compact_layout = False
            self.pages = load_mixer_pages()
            self.page_index = 0
            self.targets: list[list[str]] = [[], [], [], []]
            self.streams: list[dict[str, Any]] = []
            self.background_path = load_background_path()
            self.background_texture = None
            (
                self.show_activity_meters,
                self.meter_channel_mode,
                self.meter_style,
            ) = load_meter_preferences()
            self.meter_levels = [(0.0, 0.0)] * 4
            self.volume_levels = [0.0] * 4
            self.meter_backend = None
            self.level_monitor = None
            if shutil.which("parec") is not None and shutil.which("pactl") is not None:
                try:
                    self.meter_backend = load_meter_backend()
                    self.level_monitor = self.meter_backend.PipeWireLevelMonitor(
                        self.meter_channel_mode
                    )
                except (Exception, SystemExit) as error:
                    print(f"Virtual visualisers unavailable: {error}", file=sys.stderr)
            self.muted_pages = [[False] * 4 for _page in self.pages]
            self.saved_level_pages = [[0.5] * 4 for _page in self.pages]
            self.updating_widgets = False
            self.pending_volume_updates: list[int | None] = [None] * 4
            self.channel_icon_keys: list[tuple[Any, ...] | None] = [None] * 4
            self.channel_icon_textures: list[Any | None] = [None] * 4
            self.config_signature = self._config_signature()

            header = Gtk.HeaderBar()
            previous_page = Gtk.Button.new_from_icon_name("go-previous-symbolic")
            previous_page.set_tooltip_text("Previous mixer page")
            previous_page.connect("clicked", self.on_previous_page)
            header.pack_start(previous_page)
            next_page = Gtk.Button.new_from_icon_name("go-next-symbolic")
            next_page.set_tooltip_text("Next mixer page")
            next_page.connect("clicked", self.on_next_page)
            header.pack_start(next_page)
            self.page_title = Gtk.Label()
            self.page_title.add_css_class("title")
            header.set_title_widget(self.page_title)
            refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
            refresh.set_tooltip_text("Refresh PipeWire applications")
            refresh.connect("clicked", self.on_refresh_clicked)
            header.pack_end(refresh)
            choose_background = Gtk.Button.new_from_icon_name(
                "image-x-generic-symbolic"
            )
            choose_background.set_tooltip_text("Choose mixer background")
            choose_background.connect("clicked", self.on_choose_background)
            header.pack_end(choose_background)
            self.remove_background_button = Gtk.Button.new_from_icon_name(
                "edit-clear-symbolic"
            )
            self.remove_background_button.set_tooltip_text("Remove mixer background")
            self.remove_background_button.connect("clicked", self.on_remove_background)
            header.pack_end(self.remove_background_button)
            self.set_titlebar(header)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            outer.set_margin_top(12)
            outer.set_margin_bottom(12)
            outer.set_margin_start(12)
            outer.set_margin_end(12)
            outer.add_css_class("mixer-surface")
            self.mixer_surface = outer
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            scroller.set_child(outer)
            scroller.add_css_class("mixer-scroller")
            self.background_picture = Gtk.Picture()
            self.background_picture.set_content_fit(Gtk.ContentFit.COVER)
            self.background_picture.set_can_shrink(True)
            self.background_picture.set_hexpand(True)
            self.background_picture.set_vexpand(True)
            background_overlay = Gtk.Overlay()
            background_overlay.set_child(self.background_picture)
            background_overlay.add_overlay(scroller)
            resize_grip = Gtk.DrawingArea()
            resize_grip.set_size_request(30, 30)
            resize_grip.set_halign(Gtk.Align.END)
            resize_grip.set_valign(Gtk.Align.END)
            resize_grip.set_tooltip_text(
                "Drag to resize · hardware display aspect ratio is locked"
            )
            resize_grip.set_cursor_from_name("se-resize")
            resize_grip.set_draw_func(self.draw_resize_grip)
            resize_grip.add_css_class("resize-grip")
            resize_drag = Gtk.GestureDrag()
            resize_drag.connect("drag-begin", self.on_resize_drag_begin)
            resize_drag.connect("drag-update", self.on_resize_drag_update)
            resize_grip.add_controller(resize_drag)
            background_overlay.add_overlay(resize_grip)
            self.resize_grip = resize_grip
            self.set_child(background_overlay)

            self.connection_status = Gtk.Label(label="Connecting to PipeWire…")
            self.connection_status.set_xalign(0)
            self.connection_status.add_css_class("status-banner")
            outer.append(self.connection_status)

            channel_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
            channel_grid.set_column_homogeneous(True)
            channel_grid.set_hexpand(True)
            channel_grid.set_vexpand(True)
            outer.append(channel_grid)

            self.channel_cards: list[Any] = []
            self.channel_numbers: list[Any] = []
            self.channel_names: list[Any] = []
            self.channel_icons: list[Any] = []
            self.channel_statuses: list[Any] = []
            self.channel_scales: list[Any] = []
            self.meter_widgets: list[Any] = []
            self.meter_drag_origins = [0.0] * 4
            self.volume_labels: list[Any] = []
            self.mute_buttons: list[Any] = []
            self.accent_bars: list[Any] = []
            for index in range(4):
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                card.add_css_class("channel-card")
                card.set_hexpand(True)
                card.set_vexpand(True)
                channel_grid.attach(card, index, 0, 1, 1)
                self.channel_cards.append(card)

                accent = Gtk.DrawingArea()
                accent.set_size_request(-1, 5)
                card.append(accent)
                self.accent_bars.append(accent)

                channel_header = Gtk.CenterBox()
                number = Gtk.Label(label=str(index + 1))
                number.set_xalign(0)
                number.add_css_class("caption")
                channel_header.set_start_widget(number)
                self.channel_numbers.append(number)
                icon = Gtk.Image()
                icon.set_pixel_size(36)
                icon.set_size_request(40, 40)
                icon.add_css_class("channel-icon")
                channel_header.set_center_widget(icon)
                self.channel_icons.append(icon)
                card.append(channel_header)
                name = Gtk.Label()
                name.set_wrap(True)
                name.set_justify(Gtk.Justification.CENTER)
                name.set_lines(2)
                name.add_css_class("channel-title")
                card.append(name)
                self.channel_names.append(name)
                status = Gtk.Label()
                status.add_css_class("dim-label")
                card.append(status)
                self.channel_statuses.append(status)

                adjustment = Gtk.Adjustment.new(0, 0, 100, 1, 5, 0)
                adjustment.connect("value-changed", self.on_volume_changed, index)
                self.channel_scales.append(adjustment)
                meter = StereoMeter()
                meter.set_tooltip_text(
                    "Stereo activity · white marker shows volume · drag to adjust"
                )
                drag = Gtk.GestureDrag()
                drag.connect("drag-begin", self.on_meter_drag_begin, index)
                drag.connect("drag-update", self.on_meter_drag_update, index)
                meter.add_controller(drag)
                click = Gtk.GestureClick()
                click.connect("pressed", self.on_meter_pressed, index)
                meter.add_controller(click)
                scroll = Gtk.EventControllerScroll.new(
                    Gtk.EventControllerScrollFlags.VERTICAL
                )
                scroll.connect("scroll", self.on_meter_scroll, index)
                meter.add_controller(scroll)
                card.append(meter)
                self.meter_widgets.append(meter)
                volume = Gtk.Label(label="—")
                volume.add_css_class("volume-readout")
                card.append(volume)
                self.volume_labels.append(volume)
                mute = Gtk.ToggleButton(label="Mute")
                mute.connect("toggled", self.on_mute_toggled, index)
                card.append(mute)
                self.mute_buttons.append(mute)

            self.action_heading = Gtk.Label(label="Programmable buttons")
            self.action_heading.set_xalign(0)
            self.action_heading.add_css_class("heading")
            outer.append(self.action_heading)
            action_grid = Gtk.Grid(column_spacing=10)
            action_grid.set_column_homogeneous(True)
            action_grid.add_css_class("action-strip")
            outer.append(action_grid)
            self.action_buttons: list[Any] = []
            for index in range(4):
                button = Gtk.Button()
                button.connect("clicked", self.on_action_clicked, index)
                action_grid.attach(button, index, 0, 1, 1)
                self.action_buttons.append(button)

            self.message = Gtk.Label()
            self.message.set_xalign(0)
            self.message.set_wrap(True)
            self.message.add_css_class("dim-label")
            outer.append(self.message)

            self.apply_window_scale(default_width)
            self.update_background()
            self.show_page(0)
            self.refresh_audio_state()
            GLib.timeout_add_seconds(1, self.refresh_audio_state)
            GLib.timeout_add(80, self.refresh_meter_state)
            self.connect("close-request", self.on_close_request)

        def draw_resize_grip(self, area, cairo_context, width, height) -> None:
            colour = area.get_color()
            cairo_context.set_source_rgba(
                colour.red, colour.green, colour.blue, colour.alpha * 0.72
            )
            cairo_context.set_line_width(1.6)
            cairo_context.set_line_cap(1)
            for inset in (7.0, 12.0, 17.0):
                cairo_context.move_to(width - inset, height - 5.0)
                cairo_context.line_to(width - 5.0, height - inset)
                cairo_context.stroke()

        def on_resize_drag_begin(
            self, _gesture, _start_x: float, _start_y: float
        ) -> None:
            self.resize_start_width = max(self.get_width(), MINIMUM_WINDOW_WIDTH)
            self.resize_start_height = self.get_height()

        def on_resize_drag_update(
            self, _gesture, offset_x: float, offset_y: float
        ) -> None:
            requested_width = resize_width_from_drag(
                self.resize_start_width,
                self.resize_start_height,
                offset_x,
                offset_y,
            )
            width, height = aspect_locked_size(requested_width)
            if width == self.get_width() and height == self.get_height():
                return
            self.apply_window_scale(width)
            self.set_size_request(width, height)

        def apply_window_scale(self, width: int) -> None:
            compact = width < 840
            self.compact_layout = compact
            if compact:
                self.mixer_surface.add_css_class("compact")
            else:
                self.mixer_surface.remove_css_class("compact")

            margin = 8 if compact else 12
            self.mixer_surface.set_margin_top(margin)
            self.mixer_surface.set_margin_bottom(margin)
            self.mixer_surface.set_margin_start(margin)
            self.mixer_surface.set_margin_end(margin)
            self.mixer_surface.set_spacing(6 if compact else 10)
            self.connection_status.set_visible(not compact)
            self.action_heading.set_visible(not compact)
            self.message.set_visible(bool(self.message.get_text()))

            for card in self.channel_cards:
                card.set_spacing(4 if compact else 6)
            for icon in self.channel_icons:
                icon_size = 30 if compact else 36
                icon.set_pixel_size(icon_size)
                icon.set_size_request(icon_size + 4, icon_size + 4)
            for status in self.channel_statuses:
                status.set_visible(not compact)
            for meter in self.meter_widgets:
                meter.set_size_request(44 if compact else 54, 84 if compact else 128)

            if self.action_buttons:
                page = self.pages[self.page_index]
                for index, action in enumerate(page["button_actions"]):
                    preset = page["button_volume_presets"][index]
                    label = (
                        compact_action_label(action, preset)
                        if compact
                        else action_label(action, preset)
                    )
                    self.action_buttons[index].set_label(f"{index + 1} · {label}")

        def _config_signature(self) -> tuple[int, int] | None:
            try:
                details = CONFIG_PATH.stat()
                return details.st_mtime_ns, details.st_size
            except OSError:
                return None

        @staticmethod
        def texture_from_pil(opened):
            image = opened.convert("RGBA")
            width, height = image.size
            pixels = GLib.Bytes.new(image.tobytes())
            return Gdk.MemoryTexture.new(
                width,
                height,
                Gdk.MemoryFormat.R8G8B8A8,
                pixels,
                width * 4,
            )

        def update_background(self) -> None:
            self.background_path = load_background_path()
            if self.background_path is None:
                self.background_texture = None
                self.background_picture.set_paintable(None)
                self.mixer_surface.remove_css_class("has-background")
                self.resize_grip.remove_css_class("on-background")
                self.remove_background_button.set_sensitive(False)
                return
            try:
                prepared = prepare_virtual_background(self.background_path)
                self.background_texture = self.texture_from_pil(prepared)
                self.background_picture.set_paintable(self.background_texture)
                self.mixer_surface.add_css_class("has-background")
                self.resize_grip.add_css_class("on-background")
                self.remove_background_button.set_sensitive(True)
            except RuntimeError as error:
                self.background_texture = None
                self.background_picture.set_paintable(None)
                self.mixer_surface.remove_css_class("has-background")
                self.resize_grip.remove_css_class("on-background")
                self.remove_background_button.set_sensitive(False)
                self.show_message(str(error), error=True)

        def on_choose_background(self, _button) -> None:
            chooser = Gtk.FileChooserNative.new(
                "Choose a mixer background",
                self,
                Gtk.FileChooserAction.OPEN,
                "Choose",
                "Cancel",
            )
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Images")
            for mime_type in (
                "image/png",
                "image/jpeg",
                "image/webp",
                "image/bmp",
            ):
                image_filter.add_mime_type(mime_type)
            chooser.add_filter(image_filter)
            chooser.connect("response", self.on_background_response)
            chooser.show()

        def on_background_response(self, chooser, response) -> None:
            try:
                if response != Gtk.ResponseType.ACCEPT:
                    return
                selected = chooser.get_file()
                filename = selected.get_path() if selected is not None else None
                if not filename:
                    raise RuntimeError("Choose an image stored on this computer.")
                self.background_path = import_background(Path(filename))
                self.config_signature = self._config_signature()
                self.update_background()
                self.show_message(
                    "Mixer background updated. The hardware display will use it "
                    "after its next mixer restart."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            finally:
                chooser.destroy()

        def on_remove_background(self, _button) -> None:
            try:
                save_background_path(None)
                BACKGROUND_PATH.unlink(missing_ok=True)
                self.config_signature = self._config_signature()
                self.update_background()
                self.show_message(
                    "Mixer background removed. The hardware display will update "
                    "after its next mixer restart."
                )
            except OSError as error:
                self.show_message(
                    f"Could not remove the mixer background: {error}", error=True
                )

        def show_message(self, text: str, error: bool = False) -> None:
            self.message.set_text(text)
            self.message.set_visible(bool(text))
            self.message.remove_css_class("success")
            self.message.remove_css_class("error")
            self.message.add_css_class("error" if error else "success")

        def draw_accent(self, _area, cairo_context, width, height, colour: str) -> None:
            rgba = Gdk.RGBA()
            if not rgba.parse(colour):
                rgba.parse(DEFAULT_CHANNEL_COLOURS[0])
            cairo_context.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
            cairo_context.rectangle(0, 0, width, height)
            cairo_context.fill()

        def show_page(self, page_index: int) -> None:
            self.page_index = page_index % len(self.pages)
            page = self.pages[self.page_index]
            self.page_title.set_text(
                f"Virtual Mixer · Page {self.page_index + 1} of {len(self.pages)}"
            )
            for index, channel in enumerate(page["channels"]):
                self.channel_numbers[index].set_text(
                    str(self.page_index * 4 + index + 1)
                )
                self.channel_names[index].set_text(channel.get("label", "Disabled"))
                self.update_channel_icon(index, channel)
                colour = normalise_colour(channel.get("color"), index)
                self.accent_bars[index].set_draw_func(
                    self.draw_accent, colour
                )
                self.accent_bars[index].queue_draw()
                disabled = channel.get("kind") == "disabled"
                self.meter_widgets[index].set_sensitive(not disabled)
                self.mute_buttons[index].set_sensitive(not disabled)
                self.update_meter_widget(index)
            for index, action in enumerate(page["button_actions"]):
                preset = page["button_volume_presets"][index]
                label = (
                    compact_action_label(action, preset)
                    if self.compact_layout
                    else action_label(action, preset)
                )
                self.action_buttons[index].set_label(
                    f"{index + 1} · {label}"
                )
                self.action_buttons[index].set_sensitive(action != "disabled")
            self.refresh_audio_state()

        def update_channel_icon(
            self, index: int, channel: dict[str, str]
        ) -> None:
            streams = matching_channel_streams(channel, self.streams)
            stream_identity = tuple(
                (
                    stream.get("id"),
                    stream.get("props", {}).get("application.icon-name"),
                    stream.get("props", {}).get("application.id"),
                    stream.get("props", {}).get("application.process.binary"),
                )
                for stream in streams
            )
            icon_key = (
                channel.get("kind"),
                channel.get("label"),
                channel.get("property"),
                channel.get("value"),
                stream_identity,
            )
            if self.channel_icon_keys[index] == icon_key:
                return
            self.channel_icon_keys[index] = icon_key
            image = self.channel_icons[index]
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            for candidate in icon_candidates(channel, streams):
                candidate_path = Path(candidate)
                if candidate_path.is_file():
                    image.set_from_file(str(candidate_path))
                    image.set_tooltip_text(channel.get("label", "Audio"))
                    return
                if theme.has_icon(candidate):
                    image.set_from_icon_name(candidate)
                    image.set_tooltip_text(channel.get("label", candidate))
                    self.channel_icon_textures[index] = None
                    return
            if channel.get("kind") == "application":
                try:
                    from stream100_channel_icons import load_channel_icon

                    opened = load_channel_icon(channel, streams, icon_size=40)
                    if opened is not None:
                        opened = opened.convert("RGBA")
                        width, height = opened.size
                        pixels = GLib.Bytes.new(opened.tobytes())
                        texture = Gdk.MemoryTexture.new(
                            width,
                            height,
                            Gdk.MemoryFormat.R8G8B8A8,
                            pixels,
                            width * 4,
                        )
                        image.set_from_paintable(texture)
                        image.set_tooltip_text(channel.get("label", "Application"))
                        self.channel_icon_textures[index] = texture
                        return
                except (ImportError, OSError, RuntimeError, ValueError):
                    pass
            fallback = {
                "default": "audio-speakers-symbolic",
                "disabled": "audio-volume-muted-symbolic",
            }.get(channel.get("kind"), "application-x-executable-symbolic")
            image.set_from_icon_name(fallback)
            image.set_tooltip_text(channel.get("label", "Audio"))
            self.channel_icon_textures[index] = None

        def switch_page(self, offset: int) -> None:
            self.show_page((self.page_index + offset) % len(self.pages))

        def on_previous_page(self, _button) -> None:
            self.switch_page(-1)

        def on_next_page(self, _button) -> None:
            self.switch_page(1)

        def on_refresh_clicked(self, _button) -> None:
            self.refresh_audio_state()
            self.show_message("PipeWire applications refreshed.")

        def reload_config_if_changed(self) -> None:
            signature = self._config_signature()
            if signature == self.config_signature:
                return
            self.config_signature = signature
            pages = load_mixer_pages()
            previous_count = len(self.pages)
            previous_meter_mode = self.meter_channel_mode
            (
                self.show_activity_meters,
                self.meter_channel_mode,
                self.meter_style,
            ) = load_meter_preferences()
            if self.meter_channel_mode != previous_meter_mode:
                if self.level_monitor is not None:
                    self.level_monitor.close()
                    self.level_monitor = None
                if self.meter_backend is not None:
                    self.level_monitor = self.meter_backend.PipeWireLevelMonitor(
                        self.meter_channel_mode
                    )
            self.pages = pages
            self.muted_pages = [[False] * 4 for _page in pages]
            self.saved_level_pages = [[0.5] * 4 for _page in pages]
            self.update_background()
            self.show_page(min(self.page_index, len(pages) - 1))
            self.show_message(
                f"Reloaded {len(pages)} saved mixer page"
                f"{'s' if len(pages) != 1 else ''}."
            )
            if previous_count != len(pages):
                self.page_title.queue_draw()

        def refresh_audio_state(self) -> bool:
            self.reload_config_if_changed()
            page = self.pages[self.page_index]
            try:
                self.streams = discover_streams()
                self.targets = resolve_targets(page["channels"], self.streams)
                self.connection_status.set_text("● PipeWire connected")
                self.connection_status.remove_css_class("error")
                self.connection_status.add_css_class("success")
            except RuntimeError as error:
                self.streams = []
                self.targets = [[], [], [], []]
                self.connection_status.set_text(f"○ PipeWire unavailable · {error}")
                self.connection_status.remove_css_class("success")
                self.connection_status.add_css_class("error")

            if self.level_monitor is not None and self.meter_backend is not None:
                try:
                    meter_targets = self.meter_backend.resolve_meter_targets(
                        page["channels"], self.targets, self.streams
                    )
                    self.level_monitor.configure(meter_targets)
                except RuntimeError as error:
                    self.meter_levels = [(0.0, 0.0)] * 4
                    print(f"Virtual visualiser discovery failed: {error}", file=sys.stderr)

            muted = self.muted_pages[self.page_index]
            saved_levels = self.saved_level_pages[self.page_index]
            self.updating_widgets = True
            try:
                for index, (channel, targets) in enumerate(
                    zip(page["channels"], self.targets)
                ):
                    self.update_channel_icon(index, channel)
                    disabled = channel.get("kind") == "disabled"
                    levels = [
                        level
                        for target in targets
                        if (level := read_volume(target)) is not None
                    ]
                    available = bool(levels) and not disabled
                    self.meter_widgets[index].set_sensitive(available)
                    self.mute_buttons[index].set_sensitive(available)
                    if not available:
                        self.volume_levels[index] = 0.0
                        self.channel_scales[index].set_value(0)
                        self.channel_statuses[index].set_text(
                            "Disabled" if disabled else "Waiting for audio"
                        )
                        self.volume_labels[index].set_text("—")
                        self.update_meter_widget(index)
                        continue
                    level = max(levels)
                    self.volume_levels[index] = level
                    if level > 0.0005:
                        saved_levels[index] = level
                        muted[index] = False
                    self.channel_scales[index].set_value(level * 100)
                    self.volume_labels[index].set_text(f"{round(level * 100)}%")
                    self.channel_statuses[index].set_text(
                        f"{len(targets)} active target"
                        f"{'s' if len(targets) != 1 else ''}"
                    )
                    self.mute_buttons[index].set_active(muted[index])
                    self.mute_buttons[index].set_label(
                        "Unmute" if muted[index] else "Mute"
                    )
                    if muted[index]:
                        self.mute_buttons[index].add_css_class("destructive-action")
                    else:
                        self.mute_buttons[index].remove_css_class("destructive-action")
                    self.update_meter_widget(index)
            finally:
                self.updating_widgets = False
            return True

        def update_meter_widget(self, index: int) -> None:
            page = self.pages[self.page_index]
            channel = page["channels"][index]
            left, right = self.meter_levels[index]
            if not self.show_activity_meters:
                left, right = 0.0, 0.0
            muted = self.muted_pages[self.page_index][index]
            self.meter_widgets[index].set_state(
                left,
                right,
                self.volume_levels[index],
                normalise_colour(channel.get("color"), index),
                bool(self.targets[index]) and channel.get("kind") != "disabled",
                muted,
                self.meter_style,
            )

        def refresh_meter_state(self) -> bool:
            if self.level_monitor is not None and self.show_activity_meters:
                self.meter_levels = self.level_monitor.levels(self.volume_levels)
            else:
                self.meter_levels = [(0.0, 0.0)] * 4
            for index in range(4):
                self.update_meter_widget(index)
            return True

        def set_meter_volume_from_y(self, meter, y: float, index: int) -> None:
            if not meter.get_sensitive() or meter.get_height() <= 0:
                return
            percentage = 100.0 * (1.0 - y / meter.get_height())
            self.channel_scales[index].set_value(
                max(0.0, min(100.0, round(percentage)))
            )

        def on_meter_drag_begin(self, gesture, _x: float, y: float, index: int) -> None:
            self.meter_drag_origins[index] = y
            self.set_meter_volume_from_y(gesture.get_widget(), y, index)

        def on_meter_pressed(
            self, gesture, _press_count: int, _x: float, y: float, index: int
        ) -> None:
            self.set_meter_volume_from_y(gesture.get_widget(), y, index)

        def on_meter_drag_update(
            self, gesture, _offset_x: float, offset_y: float, index: int
        ) -> None:
            self.set_meter_volume_from_y(
                gesture.get_widget(),
                self.meter_drag_origins[index] + offset_y,
                index,
            )

        def on_meter_scroll(
            self, controller, _delta_x: float, delta_y: float, index: int
        ) -> bool:
            meter = controller.get_widget()
            if not meter.get_sensitive() or delta_y == 0:
                return False
            adjustment = self.channel_scales[index]
            adjustment.set_value(
                max(0.0, min(100.0, adjustment.get_value() - delta_y * 3.0))
            )
            return True

        def on_volume_changed(self, scale, index: int) -> None:
            if self.updating_widgets:
                return
            self.volume_levels[index] = scale.get_value() / 100.0
            self.volume_labels[index].set_text(f"{round(scale.get_value())}%")
            self.update_meter_widget(index)
            pending = self.pending_volume_updates[index]
            if pending is not None:
                GLib.source_remove(pending)
            self.pending_volume_updates[index] = GLib.timeout_add(
                75, self.apply_pending_volume, index
            )

        def apply_pending_volume(self, index: int) -> bool:
            self.pending_volume_updates[index] = None
            targets = self.targets[index]
            level = self.channel_scales[index].get_value() / 100.0
            if not targets:
                return False
            if set_absolute_volume(targets, level):
                self.muted_pages[self.page_index][index] = False
                if level > 0:
                    self.saved_level_pages[self.page_index][index] = level
                self.volume_levels[index] = level
                self.updating_widgets = True
                self.mute_buttons[index].set_active(False)
                self.mute_buttons[index].set_label("Mute")
                self.mute_buttons[index].remove_css_class("destructive-action")
                self.updating_widgets = False
                self.update_meter_widget(index)
            else:
                self.show_message(
                    f"Could not change Control {index + 1} volume.", error=True
                )
            return False

        def on_mute_toggled(self, button, index: int) -> None:
            if self.updating_widgets:
                return
            targets = self.targets[index]
            if not targets:
                return
            muted = button.get_active()
            saved_levels = self.saved_level_pages[self.page_index]
            if muted:
                levels = [
                    level
                    for target in targets
                    if (level := read_volume(target)) is not None
                ]
                if levels and max(levels) > 0:
                    saved_levels[index] = max(levels)
                level = 0.0
            else:
                level = saved_levels[index]
            if set_absolute_volume(targets, level):
                self.muted_pages[self.page_index][index] = muted
                self.updating_widgets = True
                button.set_label("Unmute" if muted else "Mute")
                if muted:
                    button.add_css_class("destructive-action")
                    self.channel_scales[index].set_value(0)
                    self.volume_labels[index].set_text("0%")
                    self.volume_levels[index] = 0.0
                else:
                    button.remove_css_class("destructive-action")
                    self.channel_scales[index].set_value(level * 100)
                    self.volume_labels[index].set_text(f"{round(level * 100)}%")
                    self.volume_levels[index] = level
                self.updating_widgets = False
                self.update_meter_widget(index)
            else:
                self.updating_widgets = True
                button.set_active(not muted)
                self.updating_widgets = False
                self.show_message(
                    f"Could not {'mute' if muted else 'unmute'} Control {index + 1}.",
                    error=True,
                )

        def on_close_request(self, _window) -> bool:
            if self.level_monitor is not None:
                self.level_monitor.close()
                self.level_monitor = None
            return False

        def on_action_clicked(self, _button, index: int) -> None:
            page = self.pages[self.page_index]
            action = page["button_actions"][index]
            if action == "next_page":
                self.switch_page(1)
                return
            if action == "previous_page":
                self.switch_page(-1)
                return
            if action == "set_channel_volume":
                preset = page["button_volume_presets"][index]
                channel = preset["channel"] - 1
                targets = self.targets[channel]
                level = preset["percentage"] / 100.0
                if targets and set_absolute_volume(targets, level):
                    self.muted_pages[self.page_index][channel] = False
                    self.saved_level_pages[self.page_index][channel] = level
                    self.show_message(
                        f"Control {channel + 1} set to {preset['percentage']}%."
                    )
                    self.refresh_audio_state()
                else:
                    self.show_message(
                        f"Control {channel + 1} has no active audio target.",
                        error=True,
                    )
                return
            succeeded, message = run_programmable_action(action)
            self.show_message(message, error=not succeeded)

    return VirtualMixerWindow


def main() -> int:
    if os.name != "posix":
        print("The OpenStream100 virtual mixer is intended for Linux.", file=sys.stderr)
        return 2
    if shutil.which("pw-dump") is None or shutil.which("wpctl") is None:
        print("PipeWire and WirePlumber command-line tools are required.", file=sys.stderr)
        return 2
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError) as error:
        print("GTK4 and python3-gobject are required for the virtual mixer.", file=sys.stderr)
        print(error, file=sys.stderr)
        return 2

    css = Gtk.CssProvider()
    css.load_from_data(
        b"""
        window {
            background-color: @theme_bg_color;
            color: @theme_fg_color;
        }
        .mixer-scroller, .mixer-surface {
            background-color: transparent;
        }
        .status-banner {
            background-color: alpha(@theme_fg_color, 0.05);
            border: 1px solid alpha(@theme_fg_color, 0.18);
            border-radius: 10px;
            padding: 7px 10px;
            color: @theme_fg_color;
        }
        .channel-card {
            background-color: @theme_base_color;
            border: 1px solid alpha(@theme_fg_color, 0.18);
            border-radius: 8px;
            padding: 0 10px 10px 10px;
            color: @theme_text_color;
        }
        .action-strip {
            background-color: alpha(@theme_base_color, 0.90);
            border: 1px solid alpha(@theme_fg_color, 0.18);
            border-radius: 8px;
            padding: 7px;
        }
        .channel-title {
            color: @theme_text_color;
            font-size: 1.15em;
            font-weight: 700;
        }
        .channel-icon { color: @theme_text_color; }
        .caption {
            color: alpha(@theme_text_color, 0.65);
            font-size: 0.8em;
            font-weight: 700;
        }
        .volume-readout {
            color: @theme_text_color;
            font-size: 1.45em;
            font-weight: 700;
        }
        .compact .channel-card {
            padding: 0 7px 7px 7px;
        }
        .compact .channel-title {
            font-size: 0.95em;
        }
        .compact .caption {
            font-size: 0.72em;
        }
        .compact .volume-readout {
            font-size: 1.18em;
        }
        .compact .action-strip {
            padding: 5px;
        }
        .resize-grip {
            background-color: alpha(@theme_bg_color, 0.88);
            border-color: alpha(@theme_fg_color, 0.22);
            border-style: solid;
            border-width: 1px 0 0 1px;
            border-radius: 8px 0 0 0;
            color: @theme_fg_color;
        }
        .resize-grip.on-background {
            background-color: rgba(24, 31, 42, 0.84);
            border-color: rgba(239, 244, 249, 0.24);
            color: #eff4f9;
        }
        .success { font-weight: 700; }
        .error { font-weight: 700; }
        .mixer-surface.has-background {
            background-color: rgba(10, 14, 20, 0.16);
            color: #eff4f9;
        }
        .has-background .status-banner,
        .has-background .action-strip {
            background-color: rgba(24, 31, 42, 0.84);
            border-color: rgba(239, 244, 249, 0.22);
            color: #eff4f9;
        }
        .has-background .channel-card {
            background-color: rgba(24, 31, 42, 0.58);
            border-color: rgba(239, 244, 249, 0.18);
            color: #eff4f9;
        }
        .has-background .channel-title,
        .has-background .channel-icon,
        .has-background .volume-readout {
            color: #eff4f9;
        }
        .has-background .caption,
        .has-background .dim-label {
            color: rgba(239, 244, 249, 0.70);
        }
        """
    )
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    application = Gtk.Application(application_id=APP_ID)
    VirtualMixerWindow = make_window_class(Gtk, GLib, Gdk)

    def activate(app) -> None:
        window = app.get_active_window()
        if window is None:
            window = VirtualMixerWindow(app)
        window.present()

    application.connect("activate", activate)
    return application.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
