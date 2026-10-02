#!/usr/bin/python3
"""Headless tests for the control panel's iOS sideload readiness checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().with_name("stream100-control.py")
SPEC = importlib.util.spec_from_file_location("stream100_control_ios", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


def result(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, "", "")


class IOSSideloadReadinessTests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        project = root / "OpenStream100Remote"
        project.mkdir()
        for name in ("Package.swift", "xtool.yml", "install-ios.sh"):
            (project / name).touch()
        return project

    def test_missing_project_and_xtool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready, messages = control.ios_sideload_readiness(root)
            self.assertFalse(ready)
            self.assertIn("source", messages[0])

            project = self.make_project(root)
            ready, messages = control.ios_sideload_readiness(
                project,
                which=lambda _name: None,
            )
            self.assertFalse(ready)
            self.assertIn("not installed", messages[0])

    def test_auth_sdk_and_ready_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_project(Path(directory))

            ready, messages = control.ios_sideload_readiness(
                project,
                which=lambda _name: "/usr/bin/xtool",
                run=lambda _arguments: result(1),
            )
            self.assertFalse(ready)
            self.assertIn("Apple account", messages[0])

            calls: list[list[str]] = []

            def missing_sdk(arguments: list[str]) -> subprocess.CompletedProcess[str]:
                calls.append(arguments)
                return result(0 if arguments[1] == "auth" else 1)

            ready, messages = control.ios_sideload_readiness(
                project,
                which=lambda _name: "/usr/bin/xtool",
                run=missing_sdk,
            )
            self.assertFalse(ready)
            self.assertIn("Darwin SDK", messages[0])
            self.assertEqual(calls[-1], ["xtool", "sdk", "status"])

            ready, messages = control.ios_sideload_readiness(
                project,
                which=lambda _name: "/usr/bin/xtool",
                run=lambda _arguments: result(0),
            )
            self.assertTrue(ready)
            self.assertIn("ready", messages[0])


class AndroidSideloadReadinessTests(unittest.TestCase):
    def test_missing_apk_and_adb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / "remote.apk"
            ready, messages = control.android_sideload_readiness(apk)
            self.assertFalse(ready)
            self.assertIn("Android app", messages[0])

            apk.touch()
            ready, messages = control.android_sideload_readiness(
                apk,
                which=lambda _name: None,
            )
            self.assertFalse(ready)
            self.assertIn("adb", messages[0])

    def test_device_states(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / "remote.apk"
            apk.touch()

            def devices(output: str) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess([], 0, output, "")

            ready, messages = control.android_sideload_readiness(
                apk,
                which=lambda _name: "/usr/bin/adb",
                run=lambda _arguments: devices(
                    "List of devices attached\nphone-1\tunauthorized\n"
                ),
            )
            self.assertFalse(ready)
            self.assertIn("authorised", messages[0])

            ready, messages = control.android_sideload_readiness(
                apk,
                which=lambda _name: "/usr/bin/adb",
                run=lambda _arguments: devices("List of devices attached\n"),
            )
            self.assertFalse(ready)
            self.assertIn("No Android device", messages[0])

            ready, messages = control.android_sideload_readiness(
                apk,
                which=lambda _name: "/usr/bin/adb",
                run=lambda _arguments: devices(
                    "List of devices attached\nphone-1\tdevice\nphone-2\tdevice\n"
                ),
            )
            self.assertFalse(ready)
            self.assertIn("only one", messages[0])

            ready, messages = control.android_sideload_readiness(
                apk,
                which=lambda _name: "/usr/bin/adb",
                run=lambda _arguments: devices(
                    "List of devices attached\nphone-1\tdevice\n"
                ),
            )
            self.assertTrue(ready)
            self.assertIn("phone-1", messages[0])


if __name__ == "__main__":
    unittest.main()
