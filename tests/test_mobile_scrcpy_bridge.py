"""scrcpy bridge 端口检测：勿对 WebSocket 端口做 bare TCP 连接探测。"""
import socket
from unittest.mock import patch

from mobile_scrcpy_bridge import _tcp_port_in_use, bridge_health


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
