"""scrcpy bridge 端口检测：勿对 WebSocket 端口做 bare TCP 连接探测。"""
import socket
from unittest.mock import patch

from mobile_scrcpy_bridge import (
    _abstract_socket_name,
    _stable_serial_scid,
    _tcp_port_in_use,
    bridge_health,
    read_forward_handshake,
)


class _FakeSock:
    """Minimal socket mock for handshake tests."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def recv(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_read_forward_handshake_with_dummy_byte():
    device_name = b"MyDevice" + b"\x00" * (64 - len(b"MyDevice"))
    sock = _FakeSock(b"\x00" + device_name)
    assert read_forward_handshake(sock) == device_name


def test_read_forward_handshake_legacy_no_dummy():
    device_name = b"LegacyDev" + b"\x00" * (64 - len(b"LegacyDev"))
    sock = _FakeSock(device_name)
    assert read_forward_handshake(sock) == device_name


def test_stable_serial_scid_deterministic():
    a = _stable_serial_scid("device-1")
    b = _stable_serial_scid("device-1")
    assert a == b
    assert len(a) == 8
    assert all(c in "0123456789abcdef" for c in a)


def test_abstract_socket_name_v2():
    scid = _stable_serial_scid("serial-a")
    assert _abstract_socket_name("2.4", scid) == "localabstract:scrcpy"


def test_abstract_socket_name_v3():
    scid = _stable_serial_scid("serial-b")
    assert _abstract_socket_name("3.1", scid) == f"localabstract:scrcpy_{scid}"


def test_tcp_port_in_use_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert _tcp_port_in_use("127.0.0.1", port) is False


def test_tcp_port_in_use_bound_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(1)
    try:
        assert _tcp_port_in_use("127.0.0.1", port) is True
    finally:
        sock.close()


def test_bridge_health_ok_when_server_installed():
    with patch("mobile_scrcpy_bridge._find_scrcpy_server_jar", return_value="/tmp/scrcpy-server"):
        with patch("mobile_scrcpy_bridge._service_listening", return_value=False):
            health = bridge_health()
    assert health["ok"] is True
    assert health["scrcpy_server_ready"] is True
    assert "就绪" in health["message"]


def test_bridge_health_not_ok_without_server():
    with patch("mobile_scrcpy_bridge._find_scrcpy_server_jar", return_value=None):
        with patch("mobile_scrcpy_bridge._service_listening", return_value=False):
            health = bridge_health()
    assert health["ok"] is False
    assert "未就绪" in health["message"]
