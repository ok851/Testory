"""feature_required：API JSON 403 vs 页面 HTML 重定向判定。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_wants_license_gate_html_redirect_navigate():
    from app import _wants_license_gate_html_redirect

    req = MagicMock()
    req.path = "/audit-logs"
    req.method = "GET"
    req.headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Sec-Fetch-Mode": "navigate",
        "X-Requested-With": "",
    }
    with patch("app.request", req):
        assert _wants_license_gate_html_redirect() is True


def test_wants_license_gate_html_redirect_api_false():
    from app import _wants_license_gate_html_redirect

    req = MagicMock()
    req.path = "/api/audit-logs"
    req.method = "GET"
    req.headers = {"Accept": "application/json", "Sec-Fetch-Mode": "cors"}
    with patch("app.request", req):
        assert _wants_license_gate_html_redirect() is False


def test_wants_license_gate_html_redirect_fetch_cors_false():
    from app import _wants_license_gate_html_redirect

    req = MagicMock()
    req.path = "/sso-settings"
    req.method = "GET"
    req.headers = {
        "Accept": "application/json",
        "Sec-Fetch-Mode": "cors",
    }
    with patch("app.request", req):
        assert _wants_license_gate_html_redirect() is False


def test_feature_required_payload_has_upgrade_url(monkeypatch):
    """API 403 体含 upgrade_url，供前端 TestoryLicenseGate 使用。"""
    from app import feature_required, license_manager

    monkeypatch.setattr(license_manager, "check_feature_available", lambda f: False)
    monkeypatch.setattr(
        license_manager,
        "describe_feature_gate",
        lambda f: {"feature": f, "min_tier": "enterprise", "title": "SSO", "available": False},
    )
    monkeypatch.setattr(
        license_manager,
        "get_limits",
        lambda: {"license_type": "free", "product_display_name": "免费版"},
    )

    @feature_required("sso")
    def _dummy():
        return {"ok": True}

    req = MagicMock()
    req.path = "/api/sso/configs"
    req.method = "GET"
    req.headers = {"Accept": "application/json", "Sec-Fetch-Mode": "cors"}
    with patch("app.request", req):
        with patch("app._wants_license_gate_html_redirect", return_value=False):
            with patch("app.jsonify", side_effect=lambda d: d):
                body, status = _dummy()
    assert status == 403
    assert body["error_code"] == "LICENSE_FEATURE_REQUIRED"
    assert "upgrade_url" in body
    assert "sso" in body["upgrade_url"]
