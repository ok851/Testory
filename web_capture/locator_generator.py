# -*- coding: utf-8 -*-
"""智能定位策略生成。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def looks_dynamic_dom_id(v: str) -> bool:
    s = str(v or "").strip()
    if not s:
        return False
    low = s.lower()
    if len(s) >= 16 and re.search(r"\d{8,}", s):
        return True
    if re.search(r"[a-f0-9]{10,}", low) and re.search(r"\d{4,}", s):
        return True
    if re.search(r"(?:^|[_-])(id|card|row|item)?\d{10,}$", low):
        return True
    if re.search(r"\d{6,}", s):
        return True
    return False


def stable_class_tokens(class_name: str) -> List[str]:
    out: List[str] = []
    for token in str(class_name or "").split():
        t = token.strip()
        if not t or len(t) <= 2:
            continue
        if re.search(r"\d{4,}", t):
            continue
        if re.search(r"[a-f0-9]{8,}", t.lower()):
            continue
        out.append(t)
    return out[:3]


def _escape_xpath(s: str) -> str:
    if '"' not in s:
        return f'"{s}"'
    if "'" not in s:
        return f"'{s}'"
    parts = s.split('"')
    return "concat(" + ", '\"', ".join(f'"{p}"' for p in parts) + ")"


def build_xpath_relative(element: Dict[str, Any], attrs: Dict[str, str]) -> str:
    tag = (element.get("tagName") or "div").lower()
    eid = str(element.get("id") or "")
    if eid and not looks_dynamic_dom_id(eid):
        return f"//{tag}[@id={_escape_xpath(eid)}]"
    name = attrs.get("name") or element.get("name") or ""
    if name:
        return f"//{tag}[@name={_escape_xpath(name)}]"
    testid = attrs.get("data-testid") or attrs.get("data-test") or ""
    if testid:
        return f"//{tag}[@data-testid={_escape_xpath(testid)}]"
    aria = attrs.get("aria-label") or ""
    if aria:
        return f"//{tag}[@aria-label={_escape_xpath(aria)}]"
    classes = stable_class_tokens(element.get("className", ""))
    if classes:
        pred = " and ".join(f"contains(@class, '{c}')" for c in classes[:2])
        return f"//{tag}[{pred}]"
    text = (element.get("textContent") or "").strip()
    if text and len(text) <= 40:
        return f"//{tag}[contains(normalize-space(.), {_escape_xpath(text[:32])})]"
    return f"//{tag}"


def build_css_selector(element: Dict[str, Any]) -> str:
    tag = (element.get("tagName") or "div").lower()
    eid = str(element.get("id") or "")
    if eid and not looks_dynamic_dom_id(eid):
        return f"#{eid}"
    classes = stable_class_tokens(element.get("className", ""))
    if classes:
        return tag + "." + ".".join(classes)
    return tag


def generate_locator_candidates(
    element: Dict[str, Any],
    *,
    css_selector: str = "",
    text_content: str = "",
) -> List[Dict[str, Any]]:
    attrs = element.get("attributes", {}) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    pack: List[Dict[str, Any]] = []

    testid = attrs.get("data-testid") or attrs.get("data-test") or attrs.get("data-id") or ""
    if testid:
        pack.append(
            {
                "selector_type": "data",
                "selector_value": f"testid={testid}",
                "score": 100,
            }
        )

    ename = attrs.get("name") or element.get("name") or ""
    if ename:
        pack.append({"selector_type": "name", "selector_value": ename, "score": 98})

    eid = str(element.get("id") or "")
    if eid and not looks_dynamic_dom_id(eid):
        pack.append({"selector_type": "id", "selector_value": eid, "score": 96})

    aria = attrs.get("aria-label") or element.get("ariaLabel") or ""
    role = attrs.get("role") or element.get("role") or ""
    if aria and role:
        pack.append(
            {
                "selector_type": "aria",
                "selector_value": f"role={role}[name={aria}]",
                "score": 92,
            }
        )
    elif aria:
        pack.append({"selector_type": "aria", "selector_value": f"name={aria}", "score": 90})

    css = css_selector or build_css_selector(element)
    if css:
        pack.append({"selector_type": "css", "selector_value": css, "score": 88})

    for cls in stable_class_tokens(element.get("className", "")):
        pack.append({"selector_type": "css", "selector_value": f".{cls}", "score": 82})

    txt = (text_content or element.get("textContent") or "").strip()
    if txt and len(txt) <= 48 and "\n" not in txt and '"' not in txt:
        pack.append({"selector_type": "partial_text", "selector_value": txt, "score": 76})

    xpath_rel = build_xpath_relative(element, attrs)
    pack.append({"selector_type": "xpath", "selector_value": xpath_rel, "score": 74})

    xpath_abs = element.get("xpath") or element.get("xpath_absolute") or ""
    if xpath_abs:
        pack.append({"selector_type": "xpath", "selector_value": xpath_abs, "score": 60})

    dedup: List[Dict[str, Any]] = []
    seen = set()
    for p in pack:
        k = (
            str(p.get("selector_type") or "").lower(),
            str(p.get("selector_value") or ""),
        )
        if not k[1] or k in seen:
            continue
        seen.add(k)
        dedup.append(p)
    dedup.sort(key=lambda x: -int(x.get("score") or 0))
    return dedup


def build_key_candidates(element: Dict[str, Any]) -> List[Dict[str, Any]]:
    """精准定位 Tab 属性表。"""
    attrs = element.get("attributes", {}) or {}
    rows: List[Dict[str, Any]] = []
    mapping = [
        ("tag", element.get("tagName") or ""),
        ("id", element.get("id") or ""),
        ("class", element.get("className") or ""),
        ("name", attrs.get("name") or element.get("name") or ""),
        ("innerText", (element.get("textContent") or "")[:80]),
        ("aria-label", attrs.get("aria-label") or ""),
        ("role", attrs.get("role") or ""),
        ("type", attrs.get("type") or ""),
        ("href", attrs.get("href") or ""),
        ("placeholder", attrs.get("placeholder") or ""),
    ]
    for prop, val in mapping:
        v = str(val or "").strip()
        if not v:
            continue
        rows.append(
            {
                "property": prop,
                "value": v,
                "match": "equals",
                "enabled": prop in ("id", "name", "aria-label", "role", "data-testid"),
            }
        )
    testid = attrs.get("data-testid") or attrs.get("data-test") or ""
    if testid:
        rows.append(
            {
                "property": "data-testid",
                "value": testid,
                "match": "equals",
                "enabled": True,
            }
        )
    return rows


def format_dom_pick_payload(raw: Dict[str, Any], *, capture_mode: str = "cdp") -> Dict[str, Any]:
    """将页面回传的拾取 payload 转为步骤表单 + element_definition 字段。"""
    element = raw.get("elementInfo", {}) or {}
    css_selector = str(raw.get("selector") or raw.get("css_selector") or "").strip()
    if not css_selector:
        css_selector = build_css_selector(element)
    text_content = (element.get("textContent") or "").strip()
    attrs = element.get("attributes", {}) or {}
    element_id = str(element.get("id") or "")
    data_testid = (
        attrs.get("data-testid")
        or attrs.get("data-test")
        or attrs.get("data-id")
        or ""
    )

    candidates = generate_locator_candidates(
        element, css_selector=css_selector, text_content=text_content
    )
    if not candidates:
        candidates = [{"selector_type": "css", "selector_value": css_selector, "score": 50}]

    primary = candidates[0]
    selector_type = primary["selector_type"]
    selector_value = primary["selector_value"]
    if (
        text_content
        and len(text_content) > 5
        and selector_type == "css"
        and not element_id
        and not data_testid
    ):
        for c in candidates:
            if c.get("selector_type") == "partial_text":
                selector_type = "partial_text"
                selector_value = c["selector_value"]
                break

    class_name = element.get("className", "") or ""
    class_tokens_raw = [c.strip() for c in str(class_name).split() if c.strip()]
    class_set = {c.lower() for c in class_tokens_raw}
    is_card_list_container = (
        "card-list" in class_set
        or any(
            ("list" in c.lower() or "container" in c.lower()) for c in class_tokens_raw
        )
    )
    card_item_xpath = ""
    if is_card_list_container:
        stable_root = stable_class_tokens(class_name)
        if stable_root:
            root_pred = " and ".join(
                [f"contains(@class,'{c}')" for c in stable_root[:2]]
            )
            root_xpath = f"//*[{root_pred}]"
        else:
            root_xpath = (
                "//*[contains(@class,'card-list') or contains(@class,'list-container')]"
            )
        card_item_xpath = (
            f"{root_xpath}"
            "//*[contains(@class,'outer-card') or contains(@class,'card') "
            "or contains(@class,'item') or @role='listitem']"
        )
        selector_type = "xpath"
        selector_value = card_item_xpath

    xpath_relative = build_xpath_relative(element, attrs if isinstance(attrs, dict) else {})
    xpath_absolute = element.get("xpath") or element.get("xpath_absolute") or raw.get("xpath_absolute") or ""

    page_name = str(raw.get("page_name") or raw.get("page_title") or "")
    locator_candidates_json = json.dumps(candidates, ensure_ascii=False)

    base = {
        "selector_type": selector_type,
        "selector_value": selector_value,
        "text_content": text_content,
        "page_name": page_name,
        "tag_name": (element.get("tagName") or "").lower(),
        "css_selector": css_selector,
        "id": str(element.get("id") or ""),
        "class_name": class_name,
        "locator_candidates": locator_candidates_json,
        "is_card_list_container": is_card_list_container,
        "card_item_xpath": card_item_xpath,
        "dynamic_id_ignored": bool(element.get("id") and looks_dynamic_dom_id(str(element.get("id")))),
        "source_frame": raw.get("source_frame") or "",
        "source_url": raw.get("source_url") or raw.get("page_url") or "",
        "dom_path": raw.get("dom_path") or [],
        "xpath_absolute": xpath_absolute,
        "xpath_relative": xpath_relative,
        "key_candidates": build_key_candidates(element),
        "capture_mode": capture_mode,
    }

    from web_capture.element_definition import build_element_definition, flatten_to_step_fields

    defn = build_element_definition(
        {**raw, **base, "elementInfo": element, "locator_candidates": candidates},
        capture_mode=capture_mode,
    )
    base.update(flatten_to_step_fields(defn))
    base["element_definition"] = json.dumps(defn, ensure_ascii=False)
    return base
