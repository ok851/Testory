# -*- coding: utf-8 -*-

from web_capture.locator_generator import (
    format_dom_pick_payload,
    generate_locator_candidates,
    looks_dynamic_dom_id,
)


def test_dynamic_id_detection():
    assert looks_dynamic_dom_id("item-928374928374")
    assert not looks_dynamic_dom_id("kw")


def test_generate_candidates_prefers_testid():
    element = {
        "tagName": "INPUT",
        "id": "kw",
        "className": "",
        "textContent": "",
        "attributes": {"data-testid": "search-input", "name": "wd"},
    }
    cands = generate_locator_candidates(element)
    assert cands[0]["selector_type"] == "data"
    assert "testid=search-input" in cands[0]["selector_value"]


def test_format_dom_pick_payload():
    raw = {
        "selector": "#kw",
        "elementInfo": {
            "tagName": "INPUT",
            "id": "kw",
            "className": "",
            "textContent": "搜索",
            "attributes": {"name": "wd"},
        },
        "source_url": "https://www.baidu.com",
    }
    out = format_dom_pick_payload(raw, capture_mode="cdp")
    assert out["selector_type"] in ("id", "name", "css", "data")
    assert out["selector_value"]
    assert "element_definition" in out
