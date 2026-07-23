# -*- coding: utf-8 -*-
"""端到端冒烟：访问上报 + License 激活通报是否可走通。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ADMIN_DIR = ROOT / "projects" / "testory-platform-admin"
WEBSITE_DIR = ROOT / "projects" / "testory-website"
PACKAGES = ROOT / "packages"


def _ensure_path(*paths: Path) -> None:
    for p in paths:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def test_visit_and_activation_via_flask_clients(monkeypatch, tmp_path):
    db_path = tmp_path / "smoke_admin.db"
    monkeypatch.setenv("PLATFORM_ADMIN_DB", str(db_path))
    monkeypatch.setenv("PLATFORM_ADMIN_SECRET", "test-secret")
    monkeypatch.setenv("PLATFORM_ADMIN_USER", "founder")
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "test-pass")
    monkeypatch.setenv("PLATFORM_ADMIN_URL", "http://127.0.0.1:5100")
    monkeypatch.setenv("WEBSITE_URL", "http://127.0.0.1:5200")
    monkeypatch.delenv("PLATFORM_ADMIN_PATH_PREFIX", raising=False)

    # --- admin app ---
    _ensure_path(ADMIN_DIR, PACKAGES)
    import importlib

    import admin_database as ad

    importlib.reload(ad)

    # Import admin app fresh-ish
    if "app" in sys.modules and getattr(sys.modules["app"], "__file__", "").replace("\\", "/").endswith(
        "testory-platform-admin/app.py"
    ):
        admin_mod = importlib.reload(sys.modules["app"])
    else:
        # Avoid colliding with repo-root app.py
        sys.modules.pop("app", None)
        sys.path.insert(0, str(ADMIN_DIR))
        import app as admin_mod  # type: ignore

        # Force db to new path
        admin_mod._db = ad.PlatformAdminDB()

    admin_client = admin_mod.app.test_client()

    # 1) public visit API
    r = admin_client.post(
        "/api/public/visit",
        json={
            "visitor_id": "v_smoke_1",
            "path": "/docs",
            "referrer": "https://example.com",
            "title": "Docs",
            "ip": "203.0.113.9",
        },
    )
    assert r.status_code == 200, r.data
    assert r.get_json().get("success") is True

    stats = admin_mod._db.visit_stats(30)
    assert stats["pv"] >= 1
    assert stats["uv"] >= 1

    # 2) license activate API
    admin_mod._db.insert_license(
        "lic_smoke",
        "KEY_SMOKE",
        "professional",
        "Smoke",
        "",
        "",
        "2099-12-31",
    )
    r2 = admin_client.post(
        "/api/licenses/activate",
        json={
            "license_id": "lic_smoke",
            "binding_type": "machine",
            "binding_id": "mach_smoke_xyz",
        },
    )
    assert r2.status_code == 200, r2.data
    assert r2.get_json().get("success") is True
    lic = admin_mod._db.get_license("lic_smoke")
    assert int(lic.get("activation_count") or 0) == 1
    assert lic.get("binding_id") == "mach_smoke_xyz"
    acts = admin_mod._db.list_license_activations("lic_smoke")
    assert len(acts) == 1

    # 3) website /api/visit proxies to admin via platform_api_json
    # Simulate by patching platform_api_json to hit admin_client
    _ensure_path(WEBSITE_DIR, PACKAGES)

    def _proxy_to_admin(path, method="GET", body=None, **kwargs):
        if method.upper() == "POST":
            resp = admin_client.post(path, json=body or {})
        else:
            resp = admin_client.get(path)
        data = resp.get_json() or {}
        if resp.status_code >= 400:
            data.setdefault("success", False)
        return data

    # Load website app without colliding
    sys.modules.pop("app", None)
    # Keep admin module as admin_app alias if needed
    with mock.patch("testory_common.platform_client.platform_api_json", side_effect=_proxy_to_admin):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "website_app_smoke", WEBSITE_DIR / "app.py"
        )
        website_mod = importlib.util.module_from_spec(spec)
        # Inject patched name used by website
        sys.modules["website_app_smoke"] = website_mod
        # website imports platform_api_json at module level — patch after load via attribute
        assert spec.loader is not None
        spec.loader.exec_module(website_mod)
        website_mod.platform_api_json = _proxy_to_admin
        web_client = website_mod.app.test_client()
        r3 = web_client.post(
            "/api/visit",
            json={
                "visitor_id": "v_smoke_web",
                "path": "/",
                "referrer": "",
                "title": "Home",
            },
        )
        assert r3.status_code == 200, r3.data
        assert r3.get_json().get("success") is True

    stats2 = admin_mod._db.visit_stats(30)
    assert stats2["pv"] >= 2
    assert stats2["uv"] >= 2

    # 4) report_license_activation HTTP path candidates against admin_client via urlopen mock
    sys.path.insert(0, str(ROOT))
    import platform_sync

    importlib.reload(platform_sync)

    calls = []

    class _Resp:
        def __init__(self, status=200):
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"success":true}'

    def fake_urlopen(req, timeout=10):
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
        calls.append(url)
        # Simulate nginx: bare host without /admin returns 404 for activate; with /admin works
        # Our candidates try both — first may fail depending on PLATFORM_ADMIN_URL
        body = req.data
        payload = json.loads(body.decode("utf-8")) if body else {}
        # Always accept when path ends with /api/licenses/activate
        if url.rstrip("/").endswith("/api/licenses/activate"):
            # Actually write via admin client
            admin_client.post("/api/licenses/activate", json=payload)
            return _Resp(200)
        raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=None)

    monkeypatch.setenv("PLATFORM_ADMIN_URL", "http://example.test")
    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = platform_sync.report_license_activation(
            "lic_smoke", "machine", "mach_smoke_2"
        )
    assert ok is True
    assert any("/api/licenses/activate" in u for u in calls)
    acts2 = admin_mod._db.list_license_activations("lic_smoke")
    assert len(acts2) >= 2
