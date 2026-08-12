#!/usr/bin/python3
"""Persistent per-user display broker for OpenStream100."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from typing import BinaryIO


APP_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_HELPER = APP_DIRECTORY / "stream100-display-helper"
DEFAULT_REPLAY = APP_DIRECTORY / "stream100-display-replay.bin"
FRAME_BYTES = 512 + 480 * 272
METADATA_OFFSET = 512 - 32
HANDSHAKE_MAGIC = b"OSD1"
ACK = b"\x06"


def default_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise RuntimeError("XDG_RUNTIME_DIR is unavailable")
    return Path(runtime) / "openstream100" / "display.sock"


@dataclass
class DisplayState:
    has_frame: bool = False
    display_mode: int = 0xFF

    def handshake(self) -> bytes:
        return HANDSHAKE_MAGIC + bytes((int(self.has_frame), self.display_mode))

    def observe(self, frame: bytes) -> None:
        self.has_frame = True
        if frame[METADATA_OFFSET : METADATA_OFFSET + 4] == b"S1C2":
            self.display_mode = frame[METADATA_OFFSET + 10]
        else:
            self.display_mode = 0xFF


def receive_exact(connection: socket.socket, size: int) -> bytes | None:
    data = bytearray(size)
    view = memoryview(data)
    received = 0
    while received < size:
        try:
            count = connection.recv_into(view[received:])
        except socket.timeout:
            continue
        if count == 0:
            if received == 0:
                return None
            raise RuntimeError("display client disconnected during a framebuffer")
        received += count
    return bytes(data)


def relay_client(
    connection: socket.socket,
    helper_input: BinaryIO,
    state: DisplayState,
) -> None:
    connection.sendall(state.handshake())
    while True:
        frame = receive_exact(connection, FRAME_BYTES)
        if frame is None:
            return
        helper_input.write(frame)
        helper_input.flush()
        state.observe(frame)
        connection.sendall(ACK)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Keep the OpenStream100 USB display session alive."
    )
    parser.add_argument("--display-helper", type=Path, default=DEFAULT_HELPER)
    parser.add_argument("--display-replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--socket", type=Path, default=None)
    return parser.parse_args()


def run(helper_path: Path, replay_path: Path, socket_path: Path) -> int:
    if not helper_path.exists():
        raise RuntimeError(f"display helper does not exist: {helper_path}")
    if not replay_path.exists():
        raise RuntimeError(f"display replay does not exist: {replay_path}")

    stopping = threading.Event()

    def request_stop(_signal_number, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass

    helper = subprocess.Popen(
        [str(helper_path), str(replay_path)],
        stdin=subprocess.PIPE,
        bufsize=0,
    )
    if helper.stdin is None:
        helper.terminate()
        raise RuntimeError("could not open the display helper input")

    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        listener.listen(1)
        listener.settimeout(0.5)
        state = DisplayState()
        print(f"OpenStream100 display broker listening at {socket_path}.", flush=True)

        while not stopping.is_set():
            return_code = helper.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"native display helper exited with status {return_code}"
                )
            try:
                connection, _address = listener.accept()
            except socket.timeout:
                continue
            print(
                "Mixer display client connected "
                f"(resident mode {state.display_mode if state.has_frame else 'cold'}).",
                flush=True,
            )
            with connection:
                connection.settimeout(0.5)
                try:
                    relay_client(connection, helper.stdin, state)
                except (BrokenPipeError, ConnectionError, OSError, RuntimeError) as error:
                    if not stopping.is_set():
                        print(f"Display client ended: {error}", file=sys.stderr, flush=True)
            print("Mixer display client disconnected; USB session retained.", flush=True)
    finally:
        listener.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        try:
            helper.stdin.close()
        except OSError:
            pass
        try:
            helper.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            helper.terminate()
            try:
                helper.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                helper.kill()
                helper.wait()
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run(
            args.display_helper,
            args.display_replay,
            args.socket if args.socket is not None else default_socket_path(),
        )
    except RuntimeError as error:
        print(f"Display broker error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
