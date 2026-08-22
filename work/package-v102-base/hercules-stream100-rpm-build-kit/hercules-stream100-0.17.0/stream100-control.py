#!/usr/bin/python3
"""Friendly GTK control panel for the OpenStream100 mixer service."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import webbrowser


APP_ID = "com.hercules.Stream100"
APP_NAME = "OpenStream100"
try:
    from stream100_version import VERSION as APP_VERSION
except ImportError:
    APP_VERSION = "unknown"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path.home() / ".config" / "hercules-stream100" / "config.json"
BACKGROUND_PATH = CONFIG_PATH.with_name("background.png")
FULLSCREEN_IMAGE_PATH = CONFIG_PATH.with_name("fullscreen-image.png")
CUSTOM_BUTTON_OVERLAY_PATH = CONFIG_PATH.with_name("button-overlay-custom.png")
BUTTON_OVERLAY_TEMPLATE_PATH = APP_DIR / "button_labels_overlay_template.png"
BUTTON_OVERLAY_SIZE = (480, 80)
BUTTON_OVERLAY_STYLES = ("boxes", "basic", "glass", "custom")
DISPLAY_MODES = ("mixer", "image", "notepad")
NOTEPAD_FONT_FAMILIES = ("sans", "serif", "monospace")
NOTEPAD_FONT_FAMILY_LABELS = ("Sans", "Serif", "Monospace")
NOTEPAD_FONT_STYLES = ("regular", "bold", "italic", "bold-italic")
NOTEPAD_FONT_STYLE_LABELS = ("Regular", "Bold", "Italic", "Bold italic")
NOTEPAD_ALIGNMENTS = ("left", "center", "right")
NOTEPAD_ALIGNMENT_LABELS = ("Left", "Centre", "Right")
DEFAULT_NOTEPAD_TEXT_COLOUR = "#EFF4F9"
MIN_NOTEPAD_FONT_SIZE = 10
MAX_NOTEPAD_FONT_SIZE = 40
DEFAULT_NOTEPAD_STYLE: dict[str, object] = {
    "font_size": 0,
    "font_family": "sans",
    "font_style": "regular",
    "text_color": DEFAULT_NOTEPAD_TEXT_COLOUR,
    "alignment": "left",
}
DEFAULT_SHOW_VOLUME_METERS = True
DEFAULT_SHOW_CHANNEL_ICONS = True
VOLUME_METER_MODES = ("activity", "volume")
DEFAULT_VOLUME_METER_MODE = "activity"
METER_CHANNEL_MODES = ("stereo", "mono")
DEFAULT_METER_CHANNEL_MODE = "stereo"
METER_STYLES = ("classic", "segmented", "rounded", "slim")
DEFAULT_METER_STYLE = "classic"
DEFAULT_KNOB_SENSITIVITY = 1.0
MIN_KNOB_SENSITIVITY = 0.5
MAX_KNOB_SENSITIVITY = 4.0
KNOB_SENSITIVITY_STEP = 0.5
DEFAULT_DISPLAY_BRIGHTNESS = 100
MIN_DISPLAY_BRIGHTNESS = 10
MAX_DISPLAY_BRIGHTNESS = 100
DISPLAY_BRIGHTNESS_STEP = 5
DEFAULT_REMOTE_PORT = 47680
MAX_MIXER_PAGES = 8
BUTTON_ACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("disabled", "Do nothing"),
    ("microphone_mute", "Mute / unmute microphone"),
    ("speaker_mute", "Mute / unmute speakers"),
    ("play_pause", "Play / pause media"),
    ("previous_track", "Previous media track"),
    ("next_track", "Next media track"),
    ("set_channel_volume", "Set channel to preset volume"),
    ("next_page", "Next mixer page"),
    ("previous_page", "Previous mixer page"),
)
BUTTON_ACTION_IDS = tuple(action for action, _label in BUTTON_ACTION_CHOICES)
DEFAULT_BUTTON_ACTIONS = ["disabled", "disabled", "disabled", "disabled"]
DEFAULT_BUTTON_VOLUME_PRESETS = [
    {"channel": 1, "percentage": 50},
    {"channel": 2, "percentage": 50},
    {"channel": 3, "percentage": 50},
    {"channel": 4, "percentage": 50},
]
SERVICE_NAME = "hercules-stream100.service"
SERVICE_PATH = Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME
SYSTEM_SERVICE_PATH = Path("/usr/lib/systemd/user") / SERVICE_NAME
MIXER_RUNNER = APP_DIR / "run-stream100-mixer.sh"
PACKAGED_MIXER_RUNNER = Path(
    "/usr/libexec/hercules-stream100/run-stream100-mixer.sh"
)
VIRTUAL_MIXER_RUNNER = APP_DIR / "run-stream100-virtual-mixer.sh"
PACKAGED_VIRTUAL_MIXER_RUNNER = Path(
    "/usr/libexec/hercules-stream100/run-stream100-virtual-mixer.sh"
)
DEFAULT_CHANNEL_COLOURS = ["#30CCBE", "#36D380", "#F6BE40", "#5B82F6"]
DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"kind": "default", "label": "Default output device", "color": DEFAULT_CHANNEL_COLOURS[0]},
    {"kind": "disabled", "label": "Disabled", "color": DEFAULT_CHANNEL_COLOURS[1]},
    {"kind": "disabled", "label": "Disabled", "color": DEFAULT_CHANNEL_COLOURS[2]},
    {"kind": "disabled", "label": "Disabled", "color": DEFAULT_CHANNEL_COLOURS[3]},
]


def command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def find_virtual_mixer_runner(
    local_runner: Path = VIRTUAL_MIXER_RUNNER,
    packaged_runner: Path = PACKAGED_VIRTUAL_MIXER_RUNNER,
) -> Path | None:
    """Find the virtual mixer beside the GUI or in the packaged installation."""
    for runner in (local_runner, packaged_runner):
        if runner.is_file():
            return runner
    return None


def launch_virtual_mixer(runner: Path | None = None) -> tuple[bool, str]:
    """Start the independent virtual mixer from a GUI-safe session."""
    selected = runner if runner is not None else find_virtual_mixer_runner()
    if selected is None or not selected.is_file():
        return False, "The virtual mixer launcher is missing."
    try:
        subprocess.Popen([str(selected)], start_new_session=True)
    except OSError as error:
        return False, f"Could not open the virtual mixer: {error}"
    return True, "Virtual mixer opened with the saved pages."


def parse_pipewire_applications(document: str) -> list[dict[str, str]]:
    try:
        objects = json.loads(document)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"PipeWire returned invalid information: {error}") from error
    if not isinstance(objects, list):
        raise RuntimeError("PipeWire returned an unexpected response")

    groups: dict[tuple[str, str], dict[str, str]] = {}
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

        label = (
            props.get("application.name")
            or props.get("node.description")
            or props.get("media.name")
            or str(match_value)
        )
        key = (match_property, str(match_value))
        groups.setdefault(
            key,
            {
                "kind": "application",
                "label": str(label),
                "property": match_property,
                "value": str(match_value),
            },
        )
    return sorted(groups.values(), key=lambda item: item["label"].casefold())


def discover_applications() -> list[dict[str, str]]:
    result = command(["pw-dump"])
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "Could not read the active PipeWire applications"
        )
    return parse_pipewire_applications(result.stdout)


def normalise_colour(value: object, index: int) -> str:
    text = str(value).strip().upper()
    if (
        len(text) == 7
        and text.startswith("#")
        and all(character in "0123456789ABCDEF" for character in text[1:])
    ):
        return text
    return DEFAULT_CHANNEL_COLOURS[index]


def normalise_notepad_text_colour(value: object) -> str:
    text = str(value).strip().upper()
    if (
        len(text) == 7
        and text.startswith("#")
        and all(character in "0123456789ABCDEF" for character in text[1:])
    ):
        return text
    return DEFAULT_NOTEPAD_TEXT_COLOUR


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
    return {
        "font_size": int(font_size),
        "font_family": family if family in NOTEPAD_FONT_FAMILIES else "sans",
        "font_style": style if style in NOTEPAD_FONT_STYLES else "regular",
        "text_color": normalise_notepad_text_colour(source.get("text_color")),
        "alignment": alignment if alignment in NOTEPAD_ALIGNMENTS else "left",
    }


def normalise_channels(channels: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, channel in enumerate(channels):
        item = dict(channel)
        item["color"] = normalise_colour(item.get("color"), index)
        result.append(item)
    return result


def read_config_payload() -> dict[str, Any]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_config_payload(payload: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(CONFIG_PATH)


def load_channels() -> list[dict[str, str]]:
    payload = read_config_payload()
    channels = payload.get("channels")
    if payload.get("version") != 1 or not isinstance(channels, list) or len(channels) != 4:
        return [dict(channel) for channel in DEFAULT_CHANNELS]
    if not all(isinstance(channel, dict) for channel in channels):
        return [dict(channel) for channel in DEFAULT_CHANNELS]
    return normalise_channels(channels)


def save_channels(channels: list[dict[str, str]]) -> None:
    if len(channels) != 4:
        raise RuntimeError("Choose an assignment for all four controls")
    channels = normalise_channels(channels)
    payload = read_config_payload()
    payload["version"] = 1
    payload["channels"] = channels
    write_config_payload(payload)


def load_saved_image(key: str) -> Path | None:
    value = read_config_payload().get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value).expanduser()
    return path if path.is_file() else None


def save_saved_image(key: str, path: Path | None) -> None:
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    if path is None:
        payload.pop(key, None)
    else:
        payload[key] = str(path)
    write_config_payload(payload)


def import_saved_image(source: Path, destination: Path, key: str) -> Path:
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
    save_saved_image(key, destination)
    return destination


def load_background_path() -> Path | None:
    return load_saved_image("background_image")


def save_background_path(path: Path | None) -> None:
    save_saved_image("background_image", path)


def import_background(source: Path) -> Path:
    return import_saved_image(source, BACKGROUND_PATH, "background_image")


def load_fullscreen_image_path() -> Path | None:
    return load_saved_image("fullscreen_image")


def save_fullscreen_image_path(path: Path | None) -> None:
    save_saved_image("fullscreen_image", path)


def import_fullscreen_image(source: Path) -> Path:
    return import_saved_image(source, FULLSCREEN_IMAGE_PATH, "fullscreen_image")


def load_custom_button_overlay_path() -> Path | None:
    if not CUSTOM_BUTTON_OVERLAY_PATH.is_file():
        return None
    try:
        from PIL import Image

        with Image.open(CUSTOM_BUTTON_OVERLAY_PATH) as opened:
            if opened.format != "PNG" or opened.size != BUTTON_OVERLAY_SIZE:
                return None
            opened.verify()
    except (ImportError, OSError, ValueError):
        return None
    return CUSTOM_BUTTON_OVERLAY_PATH


def import_custom_button_overlay(source: Path) -> Path:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Image support is missing. Install python3-pillow.") from error

    try:
        with Image.open(source) as opened:
            if opened.format != "PNG":
                raise RuntimeError("Custom button overlays must be PNG images.")
            if opened.size != BUTTON_OVERLAY_SIZE:
                raise RuntimeError(
                    "Custom button overlays must be exactly 480 by 80 pixels."
                )
            image = opened.convert("RGBA")
            image.load()
    except RuntimeError:
        raise
    except (OSError, ValueError) as error:
        raise RuntimeError("That file could not be opened as a PNG image.") from error

    CUSTOM_BUTTON_OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CUSTOM_BUTTON_OVERLAY_PATH.with_suffix(".tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    temporary.replace(CUSTOM_BUTTON_OVERLAY_PATH)
    return CUSTOM_BUTTON_OVERLAY_PATH


def export_button_overlay_template(destination: Path) -> Path:
    if not BUTTON_OVERLAY_TEMPLATE_PATH.is_file():
        raise RuntimeError("The packaged button overlay template is missing.")
    if destination.suffix.casefold() != ".png":
        destination = destination.with_suffix(".png")
    try:
        shutil.copyfile(BUTTON_OVERLAY_TEMPLATE_PATH, destination)
    except OSError as error:
        raise RuntimeError(f"Could not save the overlay template: {error}") from error
    return destination


def load_display_mode() -> str:
    value = read_config_payload().get("display_mode", "mixer")
    return value if value in DISPLAY_MODES else "mixer"


def save_display_mode(mode: str) -> None:
    if mode not in DISPLAY_MODES:
        raise RuntimeError("Unsupported display mode")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["display_mode"] = mode
    write_config_payload(payload)


def load_notepad_text() -> str:
    value = read_config_payload().get("notepad_text", "")
    if not isinstance(value, str):
        return ""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def save_notepad_text(text: str) -> None:
    if not isinstance(text, str):
        raise RuntimeError("Notepad content must be text")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["notepad_text"] = text.replace("\r\n", "\n").replace("\r", "\n")
    write_config_payload(payload)


def load_notepad_style() -> dict[str, object]:
    return normalise_notepad_style(read_config_payload().get("notepad_style"))


def save_notepad_style(style: object) -> None:
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["notepad_style"] = normalise_notepad_style(style)
    write_config_payload(payload)


def load_show_volume_meters() -> bool:
    value = read_config_payload().get(
        "show_volume_meters", DEFAULT_SHOW_VOLUME_METERS
    )
    return value if isinstance(value, bool) else DEFAULT_SHOW_VOLUME_METERS


def save_show_volume_meters(value: object) -> None:
    if not isinstance(value, bool):
        raise RuntimeError("Volume meter visibility must be on or off")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["show_volume_meters"] = value
    write_config_payload(payload)


def load_meter_channel_mode() -> str:
    value = read_config_payload().get(
        "meter_channel_mode", DEFAULT_METER_CHANNEL_MODE
    )
    return value if value in METER_CHANNEL_MODES else DEFAULT_METER_CHANNEL_MODE


def save_meter_channel_mode(mode: str) -> None:
    if mode not in METER_CHANNEL_MODES:
        raise RuntimeError("Activity monitoring must be Stereo or Mono")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["meter_channel_mode"] = mode
    write_config_payload(payload)


def load_meter_style() -> str:
    value = read_config_payload().get("meter_style", DEFAULT_METER_STYLE)
    return value if value in METER_STYLES else DEFAULT_METER_STYLE


def save_meter_style(style: str) -> None:
    if style not in METER_STYLES:
        raise RuntimeError("Unsupported visualiser style")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["meter_style"] = style
    write_config_payload(payload)


def load_show_channel_icons() -> bool:
    value = read_config_payload().get(
        "show_channel_icons", DEFAULT_SHOW_CHANNEL_ICONS
    )
    return value if isinstance(value, bool) else DEFAULT_SHOW_CHANNEL_ICONS


def save_show_channel_icons(value: object) -> None:
    if not isinstance(value, bool):
        raise RuntimeError("Channel icon visibility must be on or off")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["show_channel_icons"] = value
    write_config_payload(payload)


def load_remote_enabled() -> bool:
    value = read_config_payload().get("remote_enabled", False)
    return value if isinstance(value, bool) else False


def save_remote_enabled(value: object) -> None:
    if not isinstance(value, bool):
        raise RuntimeError("Remote control must be on or off")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["remote_enabled"] = value
    write_config_payload(payload)


def remote_admin_request(path: str, method: str = "GET") -> dict[str, Any]:
    """Call one loopback-only endpoint exposed by the running mixer."""
    request = Request(
        f"http://127.0.0.1:{DEFAULT_REMOTE_PORT}/api/v1/admin{path}",
        data=b"" if method == "POST" else None,
        method=method,
        headers={"Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=0.8) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        try:
            payload = json.loads(error.read())
            message = payload.get("error") if isinstance(payload, dict) else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = None
        raise RuntimeError(message or f"Remote service returned HTTP {error.code}") from error
    except (OSError, URLError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("The remote service is not available yet") from error
    if not isinstance(payload, dict):
        raise RuntimeError("The remote service returned an invalid response")
    return payload


def load_button_overlay_style() -> str:
    value = read_config_payload().get("button_overlay_style", "boxes")
    if value in BUTTON_OVERLAY_STYLES:
        return value
    return "boxes"


def save_button_overlay_style(value: str) -> None:
    if value not in BUTTON_OVERLAY_STYLES:
        raise RuntimeError("Unsupported button overlay style")
    payload = read_config_payload()
    payload["button_overlay_style"] = value
    write_config_payload(payload)


def load_volume_meter_mode() -> str:
    # The white volume marker and live VU bars now operate simultaneously.
    # Treat the former either/or preference as the combined live mode.
    return "activity"


def save_volume_meter_mode(mode: str) -> None:
    if mode not in VOLUME_METER_MODES:
        raise RuntimeError("Unsupported volume bar movement")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["volume_meter_mode"] = mode
    write_config_payload(payload)


def normalise_knob_sensitivity(value: object) -> float:
    try:
        sensitivity = float(value)
    except (TypeError, ValueError):
        return DEFAULT_KNOB_SENSITIVITY
    if not math.isfinite(sensitivity):
        return DEFAULT_KNOB_SENSITIVITY
    if not MIN_KNOB_SENSITIVITY <= sensitivity <= MAX_KNOB_SENSITIVITY:
        return DEFAULT_KNOB_SENSITIVITY
    steps = round(sensitivity / KNOB_SENSITIVITY_STEP)
    return steps * KNOB_SENSITIVITY_STEP


def load_knob_sensitivity() -> float:
    return normalise_knob_sensitivity(
        read_config_payload().get("knob_sensitivity", DEFAULT_KNOB_SENSITIVITY)
    )


def save_knob_sensitivity(value: object) -> None:
    sensitivity = normalise_knob_sensitivity(value)
    try:
        requested = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError("Choose a knob sensitivity between 0.5% and 4.0%") from error
    if not math.isfinite(requested) or abs(requested - sensitivity) > 0.001:
        raise RuntimeError("Choose a knob sensitivity between 0.5% and 4.0%")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["knob_sensitivity"] = sensitivity
    write_config_payload(payload)


def normalise_display_brightness(value: object) -> int:
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


def load_display_brightness() -> int:
    return normalise_display_brightness(
        read_config_payload().get("display_brightness", DEFAULT_DISPLAY_BRIGHTNESS)
    )


def save_display_brightness(value: object) -> None:
    brightness = normalise_display_brightness(value)
    try:
        requested = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError("Choose a screen brightness between 10% and 100%") from error
    if not math.isfinite(requested) or abs(requested - brightness) > 0.001:
        raise RuntimeError("Choose a screen brightness between 10% and 100%")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["display_brightness"] = brightness
    write_config_payload(payload)


def load_button_actions() -> list[str]:
    actions = read_config_payload().get("button_actions")
    if not isinstance(actions, list) or len(actions) != 4:
        return list(DEFAULT_BUTTON_ACTIONS)
    if not all(isinstance(action, str) and action in BUTTON_ACTION_IDS for action in actions):
        return list(DEFAULT_BUTTON_ACTIONS)
    return list(actions)


def save_button_actions(actions: list[str]) -> None:
    if len(actions) != 4 or not all(action in BUTTON_ACTION_IDS for action in actions):
        raise RuntimeError("Choose a function for all four programmable buttons")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["button_actions"] = list(actions)
    write_config_payload(payload)


def normalise_button_volume_presets(value: object) -> list[dict[str, int]]:
    defaults = [dict(preset) for preset in DEFAULT_BUTTON_VOLUME_PRESETS]
    if not isinstance(value, list) or len(value) != 4:
        return defaults
    presets: list[dict[str, int]] = []
    for item in value:
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


def load_button_volume_presets() -> list[dict[str, int]]:
    return normalise_button_volume_presets(
        read_config_payload().get("button_volume_presets")
    )


def save_button_volume_presets(presets: list[dict[str, int]]) -> None:
    normalised = normalise_button_volume_presets(presets)
    if normalised != presets:
        raise RuntimeError("Choose a control and a volume from 0% to 100% for each button")
    payload = read_config_payload()
    if payload.get("version") != 1 or not isinstance(payload.get("channels"), list):
        payload["version"] = 1
        payload["channels"] = normalise_channels(
            [dict(channel) for channel in DEFAULT_CHANNELS]
        )
    payload["button_volume_presets"] = normalised
    write_config_payload(payload)


def default_mixer_page() -> dict[str, Any]:
    return {
        "channels": [
            {
                "kind": "disabled",
                "label": "Disabled",
                "color": DEFAULT_CHANNEL_COLOURS[index],
            }
            for index in range(4)
        ],
        "button_actions": list(DEFAULT_BUTTON_ACTIONS),
        "button_volume_presets": [
            dict(preset) for preset in DEFAULT_BUTTON_VOLUME_PRESETS
        ],
    }


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
    ):
        return None
    normalised_presets = normalise_button_volume_presets(presets)
    if normalised_presets != presets:
        return None
    return {
        "channels": normalise_channels(channels),
        "button_actions": list(actions),
        "button_volume_presets": normalised_presets,
    }


def load_mixer_pages() -> list[dict[str, Any]]:
    payload = read_config_payload()
    raw_pages = payload.get("pages")
    if isinstance(raw_pages, list) and 1 <= len(raw_pages) <= MAX_MIXER_PAGES:
        pages = [normalise_mixer_page(page) for page in raw_pages]
        if all(page is not None for page in pages):
            return [page for page in pages if page is not None]

    channels = load_channels()
    actions = load_button_actions()
    presets = load_button_volume_presets()
    return [
        {
            "channels": channels,
            "button_actions": actions,
            "button_volume_presets": presets,
        }
    ]


def save_mixer_pages(pages: list[dict[str, Any]]) -> None:
    if not 1 <= len(pages) <= MAX_MIXER_PAGES:
        raise RuntimeError(f"Choose between 1 and {MAX_MIXER_PAGES} mixer pages")
    normalised = [normalise_mixer_page(page) for page in pages]
    if any(page is None for page in normalised):
        raise RuntimeError("Every mixer page must contain four valid controls and buttons")
    saved_pages = [page for page in normalised if page is not None]
    payload = read_config_payload()
    payload["version"] = 1
    payload["pages"] = saved_pages
    # Keep the original top-level keys synchronized with Page 1 so older
    # OpenStream100 builds can still open the configuration safely.
    payload["channels"] = saved_pages[0]["channels"]
    payload["button_actions"] = saved_pages[0]["button_actions"]
    payload["button_volume_presets"] = saved_pages[0][
        "button_volume_presets"
    ]
    write_config_payload(payload)


def choice_key(choice: dict[str, str]) -> tuple[str, str, str]:
    return (
        choice.get("kind", "disabled"),
        choice.get("property", ""),
        choice.get("value", ""),
    )


def build_choices(
    applications: list[dict[str, str]], saved_channels: list[dict[str, str]]
) -> list[dict[str, str]]:
    fixed = [
        {"kind": "disabled", "label": "Disabled"},
        {"kind": "default", "label": "Default output device"},
    ]
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for choice in [*saved_channels, *applications]:
        if choice.get("kind") == "application":
            unique.setdefault(choice_key(choice), dict(choice))
    saved_app_keys = {
        choice_key(choice)
        for choice in saved_channels
        if choice.get("kind") == "application"
    }
    active_keys = {choice_key(choice) for choice in applications}
    for key, choice in unique.items():
        if key in saved_app_keys and key not in active_keys:
            choice["display_label"] = (
                f"{choice.get('label', 'Application')} (not currently playing)"
            )
    return [
        *fixed,
        *sorted(unique.values(), key=lambda item: item.get("label", "").casefold()),
    ]


def systemd_quote(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{value}"'


def service_unit_text() -> str:
    return "\n".join(
        (
            "[Unit]",
            f"Description={APP_NAME} PipeWire mixer",
            "Requires=hercules-stream100-display.service",
            "After=pipewire.service wireplumber.service hercules-stream100-display.service",
            "",
            "[Service]",
            "Type=simple",
            # Include --require-display-broker to match the shipped system unit.
            # ExecStart is fully replaced by a user override; merging won't work.
            f"ExecStart=/usr/bin/bash {systemd_quote(MIXER_RUNNER)} --require-display-broker",
            "KillMode=mixed",
            "TimeoutStopSec=15s",
            "Restart=on-failure",
            "RestartSec=3",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        )
    )


def ensure_display_broker_enabled() -> None:
    """Ensure the persistent display broker service is enabled."""
    display_service = "hercules-stream100-display.service"
    # Check if already enabled (symlink exists in wants dir)
    wants_dir = Path.home() / ".config" / "systemd" / "user" / "wants"
    if (wants_dir / display_service).exists():
        return
    result = command(["systemctl", "--user", "enable", display_service])
    if result.returncode != 0:
        # Non-fatal: the mixer can still work with an inline display helper
        pass


def ensure_service_unit() -> None:
    if not MIXER_RUNNER.exists():
        raise RuntimeError("The mixer launcher is missing from the application folder")
    expected = service_unit_text()
    # Always manage via the user override so the control app can start/stop
    # the mixer regardless of what the system-level unit contains.
    current = ""
    try:
        current = SERVICE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    if current == expected:
        return
    SERVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERVICE_PATH.write_text(expected, encoding="utf-8")
    result = command(["systemctl", "--user", "daemon-reload"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not refresh the user service")
    # Also ensure the display broker dependency is enabled
    ensure_display_broker_enabled()


def migration_backup_path() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    candidate = data_home / "hercules-stream100-pre-rpm-backup"
    suffix = 1
    while candidate.exists():
        candidate = data_home / f"hercules-stream100-pre-rpm-backup-{suffix}"
        suffix += 1
    return candidate


def migrate_user_install() -> list[str]:
    """Move the earlier per-user installation aside without deleting user data."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    old_install = data_home / "hercules-stream100"
    old_desktop = data_home / "applications" / "com.hercules.Stream100.desktop"
    old_icon = (
        data_home
        / "icons"
        / "hicolor"
        / "scalable"
        / "apps"
        / "com.hercules.Stream100.svg"
    )
    old_items = [old_install, old_desktop, old_icon, SERVICE_PATH]
    existing = [path for path in old_items if path.exists() or path.is_symlink()]
    if not existing:
        command(["systemctl", "--user", "daemon-reload"])
        return ["No previous personal installation was found."]

    was_enabled = service_is_enabled()
    was_active = service_property("ActiveState") == "active"
    if was_active:
        stop_result = command(["systemctl", "--user", "stop", SERVICE_NAME])
        if stop_result.returncode != 0:
            raise RuntimeError(stop_result.stderr.strip() or "Could not stop the old mixer")
    if was_enabled:
        disable_result = command(["systemctl", "--user", "disable", SERVICE_NAME])
        if disable_result.returncode != 0:
            raise RuntimeError(
                disable_result.stderr.strip() or "Could not disable the old mixer service"
            )

    backup = migration_backup_path()
    integration_backup = backup / "user-integration"
    integration_backup.mkdir(parents=True, exist_ok=False)
    if old_install.exists():
        shutil.move(str(old_install), str(backup / "application"))
    for path in (old_desktop, old_icon, SERVICE_PATH):
        if path.exists() or path.is_symlink():
            destination = integration_backup / path.name
            shutil.move(str(path), str(destination))

    reload_result = command(["systemctl", "--user", "daemon-reload"])
    if reload_result.returncode != 0:
        raise RuntimeError(
            reload_result.stderr.strip() or "Could not refresh the packaged user service"
        )
    if was_enabled:
        enable_result = command(["systemctl", "--user", "enable", SERVICE_NAME])
        if enable_result.returncode != 0:
            raise RuntimeError(enable_result.stderr.strip() or "Could not restore automatic startup")
    if was_active:
        start_result = command(["systemctl", "--user", "start", SERVICE_NAME])
        if start_result.returncode != 0:
            raise RuntimeError(start_result.stderr.strip() or "Could not restart the mixer")
    return [
        f"Previous personal installation moved to {backup}",
        "Saved assignments, colours, background, and button calibration were retained.",
    ]


def service_property(name: str) -> str:
    result = command(
        ["systemctl", "--user", "show", SERVICE_NAME, f"--property={name}", "--value"]
    )
    if result.returncode == 0:
        return result.stdout.strip()
    # Fallback: check the process directly when D-Bus/systemd is unavailable
    if name == "ActiveState":
        import subprocess as _sub
        _r = _sub.run(
            ["pgrep", "-f", "stream100-mixer.py"],
            capture_output=True, text=True
        )
        return "active" if _r.returncode == 0 else "inactive"
    return "unknown"


def service_is_enabled() -> bool:
    result = command(["systemctl", "--user", "is-enabled", "--quiet", SERVICE_NAME])
    if result.returncode == 0:
        return True
    # Fallback: check for the symlink in the user wants directory
    wants_dir = Path.home() / ".config" / "systemd" / "user" / "wants"
    return (wants_dir / SERVICE_NAME).exists()


def service_action(action: str) -> None:
    if action not in {"start", "stop", "restart", "enable", "disable"}:
        raise RuntimeError("Unsupported service action")
    ensure_service_unit()
    result = command(["systemctl", "--user", action, SERVICE_NAME])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Could not {action} the mixer")


def device_connected() -> bool:
    root = Path("/sys/bus/usb/devices")
    try:
        vendor_files = list(root.glob("*/idVendor"))
    except OSError:
        return False
    for vendor_file in vendor_files:
        try:
            if vendor_file.read_text().strip().casefold() != "06f8":
                continue
            product = vendor_file.with_name("idProduct").read_text().strip().casefold()
            if product == "e053":
                return True
        except OSError:
            continue
    return False


def make_window_class(Gtk, GLib, Gdk):
    class ControlWindow(Gtk.ApplicationWindow):
        def __init__(self, application):
            super().__init__(application=application)
            self.set_title(APP_NAME)
            self.set_default_size(720, 650)
            self.set_resizable(True)
            self.pages = load_mixer_pages()
            self.current_page_index = 0
            self.saved_channels = self.pages[0]["channels"]
            self.choices: list[dict[str, str]] = []
            self.dropdowns: list[Any] = []
            self.colour_buttons: list[Any] = []
            self.display_mode = load_display_mode()
            self.notepad_text = load_notepad_text()
            self.notepad_style = load_notepad_style()
            self.show_volume_meters = load_show_volume_meters()
            self.meter_channel_mode = load_meter_channel_mode()
            self.meter_style = load_meter_style()
            self.show_channel_icons = load_show_channel_icons()
            self.remote_enabled = load_remote_enabled()
            self.remote_device_signature: tuple[tuple[str, str, int], ...] | None = None
            self.remote_pairing_dialog = None
            self.last_remote_pairing_pin = ""
            self.custom_button_overlay_path = load_custom_button_overlay_path()
            self.button_overlay_style = load_button_overlay_style()
            if (
                self.button_overlay_style == "custom"
                and self.custom_button_overlay_path is None
            ):
                self.button_overlay_style = "boxes"
                save_button_overlay_style(self.button_overlay_style)
            self.knob_sensitivity = load_knob_sensitivity()
            self.display_brightness = load_display_brightness()
            self.saved_button_actions = self.pages[0]["button_actions"]
            self.saved_button_volume_presets = self.pages[0][
                "button_volume_presets"
            ]
            self.button_action_dropdowns: list[Any] = []
            self.button_preset_channel_dropdowns: list[Any] = []
            self.button_preset_spins: list[Any] = []
            self.background_path = load_background_path()
            self.fullscreen_image_path = load_fullscreen_image_path()
            self.background_chooser = None
            self.button_overlay_chooser = None
            self.button_overlay_template_chooser = None
            self.updating_switch = False
            self.updating_remote_switch = False
            self.switching_page = False

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            root.set_margin_top(24)
            root.set_margin_bottom(24)
            root.set_margin_start(28)
            root.set_margin_end(28)
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_child(root)
            self.set_child(scroller)

            title = Gtk.Label(label=APP_NAME)
            title.set_xalign(0)
            title.add_css_class("title-1")
            root.append(title)
            subtitle = Gtk.Label(
                label="Choose which PipeWire application each physical control manages."
            )
            subtitle.set_xalign(0)
            subtitle.set_wrap(True)
            subtitle.add_css_class("dim-label")
            root.append(subtitle)

            status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
            status_box.add_css_class("status-card")
            root.append(status_box)
            self.device_status = Gtk.Label(label="Checking controller…")
            self.device_status.set_xalign(0)
            self.device_status.set_hexpand(True)
            status_box.append(self.device_status)
            self.service_status = Gtk.Label(label="Checking mixer…")
            self.service_status.set_xalign(1)
            status_box.append(self.service_status)

            pages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            pages_title = Gtk.Label(label="Mixer pages")
            pages_title.set_xalign(0)
            pages_title.add_css_class("heading")
            pages_box.append(pages_title)
            pages_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            pages_label = Gtk.Label(label="Editing")
            pages_label.set_xalign(0)
            pages_label.set_hexpand(True)
            pages_row.append(pages_label)
            self.page_dropdown = Gtk.DropDown(
                model=Gtk.StringList.new(self.page_labels())
            )
            self.page_dropdown.set_selected(0)
            pages_row.append(self.page_dropdown)
            add_page = Gtk.Button(label="Add page")
            add_page.connect("clicked", self.on_add_page)
            pages_row.append(add_page)
            self.add_page_button = add_page
            self.remove_page_button = Gtk.Button(label="Remove page")
            self.remove_page_button.connect("clicked", self.on_remove_page)
            pages_row.append(self.remove_page_button)
            pages_box.append(pages_row)
            pages_hint = Gtk.Label(
                label=(
                    "Each page has four control and button assignments. Assign Next or "
                    "Previous mixer page to a hardware button to move between pages."
                )
            )
            pages_hint.set_xalign(0)
            pages_hint.set_wrap(True)
            pages_hint.add_css_class("dim-label")
            pages_box.append(pages_hint)
            root.append(pages_box)
            self.page_dropdown.connect(
                "notify::selected", self.on_page_changed
            )
            self.update_page_buttons()

            channels_title = Gtk.Label(label="Control assignments")
            channels_title.set_xalign(0)
            channels_title.add_css_class("heading")
            root.append(channels_title)

            grid = Gtk.Grid(column_spacing=16, row_spacing=12)
            grid.set_column_homogeneous(False)
            root.append(grid)
            application_heading = Gtk.Label(label="Application")
            application_heading.set_xalign(0)
            application_heading.add_css_class("dim-label")
            grid.attach(application_heading, 2, 0, 1, 1)
            colour_heading = Gtk.Label(label="Colour")
            colour_heading.set_xalign(0.5)
            colour_heading.add_css_class("dim-label")
            grid.attach(colour_heading, 3, 0, 1, 1)
            for index in range(4):
                row = index + 1
                number = Gtk.Label(label=str(index + 1))
                number.set_size_request(36, 36)
                number.add_css_class("channel-number")
                grid.attach(number, 0, row, 1, 1)
                label = Gtk.Label(label=f"Control {index + 1}")
                label.set_xalign(0)
                label.set_size_request(90, -1)
                grid.attach(label, 1, row, 1, 1)
                dropdown = Gtk.DropDown()
                dropdown.set_hexpand(True)
                grid.attach(dropdown, 2, row, 1, 1)
                self.dropdowns.append(dropdown)
                if hasattr(Gtk, "ColorDialogButton"):
                    dialog = Gtk.ColorDialog()
                    dialog.set_title(f"Choose a colour for control {index + 1}")
                    colour_button = Gtk.ColorDialogButton.new(dialog)
                else:
                    colour_button = Gtk.ColorButton()
                    colour_button.set_title(f"Choose a colour for control {index + 1}")
                colour_button.set_tooltip_text(f"Choose the display colour for control {index + 1}")
                colour_button.set_size_request(54, 36)
                grid.attach(colour_button, 3, row, 1, 1)
                self.colour_buttons.append(colour_button)

            hint_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hint = Gtk.Label(
                label="Only applications currently playing audio can be discovered."
            )
            hint.set_xalign(0)
            hint.set_hexpand(True)
            hint.set_wrap(True)
            hint.add_css_class("dim-label")
            hint_row.append(hint)
            refresh = Gtk.Button(label="Refresh applications")
            refresh.connect("clicked", self.on_refresh_clicked)
            hint_row.append(refresh)
            root.append(hint_row)

            sensitivity_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6
            )
            sensitivity_title = Gtk.Label(label="Knob sensitivity")
            sensitivity_title.set_xalign(0)
            sensitivity_title.add_css_class("heading")
            sensitivity_box.append(sensitivity_title)
            sensitivity_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            sensitivity_label = Gtk.Label(label="Volume change per movement")
            sensitivity_label.set_xalign(0)
            sensitivity_label.set_hexpand(True)
            sensitivity_row.append(sensitivity_label)
            self.sensitivity_spin = Gtk.SpinButton.new_with_range(
                MIN_KNOB_SENSITIVITY,
                MAX_KNOB_SENSITIVITY,
                KNOB_SENSITIVITY_STEP,
            )
            self.sensitivity_spin.set_digits(1)
            self.sensitivity_spin.set_numeric(True)
            self.sensitivity_spin.set_value(self.knob_sensitivity)
            self.sensitivity_spin.set_tooltip_text(
                "Higher percentages make every knob change volume faster"
            )
            sensitivity_row.append(self.sensitivity_spin)
            sensitivity_unit = Gtk.Label(label="%")
            sensitivity_row.append(sensitivity_unit)
            sensitivity_box.append(sensitivity_row)
            sensitivity_hint = Gtk.Label(
                label="1.0% matches the current response. Higher values change volume faster."
            )
            sensitivity_hint.set_xalign(0)
            sensitivity_hint.set_wrap(True)
            sensitivity_hint.add_css_class("dim-label")
            sensitivity_box.append(sensitivity_hint)
            root.append(sensitivity_box)

            buttons_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=8
            )
            buttons_title = Gtk.Label(label="Programmable buttons")
            buttons_title.set_xalign(0)
            buttons_title.add_css_class("heading")
            buttons_box.append(buttons_title)
            buttons_grid = Gtk.Grid(column_spacing=16, row_spacing=10)
            action_heading = Gtk.Label(label="Action")
            action_heading.set_xalign(0)
            action_heading.add_css_class("dim-label")
            buttons_grid.attach(action_heading, 2, 0, 1, 1)
            target_heading = Gtk.Label(label="Target")
            target_heading.set_xalign(0)
            target_heading.add_css_class("dim-label")
            buttons_grid.attach(target_heading, 3, 0, 1, 1)
            level_heading = Gtk.Label(label="Level")
            level_heading.set_xalign(0)
            level_heading.add_css_class("dim-label")
            buttons_grid.attach(level_heading, 4, 0, 2, 1)
            for index in range(4):
                row = index + 1
                number = Gtk.Label(label=str(index + 1))
                number.set_size_request(36, 36)
                number.add_css_class("channel-number")
                buttons_grid.attach(number, 0, row, 1, 1)
                label = Gtk.Label(label=f"Button {index + 1}")
                label.set_xalign(0)
                label.set_size_request(90, -1)
                buttons_grid.attach(label, 1, row, 1, 1)
                model = Gtk.StringList.new(
                    [label for _action, label in BUTTON_ACTION_CHOICES]
                )
                dropdown = Gtk.DropDown(model=model)
                dropdown.set_hexpand(True)
                dropdown.set_selected(
                    BUTTON_ACTION_IDS.index(self.saved_button_actions[index])
                )
                buttons_grid.attach(dropdown, 2, row, 1, 1)
                self.button_action_dropdowns.append(dropdown)
                channel_model = Gtk.StringList.new(
                    [f"Control {channel}" for channel in range(1, 5)]
                )
                channel_dropdown = Gtk.DropDown(model=channel_model)
                channel_dropdown.set_selected(
                    self.saved_button_volume_presets[index]["channel"] - 1
                )
                buttons_grid.attach(channel_dropdown, 3, row, 1, 1)
                self.button_preset_channel_dropdowns.append(channel_dropdown)
                preset_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
                preset_spin.set_digits(0)
                preset_spin.set_numeric(True)
                preset_spin.set_value(
                    self.saved_button_volume_presets[index]["percentage"]
                )
                preset_spin.set_width_chars(4)
                buttons_grid.attach(preset_spin, 4, row, 1, 1)
                self.button_preset_spins.append(preset_spin)
                preset_unit = Gtk.Label(label="%")
                preset_unit.set_xalign(0)
                buttons_grid.attach(preset_unit, 5, row, 1, 1)
                dropdown.connect(
                    "notify::selected", self.on_button_action_changed, index
                )
                self.update_button_preset_controls(index)
            buttons_box.append(buttons_grid)
            buttons_hint = Gtk.Label(
                label=(
                    "For a preset-volume action, choose its target control and exact level. "
                    "An assigned button stays illuminated while OpenStream100 is running."
                )
            )
            buttons_hint.set_xalign(0)
            buttons_hint.set_wrap(True)
            buttons_hint.add_css_class("dim-label")
            buttons_box.append(buttons_hint)
            root.append(buttons_box)

            screen_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            screen_title = Gtk.Label(label="Screen content")
            screen_title.set_xalign(0)
            screen_title.add_css_class("heading")
            screen_box.append(screen_title)
            mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            mode_label = Gtk.Label(label="Display mode")
            mode_label.set_xalign(0)
            mode_label.set_hexpand(True)
            mode_row.append(mode_label)
            mode_model = Gtk.StringList.new(["Mixer", "Full-screen image", "Notepad"])
            self.display_mode_dropdown = Gtk.DropDown(model=mode_model)
            self.display_mode_dropdown.set_selected(DISPLAY_MODES.index(self.display_mode))
            self.display_mode_dropdown.connect(
                "notify::selected", self.on_display_mode_changed
            )
            mode_row.append(self.display_mode_dropdown)
            screen_box.append(mode_row)
            mode_hint = Gtk.Label(
                label="Volume, mute, and programmable-button controls continue working in every mode."
            )
            mode_hint.set_xalign(0)
            mode_hint.set_wrap(True)
            mode_hint.add_css_class("dim-label")
            screen_box.append(mode_hint)
            meter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            meter_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            meter_text.set_hexpand(True)
            meter_title = Gtk.Label(label="Show volume and activity meters")
            meter_title.set_xalign(0)
            meter_description = Gtk.Label(
                label=(
                    "Keep the white marker at the selected volume and animate "
                    "the coloured bars from live audio."
                )
            )
            meter_description.set_xalign(0)
            meter_description.set_wrap(True)
            meter_description.add_css_class("dim-label")
            meter_text.append(meter_title)
            meter_text.append(meter_description)
            meter_row.append(meter_text)
            self.volume_meter_switch = Gtk.Switch()
            self.volume_meter_switch.set_valign(Gtk.Align.CENTER)
            self.volume_meter_switch.set_active(self.show_volume_meters)
            self.volume_meter_switch.connect(
                "notify::active", self.on_volume_meter_visibility_changed
            )
            meter_row.append(self.volume_meter_switch)
            self.volume_meter_row = meter_row
            screen_box.append(meter_row)

            meter_mode_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            meter_mode_text = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2
            )
            meter_mode_text.set_hexpand(True)
            meter_mode_title = Gtk.Label(label="Activity monitoring")
            meter_mode_title.set_xalign(0)
            meter_mode_description = Gtk.Label(
                label=(
                    "Stereo follows left and right independently. Mono mixes "
                    "to one level and mirrors it across both bars."
                )
            )
            meter_mode_description.set_xalign(0)
            meter_mode_description.set_wrap(True)
            meter_mode_description.add_css_class("dim-label")
            meter_mode_text.append(meter_mode_title)
            meter_mode_text.append(meter_mode_description)
            meter_mode_row.append(meter_mode_text)
            self.meter_channel_mode_combo = Gtk.ComboBoxText()
            self.meter_channel_mode_combo.append("stereo", "Stereo")
            self.meter_channel_mode_combo.append("mono", "Mono")
            self.meter_channel_mode_combo.set_active_id(self.meter_channel_mode)
            self.meter_channel_mode_combo.set_valign(Gtk.Align.CENTER)
            self.meter_channel_mode_combo.connect(
                "changed", self.on_meter_channel_mode_changed
            )
            meter_mode_row.append(self.meter_channel_mode_combo)
            self.meter_channel_mode_row = meter_mode_row
            screen_box.append(meter_mode_row)

            meter_style_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            meter_style_text = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2
            )
            meter_style_text.set_hexpand(True)
            meter_style_title = Gtk.Label(label="Visualiser style")
            meter_style_title.set_xalign(0)
            meter_style_description = Gtk.Label(
                label=(
                    "Choose one of the four full-height animated meter styles "
                    "built into the Stream 100 firmware."
                )
            )
            meter_style_description.set_xalign(0)
            meter_style_description.set_wrap(True)
            meter_style_description.add_css_class("dim-label")
            meter_style_text.append(meter_style_title)
            meter_style_text.append(meter_style_description)
            meter_style_row.append(meter_style_text)
            self.meter_style_combo = Gtk.ComboBoxText()
            self.meter_style_combo.append("classic", "Classic")
            self.meter_style_combo.append("segmented", "Segmented")
            self.meter_style_combo.append("rounded", "Rounded")
            self.meter_style_combo.append("slim", "Slim")
            self.meter_style_combo.set_active_id(self.meter_style)
            self.meter_style_combo.set_valign(Gtk.Align.CENTER)
            self.meter_style_combo.connect(
                "changed", self.on_meter_style_changed
            )
            meter_style_row.append(self.meter_style_combo)
            self.meter_style_row = meter_style_row
            screen_box.append(meter_style_row)

            # Channel icons toggle
            icons_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            icons_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            icons_title = Gtk.Label(label="Show channel application icons")
            icons_title.set_xalign(0)
            icons_description = Gtk.Label(
                label=(
                    "Display a small icon badge at the top-right of each mixer "
                    "column. Uses the system icon theme for applications, "
                    "inputs, and outputs with a crisp built-in fallback."
                )
            )
            icons_description.set_xalign(0)
            icons_description.set_wrap(True)
            icons_description.add_css_class("dim-label")
            icons_text.append(icons_title)
            icons_text.append(icons_description)
            icons_row.append(icons_text)
            self.channel_icons_switch = Gtk.Switch()
            self.channel_icons_switch.set_valign(Gtk.Align.CENTER)
            self.channel_icons_switch.set_active(self.show_channel_icons)
            self.channel_icons_switch.connect(
                "notify::active", self.on_channel_icons_visibility_changed
            )
            icons_row.append(self.channel_icons_switch)
            screen_box.append(icons_row)

            # Button overlay style selector
            overlay_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            overlay_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            overlay_title = Gtk.Label(label="Button overlay style")
            overlay_title.set_xalign(0)
            overlay_description = Gtk.Label(
                label=(
                    "Choose Boxes, Basic, Glass, or an imported Custom design "
                    "for the button labels overlay."
                )
            )
            overlay_description.set_xalign(0)
            overlay_description.set_wrap(True)
            overlay_description.add_css_class("dim-label")
            overlay_text.append(overlay_title)
            overlay_text.append(overlay_description)
            overlay_text.set_hexpand(True)
            overlay_row.append(overlay_text)
            self.button_overlay_combo = Gtk.ComboBoxText()
            self.button_overlay_combo.append("boxes", "Boxes")
            self.button_overlay_combo.append("basic", "Basic")
            self.button_overlay_combo.append("glass", "Glass")
            self.button_overlay_combo.append("custom", "Custom")
            self.button_overlay_combo.set_active_id(self.button_overlay_style)
            self.button_overlay_combo.set_valign(Gtk.Align.CENTER)
            self.button_overlay_combo.connect(
                "changed", self.on_button_overlay_style_changed
            )
            overlay_row.append(self.button_overlay_combo)
            screen_box.append(overlay_row)

            custom_overlay_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            self.custom_overlay_label = Gtk.Label(label="No custom overlay imported")
            self.custom_overlay_label.set_xalign(0)
            self.custom_overlay_label.set_hexpand(True)
            custom_overlay_row.append(self.custom_overlay_label)
            import_overlay = Gtk.Button(label="Import custom overlay…")
            import_overlay.connect("clicked", self.on_choose_custom_button_overlay)
            custom_overlay_row.append(import_overlay)
            save_template = Gtk.Button(label="Save template…")
            save_template.connect("clicked", self.on_save_button_overlay_template)
            custom_overlay_row.append(save_template)
            self.remove_custom_overlay_button = Gtk.Button(label="Remove")
            self.remove_custom_overlay_button.connect(
                "clicked", self.on_remove_custom_button_overlay
            )
            custom_overlay_row.append(self.remove_custom_overlay_button)
            screen_box.append(custom_overlay_row)
            custom_overlay_hint = Gtk.Label(
                label=(
                    "Design on the supplied 480×80 transparent PNG template, "
                    "then import the finished PNG and select Custom above."
                )
            )
            custom_overlay_hint.set_xalign(0)
            custom_overlay_hint.set_wrap(True)
            custom_overlay_hint.add_css_class("dim-label")
            screen_box.append(custom_overlay_hint)
            self.update_custom_button_overlay_row()

            brightness_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            brightness_label = Gtk.Label(label="Screen brightness")
            brightness_label.set_xalign(0)
            brightness_label.set_size_request(150, -1)
            brightness_row.append(brightness_label)
            self.brightness_scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL,
                MIN_DISPLAY_BRIGHTNESS,
                MAX_DISPLAY_BRIGHTNESS,
                DISPLAY_BRIGHTNESS_STEP,
            )
            self.brightness_scale.set_hexpand(True)
            self.brightness_scale.set_draw_value(False)
            self.brightness_scale.set_value(self.display_brightness)
            self.brightness_scale.set_tooltip_text(
                "Adjust the controller screen without redrawing it"
            )
            brightness_row.append(self.brightness_scale)
            self.brightness_value_label = Gtk.Label(
                label=f"{self.display_brightness}%"
            )
            self.brightness_value_label.set_width_chars(4)
            self.brightness_value_label.set_xalign(1)
            brightness_row.append(self.brightness_value_label)
            self.brightness_scale.connect(
                "value-changed", self.on_brightness_changed
            )
            screen_box.append(brightness_row)
            brightness_hint = Gtk.Label(
                label=(
                    "Changes are saved immediately and applied live while the mixer "
                    "is running. The startup logo keeps its own safe brightness."
                )
            )
            brightness_hint.set_xalign(0)
            brightness_hint.set_wrap(True)
            brightness_hint.add_css_class("dim-label")
            screen_box.append(brightness_hint)
            root.append(screen_box)

            self.fullscreen_image_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6
            )
            fullscreen_title = Gtk.Label(label="Full-screen image")
            fullscreen_title.set_xalign(0)
            fullscreen_title.add_css_class("heading")
            self.fullscreen_image_box.append(fullscreen_title)
            fullscreen_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8
            )
            self.fullscreen_image_label = Gtk.Label(label="No image selected")
            self.fullscreen_image_label.set_xalign(0)
            self.fullscreen_image_label.set_hexpand(True)
            fullscreen_row.append(self.fullscreen_image_label)
            choose_fullscreen = Gtk.Button(label="Choose image…")
            choose_fullscreen.connect("clicked", self.on_choose_fullscreen_image)
            fullscreen_row.append(choose_fullscreen)
            self.remove_fullscreen_button = Gtk.Button(label="Remove")
            self.remove_fullscreen_button.connect(
                "clicked", self.on_remove_fullscreen_image
            )
            fullscreen_row.append(self.remove_fullscreen_button)
            self.fullscreen_image_box.append(fullscreen_row)
            fullscreen_hint = Gtk.Label(
                label="This image fills the screen without mixer labels or percentage badges."
            )
            fullscreen_hint.set_xalign(0)
            fullscreen_hint.set_wrap(True)
            fullscreen_hint.add_css_class("dim-label")
            self.fullscreen_image_box.append(fullscreen_hint)
            root.append(self.fullscreen_image_box)

            self.notepad_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6
            )
            notepad_title = Gtk.Label(label="Notepad")
            notepad_title.set_xalign(0)
            notepad_title.add_css_class("heading")
            self.notepad_box.append(notepad_title)
            notepad_hint = Gtk.Label(
                label=(
                    "Type or paste reference notes below, then choose formatting "
                    "for the complete note. Auto-fit keeps long notes readable."
                )
            )
            notepad_hint.set_xalign(0)
            notepad_hint.set_wrap(True)
            notepad_hint.add_css_class("dim-label")
            self.notepad_box.append(notepad_hint)

            notepad_format_grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            family_label = Gtk.Label(label="Font")
            family_label.set_xalign(0)
            notepad_format_grid.attach(family_label, 0, 0, 1, 1)
            self.notepad_font_family_dropdown = Gtk.DropDown(
                model=Gtk.StringList.new(list(NOTEPAD_FONT_FAMILY_LABELS))
            )
            self.notepad_font_family_dropdown.set_selected(
                NOTEPAD_FONT_FAMILIES.index(
                    str(self.notepad_style["font_family"])
                )
            )
            self.notepad_font_family_dropdown.set_hexpand(True)
            notepad_format_grid.attach(
                self.notepad_font_family_dropdown, 1, 0, 1, 1
            )

            style_label = Gtk.Label(label="Style")
            style_label.set_xalign(0)
            notepad_format_grid.attach(style_label, 2, 0, 1, 1)
            self.notepad_font_style_dropdown = Gtk.DropDown(
                model=Gtk.StringList.new(list(NOTEPAD_FONT_STYLE_LABELS))
            )
            self.notepad_font_style_dropdown.set_selected(
                NOTEPAD_FONT_STYLES.index(str(self.notepad_style["font_style"]))
            )
            self.notepad_font_style_dropdown.set_hexpand(True)
            notepad_format_grid.attach(
                self.notepad_font_style_dropdown, 3, 0, 1, 1
            )

            size_label = Gtk.Label(label="Size")
            size_label.set_xalign(0)
            notepad_format_grid.attach(size_label, 0, 1, 1, 1)
            size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.notepad_auto_size_check = Gtk.CheckButton(label="Auto-fit")
            self.notepad_auto_size_check.set_active(
                int(self.notepad_style["font_size"]) == 0
            )
            self.notepad_auto_size_check.connect(
                "toggled", self.on_notepad_auto_size_changed
            )
            size_box.append(self.notepad_auto_size_check)
            self.notepad_font_size_spin = Gtk.SpinButton.new_with_range(
                MIN_NOTEPAD_FONT_SIZE, MAX_NOTEPAD_FONT_SIZE, 1
            )
            configured_font_size = int(self.notepad_style["font_size"])
            self.notepad_font_size_spin.set_value(configured_font_size or 20)
            self.notepad_font_size_spin.set_numeric(True)
            self.notepad_font_size_spin.set_sensitive(configured_font_size != 0)
            size_box.append(self.notepad_font_size_spin)
            size_box.append(Gtk.Label(label="px"))
            notepad_format_grid.attach(size_box, 1, 1, 1, 1)

            alignment_label = Gtk.Label(label="Alignment")
            alignment_label.set_xalign(0)
            notepad_format_grid.attach(alignment_label, 2, 1, 1, 1)
            self.notepad_alignment_dropdown = Gtk.DropDown(
                model=Gtk.StringList.new(list(NOTEPAD_ALIGNMENT_LABELS))
            )
            self.notepad_alignment_dropdown.set_selected(
                NOTEPAD_ALIGNMENTS.index(str(self.notepad_style["alignment"]))
            )
            self.notepad_alignment_dropdown.set_hexpand(True)
            notepad_format_grid.attach(
                self.notepad_alignment_dropdown, 3, 1, 1, 1
            )

            colour_label = Gtk.Label(label="Text colour")
            colour_label.set_xalign(0)
            notepad_format_grid.attach(colour_label, 0, 2, 1, 1)
            if hasattr(Gtk, "ColorDialogButton"):
                notepad_colour_dialog = Gtk.ColorDialog()
                notepad_colour_dialog.set_title("Choose the Notepad text colour")
                self.notepad_colour_button = Gtk.ColorDialogButton.new(
                    notepad_colour_dialog
                )
            else:
                self.notepad_colour_button = Gtk.ColorButton()
                self.notepad_colour_button.set_title(
                    "Choose the Notepad text colour"
                )
            notepad_rgba = Gdk.RGBA()
            notepad_rgba.parse(str(self.notepad_style["text_color"]))
            self.notepad_colour_button.set_rgba(notepad_rgba)
            self.notepad_colour_button.set_size_request(70, 36)
            notepad_format_grid.attach(self.notepad_colour_button, 1, 2, 1, 1)
            self.notepad_box.append(notepad_format_grid)

            notepad_scroller = Gtk.ScrolledWindow()
            notepad_scroller.set_policy(
                Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
            )
            notepad_scroller.set_min_content_height(150)
            self.notepad_buffer = Gtk.TextBuffer()
            self.notepad_buffer.set_text(self.notepad_text)
            self.notepad_buffer.connect("changed", self.on_notepad_text_changed)
            self.notepad_view = Gtk.TextView(buffer=self.notepad_buffer)
            self.notepad_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.notepad_view.set_left_margin(10)
            self.notepad_view.set_right_margin(10)
            self.notepad_view.set_top_margin(8)
            self.notepad_view.set_bottom_margin(8)
            notepad_scroller.set_child(self.notepad_view)
            self.notepad_box.append(notepad_scroller)
            self.notepad_status = Gtk.Label(label="")
            self.notepad_status.set_xalign(0)
            self.notepad_status.set_wrap(True)
            self.notepad_status.add_css_class("dim-label")
            self.notepad_box.append(self.notepad_status)
            self.update_notepad_status()
            root.append(self.notepad_box)

            self.background_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6
            )
            background_title = Gtk.Label(label="Mixer background")
            background_title.set_xalign(0)
            background_title.add_css_class("heading")
            self.background_box.append(background_title)
            background_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.background_label = Gtk.Label(label="No custom background")
            self.background_label.set_xalign(0)
            self.background_label.set_hexpand(True)
            self.background_label.add_css_class("background-name")
            background_row.append(self.background_label)
            choose_background = Gtk.Button(label="Choose image…")
            choose_background.connect("clicked", self.on_choose_background)
            background_row.append(choose_background)
            self.remove_background_button = Gtk.Button(label="Remove")
            self.remove_background_button.connect("clicked", self.on_remove_background)
            background_row.append(self.remove_background_button)
            self.background_box.append(background_row)
            background_hint = Gtk.Label(
                label="The image is cropped to fill the controller and darkened so labels remain readable."
            )
            background_hint.set_xalign(0)
            background_hint.set_wrap(True)
            background_hint.add_css_class("dim-label")
            self.background_box.append(background_hint)
            root.append(self.background_box)
            self.update_background_row()
            self.update_fullscreen_image_row()
            self.update_mode_controls()

            remote_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            remote_title = Gtk.Label(label="Android remote control")
            remote_title.set_xalign(0)
            remote_title.add_css_class("heading")
            remote_box.append(remote_title)
            remote_enable_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=12
            )
            remote_enable_text = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=2
            )
            remote_enable_text.set_hexpand(True)
            remote_enable_label = Gtk.Label(label="Enable local-network remote")
            remote_enable_label.set_xalign(0)
            remote_enable_description = Gtk.Label(
                label=(
                    "Allow paired phones on this network to operate the virtual mixer. "
                    "The mixer restarts when this setting changes."
                )
            )
            remote_enable_description.set_xalign(0)
            remote_enable_description.set_wrap(True)
            remote_enable_description.add_css_class("dim-label")
            remote_enable_text.append(remote_enable_label)
            remote_enable_text.append(remote_enable_description)
            remote_enable_row.append(remote_enable_text)
            self.remote_switch = Gtk.Switch()
            self.remote_switch.set_valign(Gtk.Align.CENTER)
            self.remote_switch.set_active(self.remote_enabled)
            self.remote_switch.connect(
                "notify::active", self.on_remote_enabled_changed
            )
            remote_enable_row.append(self.remote_switch)
            remote_box.append(remote_enable_row)

            self.remote_status = Gtk.Label(label="Remote control is disabled")
            self.remote_status.set_xalign(0)
            self.remote_status.set_wrap(True)
            self.remote_status.add_css_class("dim-label")
            remote_box.append(self.remote_status)

            pairing_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.start_pairing_button = Gtk.Button(label="Pair new phone")
            self.start_pairing_button.connect(
                "clicked", self.on_start_remote_pairing
            )
            pairing_row.append(self.start_pairing_button)
            self.cancel_pairing_button = Gtk.Button(label="Cancel PIN")
            self.cancel_pairing_button.connect(
                "clicked", self.on_cancel_remote_pairing
            )
            pairing_row.append(self.cancel_pairing_button)
            qr_button = Gtk.Button(label="Show QR fallback")
            qr_button.connect("clicked", self.on_show_remote_qr)
            pairing_row.append(qr_button)
            self.remote_qr_button = qr_button
            remote_box.append(pairing_row)

            self.remote_pin = Gtk.Label(label="")
            self.remote_pin.set_xalign(0)
            self.remote_pin.set_wrap(True)
            self.remote_pin.add_css_class("pairing-pin")
            remote_box.append(self.remote_pin)

            paired_title = Gtk.Label(label="Paired phones")
            paired_title.set_xalign(0)
            paired_title.add_css_class("dim-label")
            remote_box.append(paired_title)
            self.remote_devices_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=6
            )
            remote_box.append(self.remote_devices_box)

            firewall_hint = Gtk.Label(
                label=(
                    "Uses TCP port 47680 on the local network. If discovery works but "
                    "connection fails, allow this port through the computer firewall."
                )
            )
            firewall_hint.set_xalign(0)
            firewall_hint.set_wrap(True)
            firewall_hint.add_css_class("dim-label")
            remote_box.append(firewall_hint)
            root.append(remote_box)

            autostart_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            autostart_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            autostart_text.set_hexpand(True)
            auto_title = Gtk.Label(label="Start automatically when I sign in")
            auto_title.set_xalign(0)
            auto_description = Gtk.Label(
                label="The mixer runs quietly in the background using your saved assignments."
            )
            auto_description.set_xalign(0)
            auto_description.set_wrap(True)
            auto_description.add_css_class("dim-label")
            autostart_text.append(auto_title)
            autostart_text.append(auto_description)
            autostart_row.append(autostart_text)
            self.autostart_switch = Gtk.Switch()
            self.autostart_switch.set_valign(Gtk.Align.CENTER)
            self.autostart_switch.connect("notify::active", self.on_autostart_changed)
            autostart_row.append(self.autostart_switch)
            root.append(autostart_row)

            self.message = Gtk.Label(label="")
            self.message.set_xalign(0)
            self.message.set_wrap(True)
            self.message.add_css_class("dim-label")
            root.append(self.message)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            actions.set_halign(Gtk.Align.END)
            root.append(actions)
            virtual_mixer_button = Gtk.Button(label="Open virtual mixer")
            virtual_mixer_button.set_tooltip_text(
                "Open a mouse-controlled mixer for the saved pages"
            )
            virtual_mixer_button.connect("clicked", self.on_virtual_mixer_clicked)
            actions.append(virtual_mixer_button)
            self.power_button = Gtk.Button(label="Start mixer")
            self.power_button.connect("clicked", self.on_power_clicked)
            actions.append(self.power_button)
            apply_button = Gtk.Button(label="Apply changes")
            apply_button.add_css_class("suggested-action")
            apply_button.connect("clicked", self.on_apply_clicked)
            actions.append(apply_button)

            version = Gtk.Label(label=f"{APP_NAME} {APP_VERSION}")
            version.set_xalign(1)
            version.add_css_class("dim-label")
            root.append(version)

            try:
                ensure_service_unit()
            except RuntimeError as error:
                self.show_message(str(error), error=True)
            self.refresh_applications()
            self.refresh_status()
            GLib.timeout_add_seconds(2, self.refresh_status)

        def show_message(self, text: str, error: bool = False) -> None:
            self.message.set_text(text)
            self.message.remove_css_class("success")
            self.message.remove_css_class("error")
            self.message.add_css_class("error" if error else "success")

        def rebuild_remote_devices(self, devices: list[dict[str, Any]]) -> None:
            signature = tuple(
                (
                    str(device.get("id", "")),
                    str(device.get("name", "Phone")),
                    int(device.get("paired_at", 0)),
                )
                for device in devices
            )
            if signature == self.remote_device_signature:
                return
            self.remote_device_signature = signature
            child = self.remote_devices_box.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                self.remote_devices_box.remove(child)
                child = following
            if not devices:
                empty = Gtk.Label(label="No phones paired yet")
                empty.set_xalign(0)
                empty.add_css_class("dim-label")
                self.remote_devices_box.append(empty)
                return
            for device in devices:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                paired_at = int(device.get("paired_at", 0))
                date = time.strftime("%d %b %Y", time.localtime(paired_at)) if paired_at else ""
                label = Gtk.Label(
                    label=f"{device.get('name', 'Phone')} · paired {date}"
                    if date
                    else str(device.get("name", "Phone"))
                )
                label.set_xalign(0)
                label.set_hexpand(True)
                row.append(label)
                revoke = Gtk.Button(label="Revoke")
                revoke.connect(
                    "clicked", self.on_revoke_remote_device, str(device.get("id", ""))
                )
                row.append(revoke)
                self.remote_devices_box.append(row)

        def show_remote_pairing_dialog(
            self,
            pin: str,
            device_name: str,
            expires: int,
        ) -> None:
            if self.remote_pairing_dialog is not None:
                self.remote_pairing_dialog.close()
            dialog = Gtk.Window(
                title="Pair Android phone",
                transient_for=self,
                modal=True,
            )
            dialog.set_default_size(430, 230)
            dialog.set_resizable(False)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            content.set_margin_top(24)
            content.set_margin_bottom(24)
            content.set_margin_start(28)
            content.set_margin_end(28)
            heading = Gtk.Label(
                label=f"Pair {device_name}" if device_name else "Pair a new phone"
            )
            heading.add_css_class("title-2")
            content.append(heading)
            pin_label = Gtk.Label(label=f"{pin[:3]} {pin[3:]}")
            pin_label.add_css_class("pairing-pin")
            content.append(pin_label)
            hint = Gtk.Label(
                label=(
                    f"Enter this PIN in the Android app. It expires in about "
                    f"{expires} seconds and can be used once."
                )
            )
            hint.set_wrap(True)
            hint.set_justify(Gtk.Justification.CENTER)
            hint.add_css_class("dim-label")
            content.append(hint)
            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            buttons.set_halign(Gtk.Align.CENTER)
            dismiss = Gtk.Button(label="Hide")
            dismiss.connect("clicked", lambda _button: dialog.close())
            buttons.append(dismiss)
            cancel = Gtk.Button(label="Cancel pairing")
            cancel.connect("clicked", self.on_cancel_remote_pairing)
            buttons.append(cancel)
            content.append(buttons)
            dialog.set_child(content)

            def on_close(_window) -> bool:
                if self.remote_pairing_dialog is dialog:
                    self.remote_pairing_dialog = None
                return False

            dialog.connect("close-request", on_close)
            self.remote_pairing_dialog = dialog
            dialog.present()

        def refresh_remote_status(self, mixer_running: bool) -> None:
            enabled = load_remote_enabled()
            self.remote_enabled = enabled
            if self.remote_switch.get_active() != enabled:
                self.updating_remote_switch = True
                self.remote_switch.set_active(enabled)
                self.updating_remote_switch = False
            available = enabled and mixer_running
            self.start_pairing_button.set_sensitive(available)
            self.remote_qr_button.set_sensitive(available)
            if not enabled:
                self.remote_status.set_text("Remote control is disabled")
                self.remote_pin.set_text("")
                self.cancel_pairing_button.set_sensitive(False)
                self.last_remote_pairing_pin = ""
                if self.remote_pairing_dialog is not None:
                    self.remote_pairing_dialog.close()
                self.rebuild_remote_devices([])
                return
            if not mixer_running:
                self.remote_status.set_text(
                    "Remote control will become available when the mixer starts"
                )
                self.remote_pin.set_text("")
                self.cancel_pairing_button.set_sensitive(False)
                self.last_remote_pairing_pin = ""
                if self.remote_pairing_dialog is not None:
                    self.remote_pairing_dialog.close()
                return
            try:
                status = remote_admin_request("/remote")
            except RuntimeError:
                self.remote_status.set_text(
                    "Remote control is starting, or this mixer needs to be restarted"
                )
                self.remote_pin.set_text("")
                self.cancel_pairing_button.set_sensitive(False)
                return
            self.remote_status.set_text(
                f"● Available at {status.get('server', 'port 47680')}"
            )
            pairing = status.get("pairing", {})
            if isinstance(pairing, dict) and pairing.get("active"):
                pin = str(pairing.get("pin", ""))
                expires = int(pairing.get("expires_in", 0))
                device_name = str(pairing.get("device_name", "")).strip()
                self.remote_pin.set_text(
                    f"Pairing PIN: {pin[:3]} {pin[3:]}"
                    + (f"  ·  for {device_name}" if device_name else "")
                    + f"  ·  expires in {expires} seconds"
                )
                self.cancel_pairing_button.set_sensitive(True)
                if pin and pin != self.last_remote_pairing_pin:
                    self.last_remote_pairing_pin = pin
                    self.show_remote_pairing_dialog(pin, device_name, expires)
            else:
                self.remote_pin.set_text(
                    "Choose Pair new phone, then enter the displayed PIN in Android."
                )
                self.cancel_pairing_button.set_sensitive(False)
                self.last_remote_pairing_pin = ""
                if self.remote_pairing_dialog is not None:
                    self.remote_pairing_dialog.close()
            devices = status.get("devices", [])
            self.rebuild_remote_devices(devices if isinstance(devices, list) else [])

        def on_remote_enabled_changed(self, switch, _parameter) -> None:
            if self.updating_remote_switch:
                return
            enabled = switch.get_active()
            previous = self.remote_enabled
            try:
                save_remote_enabled(enabled)
                self.remote_enabled = enabled
                if service_property("ActiveState") == "active":
                    service_action("restart")
                    self.show_message(
                        "Remote control enabled and the mixer restarted."
                        if enabled
                        else "Remote control disabled and the mixer restarted."
                    )
                else:
                    self.show_message(
                        "Remote control enabled. Start the mixer to pair a phone."
                        if enabled
                        else "Remote control disabled."
                    )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
                self.updating_remote_switch = True
                switch.set_active(previous)
                self.updating_remote_switch = False
                self.remote_enabled = previous
                save_remote_enabled(previous)
            self.refresh_status()

        def on_start_remote_pairing(self, _button) -> None:
            try:
                response = remote_admin_request("/pairing/start", "POST")
                pairing = response.get("pairing", {})
                pin = str(pairing.get("pin", "")) if isinstance(pairing, dict) else ""
                self.show_message(
                    f"Pairing is open. Enter {pin[:3]} {pin[3:]} on the phone."
                )
            except RuntimeError as error:
                self.show_message(str(error), error=True)
            self.refresh_status()

        def on_cancel_remote_pairing(self, _button) -> None:
            try:
                remote_admin_request("/pairing/cancel", "POST")
                self.show_message("Phone pairing cancelled.")
            except RuntimeError as error:
                self.show_message(str(error), error=True)
            self.refresh_status()

        def on_show_remote_qr(self, _button) -> None:
            if not webbrowser.open(
                f"http://127.0.0.1:{DEFAULT_REMOTE_PORT}/api/v1/pair"
            ):
                self.show_message("Could not open the pairing page.", error=True)

        def on_revoke_remote_device(self, _button, device_id: str) -> None:
            try:
                remote_admin_request(f"/devices/{quote(device_id, safe='')}", "DELETE")
                self.show_message("Phone access revoked.")
            except RuntimeError as error:
                self.show_message(str(error), error=True)
            self.remote_device_signature = None
            self.refresh_status()

        def update_background_row(self) -> None:
            if self.background_path is None:
                self.background_label.set_text("No custom background")
                self.remove_background_button.set_sensitive(False)
            else:
                self.background_label.set_text("Custom background selected")
                self.background_label.set_tooltip_text(str(self.background_path))
                self.remove_background_button.set_sensitive(True)

        def update_fullscreen_image_row(self) -> None:
            if self.fullscreen_image_path is None:
                self.fullscreen_image_label.set_text("No image selected")
                self.remove_fullscreen_button.set_sensitive(False)
            else:
                self.fullscreen_image_label.set_text("Full-screen image selected")
                self.fullscreen_image_label.set_tooltip_text(
                    str(self.fullscreen_image_path)
                )
                self.remove_fullscreen_button.set_sensitive(True)

        def update_custom_button_overlay_row(self) -> None:
            if self.custom_button_overlay_path is None:
                self.custom_overlay_label.set_text("No custom overlay imported")
                self.custom_overlay_label.set_tooltip_text(None)
                self.remove_custom_overlay_button.set_sensitive(False)
            else:
                self.custom_overlay_label.set_text("Custom overlay ready")
                self.custom_overlay_label.set_tooltip_text(
                    str(self.custom_button_overlay_path)
                )
                self.remove_custom_overlay_button.set_sensitive(True)

        def update_mode_controls(self) -> None:
            self.background_box.set_sensitive(self.display_mode == "mixer")
            self.fullscreen_image_box.set_sensitive(self.display_mode == "image")
            self.notepad_box.set_sensitive(self.display_mode == "notepad")
            self.volume_meter_row.set_sensitive(self.display_mode == "mixer")
            self.meter_channel_mode_row.set_sensitive(
                self.display_mode == "mixer"
            )
            self.meter_style_row.set_sensitive(self.display_mode == "mixer")

        def update_notepad_status(self) -> None:
            character_count = len(self.notepad_text)
            if character_count:
                message = (
                    f"{character_count:,} characters. Select Apply changes to "
                    "save the text and formatting, then update the controller."
                )
            else:
                message = (
                    "An empty note shows a short prompt on the controller. "
                    "Select Apply changes after editing."
                )
            self.notepad_status.set_text(message)

        def on_notepad_auto_size_changed(self, check_button) -> None:
            self.notepad_font_size_spin.set_sensitive(
                not check_button.get_active()
            )

        def selected_notepad_style(self) -> dict[str, object]:
            family_index = self.notepad_font_family_dropdown.get_selected()
            style_index = self.notepad_font_style_dropdown.get_selected()
            alignment_index = self.notepad_alignment_dropdown.get_selected()
            if family_index >= len(NOTEPAD_FONT_FAMILIES):
                family_index = 0
            if style_index >= len(NOTEPAD_FONT_STYLES):
                style_index = 0
            if alignment_index >= len(NOTEPAD_ALIGNMENTS):
                alignment_index = 0
            rgba = self.notepad_colour_button.get_rgba()
            return normalise_notepad_style(
                {
                    "font_size": (
                        0
                        if self.notepad_auto_size_check.get_active()
                        else self.notepad_font_size_spin.get_value_as_int()
                    ),
                    "font_family": NOTEPAD_FONT_FAMILIES[family_index],
                    "font_style": NOTEPAD_FONT_STYLES[style_index],
                    "text_color": "#{:02X}{:02X}{:02X}".format(
                        round(rgba.red * 255),
                        round(rgba.green * 255),
                        round(rgba.blue * 255),
                    ),
                    "alignment": NOTEPAD_ALIGNMENTS[alignment_index],
                }
            )

        def on_notepad_text_changed(self, text_buffer) -> None:
            start, end = text_buffer.get_bounds()
            self.notepad_text = text_buffer.get_text(start, end, True)
            self.update_notepad_status()

        def on_display_mode_changed(self, dropdown, _parameter) -> None:
            selected = dropdown.get_selected()
            if selected >= len(DISPLAY_MODES):
                return
            try:
                self.display_mode = DISPLAY_MODES[selected]
                save_display_mode(self.display_mode)
                self.update_mode_controls()
                self.show_message(
                    "Display mode selected. Select Apply changes to update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)

        def on_channel_icons_visibility_changed(self, _switch, _parameter) -> None:
            active = _switch.get_active() if hasattr(_switch, "get_active") else False
            save_show_channel_icons(active)

        def on_meter_channel_mode_changed(self, _combo) -> None:
            mode = self.meter_channel_mode_combo.get_active_id()
            if not mode:
                return
            try:
                save_meter_channel_mode(mode)
                self.meter_channel_mode = mode
                self.show_message(
                    "Activity monitoring mode selected. Select Apply changes "
                    "to update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)

        def on_meter_style_changed(self, _combo) -> None:
            style = self.meter_style_combo.get_active_id()
            if not style:
                return
            try:
                save_meter_style(style)
                self.meter_style = style
                self.show_message(
                    "Visualiser style selected. Select Apply changes to "
                    "update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)

        def on_button_overlay_style_changed(self, _combo) -> None:
            style = self.button_overlay_combo.get_active_id()
            if not style:
                return
            if style == "custom" and self.custom_button_overlay_path is None:
                self.button_overlay_combo.set_active_id(self.button_overlay_style)
                self.show_message(
                    "Import a 480×80 PNG custom overlay before selecting Custom.",
                    error=True,
                )
                return
            try:
                save_button_overlay_style(style)
                self.button_overlay_style = style
                self.show_message(
                    "Button overlay selected. Select Apply changes to update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)

        def on_choose_custom_button_overlay(self, _button) -> None:
            chooser = Gtk.FileChooserNative.new(
                "Import a custom button overlay",
                self,
                Gtk.FileChooserAction.OPEN,
                "Import",
                "Cancel",
            )
            image_filter = Gtk.FileFilter()
            image_filter.set_name("PNG images")
            image_filter.add_mime_type("image/png")
            image_filter.add_pattern("*.png")
            chooser.add_filter(image_filter)
            chooser.connect("response", self.on_custom_button_overlay_response)
            self.button_overlay_chooser = chooser
            chooser.show()

        def on_custom_button_overlay_response(self, chooser, response) -> None:
            try:
                if response != Gtk.ResponseType.ACCEPT:
                    return
                selected = chooser.get_file()
                filename = selected.get_path() if selected is not None else None
                if not filename:
                    raise RuntimeError("Choose a PNG image stored on this computer.")
                self.custom_button_overlay_path = import_custom_button_overlay(
                    Path(filename)
                )
                self.update_custom_button_overlay_row()
                self.button_overlay_style = "custom"
                save_button_overlay_style(self.button_overlay_style)
                self.button_overlay_combo.set_active_id(self.button_overlay_style)
                self.show_message(
                    "Custom overlay imported and selected. Select Apply changes "
                    "to update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            finally:
                chooser.destroy()
                self.button_overlay_chooser = None

        def on_save_button_overlay_template(self, _button) -> None:
            chooser = Gtk.FileChooserNative.new(
                "Save the button overlay template",
                self,
                Gtk.FileChooserAction.SAVE,
                "Save",
                "Cancel",
            )
            chooser.set_current_name("OpenStream100 button overlay template.png")
            image_filter = Gtk.FileFilter()
            image_filter.set_name("PNG image")
            image_filter.add_mime_type("image/png")
            image_filter.add_pattern("*.png")
            chooser.add_filter(image_filter)
            chooser.connect("response", self.on_button_overlay_template_response)
            self.button_overlay_template_chooser = chooser
            chooser.show()

        def on_button_overlay_template_response(self, chooser, response) -> None:
            try:
                if response != Gtk.ResponseType.ACCEPT:
                    return
                selected = chooser.get_file()
                filename = selected.get_path() if selected is not None else None
                if not filename:
                    raise RuntimeError("Choose where to save the PNG template.")
                destination = export_button_overlay_template(Path(filename))
                self.show_message(f"Overlay template saved as {destination.name}.")
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            finally:
                chooser.destroy()
                self.button_overlay_template_chooser = None

        def on_remove_custom_button_overlay(self, _button) -> None:
            try:
                CUSTOM_BUTTON_OVERLAY_PATH.unlink(missing_ok=True)
                self.custom_button_overlay_path = None
                if self.button_overlay_style == "custom":
                    self.button_overlay_style = "boxes"
                    save_button_overlay_style(self.button_overlay_style)
                    self.button_overlay_combo.set_active_id(
                        self.button_overlay_style
                    )
                self.update_custom_button_overlay_row()
                self.show_message(
                    "Custom overlay removed. Select Apply changes to update the controller."
                )
            except OSError as error:
                self.show_message(
                    f"Could not remove the custom overlay: {error}", error=True
                )

        def on_volume_meter_visibility_changed(self, _switch, _parameter) -> None:
            self.update_mode_controls()

        def on_brightness_changed(self, scale) -> None:
            brightness = normalise_display_brightness(scale.get_value())
            self.brightness_value_label.set_text(f"{brightness}%")
            if brightness == self.display_brightness:
                return
            try:
                save_display_brightness(brightness)
                self.display_brightness = brightness
                self.show_message(
                    f"Screen brightness saved at {brightness}%. A running mixer applies it live."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)

        def on_choose_background(self, _button) -> None:
            chooser = Gtk.FileChooserNative.new(
                "Choose a display background",
                self,
                Gtk.FileChooserAction.OPEN,
                "Choose",
                "Cancel",
            )
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Images")
            image_filter.add_mime_type("image/png")
            image_filter.add_mime_type("image/jpeg")
            image_filter.add_mime_type("image/webp")
            image_filter.add_mime_type("image/bmp")
            chooser.add_filter(image_filter)
            chooser.connect("response", self.on_background_response)
            self.background_chooser = chooser
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
                self.update_background_row()
                self.show_message(
                    "Background imported. Select Apply changes to update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            finally:
                chooser.destroy()
                self.background_chooser = None

        def on_remove_background(self, _button) -> None:
            try:
                save_background_path(None)
                BACKGROUND_PATH.unlink(missing_ok=True)
                self.background_path = None
                self.update_background_row()
                self.show_message(
                    "Background removed. Select Apply changes to update the controller."
                )
            except OSError as error:
                self.show_message(f"Could not remove the imported background: {error}", error=True)

        def on_choose_fullscreen_image(self, _button) -> None:
            chooser = Gtk.FileChooserNative.new(
                "Choose a full-screen image",
                self,
                Gtk.FileChooserAction.OPEN,
                "Choose",
                "Cancel",
            )
            image_filter = Gtk.FileFilter()
            image_filter.set_name("Images")
            image_filter.add_mime_type("image/png")
            image_filter.add_mime_type("image/jpeg")
            image_filter.add_mime_type("image/webp")
            image_filter.add_mime_type("image/bmp")
            chooser.add_filter(image_filter)
            chooser.connect("response", self.on_fullscreen_image_response)
            self.background_chooser = chooser
            chooser.show()

        def on_fullscreen_image_response(self, chooser, response) -> None:
            try:
                if response != Gtk.ResponseType.ACCEPT:
                    return
                selected = chooser.get_file()
                filename = selected.get_path() if selected is not None else None
                if not filename:
                    raise RuntimeError("Choose an image stored on this computer.")
                self.fullscreen_image_path = import_fullscreen_image(Path(filename))
                self.update_fullscreen_image_row()
                self.show_message(
                    "Full-screen image imported. Select Apply changes to update the controller."
                )
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            finally:
                chooser.destroy()
                self.background_chooser = None

        def on_remove_fullscreen_image(self, _button) -> None:
            try:
                save_fullscreen_image_path(None)
                FULLSCREEN_IMAGE_PATH.unlink(missing_ok=True)
                self.fullscreen_image_path = None
                self.update_fullscreen_image_row()
                self.show_message(
                    "Full-screen image removed. Select Apply changes to update the controller."
                )
            except OSError as error:
                self.show_message(
                    f"Could not remove the full-screen image: {error}", error=True
                )

        def refresh_applications(self) -> None:
            self.saved_channels = self.pages[self.current_page_index]["channels"]
            all_saved_channels = [
                channel
                for page in self.pages
                for channel in page["channels"]
            ]
            try:
                applications = discover_applications()
                self.choices = build_choices(applications, all_saved_channels)
                count = len(applications)
                self.show_message(
                    f"Found {count} active audio application{'s' if count != 1 else ''}."
                )
            except RuntimeError as error:
                self.choices = build_choices([], all_saved_channels)
                self.show_message(str(error), error=True)

            model = Gtk.StringList.new(
                [
                    choice.get("display_label", choice.get("label", "Disabled"))
                    for choice in self.choices
                ]
            )
            key_to_index = {choice_key(choice): index for index, choice in enumerate(self.choices)}
            for index, dropdown in enumerate(self.dropdowns):
                dropdown.set_model(model)
                selected = key_to_index.get(choice_key(self.saved_channels[index]), 0)
                dropdown.set_selected(selected)
                rgba = Gdk.RGBA()
                rgba.parse(normalise_colour(self.saved_channels[index].get("color"), index))
                self.colour_buttons[index].set_rgba(rgba)
            self.update_button_page_widgets()

        def page_labels(self) -> list[str]:
            return [
                f"Page {index + 1} · Controls {index * 4 + 1}–{index * 4 + 4}"
                for index in range(len(self.pages))
            ]

        def update_page_buttons(self) -> None:
            self.add_page_button.set_sensitive(len(self.pages) < MAX_MIXER_PAGES)
            self.remove_page_button.set_sensitive(len(self.pages) > 1)

        def rebuild_page_dropdown(self, selected: int) -> None:
            self.switching_page = True
            self.page_dropdown.set_model(Gtk.StringList.new(self.page_labels()))
            self.page_dropdown.set_selected(selected)
            self.switching_page = False
            self.update_page_buttons()

        def capture_current_page(self) -> None:
            self.pages[self.current_page_index] = {
                "channels": self.selected_channels(),
                "button_actions": self.selected_button_actions(),
                "button_volume_presets": self.selected_button_volume_presets(),
            }

        def update_button_page_widgets(self) -> None:
            page = self.pages[self.current_page_index]
            self.saved_button_actions = page["button_actions"]
            self.saved_button_volume_presets = page["button_volume_presets"]
            for index, dropdown in enumerate(self.button_action_dropdowns):
                dropdown.set_selected(
                    BUTTON_ACTION_IDS.index(self.saved_button_actions[index])
                )
                self.button_preset_channel_dropdowns[index].set_selected(
                    self.saved_button_volume_presets[index]["channel"] - 1
                )
                self.button_preset_spins[index].set_value(
                    self.saved_button_volume_presets[index]["percentage"]
                )
                self.update_button_preset_controls(index)

        def show_page(self, index: int) -> None:
            self.current_page_index = index
            self.saved_channels = self.pages[index]["channels"]
            key_to_index = {
                choice_key(choice): choice_index
                for choice_index, choice in enumerate(self.choices)
            }
            for control_index, dropdown in enumerate(self.dropdowns):
                selected = key_to_index.get(
                    choice_key(self.saved_channels[control_index]), 0
                )
                dropdown.set_selected(selected)
                rgba = Gdk.RGBA()
                rgba.parse(
                    normalise_colour(
                        self.saved_channels[control_index].get("color"),
                        control_index,
                    )
                )
                self.colour_buttons[control_index].set_rgba(rgba)
            self.update_button_page_widgets()

        def on_page_changed(self, dropdown, _parameter) -> None:
            if self.switching_page:
                return
            selected = dropdown.get_selected()
            if selected >= len(self.pages) or selected == self.current_page_index:
                return
            self.capture_current_page()
            self.show_page(selected)
            self.show_message(f"Editing mixer page {selected + 1}.")

        def on_add_page(self, _button) -> None:
            if len(self.pages) >= MAX_MIXER_PAGES:
                return
            self.capture_current_page()
            self.pages.append(default_mixer_page())
            selected = len(self.pages) - 1
            self.rebuild_page_dropdown(selected)
            self.show_page(selected)
            self.show_message(f"Mixer page {selected + 1} added.")

        def on_remove_page(self, _button) -> None:
            if len(self.pages) <= 1:
                return
            removed = self.current_page_index
            self.pages.pop(removed)
            selected = min(removed, len(self.pages) - 1)
            self.rebuild_page_dropdown(selected)
            self.show_page(selected)
            self.show_message(f"Mixer page {removed + 1} removed.")

        def selected_channels(self) -> list[dict[str, str]]:
            channels: list[dict[str, str]] = []
            for index, dropdown in enumerate(self.dropdowns):
                selected = dropdown.get_selected()
                if selected >= len(self.choices):
                    selected = 0
                channel = dict(self.choices[selected])
                channel.pop("display_label", None)
                rgba = self.colour_buttons[index].get_rgba()
                channel["color"] = "#{:02X}{:02X}{:02X}".format(
                    round(rgba.red * 255),
                    round(rgba.green * 255),
                    round(rgba.blue * 255),
                )
                channels.append(channel)
            return channels

        def selected_button_actions(self) -> list[str]:
            actions: list[str] = []
            for dropdown in self.button_action_dropdowns:
                selected = dropdown.get_selected()
                if selected >= len(BUTTON_ACTION_IDS):
                    selected = 0
                actions.append(BUTTON_ACTION_IDS[selected])
            return actions

        def update_button_preset_controls(self, index: int) -> None:
            selected = self.button_action_dropdowns[index].get_selected()
            enabled = (
                selected < len(BUTTON_ACTION_IDS)
                and BUTTON_ACTION_IDS[selected] == "set_channel_volume"
            )
            self.button_preset_channel_dropdowns[index].set_sensitive(enabled)
            self.button_preset_spins[index].set_sensitive(enabled)

        def on_button_action_changed(self, _dropdown, _parameter, index: int) -> None:
            self.update_button_preset_controls(index)

        def selected_button_volume_presets(self) -> list[dict[str, int]]:
            return [
                {
                    "channel": channel_dropdown.get_selected() + 1,
                    "percentage": spin.get_value_as_int(),
                }
                for channel_dropdown, spin in zip(
                    self.button_preset_channel_dropdowns,
                    self.button_preset_spins,
                )
            ]

        def apply_assignments(self) -> None:
            self.capture_current_page()
            save_mixer_pages(self.pages)
            save_knob_sensitivity(self.sensitivity_spin.get_value())
            save_show_volume_meters(self.volume_meter_switch.get_active())
            save_meter_channel_mode(self.meter_channel_mode)
            save_meter_style(self.meter_style)
            save_volume_meter_mode("activity")
            save_notepad_text(self.notepad_text)
            self.notepad_style = self.selected_notepad_style()
            save_notepad_style(self.notepad_style)
            if service_property("ActiveState") == "active":
                service_action("restart")
                self.show_message("Changes saved and the mixer restarted.")
            else:
                self.show_message("Changes saved. Select Start mixer when you are ready.")

        def on_apply_clicked(self, _button) -> None:
            try:
                self.apply_assignments()
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            self.refresh_status()

        def on_refresh_clicked(self, _button) -> None:
            self.capture_current_page()
            self.refresh_applications()

        def on_virtual_mixer_clicked(self, _button) -> None:
            launched, message = launch_virtual_mixer()
            self.show_message(message, error=not launched)

        def on_power_clicked(self, _button) -> None:
            try:
                if service_property("ActiveState") == "active":
                    service_action("stop")
                    self.show_message("Mixer stopped. Any soft-muted applications were restored.")
                else:
                    self.capture_current_page()
                    save_mixer_pages(self.pages)
                    save_knob_sensitivity(self.sensitivity_spin.get_value())
                    save_show_volume_meters(self.volume_meter_switch.get_active())
                    save_meter_channel_mode(self.meter_channel_mode)
                    save_meter_style(self.meter_style)
                    save_volume_meter_mode("activity")
                    save_notepad_text(self.notepad_text)
                    self.notepad_style = self.selected_notepad_style()
                    save_notepad_style(self.notepad_style)
                    if not device_connected():
                        raise RuntimeError("Connect the Stream 100 before starting the mixer.")
                    service_action("start")
                    self.show_message("Mixer started with the displayed assignments.")
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
            self.refresh_status()

        def on_autostart_changed(self, switch, _parameter) -> None:
            if self.updating_switch:
                return
            try:
                service_action("enable" if switch.get_active() else "disable")
                state = "enabled" if switch.get_active() else "disabled"
                self.show_message(f"Automatic sign-in startup {state}.")
            except (OSError, RuntimeError) as error:
                self.show_message(str(error), error=True)
                self.updating_switch = True
                switch.set_active(not switch.get_active())
                self.updating_switch = False

        def refresh_status(self) -> bool:
            connected = device_connected()
            self.device_status.set_text(
                "● Controller connected" if connected else "○ Controller not connected"
            )
            self.device_status.remove_css_class("success")
            self.device_status.remove_css_class("error")
            self.device_status.add_css_class("success" if connected else "error")

            state = service_property("ActiveState")
            running = state == "active"
            self.service_status.set_text("● Mixer running" if running else "○ Mixer stopped")
            self.service_status.remove_css_class("success")
            self.service_status.remove_css_class("dim-label")
            self.service_status.add_css_class("success" if running else "dim-label")
            self.power_button.set_label("Stop mixer" if running else "Start mixer")

            enabled = service_is_enabled()
            if self.autostart_switch.get_active() != enabled:
                self.updating_switch = True
                self.autostart_switch.set_active(enabled)
                self.updating_switch = False
            self.refresh_remote_status(running)
            return True

    return ControlWindow


def main() -> int:
    if os.name != "posix":
        print("The Stream 100 control panel is intended to run on Fedora Linux.", file=sys.stderr)
        return 2
    if "--migrate-user-install" in sys.argv[1:]:
        try:
            for message in migrate_user_install():
                print(message)
            return 0
        except (OSError, RuntimeError) as error:
            print(f"Migration failed: {error}", file=sys.stderr)
            return 1
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError) as error:
        print(
            "The GTK desktop library is missing. On Fedora, run:\n"
            "  sudo dnf install python3-gobject gtk4",
            file=sys.stderr,
        )
        print(error, file=sys.stderr)
        return 2

    css = Gtk.CssProvider()
    css.load_from_data(
        b"""
        .status-card {
            background-color: rgba(48, 204, 190, 0.10);
            border: 1px solid rgba(48, 204, 190, 0.22);
            border-radius: 12px;
            padding: 14px;
        }
        .channel-number {
            background-color: #20303f;
            color: #30ccbe;
            border-radius: 18px;
            font-weight: 700;
        }
        .success { color: #28b978; }
        .error { color: #d94d5e; }
        .pairing-pin {
            color: #30ccbe;
            font-size: 20px;
            font-weight: 700;
        }
        """
    )
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    application = Gtk.Application(application_id=APP_ID)
    ControlWindow = make_window_class(Gtk, GLib, Gdk)

    def activate(app) -> None:
        window = app.get_active_window()
        if window is None:
            window = ControlWindow(app)
        window.present()

    application.connect("activate", activate)
    return application.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
