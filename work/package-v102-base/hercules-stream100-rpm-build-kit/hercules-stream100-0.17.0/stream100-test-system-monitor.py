#!/usr/bin/python3
"""Tests for dependency-free System Monitor metric parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

from stream100_system_monitor import (
    ConfiguredSystemSnapshot,
    SystemSnapshot,
    cpu_usage_between,
    parse_cpu_times,
    parse_memory_usage,
    parse_temperature,
)


class SystemMonitorTests(unittest.TestCase):
    def test_cpu_parser_and_delta(self) -> None:
        previous = parse_cpu_times("cpu  100 0 50 850 0 0 0 0\n")
        current = parse_cpu_times("cpu  130 0 60 910 0 0 0 0\n")
        self.assertEqual(previous, (1000, 850))
        self.assertEqual(current, (1100, 910))
        self.assertAlmostEqual(cpu_usage_between(previous, current), 0.4)

    def test_memory_uses_available_value(self) -> None:
        text = "MemTotal: 1000000 kB\nMemFree: 100000 kB\nMemAvailable: 250000 kB\n"
        self.assertAlmostEqual(parse_memory_usage(text), 0.75)

    def test_temperature_formats_and_limits(self) -> None:
        self.assertEqual(parse_temperature("62000\n"), 62.0)
        self.assertEqual(parse_temperature("54.5\n"), 54.5)
        self.assertIsNone(parse_temperature("999000\n"))
        self.assertIsNone(parse_temperature("unavailable\n"))

    def test_snapshot_maps_usage_and_temperature_to_native_bars(self) -> None:
        snapshot = SystemSnapshot(0.4, 65.0, None, None, 0.75, 0.25)
        self.assertEqual(snapshot.levels, [0.4, 0.0, 0.75, 0.25])
        self.assertEqual(
            snapshot.meter_levels,
            [(0.4, 0.65), (0.0, 0.0), (0.75, 0.75), (0.25, 0.25)],
        )

    def test_system_monitor_frame_uses_separate_framebuffer_meters(self) -> None:
        mixer_path = Path(__file__).with_name("stream100-mixer-alpha.py")
        spec = importlib.util.spec_from_file_location("stream100_mixer_alpha", mixer_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mixer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mixer)
        snapshot = SystemSnapshot(0.42, 61.0, 0.33, 55.0, 0.67, 0.81)
        frame = mixer.render_system_monitor_display(
            snapshot,
            [1, 0, 2, 0],
            ["disabled"] * 4,
            [{"channel": 1, "percentage": 50}] * 4,
            "classic",
            85,
        )
        metadata = frame[
            mixer.DISPLAY_PALETTE_BYTES - 32 : mixer.DISPLAY_PALETTE_BYTES
        ]
        self.assertEqual(len(frame), mixer.DISPLAY_MESSAGE_BYTES)
        self.assertEqual(metadata[:4], b"S1C3")
        self.assertEqual(metadata[4:8], bytes((42, 33, 67, 81)))
        self.assertEqual(metadata[10], 6)
        self.assertEqual(metadata[30], 0)
        changed_snapshot = SystemSnapshot(0.84, 72.0, 0.11, 47.0, 0.31, 0.44)
        changed_frame = mixer.render_system_monitor_display(
            changed_snapshot,
            [1, 0, 2, 0],
            ["disabled"] * 4,
            [{"channel": 1, "percentage": 50}] * 4,
            "classic",
            85,
        )
        framebuffer_start = mixer.DISPLAY_PALETTE_BYTES
        self.assertNotEqual(
            frame[framebuffer_start:], changed_frame[framebuffer_start:]
        )
        changed_planes = sum(
            frame[framebuffer_start + offset : framebuffer_start + offset + 4080]
            != changed_frame[
                framebuffer_start + offset : framebuffer_start + offset + 4080
            ]
            for offset in range(0, mixer.DISPLAY_FRAMEBUFFER_BYTES, 4080)
        )
        self.assertEqual(changed_planes, 4)
        self.assertEqual(
            frame[:mixer.DISPLAY_PALETTE_BYTES - 32],
            changed_frame[:mixer.DISPLAY_PALETTE_BYTES - 32],
        )
        warmer_gpu_frame = mixer.render_system_monitor_display(
            SystemSnapshot(0.42, 61.0, 0.33, 56.0, 0.67, 0.81),
            [1, 0, 2, 0],
            ["disabled"] * 4,
            [{"channel": 1, "percentage": 50}] * 4,
            "classic",
            85,
        )
        self.assertNotEqual(
            frame[framebuffer_start:], warmer_gpu_frame[framebuffer_start:]
        )
        with tempfile.TemporaryDirectory() as temporary:
            cache_path = Path(temporary) / "last-display-frame.bin"
            mixer.save_resident_display_frame(cache_path, frame)
            self.assertEqual(mixer.load_resident_display_frame(cache_path, b""), frame)

        service_path = Path(__file__).with_name("stream100-display-service.py")
        service_spec = importlib.util.spec_from_file_location(
            "stream100_display_service", service_path
        )
        self.assertIsNotNone(service_spec)
        self.assertIsNotNone(service_spec.loader)
        service = importlib.util.module_from_spec(service_spec)
        sys.modules[service_spec.name] = service
        service_spec.loader.exec_module(service)
        state = service.DisplayState()
        state.observe(frame)
        self.assertEqual(state.display_mode, 6)
        self.assertEqual(state.handshake(), b"OSD1\x01\x06")

    def test_system_monitor_icons_follow_configured_sources(self) -> None:
        mixer_path = Path(__file__).with_name("stream100-mixer-alpha.py")
        spec = importlib.util.spec_from_file_location(
            "stream100_mixer_icons", mixer_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mixer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mixer)

        snapshot = ConfiguredSystemSnapshot(
            ("disk:/home", "cpu", "memory", "gpu:0000:01:00.0"),
            ("DISK /HOME", "CPU + TEMP", "MEMORY", "GPU TEST"),
            (0.2, 0.3, 0.4, 0.5),
            ((0.2, 0.2), (0.3, 0.6), (0.4, 0.4), (0.5, 0.7)),
        )
        self.assertEqual(
            [
                mixer._system_monitor_icon_kind(source_id)
                for source_id in mixer.system_monitor_source_ids(snapshot)
            ],
            ["disk", "cpu", "memory", "gpu"],
        )

        frame = mixer.render_system_monitor_display(
            snapshot,
            [0] * 4,
            ["disabled"] * 4,
            [{"channel": 1, "percentage": 50}] * 4,
            "classic",
            100,
        )
        packed = frame[mixer.DISPLAY_PALETTE_BYTES:]
        row_major = bytearray(mixer.DISPLAY_FRAMEBUFFER_BYTES)
        for chunk, (x_offset, y_offset) in enumerate(mixer.DISPLAY_CHUNK_OFFSETS):
            cursor = chunk * 4080
            for y in range(y_offset, mixer.DISPLAY_HEIGHT, 4):
                row = y * mixer.DISPLAY_WIDTH
                for x in range(x_offset, mixer.DISPLAY_WIDTH, 8):
                    row_major[row + x] = packed[cursor]
                    cursor += 1

        icon_masks = []
        for column in range(4):
            left = column * 120 + 90
            icon_masks.append(
                frozenset(
                    (x - left, y - 11)
                    for y in range(11, 35)
                    for x in range(left, left + 24)
                    if row_major[y * mixer.DISPLAY_WIDTH + x] in {2, 3}
                )
            )
        self.assertTrue(all(icon_masks))
        self.assertEqual(len(set(icon_masks)), 4)

    def test_system_monitor_uses_custom_background(self) -> None:
        from PIL import Image

        mixer_path = Path(__file__).with_name("stream100-mixer-alpha.py")
        spec = importlib.util.spec_from_file_location(
            "stream100_mixer_background", mixer_path
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        mixer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mixer)
        snapshot = SystemSnapshot(0.42, 61.0, 0.33, 55.0, 0.67, 0.81)
        arguments = (
            snapshot,
            [0] * 4,
            ["disabled"] * 4,
            [{"channel": 1, "percentage": 50}] * 4,
            "classic",
            100,
        )

        plain_frame = mixer.render_system_monitor_display(*arguments)
        with tempfile.TemporaryDirectory() as temporary:
            background_path = Path(temporary) / "monitor-background.png"
            Image.new("RGB", (480, 272), (120, 20, 160)).save(background_path)
            background_frame = mixer.render_system_monitor_display(
                *arguments, background_image=background_path
            )

        self.assertNotEqual(
            plain_frame[:mixer.DISPLAY_PALETTE_BYTES - 32],
            background_frame[:mixer.DISPLAY_PALETTE_BYTES - 32],
        )
        self.assertNotEqual(
            plain_frame[mixer.DISPLAY_PALETTE_BYTES:],
            background_frame[mixer.DISPLAY_PALETTE_BYTES:],
        )


if __name__ == "__main__":
    unittest.main()
