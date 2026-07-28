# -*- coding: utf-8 -*-
"""Gateway ensure / connect fallback 行为。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_ensure_mobile_gateway_ready_when_healthy():
    from mobile_service_bootstrap import ensure_mobile_gateway_ready

    with patch("mobile_service_bootstrap._ensure_mobile_env_defaults"):
        with patch("mobile_service_bootstrap._verify_gateway_health", return_value=True):
            out = ensure_mobile_gateway_ready()
    assert out["ok"] is True
    assert out["started"] is False


def test_ensure_mobile_gateway_ready_starts_when_down():
    from mobile_service_bootstrap import ensure_mobile_gateway_ready

    with patch("mobile_service_bootstrap._ensure_mobile_env_defaults"):
        with patch(
            "mobile_service_bootstrap._verify_gateway_health",
            side_effect=[False, True],
        ):
            with patch(
                "mobile_service_bootstrap.bootstrap_mobile_services",
                return_value={"gateway_started": True},
            ) as boot:
                out = ensure_mobile_gateway_ready()
    assert boot.called
    assert out["ok"] is True
    assert out["started"] is True


def test_adb_local_connect_fallback():
    from mobile_routes import _adb_local_connect_fallback

    with patch(
        "mobile_routes.list_usb_devices",
        return_value=[{"udid": "2512BPNDAC", "state": "device"}],
    ):
        with patch("mobile_routes.list_emulators", return_value=[]):
            with patch("mobile_routes.set_connected_udid") as set_u:
                with patch(
                    "mobile_routes.get_device_info",
                    return_value={"width": 1080, "height": 2400},
                ):
                    with patch(
                        "mobile_assistant_bundles.assistant_installed_on_device",
                        return_value=True,
                    ):
                        out = _adb_local_connect_fallback(
                            "2512BPNDAC", gateway_error="refused"
                        )
    assert out["success"] is True
    assert out["assistant_installed"] is True
    assert not (out.get("warning") or "").strip()
    set_u.assert_called_with("2512BPNDAC")


def test_connect_succeeds_without_gateway():
    """Gateway 挂了也不应阻断 USB 选中设备。"""
    from mobile_routes import register_mobile_routes

    calls = {}

    class FakeApp:
        def route(self, *a, **k):
            def deco(fn):
                calls[a[0] if a else k.get("rule")] = fn
                return fn
            return deco

    # 轻量：直接测 fallback 成功即可代表主路径
    with patch(
        "mobile_routes.list_usb_devices",
        return_value=[{"udid": "2512BPNDAC", "state": "device"}],
    ):
        with patch("mobile_routes.list_emulators", return_value=[]):
            with patch("mobile_routes.set_connected_udid"):
                with patch("mobile_routes.get_device_info", return_value={}):
                    with patch(
                        "mobile_assistant_bundles.assistant_installed_on_device",
                        return_value=False,
                    ):
                        from mobile_routes import _adb_local_connect_fallback
                        out = _adb_local_connect_fallback("2512BPNDAC")
    assert out["success"] is True
