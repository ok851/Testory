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


def test_clamp_overwrites_selector_when_probe_index_set() -> None:
    reg = [
        {
            "i": 1,
            "tag": "input",
            "recommended_selector": "#kw",
            "recommended_selector_type": "css",
            "css": "#kw",
            "ph": "搜索",
            "txt": "",
            "al": "",
            "id": "kw",
            "name": "",
            "typ": "text",
            "rid": "",
        }
    ]
    data = {
        "case_url": "https://www.baidu.com/",
        "steps": [
            {
                "action": "click",
                "probe_index": 1,
                "selector_type": "css",
                "selector_value": ".hallucinated",
                "input_value": "",
                "description": "点输入框",
            },
        ],
    }
    out = local_ai_service._normalize_output(data, "在百度搜索", "proj", "model", probe_registry=reg)
    click_step = next(s for s in out["steps"] if s.get("action") == "click")
    assert click_step["selector_value"] == "#kw"
    # 归一化阶段已按 probe_index 对齐 recommended；clamp 再写一次时 sv 已一致，未必产生「不一致」告警。


def test_clamp_rewrites_unknown_selector_via_probe_pick() -> None:
    reg = [
        {
            "i": 1,
            "tag": "input",
            "recommended_selector": "#kw",
            "recommended_selector_type": "css",
            "css": "#kw",
            "ph": "搜索",
            "txt": "",
            "al": "",
            "id": "kw",
            "name": "",
            "typ": "text",
            "rid": "searchbox",
        },
        {
            "i": 2,
            "tag": "button",
            "recommended_selector": "#su",
            "recommended_selector_type": "css",
            "css": "#su",
            "txt": "百度一下",
            "al": "",
            "ph": "",
            "id": "su",
            "name": "",
            "typ": "submit",
            "rid": "button",
        },
    ]
    data = {
        "case_url": "https://www.baidu.com/",
        "steps": [
            {
                "action": "click",
                "selector_type": "css",
                "selector_value": ".made-up-class",
                "input_value": "",
                "description": "点击百度一下按钮",
            },
        ],
    }
    out = local_ai_service._normalize_output(data, "在百度搜索", "proj", "model", probe_registry=reg)
    click_step = next(s for s in out["steps"] if s.get("action") == "click")
    assert click_step["selector_value"] == "#su"
    cw = (out.get("meta") or {}).get("selector_clamp_warnings") or []
    assert any("未出现在 LIVE" in x for x in cw)


if __name__ == "__main__":
    test_goal_suggests_baidu_search_url()
    test_placeholder_template_hosts()
    test_normalize_replaces_example_case_url_and_prepends_navigate()
    test_navigate_step_sanitizes_placeholder_url()
    test_clamp_overwrites_selector_when_probe_index_set()
    test_clamp_rewrites_unknown_selector_via_probe_pick()
    print("ok")
