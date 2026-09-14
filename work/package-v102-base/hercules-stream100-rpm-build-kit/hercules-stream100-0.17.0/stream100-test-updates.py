#!/usr/bin/python3
"""Headless tests for the OpenStream100 GitHub update checker."""

from __future__ import annotations

from email.message import Message
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError


MODULE_PATH = Path(__file__).resolve().with_name("stream100-control.py")
SPEC = importlib.util.spec_from_file_location("stream100_control_updates", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


class FakeResponse:
    def __init__(self, payload: object, etag: str = '"release-etag"') -> None:
        self.document = json.dumps(payload).encode("utf-8")
        self.headers = {"ETag": etag}

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.document[:limit]


class UpdateCheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_urlopen = control.urlopen

    def tearDown(self) -> None:
        control.urlopen = self.original_urlopen

    def test_version_parser_and_comparison(self) -> None:
        self.assertEqual(control.parse_release_version("v0.18.1"), (0, 18, 1))
        self.assertEqual(control.parse_release_version("1.2.3"), (1, 2, 3))
        for invalid in (None, "", "v1.2", "1.2.3-beta", "release-1.2.3"):
            self.assertIsNone(control.parse_release_version(invalid))

        release = {
            "version": "0.18.1",
            "tag": "v0.18.1",
            "name": "OpenStream100 0.18.1",
            "url": f"{control.UPDATE_RELEASES_URL}/tag/v0.18.1",
        }
        self.assertEqual(
            control.newer_release(release, "0.18.0"),
            release,
        )
        self.assertIsNone(control.newer_release(release, "0.18.1"))
        self.assertIsNone(control.newer_release(release, "0.19.0"))

    def test_release_validation(self) -> None:
        release = control.normalise_release(
            {
                "tag_name": "v0.19.0",
                "name": "OpenStream100 0.19.0",
                "html_url": f"{control.UPDATE_RELEASES_URL}/tag/v0.19.0",
            }
        )
        self.assertEqual(release["version"], "0.19.0")
        with self.assertRaises(RuntimeError):
            control.normalise_release(
                {
                    "tag_name": "v0.19.0",
                    "html_url": "https://example.com/not-the-project",
                }
            )

    def test_state_round_trip_and_interval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates.json"
            state = {"last_checked": 1000.0, "etag": '"test"'}
            control.save_update_state(state, path)
            self.assertEqual(control.load_update_state(path), state)
            self.assertFalse(control.update_check_due(state, now=1001.0, interval=10.0))
            self.assertTrue(control.update_check_due(state, now=1010.0, interval=10.0))
            self.assertTrue(control.update_check_due({}, now=1001.0, interval=10.0))

    def test_fetch_latest_release_and_headers(self) -> None:
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(
                {
                    "tag_name": "v0.19.0",
                    "name": "OpenStream100 0.19.0",
                    "html_url": f"{control.UPDATE_RELEASES_URL}/tag/v0.19.0",
                }
            )

        control.urlopen = fake_urlopen
        release, etag, not_modified = control.fetch_latest_release('"old"')
        self.assertEqual(release["version"], "0.19.0")
        self.assertEqual(etag, '"release-etag"')
        self.assertFalse(not_modified)
        request, timeout = requests[0]
        self.assertEqual(request.full_url, control.UPDATE_API_URL)
        self.assertEqual(request.get_header("If-none-match"), '"old"')
        self.assertIn("OpenStream100/", request.get_header("User-agent"))
        self.assertEqual(timeout, control.UPDATE_REQUEST_TIMEOUT_SECONDS)

    def test_not_modified_and_offline_results(self) -> None:
        def not_modified(request, timeout):
            del timeout
            raise HTTPError(
                request.full_url,
                304,
                "Not Modified",
                Message(),
                BytesIO(),
            )

        control.urlopen = not_modified
        self.assertEqual(
            control.fetch_latest_release('"same"'),
            (None, '"same"', True),
        )

        def offline(_request, timeout):
            del timeout
            raise URLError("offline")

        control.urlopen = offline
        with self.assertRaisesRegex(RuntimeError, "Could not reach GitHub"):
            control.fetch_latest_release()


if __name__ == "__main__":
    unittest.main()
