#!/usr/bin/python3
"""Regression tests for OpenStream and Hercules mixer badge layouts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image


SOURCE_DIRECTORY = Path(__file__).resolve().parent


def load_source_module(module_name: str, filename: str):
    specification = importlib.util.spec_from_file_location(
        module_name, SOURCE_DIRECTORY / filename
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"could not load {filename}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class BadgeStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.control = load_source_module("stream100_control_badges", "stream100-control.py")
        cls.mixer = load_source_module("stream100_mixer_badges", "stream100-mixer-alpha.py")
        cls.preview = load_source_module("stream100_preview_badges", "stream100_preview.py")
        cls.channels = [
            {"kind": "application", "label": f"Application {index + 1}", "color": "#30CCBE"}
            for index in range(4)
        ]
        cls.targets = [["preview"] for _index in range(4)]
        cls.levels = [0.25, 0.50, 0.75, 1.0]

    def render(self, style: str, transient_mask: int = 0):
        icon_sizes: list[int] = []

        def solid_icon(_index, _channel, _streams, icon_size):
            icon_sizes.append(icon_size)
            return Image.new("RGBA", (icon_size, icon_size), (255, 0, 255, 255)), True

        with mock.patch.object(self.mixer, "_cached_channel_icon", side_effect=solid_icon):
            frame = self.mixer.render_mixer_display(
                self.channels,
                self.targets,
                [False] * 4,
                self.levels,
                preview_levels=self.levels,
                native_overlay=True,
                streams_by_ch=[[] for _channel in self.channels],
                badge_style=style,
                transient_volume_mask=transient_mask,
            )
        return frame, icon_sizes

    def test_badge_style_setting_defaults_validates_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            original_path = self.control.CONFIG_PATH
            self.control.CONFIG_PATH = config_path
            try:
                self.assertEqual(self.control.load_badge_style(), "openstream")
                self.control.save_badge_style("hercules")
                self.assertEqual(self.control.load_badge_style(), "hercules")
                payload = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["badge_style"], "hercules")
                with self.assertRaises(RuntimeError):
                    self.control.save_badge_style("unknown")
            finally:
                self.control.CONFIG_PATH = original_path

    def test_control_panel_exposes_both_badge_style_choices(self) -> None:
        self.assertEqual(
            self.control.BADGE_STYLE_CHOICES,
            (
                ("openstream", "OpenStream Style (icon and percentage)"),
                ("hercules", "Hercules Style (icon only)"),
            ),
        )

    def test_metadata_encodes_hercules_style_without_changing_meter_bits(self) -> None:
        openstream, _sizes = self.render("openstream")
        hercules, _sizes = self.render("hercules")
        offset = self.mixer.DISPLAY_PALETTE_BYTES - 32
        self.assertEqual(openstream[offset + 30] & 0x04, 0)
        self.assertEqual(hercules[offset + 30] & 0x04, 0x04)
        self.assertEqual(openstream[offset + 30] & 0x03, hercules[offset + 30] & 0x03)

    def test_hercules_uses_larger_centered_icons(self) -> None:
        frame, icon_sizes = self.render("hercules")
        image = self.preview.decode_display_frame(frame, self.mixer)
        self.assertEqual(icon_sizes, [32, 32, 32, 32])
        self.assertNotEqual(image.getpixel((60, 20)), image.getpixel((102, 20)))

    def test_transient_percentage_uses_metadata_without_framebuffer_refresh(self) -> None:
        normal, _sizes = self.render("hercules")
        changed, _sizes = self.render("hercules", transient_mask=0b0010)
        framebuffer_offset = self.mixer.DISPLAY_PALETTE_BYTES
        normal_pixels = normal[framebuffer_offset:]
        changed_pixels = changed[framebuffer_offset:]
        self.assertEqual(normal_pixels, changed_pixels)
        offset = self.mixer.DISPLAY_PALETTE_BYTES - 32
        self.assertEqual(normal[offset + 30] >> 4, 0)
        self.assertEqual(changed[offset + 30] >> 4, 0b0010)
        # Only metadata changes. The helper handles the temporary value on the
        # native object layer and never sends a framebuffer plane or latch.
        self.assertEqual(
            normal[:offset],
            changed[:offset],
        )


if __name__ == "__main__":
    unittest.main()
