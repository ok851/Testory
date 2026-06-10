# -*- coding: utf-8 -*-
"""投屏后端配置与 scrcpy 触控消息。"""

import json
import struct
from unittest.mock import MagicMock, patch

from mobile_env_config import (
    emulator_scrcpy_ws_enabled,
    resolve_mirror_backend,
    scrcpy_mirror_fps,
)
from mobile_scrcpy_bridge import _handle_ws_control_message
from mobile_mirror import start_scrcpy_mirror


def test_resolve_mirror_backend_emulator_default_scrcpy_ws(monkeypatch):
    monkeypatch.delenv("MOBILE_MIRROR_BACKEND", raising=False)
    monkeypatch.setenv("MOBILE_EMULATOR_SCRCPY", "1")
    assert resolve_mirror_backend("emulator-5554") == "scrcpy_ws"


def test_resolve_mirror_backend_emulator_opt_out_screencap(monkeypatch):
    monkeypatch.delenv("MOBILE_MIRROR_BACKEND", raising=False)
    monkeypatch.setenv("MOBILE_EMULATOR_SCRCPY", "0")
    assert resolve_mirror_backend("emulator-5554") == "screencap"


def test_resolve_mirror_backend_real_device_screencap(monkeypatch):
    monkeypatch.delenv("MOBILE_MIRROR_BACKEND", raising=False)
    monkeypatch.setenv("MOBILE_EMULATOR_SCRCPY", "1")
    assert resolve_mirror_backend("192.168.1.5:5555") == "screencap"


def test_scrcpy_mirror_fps_default(monkeypatch):
    monkeypatch.delenv("MOBILE_SCRCPY_FPS", raising=False)
    monkeypatch.delenv("MOBILE_MIRROR_FPS", raising=False)
    assert scrcpy_mirror_fps() == 24


def test_emulator_scrcpy_ws_enabled_default(monkeypatch):
    monkeypatch.delenv("MOBILE_EMULATOR_SCRCPY", raising=False)
    assert emulator_scrcpy_ws_enabled() is True


def test_start_scrcpy_mirror_skips_external_window_for_emulator(monkeypatch):
    monkeypatch.setattr(
        "mobile_mirror.subprocess.Popen",
        MagicMock(side_effect=AssertionError("should not spawn scrcpy.exe")),
    )
    out = start_scrcpy_mirror("emulator-5554")
    assert out["scrcpy_started"] is False
    assert out["session_id"]


def test_ws_control_tap_invokes_inject(monkeypatch):
    device = MagicMock()
    device.running = True
    device.inject_tap.return_value = True
    sess = MagicMock()
    sess.device = device
    monkeypatch.setattr(
        "mobile_scrcpy_bridge._active_sessions",
        {"emulator-5554": sess},
    )
    _handle_ws_control_message(
        "emulator-5554",
        json.dumps({"type": "tap", "x": 10, "y": 20, "screen_width": 1080, "screen_height": 2400}),
    )
    device.inject_tap.assert_called_once_with(10, 20, screen_width=1080, screen_height=2400)


def test_iter_scrcpy_http_stream_yields_length_prefix(monkeypatch):
    from mobile_scrcpy_bridge import iter_scrcpy_http_stream

    class FakeSession:
        def __init__(self, serial):
            self.serial = serial
            self.running = True
            self._n = 0

        def start(self):
            return None

        def read_packet(self):
            self._n += 1
            if self._n > 2:
                self.running = False
                return None
            return b"\x00\x00\x01" + bytes([self._n])

        def stop(self):
            self.running = False

    monkeypatch.setattr("mobile_scrcpy_bridge._find_scrcpy_server_jar", lambda: "/tmp/jar")
    monkeypatch.setattr("mobile_scrcpy_bridge.ScrcpyDeviceSession", FakeSession)
    chunks = list(iter_scrcpy_http_stream("emulator-5554"))
    assert len(chunks) == 2
    assert chunks[0][:4] == b"\x00\x00\x00\x04"
    assert chunks[0][4:] == b"\x00\x00\x01\x01"


def test_stable_serial_port_is_deterministic():
    from mobile_scrcpy_bridge import _stable_serial_port

    a = _stable_serial_port("emulator-5554")
    b = _stable_serial_port("emulator-5554")
    c = _stable_serial_port("emulator-5556")
    assert a == b
    assert a != c
    assert 27183 <= a < 27683


def test_inject_touch_packet_size():
    from mobile_scrcpy_bridge import (
        _AMOTION_EVENT_ACTION_DOWN,
        _POINTER_ID_GENERIC,
        _SC_CONTROL_MSG_INJECT_TOUCH_EVENT,
    )

    msg = struct.pack(
        ">BBQIIHHHII",
        _SC_CONTROL_MSG_INJECT_TOUCH_EVENT,
        _AMOTION_EVENT_ACTION_DOWN,
        _POINTER_ID_GENERIC,
        100 << 16,
        200 << 16,
        1080,
        2400,
        0xFFFF,
        1,
        1,
    )
    assert len(msg) == 32
