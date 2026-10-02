#!/usr/bin/python3
"""Tests for tray display-mode persistence and GTK menu actions."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "stream100_tray_test", Path(__file__).with_name("stream100-tray.py")
)
tray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / "config.json"
        patcher = mock.patch.object(tray, "CONFIG_PATH", self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(
            tray, "systemctl", return_value=subprocess.CompletedProcess([], 0, "", "")
        )
        self.systemctl = patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_modes_preserve_other_settings(self):
        original = {"channels": [{"label": "Music"}], "notepad_text": "Notes",
                    "fullscreen_image": "/tmp/art.png", "remote_enabled": True}
        self.config.write_text(json.dumps(original))
        for mode in ("image", "notepad", "system", "mixer"):
            tray.select_display_mode(mode)
            self.assertEqual(tray.load_display_mode(), mode)
            self.assertEqual(json.loads(self.config.read_text()),
                             dict(original, display_mode=mode))
        self.assertEqual(self.systemctl.call_count, 4)
        self.systemctl.assert_called_with("try-restart", tray.SERVICE_NAME)
        self.assertEqual(list(self.config.parent.glob("*.tmp")), [])

    def test_missing_configuration(self):
        self.assertEqual(tray.load_display_mode(), "mixer")
        tray.select_display_mode("notepad")
        self.assertEqual(tray.load_display_mode(), "notepad")

    def test_current_mode_does_not_restart(self):
        self.config.write_text('{"display_mode": "system"}')
        tray.select_display_mode("system")
        self.systemctl.assert_not_called()

    def test_invalid_mode_does_not_write(self):
        with self.assertRaises(ValueError):
            tray.select_display_mode("invalid")
        self.assertFalse(self.config.exists())
        self.systemctl.assert_not_called()

    def test_bad_configuration_is_preserved(self):
        for text in ("invalid json", "[]"):
            self.config.write_text(text)
            with self.assertRaises(ValueError):
                tray.select_display_mode("image")
            self.assertEqual(self.config.read_text(), text)
        self.systemctl.assert_not_called()

    def test_failed_restart_keeps_saved_mode_and_reports_error(self):
        self.systemctl.return_value = subprocess.CompletedProcess([], 1, "", "Failed")
        with self.assertRaisesRegex(RuntimeError, "Failed"):
            tray.select_display_mode("image")
        self.assertEqual(tray.load_display_mode(), "image")

    def test_gtk_menu_tracks_changes_without_recursive_restarts(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import Gtk, GLib, AyatanaAppIndicator3 as AppIndicator
        except (ImportError, ValueError):
            self.skipTest("GTK 3 and Ayatana AppIndicator are unavailable")
        if not Gtk.init_check()[0]:
            self.skipTest("No display available")
        self.config.write_text('{"display_mode": "notepad"}')
        indicator = mock.Mock()
        with mock.patch.object(AppIndicator.Indicator, "new", return_value=indicator), \
             mock.patch.object(Gtk, "main"), \
             mock.patch.object(GLib, "timeout_add_seconds") as timer, \
             mock.patch.object(tray.signal, "signal"), \
             mock.patch.object(tray, "mixer_running", return_value=False):
            self.assertEqual(tray.main(), 0)
            menu = indicator.set_menu.call_args.args[0]
            mode_menu = next(item for item in menu.get_children()
                             if item.get_label() == "Display mode").get_submenu()
            items = mode_menu.get_children()
            self.assertEqual([item.get_label() for item in items],
                             [label for _, label in tray.DISPLAY_MODES])
            self.assertTrue(items[2].get_active())
            self.systemctl.assert_not_called()
            items[1].activate()
            self.assertEqual(tray.load_display_mode(), "image")
            self.systemctl.assert_called_once_with("try-restart", tray.SERVICE_NAME)
            # A change from the control panel updates the radio selection only.
            self.config.write_text('{"display_mode": "system"}')
            timer.call_args.args[1]()
            self.assertTrue(items[3].get_active())
            self.assertEqual(self.systemctl.call_count, 1)
            menu.destroy()


if __name__ == "__main__":
    unittest.main()
