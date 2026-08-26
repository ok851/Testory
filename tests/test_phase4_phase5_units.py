"""测试单元、YAML 导出、VisionActionPort 单测。"""
import json

import pytest

from modules.execution.case_yaml_export import case_to_yaml_preview
from database import Database


@pytest.fixture
def db(tmp_path):
    Database._schema_initialized = False
    d = Database(str(tmp_path / "t.db"))
    pid = d.create_project("P1", "desc")
    return d, pid


def test_test_unit_crud(db):
    d, pid = db
    uid = d.create_test_unit(pid, "登录模块", description="登录相关")
    units = d.get_test_units(pid)
    assert len(units) == 1
    assert units[0]["name"] == "登录模块"
    cid = d.create_test_case_v2(pid, "用例A", unit_id=uid)
    cases = d.get_project_cases(pid, unit_id=uid)
    assert len(cases) == 1
    assert cases[0]["unit_id"] == uid
    assert cases[0]["unit_name"] == "登录模块"
    assert d.update_test_unit(uid, name="登录与鉴权", description="更新描述")
    updated = d.get_test_unit(uid)
    assert updated["name"] == "登录与鉴权"
    assert updated["description"] == "更新描述"
    ungrouped = d.get_project_cases(pid, unit_id="ungrouped")
    assert all(c["id"] != cid for c in ungrouped)
    assert d.delete_test_unit(uid)
    case = d.get_test_case_v2(cid)
    assert case.get("unit_id") is None


def test_ensure_default_test_unit(db):
    d, pid = db
    uid = d.ensure_default_test_unit(pid)
    assert uid > 0
    assert d.get_test_units(pid)


def test_case_yaml_preview():
    yaml = case_to_yaml_preview(
        {"name": "登录测试", "project_id": 1, "url": "https://x.com"},
        [{"action": "click", "description": "点击登录", "selector_value": "#login"}],
        unit_name="登录模块",
    )
    assert "登录测试" in yaml
    assert "登录模块" in yaml
    assert "点击登录" in yaml


def test_project_yaml_preview(db):
    d, pid = db
    uid = d.create_test_unit(pid, "订单")
    d.create_test_case_v2(pid, "下单", unit_id=uid)
    from modules.execution.case_yaml_export import build_project_yaml_preview

    yaml = build_project_yaml_preview(d, pid, unit_id=uid)
    assert yaml and "下单" in yaml and "订单" in yaml


def test_project_cases_pagination(db):
    d, pid = db
    for i in range(12):
        d.create_test_case_v2(pid, f"用例{i:02d}")
    page1, total = d.get_project_cases_paginated(pid, page=1, page_size=10)
    assert total == 12
    assert len(page1) == 10
    page2, total2 = d.get_project_cases_paginated(pid, page=2, page_size=10)
    assert total2 == 12
    assert len(page2) == 2
    uid = d.create_test_unit(pid, "模块A")
    d.create_test_case_v2(pid, "模块用例", unit_id=uid)
    u_cases, u_total = d.get_project_cases_paginated(pid, unit_id=uid, page=1, page_size=10)
    assert u_total == 1
    assert len(u_cases) == 1


def test_resolve_unit_id_on_import(db):
    d, pid = db
    from modules.execution.case_importer import CaseImportExporter

    exp = CaseImportExporter(d)
    uid = exp._resolve_unit_id(pid, "支付模块")
    assert uid > 0
    uid2 = exp._resolve_unit_id(pid, "支付模块")
    assert uid2 == uid


def test_mcp_kit_for_web_port():
    from testory_mcp.kit import mcp_kit_for_port
    from modules.ai.vision_action_port import WebVisionActionPort

    class _P(WebVisionActionPort):
        def capture(self):
            from modules.ai.vision_action_port import CaptureFrame

            return CaptureFrame(b"x", 100, 100)

        def ground(self, description, frame=None):
            return None

        def tap(self, description):
            from modules.ai.vision_action_port import ActResult

            return ActResult(ok=True)

        def input_text(self, description, text):
            from modules.ai.vision_action_port import ActResult

            return ActResult(ok=True)

        def run_steps(self, steps):
            return []

    desc, tools = mcp_kit_for_port(_P("sid"))
    assert "web" in desc
    names = [t["name"] for t in tools]
    assert "web_tap" in names
    assert "web_assert" in names
