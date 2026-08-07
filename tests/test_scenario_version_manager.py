# -*- coding: utf-8 -*-
"""ScenarioVersionManager 单元测试：版本保存/历史/回滚/diff/导入导出。"""
from __future__ import annotations

import json
import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _tmp_versions_dir(tmp_path, monkeypatch):
    """将版本存储目录重定向到临时目录。"""
    d = tmp_path / "scenario_versions"
    d.mkdir()
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    return d


@pytest.fixture
def mgr():
    from ai_modules.execute.scenario_version_manager import ScenarioVersionManager
    return ScenarioVersionManager()


@pytest.fixture
def sample_plan():
    return {
        "scenario": "测试场景",
        "stages": [
            {"id": "s1", "layer": "api", "label": "API 阶段"},
            {"id": "s2", "layer": "web", "label": "Web 阶段"},
        ],
        "variables": {"token": "abc"},
    }


class TestSaveVersion:
    def test_save_first_version(self, mgr, sample_plan):
        result = mgr.save_version("sc-1", sample_plan, message="初始版本")
        assert result["success"] is True
        assert result["version"] == 1

    def test_save_incremental_version(self, mgr, sample_plan):
        mgr.save_version("sc-2", sample_plan, message="v1")
        sample_plan["stages"].append({"id": "s3", "layer": "mobile"})
        result = mgr.save_version("sc-2", sample_plan, message="v2")
        assert result["version"] == 2

    def test_dedup_same_content(self, mgr, sample_plan):
        mgr.save_version("sc-3", sample_plan)
        result = mgr.save_version("sc-3", sample_plan)
        assert result["skipped"] is True
        assert result["version"] == 1

    def test_max_versions_limit(self, mgr, sample_plan):
        mgr.MAX_VERSIONS = 5
        for i in range(10):
            sample_plan["variables"]["i"] = i
            mgr.save_version("sc-4", sample_plan, message=f"v{i}")
        history = mgr.get_history("sc-4")
        assert len(history) <= 5
        # Should keep the latest versions
        assert history[-1]["version"] == 10


class TestGetHistory:
    def test_empty_history(self, mgr):
        assert mgr.get_history("nonexistent") == []

    def test_history_excludes_plan(self, mgr, sample_plan):
        mgr.save_version("sc-h1", sample_plan)
        history = mgr.get_history("sc-h1")
        assert len(history) == 1
        assert "plan" not in history[0]  # plan excluded for size


class TestGetVersion:
    def test_get_existing_version(self, mgr, sample_plan):
        mgr.save_version("sc-g1", sample_plan)
        ver = mgr.get_version("sc-g1", 1)
        assert ver is not None
        assert ver["version"] == 1
        assert ver["plan"] == sample_plan

    def test_get_nonexistent_version(self, mgr):
        assert mgr.get_version("nonexistent", 1) is None


class TestRollback:
    def test_rollback_creates_new_version(self, mgr, sample_plan):
        mgr.save_version("sc-rb1", sample_plan, message="v1")
        sample_plan["stages"].append({"id": "s3"})
        mgr.save_version("sc-rb1", sample_plan, message="v2")

        result = mgr.rollback("sc-rb1", 1)
        assert result["success"] is True
        assert result["version"] == 3  # new version with v1 content

        ver = mgr.get_version("sc-rb1", 3)
        assert len(ver["plan"]["stages"]) == 2  # v1 had 2 stages

    def test_rollback_nonexistent(self, mgr):
        result = mgr.rollback("nonexistent", 1)
        assert result["success"] is False


class TestDiff:
    def test_diff_two_versions(self, mgr, sample_plan):
        mgr.save_version("sc-d1", sample_plan, message="v1")
        sample_plan["stages"].append({"id": "s3", "layer": "mobile"})
        sample_plan["variables"]["new_var"] = "hello"
        mgr.save_version("sc-d1", sample_plan, message="v2")

        diff = mgr.diff("sc-d1", 1, 2)
        assert diff["has_changes"] is True
        assert any(c["stage_id"] == "s3" and c["change"] == "added" for c in diff["stage_changes"])
        assert "new_var" in diff["variable_changes"]

    def test_diff_same_version(self, mgr, sample_plan):
        mgr.save_version("sc-d2", sample_plan)
        diff = mgr.diff("sc-d2", 1, 1)
        assert diff["has_changes"] is False

    def test_diff_nonexistent_version(self, mgr):
        diff = mgr.diff("nonexistent", 1, 2)
        assert "error" in diff


class TestExportImport:
    def test_export_latest(self, mgr, sample_plan):
        mgr.save_version("sc-e1", sample_plan)
        result = mgr.export_version("sc-e1")
        assert result["success"] is True
        assert result["plan"] == sample_plan

    def test_export_specific_version(self, mgr, sample_plan):
        mgr.save_version("sc-e2", sample_plan, message="v1")
        sample_plan["stages"].append({"id": "s3"})
        mgr.save_version("sc-e2", sample_plan, message="v2")

        result = mgr.export_version("sc-e2", version=1)
        assert result["export_version"] == 1
        assert len(result["plan"]["stages"]) == 2

    def test_import_version(self, mgr, sample_plan):
        exported = {"plan": sample_plan, "export_version": 1}
        result = mgr.import_version("sc-i1", exported, message="导入测试")
        assert result["success"] is True
        assert result["version"] == 1

    def test_import_invalid_data(self, mgr):
        result = mgr.import_version("sc-i2", {"no_plan": True})
        assert result["success"] is False
        assert "缺少" in result["error"]


class TestListAllScenarios:
    def test_list_scenarios(self, mgr, sample_plan):
        mgr.save_version("sc-l1", sample_plan)
        mgr.save_version("sc-l2", sample_plan, message="other")
        all_sc = mgr.list_all_scenarios()
        ids = [s["scenario_id"] for s in all_sc]
        assert "sc-l1" in ids
        assert "sc-l2" in ids
