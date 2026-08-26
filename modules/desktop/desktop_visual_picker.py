# -*- coding: utf-8 -*-
"""
桌面混合捕获录制器（结构 UIA + 视觉模板）。

默认「智能点选」：单击目标 → UIA 取控件边界 → 自动生成视觉模板与 element_snapshot。
「区域框选」：仅作视觉补充（UIA 难命中或需自定义区域时使用）。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_visual_picker 仅支持 Windows")

VISUAL_SELECTOR_TYPE = "visual"
# 捕获 UI 引擎版本：便于确认子进程已加载新代码（非全屏遮罩）
CAPTURE_UI_ENGINE = "border-v11-click-shield"
_HOVER_PEEK_INTERVAL_SEC = 0.45
_HOVER_MOVE_MIN_PX = 12
CAPTURE_MODE_SMART = "smart"
CAPTURE_MODE_REGION = "region"

# 与平台步骤页 / 元素编辑器一致的配色
_THEME = {
    "bg": "#ffffff",
    "surface": "#f5f7fa",
    "border": "#e0e5eb",
    "border_light": "#f0f2f5",
    "text": "#1a1a2e",
    "text_light": "#4a4a68",
    "muted": "#8e99a4",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_dark": "#1e40af",
    "success": "#10b981",
    "success_light": "#d1fae5",
    "warning": "#f59e0b",
    "warning_light": "#fef3c7",
    "danger": "#ef4444",
    "danger_light": "#fee2e2",
    "secondary": "#64748b",
    "accent": "#8b5cf6",
    "accent_light": "#f5f3ff",
    "highlight": "#06b6d4",
    "highlight_fill": "#ecfeff",
    "hover_border": "#2563eb",
    "hover_bg": "#eff6ff",
}


# 与 desktop_uia_core / desktop_uia_snapshot 的伪容器列表对齐
_FAKE_CONTAINER_PATTERNS = (
    "chrome",
    "renderwidget",
    "legacy window",
    "widgetwin",
    "corewindow",
    "webview",
    "directui",
    "cef",
    "electron",
    "chromium",
    "tabwindowclass",
    "chrome_widgetwin",
    "chrome_renderwidgethosthwnd",
    "cefbrowserwindow",
)


def _is_fake_container(name: str, class_name: str = "") -> bool:
    combined = f"{name or ''} {class_name or ''}".lower()
    return any(p in combined for p in _FAKE_CONTAINER_PATTERNS)


def _layered_locate(x: int, y: int, *, allow_ocr: bool = True) -> Dict:
    candidates = []

    # 桌面图标：ListView HitTest 优先（避免整块 FolderView）
    try:
        from modules.desktop.desktop_shell_listview import peek_desktop_icon_at_point

        icon = peek_desktop_icon_at_point(int(x), int(y))
        if icon and icon.screen_rect:
            rw = icon.screen_rect[2] - icon.screen_rect[0]
            rh = icon.screen_rect[3] - icon.screen_rect[1]
            score = 120
            if 24 <= rw <= 200 and 24 <= rh <= 220:
                score += 40
            if icon.icon_name and not _is_shell_noise_label(icon.icon_name):
                score += 30
            candidates.append({
                "rect": icon.screen_rect,
                "label": icon.icon_name or "桌面图标",
                "score": score,
                "source": "shell_listview",
                "control_type": "ListItem",
            })
    except Exception:
        pass

    win32_result = _locate_via_win32(x, y)
    if win32_result and win32_result.get("rect"):
        rw = win32_result["rect"][2] - win32_result["rect"][0]
        rh = win32_result["rect"][3] - win32_result["rect"][1]
        label = win32_result.get("label", "") or ""
        score = 10
        try:
            from modules.desktop.desktop_highlight_win32 import rect_is_near_fullscreen

            near_full = rect_is_near_fullscreen(win32_result["rect"])
        except Exception:
            near_full = rw > 1600 and rh > 900
        if _is_shell_noise_label(label) or near_full:
            score -= 40
        # 越小越像真实控件
        area = max(1, rw * rh)
        if area < 80_000:
            score += 25
        elif area < 250_000:
            score += 10
        if 12 <= rw <= 900 and 10 <= rh <= 700:
            score += 15
        if label and not _is_shell_noise_label(label):
            score += 15
        candidates.append({
            "rect": win32_result["rect"],
            "label": label,
            "score": score,
            "source": "win32",
            "control_type": win32_result.get("control_type") or "Window",
            "class_name": win32_result.get("class_name") or "",
        })

    uia_result = _locate_via_uia(x, y)
    if uia_result and uia_result.get("rect"):
        rw = uia_result["rect"][2] - uia_result["rect"][0]
        rh = uia_result["rect"][3] - uia_result["rect"][1]
        label = uia_result.get("label", "") or ""
        score = 55  # UIA 优先于 Win32 顶层窗
        try:
            from modules.desktop.desktop_highlight_win32 import rect_is_near_fullscreen

            near_full = rect_is_near_fullscreen(uia_result["rect"])
        except Exception:
            near_full = rw > 1600 and rh > 900
        if _is_shell_noise_label(label) or near_full:
            score -= 50
        area = max(1, rw * rh)
        if area < 80_000:
            score += 30
        elif area < 250_000:
            score += 12
        if label and not _is_shell_noise_label(label):
            score += 20
        ct = (uia_result.get("control_type") or "").lower()
        if ct in (
            "listitem",
            "button",
            "edit",
            "menuitem",
            "checkbox",
            "radiobutton",
            "hyperlink",
            "tabitem",
            "combobox",
            "treeitem",
        ):
            score += 35
        elif ct in ("text", "image"):
            score += 15
        candidates.append({
            "rect": uia_result["rect"],
            "label": label,
            "score": score,
            "source": "uia",
            "control_type": uia_result.get("control_type") or "",
            "class_name": uia_result.get("class_name") or "",
        })

    # OCR 很重，悬停禁止；仅在最终点选时允许
    if allow_ocr:
        try:
            from modules.desktop.desktop_ocr_locate import locate_element_via_ocr

            ocr_result = locate_element_via_ocr(x, y)
            if ocr_result and ocr_result.get("rect"):
                rw = ocr_result["rect"][2] - ocr_result["rect"][0]
                rh = ocr_result["rect"][3] - ocr_result["rect"][1]
                score = 25
                if 20 <= rw <= 300 and 15 <= rh <= 120:
                    score += 35
                if ocr_result.get("text"):
                    score += 30
                candidates.append({
                    "rect": ocr_result["rect"],
                    "label": ocr_result.get("text", ""),
                    "score": score,
                    "source": "ocr",
                })
        except Exception:
            pass

    if candidates:
        best = max(candidates, key=lambda c: c["score"])
        rect = best["rect"]
        label = best.get("label") or ""
        try:
            from modules.desktop.desktop_highlight_win32 import rect_is_near_fullscreen

            near_full = rect_is_near_fullscreen(rect)
        except Exception:
            rw = rect[2] - rect[0]
            rh = rect[3] - rect[1]
            near_full = rw > 1600 and rh > 900
        # 仅在近乎整屏 / 壳层噪声时放弃，不再退化成「跟着鼠标的小框」
        if best["score"] < 10 or near_full or _is_shell_noise_label(label) or _is_shell_noise_class(
            str(best.get("class_name") or "")
        ):
            ocr_cands = [c for c in candidates if c["source"] == "ocr" and c["score"] >= 40]
            if ocr_cands:
                best = max(ocr_cands, key=lambda c: c["score"])
            elif best["source"] == "shell_listview" and not near_full:
                pass  # 保留桌面图标
            else:
                return _empty_locate()
        out = {
            "rect": best["rect"],
            "label": best.get("label") or "",
            "source": best["source"],
            "control_type": best.get("control_type") or "",
            "class_name": best.get("class_name") or "",
            "ok": True,
        }
        return out

    return _empty_locate()


def _hover_locate(x: int, y: int) -> Dict:
    """悬停专用：禁止 OCR；禁止光标跟随小框。"""
    hit = _layered_locate(x, y, allow_ocr=False)
    if _is_reliable_element_hit(hit, x, y):
        return hit
    # 应用窗内再加深一次：唤醒无障碍树后重试 UIA（QQ/Electron 常见）
    try:
        from modules.desktop.desktop_win32_snapshot import get_window_class, window_from_point

        hwnd = window_from_point(int(x), int(y))
        cls = get_window_class(hwnd) if hwnd else ""
        if hwnd and not _is_shell_noise_class(cls):
            from modules.desktop.desktop_uia_core import wake_accessibility_around_point

            wake_accessibility_around_point(int(x), int(y), max_children=40)
            uia2 = _locate_via_uia(x, y, timeout_sec=1.2, wake=False)
            if uia2 and uia2.get("rect"):
                cand = {
                    **uia2,
                    "source": "uia",
                    "score": 80,
                    "ok": True,
                }
                if _is_reliable_element_hit(cand, x, y):
                    return cand
    except Exception:
        pass
    return _empty_locate()


def _is_shell_noise_label(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False  # 空名称≠壳噪声（很多按钮无 Name）
    noise = (
        "folderview",
        "syslistview32",
        "desktop",
        "桌面",
        "workerw",
        "progman",
        "shell_traywnd",
        "chrome_renderwidgethosthwnd",
        "legacy window",
        "shelldll_defview",
    )
    return any(k == n or k in n for k in noise)


def _is_shell_noise_class(class_name: str) -> bool:
    c = (class_name or "").strip().lower()
    if not c:
        return False
    return any(
        k in c
        for k in (
            "progman",
            "workerw",
            "shelldll_defview",
            "syslistview32",
            "shell_traywnd",
            "traynotifywnd",
        )
    )


def _rect_area(rect: Tuple[int, int, int, int]) -> int:
    return max(0, int(rect[2]) - int(rect[0])) * max(0, int(rect[3]) - int(rect[1]))


def _is_reliable_element_hit(result: Dict, x: int, y: int) -> bool:
    """
    是否为「真实控件命中」——仅此时才画悬停绿框。
    禁止：fallback 光标小框、整屏桌面壳、不包含鼠标的框。
    """
    if not result:
        return False
    source = (result.get("source") or "").strip().lower()
    if source in ("", "fallback", "none"):
        return False
    rect = result.get("rect")
    if not rect or len(rect) != 4:
        return False
    if not _rect_contains(rect, int(x), int(y)):
        return False
    try:
        from modules.desktop.desktop_highlight_win32 import rect_is_near_fullscreen

        if rect_is_near_fullscreen(rect):
            return False
    except Exception:
        if _rect_area(rect) > 1920 * 1080 * 0.7:
            return False
    label = (result.get("label") or "").strip()
    cls = (result.get("class_name") or "").strip()
    if _is_shell_noise_label(label) or _is_shell_noise_class(cls):
        return False
    # Win32 顶层大窗不够「元素级」：面积过大且无交互类型则拒绝
    if source == "win32":
        area = _rect_area(rect)
        if area > 220_000 and (result.get("control_type") or "").lower() in ("", "window", "pane"):
            return False
    return True


def _empty_locate() -> Dict:
    return {
        "rect": None,
        "label": "",
        "source": "none",
        "control_type": "",
        "class_name": "",
        "ok": False,
    }


def _locate_via_win32(x: int, y: int) -> Dict:
    try:
        from modules.desktop.desktop_win32_snapshot import (
            deepest_child_at_point,
            get_window_class,
            get_window_rect,
            get_window_text,
            window_from_point,
        )

        hwnd = deepest_child_at_point(x, y) or window_from_point(x, y)
        if not hwnd:
            return {}

        cls = get_window_class(hwnd)
        if _is_shell_noise_class(cls):
            return {}

        rect = get_window_rect(hwnd)
        if not rect:
            return {}
        try:
            from modules.desktop.desktop_highlight_win32 import rect_is_near_fullscreen

            if rect_is_near_fullscreen(rect):
                return {}
        except Exception:
            pass

        text = get_window_text(hwnd)
        return {
            "rect": rect,
            "label": text or "",
            "control_type": "Window",
            "class_name": cls or "",
            "source": "win32",
        }
    except Exception:
        return {}


def _locate_via_uia(
    x: int, y: int, *, timeout_sec: float = 0.95, wake: bool = True
) -> Dict:
    """
    悬停/点选 UIA 入口：必须走 peek 的 COM 线程池，禁止在任意线程直接调 UIA。
    """
    try:
        from modules.desktop.desktop_uia_snapshot import peek_element_at_point

        res = peek_element_at_point(
            x, y, timeout_sec=float(timeout_sec), wake_app=bool(wake)
        )
        if res.ok and res.bounding_rect:
            return {
                "rect": res.bounding_rect,
                "label": res.element_label or "",
                "control_type": res.control_type or "",
                "class_name": getattr(res, "class_name", "") or "",
            }
    except TypeError:
        # 旧签名兼容
        try:
            from modules.desktop.desktop_uia_snapshot import peek_element_at_point

            res = peek_element_at_point(x, y, timeout_sec=float(timeout_sec))
            if res.ok and res.bounding_rect:
                return {
                    "rect": res.bounding_rect,
                    "label": res.element_label or "",
                    "control_type": res.control_type or "",
                    "class_name": "",
                }
        except Exception:
            pass
    except Exception:
        pass

    return {}


def _cursor_pos() -> Tuple[int, int]:
    import ctypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _escape_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)


def _lbutton_down() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _f2_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x71) & 0x8000)


def _f3_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x72) & 0x8000)


def _rect_contains(rect: Tuple[int, int, int, int], x: int, y: int) -> bool:
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def _get_toolstrip_bounds(tk_root: Any) -> Optional[Tuple[int, int, int, int]]:
    try:
        rx = tk_root.winfo_rootx()
        ry = tk_root.winfo_rooty()
        rw = tk_root.winfo_width()
        rh = tk_root.winfo_height()
        if rw > 0 and rh > 0:
            return (rx, ry, rx + rw, ry + rh)
    except Exception:
        pass
    return None


def _is_over_toolstrip(tk_root: Any, x: int, y: int) -> bool:
    b = _get_toolstrip_bounds(tk_root)
    return b is not None and _rect_contains(b, x, y)


def _patch_snapshot_text(
    snapshot: Optional[Dict[str, Any]], text: str
) -> Optional[Dict[str, Any]]:
    """让 element_snapshot 关键属性与展示名同源，避免详情页被旧 UIA 名覆盖。"""
    if not isinstance(snapshot, dict) or not (text or "").strip():
        return snapshot
    import copy

    out = copy.deepcopy(snapshot)
    sel = out.get("selector") if isinstance(out.get("selector"), dict) else {}
    sel = dict(sel or {})
    kc = list(sel.get("key_candidates") or [])
    patched = False
    for row in kc:
        if not isinstance(row, dict):
            continue
        prop = str(row.get("property") or "").lower()
        if prop in ("uia-name", "name", "ocr-text", "text", "legacy-text"):
            row["value"] = text
            patched = True
    if not patched:
        kc.insert(0, {"property": "uia-name", "value": text, "match": "equals"})
    sel["key_candidates"] = kc
    out["selector"] = sel
    return out


def _canonical_element_label(
    *,
    element_label: str,
    control_type_label: str,
    act: str,
    cx: int,
    cy: int,
) -> Tuple[str, str]:
    """返回 (display_label, plain_text)。"""
    plain = (element_label or "").strip()
    ct = (control_type_label or "").lower()
    if plain:
        if "list" in ct:
            display = f"ListItem_{plain}"
        elif control_type_label and control_type_label != "Control":
            display = f"{control_type_label}_{plain}"
        else:
            display = plain
    else:
        display = f"未命名元素_{act}@{cx},{cy}"
        plain = display
    return display, plain


def build_pick_from_smart_click(
    click_x: int,
    click_y: int,
    *,
    action: str = "click",
    match_threshold: float = 0.72,
    frozen_preview_b64: str = "",
    frozen_rect: Optional[Tuple[int, int, int, int]] = None,
    frozen_label: str = "",
) -> Dict[str, Any]:
    from modules.desktop.desktop_uia_snapshot import (
        capture_element_snapshot_at_point,
        rect_with_padding,
    )
    from modules.desktop.desktop_visual_engine import build_visual_step_payload

    act = (action or "click").strip().lower()
    # 若点击瞬间已冻结预览/矩形，优先采用，避免 UI 已切换后重截不一致
    frozen_preview_b64 = (frozen_preview_b64 or "").strip()
    if frozen_rect and len(frozen_rect) == 4:
        fl, ft, fr, fb = (
            int(frozen_rect[0]),
            int(frozen_rect[1]),
            int(frozen_rect[2]),
            int(frozen_rect[3]),
        )
        if fr > fl and fb > ft:
            # 后面几何计算会再覆盖；此处先占位
            pass
    snap = capture_element_snapshot_at_point(click_x, click_y, timeout_sec=2.5)
    snap_cls = ""
    if isinstance(snap.element_snapshot, dict):
        snap_cls = str(snap.element_snapshot.get("class_name") or "")
    is_fake = (
        (snap.error_code or "") == "fake_container"
        or _is_fake_container(snap.element_label or "", snap_cls)
    )

    cx, cy = click_x, click_y
    if snap.screen_center and not is_fake:
        cx, cy = snap.screen_center

    if is_fake:
        # 对已打开窗口：先唤醒无障碍树再捕一次（打开应用=点击，无需特殊启动）
        try:
            from modules.desktop.desktop_uia_core import wake_accessibility_around_point
            from modules.desktop.desktop_uia_snapshot import capture_element_snapshot_at_point as _recap

            wake_accessibility_around_point(click_x, click_y)
            again = _recap(click_x, click_y, timeout_sec=2.0)
            again_cls = ""
            if isinstance(again.element_snapshot, dict):
                again_cls = str(again.element_snapshot.get("class_name") or "")
            still_fake = (
                (again.error_code or "") == "fake_container"
                or _is_fake_container(again.element_label or "", again_cls)
            )
            if again.ok and not still_fake:
                snap = again
                is_fake = False
                if snap.screen_center:
                    cx, cy = snap.screen_center
        except Exception:
            pass

    if is_fake:
        snap = snap._replace(
            element_label="",
            control_type="",
            ok=False,
            error_code="fake_container",
            message="渲染层控件，改用视觉/OCR 捕获内部元素",
        )
        # 若进程碰巧开着调试口则用 DOM；否则走视觉——应用已打开即可，不必重启
        try:
            from modules.desktop.desktop_embed_cdp import capture_embed_element_at_point

            embed = capture_embed_element_at_point(click_x, click_y)
            if embed and embed.get("ok") and embed.get("element_snapshot"):
                rect = embed.get("bounding_rect")
                center = embed.get("screen_center") or (click_x, click_y)
                snap = snap._replace(
                    ok=True,
                    error_code="",
                    message=embed.get("message") or "embed_cdp",
                    element_snapshot=embed.get("element_snapshot"),
                    screen_center=tuple(center) if center else (click_x, click_y),
                    bounding_rect=tuple(rect) if rect and len(rect) == 4 else None,
                    element_label=embed.get("element_label") or "",
                    control_type=embed.get("control_type") or "Element",
                    window_title=embed.get("window_title") or snap.window_title,
                    process_name=embed.get("process_name") or snap.process_name,
                )
                is_fake = False
                cx, cy = int(center[0]), int(center[1])
        except Exception:
            pass

    layered_result = _layered_locate(click_x, click_y)
    layered_rect = layered_result.get('rect')
    layered_label = layered_result.get('label', '')
    layered_source = layered_result.get('source', '')

    pad = 48
    # 结构化捕获成功时优先用控件边界，避免 layered OCR/Win32 覆盖成错误宿主
    prefer_snap_geom = bool(
        snap.ok and snap.bounding_rect and len(snap.bounding_rect) == 4 and not is_fake
    )
    if prefer_snap_geom:
        l, t, r, b = snap.bounding_rect
        bg_w = r - l
        bg_h = b - t
        if bg_w > 400 or bg_h > 300:
            # 过大控件仍用点击附近小框做视觉模板
            l, t, r, b = click_x - 64, click_y - 64, click_x + 64, click_y + 64
            cx, cy = click_x, click_y
        else:
            cx, cy = (l + r) // 2, (t + b) // 2
            if snap.screen_center:
                cx, cy = snap.screen_center
    elif layered_rect:
        l, t, r, b = layered_rect
        bg_w = r - l
        bg_h = b - t
        import tkinter as tk
        try:
            root_tmp = tk.Tk()
            screen_w = root_tmp.winfo_screenwidth()
            screen_h = root_tmp.winfo_screenheight()
            root_tmp.destroy()
        except Exception:
            screen_w = 1920
            screen_h = 1080

        if bg_w > screen_w * 0.85 or bg_h > screen_h * 0.85:
            l, t, r, b = click_x - pad, click_y - pad, click_x + pad, click_y + pad
        elif bg_w > 150 or bg_h > 150:
            l, t, r, b = click_x - 64, click_y - 64, click_x + 64, click_y + 64
        cx, cy = (l + r) // 2, (t + b) // 2
    else:
        cx, cy = click_x, click_y
        l, t, r, b = cx - pad, cy - pad, cx + pad, cy + pad

    payload = build_visual_step_payload(
        l, t, r, b, cx, cy, match_threshold=match_threshold
    )
    if snap.element_snapshot:
        payload = payload.merge_element_snapshot(snap.element_snapshot)

    if snap.ok and (snap.element_label or "").strip() and not is_fake:
        element_label = (snap.element_label or "").strip()
    elif layered_label and layered_source in ("uia", "win32", "ocr"):
        element_label = layered_label
    else:
        element_label = layered_label or snap.element_label or ""

    sel = (snap.element_snapshot or {}).get("selector") or {}
    resolved_via = sel.get("resolved_via") or ""
    resolved_method_force = ""
    ocr_hit = None
    # 应用已打开时：假容器优先用点击附近 OCR 框成可回放视觉步骤（正式路径）
    need_ocr = (
        is_fake
        or not element_label
        or resolved_via == "uia_window"
    )
    if not need_ocr and snap.bounding_rect and not prefer_snap_geom:
        rect_w = snap.bounding_rect[2] - snap.bounding_rect[0]
        rect_h = snap.bounding_rect[3] - snap.bounding_rect[1]
        if rect_w > 300 or rect_h > 300:
            need_ocr = True
    # 已有点击瞬间冻结图时，禁止再走 OCR 重截（否则易与冻结名/图打架）
    if frozen_preview_b64:
        need_ocr = False
    if need_ocr:
        try:
            from modules.desktop.desktop_ocr import extract_primary_text
            from modules.desktop.desktop_ocr_locate import locate_element_via_ocr
            from modules.desktop.desktop_precise_locator import capture_rect_preview_b64

            ocr_hit = locate_element_via_ocr(click_x, click_y, search_radius=160)
            if ocr_hit and ocr_hit.get("rect"):
                ol, ot, oright, ob = ocr_hit["rect"]
                ow, oh = oright - ol, ob - ot
                if 8 <= ow <= 600 and 8 <= oh <= 200:
                    l, t, r, b = ol, ot, oright, ob
                    cx, cy = (l + r) // 2, (t + b) // 2
                    if ocr_hit.get("text"):
                        element_label = str(ocr_hit["text"]).strip() or element_label
                    payload = build_visual_step_payload(
                        l, t, r, b, click_x, click_y, match_threshold=match_threshold
                    )
                    if snap.element_snapshot:
                        payload = payload.merge_element_snapshot(snap.element_snapshot)

            preview = capture_rect_preview_b64(l, t, r, b, padding=4)
            if preview:
                ocr_text = element_label or extract_primary_text(preview)
                if ocr_text:
                    element_label = ocr_text
                    ocr_snapshot = {
                        "selector": {
                            "anchor_props": "Button",
                            "key_candidates": [
                                {"property": "ocr-text", "value": ocr_text, "match": "equals"}
                            ],
                            "parent_chain": [],
                            "resolved_via": "ocr",
                        }
                    }
                    payload = payload.merge_element_snapshot(ocr_snapshot)
                    if is_fake or not snap.ok:
                        snap = snap._replace(
                            ok=True,
                            error_code="",
                            message="视觉/OCR 捕获应用内部元素",
                            element_label=ocr_text,
                            control_type=(ocr_hit or {}).get("control_type") or "Control",
                            element_snapshot=ocr_snapshot,
                            bounding_rect=(l, t, r, b),
                            screen_center=(cx, cy),
                        )
                        is_fake = False
                        resolved_method_force = "ocr"
        except ImportError:
            pass
        except Exception:
            pass

    window_rect = None
    try:
        from modules.desktop.desktop_win32_snapshot import get_parent_window_rect

        window_rect = get_parent_window_rect(click_x, click_y)
        if window_rect and element_label and not snap.ok:
            l2, t2, r2, b2 = window_rect
            if snap.element_snapshot:
                sel = snap.element_snapshot.get("selector") or {}
                sel["window_bounds"] = window_rect
                snap.element_snapshot["selector"] = sel
    except ImportError:
        pass

    app_window_title = snap.window_title or ""
    app_process_name = snap.process_name or ""
    if not app_window_title:
        try:
            from modules.desktop.desktop_win32_snapshot import (
                get_top_level_window,
                get_window_text,
                get_process_name_from_hwnd,
                window_from_point,
            )
            hwnd = window_from_point(click_x, click_y)
            if hwnd:
                top = get_top_level_window(hwnd)
                app_window_title = get_window_text(top) or ""
                app_process_name = get_process_name_from_hwnd(hwnd) or ""
        except ImportError:
            pass

    resolved_method = "uia" if snap.ok else (
        "win32" if snap.element_label and not snap.ok else "visual"
    )
    if resolved_method_force:
        resolved_method = resolved_method_force
    ct = (snap.control_type or "").lower()
    if "list" in ct and element_label:
        resolved_method = snap.error_code or resolved_method
    elif snap.ok and snap.element_snapshot:
        resolved_method = (
            snap.element_snapshot.get("selector") or {}
        ).get("resolved_via") or resolved_method

    control_type_label = snap.control_type or "Control"
    if not snap.ok:
        control_type_label = "Control"

    preview_b64 = (frozen_preview_b64 or "").strip()
    # 冻结矩形：名称/几何一律对齐绿框所见，避免事后结构矩形把模板裁偏
    if frozen_rect and len(frozen_rect) == 4:
        fl, ft, fr, fb = [int(v) for v in frozen_rect]
        if fr - fl >= 4 and fb - ft >= 4:
            l, t, r, b = fl, ft, fr, fb
            cx, cy = (l + r) // 2, (t + b) // 2
        if (frozen_label or "").strip():
            element_label = frozen_label.strip()

    if preview_b64:
        payload = build_visual_step_payload(
            l,
            t,
            r,
            b,
            click_x,
            click_y,
            match_threshold=match_threshold,
            template_image_b64=preview_b64,
            preserve_full_template=True,
        )
        preview_b64 = payload.template_image_base64
    else:
        try:
            from modules.desktop.desktop_precise_locator import capture_rect_preview_b64

            preview_b64 = capture_rect_preview_b64(l, t, r, b, padding=0) or ""
        except Exception:
            preview_b64 = ""
        if preview_b64:
            payload = build_visual_step_payload(
                l,
                t,
                r,
                b,
                click_x,
                click_y,
                match_threshold=match_threshold,
                template_image_b64=preview_b64,
                preserve_full_template=True,
            )
            preview_b64 = payload.template_image_base64
        else:
            payload = build_visual_step_payload(
                l, t, r, b, click_x, click_y, match_threshold=match_threshold
            )
            preview_b64 = payload.template_image_base64

    display_label, plain_text = _canonical_element_label(
        element_label=element_label,
        control_type_label=control_type_label,
        act=act,
        cx=cx,
        cy=cy,
    )
    structure_info = {
        "app_window_title": app_window_title,
        "app_process_name": app_process_name,
        "element_text": plain_text,
        "element_type": control_type_label,
        "resolved_method": resolved_method,
    }

    snap_out = snap.element_snapshot
    if plain_text:
        snap_out = _patch_snapshot_text(snap_out, plain_text)
    if snap_out:
        payload = payload.merge_element_snapshot(snap_out)

    pick: Dict[str, Any] = {
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": payload.to_json(),
        "pick_point": {"x": cx, "y": cy},
        "preview_image_b64": preview_b64,
        "label": display_label,
        "name": plain_text,
        "capture_mode": CAPTURE_MODE_SMART,
        "rectangle": {"left": l, "top": t, "right": r, "bottom": b},
        "structure_info": structure_info,
        "preview_frozen": bool(frozen_preview_b64),
    }
    if snap_out:
        pick["element_snapshot"] = snap_out
    if not snap.ok:
        hint = snap.message or snap.error_code or "结构信息不可用"
        if plain_text:
            hint = f"已用视觉/OCR 捕获「{plain_text}」，可直接用于回放"
        else:
            hint = "已按点击位置生成视觉模板，可直接用于回放（打开应用即点即捕，无需特殊启动）"
        pick["uia_hint"] = hint
    elif resolved_method in ("ocr", "visual", "embed_cdp"):
        pick["uia_hint"] = f"已捕获应用内部元素（{resolved_method}）"
    if window_rect:
        pick["window_rect"] = {
            "left": window_rect[0],
            "top": window_rect[1],
            "right": window_rect[2],
            "bottom": window_rect[3],
        }
    return pick


class VisualRegionPickerOverlay:
    """悬浮工具条 + 智能点选 / 区域框选（与平台混合定位一致）。"""

    def __init__(
        self,
        *,
        on_record: Callable[[Dict[str, Any]], None],
        on_message: Callable[[str], None],
        on_error: Callable[[str], None],
        on_close: Callable[[], None],
        on_armed_change: Optional[Callable[[bool], None]] = None,
        default_action: str = "click",
    ):
        self._on_record = on_record
        self._on_message = on_message
        self._on_error = on_error
        self._on_close = on_close
        self._on_armed_change = on_armed_change
        self._default_action = (default_action or "click").strip().lower()
        self._root = None
        # 无全屏 overlay；仅 Win32 非分层细绿框
        self._overlay = None
        self._canvas = None
        self._hl_hover = None
        self._hl_region = None
        self._capture_badge = None
        self._action_var = None
        self._status_var = None
        self._mode_var = None
        self._capture_mode = CAPTURE_MODE_SMART
        self._armed = False
        self._drag_start: Optional[Tuple[int, int]] = None
        self._drag_rect: Optional[Tuple[int, int, int, int]] = None
        self._phase = "free"
        self._pending_drag = False
        self._prev_lbutton = False
        self._prev_f2 = False
        self._prev_f3 = False
        self._prev_escape = False
        self._stop = False
        self._sw = 1920
        self._sh = 1080
        self._hover_busy = False
        self._last_hover_ts = 0.0
        self._hover_label = ""
        self._hover_rect: Optional[Tuple[int, int, int, int]] = None
        self._hover_rect_id: Optional[int] = None
        self._last_hover_xy: Optional[Tuple[int, int]] = None
        self._consume_click_guard = False
        self._pick_enabled_after = 0.0
        self._arm_btn = None
        self._state_lbl = None

        self._locked_pick: Optional[Dict[str, Any]] = None
        self._locked_rect: Optional[Tuple[int, int, int, int]] = None
        self._element_panel: Optional[ElementInfoPanel] = None
        self._click_guard = None
        self._frozen_preview_b64 = ""
        self._frozen_rect: Optional[Tuple[int, int, int, int]] = None
        self._frozen_label = ""
        self._hook_capture_clicks = True  # SetCapture 截获，非全局钩子

    def run(self) -> None:
        import tkinter as tk

        self._root = tk.Tk()
        self._root.title("桌面元素捕获")
        self._root.resizable(False, False)
        self._root.configure(bg=_THEME["bg"])
        try:
            self._root.attributes("-topmost", True)
        except Exception:
            pass

        self._sw = int(self._root.winfo_screenwidth())
        self._sh = int(self._root.winfo_screenheight())
        self._root.geometry("+20+20")

        outer = tk.Frame(self._root, bg=_THEME["bg"], padx=14, pady=12)
        outer.pack(fill="both", expand=True)

        hdr = tk.Frame(outer, bg=_THEME["bg"])
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="桌面元素捕获",
            fg=_THEME["text"],
            bg=_THEME["bg"],
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            hdr,
            text="结构 + 视觉",
            fg=_THEME["primary"],
            bg="#fef2f2",
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            outer,
            text="推荐智能点选：与 UIA 结构定位同源，自动生成模板与结构信息",
            fg=_THEME["text_light"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(6, 10))

        mode_row = tk.Frame(outer, bg=_THEME["bg"], highlightbackground=_THEME["border"], highlightthickness=1)
        mode_row.pack(fill="x", pady=(0, 10))
        inner_mode = tk.Frame(mode_row, bg=_THEME["bg"], padx=10, pady=10)
        inner_mode.pack(fill="x")

        mode_lbl = tk.Label(
            inner_mode,
            text="捕获模式",
            fg=_THEME["text_light"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9, "bold"),
        )
        mode_lbl.pack(anchor="w", pady=(0, 6))

        self._mode_var = tk.StringVar(value=CAPTURE_MODE_SMART)
        for text, val in (
            ("智能点选（推荐）", CAPTURE_MODE_SMART),
            ("区域框选（仅视觉）", CAPTURE_MODE_REGION),
        ):
            rb = tk.Radiobutton(
                inner_mode,
                text=text,
                variable=self._mode_var,
                value=val,
                command=self._on_mode_change,
                bg=_THEME["bg"],
                fg=_THEME["text"],
                activebackground=_THEME["hover_bg"],
                selectcolor=_THEME["bg"],
                font=("Segoe UI", 9),
            )
            rb.pack(anchor="w", pady=2)

        state_row = tk.Frame(outer, bg=_THEME["bg"])
        state_row.pack(fill="x", pady=(0, 8))
        self._state_lbl = tk.Label(
            state_row,
            text="● 待命（未开始捕获）",
            fg=_THEME["text"],
            bg=_THEME["surface"],
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=5,
            relief="solid",
            borderwidth=1,
        )
        self._state_lbl.pack(anchor="w", fill="x")

        self._status_var = tk.StringVar(
            value="默认待命：点「开始捕获」或 F2 后，再点目标元素才会录制"
        )
        tk.Label(
            outer,
            textvariable=self._status_var,
            fg=_THEME["text_light"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        act_row = tk.Frame(outer, bg=_THEME["bg"])
        act_row.pack(fill="x", pady=(0, 12))
        act_lbl = tk.Label(
            act_row,
            text="动作",
            fg=_THEME["text_light"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9, "bold"),
        )
        act_lbl.pack(side="left", padx=(0, 10))
        self._action_var = tk.StringVar(value=self._default_action)
        action_menu = tk.OptionMenu(
            act_row,
            self._action_var,
            "click",
            "double_click",
            "right_click",
            "input",
        )
        action_menu.config(
            bg="white",
            fg=_THEME["text"],
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            padx=8,
            pady=4,
            font=("Segoe UI", 9),
        )
        action_menu.pack(side="left")

        btn_row = tk.Frame(outer, bg=_THEME["bg"])
        btn_row.pack(fill="x")
        self._arm_btn = tk.Button(
            btn_row,
            text="▶ 开始捕获 (F2)",
            command=self._toggle_arm,
            bg=_THEME["primary"],
            fg="white",
            activebackground=_THEME["primary_hover"],
            activeforeground="white",
            relief="flat",
            padx=14,
            pady=7,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )
        self._arm_btn.pack(side="left", padx=(0, 8))
        tk.Button(
            btn_row,
            text="■ 停止捕获",
            command=self._disarm_pick,
            bg=_THEME["surface"],
            fg=_THEME["text"],
            activebackground=_THEME["hover_bg"],
            activeforeground=_THEME["text"],
            relief="flat",
            borderwidth=1,
            highlightbackground=_THEME["border"],
            padx=12,
            pady=7,
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            btn_row,
            text="结束 (ESC)",
            command=self._request_close,
            bg=_THEME["danger"],
            fg="white",
            activebackground="#dc2626",
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=7,
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(side="left")

        hint_frame = tk.Frame(outer, bg=_THEME["bg"])
        hint_frame.pack(anchor="w", pady=(12, 0), fill="x")
        tk.Label(
            hint_frame,
            text="⌨️ 快捷键",
            fg=_THEME["text_light"],
            bg=_THEME["bg"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            hint_frame,
            text="F2 切换捕获 · ESC 结束会话 · 智能点选=单击元素",
            fg=_THEME["muted"],
            bg=_THEME["bg"],
            font=("Segoe UI", 8),
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        self._build_overlay()
        self._init_click_guard()
        self._on_message(
            f"捕获器已就绪 [{CAPTURE_UI_ENGINE}]：点「开始捕获」后指向目标，单击或按 F3"
            "（透明挡板拦截点击，预览与名称同源）"
        )
        self._refresh_arm_ui()
        self._root.bind("<Escape>", lambda _e: self._request_close())
        self._poll_input()
        self._root.mainloop()
        self._teardown_click_guard()

    def _on_mode_change(self) -> None:
        self._capture_mode = (self._mode_var.get() if self._mode_var else CAPTURE_MODE_SMART)
        if self._armed:
            self._disarm_pick()
        if self._capture_mode == CAPTURE_MODE_SMART:
            self._set_status("已选智能点选：开始捕获后在目标上单击一次")
        else:
            self._set_status("已选区域框选：开始捕获后拖拽框选（仅视觉补充）")

    def _build_overlay(self) -> None:
        """初始化非分层绿框高亮（不创建任何全屏/Layered 窗口）。"""
        try:
            from modules.desktop.desktop_highlight_win32 import Win32HighlightBorder

            green = (34, 197, 94)
            self._hl_hover = Win32HighlightBorder(thickness=3, color_rgb=green)
            self._hl_region = Win32HighlightBorder(thickness=3, color_rgb=green)
            self._capture_badge = None
        except Exception as exc:
            self._hl_hover = None
            self._hl_region = None
            self._capture_badge = None
            self._on_error(f"高亮层初始化失败（捕获仍可用）：{exc}")

    def _destroy_highlights(self) -> None:
        for obj in (self._hl_hover, self._hl_region):
            if obj is None:
                continue
            try:
                obj.destroy()
            except Exception:
                pass
        self._hl_hover = None
        self._hl_region = None
        self._capture_badge = None

    def _show_capture_overlay(self) -> None:
        # 故意不创建任何屏幕覆盖层；状态只显示在工具条
        return

    def _hide_capture_overlay(self) -> None:
        self._hide_hover_borders()
        if self._hl_region:
            try:
                self._hl_region.hide()
            except Exception:
                pass

    def _show_overlay(self) -> None:
        return

    def _hide_overlay(self) -> None:
        if self._hl_region:
            try:
                self._hl_region.hide()
            except Exception:
                pass
        self._hide_hover_borders()

    def _hide_hover_borders(self) -> None:
        if self._hl_hover:
            try:
                self._hl_hover.hide()
            except Exception:
                pass
        self._hover_rect_id = None
        self._hover_rect = None

    def _clear_hover_highlight(self) -> None:
        self._hide_hover_borders()
        if self._phase == "drag" or self._phase == "click_offset":
            return

    def _show_hover_borders(self, rect: Tuple[int, int, int, int]) -> None:
        if self._hover_rect == rect:
            return
        try:
            from modules.desktop.desktop_highlight_win32 import rect_is_near_fullscreen

            if rect_is_near_fullscreen(rect):
                self._hide_hover_borders()
                return
        except Exception:
            pass
        self._hover_rect = rect
        if self._hl_region:
            try:
                self._hl_region.hide()
            except Exception:
                pass
        if self._hl_hover:
            try:
                self._hl_hover.show(rect)
            except Exception:
                pass

    def _set_status(self, msg: str) -> None:
        if self._status_var:
            self._status_var.set(msg)
        self._on_message(msg)

    def _refresh_arm_ui(self) -> None:
        if self._state_lbl:
            if self._armed:
                self._state_lbl.configure(
                    text="● 捕获中（挡板拦截 · 单击/F3）",
                    fg=_THEME["highlight"],
                    bg="#dcfce7",
                )
            else:
                self._state_lbl.configure(
                    text="● 待命（点击不会录制）",
                    fg=_THEME["muted"],
                    bg="#f1f5f9",
                )
        if self._arm_btn:
            if self._armed:
                self._arm_btn.configure(
                    text="■ 停止捕获 (F2)",
                    bg=_THEME["secondary"],
                )
            else:
                self._arm_btn.configure(
                    text="▶ 开始捕获 (F2)",
                    bg=_THEME["primary"],
                )

    def _notify_armed_change(self) -> None:
        if self._on_armed_change:
            try:
                self._on_armed_change(bool(self._armed))
            except Exception:
                pass

    def _pause_after_record(self) -> None:
        """单次捕获后自动回到待命，避免连点误录。"""
        self._armed = False
        self._set_click_guard_enabled(False)
        self._consume_click_guard = False
        self._phase = "free"
        self._pending_drag = False
        self._drag_start = None
        self._drag_rect = None
        self._clear_frozen_capture()
        self._hide_capture_overlay()
        self._hide_overlay()
        self._hide_hover_borders()
        self._last_hover_xy = None
        self._set_status("已录制；已暂停捕获。需要继续请再点「开始捕获」或 F2")
        self._refresh_arm_ui()
        self._notify_armed_change()

    def _begin_click_guard(self) -> None:
        """忽略「开始捕获」同一次按键，避免松开时误录。"""
        self._consume_click_guard = True
        self._pending_drag = False
        self._drag_start = None
        self._prev_lbutton = _lbutton_down()
        self._pick_enabled_after = time.time() + 0.2

    def _is_over_picker_ui(self, x: int, y: int) -> bool:
        if _is_over_toolstrip(self._root, x, y):
            return True
        panel = self._element_panel
        if panel is not None:
            try:
                win = getattr(panel, "_window", None) or getattr(panel, "_root", None)
                if win is not None:
                    rx, ry = int(win.winfo_rootx()), int(win.winfo_rooty())
                    rw, rh = int(win.winfo_width()), int(win.winfo_height())
                    if rw > 0 and rh > 0 and _rect_contains((rx, ry, rx + rw, ry + rh), x, y):
                        return True
            except Exception:
                pass
        return False

    def _picker_hwnd(self) -> int:
        if not self._root:
            return 0
        try:
            return int(self._root.winfo_id())
        except Exception:
            return 0

    def _collect_exclude_rects(self) -> list:
        rects = []
        tsb = _get_toolstrip_bounds(self._root)
        if tsb:
            rects.append(tsb)
        return rects

    def _init_click_guard(self) -> None:
        try:
            from modules.desktop.desktop_capture_click_shield import CaptureClickShield

            self._click_guard = CaptureClickShield()
            self._hook_capture_clicks = True
        except Exception as exc:
            self._click_guard = None
            self._hook_capture_clicks = False
            self._on_error(f"点击挡板不可用（请用 F3 捕获）：{exc}")

    def _teardown_click_guard(self) -> None:
        if self._click_guard is not None:
            try:
                self._click_guard.uninstall()
            except Exception:
                pass
            self._click_guard = None

    def _set_click_guard_enabled(self, enabled: bool) -> None:
        if self._click_guard is None:
            return
        try:
            if enabled:
                self._click_guard.set_exclude_rects(self._collect_exclude_rects())
                self._click_guard.show()
            else:
                self._click_guard.hide()
            self._hook_capture_clicks = True
        except Exception as exc:
            self._hook_capture_clicks = False
            try:
                self._click_guard.hide()
            except Exception:
                pass
            self._on_error(f"点击挡板切换失败（请用 F3 捕获）：{exc}")

    def _refresh_shield_exclude(self) -> None:
        if self._click_guard is None or not getattr(self._click_guard, "enabled", False):
            return
        try:
            self._click_guard.set_exclude_rects(self._collect_exclude_rects())
        except Exception:
            pass

    def _clear_frozen_capture(self) -> None:
        self._frozen_preview_b64 = ""
        self._frozen_rect = None
        self._frozen_label = ""

    def _freeze_capture_at_point(self, x: int, y: int) -> None:
        """点击瞬间同步冻结：名称/矩形/预览图（与绿框一致，不裁切）。"""
        self._clear_frozen_capture()
        rect = self._hover_rect
        label = (self._hover_label or "").strip()
        # 挡板可能挡住 UIA：冻结时短暂打穿
        shield = self._click_guard
        if shield is not None and getattr(shield, "enabled", False):
            try:
                shield.set_click_through(True)
            except Exception:
                pass
        try:
            if not rect or not _rect_contains(rect, int(x), int(y)):
                try:
                    hit = _hover_locate(int(x), int(y))
                    if _is_reliable_element_hit(hit, int(x), int(y)):
                        rect = hit.get("rect")
                        label = (hit.get("label") or "").strip() or label
                except Exception:
                    pass
        finally:
            if shield is not None and getattr(shield, "enabled", False):
                try:
                    shield.set_click_through(False)
                except Exception:
                    pass
        if not rect or len(rect) != 4:
            rect = (int(x) - 40, int(y) - 24, int(x) + 40, int(y) + 24)
        self._frozen_rect = tuple(int(v) for v in rect)  # type: ignore[assignment]
        self._frozen_label = label
        try:
            from modules.desktop.desktop_precise_locator import capture_rect_preview_b64

            # padding=0：与高亮绿框内容对齐
            self._frozen_preview_b64 = capture_rect_preview_b64(
                int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]), padding=0
            ) or ""
        except Exception:
            self._frozen_preview_b64 = ""

    def _arm_pick(self) -> None:
        self._capture_mode = (
            self._mode_var.get() if self._mode_var else CAPTURE_MODE_SMART
        )
        self._armed = True
        self._phase = "free"
        self._drag_start = None
        self._drag_rect = None
        self._clear_frozen_capture()
        self._begin_click_guard()
        self._hide_hover_borders()
        self._last_hover_xy = None
        self._set_click_guard_enabled(self._capture_mode == CAPTURE_MODE_SMART)
        if self._capture_mode == CAPTURE_MODE_SMART:
            self._show_capture_overlay()
            self._set_status(
                f"捕获中 [{CAPTURE_UI_ENGINE}]：指向目标后单击或按 F3（挡板拦截，不触发应用）"
            )
        else:
            self._hide_overlay()
            self._set_status("捕获中：拖拽框选区域（区域模式不启用挡板）")

        self._refresh_arm_ui()
        self._notify_armed_change()

    def _disarm_pick(self) -> None:
        self._armed = False
        self._set_click_guard_enabled(False)
        self._consume_click_guard = False
        self._phase = "free"
        self._pending_drag = False
        self._drag_start = None
        self._drag_rect = None
        self._clear_frozen_capture()
        self._hide_capture_overlay()
        self._hide_overlay()
        self._hide_hover_borders()
        self._last_hover_xy = None

        getter = getattr(self._status_var, "get", None)
        status_txt = (getter() if callable(getter) else "") or ""
        if status_txt.find("已录制") < 0:
            self._set_status("已停止捕获（待命）；点「开始捕获」或 F2 再继续")
        self._refresh_arm_ui()
        self._notify_armed_change()

    def _toggle_arm(self) -> None:
        if self._armed:
            self._disarm_pick()
        else:
            self._arm_pick()

    def _request_close(self) -> None:
        self._stop = True
        self._set_click_guard_enabled(False)
        self._teardown_click_guard()
        self._hide_capture_overlay()
        self._hide_overlay()
        self._hide_hover_borders()
        self._destroy_highlights()
        self._on_close()
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass

    def _apply_hover_peek(
        self,
        label: str,
        rect: Optional[Tuple[int, int, int, int]],
        *,
        reliable: bool = False,
        control_type: str = "",
    ) -> None:
        if not self._armed or self._phase != "free":
            self._clear_hover_highlight()
            return
        if self._capture_mode != CAPTURE_MODE_SMART:
            return
        # 无真实命中：不画框、不假装已框选（避免绿框跟着鼠标跑）
        if not reliable or not rect:
            self._clear_hover_highlight()
            self._hover_label = ""
            self._set_status("捕获中：将鼠标移到按钮/输入框/图标上，命中后显示绿框")
            return
        nice = (label or "").strip()
        self._hover_label = nice
        if nice and not _is_fake_container(nice) and not _is_shell_noise_label(nice):
            ct = (control_type or "").strip()
            extra = f" [{ct}]" if ct else ""
            self._set_status(f"目标：{nice}{extra} — 单击确认捕获")
        else:
            ct = (control_type or "").strip() or "控件"
            self._set_status(f"目标：已命中{ct} — 单击确认捕获")
        if self._hover_rect != rect:
            self._show_hover_borders(rect)

    def _schedule_hover_peek(self, x: int, y: int) -> None:
        if self._hover_busy or self._capture_mode != CAPTURE_MODE_SMART:
            return
        if time.time() - self._last_hover_ts < _HOVER_PEEK_INTERVAL_SEC:
            return
        if self._last_hover_xy:
            lx, ly = self._last_hover_xy
            if abs(x - lx) + abs(y - ly) < _HOVER_MOVE_MIN_PX:
                return
        self._last_hover_ts = time.time()
        self._last_hover_xy = (x, y)
        self._hover_busy = True

        def _work() -> None:
            label = ""
            rect: Optional[Tuple[int, int, int, int]] = None
            reliable = False
            control_type = ""
            shield = self._click_guard
            try:
                if shield is not None and getattr(shield, "enabled", False):
                    try:
                        shield.set_click_through(True)
                    except Exception:
                        pass
                result = _hover_locate(x, y)
                reliable = _is_reliable_element_hit(result, x, y)
                if reliable:
                    rect = result.get("rect")
                    label = result.get("label", "") or ""
                    control_type = result.get("control_type", "") or ""
                    if _is_shell_noise_label(label):
                        label = ""
            except Exception:
                reliable = False
                rect = None
            finally:
                if shield is not None and getattr(shield, "enabled", False):
                    try:
                        shield.set_click_through(False)
                    except Exception:
                        pass

            self._hover_busy = False

            if self._root and self._armed and self._phase == "free":
                self._root.after(
                    0,
                    lambda lb=label, rc=rect, rel=reliable, ct=control_type: self._apply_hover_peek(
                        lb, rc, reliable=rel, control_type=ct
                    ),
                )

        threading.Thread(target=_work, daemon=True, name="uia-hover").start()

    def _poll_input(self) -> None:
        if self._stop or not self._root:
            return

        try:
            esc = _escape_pressed()
            if esc and not self._prev_escape:
                self._request_close()
                return
            self._prev_escape = esc

            f2 = _f2_pressed()
            if f2 and not self._prev_f2:
                self._toggle_arm()
            self._prev_f2 = f2

            if self._phase == "locked":
                x, y = _cursor_pos()
                down = _lbutton_down()
                if esc:
                    self._cancel_locked_pick()
                elif down and not self._prev_lbutton:
                    tsb = _get_toolstrip_bounds(self._root)
                    if tsb and _rect_contains(tsb, x, y):
                        pass
                    elif not self._locked_rect or not _rect_contains(self._locked_rect, x, y):
                        self._cancel_locked_pick()
                self._prev_lbutton = down
                return

            if not self._armed:
                self._consume_click_guard = False
                self._prev_lbutton = _lbutton_down()
                self._prev_f3 = _f3_pressed()
                return

            self._refresh_shield_exclude()

            x, y = _cursor_pos()
            down = _lbutton_down()
            smart = self._capture_mode == CAPTURE_MODE_SMART
            f3 = _f3_pressed()

            # 挡板队列：点击被挡板吃掉后由此取点（目标应用收不到）
            shield_click = None
            if self._click_guard is not None and getattr(self._click_guard, "enabled", False):
                try:
                    while True:
                        nxt = self._click_guard.pop_click()
                        if nxt is None:
                            break
                        shield_click = nxt
                except Exception:
                    shield_click = None

            if self._consume_click_guard:
                if down or shield_click:
                    self._prev_lbutton = True
                    self._pending_drag = False
                    self._drag_start = None
                    self._prev_f3 = f3
                    return
                self._consume_click_guard = False
                self._prev_lbutton = False
                self._pick_enabled_after = time.time() + 0.15

            if smart and self._phase == "free" and not down and not _is_over_toolstrip(self._root, x, y):
                self._schedule_hover_peek(x, y)

            # F3 或挡板点击：确认捕获
            confirm_xy = None
            if smart and self._phase == "free" and f3 and not self._prev_f3:
                confirm_xy = (x, y)
            elif smart and self._phase == "free" and shield_click:
                confirm_xy = shield_click
            self._prev_f3 = f3

            if confirm_xy is not None:
                hx, hy = confirm_xy
                if time.time() >= self._pick_enabled_after and not self._is_over_picker_ui(hx, hy):
                    if not self._frozen_preview_b64:
                        self._freeze_capture_at_point(hx, hy)
                    self._finalize_smart_pick(hx, hy)
                self._pending_drag = False
                self._drag_start = None
                self._prev_lbutton = down
                return

            if self._phase == "free":
                if down and not self._prev_lbutton:
                    # 有挡板时点击走 shield_click；此处仅处理工具条/无挡板兜底
                    if self._click_guard is not None and getattr(self._click_guard, "enabled", False):
                        self._prev_lbutton = down
                        return
                    self._hide_hover_borders()
                    self._drag_start = (x, y)
                    self._pending_drag = True
                    if smart and not self._is_over_picker_ui(x, y):
                        self._freeze_capture_at_point(x, y)
                elif self._pending_drag and self._drag_start and down:
                    sx, sy = self._drag_start
                    if abs(x - sx) + abs(y - sy) >= 10:
                        if smart:
                            self._pending_drag = False
                            self._drag_start = None
                            self._set_status("智能点选请直接单击或按 F3，无需拖拽")
                        else:
                            self._phase = "drag"
                            self._pending_drag = False
                            self._show_overlay()
                            self._set_status("拖拽中… 松开鼠标完成框选")
                elif not down and self._prev_lbutton and self._pending_drag and self._drag_start:
                    if smart:
                        if time.time() < self._pick_enabled_after:
                            pass
                        elif self._is_over_picker_ui(x, y):
                            pass
                        else:
                            if not self._frozen_preview_b64:
                                self._freeze_capture_at_point(x, y)
                            self._finalize_smart_pick(x, y)
                        self._pending_drag = False
                        self._drag_start = None
                    else:
                        self._pending_drag = False
                        self._drag_start = None
            elif self._phase == "drag":
                if self._drag_start:
                    self._drag_rect = (self._drag_start[0], self._drag_start[1], x, y)
                    self._redraw_region()
                if not down and self._prev_lbutton:
                    if self._drag_rect:
                        l, t, r, b = self._drag_rect
                        if abs(r - l) >= 8 and abs(b - t) >= 8:
                            self._phase = "click_offset"
                            self._set_status("在框内点击一次以确定操作位置")
                        else:
                            self._drag_rect = None
                            self._phase = "free"
                            self._hide_overlay()
                            self._set_status("框选过小，请重新拖拽")
                    else:
                        self._phase = "free"
                        self._hide_overlay()
            elif self._phase == "click_offset":
                offset_click = shield_click
                if offset_click is None and down and not self._prev_lbutton:
                    offset_click = (x, y)
                if offset_click is not None:
                    ox, oy = offset_click
                    self._finalize_region_record(ox, oy)
                    self._drag_rect = None
                    self._phase = "free"
                    self._hide_overlay()
                    self._set_status("已录制；可继续捕获或点「结束」")

            self._prev_lbutton = down
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            if not self._stop and self._root:
                self._root.after(16, self._poll_input)

    def _redraw_region(self) -> None:
        if not self._drag_rect:
            return
        self._hide_hover_borders()
        if self._hl_region:
            try:
                self._hl_region.show(self._drag_rect)
            except Exception:
                pass

    def _flash_rect(self, l: int, t: int, r: int, b: int) -> None:
        self._show_hover_borders((l, t, r, b))

        def _hide() -> None:
            self._hide_hover_borders()

        if self._root:
            self._root.after(450, _hide)

    def _finalize_smart_pick(self, click_x: int, click_y: int) -> None:
        try:
            self._set_click_guard_enabled(False)
            if not self._frozen_preview_b64:
                self._freeze_capture_at_point(click_x, click_y)
            frozen_preview = self._frozen_preview_b64
            frozen_rect = self._frozen_rect
            frozen_label = self._frozen_label
            self._hide_capture_overlay()
            self._freeze_hover_borders()
            self._phase = "locked"
            self._armed = False
            self._pick_enabled_after = time.time() + 9999
            self._set_status("正在分析元素…（预览已冻结）")

            import threading
            thr = threading.Thread(
                target=self._build_locked_pick,
                args=(click_x, click_y, frozen_preview, frozen_rect, frozen_label),
                daemon=True,
            )
            thr.start()
        except Exception as exc:
            self._on_error(str(exc))

    def _build_locked_pick(
        self,
        click_x: int,
        click_y: int,
        frozen_preview: str = "",
        frozen_rect: Optional[Tuple[int, int, int, int]] = None,
        frozen_label: str = "",
    ) -> None:
        try:
            pick = build_pick_from_smart_click(
                click_x,
                click_y,
                action="click",
                frozen_preview_b64=frozen_preview or "",
                frozen_rect=frozen_rect,
                frozen_label=frozen_label or "",
            )
            self._locked_pick = pick
            self._locked_rect = pick.get("rectangle")

            if self._root:
                self._root.after(0, self._on_locked_pick_ready, pick, click_x, click_y)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            if self._root:
                self._root.after(0, lambda e=str(exc): (
                    self._on_error(e),
                    self._cancel_locked_pick(),
                ))

    def _on_locked_pick_ready(self, pick: Dict[str, Any], click_x: int, click_y: int) -> None:
        if self._phase != "locked":
            return
        label = pick.get("label", "")
        preview = pick.get("preview_image_b64", "")
        si = pick.get("structure_info", {})
        hint = pick.pop("uia_hint", None)
        if label:
            msg = f"已选中：{label} — 请选择操作"
        else:
            msg = "已选中元素 — 请选择操作"
        if hint:
            msg += f"（{hint}）"
        self._set_status(msg)

        self._element_panel = ElementInfoPanel(
            on_action=self._on_element_action,
            on_cancel=self._cancel_locked_pick,
        )
        self._element_panel.update_data(
            pick_data=pick,
            element_label=label,
            preview_b64=preview,
            structure_info=si,
        )
        self._element_panel.position_near(click_x, click_y)
        self._element_panel.show()

        rect = pick.get("rectangle") or {}
        self._flash_rect(
            int(rect.get("left", click_x - 32)),
            int(rect.get("top", click_y - 32)),
            int(rect.get("right", click_x + 32)),
            int(rect.get("bottom", click_y + 32)),
        )

    def _on_element_action(self, action: str, input_value: str = "") -> None:
        pick = self._locked_pick
        if not pick:
            return
        pick_with_act = {**pick, "record_action": action, "input_value": input_value}
        self._on_record({
            "pick": pick_with_act,
            "action": action,
            "input_value": input_value,
            "click_x": int((pick.get("pick_point") or {}).get("x", 0)),
            "click_y": int((pick.get("pick_point") or {}).get("y", 0)),
        })
        si = pick.get("structure_info", {})
        label = pick.get("label", "")
        method = si.get("resolved_method", "")
        msg = f"已录制: {label} → {action}"
        if input_value:
            msg += f"「{input_value}」"
        if method and method != "uia":
            msg += f" ({method.upper()})"
        self._set_status(msg)

    def _freeze_hover_borders(self) -> None:
        pass

    def _cancel_locked_pick(self) -> None:
        self._hide_capture_overlay()
        self._clear_hover_highlight()
        self._locked_pick = None
        self._locked_rect = None
        self._phase = "free"
        if self._element_panel:
            self._element_panel.close()
            self._element_panel = None
        self._set_status("已取消；点「开始捕获」或 F2 重新捕获")
        self._refresh_arm_ui()

    def _finalize_region_record(self, click_x: int, click_y: int) -> None:
        if not self._drag_rect:
            return
        l, t, r, b = self._drag_rect
        x0, y0 = min(l, r), min(t, b)
        x1, y1 = max(l, r), max(t, b)
        if not (x0 <= click_x <= x1 and y0 <= click_y <= y1):
            click_x = (x0 + x1) // 2
            click_y = (y0 + y1) // 2
        try:
            from modules.desktop.desktop_visual_engine import (
                VISUAL_SELECTOR_TYPE,
                build_visual_step_payload,
            )

            preview_b64 = ""
            try:
                from modules.desktop.desktop_precise_locator import capture_rect_preview_b64

                preview_b64 = capture_rect_preview_b64(l, t, r, b, padding=8)
            except Exception:
                pass
            payload = build_visual_step_payload(
                l, t, r, b, click_x, click_y, match_threshold=0.72
            )
            action = (self._action_var.get() if self._action_var else "click").strip()
            pick = {
                "selector_type": VISUAL_SELECTOR_TYPE,
                "selector_value": payload.to_json(),
                "pick_point": {"x": click_x, "y": click_y},
                "preview_image_b64": preview_b64,
                "label": f"视觉_{action}@{click_x},{click_y}",
                "capture_mode": CAPTURE_MODE_REGION,
                "rectangle": {
                    "left": x0,
                    "top": y0,
                    "right": x1,
                    "bottom": y1,
                },
            }
            self._on_record(
                {
                    "pick": pick,
                    "action": action,
                    "click_x": click_x,
                    "click_y": click_y,
                }
            )
            self._set_status(f"已框选录制 @ ({click_x},{click_y})")
            self._pause_after_record()
        except Exception as exc:
            self._on_error(str(exc))


class ElementInfoPanel:
    """选中元素后弹出的微型信息面板：截图缩略图 + 结构信息 + 操作按钮 + 连续操作支持。"""

    _ACTIONS = [
        ("单击", "click"),
        ("双击", "double_click"),
        ("右键", "right_click"),
        ("输入", "input"),
        ("验证", "verify"),
    ]

    def __init__(
        self,
        on_action: "Callable[[str, str], None]",
        on_cancel: "Callable[[], None]",
        *,
        loading: bool = False,
    ):
        self._on_action = on_action
        self._on_cancel = on_cancel
        self._pick_data: Dict[str, Any] = {}
        self._recorded_actions: List[Dict[str, str]] = []
        self._window: Any = None
        self._img_label: Any = None
        self._info_text: Any = None
        self._input_entry: Any = None
        self._input_frame: Any = None
        self._preview_photo: Any = None
        self._history_text: Any = None
        self._loading = loading
        self._build()

    def _build(self) -> None:
        import tkinter as tk

        win = tk.Toplevel()
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        try:
            win.overrideredirect(True)
        except Exception:
            pass
        win.configure(bg="#1e1e2e")
        win.resizable(False, False)

        header = tk.Label(
            win,
            text="元素信息",
            fg="#cdd6f4",
            bg="#1e1e2e",
            font=("Segoe UI", 11, "bold"),
        )
        header.pack(pady=(10, 4), padx=12, anchor="w")

        self._img_label = tk.Label(win, bg="#313244", width=24, height=12)
        self._img_label.pack(padx=12, pady=4)

        self._info_text = tk.Label(
            win,
            text="" if not self._loading else "正在分析元素…",
            fg="#a6adc8",
            bg="#1e1e2e",
            font=("Segoe UI", 9),
            justify="left",
        )
        self._info_text.pack(padx=12, pady=(2, 0), anchor="w")

        self._history_text = tk.Label(
            win,
            text="",
            fg="#f9e2af",
            bg="#1e1e2e",
            font=("Segoe UI", 8),
            justify="left",
        )
        self._history_text.pack(padx=12, pady=(2, 4), anchor="w")

        sep = tk.Frame(win, bg="#45475a", height=1)
        sep.pack(fill="x", padx=8, pady=4)

        btn_frame = tk.Frame(win, bg="#1e1e2e")
        btn_frame.pack(padx=10, pady=(2, 4), fill="x")

        row_idx = 0
        col_count = 3
        for label, action in self._ACTIONS:
            col = row_idx % col_count
            if col == 0:
                row_widget = tk.Frame(btn_frame, bg="#1e1e2e")
                row_widget.pack(fill="x", pady=1)
            btn = tk.Button(
                row_widget,
                text=label,
                command=lambda a=action: self._on_action_click(a),
                bg="#45475a",
                fg="#cdd6f4",
                activebackground="#585b70",
                activeforeground="#cdd6f4",
                relief="flat",
                font=("Segoe UI", 9),
                padx=2,
                pady=3,
            )
            btn.pack(side="left", fill="x", expand=True, padx=2)
            row_idx += 1

        self._input_frame = tk.Frame(win, bg="#1e1e2e")
        self._input_entry = tk.Entry(
            self._input_frame,
            bg="#313244",
            fg="#cdd6f4",
            insertbackground="#cdd6f4",
            relief="flat",
            font=("Segoe UI", 9),
        )
        self._input_entry.pack(side="left", fill="x", expand=True, padx=2)
        tk.Button(
            self._input_frame,
            text="确认输入",
            command=lambda: self._on_action_click("input"),
            bg="#89b4fa",
            fg="#1e1e2e",
            activebackground="#74c7ec",
            relief="flat",
            font=("Segoe UI", 8),
            padx=8,
        ).pack(side="left", padx=2)

        cancel_btn = tk.Button(
            win,
            text="取消",
            command=self._on_cancel_click,
            bg="#f38ba8",
            fg="#1e1e2e",
            activebackground="#eba0ac",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        cancel_btn.pack(pady=(2, 10), padx=12, fill="x")

        self._window = win
        self._input_frame.pack_forget()

    def _on_action_click(self, action: str) -> None:
        if action == "input":
            val = (self._input_entry.get() or "").strip()
            if not val:
                self._input_frame.pack(
                    padx=10, pady=(2, 4), fill="x", before=self._window.children.get(list(self._window.children.keys())[-1])
                )
                self._input_entry.focus_set()
                return
            input_val = val
            self._input_entry.delete(0, "end")
            self._input_frame.pack_forget()
        else:
            input_val = ""
        self._on_action(action, input_val)
        self._recorded_actions.append({"action": action, "input": input_val})
        self._update_history()

    def _on_cancel_click(self) -> None:
        self._on_cancel()

    def _update_history(self) -> None:
        if not self._history_text:
            return
        parts = []
        for i, rec in enumerate(self._recorded_actions, 1):
            act = rec["action"]
            inp = rec.get("input", "")
            label = act
            if inp:
                label = f"{act}「{inp}」"
            parts.append(f"{i}. {label}")
        self._history_text.configure(text="\n".join(parts) if parts else "")

    def update_data(
        self,
        pick_data: Dict[str, Any],
        element_label: str,
        preview_b64: str,
        structure_info: Dict[str, Any],
    ) -> None:
        self._pick_data = pick_data
        lines = []
        if element_label:
            lines.append(f"标签: {element_label}")
        proc = structure_info.get("app_process_name", "")
        if proc:
            lines.append(f"进程: {proc}")
        title = structure_info.get("app_window_title", "")
        if title:
            lines.append(f"窗口: {title}")
        etype = structure_info.get("element_type", "")
        if etype and etype != "Control":
            lines.append(f"控件: {etype}")
        method = structure_info.get("resolved_method", "")
        if method:
            lines.append(f"定位: {method.upper()}")
        self._info_text.configure(text="\n".join(lines) if lines else "无结构化信息")

        if preview_b64 and self._img_label:
            try:
                import base64
                import io
                from PIL import Image, ImageTk

                png = base64.b64decode(preview_b64)
                img = Image.open(io.BytesIO(png)).convert("RGBA")
                img = img.resize((96, 72), Image.LANCZOS)
                self._preview_photo = ImageTk.PhotoImage(img)
                self._img_label.configure(image=self._preview_photo, bg="#313244")
            except Exception:
                self._img_label.configure(text="[无预览]", bg="#313244")

    def position_near(self, x: int, y: int) -> None:
        if not self._window:
            return
        try:
            self._window.update_idletasks()
            w = self._window.winfo_width()
            h = self._window.winfo_height()
        except Exception:
            w, h = 220, 320
        sw = self._window.winfo_screenwidth()
        sh = self._window.winfo_screenheight()
        nx = x + 20
        ny = y + 20
        if nx + w > sw:
            nx = x - w - 20
        if ny + h > sh:
            ny = y - h - 20
        try:
            self._window.geometry(f"+{max(0, nx)}+{max(0, ny)}")
        except Exception:
            pass

    def show(self) -> None:
        if self._window:
            try:
                self._window.deiconify()
                self._window.lift()
            except Exception:
                pass

    def close(self) -> None:
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

    def is_open(self) -> bool:
        return self._window is not None


def schedule_uia_snapshot_enrichment(
    pick: Dict[str, Any],
    click_x: int,
    click_y: int,
    *,
    on_done: Optional[Any] = None,
) -> None:
    """后台补全 element_snapshot（点选模式通常已有，框选模式才需要）。"""
    if pick.get("element_snapshot"):
        if on_done:
            on_done(pick)
        return
    import threading

    def _worker() -> None:
        try:
            from modules.desktop.desktop_uia_snapshot import capture_element_snapshot_at_point
            from modules.desktop.desktop_visual_engine import VisualStepPayload

            res = capture_element_snapshot_at_point(click_x, click_y, timeout_sec=2.0)
            if not res.ok or not res.element_snapshot:
                return
            sv = (pick.get("selector_value") or "").strip()
            if not sv:
                return
            payload = VisualStepPayload.from_json(sv)
            merged = payload.merge_element_snapshot(res.element_snapshot)
            pick["selector_value"] = merged.to_json()
            pick["element_snapshot"] = res.element_snapshot
            if res.screen_center:
                pick["pick_point"] = {
                    "x": res.screen_center[0],
                    "y": res.screen_center[1],
                }
            snap_sel = res.element_snapshot.get("selector") or {}
            kc = snap_sel.get("key_candidates") or []
            current_label = (pick.get("label") or "").strip()
            is_fallback = (
                not current_label
                or current_label.startswith("桌面_")
            )
            # 冻结预览时禁止 enrichment 改名，保证与模板一致
            if pick.get("preview_frozen"):
                plain = (pick.get("name") or pick.get("structure_info", {}).get("element_text") or "").strip()
                if plain:
                    patched = _patch_snapshot_text(res.element_snapshot, plain)
                    if patched:
                        pick["element_snapshot"] = patched
                        merged2 = payload.merge_element_snapshot(patched)
                        pick["selector_value"] = merged2.to_json()
            elif kc and is_fallback:
                kv = kc[0].get("value") or ""
                if kv:
                    pick["label"] = f"ListItem_{kv}"
                    pick["name"] = kv
            if on_done:
                on_done(pick)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name="uia-snap-enrich").start()


def build_visual_recorded_step(
    pick: Dict[str, Any],
    *,
    action: str = "click",
    input_value: str = "",
) -> Dict[str, Any]:
    act = (action or "click").strip().lower()
    sv = (pick.get("selector_value") or "").strip()
    pt = pick.get("pick_point") or {}
    cx = int(pt.get("x", 0))
    cy = int(pt.get("y", 0))
    label = (pick.get("label") or pick.get("name") or "").strip()
    mode = pick.get("capture_mode") or CAPTURE_MODE_SMART
    structure_info = pick.get("structure_info") or {}

    desc = label if label else f"桌面：{act} @ ({cx},{cy})"
    if mode == CAPTURE_MODE_REGION and "视觉" not in desc:
        desc = f"视觉：{desc}"

    if structure_info.get("app_process_name") and structure_info.get("element_text"):
        proc = structure_info["app_process_name"]
        text = structure_info["element_text"]
        method = structure_info.get("resolved_method", "")
        method_tag = f" ({method.upper()})" if method and method not in ("uia",) else ""
        desc = f"{proc} → {text}{method_tag}"

    locator_candidates = pick.get("locator_candidates") or []
    if not locator_candidates and structure_info.get("element_text"):
        ocr_text = structure_info["element_text"]
        resolved_method = structure_info.get("resolved_method", "")
        if resolved_method in ("ocr", "win32", "visual"):
            locator_candidates = [{
                "selector_type": "ocr_text",
                "selector_value": ocr_text,
                "score": 85,
            }]

    return {
        "action": act,
        "automation_layer": "desktop",
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": sv,
        "input_value": (input_value or "").strip(),
        "compare_type": "",
        "description": desc,
        "desktop_spec": pick.get("desktop_spec") or {},
        "locator_candidates": locator_candidates,
        "record_meta": {"pick": pick, "visual": True, "capture_mode": mode},
    }
