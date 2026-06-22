"""locator_tier_utils / locator_visual_fallback 轻量单测。"""
import json

import numpy as np
import cv2

from locator_tier_utils import (
    split_locator_candidates,
    parse_viewport_coord_value,
    merge_candidates_json,
    build_viewport_coord_candidate,
    build_vlm_ground_candidate,
    parse_vlm_ground_prompt,
)
from locator_visual_fallback import match_template_in_viewport_png, prepare_template_png_bytes_for_storage


def test_split_and_coord_parse():
    raw = [
        {"selector_type": "css", "selector_value": "#a", "score": 90},
        {"selector_type": "visual_template", "selector_value": "eHh4", "score": 40},
        {"selector_type": "viewport_coord", "selector_value": '{"fx":0.25,"fy":0.75}', "score": 20},
    ]
    dom, vis, crd, vlm = split_locator_candidates(raw)
    assert len(dom) == 1
    assert len(vis) == 1
    assert len(crd) == 1
    assert len(vlm) == 0
    assert parse_viewport_coord_value(crd[0]["selector_value"]) == (0.25, 0.75)


def test_vlm_ground_candidate():
    raw = [
        {"selector_type": "css", "selector_value": "#a", "score": 90},
        {"selector_type": "vlm_ground", "selector_value": '{"prompt":"登录按钮"}', "score": 35},
    ]
    dom, vis, crd, vlm = split_locator_candidates(raw)
    assert len(dom) == 1
    assert len(vlm) == 1
    assert parse_vlm_ground_prompt(vlm[0]["selector_value"]) == "登录按钮"
    built = build_vlm_ground_candidate("提交")
    merged = merge_candidates_json("[]", [built])
    arr = json.loads(merged)
    assert any(x.get("selector_type") == "vlm_ground" for x in arr)


def test_merge_candidates_json():
    base = json.dumps([{"selector_type": "css", "selector_value": "button", "score": 80}], ensure_ascii=False)
    extra = [build_viewport_coord_candidate(0.5, 0.5, score=22)]
    out = merge_candidates_json(base, extra)
    arr = json.loads(out)
    assert any(x.get("selector_type") == "viewport_coord" for x in arr)


def test_match_template_self():
    img = np.zeros((80, 120, 3), dtype=np.uint8)
    cv2.rectangle(img, (40, 30), (70, 50), (0, 255, 0), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    tpl = img[28:52, 38:72]
    ok2, tbuf = cv2.imencode(".png", tpl)
    assert ok2
    import base64

    payload = base64.b64encode(bytes(tbuf)).decode("ascii")
    hit = match_template_in_viewport_png(bytes(buf), payload)
    assert hit is not None
    cx, cy, mv = hit
    assert mv >= 0.9
    assert 40 <= cx <= 80
    assert 30 <= cy <= 55


def test_prepare_template_shrink():
    big = np.ones((400, 400, 3), dtype=np.uint8) * 200
    ok, buf = cv2.imencode(".png", big)
    assert ok
    small = prepare_template_png_bytes_for_storage(bytes(buf))
    assert len(small) < len(bytes(buf))
