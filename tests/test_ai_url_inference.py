"""Regression: Baidu-style goals must not keep example.com case_url or empty selectors when site fallback applies."""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from ai_local_inference import (
    _goal_suggests_seed_url,
    _placeholder_template_case_url,
    local_ai_service,
)


def test_goal_suggests_baidu_search_url() -> None:
    assert _goal_suggests_seed_url("在百度搜索自动化测试") == "https://www.baidu.com/"
    assert _goal_suggests_seed_url("random") == ""


def test_placeholder_template_hosts() -> None:
    assert _placeholder_template_case_url("https://example.com/login") is True
    assert _placeholder_template_case_url("https://www.baidu.com/") is False


def test_normalize_replaces_example_case_url_and_prepends_navigate() -> None:
    data = {
        "case_url": "https://example.com/login",
        "steps": [
            {
                "action": "click",
                "selector_type": "",
                "selector_value": "",
                "input_value": "",
                "description": "点击百度一下",
            }
        ],
    }
    out = local_ai_service._normalize_output(
        data, "在百度搜索自动化测试", "proj", "model", probe_registry=None
    )
    assert out["case_url"] == "https://www.baidu.com/"
    assert out["steps"][0]["action"] == "navigate"
    assert out["steps"][0]["input_value"] == "https://www.baidu.com/"
    assert out["steps"][1]["action"] == "click"
    assert out["steps"][1]["selector_value"] == "#su"


def test_navigate_step_sanitizes_placeholder_url() -> None:
    data = {
        "case_url": "",
        "steps": [
            {
                "action": "navigate",
                "selector_type": "",
                "selector_value": "",
                "input_value": "https://example.com/login",
                "description": "go",
            }
        ],
    }
    out = local_ai_service._normalize_output(
        data, "在百度搜索自动化测试", "proj", "model", probe_registry=None
    )
    assert out["case_url"] == "https://www.baidu.com/"
    assert out["steps"][0]["input_value"] == "https://www.baidu.com/"


if __name__ == "__main__":
    test_goal_suggests_baidu_search_url()
    test_placeholder_template_hosts()
    test_normalize_replaces_example_case_url_and_prepends_navigate()
    test_navigate_step_sanitizes_placeholder_url()
    print("ok")
