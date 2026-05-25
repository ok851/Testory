# -*- coding: utf-8 -*-
"""
纯视觉框选录制器。

交互设计（高自由度）：
1. 启动后仅显示小悬浮工具条，不铺全屏蒙版，用户可自由切换窗口
2. 用户切到目标应用后，点击「开始框选」进入 armed 状态
3. armed 时在屏幕上拖拽：临时弹出全透明捕获层，仅绘制框线（不暗化屏幕）
4. 框选完成后在框内点击确定操作点；ESC 结束录制
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Optional, Tuple

if sys.platform != "win32":
    raise RuntimeError("desktop_visual_picker 仅支持 Windows")

VISUAL_SELECTOR_TYPE = "visual"
_TRANSPARENT_KEY = "#010101"


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


class VisualRegionPickerOverlay:
    """悬浮工具条 + 按需透明框选层（不暗化全屏）。"""

    def __init__(
        self,
        *,
        on_record: Callable[[Dict[str, Any]], None],
        on_message: Callable[[str], None],
        on_error: Callable[[str], None],
        on_close: Callable[[], None],
        default_action: str = "click",
    ):
        self._on_record = on_record
        self._on_message = on_message
        self._on_error = on_error
        self._on_close = on_close
        self._default_action = (default_action or "click").strip().lower()
        self._root = None
        self._overlay = None
        self._canvas = None
        self._action_var = None
        self._status_var = None
        self._armed = False
        self._drag_start: Optional[Tuple[int, int]] = None
        self._drag_rect: Optional[Tuple[int, int, int, int]] = None
        self._phase = "free"  # free | drag | click_offset
        self._pending_drag = False
        self._prev_lbutton = False
        self._prev_f2 = False
        self._prev_escape = False
        self._stop = False
        self._sw = 1920
        self._sh = 1080

    def run(self) -> None:
        import tkinter as tk

        self._root = tk.Tk()
        self._root.title("框选录制")
        self._root.resizable(False, False)
        self._root.configure(bg="#1e293b")
        try:
            self._root.attributes("-topmost", True)
        except Exception:
            pass

        self._sw = int(self._root.winfo_screenwidth())
        self._sh = int(self._root.winfo_screenheight())
        self._root.geometry("+16+16")

        bar = tk.Frame(self._root, bg="#1e293b", padx=10, pady=8)
        bar.pack(fill="both", expand=True)

        tk.Label(
            bar,
            text="视觉框选录制",
            fg="#f8fafc",
            bg="#1e293b",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        self._status_var = tk.StringVar(
            value="请先切换到目标窗口，再点「开始框选」"
        )
        tk.Label(
            bar,
            textvariable=self._status_var,
            fg="#94a3b8",
            bg="#1e293b",
            font=("Segoe UI", 9),
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(4, 8))

        row1 = tk.Frame(bar, bg="#1e293b")
        row1.pack(fill="x", pady=(0, 6))
        self._action_var = tk.StringVar(value=self._default_action)
        tk.Label(row1, text="动作", fg="#cbd5e1", bg="#1e293b", font=("Segoe UI", 9)).pack(
            side="left", padx=(0, 6)
        )
        tk.OptionMenu(
            row1,
            self._action_var,
            "click",
            "double_click",
            "right_click",
            "input",
        ).pack(side="left")

        row2 = tk.Frame(bar, bg="#1e293b")
        row2.pack(fill="x", pady=(0, 4))
        tk.Button(
            row2,
            text="开始框选 (F2)",
            command=self._arm_pick,
            bg="#2563eb",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            row2,
            text="取消框选",
            command=self._disarm_pick,
            bg="#475569",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
        ).pack(side="left", padx=(0, 6))
        tk.Button(
            row2,
            text="结束 (ESC)",
            command=self._request_close,
            bg="#64748b",
            fg="white",
            relief="flat",
            padx=10,
            pady=4,
        ).pack(side="left")

        tk.Label(
            bar,
            text="提示：工具条不遮挡桌面；armed 后在目标上拖拽框选，框内点击定位置",
            fg="#64748b",
            bg="#1e293b",
            font=("Segoe UI", 8),
            wraplength=360,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        self._build_overlay()
        self._on_message("框选录制已启动：请先切换到目标窗口")
        self._root.bind("<Escape>", lambda _e: self._request_close())
        self._root.bind("<F2>", lambda _e: self._toggle_arm())
        self._poll_input()
        self._root.mainloop()

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

    def _set_status(self, msg: str) -> None:
        if self._status_var:
            self._status_var.set(msg)
        self._on_message(msg)

    def _arm_pick(self) -> None:
        self._armed = True
        self._phase = "free"
        self._drag_start = None
        self._drag_rect = None
        self._hide_overlay()
        self._set_status("已就绪：在目标上按住鼠标拖拽框选区域")

    def _disarm_pick(self) -> None:
        self._armed = False
        self._phase = "free"
        self._pending_drag = False
        self._drag_start = None
        self._drag_rect = None
        self._hide_overlay()
        self._set_status("已取消框选；可切换窗口后再次「开始框选」")

    def _toggle_arm(self) -> None:
        if self._armed:
            self._disarm_pick()
        else:
            self._arm_pick()

    def _request_close(self) -> None:
        self._stop = True
        self._hide_overlay()
        self._on_close()
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass

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
            self._prev_lbutton = _lbutton_down()
            self._root.after(16, self._poll_input)
            return

        x, y = _cursor_pos()
        down = _lbutton_down()

        if self._phase == "free":
            if down and not self._prev_lbutton:
                self._drag_start = (x, y)
                self._pending_drag = True
            elif self._pending_drag and self._drag_start and down:
                sx, sy = self._drag_start
                if abs(x - sx) + abs(y - sy) >= 10:
                    self._phase = "drag"
                    self._pending_drag = False
                    self._show_overlay()
                    self._set_status("拖拽中… 松开鼠标完成框选")
            elif not down and self._prev_lbutton:
                self._pending_drag = False
                self._drag_start = None
        elif self._phase == "drag":
            if self._drag_start:
                self._drag_rect = (self._drag_start[0], self._drag_start[1], x, y)
                self._redraw()
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
                self._finalize_record(x, y)
                self._drag_rect = None
                self._phase = "free"
                self._hide_overlay()
                self._set_status("已录制；可继续框选或切换窗口后再次录制")

        self._prev_lbutton = down
        self._root.after(16, self._poll_input)

    def _redraw(self) -> None:
        if not self._canvas or not self._drag_rect:
            return
        self._canvas.delete("all")
        l, t, r, b = self._drag_rect
        x0, y0 = min(l, r), min(t, b)
        x1, y1 = max(l, r), max(t, b)
        self._canvas.create_rectangle(
            x0, y0, x1, y1, outline="#38bdf8", width=2, dash=(6, 3)
        )
        self._canvas.create_rectangle(
            x0, y0, x1, y1, outline="#0ea5e9", width=1
        )

    def _finalize_record(self, click_x: int, click_y: int) -> None:
        if not self._drag_rect:
            return
        l, t, r, b = self._drag_rect
        x0, y0 = min(l, r), min(t, b)
        x1, y1 = max(l, r), max(t, b)
        if not (x0 <= click_x <= x1 and y0 <= click_y <= y1):
            cx = (x0 + x1) // 2
            cy = (y0 + y1) // 2
            click_x, click_y = cx, cy
        try:
            from desktop_visual_engine import (
                VISUAL_SELECTOR_TYPE,
                build_visual_step_payload,
            )

            payload = build_visual_step_payload(
                l, t, r, b, click_x, click_y, match_threshold=0.72
            )
            action = (self._action_var.get() if self._action_var else "click").strip()
            pick = {
                "selector_type": VISUAL_SELECTOR_TYPE,
                "selector_value": payload.to_json(),
                "pick_point": {"x": click_x, "y": click_y},
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
            self._set_status(f"已录制 visual 步骤 @ ({click_x},{click_y})")
        except Exception as exc:
            self._on_error(str(exc))


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
    desc = f"视觉：{act} @ ({cx},{cy})"
    return {
        "action": act,
        "automation_layer": "desktop",
        "selector_type": VISUAL_SELECTOR_TYPE,
        "selector_value": sv,
        "input_value": (input_value or "").strip(),
        "compare_type": "",
        "description": desc,
        "desktop_spec": {},
        "locator_candidates": [],
        "record_meta": {"pick": pick, "visual": True},
    }
