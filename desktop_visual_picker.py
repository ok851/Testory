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
_TRANSPARENT_KEY = "#010101"
_HOVER_PEEK_INTERVAL_SEC = 0.28
_HOVER_MOVE_MIN_PX = 8
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


_FAKE_CONTAINER_PATTERNS = (
    "chrome",
    "renderwidget",
    "legacy window",
    "widgetwin",
    "corewindow",
    "webview",
)


def _is_fake_container(name: str, class_name: str = "") -> bool:
    combined = f"{name or ''} {class_name or ''}".lower()
    return any(p in combined for p in _FAKE_CONTAINER_PATTERNS)


def _layered_locate(x: int, y: int) -> Dict:
    from typing import Dict, Optional, Tuple

    candidates = []

    win32_result = _locate_via_win32(x, y)
    if win32_result and win32_result.get('rect'):
        rw = win32_result['rect'][2] - win32_result['rect'][0]
        rh = win32_result['rect'][3] - win32_result['rect'][1]
        score = 0
        if 20 <= rw <= 500 and 20 <= rh <= 500:
            score += 30
        if win32_result.get('label'):
            score += 20
        candidates.append({
            'rect': win32_result['rect'],
            'label': win32_result.get('label', ''),
            'score': score,
            'source': 'win32',
        })

    uia_result = _locate_via_uia(x, y)
    if uia_result and uia_result.get('rect'):
        rw = uia_result['rect'][2] - uia_result['rect'][0]
        rh = uia_result['rect'][3] - uia_result['rect'][1]
        score = 40
        if 15 <= rw <= 400 and 15 <= rh <= 400:
            score += 30
        if uia_result.get('label'):
            score += 20
        candidates.append({
            'rect': uia_result['rect'],
            'label': uia_result.get('label', ''),
            'score': score,
            'source': 'uia',
        })

    try:
        from desktop_ocr_locate import locate_element_via_ocr
        ocr_result = locate_element_via_ocr(x, y)
        if ocr_result and ocr_result.get('rect'):
            rw = ocr_result['rect'][2] - ocr_result['rect'][0]
            rh = ocr_result['rect'][3] - ocr_result['rect'][1]
            score = 25
            if 20 <= rw <= 300 and 15 <= rh <= 100:
                score += 25
            if ocr_result.get('text'):
                score += 30
            candidates.append({
                'rect': ocr_result['rect'],
                'label': ocr_result.get('text', ''),
                'score': score,
                'source': 'ocr',
            })
    except Exception:
        pass

    if candidates:
        best = max(candidates, key=lambda c: c['score'])
        return {
            'rect': best['rect'],
            'label': best['label'],
            'source': best['source'],
        }

    return {
        'rect': (x - 48, y - 48, x + 48, y + 48),
        'label': '',
        'source': 'fallback',
    }


def _locate_via_win32(x: int, y: int) -> Dict:
    from typing import Dict, Optional, Tuple

    try:
        from desktop_win32_snapshot import window_from_point, get_window_rect, get_window_text, get_window_class

        hwnd = window_from_point(x, y)
        if not hwnd:
            return {}

        rect = get_window_rect(hwnd)
        if not rect:
            return {}

        text = get_window_text(hwnd)
        cls = get_window_class(hwnd)

        return {
            'rect': rect,
            'label': text or cls or '',
        }
    except Exception:
        return {}


def _locate_via_uia(x: int, y: int) -> Dict:
    from typing import Dict, Optional, Tuple

    try:
        from desktop_uia_snapshot import peek_element_at_point

        res = peek_element_at_point(x, y, timeout_sec=0.32)
        if res.ok and res.bounding_rect:
            return {
                'rect': res.bounding_rect,
                'label': res.element_label or '',
            }
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


def build_pick_from_smart_click(
    click_x: int,
    click_y: int,
    *,
    action: str = "click",
    match_threshold: float = 0.72,
) -> Dict[str, Any]:
    from desktop_uia_snapshot import (
        capture_element_snapshot_at_point,
        rect_with_padding,
    )
    from desktop_visual_engine import build_visual_step_payload

    act = (action or "click").strip().lower()
    snap = capture_element_snapshot_at_point(click_x, click_y, timeout_sec=2.5)
    is_fake = _is_fake_container(
        snap.element_label or "",
        (snap.element_snapshot or {}).get("class_name") or "",
    )

    cx, cy = click_x, click_y
    if snap.screen_center and not is_fake:
        cx, cy = snap.screen_center

    if is_fake:
        snap = snap._replace(
            element_label="",
            control_type="",
            ok=False,
            error_code="fake_container",
            message="UIA仅命中渲染容器，无法识别内部元素",
        )

    layered_result = _layered_locate(click_x, click_y)
    layered_rect = layered_result.get('rect')
    layered_label = layered_result.get('label', '')

    pad = 48
    if layered_rect:
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

    element_label = layered_label or snap.element_label or ""

    sel = (snap.element_snapshot or {}).get("selector") or {}
    resolved_via = sel.get("resolved_via") or ""
    need_ocr = not element_label or resolved_via == "uia_window"
    if not need_ocr and snap.bounding_rect:
        rect_w = snap.bounding_rect[2] - snap.bounding_rect[0]
        rect_h = snap.bounding_rect[3] - snap.bounding_rect[1]
        if rect_w > 300 or rect_h > 300:
            need_ocr = True
    if need_ocr:
        try:
            from desktop_ocr import extract_primary_text
            from desktop_precise_locator import capture_rect_preview_b64

            preview = capture_rect_preview_b64(l, t, r, b, padding=4)
            if preview:
                ocr_text = extract_primary_text(preview)
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
        except ImportError:
            pass

    window_rect = None
    try:
        from desktop_win32_snapshot import get_parent_window_rect

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
            from desktop_win32_snapshot import (
                get_parent_window_rect,
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
    ct = (snap.control_type or "").lower()
    if "list" in ct and element_label:
        resolved_method = snap.error_code or resolved_method
    elif snap.ok and snap.element_snapshot:
        resolved_method = (
            snap.element_snapshot.get("selector") or {}
        ).get("resolved_via") or "uia"

    control_type_label = snap.control_type or "Control"
    if not snap.ok:
        control_type_label = "Control"

    label = ""
    if element_label:
        if "list" in ct:
            label = f"ListItem_{element_label}"
        elif control_type_label and control_type_label != "Control":
            label = f"{control_type_label}_{element_label}"
        else:
            label = element_label
    if not label:
        label = f"未命名元素_{act}@{cx},{cy}"

    replacement_label = element_label or label

    structure_info = {
        "app_window_title": app_window_title,
        "app_process_name": app_process_name,
        "element_text": element_label,
        "element_type": control_type_label,
        "resolved_method": resolved_method,
    }

    preview_b64 = ""
    try:
        from desktop_precise_locator import capture_rect_preview_b64

        preview_b64 = capture_rect_preview_b64(l, t, r, b, padding=4)
    except Exception:
        pass

    pick: Dict[str, Any] = {
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": payload.to_json(),
        "pick_point": {"x": cx, "y": cy},
        "preview_image_b64": preview_b64,
        "label": replacement_label,
        "name": replacement_label,
        "capture_mode": CAPTURE_MODE_SMART,
        "rectangle": {"left": l, "top": t, "right": r, "bottom": b},
        "structure_info": structure_info,
    }
    if snap.element_snapshot:
        pick["element_snapshot"] = snap.element_snapshot
    if not snap.ok:
        hint = snap.message or snap.error_code or "结构信息不可用"
        if element_label:
            hint = f"已用OCR提取文本「{element_label}」，{hint}"
        pick["uia_hint"] = hint
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
        self._overlay = None
        self._canvas = None
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
        self._on_message("捕获器已就绪（待命）；点「开始捕获」后再点目标元素")
        self._refresh_arm_ui()
        self._root.bind("<Escape>", lambda _e: self._request_close())
        self._poll_input()
        self._root.mainloop()

    def _on_mode_change(self) -> None:
        self._capture_mode = (self._mode_var.get() if self._mode_var else CAPTURE_MODE_SMART)
        if self._armed:
            self._disarm_pick()
        if self._capture_mode == CAPTURE_MODE_SMART:
            self._set_status("已选智能点选：开始捕获后在目标上单击一次")
        else:
            self._set_status("已选区域框选：开始捕获后拖拽框选（仅视觉补充）")

    def _build_overlay(self) -> None:
        import tkinter as tk

        _OVERLAY_KEY = "#010203"

        self._overlay = tk.Toplevel(self._root)
        self._overlay.withdraw()
        self._overlay.overrideredirect(True)
        self._overlay.geometry(f"{self._sw}x{self._sh}+0+0")
        self._overlay.configure(bg=_OVERLAY_KEY)
        try:
            self._overlay.attributes("-topmost", True)
            self._overlay.attributes("-transparentcolor", _OVERLAY_KEY)
        except Exception:
            pass
        self._canvas = tk.Canvas(
            self._overlay,
            width=self._sw,
            height=self._sh,
            highlightthickness=0,
            bg=_OVERLAY_KEY,
        )
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Button-1>", lambda _e: None)
        self._canvas.bind("<ButtonRelease-1>", lambda _e: None)
        self._overlay_key = _OVERLAY_KEY

        self._overlay.update_idletasks()
        try:
            import ctypes
            hwnd = int(self._overlay.frame(), 16)
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_NOACTIVATE = 0x08000000
            u32 = ctypes.windll.user32
            cur_ex = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur_ex | WS_EX_LAYERED | WS_EX_NOACTIVATE)
            user32 = ctypes.windll.user32
            LWA_COLORKEY = 0x00000001
            # _OVERLAY_KEY = "#010203" -> R=0x01, G=0x02, B=0x03
            # COLORREF format: 0x00BBGGRR
            key_color = (0x03 << 16) | (0x02 << 8) | 0x01
            user32.SetLayeredWindowAttributes(hwnd, key_color, 0, LWA_COLORKEY)
        except Exception:
            pass

    def _show_capture_overlay(self) -> None:
        if not self._overlay or not self._canvas:
            return
        self._canvas.delete("all")

        bw = 4
        self._canvas.create_rectangle(
            bw, bw, self._sw - bw, self._sh - bw,
            outline="#22c55e", width=bw,
        )
        self._canvas.create_rectangle(
            bw + 2, bw + 2, self._sw - bw - 2, self._sh - bw - 2,
            outline="#86efac", width=1,
        )

        label_text = "桌面捕获模式"
        self._canvas.create_text(
            self._sw // 2, 48,
            text=label_text,
            fill="#22c55e",
            font=("Microsoft YaHei", 16, "bold"),
            anchor="center",
        )

        sub_text = "单击目标元素 · Esc 退出 · F2 暂停"
        self._canvas.create_text(
            self._sw // 2, 72,
            text=sub_text,
            fill=_THEME["text_light"],
            font=("Microsoft YaHei", 11),
            anchor="center",
        )

        corner_size = 24
        corners = [
            (bw, bw, bw + corner_size, bw + corner_size),
            (self._sw - bw - corner_size, bw, self._sw - bw, bw + corner_size),
            (bw, self._sh - bw - corner_size, bw + corner_size, self._sh - bw),
            (self._sw - bw - corner_size, self._sh - bw - corner_size, self._sw - bw, self._sh - bw),
        ]
        for x0, y0, x1, y1 in corners:
            self._canvas.create_line(x0, y0, x1, y0, fill="#22c55e", width=3)
            self._canvas.create_line(x0, y0, x0, y1, fill="#22c55e", width=3)

        try:
            self._overlay.deiconify()
            self._overlay.lift()
        except Exception:
            pass

    def _hide_capture_overlay(self) -> None:
        if self._overlay:
            try:
                self._overlay.withdraw()
            except Exception:
                pass
        if self._canvas:
            self._canvas.delete("all")
        self._hover_rect_id = None
        self._hover_rect = None

    def _show_overlay(self) -> None:
        if not self._overlay or not self._canvas:
            return
        self._canvas.delete("all")
        try:
            self._overlay.deiconify()
            self._overlay.lift()
        except Exception:
            pass

    def _hide_overlay(self) -> None:
        if self._overlay:
            try:
                self._overlay.withdraw()
            except Exception:
                pass
        if self._canvas:
            self._canvas.delete("all")
        self._hover_rect_id = None
        self._hover_rect = None

    def _hide_hover_borders(self) -> None:
        if self._canvas:
            self._canvas.delete("hover")
        self._hover_rect_id = None
        self._hover_rect = None

    def _clear_hover_highlight(self) -> None:
        self._hide_hover_borders()
        if self._phase == "drag" or self._phase == "click_offset":
            return

    def _show_hover_borders(self, rect: Tuple[int, int, int, int]) -> None:
        l, t, r, b = rect
        pad = 2
        x0 = min(l, r) - pad
        y0 = min(t, b) - pad
        x1 = max(l, r) + pad
        y1 = max(t, b) + pad
        framed = (x0, y0, x1, y1)
        if self._hover_rect == framed:
            return
        self._hide_hover_borders()
        if self._canvas and self._overlay:
            self._canvas.create_rectangle(
                x0 - 2, y0 - 2, x1 + 2, y1 + 2,
                outline="#fee2e2", width=2,
                tags="hover",
            )
            self._hover_rect_id = self._canvas.create_rectangle(
                x0, y0, x1, y1,
                outline="#ef4444", width=2,
                tags="hover",
            )

            corner_size = 12
            corners = [
                (x0, y0, x0 + corner_size, y0 + corner_size),
                (x1 - corner_size, y0, x1, y0 + corner_size),
                (x0, y1 - corner_size, x0 + corner_size, y1),
                (x1 - corner_size, y1 - corner_size, x1, y1),
            ]
            for cx0, cy0, cx1, cy1 in corners:
                self._canvas.create_line(cx0, cy0, cx1, cy0, fill="#ef4444", width=2, tags="hover")
                self._canvas.create_line(cx0, cy0, cx0, cy1, fill="#ef4444", width=2, tags="hover")
        self._hover_rect = framed

    def _set_status(self, msg: str) -> None:
        if self._status_var:
            self._status_var.set(msg)
        self._on_message(msg)

    def _refresh_arm_ui(self) -> None:
        if self._state_lbl:
            if self._armed:
                self._state_lbl.configure(
                    text="● 捕获中（点击目标将录制）",
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
        self._consume_click_guard = False
        self._phase = "free"
        self._pending_drag = False
        self._drag_start = None
        self._drag_rect = None
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

    def _arm_pick(self) -> None:
        self._capture_mode = (
            self._mode_var.get() if self._mode_var else CAPTURE_MODE_SMART
        )
        self._armed = True
        self._phase = "free"
        self._drag_start = None
        self._drag_rect = None
        self._begin_click_guard()
        self._hide_hover_borders()
        self._last_hover_xy = None
        if self._capture_mode == CAPTURE_MODE_SMART:
            self._show_capture_overlay()
            self._set_status(
                "捕获中：移动鼠标可高亮元素边框，在目标上单击一次确认"
            )
        else:
            self._hide_overlay()
            self._set_status("捕获中：拖拽框选区域，再在框内点击定操作点")

        try:
            import ctypes
            hwnd = int(self._overlay.frame(), 16)
            ctypes.windll.user32.SetCapture(hwnd)
        except Exception:
            pass

        self._refresh_arm_ui()
        self._notify_armed_change()

    def _disarm_pick(self) -> None:
        self._armed = False
        self._consume_click_guard = False
        self._phase = "free"
        self._pending_drag = False
        self._drag_start = None
        self._drag_rect = None
        self._hide_capture_overlay()
        self._hide_overlay()
        self._hide_hover_borders()
        self._last_hover_xy = None

        try:
            import ctypes
            ctypes.windll.user32.ReleaseCapture()
        except Exception:
            pass

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
        self._hide_overlay()
        self._hide_hover_borders()
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
    ) -> None:
        if not self._armed or self._phase != "free":
            self._clear_hover_highlight()
            return
        if self._capture_mode != CAPTURE_MODE_SMART:
            return
        if label and not _is_fake_container(label):
            self._set_status(f"指向：{label} — 单击确认捕获")
        if rect:
            if self._hover_rect != rect:
                self._show_hover_borders(rect)
        else:
            self._clear_hover_highlight()

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
            try:
                result = _layered_locate(x, y)
                rect = result.get('rect')
                label = result.get('label', '')
            except Exception:
                pass

            if rect:
                rw = rect[2] - rect[0]
                rh = rect[3] - rect[1]
                if rw > 150 or rh > 150:
                    rect = (x - 48, y - 48, x + 48, y + 48)

            self._hover_busy = False

            if self._root and self._armed and self._phase == "free":
                self._root.after(
                    0,
                    lambda: self._apply_hover_peek(label, rect),
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
                return

            x, y = _cursor_pos()
            down = _lbutton_down()
            smart = self._capture_mode == CAPTURE_MODE_SMART

            if self._consume_click_guard:
                if down:
                    self._prev_lbutton = True
                    self._pending_drag = False
                    self._drag_start = None
                    return
                self._consume_click_guard = False
                self._prev_lbutton = False
                self._pick_enabled_after = time.time() + 0.15

            if smart and self._phase == "free" and not down and not _is_over_toolstrip(self._root, x, y):
                self._schedule_hover_peek(x, y)

            if self._phase == "free":
                if down and not self._prev_lbutton:
                    self._hide_hover_borders()
                    self._drag_start = (x, y)
                    self._pending_drag = True
                elif self._pending_drag and self._drag_start and down:
                    sx, sy = self._drag_start
                    if abs(x - sx) + abs(y - sy) >= 10:
                        if smart:
                            self._pending_drag = False
                            self._drag_start = None
                            self._set_status("智能点选请直接单击，无需拖拽")
                        else:
                            self._phase = "drag"
                            self._pending_drag = False
                            self._show_overlay()
                            self._set_status("拖拽中… 松开鼠标完成框选")
                elif not down and self._prev_lbutton and self._pending_drag and self._drag_start:
                    if smart:
                        if time.time() < self._pick_enabled_after:
                            self._pending_drag = False
                            self._drag_start = None
                        elif _is_over_toolstrip(self._root, x, y):
                            self._pending_drag = False
                            self._drag_start = None
                        else:
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
                if down and not self._prev_lbutton:
                    self._finalize_region_record(x, y)
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
        if not self._canvas or not self._drag_rect:
            return
        self._hide_hover_borders()
        self._canvas.delete("all")
        l, t, r, b = self._drag_rect
        x0, y0 = min(l, r), min(t, b)
        x1, y1 = max(l, r), max(t, b)
        self._canvas.create_rectangle(
            x0, y0, x1, y1, outline="#22c55e", width=2
        )
        self._canvas.create_rectangle(
            x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline="#86efac", width=1
        )

    def _flash_rect(self, l: int, t: int, r: int, b: int) -> None:
        if self._overlay:
            try:
                self._overlay.deiconify()
                self._overlay.lift()
            except Exception:
                pass
        self._show_hover_borders((l, t, r, b))

        def _hide() -> None:
            self._hide_hover_borders()
            if self._overlay:
                try:
                    self._overlay.withdraw()
                except Exception:
                    pass

        if self._root:
            self._root.after(450, _hide)

    def _finalize_smart_pick(self, click_x: int, click_y: int) -> None:
        try:
            self._hide_capture_overlay()
            self._freeze_hover_borders()
            self._phase = "locked"
            self._armed = False
            self._pick_enabled_after = time.time() + 9999
            self._set_status("正在分析元素…")

            import threading
            thr = threading.Thread(target=self._build_locked_pick, args=(click_x, click_y), daemon=True)
            thr.start()
        except Exception as exc:
            self._on_error(str(exc))

    def _build_locked_pick(self, click_x: int, click_y: int) -> None:
        try:
            pick = build_pick_from_smart_click(click_x, click_y, action="click")
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
            from desktop_visual_engine import (
                VISUAL_SELECTOR_TYPE,
                build_visual_step_payload,
            )

            preview_b64 = ""
            try:
                from desktop_precise_locator import capture_rect_preview_b64

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
            from desktop_uia_snapshot import capture_element_snapshot_at_point
            from desktop_visual_engine import VisualStepPayload

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
            if kc and is_fallback:
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
