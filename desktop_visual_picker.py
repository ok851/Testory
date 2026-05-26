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
    "surface": "#f8fafc",
    "border": "#e5e7eb",
    "text": "#111827",
    "muted": "#6b7280",
    "primary": "#dc2626",
    "primary_dark": "#b91c1c",
    "secondary": "#475569",
    "accent": "#2563eb",
    "highlight": "#16a34a",
    "highlight_fill": "#bbf7d0",
}


def _xor_focus_rect(rect: Tuple[int, int, int, int]) -> None:
    """屏幕 XOR 焦点框（不创建窗口、不挡鼠标）。"""
    try:
        import ctypes
        from ctypes import wintypes

        l, t, r, b = rect
        x0, y0 = min(l, r), min(t, b)
        x1, y1 = max(l, r), max(t, b)
        if x1 <= x0:
            x1 = x0 + 4
        if y1 <= y0:
            y1 = y0 + 4

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        rc = RECT(x0, y0, x1, y1)
        hdc = ctypes.windll.user32.GetDC(0)
        if hdc:
            ctypes.windll.user32.DrawFocusRect(hdc, ctypes.byref(rc))
            ctypes.windll.user32.ReleaseDC(0, hdc)
    except Exception:
        pass


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


def build_pick_from_smart_click(
    click_x: int,
    click_y: int,
    *,
    action: str = "click",
    match_threshold: float = 0.72,
) -> Dict[str, Any]:
    """智能点选：UIA 边界生成视觉模板，并内联结构快照（若可用）。"""
    from desktop_uia_snapshot import (
        capture_element_snapshot_at_point,
        rect_with_padding,
    )
    from desktop_visual_engine import build_visual_step_payload

    act = (action or "click").strip().lower()
    snap = capture_element_snapshot_at_point(click_x, click_y, timeout_sec=2.5)
    cx, cy = click_x, click_y
    if snap.screen_center:
        cx, cy = snap.screen_center

    pad = 48
    if snap.bounding_rect:
        l, t, r, b = rect_with_padding(snap.bounding_rect, pad=6, min_side=32)
    else:
        l, t, r, b = cx - pad, cy - pad, cx + pad, cy + pad

    payload = build_visual_step_payload(
        l, t, r, b, cx, cy, match_threshold=match_threshold
    )
    if snap.element_snapshot:
        payload = payload.merge_element_snapshot(snap.element_snapshot)

    label = ""
    if snap.element_label:
        label = f"ListItem_{snap.element_label}" if "list" in (
            snap.control_type or ""
        ).lower() else snap.element_label
    if not label:
        label = f"桌面_{act}@{cx},{cy}"

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
        "label": label,
        "name": snap.element_label or label,
        "capture_mode": CAPTURE_MODE_SMART,
        "rectangle": {"left": l, "top": t, "right": r, "bottom": b},
    }
    if snap.element_snapshot:
        pick["element_snapshot"] = snap.element_snapshot
    if not snap.ok:
        pick["uia_hint"] = snap.message or snap.error_code or "结构信息不可用，已使用视觉"
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
        self._last_hover_xy: Optional[Tuple[int, int]] = None
        self._consume_click_guard = False
        self._pick_enabled_after = 0.0
        self._arm_btn = None
        self._state_lbl = None

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
            fg=_THEME["muted"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9),
            wraplength=400,
            justify="left",
        ).pack(anchor="w", pady=(6, 10))

        mode_row = tk.Frame(outer, bg=_THEME["surface"], highlightbackground=_THEME["border"], highlightthickness=1)
        mode_row.pack(fill="x", pady=(0, 10))
        inner_mode = tk.Frame(mode_row, bg=_THEME["surface"], padx=8, pady=8)
        inner_mode.pack(fill="x")

        self._mode_var = tk.StringVar(value=CAPTURE_MODE_SMART)
        for text, val in (
            ("智能点选（推荐）", CAPTURE_MODE_SMART),
            ("区域框选（仅视觉）", CAPTURE_MODE_REGION),
        ):
            tk.Radiobutton(
                inner_mode,
                text=text,
                variable=self._mode_var,
                value=val,
                command=self._on_mode_change,
                bg=_THEME["surface"],
                fg=_THEME["text"],
                activebackground=_THEME["surface"],
                selectcolor=_THEME["bg"],
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=1)

        state_row = tk.Frame(outer, bg=_THEME["bg"])
        state_row.pack(fill="x", pady=(0, 6))
        self._state_lbl = tk.Label(
            state_row,
            text="● 待命（未开始捕获）",
            fg=_THEME["muted"],
            bg="#f1f5f9",
            font=("Segoe UI", 9, "bold"),
            padx=8,
            pady=4,
        )
        self._state_lbl.pack(anchor="w")

        self._status_var = tk.StringVar(
            value="默认待命：点「开始捕获」或 F2 后，再点目标元素才会录制"
        )
        tk.Label(
            outer,
            textvariable=self._status_var,
            fg=_THEME["secondary"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9),
            wraplength=400,
            justify="left",
        ).pack(anchor="w", pady=(0, 10))

        act_row = tk.Frame(outer, bg=_THEME["bg"])
        act_row.pack(fill="x", pady=(0, 10))
        tk.Label(
            act_row,
            text="动作",
            fg=_THEME["muted"],
            bg=_THEME["bg"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=(0, 8))
        self._action_var = tk.StringVar(value=self._default_action)
        tk.OptionMenu(
            act_row,
            self._action_var,
            "click",
            "double_click",
            "right_click",
            "input",
        ).pack(side="left")

        btn_row = tk.Frame(outer, bg=_THEME["bg"])
        btn_row.pack(fill="x")
        self._arm_btn = tk.Button(
            btn_row,
            text="▶ 开始捕获 (F2)",
            command=self._toggle_arm,
            bg=_THEME["primary"],
            fg="white",
            activebackground=_THEME["primary_dark"],
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=6,
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
            relief="flat",
            highlightbackground=_THEME["border"],
            padx=10,
            pady=6,
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            btn_row,
            text="结束 (ESC)",
            command=self._request_close,
            bg=_THEME["secondary"],
            fg="white",
            relief="flat",
            padx=10,
            pady=6,
            font=("Segoe UI", 9),
            cursor="hand2",
        ).pack(side="left")

        tk.Label(
            outer,
            text="F2 切换捕获 · 每次录完自动暂停 · ESC 结束会话 · 智能点选=单击元素",
            fg=_THEME["muted"],
            bg=_THEME["bg"],
            font=("Segoe UI", 8),
            wraplength=400,
            justify="left",
        ).pack(anchor="w", pady=(10, 0))

        self._build_overlay()
        self._on_message("捕获器已就绪（待命）；点「开始捕获」后再点目标元素")
        self._refresh_arm_ui()
        self._root.bind("<Escape>", lambda _e: self._request_close())
        self._root.bind("<F2>", lambda _e: self._toggle_arm())
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

        self._overlay = tk.Toplevel(self._root)
        self._overlay.withdraw()
        self._overlay.overrideredirect(True)
        self._overlay.geometry(f"{self._sw}x{self._sh}+0+0")
        self._overlay.configure(bg=_TRANSPARENT_KEY)
        try:
            self._overlay.attributes("-topmost", True)
            self._overlay.attributes("-transparentcolor", _TRANSPARENT_KEY)
        except Exception:
            pass
        self._canvas = tk.Canvas(
            self._overlay,
            width=self._sw,
            height=self._sh,
            highlightthickness=0,
            bg=_TRANSPARENT_KEY,
        )
        self._canvas.pack(fill="both", expand=True)

    def _show_overlay(self) -> None:
        if self._overlay:
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
        self._hover_rect = None

    def _hide_hover_borders(self) -> None:
        if self._hover_rect:
            _xor_focus_rect(self._hover_rect)
        self._hover_rect = None

    def _clear_hover_highlight(self) -> None:
        self._hide_hover_borders()
        if self._phase == "drag" or self._phase == "click_offset":
            return

    def _show_hover_borders(self, rect: Tuple[int, int, int, int]) -> None:
        l, t, r, b = rect
        pad = 3
        framed = (min(l, r) - pad, min(t, b) - pad, max(l, r) + pad, max(t, b) + pad)
        if self._hover_rect == framed:
            return
        if self._hover_rect:
            _xor_focus_rect(self._hover_rect)
        _xor_focus_rect(framed)
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
        self._hide_overlay()
        self._hide_hover_borders()
        self._last_hover_xy = None
        if self._capture_mode == CAPTURE_MODE_SMART:
            self._set_status(
                "捕获中：移动鼠标可高亮元素边框，在目标上单击一次确认"
            )
        else:
            self._set_status("捕获中：拖拽框选区域，再在框内点击定操作点")
        self._refresh_arm_ui()
        self._notify_armed_change()

    def _disarm_pick(self) -> None:
        self._armed = False
        self._consume_click_guard = False
        self._phase = "free"
        self._pending_drag = False
        self._drag_start = None
        self._drag_rect = None
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
        if label:
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
                from desktop_uia_snapshot import peek_element_at_point

                res = peek_element_at_point(x, y, timeout_sec=0.22)
                if res.ok:
                    rect = res.bounding_rect
                    if res.element_label:
                        ct = res.control_type or "Control"
                        label = f"{res.element_label} ({ct})"
            except Exception:
                pass
            finally:
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

        esc = _escape_pressed()
        if esc and not self._prev_escape:
            self._request_close()
            return
        self._prev_escape = esc

        f2 = _f2_pressed()
        if f2 and not self._prev_f2:
            self._toggle_arm()
        self._prev_f2 = f2

        if not self._armed:
            self._consume_click_guard = False
            self._prev_lbutton = _lbutton_down()
            self._root.after(16, self._poll_input)
            return

        x, y = _cursor_pos()
        down = _lbutton_down()
        smart = self._capture_mode == CAPTURE_MODE_SMART

        if self._consume_click_guard:
            if down:
                self._prev_lbutton = True
                self._pending_drag = False
                self._drag_start = None
                self._root.after(16, self._poll_input)
                return
            self._consume_click_guard = False
            self._prev_lbutton = False
            self._pick_enabled_after = time.time() + 0.15

        if smart and self._phase == "free" and not down:
            self._schedule_hover_peek(x, y)

        if self._phase == "free":
            if down and not self._prev_lbutton:
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
                    else:
                        self._finalize_smart_pick(x, y)
                    self._phase = "free"
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
            x0, y0, x1, y1, outline=_THEME["primary"], width=2, dash=(5, 3)
        )
        self._canvas.create_rectangle(
            x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline="#fecaca", width=1
        )

    def _flash_rect(self, l: int, t: int, r: int, b: int) -> None:
        self._show_hover_borders((l, t, r, b))

        def _hide() -> None:
            if not self._armed:
                self._hide_hover_borders()

        if self._root:
            self._root.after(450, _hide)

    def _finalize_smart_pick(self, click_x: int, click_y: int) -> None:
        try:
            action = (self._action_var.get() if self._action_var else "click").strip()
            pick = build_pick_from_smart_click(click_x, click_y, action=action)
            rect = pick.get("rectangle") or {}
            self._flash_rect(
                int(rect.get("left", click_x - 32)),
                int(rect.get("top", click_y - 32)),
                int(rect.get("right", click_x + 32)),
                int(rect.get("bottom", click_y + 32)),
            )
            hint = pick.pop("uia_hint", None)
            self._on_record(
                {
                    "pick": pick,
                    "action": action,
                    "click_x": int((pick.get("pick_point") or {}).get("x", click_x)),
                    "click_y": int((pick.get("pick_point") or {}).get("y", click_y)),
                }
            )
            msg = f"已捕获：{pick.get('label', '')} @ ({click_x},{click_y})"
            if hint:
                msg += f"（{hint}）"
            self._set_status(msg)
            self._pause_after_record()
        except Exception as exc:
            self._on_error(str(exc))

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
            if kc:
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
    desc = label if label else f"桌面：{act} @ ({cx},{cy})"
    if mode == CAPTURE_MODE_REGION and "视觉" not in desc:
        desc = f"视觉：{desc}"
    return {
        "action": act,
        "automation_layer": "desktop",
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": sv,
        "input_value": (input_value or "").strip(),
        "compare_type": "",
        "description": desc,
        "desktop_spec": pick.get("desktop_spec") or {},
        "locator_candidates": pick.get("locator_candidates") or [],
        "record_meta": {"pick": pick, "visual": True, "capture_mode": mode},
    }
