# -*- coding: utf-8 -*-
"""前端源码轻量语义解析：精准识别可交互 UI 组件/标签与稳定定位线索。

不做完整 AST 依赖；用结构化正则 + 启发式覆盖 React/JSX、Vue SFC、常见 UI 库。
输出供 AI Agent 生成可靠自动化用例。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# ---- 稳定定位相关属性 ----
_TESTID_ATTRS = r"data-testid|data-test-id|data-cy|data-qa|data-test"
_ROLE_RE = re.compile(r"""\brole\s*=\s*['"]([^'"]+)['"]""", re.I)
_NAME_ATTR_RE = re.compile(r"""\bname\s*=\s*['"]([^'"]+)['"]""", re.I)
_PLACEHOLDER_RE = re.compile(r"""placeholder\s*=\s*['"]([^'"]+)['"]""", re.I)
_TYPE_RE = re.compile(r"""\btype\s*=\s*['"]([^'"]+)['"]""", re.I)
_ID_RE = re.compile(r"""\bid\s*=\s*['"]([^'"]+)['"]""", re.I)
_CLASS_RE = re.compile(r"""(?:className|class)\s*=\s*['"]([^'"]+)['"]""", re.I)
_ARIA_RE = re.compile(r"""aria-label\s*=\s*['"]([^'"]+)['"]""", re.I)
_TESTID_RE = re.compile(
    rf"""(?:{_TESTID_ATTRS})\s*=\s*['"]([^'"]+)['"]""",
    re.I,
)

# 仅匹配开标签，避免父节点吞掉子组件
_OPEN_TAG_RE = re.compile(
    r"""<(?P<tag>[A-Za-z][\w.-]*)(?P<attrs>(?:\s[^<>]*?)?)\s*(?P<self>/)?>""",
)

_HANDLER_RE = re.compile(
    r"""(?:on([A-Z][a-zA-Z]+)|@([a-z]+)|v-on:([a-z]+))\s*=\s*"""
    r"""(?:\{([^}]{0,120})\}|['"]([^'"]+)['"]|([A-Za-z_][\w.]*))""",
    re.I,
)

_VIF_RE = re.compile(r"""(?:v-if|v-show|v-else-if)\s*=\s*['"]([^'"]+)['"]""", re.I)
_JSX_COND_RE = re.compile(r"""\{([^}]{0,80}?&&[^}]{0,80})\}""")
_ROUTE_RE = re.compile(
    r"""(?:path|route|to|href)\s*[:=]\s*['"](/[^'"]{1,160})['"]""",
    re.I,
)

# 组件名 → 语义角色
_COMPONENT_ROLES: Dict[str, str] = {
    "button": "button",
    "btn": "button",
    "a": "link",
    "link": "link",
    "navlink": "link",
    "input": "textbox",
    "textarea": "textbox",
    "select": "combobox",
    "option": "option",
    "checkbox": "checkbox",
    "radio": "radio",
    "switch": "switch",
    "form": "form",
    "formitem": "form_field",
    "form.item": "form_field",
    "modal": "dialog",
    "dialog": "dialog",
    "drawer": "dialog",
    "table": "table",
    "tab": "tab",
    "tabs": "tablist",
    "tabpane": "tab",
    "menu": "menu",
    "menuitem": "menuitem",
    "dropdown": "menu",
    "select.option": "option",
    "datepicker": "datepicker",
    "date-picker": "datepicker",
    "upload": "upload",
    "pagination": "pagination",
    "card": "region",
    "list": "list",
    "tree": "tree",
    "treeselect": "combobox",
    "cascader": "combobox",
    "autocomplete": "combobox",
    "search": "search",
    "el-button": "button",
    "el-input": "textbox",
    "el-select": "combobox",
    "el-form": "form",
    "el-dialog": "dialog",
    "el-table": "table",
    "el-tab-pane": "tab",
    "a-button": "button",
    "a-input": "textbox",
    "a-select": "combobox",
    "a-form": "form",
    "a-modal": "dialog",
    "a-table": "table",
    "a-tabs": "tablist",
    "buttonbase": "button",
    "textfield": "textbox",
    "outlinedinput": "textbox",
}

_INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio", "switch",
    "form", "form_field", "dialog", "table", "tab", "tablist", "menu",
    "menuitem", "datepicker", "upload", "pagination", "search", "option",
}

_TEXT_CHILD_RE = re.compile(
    r""">\s*([^<>{}\n]{1,60}?)\s*<"""
    r"""|(?:>|\})\s*\{?\s*['"]([^'"]{1,60})['"]\s*\}?\s*(?:</|<)"""
)


def _norm_tag(tag: str) -> str:
    return (tag or "").strip()


def _role_of(tag: str) -> str:
    t = _norm_tag(tag).lower()
    if t in _COMPONENT_ROLES:
        return _COMPONENT_ROLES[t]
    # PascalCase 组件：Button / LoginForm
    simple = t.split(".")[-1]
    if simple in _COMPONENT_ROLES:
        return _COMPONENT_ROLES[simple]
    # 启发式：含 Button/Input/Select 等
    for key, role in (
        ("button", "button"),
        ("input", "textbox"),
        ("select", "combobox"),
        ("modal", "dialog"),
        ("dialog", "dialog"),
        ("table", "table"),
        ("form", "form"),
        ("tab", "tab"),
        ("menu", "menu"),
        ("link", "link"),
        ("upload", "upload"),
        ("switch", "switch"),
        ("checkbox", "checkbox"),
    ):
        if key in simple:
            return role
    if t in ("div", "span", "p", "section", "header", "footer", "main"):
        return "container"
    return "component"


def _attr(attrs: str, pattern: re.Pattern) -> Optional[str]:
    m = pattern.search(attrs or "")
    return m.group(1).strip() if m else None


def _visible_text(attrs: str, body: str) -> str:
    # children 文本优先
    if body:
        m = _TEXT_CHILD_RE.search(">" + body[:200] + "<")
        if m:
            return (m.group(1) or m.group(2) or "").strip()
        # 简单去标签
        plain = re.sub(r"<[^>]+>", "", body)
        plain = re.sub(r"\{[^}]+\}", "", plain).strip()
        if 0 < len(plain) <= 40 and "\n" not in plain:
            return plain
    for pat in (_ARIA_RE, _PLACEHOLDER_RE):
        v = _attr(attrs, pat)
        if v:
            return v
    return ""


def _handlers(attrs: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in _HANDLER_RE.finditer(attrs or ""):
        ev = (m.group(1) or m.group(2) or m.group(3) or "").lower()
        if m.group(1):
            ev = ev[:1].lower() + ev[1:] if ev else ev
            # onClick -> click
            if ev.startswith("on"):
                ev = ev[2:].lower()
            else:
                ev = ev.lower()
        handler = (m.group(4) or m.group(5) or m.group(6) or "").strip()
        if ev:
            out.append({"event": ev, "handler": handler[:120]})
    return out[:8]


def _locator_candidates(node: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按稳定性排序的定位候选。"""
    cands: List[Dict[str, Any]] = []
    testid = node.get("testid")
    if testid:
        cands.append({
            "priority": 1,
            "strategy": "testid",
            "selector_type": "css",
            "selector_value": f'[data-testid="{testid}"]',
            "stability": "high",
            "reason": "data-testid 是跨重构最稳定位",
        })
    role = node.get("role") or node.get("semantic_role")
    name = node.get("accessible_name") or node.get("visible_text") or node.get("aria_label")
    if role and name and role in _INTERACTIVE_ROLES:
        cands.append({
            "priority": 2,
            "strategy": "role_name",
            "selector_type": "role",
            "selector_value": f'role={role}[name="{name}"]',
            "stability": "high",
            "reason": "无障碍 role+name，贴近用户语义",
        })
    if node.get("aria_label"):
        cands.append({
            "priority": 3,
            "strategy": "aria_label",
            "selector_type": "css",
            "selector_value": f'[aria-label="{node["aria_label"]}"]',
            "stability": "medium",
            "reason": "aria-label 语义定位",
        })
    if node.get("placeholder"):
        cands.append({
            "priority": 4,
            "strategy": "placeholder",
            "selector_type": "css",
            "selector_value": f'[placeholder="{node["placeholder"]}"]',
            "stability": "medium",
            "reason": "输入框 placeholder",
        })
    if node.get("name_attr"):
        cands.append({
            "priority": 4,
            "strategy": "name",
            "selector_type": "css",
            "selector_value": f'[name="{node["name_attr"]}"]',
            "stability": "medium",
            "reason": "表单 name 属性",
        })
    if node.get("element_id"):
        cands.append({
            "priority": 5,
            "strategy": "id",
            "selector_type": "css",
            "selector_value": f'#{node["element_id"]}',
            "stability": "medium",
            "reason": "id 可能随构建变化，次选",
        })
    if node.get("visible_text") and node.get("semantic_role") in ("button", "link", "tab", "menuitem"):
        cands.append({
            "priority": 6,
            "strategy": "text",
            "selector_type": "text",
            "selector_value": node["visible_text"],
            "stability": "low",
            "reason": "可见文案，易因 i18n/文案改动失效，需标记视觉可定位",
        })
    if node.get("css_hint"):
        cands.append({
            "priority": 9,
            "strategy": "css_class",
            "selector_type": "css",
            "selector_value": node["css_hint"],
            "stability": "low",
            "reason": "class 不稳定，仅兜底",
        })
    cands.sort(key=lambda x: int(x["priority"]))
    return cands


def _extract_child_text(full: str, end: int, tag: str) -> str:
    """开标签结束后，若有简单文本子节点则提取。"""
    rest = full[end: end + 120]
    m = re.match(
        r"\s*([^<>\n]{1,60}?)\s*</" + re.escape(tag) + r"\s*>",
        rest,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m2 = re.match(r"""\s*\{?\s*['"]([^'"]{1,60})['"]\s*\}?""", rest)
    if m2:
        return m2.group(1).strip()
    m3 = re.match(r"""\s*([^<\n{]{1,40})\s*<""", rest)
    if m3:
        return m3.group(1).strip()
    return ""


def _parse_open_tag(m: re.Match, source_file: str, full_text: str) -> Optional[Dict[str, Any]]:
    tag = m.group("tag")
    attrs = m.group("attrs") or ""
    self_closing = bool(m.group("self"))
    body = "" if self_closing else _extract_child_text(full_text, m.end(), tag)
    semantic = _role_of(tag)
    testid = _attr(attrs, _TESTID_RE)
    aria = _attr(attrs, _ARIA_RE)
    placeholder = _attr(attrs, _PLACEHOLDER_RE)
    name_attr = _attr(attrs, _NAME_ATTR_RE)
    el_id = _attr(attrs, _ID_RE)
    role_attr = _attr(attrs, _ROLE_RE)
    input_type = _attr(attrs, _TYPE_RE)
    class_raw = _attr(attrs, _CLASS_RE) or ""
    css_hint = ""
    if class_raw:
        first = class_raw.split()[0]
        if first and not first.startswith("{") and len(first) < 40:
            css_hint = f".{first}"

    visible = _visible_text(attrs, body)
    handlers = _handlers(attrs)
    conditions: List[str] = []
    for cm in _VIF_RE.finditer(attrs):
        conditions.append(cm.group(1))
    for cm in _JSX_COND_RE.finditer(attrs):
        conditions.append(cm.group(1).strip()[:80])

    # 跳过纯容器且无定位线索、无事件
    if semantic == "container" and not testid and not aria and not handlers:
        return None
    if semantic == "component" and not testid and not aria and not handlers and not visible:
        if not any(k in tag.lower() for k in ("button", "input", "form", "modal", "table", "select", "tab", "dialog")):
            return None

    node = {
        "tag": tag,
        "semantic_role": semantic,
        "role": role_attr or (semantic if semantic in _INTERACTIVE_ROLES else None),
        "testid": testid,
        "aria_label": aria,
        "placeholder": placeholder,
        "name_attr": name_attr,
        "element_id": el_id,
        "input_type": input_type,
        "visible_text": visible,
        "accessible_name": aria or visible or placeholder or name_attr or "",
        "handlers": handlers,
        "conditions": conditions[:5],
        "css_hint": css_hint,
        "source_file": source_file,
        "self_closing": self_closing,
        "interactive": semantic in _INTERACTIVE_ROLES or bool(handlers) or bool(testid),
    }
    node["locators"] = _locator_candidates(node)
    node["best_locator"] = node["locators"][0] if node["locators"] else None
    return node


def parse_frontend_source(
    content: str,
    *,
    source_file: str = "",
) -> Dict[str, Any]:
    """解析单文件，返回组件清单。"""
    text = content or ""
    nodes: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for m in _OPEN_TAG_RE.finditer(text[:80_000]):
        node = _parse_open_tag(m, source_file, text)
        if not node:
            continue
        key = (
            f"{node.get('testid')}|{node.get('tag')}|{node.get('accessible_name')}|"
            f"{node.get('semantic_role')}|{node.get('source_file')}"
        )
        if key in seen:
            continue
        seen.add(key)
        nodes.append(node)

    routes = sorted(set(_ROUTE_RE.findall(text)))[:30]
    forms = [n for n in nodes if n.get("semantic_role") in ("form", "form_field")]
    dialogs = [n for n in nodes if n.get("semantic_role") == "dialog"]
    interactive = [n for n in nodes if n.get("interactive")]

    export_names: List[str] = []
    for pat in (
        r"export\s+(?:default\s+)?(?:function|const|class)\s+([A-Z][A-Za-z0-9_]*)",
        r"defineComponent\s*\(\s*\{[^}]*name\s*:\s*['\"]([^'\"]+)['\"]",
    ):
        export_names.extend(re.findall(pat, text))

    return {
        "source_file": source_file,
        "export_components": sorted(set(export_names))[:20],
        "nodes": nodes[:120],
        "interactive_nodes": interactive[:80],
        "forms": forms[:20],
        "dialogs": dialogs[:10],
        "routes": routes,
        "counts": {
            "nodes": len(nodes),
            "interactive": len(interactive),
            "with_testid": sum(1 for n in nodes if n.get("testid")),
            "high_stability": sum(
                1 for n in interactive
                if (n.get("best_locator") or {}).get("stability") == "high"
            ),
        },
    }


def parse_frontend_files(
    file_snippets: Dict[str, Any],
    *,
    diff: str = "",
) -> Dict[str, Any]:
    """多文件解析 + 可选 diff 中新增片段。"""
    snippets = file_snippets if isinstance(file_snippets, dict) else {}
    inventories: List[Dict[str, Any]] = []
    for path, content in list(snippets.items())[:40]:
        p = str(path)
        if not _looks_frontend(p, str(content)):
            continue
        inventories.append(parse_frontend_source(str(content)[:80_000], source_file=p))

    # diff 中 + 行也可补解析
    if diff and not inventories:
        added = "\n".join(
            line[1:] for line in (diff or "").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if added.strip():
            inventories.append(parse_frontend_source(added[:40_000], source_file="(diff)"))

    all_interactive: List[Dict[str, Any]] = []
    all_routes: List[str] = []
    for inv in inventories:
        all_interactive.extend(inv.get("interactive_nodes") or [])
        all_routes.extend(inv.get("routes") or [])

    # 去重 interactive
    uniq: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for n in all_interactive:
        k = f"{n.get('testid')}|{n.get('accessible_name')}|{n.get('semantic_role')}|{n.get('source_file')}"
        if k in seen:
            continue
        seen.add(k)
        uniq.append(n)

    high = [n for n in uniq if (n.get("best_locator") or {}).get("stability") == "high"]
    medium = [n for n in uniq if (n.get("best_locator") or {}).get("stability") == "medium"]
    low = [n for n in uniq if (n.get("best_locator") or {}).get("stability") == "low"]

    return {
        "files_parsed": len(inventories),
        "inventories": inventories,
        "interactive_nodes": uniq[:100],
        "routes": sorted(set(all_routes))[:40],
        "stability_buckets": {
            "high": len(high),
            "medium": len(medium),
            "low": len(low),
        },
        "recommended_for_automation": (high + medium)[:40],
        "summary": (
            f"解析 {len(inventories)} 个前端文件，"
            f"可交互节点 {len(uniq)}（高稳 {len(high)} / 中 {len(medium)} / 低 {len(low)}）"
        ),
    }


def _looks_frontend(path: str, content: str) -> bool:
    p = (path or "").lower()
    if any(p.endswith(ext) for ext in (
        ".tsx", ".jsx", ".vue", ".svelte", ".html", ".ts", ".js",
    )):
        return True
    head = (content or "")[:500]
    return bool(re.search(r"<(?:div|button|input|template|script)|className=|data-testid", head))


def inventory_to_prompt_block(inventory: Dict[str, Any], *, max_nodes: int = 30) -> str:
    """压缩为 LLM 可读块。"""
    lines = [inventory.get("summary") or "", "优先自动化节点（按稳定性）:"]
    nodes = inventory.get("recommended_for_automation") or inventory.get("interactive_nodes") or []
    for i, n in enumerate(nodes[:max_nodes], 1):
        loc = n.get("best_locator") or {}
        lines.append(
            f"{i}. [{n.get('semantic_role')}] tag={n.get('tag')} "
            f"name={n.get('accessible_name')!r} testid={n.get('testid')!r} "
            f"locator={loc.get('selector_type')}:{loc.get('selector_value')} "
            f"stability={loc.get('stability')} file={n.get('source_file')} "
            f"events={[h.get('event') for h in (n.get('handlers') or [])]}"
        )
    routes = inventory.get("routes") or []
    if routes:
        lines.append("routes: " + ", ".join(routes[:15]))
    return "\n".join(lines)
