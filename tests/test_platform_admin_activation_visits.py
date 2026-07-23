# -*- coding: utf-8 -*-
"""平台控制面：License 激活计数与访问统计。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ADMIN_DIR = Path(__file__).resolve().parents[1] / "projects" / "testory-platform-admin"
sys.path.insert(0, str(ADMIN_DIR))


@pytest.fixture()
def admin_db(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "platform_admin.db")
        monkeypatch.setenv("PLATFORM_ADMIN_DB", db_path)
        import importlib
        import admin_database as ad

        importlib.reload(ad)
        db = ad.PlatformAdminDB()
        yield db


def test_list_licenses_includes_activation_count(admin_db):
    admin_db.insert_license(
        "lic_a",
        "KEYA",
        "professional",
        "Alice",
        "",
        "",
        "2099-01-01",
    )
    admin_db.insert_license(
        "lic_b",
        "KEYB",
        "professional",
        "Bob",
        "",
        "",
        "2099-01-01",
    )
    admin_db.record_activation("lic_b", "machine", "mach_bob")
    rows = {r["license_id"]: r for r in admin_db.list_licenses()}
    assert int(rows["lic_a"]["activation_count"] or 0) == 0
    assert int(rows["lic_b"]["activation_count"] or 0) == 1
    assert rows["lic_b"]["binding_id"] == "mach_bob"
    detail = admin_db.get_license("lic_b")
    assert int(detail["activation_count"] or 0) == 1
    assert detail["last_activated_at"]


def test_visit_stats(admin_db):
    admin_db.record_site_visit(visitor_id="v1", path="/", referrer="", title="Home", ip="1.1.1.1")
    admin_db.record_site_visit(visitor_id="v1", path="/docs", referrer="", title="Docs", ip="1.1.1.1")
    admin_db.record_site_visit(visitor_id="v2", path="/", referrer="", title="Home", ip="2.2.2.2")
    stats = admin_db.visit_stats(30)
    assert stats["pv"] == 3
    assert stats["uv"] == 2
    assert stats["today_pv"] == 3
    assert any(p["path"] == "/" and p["hits"] == 2 for p in stats["top_paths"])
