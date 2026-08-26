# -*- coding: utf-8 -*-
"""本地冒烟脚本：启动控制面 test client，验证访问量与激活通报 API。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_DIR = ROOT / "projects" / "testory-platform-admin"
PACKAGES = ROOT / "packages"
sys.path[:0] = [str(ADMIN_DIR), str(PACKAGES), str(ROOT)]

fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["PLATFORM_ADMIN_DB"] = db_path
os.environ["PLATFORM_ADMIN_SECRET"] = "smoke-secret"
os.environ["PLATFORM_ADMIN_URL"] = "http://127.0.0.1:5100"
os.environ.pop("PLATFORM_ADMIN_PATH_PREFIX", None)

import admin_database as ad
from app import app, _db  # noqa: E402  admin app

# ensure db bound to temp
import app as admin_app

admin_app._db = ad.PlatformAdminDB()
client = app.test_client()

print("=== 1. Visit API ===")
r = client.post(
    "/api/public/visit",
    json={
        "visitor_id": "v_live_check",
        "path": "/pricing",
        "referrer": "",
        "title": "Pricing",
        "ip": "198.51.100.1",
    },
)
print("POST /api/public/visit ->", r.status_code, r.get_json())
stats = admin_app._db.visit_stats(30)
print("visit_stats:", {k: stats[k] for k in ("pv", "uv", "today_pv", "today_uv")})
assert r.status_code == 200 and r.get_json().get("success")
assert stats["pv"] >= 1

print("=== 2. License activate API ===")
admin_app._db.insert_license(
    "lic_live_check",
    "KEY",
    "professional",
    "Check",
    "",
    "",
    "2099-01-01",
)
r2 = client.post(
    "/api/licenses/activate",
    json={
        "license_id": "lic_live_check",
        "binding_type": "machine",
        "binding_id": "mach_live_check",
    },
)
print("POST /api/licenses/activate ->", r2.status_code, r2.get_json())
lic = admin_app._db.get_license("lic_live_check")
print(
    "license:",
    {
        "activation_count": lic.get("activation_count"),
        "binding_id": lic.get("binding_id"),
        "last_activated_at": lic.get("last_activated_at"),
    },
)
assert r2.status_code == 200 and r2.get_json().get("success")
assert int(lic["activation_count"]) == 1

print("=== 3. report_license_activation (urllib -> test handler) ===")
from modules.core import platform_sync
from unittest import mock
import urllib.request


class Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"success":true}'


def fake_urlopen(req, timeout=10):
    url = req.get_full_url()
    body = json.loads(req.data.decode("utf-8"))
    print("  urlopen:", url, body)
    rr = client.post("/api/licenses/activate", json=body)
    if rr.status_code != 200:
        raise RuntimeError(rr.data)
    return Resp()


with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
    os.environ["PLATFORM_ADMIN_URL"] = "http://127.0.0.1:5100"
    ok = platform_sync.report_license_activation(
        "lic_live_check", "machine", "mach_live_check_2"
    )
print("report_license_activation ->", ok)
assert ok is True
acts = admin_app._db.list_license_activations("lic_live_check")
print("activations:", len(acts), [a["binding_id"] for a in acts])
assert len(acts) == 2

print("=== 4. Website visit proxy shape ===")
from testory_common.platform_client import platform_api_json
from unittest import mock as m2


def proxy(path, method="GET", body=None, **kw):
    if method.upper() == "POST":
        resp = client.post(path, json=body or {})
    else:
        resp = client.get(path)
    return resp.get_json() or {"success": False}


# simulate website forwarding
body = {
    "visitor_id": "v_from_website",
    "path": "/",
    "referrer": "",
    "title": "Home",
    "ip": "203.0.113.10",
    "user_agent": "smoke",
}
result = proxy("/api/public/visit", method="POST", body=body)
print("website->admin visit ->", result)
assert result.get("success") is True
stats2 = admin_app._db.visit_stats(30)
print("final visit_stats:", {k: stats2[k] for k in ("pv", "uv")})
assert stats2["uv"] >= 2

print("\nALL OK: 访问量上报与 License 激活通报链路可走通")
try:
    os.remove(db_path)
except OSError:
    pass
