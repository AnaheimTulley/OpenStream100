#!/usr/bin/python3
"""Regression tests for the saved OpenStream100 notepad display mode."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw


SOURCE_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SOURCE_DIRECTORY))


def load_source_module(module_name: str, filename: str):
    specification = importlib.util.spec_from_file_location(
        module_name, SOURCE_DIRECTORY / filename
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class NotepadModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.control = load_source_module("stream100_control_test", "stream100-control.py")
        cls.mixer = load_source_module("stream100_mixer_test", "stream100-mixer-alpha.py")
        cls.channels = [
            {"kind": "default", "label": "Default output", "color": "#30CCBE"},
            {"kind": "disabled", "label": "Disabled", "color": "#36D380"},
            {"kind": "disabled", "label": "Disabled", "color": "#F6BE40"},
            {"kind": "disabled", "label": "Disabled", "color": "#5B82F6"},
        ]

    def test_control_panel_saves_normalized_note_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            original_path = self.control.CONFIG_PATH
            self.control.CONFIG_PATH = config_path
            try:
                self.control.save_notepad_text("First line\r\nSecond line\rThird")
                self.assertEqual(
                    self.control.load_notepad_text(),
                    "First line\nSecond line\nThird",
                )
                payload = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["notepad_text"], "First line\nSecond line\nThird")
                self.assertEqual(payload["version"], 1)
                self.assertEqual(len(payload["channels"]), 4)
            finally:
                self.control.CONFIG_PATH = original_path

    def test_control_panel_saves_validated_typography(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            original_path = self.control.CONFIG_PATH
            self.control.CONFIG_PATH = config_path
            try:
                self.control.save_notepad_style(
                    {
                        "font_size": 32,
                        "font_family": "serif",
                        "font_style": "bold-italic",
                        "text_color": "#a1b2c3",
                        "alignment": "center",
                    }
                )
                self.assertEqual(
                    self.control.load_notepad_style(),
                    {
                        "font_size": 32,
                        "font_family": "serif",
                        "font_style": "bold-italic",
                        "text_color": "#A1B2C3",
                        "alignment": "center",
                    },
                )
            finally:
                self.control.CONFIG_PATH = original_path

    def test_control_panel_finds_virtual_mixer_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            local_runner = root / "local-runner"
            packaged_runner = root / "packaged-runner"
            packaged_runner.touch()
            self.assertEqual(
                self.control.find_virtual_mixer_runner(
                    local_runner, packaged_runner
                ),
                packaged_runner,
            )
            local_runner.touch()
            self.assertEqual(
                self.control.find_virtual_mixer_runner(
                    local_runner, packaged_runner
                ),
                local_runner,
            )

    def test_control_panel_launches_virtual_mixer_in_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            runner = Path(temporary_directory) / "virtual-mixer-runner"
            runner.touch()
            with mock.patch.object(self.control.subprocess, "Popen") as popen:
                launched, message = self.control.launch_virtual_mixer(runner)
            self.assertTrue(launched)
            self.assertEqual(message, "Virtual mixer opened with the saved pages.")
            popen.assert_called_once_with([str(runner)], start_new_session=True)

    def test_mixer_loads_notepad_mode_and_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "display_mode": "notepad",
                        "notepad_text": "Cue list\r\nIntro",
                        "notepad_style": {
                            "font_size": 18,
                            "font_family": "monospace",
                            "font_style": "italic",
                            "text_color": "#36D380",
                            "alignment": "right",
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(self.mixer.load_display_mode(config_path), "notepad")
            self.assertEqual(self.mixer.load_notepad_text(config_path), "Cue list\nIntro")
            self.assertEqual(
                self.mixer.load_notepad_style(config_path)["font_family"],
                "monospace",
            )

    def test_notepad_frame_uses_full_screen_hardware_mode(self) -> None:
        frame = self.mixer.render_notepad_display(
            self.channels,
            [[], [], [], []],
            [False, False, False, False],
            [0.5, 0.5, 0.5, 0.5],
            "Stream checklist\n• Check microphone\n• Start recording",
            button_leds=[1, 0, 1, 0],
            display_brightness=75,
        )
        self.assertEqual(len(frame), self.mixer.DISPLAY_MESSAGE_BYTES)
        metadata_start = self.mixer.DISPLAY_PALETTE_BYTES - 32
        metadata = frame[metadata_start : self.mixer.DISPLAY_PALETTE_BYTES]
        self.assertEqual(metadata[:4], b"S1C3")
        self.assertEqual(metadata[10], 5)
        encoded_brightness = (metadata[29] & 0xF0) | (metadata[9] >> 4)
        self.assertEqual(encoded_brightness - 1, 75)

    def test_hardware_icon_cache_follows_the_current_mixer_page(self) -> None:
        page_one = [{"kind": "default", "label": "Page one output"}]
        page_two = [{"kind": "default-source", "label": "Page two microphone"}]

        def icon_for_channel(channel, _streams, icon_size=24):
            colour = (220, 30, 30, 255) if channel["label"].startswith("Page one") else (30, 90, 220, 255)
            return Image.new("RGBA", (icon_size, icon_size), colour)

        self.mixer._clear_icon_cache()
        with mock.patch(
            "stream100_channel_icons.load_channel_icon",
            side_effect=icon_for_channel,
        ):
            page_one_icons, _changed = self.mixer._resolve_channel_icons_for_streams(
                page_one, [[]]
            )
            canvas = Image.new("RGBA", (480, 272), (0, 0, 0, 255))
            self.mixer._draw_channel_icons_on_mixer(
                canvas, ImageDraw.Draw(canvas), page_two, [[]]
            )
            page_two_icon = self.mixer._icon_cache[
                self.mixer._icon_cache_key(0, -1)
            ]
            self.mixer._draw_channel_icons_on_mixer(
                canvas, ImageDraw.Draw(canvas), page_one, [[]]
            )

        current_icon = self.mixer._icon_cache[self.mixer._icon_cache_key(0, -1)]
        self.assertEqual(page_one_icons[0].getpixel((0, 0)), (220, 30, 30, 255))
        self.assertEqual(page_two_icon.getpixel((0, 0)), (30, 90, 220, 255))
        self.assertEqual(current_icon.getpixel((0, 0)), (220, 30, 30, 255))

    def test_long_notes_are_ellipsized_within_the_body(self) -> None:
        image = Image.new("RGB", (480, 272))
        draw = ImageDraw.Draw(image)
        font, lines, line_height, truncated = self.mixer.fit_note_text(
            draw,
            " ".join(["reference"] * 300),
            434,
            196,
        )
        self.assertTrue(truncated)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertLessEqual(len(lines) * line_height, 196)
        self.assertTrue(all(draw.textlength(line, font=font) <= 434 for line in lines))

    def test_fixed_font_colour_and_alignment_are_applied(self) -> None:
        image = Image.new("RGB", (480, 272))
        draw = ImageDraw.Draw(image)
        font, lines, _line_height, _truncated = self.mixer.fit_note_text(
            draw,
            "Aligned note",
            434,
            196,
            font_size=32,
            font_family="monospace",
            font_style="bold-italic",
        )
        self.assertEqual(getattr(font, "size", 32), 32)
        left = self.mixer.note_line_x(draw, lines[0], font, 23, 434, "left")
        center = self.mixer.note_line_x(draw, lines[0], font, 23, 434, "center")
        right = self.mixer.note_line_x(draw, lines[0], font, 23, 434, "right")
        self.assertEqual(left, 23)
        self.assertLess(left, center)
        self.assertLess(center, right)

        frame = self.mixer.render_notepad_display(
            self.channels,
            [[], [], [], []],
            [False, False, False, False],
            [0.5, 0.5, 0.5, 0.5],
            "Magenta reference",
            notepad_style={
                "font_size": 24,
                "font_family": "serif",
                "font_style": "bold",
                "text_color": "#FF00FF",
                "alignment": "center",
            },
        )
        palette_entry = int.from_bytes(frame[30:32], "little")
        self.assertEqual(palette_entry, 0xF81F)


if __name__ == "__main__":
    unittest.main()
