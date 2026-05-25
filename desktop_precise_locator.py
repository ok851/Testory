# -*- coding: utf-8 -*-
"""
桌面「精准定位」：对齐市面 RPA（截图预览 + UIA 路径），执行时不依赖 Z 序/是否被遮挡。
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from desktop_locator import is_desktop_shell_spec, parse_desktop_spec


def standard_desktop_shell_spec() -> Dict[str, Any]:
    """固定附着 explorer 桌面层，而非 WindowFromPoint 得到的覆盖窗口。"""
    return {
        "surface": "desktop_shell",
        "process": "explorer.exe",
        "window_title": "Program Manager",
        "class_name": "Progman",
    }


def synthesize_desktop_icon_uia_path(icon_name: str) -> List[Dict[str, Any]]:
    """生成与市面 RPA 一致的桌面图标 UIA 链（WorkerW/Progman → DefView → ListView → ListItem）。"""
    name = (icon_name or "").strip()
    nodes = [
        {"control_type": "Window", "class_name": "Progman|WorkerW", "name": ""},
        {"control_type": "Pane", "class_name": "SHELLDLL_DefView", "name": ""},
        {"control_type": "List", "class_name": "SysListView32", "name": "桌面"},
    ]
    if name:
        nodes.append({"control_type": "ListItem", "name": name})
    try:
        from desktop_picker import _normalize_desktop_uia_path

        return _normalize_desktop_uia_path(nodes)
    except ImportError:
        return nodes


def capture_rect_preview_b64(
    left: int,
    top: int,
    right: int,
    bottom: int,
    *,
    padding: int = 8,
) -> str:
    """捕获元素区域截图（base64 PNG），用于预览与 visual_template。"""
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore
    except ImportError:
        return ""
    l = int(min(left, right)) - padding
    t = int(min(top, bottom)) - padding
    w = max(4, int(max(left, right) - min(left, right)) + 2 * padding)
    h = max(4, int(max(top, bottom) - min(top, bottom)) + 2 * padding)
    try:
        with mss.mss() as sct:
            mon = {"left": l, "top": t, "width": w, "height": h}
            shot = sct.grab(mon)
            return base64.b64encode(mss.tools.to_png(shot.rgb, shot.size)).decode(
                "ascii"
            )
    except Exception:
        return ""


def build_visual_template_candidate(png_b64: str, score: int = 96) -> Dict[str, Any]:
    payload = json.dumps(
        {"png_b64": png_b64, "threshold": 0.72},
        ensure_ascii=False,
    )
    return {
        "selector_type": "visual_template",
        "selector_value": payload,
        "score": score,
    }


def build_relative_coord_value(
    spec: Dict[str, Any],
    screen_x: int,
    screen_y: int,
) -> str:
    """客户区相对百分比坐标（窗口缩放/移动后更稳）。"""
    hwnd = int((spec or {}).get("hwnd") or 0)
    if not hwnd:
        return ""
    try:
        import ctypes

        from desktop_input import screen_to_client_xy

        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        if not user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
            return ""
        cw = int(rect.right) - int(rect.left)
        ch = int(rect.bottom) - int(rect.top)
        if cw < 4 or ch < 4:
            return ""
        cx, cy = screen_to_client_xy(hwnd, int(screen_x), int(screen_y))
        if cx < 0 or cy < 0 or cx > cw or cy > ch:
            return ""
        payload = {
            "x_pct": round(float(cx) / float(cw), 4),
            "y_pct": round(float(cy) / float(ch), 4),
        }
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return ""


def relative_coord_to_client_xy(
    spec: Dict[str, Any],
    selector_value: str,
) -> Tuple[int, int]:
    """将 relative_coord JSON 解析为客户区像素坐标。"""
    raw = (selector_value or "").strip()
    if not raw:
        raise ValueError("relative_coord 值为空")
    data = json.loads(raw) if raw.startswith("{") else {}
    if not isinstance(data, dict):
        raise ValueError("relative_coord 格式无效")
    x_pct = float(data.get("x_pct", 0))
    y_pct = float(data.get("y_pct", 0))
    hwnd = int((spec or {}).get("hwnd") or 0)
    if not hwnd:
        raise ValueError("relative_coord 需要 desktop_spec.hwnd")
    import ctypes

    user32 = ctypes.windll.user32
    rect = ctypes.wintypes.RECT()
    if not user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        raise ValueError("无法读取目标窗口客户区")
    cw = int(rect.right) - int(rect.left)
    ch = int(rect.bottom) - int(rect.top)
    if cw < 1 or ch < 1:
        raise ValueError("目标窗口客户区尺寸无效")
    cx = max(0, min(cw - 1, int(round(x_pct * cw))))
    cy = max(0, min(ch - 1, int(round(y_pct * ch))))
    return cx, cy


def uia_path_from_locator_candidates(raw: Any) -> str:
    if not raw:
        return ""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(items, list):
        return ""
    best = ""
    best_score = -1
    for it in items:
        if not isinstance(it, dict):
            continue
        if (it.get("selector_type") or "").strip().lower() != "uia_path":
            continue
        sc = int(it.get("score") or 0)
        if sc > best_score:
            best_score = sc
            best = (it.get("selector_value") or "").strip()
    return best


def visual_template_from_candidates(raw: Any) -> str:
    if not raw:
        return ""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(items, list):
        return ""
    for it in sorted(
        (x for x in items if isinstance(x, dict)),
        key=lambda x: -int(x.get("score") or 0),
    ):
        if (it.get("selector_type") or "").strip().lower() == "visual_template":
            return (it.get("selector_value") or "").strip()
    return ""


def _icon_name_from_context(
    spec: Dict[str, Any],
    description: str = "",
    case_name: str = "",
) -> str:
    import re

    cn = (case_name or spec.get("_case_name") or "").strip()
    if cn:
        if "记事本" in cn or "notepad" in cn.lower():
            return "记事本"
    m = re.search(r"「([^」]+)」", description or "")
    if m:
        n = (m.group(1) or "").strip()
        if n.lower() not in ("folderview", "desktop", "桌面"):
            return n
    tn = (spec.get("target_name") or "").strip()
    if tn.lower() not in ("folderview", "desktop", "桌面", "syslistview32"):
        return tn
    return ""


def is_misbound_overlay_spec(spec: Dict[str, Any]) -> bool:
    s = spec or {}
    if is_desktop_shell_spec(s):
        return False
    proc = (s.get("process") or "").strip().lower()
    if proc in ("applicationframehost.exe",):
        return True
    cls = (s.get("class_name") or "").strip()
    tn = (s.get("target_name") or "").strip().lower()
    return cls == "ApplicationFrameWindow" and tn in (
        "folderview",
        "desktop",
        "桌面",
    )


def format_uia_path_for_display(nodes: Any) -> List[str]:
    """将 UIA 路径节点渲染为竞品风格 XML 行（供捕获弹窗「精准定位」Tab）。"""
    if isinstance(nodes, str):
        try:
            nodes = json.loads(nodes)
        except json.JSONDecodeError:
            return []
    if not isinstance(nodes, list):
        return []
    lines: List[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        tag = (n.get("control_type") or "Control").strip() or "Control"
        attrs: List[str] = []
        cls = (n.get("class_name") or "").strip()
        if cls:
            if "|" in cls:
                attrs.append(f'regex:cls="{cls}"')
            else:
                attrs.append(f'cls="{cls}"')
        name = (n.get("name") or "").strip()
        if name:
            attrs.append(f'uia-name="{name}"')
        aid = (n.get("automation_id") or "").strip()
        if aid:
            attrs.append(f'automation_id="{aid}"')
        if tag == "Window" and not any(a.startswith("app=") for a in attrs):
            attrs.insert(0, 'app="explorer"')
        attr_s = (" " + " ".join(attrs)) if attrs else ""
        lines.append(f"<{tag}{attr_s} />")
    return lines


def enrich_desktop_spec_for_precise_run(
    spec: Optional[Dict[str, Any]],
    locator_candidates: Any = None,
    *,
    description: str = "",
    case_name: str = "",
    selector_type: str = "",
    selector_value: str = "",
) -> Dict[str, Any]:
    """
    运行前规范化 desktop_spec：优先 UIA 精准链 + desktop_shell，忽略误绑的覆盖窗口 hwnd。
    """
    out = dict(spec or {})
    st = (selector_type or "").strip().lower()
    uia_json = uia_path_from_locator_candidates(locator_candidates)
    if not uia_json and isinstance(out.get("uia_path"), list):
        uia_json = json.dumps(out["uia_path"], ensure_ascii=False)
    if not uia_json and st == "uia_path":
        sv = (selector_value or "").strip()
        if sv.startswith("["):
            uia_json = sv
    icon = _icon_name_from_context(out, description, case_name)
    if not uia_json and icon and (
        is_misbound_overlay_spec(out)
        or st == "coordinate"
        or (st == "uia_path" and not out.get("uia_path"))
    ):
        uia_json = json.dumps(
            synthesize_desktop_icon_uia_path(icon),
            ensure_ascii=False,
        )
    should_promote = bool(
        uia_json
        or is_desktop_shell_spec(out)
        or icon
        or is_misbound_overlay_spec(out)
        or (st == "uia_path" and uia_json)
    )
    if uia_json and not is_desktop_shell_spec(out):
        should_promote = True
    if should_promote:
        shell = standard_desktop_shell_spec()
        out.update(shell)
        if uia_json:
            try:
                out["uia_path"] = json.loads(uia_json)
            except json.JSONDecodeError:
                out["uia_path"] = uia_json
        if icon:
            out["target_name"] = icon
        out.pop("hwnd", None)
        out.pop("pid", None)
        out.pop("window_title_re", None)
    elif st == "uia_path" and (out.get("uia_path") or uia_json):
        out.pop("hwnd", None)
        out.pop("pid", None)
    return out
