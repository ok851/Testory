# -*- coding: utf-8 -*-
"""ScenarioTemplates 单元测试：模板列表、获取、参数实例化、自定义模板。"""
from __future__ import annotations

import json
import os

import pytest


@pytest.fixture(autouse=True)
def _tmp_templates_dir(tmp_path, monkeypatch):
    """将自定义模板目录重定向到临时目录。"""
    d = tmp_path / "scenario_templates"
    d.mkdir()
    monkeypatch.setenv("UAT_DATA_DIR", str(tmp_path))
    return d


from ai_modules.execute.scenario_templates import (
    delete_custom_template,
    get_template,
    instantiate_template,
    list_templates,
    save_custom_template,
)


class TestListTemplates:
    def test_list_all_builtin(self):
        templates = list_templates()
        assert len(templates) >= 5  # 5 built-in templates
        ids = [t["template_id"] for t in templates]
        assert "ecommerce-order-verify" in ids
        assert "finance-transfer-otp" in ids

    def test_list_by_industry(self):
        ecommerce = list_templates(industry="电商")
        assert all("电商" in t.get("industry", "") for t in ecommerce)
        assert len(ecommerce) >= 1

    def test_list_by_tag(self):
        otp = list_templates(tag="OTP")
        assert len(otp) >= 1
        assert all("OTP" in t.get("tags", []) for t in otp)

    def test_list_excludes_plan_template(self):
        templates = list_templates()
        for t in templates:
            assert "plan_template" not in t  # excluded for size

    def test_list_includes_parameters(self):
        templates = list_templates()
        t = next(t for t in templates if t["template_id"] == "ecommerce-order-verify")
        assert len(t["parameters"]) >= 3
        assert any(p["name"] == "api_base_url" for p in t["parameters"])


class TestGetTemplate:
    def test_get_builtin(self):
        t = get_template("ecommerce-order-verify")
        assert t is not None
        assert t["template_id"] == "ecommerce-order-verify"
        assert "plan_template" in t
        assert len(t["plan_template"]["stages"]) >= 3

    def test_get_nonexistent(self):
        assert get_template("nonexistent") is None


class TestInstantiateTemplate:
    def test_instantiate_ecommerce(self):
        result = instantiate_template(
            "ecommerce-order-verify",
            {
                "api_base_url": "https://shop.example.com",
                "product_id": "PROD-001",
                "web_order_url": "https://shop.example.com/orders",
            },
        )
        assert result["success"] is True
        plan = result["plan"]
        assert "plan_id" in plan
        assert len(plan["stages"]) >= 3
        # Check parameter substitution
        stage1 = plan["stages"][0]
        assert "shop.example.com" in stage1["request"]["url"]
        assert "PROD-001" in json.dumps(stage1["request"]["body"])

    def test_instantiate_missing_required(self):
        result = instantiate_template(
            "ecommerce-order-verify",
            {"api_base_url": "https://example.com"},  # missing product_id
        )
        assert result["success"] is False
        assert "product_id" in result["error"]

    def test_instantiate_with_defaults(self):
        result = instantiate_template(
            "ecommerce-order-verify",
            {
                "api_base_url": "https://shop.example.com",
                "product_id": "P1",
                "web_order_url": "https://shop.example.com/orders",
                # order_amount has default "99.00"
            },
        )
        assert result["success"] is True

    def test_instantiate_custom_scenario_name(self):
        result = instantiate_template(
            "ecommerce-order-verify",
            {
                "api_base_url": "https://shop.example.com",
                "product_id": "P1",
                "web_order_url": "https://shop.example.com/orders",
            },
            scenario_name="自定义名称",
        )
        assert result["success"] is True
        assert result["plan"]["scenario"] == "自定义名称"

    def test_instantiate_nonexistent(self):
        result = instantiate_template("nonexistent", {})
        assert result["success"] is False


class TestCustomTemplates:
    def test_save_and_get_custom(self):
        payload = {
            "template_id": "my-custom",
            "name": "自定义模板",
            "industry": "测试",
            "parameters": [],
            "plan_template": {
                "stages": [{"id": "s1", "layer": "api"}],
            },
        }
        result = save_custom_template(payload)
        assert result["success"] is True

        t = get_template("my-custom")
        assert t is not None
        assert t["name"] == "自定义模板"

    def test_save_auto_id(self):
        payload = {
            "name": "自动ID",
            "plan_template": {"stages": [{"id": "s1"}]},
        }
        result = save_custom_template(payload)
        assert result["success"] is True
        assert result["template_id"].startswith("custom-")

    def test_delete_custom(self):
        save_custom_template({
            "template_id": "to-delete",
            "plan_template": {"stages": [{"id": "s1"}]},
        })
        result = delete_custom_template("to-delete")
        assert result["success"] is True
        assert get_template("to-delete") is None

    def test_delete_builtin_fails(self):
        result = delete_custom_template("ecommerce-order-verify")
        assert result["success"] is False
        assert "内置" in result["error"]

    def test_delete_nonexistent(self):
        result = delete_custom_template("nonexistent")
        assert result["success"] is False
