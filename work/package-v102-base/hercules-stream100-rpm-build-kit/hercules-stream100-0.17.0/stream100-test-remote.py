#!/usr/bin/python3
"""Regression checks for the OpenStream100 remote mixer protocol."""

from __future__ import annotations

from http import HTTPStatus
import importlib.util
import json
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import stream100_remote as remote


def load_mixer_module():
    path = Path(__file__).with_name("stream100-mixer-alpha.py")
    spec = importlib.util.spec_from_file_location("stream100_remote_mixer_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request_json(
    url: str,
    token: str | None = None,
    payload: dict[str, object] | None = None,
    method: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {}
    body = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=2)
    except HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


def test_token_file() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "remote-token"
        first = remote.load_or_create_token(path)
        second = remote.load_or_create_token(path)
        assert first == second
        assert len(first) >= 32
        assert path.stat().st_mode & 0o077 == 0
        assert len(remote.token_fingerprint(first)) == 12
        uri = remote.pairing_uri("192.168.1.20:47680", first)
        assert uri.startswith("openstream100://pair?")
        assert "server=192.168.1.20%3A47680" in uri


def test_command_validation() -> None:
    command = remote.parse_command(
        {
            "protocol": 1,
            "request_id": "android-42",
            "command": "set_volume",
            "channel": 2,
            "value": 0.625,
        }
    )
    assert command.channel == 2
    assert command.value == 0.625
    for invalid in (
        {},
        {"protocol": 2, "request_id": "1", "command": "toggle_mute", "channel": 0},
        {"protocol": 1, "request_id": "1", "command": "set_volume", "channel": 4, "value": 0.5},
        {"protocol": 1, "request_id": "1", "command": "set_volume", "channel": 0, "value": 2},
    ):
        try:
            remote.parse_command(invalid)
        except remote.RemoteProtocolError:
            pass
        else:
            raise AssertionError(f"invalid command passed validation: {invalid}")


def test_authenticated_http_transport() -> None:
    bridge = remote.RemoteBridge()
    bridge.publish(
        {
            "connected": True,
            "page": 0,
            "page_count": 1,
            "channels": [{"label": "Music", "level": 0.5}],
        }
    )
    icon_path = "/api/v1/icons/0/0.png"
    bridge.publish_asset(icon_path, b"test-png", "image/png")
    token = "test-token-that-is-long-enough-for-authentication"
    server = remote.RemoteServer(bridge, token, host="127.0.0.1", port=0)
    server.start()
    host, port = server.address
    base = f"http://{host}:{port}/api/v1"
    try:
        status, health = request_json(base + "/health")
        assert status == HTTPStatus.OK
        assert health["protocol"] == 1

        status, unauthorized = request_json(base + "/state")
        assert status == HTTPStatus.UNAUTHORIZED
        assert "authorization" in str(unauthorized["error"])

        status, state = request_json(base + "/state", token)
        assert status == HTTPStatus.OK
        assert state["channels"][0]["label"] == "Music"
        icon_request = Request(
            f"http://{host}:{port}{icon_path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urlopen(icon_request, timeout=2) as icon_response:
            assert icon_response.headers.get_content_type() == "image/png"
            assert icon_response.read() == b"test-png"

        with urlopen(base + "/pairing.svg", timeout=2) as qr_response:
            assert qr_response.headers.get_content_type() == "image/svg+xml"
            assert b"<svg" in qr_response.read()

        status, accepted = request_json(
            base + "/command",
            token,
            {
                "protocol": 1,
                "request_id": "phone-1",
                "command": "toggle_mute",
                "channel": 0,
            },
        )
        assert status == HTTPStatus.ACCEPTED
        assert accepted["request_id"] == "phone-1"
        status, duplicate = request_json(
            base + "/command",
            token,
            {
                "protocol": 1,
                "request_id": "phone-1",
                "command": "toggle_mute",
                "channel": 0,
            },
        )
        assert status == HTTPStatus.ACCEPTED
        assert duplicate["duplicate"] is True
        commands = bridge.drain()
        assert len(commands) == 1
        assert commands[0].name == "toggle_mute"
    finally:
        server.close()


def test_pin_pairing_and_device_revocation() -> None:
    bridge = remote.RemoteBridge()
    bridge.publish({"connected": True, "page": 0, "page_count": 1, "channels": []})
    legacy_token = "legacy-token-that-is-long-enough-for-authentication"
    with tempfile.TemporaryDirectory() as directory:
        store_path = Path(directory) / "remote-devices.json"
        server = remote.RemoteServer(
            bridge,
            legacy_token,
            host="127.0.0.1",
            port=0,
            device_store_path=store_path,
        )
        server.start()
        host, port = server.address
        base = f"http://{host}:{port}/api/v1"
        try:
            status, requested = request_json(
                base + "/pair/request",
                payload={
                    "protocol": 1,
                    "device_id": "android-test-1",
                    "device_name": "Test phone",
                },
                method="POST",
            )
            assert status == HTTPStatus.ACCEPTED
            assert "pin" not in requested["pairing"]
            status, pairing_admin = request_json(base + "/admin/remote")
            assert status == HTTPStatus.OK
            assert pairing_admin["pairing"]["device_name"] == "Test phone"
            pin = str(pairing_admin["pairing"]["pin"])
            assert len(pin) == 6 and pin.isdigit()

            status, rejected = request_json(
                base + "/pair/complete",
                payload={
                    "protocol": 1,
                    "pin": "999999" if pin != "999999" else "888888",
                    "device_id": "android-test-1",
                    "device_name": "Test phone",
                },
                method="POST",
            )
            assert status == HTTPStatus.FORBIDDEN
            assert "incorrect" in str(rejected["error"])

            status, paired = request_json(
                base + "/pair/complete",
                payload={
                    "protocol": 1,
                    "pin": pin,
                    "device_id": "android-test-1",
                    "device_name": "Test phone",
                },
                method="POST",
            )
            assert status == HTTPStatus.CREATED
            device_token = str(paired["token"])
            assert len(device_token) >= 32
            assert device_token not in store_path.read_text(encoding="utf-8")
            assert remote.PairedDeviceStore(store_path).authorize(device_token)

            status, state = request_json(base + "/state", device_token)
            assert status == HTTPStatus.OK
            assert state["connected"] is True

            status, admin = request_json(base + "/admin/remote")
            assert status == HTTPStatus.OK
            assert admin["devices"][0]["name"] == "Test phone"
            assert "token_hash" not in admin["devices"][0]

            status, revoked = request_json(
                base + "/admin/devices/android-test-1", method="DELETE"
            )
            assert status == HTTPStatus.OK
            assert revoked["revoked"] is True
            status, unauthorized = request_json(base + "/state", device_token)
            assert status == HTTPStatus.UNAUTHORIZED
        finally:
            server.close()


def test_phone_snapshot_contract() -> None:
    mixer = load_mixer_module()
    page = {
        "channels": [
            {"kind": "application", "label": "Music", "color": "#30CCBE"},
            {"kind": "disabled", "label": "Disabled", "color": "#36D380"},
            {"kind": "disabled", "label": "Disabled", "color": "#F6BE40"},
            {"kind": "disabled", "label": "Disabled", "color": "#5B82F6"},
        ],
        "button_actions": ["play_pause", "disabled", "disabled", "set_channel_volume"],
        "button_volume_presets": [
            {"channel": 1, "percentage": 50},
            {"channel": 2, "percentage": 50},
            {"channel": 3, "percentage": 50},
            {"channel": 1, "percentage": 75},
        ],
    }
    snapshot = mixer.build_remote_snapshot(
        [page],
        0,
        [["42"], [], [], []],
        [[False, False, False, False]],
        [0.625, 0.0, 0.0, 0.0],
        [(0.25, 0.5), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
        icon_paths=["/api/v1/icons/0/0.png?v=abc", None, None, None],
    )
    assert snapshot["connected"] is True
    assert snapshot["channels"][0]["available"] is True
    assert snapshot["channels"][0]["level"] == 0.625
    assert snapshot["channels"][0]["meter_right"] == 0.5
    assert snapshot["channels"][0]["icon"].endswith("?v=abc")
    assert snapshot["actions"][3]["target_channel"] == 0
    assert snapshot["actions"][3]["percentage"] == 75


if __name__ == "__main__":
    test_token_file()
    test_command_validation()
    test_authenticated_http_transport()
    test_pin_pairing_and_device_revocation()
    test_phone_snapshot_contract()
    print("Remote mixer tests passed.")
