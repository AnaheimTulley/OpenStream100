#!/usr/bin/python3
"""Regression checks for the hardware-independent virtual mixer core."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from PIL import Image

import stream100_channel_icons as channel_icons
import stream100_virtual_mixer as virtual


def test_page_loading() -> None:
    page = virtual.default_page()
    page["channels"][1] = {
        "kind": "application",
        "label": "Music",
        "property": "application.name",
        "value": "Music",
        "color": "#123abc",
    }
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.json"
        config.write_text(json.dumps({"pages": [page]}), encoding="utf-8")
        loaded = virtual.load_mixer_pages(config)
    assert len(loaded) == 1
    assert loaded[0]["channels"][1]["label"] == "Music"
    assert loaded[0]["channels"][1]["color"] == "#123ABC"


def test_legacy_and_invalid_config_fallback() -> None:
    legacy = virtual.default_page()
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "config.json"
        config.write_text(
            json.dumps(
                {
                    "channels": legacy["channels"],
                    "button_actions": legacy["button_actions"],
                    "button_volume_presets": legacy["button_volume_presets"],
                }
            ),
            encoding="utf-8",
        )
        loaded = virtual.load_mixer_pages(config)
        config.write_text("not json", encoding="utf-8")
        fallback = virtual.load_mixer_pages(config)
    assert loaded[0]["channels"][0]["kind"] == "default"
    assert fallback == [virtual.default_page()]


def test_background_and_meter_preferences() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.png"
        destination = root / "background.png"
        config = root / "config.json"
        Image.new("RGB", (64, 32), (180, 90, 30)).save(source)
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sentinel": "preserved",
                    "show_volume_meters": False,
                    "meter_channel_mode": "mono",
                    "meter_style": "segmented",
                }
            ),
            encoding="utf-8",
        )
        imported = virtual.import_background(source, destination, config)
        assert imported == destination
        assert virtual.load_background_path(config) == destination
        assert virtual.prepare_virtual_background(destination).mode == "RGBA"
        assert virtual.load_meter_preferences(config) == (
            False,
            "mono",
            "segmented",
        )
        assert virtual.read_config_payload(config)["sentinel"] == "preserved"
        virtual.save_background_path(None, config)
        assert virtual.load_background_path(config) is None


def test_stream_resolution() -> None:
    document = json.dumps(
        [
            {
                "id": 42,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "Music",
                    }
                },
            },
            {
                "id": 99,
                "type": "PipeWire:Interface:Node",
                "info": {"props": {"media.class": "Audio/Sink"}},
            },
        ]
    )
    streams = virtual.parse_pipewire_streams(document)
    assert streams[0]["props"]["application.name"] == "Music"
    channels = virtual.default_page()["channels"]
    channels[1] = {
        "kind": "application",
        "label": "Music",
        "property": "application.name",
        "value": "Music",
        "color": "#36D380",
    }
    assert virtual.resolve_targets(channels, streams) == [
        [virtual.DEFAULT_TARGET], ["42"], [], []
    ]
    assert virtual.matching_channel_streams(channels[1], streams) == streams


def test_icon_candidates() -> None:
    channel = {
        "kind": "application",
        "label": "Spotify",
        "property": "application.name",
        "value": "Spotify",
        "color": "#36D380",
    }
    streams = [
        {
            "props": {
                "application.id": "com.spotify.Client",
                "application.process.binary": "spotify",
            }
        }
    ]
    candidates = virtual.icon_candidates(channel, streams)
    assert "com.spotify.Client" in candidates
    assert "com-spotify-Client" in candidates
    assert "spotify" in candidates
    assert virtual.icon_candidates({"kind": "default"}, [])[0] == (
        "audio-speakers-symbolic"
    )
    assert virtual.icon_candidates(channel, streams) == (
        channel_icons.channel_icon_candidates(channel, streams)
    )


def test_hardware_icons_use_crisp_theme_or_vector_images() -> None:
    output = {"kind": "default", "label": "Default output device"}
    microphone = {"kind": "default", "label": "Microphone input"}
    disabled = {"kind": "disabled", "label": "Disabled"}
    assert channel_icons.channel_icon_role(output) == "speaker"
    assert channel_icons.channel_icon_role(microphone) == "microphone"
    assert channel_icons.channel_icon_role(disabled) == "muted"

    resolved = [
        channel_icons.load_channel_icon(channel, [], icon_size=32)
        for channel in (output, microphone, disabled)
    ]
    assert all(icon.size == (32, 32) for icon in resolved)
    assert all(icon.getchannel("A").getextrema()[1] > 0 for icon in resolved)
    assert resolved[0].tobytes() != resolved[1].tobytes()
    gear = channel_icons.load_emoji_fallback(None, icon_size=32)
    assert all(icon.tobytes() != gear.tobytes() for icon in resolved)

    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "wide-icon.png"
        Image.new("RGBA", (128, 64), (210, 40, 60, 255)).save(source)
        application = {
            "kind": "application",
            "label": "Test application",
            "application_id": str(source),
        }
        icon = channel_icons.load_channel_icon(application, [], icon_size=24)
        assert icon.size == (24, 24)


def test_volume_and_action_labels() -> None:
    assert virtual.parse_wpctl_volume("Volume: 0.625 [MUTED]") == 0.625
    assert virtual.parse_wpctl_volume("unexpected") is None
    assert virtual.action_label(
        "set_channel_volume", {"channel": 3, "percentage": 75}
    ) == "Set volume: C3 · 75%"
    assert virtual.action_label(
        "play_pause", {"channel": 1, "percentage": 50}
    ) == "Play / pause"
    assert virtual.compact_action_label(
        "set_channel_volume", {"channel": 3, "percentage": 75}
    ) == "C3 · 75%"


def test_aspect_locked_window_sizing() -> None:
    assert virtual.aspect_locked_size(960) == (960, 544)
    assert virtual.aspect_locked_size(800) == (800, 453)
    assert virtual.aspect_locked_size(400) == (720, 408)
    assert virtual.resize_width_from_drag(960, 544, -240, 0) == 720
    assert virtual.resize_width_from_drag(960, 544, 0, 68) == 1080


if __name__ == "__main__":
    test_page_loading()
    test_legacy_and_invalid_config_fallback()
    test_background_and_meter_preferences()
    test_stream_resolution()
    test_icon_candidates()
    test_hardware_icons_use_crisp_theme_or_vector_images()
    test_volume_and_action_labels()
    test_aspect_locked_window_sizing()
    print("Virtual mixer tests passed.")
