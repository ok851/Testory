# -*- coding: utf-8 -*-
"""跨端场景持久化：字符串 scenario_id、字段归一、更新/删除。"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_modules.execute import orchestrator as orch


@pytest.fixture
def scenario_store(tmp_path, monkeypatch):
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    # 清掉可能的缓存路径副作用
    yield tmp_path
    if (tmp_path / "cross_platform_scenarios.json").exists():
        (tmp_path / "cross_platform_scenarios.json").unlink()


def test_store_path_uses_uat_data_dir(scenario_store):
    p = orch._store_path()
    assert str(scenario_store) in str(p)
    assert p.name == "cross_platform_scenarios.json"


def test_default_store_path_is_under_repo_data(monkeypatch, tmp_path):
    """Y4：未设置 UAT_DATA_DIR 时必须落在仓库 data/，不得 parents[3] 越界。"""
    monkeypatch.delenv("UAT_DATA_DIR", raising=False)
    info = orch.scenario_store_info()
    p = Path(info["path"])
    repo = orch._repo_root()
    assert p.parent == (repo / "data").resolve()
    assert info["using_uat_data_dir"] is False
    assert "备份" in info["hint"]


def test_save_list_get_delete_roundtrip(scenario_store):
    saved = orch.save_cross_platform_scenario(
        {
            "name": "登录联动",
            "project_id": 7,
            "plan": {"scenario": "登录联动", "stages": [{"id": "a1", "layer": "api"}]},
        }
    )
    assert saved["success"] is True
    sid = saved["scenario_id"]
    assert sid
    assert saved["scenario"]["id"] == sid
    assert saved["scenario"]["name"] == "登录联动"
    assert saved["scenario"]["stage_count"] == 1

    listed = orch.list_cross_platform_scenarios()
    assert len(listed) == 1
    assert listed[0]["scenario_id"] == sid
    assert listed[0]["id"] == sid

    got = orch.get_cross_platform_scenario(sid)
    assert got is not None
    assert got["plan"]["stages"][0]["id"] == "a1"

    # 更新覆盖
    saved2 = orch.save_cross_platform_scenario(
        {
            "scenario_id": sid,
            "name": "登录联动-v2",
            "project_id": 7,
            "plan": {"stages": [{"id": "a1"}, {"id": "w1"}]},
        }
    )
    assert saved2["success"] is True
    assert saved2["scenario"]["name"] == "登录联动-v2"
    assert saved2["scenario"]["stage_count"] == 2
    assert orch.get_cross_platform_scenario(sid)["created_at"]

    deleted = orch.delete_cross_platform_scenario(sid)
    assert deleted["success"] is True
    assert orch.get_cross_platform_scenario(sid) is None


def test_accept_id_alias_and_legacy_plan_json(scenario_store):
    raw = {
        "id": "legacyabc123",
        "project_id": 1,
        "plan_json": json.dumps({"name": "旧格式", "stages": []}),
    }
    path = orch._store_path()
    path.write_text(json.dumps([raw]), encoding="utf-8")
    got = orch.get_cross_platform_scenario("legacyabc123")
    assert got is not None
    assert got["scenario_id"] == "legacyabc123"
    assert got["name"] == "旧格式"
    assert isinstance(got["plan"], dict)


def test_delete_missing_fails(scenario_store):
    out = orch.delete_cross_platform_scenario("no-such-id")
    assert out["success"] is False


def test_save_requires_plan_or_web_case(scenario_store):
    out = orch.save_cross_platform_scenario({"name": "x", "project_id": 1})
    assert out["success"] is False


def test_coerce_numeric_string_id(scenario_store):
    """历史若误用数字字符串，string 路由仍可取到。"""
    orch.save_cross_platform_scenario(
        {
            "scenario_id": "42",
            "name": "数字ID",
            "project_id": 1,
            "plan": {"stages": []},
        }
    )
    got = orch.get_cross_platform_scenario("42")
    assert got is not None
    assert got["id"] == "42"


def test_uuid_like_id_not_int_only(scenario_store):
    sid = "a1b2c3d4e5f6"
    orch.save_cross_platform_scenario(
        {"scenario_id": sid, "name": "UUID", "project_id": 1, "plan": {"stages": [{"id": "s1"}]}}
    )
    assert orch.get_cross_platform_scenario(sid)["scenario_id"] == sid
