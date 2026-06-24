# -*- coding: utf-8 -*-
"""投屏后端配置与 scrcpy 触控消息。"""

import json
import struct
from unittest.mock import MagicMock, patch

from mobile_env_config import (
    device_scrcpy_ws_enabled,
    resolve_mirror_backend,
    scrcpy_available,
    scrcpy_mirror_fps,
)
from mobile_scrcpy_bridge import _handle_ws_control_message, _version_candidates
from mobile_mirror import start_scrcpy_mirror


def test_resolve_mirror_backend_default_scrcpy_ws_when_available(monkeypatch):
    monkeypatch.delenv("MOBILE_MIRROR_BACKEND", raising=False)
    monkeypatch.setenv("MOBILE_DEVICE_SCRCPY", "1")
    monkeypatch.setattr("mobile_env_config.scrcpy_available", lambda: True)
    assert resolve_mirror_backend("192.168.1.5:5555") == "scrcpy_ws"


def test_resolve_mirror_backend_opt_out_screencap(monkeypatch):
    monkeypatch.delenv("MOBILE_MIRROR_BACKEND", raising=False)
    monkeypatch.setenv("MOBILE_DEVICE_SCRCPY", "0")
    monkeypatch.setattr("mobile_env_config.scrcpy_available", lambda: True)
    assert resolve_mirror_backend("device-serial") == "screencap"


def test_resolve_mirror_backend_no_scrcpy_screencap(monkeypatch):
    monkeypatch.delenv("MOBILE_MIRROR_BACKEND", raising=False)
    monkeypatch.setenv("MOBILE_DEVICE_SCRCPY", "1")
    monkeypatch.setattr("mobile_env_config.scrcpy_available", lambda: False)
    assert resolve_mirror_backend("device-serial") == "screencap"


def test_scrcpy_available_with_server_jar_only(monkeypatch, tmp_path):
    monkeypatch.delenv("SCRCPY_PATH", raising=False)
    jar = tmp_path / "scrcpy-server"
    jar.write_bytes(b"x" * 2000)
    monkeypatch.setattr("mobile_scrcpy_bridge.find_scrcpy_server_jar", lambda: str(jar))
    monkeypatch.setattr(
        "mobile_scrcpy_bundles.get_installed_scrcpy_exe",
        lambda: None,
    )
    monkeypatch.setattr("mobile_env_config.scrcpy_path", lambda: "scrcpy")
    assert scrcpy_available() is True


def test_version_candidates_prefers_jar_major(monkeypatch):
    monkeypatch.setattr(
        "mobile_scrcpy_bridge._scrcpy_server_version",
        lambda: "2.4",
    )
    out = _version_candidates()
    assert out[0] == "2.4"
    assert "3.1" not in out


def test_scrcpy_mirror_diagnostics_without_device(monkeypatch):
    from mobile_scrcpy_bridge import scrcpy_mirror_diagnostics

    monkeypatch.setattr("mobile_env_config.scrcpy_available", lambda: True)
    monkeypatch.setattr("mobile_env_config.resolve_mirror_backend", lambda u: "scrcpy_ws")
    monkeypatch.setattr("mobile_scrcpy_bridge._find_scrcpy_server_jar", lambda: "/tmp/jar")
    diag = scrcpy_mirror_diagnostics("")
    assert diag["mirror_backend_selected"] == "scrcpy_ws"
    assert diag["scrcpy_server_jar"] == "/tmp/jar"
    assert "version_candidates" in diag


def test_scrcpy_mirror_fps_default(monkeypatch):
    monkeypatch.delenv("MOBILE_SCRCPY_FPS", raising=False)
    monkeypatch.delenv("MOBILE_MIRROR_FPS", raising=False)
    assert scrcpy_mirror_fps() == 24


def test_device_scrcpy_ws_enabled_default(monkeypatch):
    monkeypatch.delenv("MOBILE_DEVICE_SCRCPY", raising=False)
    monkeypatch.delenv("MOBILE_EMULATOR_SCRCPY", raising=False)
    assert device_scrcpy_ws_enabled() is True


def test_start_scrcpy_mirror_no_external_window(monkeypatch):
    monkeypatch.setattr(
        "mobile_mirror.subprocess.Popen",
        MagicMock(side_effect=AssertionError("should not spawn scrcpy.exe")),
    )
    out = start_scrcpy_mirror("ABCD1234")
    assert out["scrcpy_started"] is False
    assert out["session_id"]


def test_ws_control_tap_invokes_inject(monkeypatch):
    device = MagicMock()
    device.running = True
    device.inject_tap.return_value = True
    monkeypatch.setattr(
        "mobile_scrcpy_bridge._get_persistent_device",
        lambda serial: device if serial == "device-1" else None,
    )
    _handle_ws_control_message(
        "device-1",
        json.dumps({"type": "tap", "x": 10, "y": 20, "screen_width": 1080, "screen_height": 2400}),
    )
    device.inject_tap.assert_called_once_with(10, 20, screen_width=1080, screen_height=2400)


def test_iter_scrcpy_http_stream_yields_length_prefix(monkeypatch):
    from mobile_scrcpy_bridge import ScrcpyPacket, iter_scrcpy_http_stream

    class FakeSession:
        def __init__(self, serial):
            self.serial = serial
            self.running = True
            self._n = 0

        def start(self):
            return None

        def read_packet(self):
            self._n += 1
            if self._n > 5:
                self.running = False
                return None
            payload = b"\x00\x00\x01" + bytes([self._n])
            return ScrcpyPacket(payload, is_config=(self._n == 1), is_key=(self._n == 2))

        def stop(self):
            self.running = False

    monkeypatch.setattr("mobile_scrcpy_bridge._find_scrcpy_server_jar", lambda: "/tmp/jar")
    monkeypatch.setattr("mobile_scrcpy_bridge.ScrcpyDeviceSession", FakeSession)
    monkeypatch.setattr("mobile_scrcpy_bridge._relays", {})
    monkeypatch.setattr("mobile_scrcpy_bridge._persistent_sessions", {})
    chunks = list(iter_scrcpy_http_stream("device-1"))
    assert len(chunks) >= 1
    assert chunks[0][0] in (0, 1, 2)
    plen = (chunks[0][1] << 24) | (chunks[0][2] << 16) | (chunks[0][3] << 8) | chunks[0][4]
    assert plen >= 4
    assert chunks[0][5:8] == b"\x00\x00\x01"


def test_stable_serial_port_is_deterministic():
    from mobile_scrcpy_bridge import _stable_serial_port

    a = _stable_serial_port("device-1")
    b = _stable_serial_port("device-1")
    c = _stable_serial_port("device-2")
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


def test_mirror_payload_downgrades_when_warm_fails(monkeypatch):
    from mobile_routes import _mirror_payload

    monkeypatch.setattr("mobile_env_config.scrcpy_available", lambda: True)
    monkeypatch.setattr("mobile_env_config.resolve_mirror_backend", lambda udid: "scrcpy_ws")
    monkeypatch.setattr("mobile_scrcpy_bridge.ensure_bridge_started", lambda: (True, ""))
    monkeypatch.setattr(
        "mobile_scrcpy_bridge.bridge_health",
        lambda: {"scrcpy_server_ready": True, "ok": True},
    )
    monkeypatch.setattr(
        "mobile_scrcpy_bridge.warm_scrcpy_session",
        lambda serial, timeout=12.0: (False, "scrcpy 长时间无视频帧"),
    )
    payload = _mirror_payload("REAL001", "sess-1")
    assert payload["mirror_backend"] == "screencap"
    assert "无视频帧" in payload.get("mirror_fallback_reason", "")


def test_mirror_payload_keeps_scrcpy_when_warm_ok(monkeypatch):
    from mobile_routes import _mirror_payload

    monkeypatch.setattr("mobile_env_config.scrcpy_available", lambda: True)
    monkeypatch.setattr("mobile_env_config.resolve_mirror_backend", lambda udid: "scrcpy_ws")
    monkeypatch.setattr("mobile_env_config.scrcpy_bridge_url", lambda host="": "ws://127.0.0.1:8767")
    monkeypatch.setattr("mobile_scrcpy_bridge.ensure_bridge_started", lambda: (True, ""))
    monkeypatch.setattr(
        "mobile_scrcpy_bridge.bridge_health",
        lambda: {"scrcpy_server_ready": True, "ok": True},
    )
    monkeypatch.setattr(
        "mobile_scrcpy_bridge.warm_scrcpy_session",
        lambda serial, timeout=12.0: (True, "ok"),
    )
    payload = _mirror_payload("REAL001", "sess-1")
    assert payload["mirror_backend"] == "scrcpy_ws"
    assert payload.get("scrcpy_warmed") is True
    assert "mirror_stream_url" in payload
