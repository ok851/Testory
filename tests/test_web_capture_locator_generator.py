# -*- coding: utf-8 -*-

from web_capture.locator_generator import (
    format_dom_pick_payload,
    generate_locator_candidates,
    looks_dynamic_dom_id,
    select_primary_locator,
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


def test_select_primary_locator_skips_non_unique_id():
    cands = [
        {"selector_type": "id", "selector_value": "btn", "score": 96},
        {"selector_type": "name", "selector_value": "submit", "score": 98},
    ]
    st, sv, mc = select_primary_locator(cands, {"id|btn": 3, "name|submit": 1})
    assert st == "name"
    assert sv == "submit"
    assert mc == 1


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
        "verified_counts": {"id|kw": 1, "name|wd": 1},
        "source_url": "https://www.baidu.com",
    }
    out = format_dom_pick_payload(raw, capture_mode="extension")
    assert out["selector_type"] in ("id", "name", "css", "data")
    assert out["selector_value"]
    assert "element_definition" in out
