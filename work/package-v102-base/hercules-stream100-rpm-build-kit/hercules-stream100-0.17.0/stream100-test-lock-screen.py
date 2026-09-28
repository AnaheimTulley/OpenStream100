#!/usr/bin/python3
"""Headless tests for lock-screen protection."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().with_name("stream100-mixer-alpha.py")
SPEC = importlib.util.spec_from_file_location("stream100_mixer_lock_screen", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
mixer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mixer)


class LockScreenProtectionTests(unittest.TestCase):
    def test_lock_setting_is_opt_in_and_strictly_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertFalse(mixer.load_lock_screen_protection(path))
            path.write_text(json.dumps({"lock_screen_protection": True}))
            self.assertTrue(mixer.load_lock_screen_protection(path))
            path.write_text(json.dumps({"lock_screen_protection": "yes"}))
            self.assertFalse(mixer.load_lock_screen_protection(path))

    def test_session_query_uses_logind_locked_hint(self) -> None:
        original_run = mixer.subprocess.run
        calls: list[list[str]] = []

        def fake_run(arguments, **_kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 0, "yes\n", "")

        mixer.subprocess.run = fake_run
        try:
            self.assertTrue(mixer.query_session_locked("test-session"))
        finally:
            mixer.subprocess.run = original_run
        self.assertEqual(calls[0][2], "test-session")
        self.assertIn("--property=LockedHint", calls[0])

    def test_brightness_zero_preserves_the_rendered_frame(self) -> None:
        repeats, remainder = divmod(mixer.DISPLAY_MESSAGE_BYTES, 256)
        frame = bytearray(range(256)) * repeats
        frame.extend(range(remainder))
        metadata_offset = mixer.DISPLAY_PALETTE_BYTES - 32
        frame[metadata_offset : metadata_offset + 4] = b"S1C3"
        frame[metadata_offset + 9] = 0xA5
        frame[metadata_offset + 29] = 0xB3

        blanked = mixer.update_display_brightness(bytes(frame), 0)
        changed = [
            index
            for index, (before, after) in enumerate(zip(frame, blanked))
            if before != after
        ]
        self.assertEqual(changed, [metadata_offset + 9, metadata_offset + 29])
        self.assertEqual(blanked[metadata_offset + 9], 0x15)
        self.assertEqual(blanked[metadata_offset + 29], 0x03)

        restored = mixer.update_display_brightness(blanked, 75)
        encoded = 76
        self.assertEqual(restored[metadata_offset + 9] >> 4, encoded & 0x0F)
        self.assertEqual(restored[metadata_offset + 29] & 0xF0, encoded & 0xF0)


if __name__ == "__main__":
    unittest.main()
