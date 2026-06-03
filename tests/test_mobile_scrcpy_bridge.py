"""scrcpy bridge 端口检测：勿对 WebSocket 端口做裸 TCP 连接探测。"""
import socket

from mobile_scrcpy_bridge import _tcp_port_in_use


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
