# -*- coding: utf-8 -*-
"""
Windows 桌面控件拾取 / 录制器（悬浮工具条 + 鼠标点选 UIA 控件）。

拾取模式：点「拾取控件」后在目标程序上点击，将定位写入步骤表单。
录制模式：无需预先选择窗口；启动后显示左上角悬浮条与屏幕红框提示。
录制中按住 Ctrl 并点击目标控件即可捕获（自动识别单击/输入等），ESC 结束。
"""

from __future__ import annotations

import json
import queue
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_PICKER_AVAILABLE = sys.platform == "win32"

RECORD_ACTION_CHOICES: List[Tuple[str, str]] = [
    ("auto", "自动识别"),
    ("click", "单击"),
    ("double_click", "双击"),
    ("input", "输入"),
    ("right_click", "右键"),
    ("verify", "验证码"),
    ("wait", "等待"),
    ("hotkey", "快捷键"),
]

VERIFY_TYPE_CHOICES: List[Tuple[str, str]] = [
    ("auto", "自动识别"),
    ("slider", "滑动方块"),
    ("image", "点击图片文字"),
]

_INPUT_CONTROL_HINTS = ("edit", "document", "text", "password", "spinner")
_CLICK_CONTROL_HINTS = (
    "button",
    "hyperlink",
    "menuitem",
    "listitem",
    "treeitem",
    "tabitem",
    "checkbox",
    "radiobutton",
    "splitbutton",
    "thumb",
    "cell",
)
_VERIFY_NAME_HINTS = ("验证码", "captcha", "verify", "滑块", "人机验证")

_PICKER_UI_HWNDS: set = set()
_last_attach_spec_key: Optional[Tuple[Any, ...]] = None

_session_lock = threading.Lock()
_session: Dict[str, Any] = {
    "active": False,
    "record_mode": False,
    "unified_mode": False,
    "recording": False,
    "paused": False,
    "armed": False,
    "desktop_spec": {},
    "last_pick": None,
    "recorded_steps": [],
    "error": "",
    "picker_closed": False,
    "message": "",
}


def desktop_picker_available() -> bool:
    if not _PICKER_AVAILABLE:
        return False
    try:
        from desktop_locator import desktop_runtime_available

        return desktop_runtime_available()
    except ImportError:
        return False


def _session_snapshot() -> Dict[str, Any]:
    with _session_lock:
        return {
            "active": bool(_session.get("active")),
            "record_mode": bool(_session.get("record_mode")),
            "unified_mode": bool(_session.get("unified_mode")),
            "recording": bool(_session.get("recording")),
            "paused": bool(_session.get("paused")),
            "armed": bool(_session.get("armed")),
            "last_pick": _session.get("last_pick"),
            "recorded_steps": list(_session.get("recorded_steps") or []),
            "error": _session.get("error") or "",
            "picker_closed": bool(_session.get("picker_closed")),
            "message": _session.get("message") or "",
            "desktop_spec": dict(_session.get("desktop_spec") or {}),
        }


def _set_session(**kwargs: Any) -> None:
    with _session_lock:
        _session.update(kwargs)


def _clear_pick_buffers() -> None:
    _set_session(last_pick=None, error="")


def _reset_attach_tracking() -> None:
    global _last_attach_spec_key
    _last_attach_spec_key = None


def _top_level_hwnd_at(x: int, y: int, exclude: set) -> Optional[int]:
    import ctypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    user32 = ctypes.windll.user32
    GA_ROOT = 2
    h = int(user32.WindowFromPoint(_POINT(x, y)) or 0)
    if not h or h in exclude:
        return None
    root = int(user32.GetAncestor(h, GA_ROOT) or h)
    if root in exclude or not user32.IsWindow(root):
        return None
    return root


def _desktop_spec_at_point(x: int, y: int, exclude: set) -> Dict[str, Any]:
    hwnd = _top_level_hwnd_at(x, y, exclude)
    if not hwnd:
        raise RuntimeError("未识别到有效窗口，请点击应用或桌面上的控件")
    from desktop_discovery import attachment_spec_for_window

    spec, _title = attachment_spec_for_window(hwnd)
    return spec


def _sync_attach_for_spec(spec: Dict[str, Any]) -> None:
    if not spec:
        return
    _set_session(desktop_spec=dict(spec))
    try:
        from desktop_automation import sync_desktop_attach_from_spec

        sync_desktop_attach_from_spec(spec)
    except Exception:
        pass


def _maybe_append_attach_step(spec: Dict[str, Any]) -> None:
    """窗口切换时自动插入 attach_window，便于回放时附着到正确窗口。"""
    global _last_attach_spec_key
    if not spec or not spec.get("hwnd"):
        return
    key = (
        int(spec.get("hwnd") or 0),
        (spec.get("process") or "").strip(),
        (spec.get("window_title") or "").strip(),
    )
    if key == _last_attach_spec_key:
        return
    _last_attach_spec_key = key
    title = (spec.get("window_title") or spec.get("process") or "目标窗口").strip()
    _append_recorded_step(
        None,
        action="attach_window",
        input_value=title,
        description=f"附着窗口：{title[:60]}",
        desktop_spec_override=spec,
    )


def _element_to_locator(wrapper: Any, root_handle: int) -> Tuple[str, str, List[Dict[str, Any]]]:
    """返回 (selector_type, selector_value, uia_path_nodes)。"""
    ei = wrapper.element_info
    aid = (getattr(ei, "automation_id", None) or "").strip()
    name = (getattr(ei, "name", None) or "").strip()
    ct = str(getattr(ei, "control_type", "") or "")

    path_nodes: List[Dict[str, Any]] = []
    w = wrapper
    for _ in range(32):
        try:
            pei = w.element_info
            path_nodes.insert(
                0,
                {
                    "automation_id": (getattr(pei, "automation_id", None) or "").strip(),
                    "name": (getattr(pei, "name", None) or "").strip(),
                    "control_type": str(getattr(pei, "control_type", "") or ""),
                },
            )
            parent = w.parent()
            if parent is None:
                break
            ph = int(getattr(parent, "handle", 0) or 0)
            if ph and ph == root_handle:
                break
            w = parent
        except Exception:
            break

    if aid and len(aid) <= 200 and aid.lower() not in ("", "titlebar"):
        return "automation_id", aid, path_nodes
    if name and 0 < len(name) <= 120:
        return "name", name, path_nodes
    if ct and name:
        return "name", name, path_nodes
    if path_nodes:
        return "uia_path", json.dumps(path_nodes, ensure_ascii=False), path_nodes

    try:
        rect = wrapper.rectangle()
        cx = int((rect.left + rect.right) / 2)
        cy = int((rect.top + rect.bottom) / 2)
        return "coordinate", f"{cx},{cy}", path_nodes
    except Exception:
        return "coordinate", "0,0", path_nodes


def _pick_control_at(x: int, y: int, toolbar_hwnds: set) -> Optional[Dict[str, Any]]:
    exclude = set(toolbar_hwnds) | set(_PICKER_UI_HWNDS)
    pt_hwnd = _top_level_hwnd_at(x, y, exclude)
    if not pt_hwnd:
        return None

    from pywinauto import Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper

    from desktop_automation import _get_worker

    spec = _desktop_spec_at_point(x, y, exclude)
    _sync_attach_for_spec(spec)

    worker = _get_worker()
    root_handle = 0
    try:
        if worker.automation._window is not None:
            root_handle = int(getattr(worker.automation._window, "handle", 0) or 0)
    except Exception:
        pass

    try:
        raw = Desktop(backend="uia").from_point(x, y)
        wrapper = raw if hasattr(raw, "element_info") else UIAWrapper(raw)
    except Exception as exc:
        raise RuntimeError(f"无法识别该位置的控件: {exc}") from exc

    st, sv, path_nodes = _element_to_locator(wrapper, root_handle)
    ei = wrapper.element_info
    label = (getattr(ei, "name", None) or getattr(ei, "automation_id", None) or st or "控件")
    pick = {
        "selector_type": st,
        "selector_value": sv,
        "control_type": str(getattr(ei, "control_type", "") or ""),
        "name": (getattr(ei, "name", None) or "").strip(),
        "automation_id": (getattr(ei, "automation_id", None) or "").strip(),
        "class_name": (getattr(ei, "class_name", None) or "").strip(),
        "rectangle": {
            "left": int(wrapper.rectangle().left),
            "top": int(wrapper.rectangle().top),
            "right": int(wrapper.rectangle().right),
            "bottom": int(wrapper.rectangle().bottom),
        },
        "uia_path": path_nodes,
        "label": str(label)[:80],
        "value_text": "",
    }
    try:
        pick["value_text"] = (wrapper.window_text() or "").strip()[:200]
    except Exception:
        pass
    pick["desktop_spec"] = spec
    pick["window_title"] = spec.get("window_title") or ""
    return pick


def _action_label(action: str) -> str:
    for k, label in RECORD_ACTION_CHOICES:
        if k == action:
            return label
    return action or "操作"


def _control_type_lower(pick: Dict[str, Any]) -> str:
    return str(pick.get("control_type") or "").strip().lower()


def _pick_text_blob(pick: Dict[str, Any]) -> str:
    parts = [
        pick.get("name") or "",
        pick.get("automation_id") or "",
        pick.get("label") or "",
        pick.get("class_name") or "",
    ]
    return " ".join(parts).lower()


def _is_input_like_control(pick: Dict[str, Any]) -> bool:
    ct = _control_type_lower(pick)
    if any(h in ct for h in _INPUT_CONTROL_HINTS):
        return True
    blob = _pick_text_blob(pick)
    return any(k in blob for k in ("输入", "input", "password", "search", "搜索", "query"))


def _is_verify_like_control(pick: Dict[str, Any]) -> bool:
    blob = _pick_text_blob(pick)
    return any(h in blob for h in _VERIFY_NAME_HINTS)


def _infer_record_action(pick: Dict[str, Any]) -> str:
    if _is_verify_like_control(pick):
        return "verify"
    if _is_input_like_control(pick):
        return "input"
    ct = _control_type_lower(pick)
    if "combobox" in ct:
        return "click"
    if any(h in ct for h in _CLICK_CONTROL_HINTS):
        return "click"
    if pick.get("value_text"):
        return "input"
    return "click"


def _append_recorded_step(
    pick: Optional[Dict[str, Any]],
    *,
    action: str = "click",
    input_value: str = "",
    compare_type: str = "",
    inferred: bool = False,
    desktop_spec_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    spec = dict(
        desktop_spec_override
        or (pick or {}).get("desktop_spec")
        or _session_snapshot().get("desktop_spec")
        or {}
    )
    act = (action or "click").strip().lower()
    iv = (input_value or "").strip()
    ct = (compare_type or "").strip()
    label = (pick or {}).get("label") or "控件"

    if act == "verify" and not iv:
        iv = ct or "auto"
        ct = iv
    if act == "wait" and not iv:
        iv = "1"
    if act == "hotkey" and not iv:
        iv = "^v"

    desc = f"录制：{_action_label(act)}"
    if inferred and act != "auto":
        desc += "（自动）"
    if pick and label:
        desc += f"「{label}」"
    if act == "input" and iv:
        desc += f" → {iv[:40]}"
    elif act == "verify":
        desc += f"（{iv or 'auto'}）"
    elif act == "wait":
        desc += f" {iv} 秒"
    elif act == "hotkey" and iv:
        desc += f" {iv}"

    step = {
        "action": act,
        "automation_layer": "desktop",
        "selector_type": (pick or {}).get("selector_type") or "automation_id",
        "selector_value": (pick or {}).get("selector_value") or "",
        "input_value": iv,
        "compare_type": ct,
        "description": desc,
        "desktop_spec": spec,
        "record_meta": {"pick": pick, "inferred": inferred},
    }
    with _session_lock:
        recorded = list(_session.get("recorded_steps") or [])
        recorded.append(step)
        _session["recorded_steps"] = recorded
        if pick:
            _session["last_pick"] = {
                **pick,
                "record_action": act,
                "input_value": iv,
                "compare_type": ct,
            }
        _session["message"] = f"已录制第 {len(recorded)} 步（{_action_label(act)}）"
    return step


def _hwnd_for_tk(widget: Any) -> int:
    import ctypes

    h = int(widget.winfo_id())
    root = int(ctypes.windll.user32.GetAncestor(h, 2) or 0)
    return root or h


def _ctrl_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)


def _shift_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)


class _ScreenBorderOverlay:
    """全屏红色捕获边框（参考 RPA 录制提示）。"""

    BORDER = 3
    COLOR = "#e53935"

    def __init__(self, master: Any) -> None:
        self._master = master
        self._tops: List[Any] = []

    def show(self) -> None:
        self.hide()
        sw = self._master.winfo_screenwidth()
        sh = self._master.winfo_screenheight()
        b = self.BORDER
        import tkinter as tk

        for x, y, w, h in (
            (0, 0, sw, b),
            (0, sh - b, sw, b),
            (0, 0, b, sh),
            (sw - b, 0, b, sh),
        ):
            t = tk.Toplevel(self._master)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            t.configure(bg=self.COLOR)
            t.geometry(f"{w}x{h}+{x}+{y}")
            self._tops.append(t)
            _PICKER_UI_HWNDS.add(_hwnd_for_tk(t))

    def hide(self) -> None:
        for t in self._tops:
            try:
                t.destroy()
            except Exception:
                pass
        self._tops = []


class _DesktopPickerUI(threading.Thread):
    """悬浮工具条线程（tkinter + 低级鼠标钩子）。"""

    def __init__(
        self,
        desktop_spec: Dict[str, Any],
        record_mode: bool,
        *,
        unified_mode: bool = False,
    ):
        super().__init__(daemon=True, name="desktop-picker-ui")
        self._desktop_spec = dict(desktop_spec or {})
        self._record_mode = bool(record_mode)
        self._unified_mode = bool(unified_mode)
        self._recording = False
        self._paused = False
        self._armed = False
        self._hook_id = None
        self._toolbar_hwnds: set = set()
        self._root = None
        self._stop_flag = False
        self._btn_start = None
        self._btn_pause = None
        self._btn_end = None
        self._btn_arm = None
        self._btn_insert = None
        self._action_var = None
        self._param_var = None
        self._verify_var = None
        self._param_row = None
        self._param_label = None
        self._verify_row = None
        self._action_row = None
        self._kb_hook_id = None
        self._border = None
        self._pick_queue: queue.Queue = queue.Queue()

    def _drain_pick_queue(self) -> None:
        while True:
            try:
                pick = self._pick_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._finish_pick(pick)
            except Exception as exc:
                _set_session(error=str(exc), armed=False)
                self._armed = False
                if self._root:
                    self._root.after(0, self._sync_buttons)

    def _install_hook(self) -> None:
        import ctypes
        from ctypes import wintypes

        WH_MOUSE_LL = 14
        WM_LBUTTONDOWN = 0x0201

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        self_ref = self

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        def _proc(n_code, w_param, l_param):
            if n_code >= 0 and w_param == WM_LBUTTONDOWN and self_ref._armed:
                # 纯桌面录制模式需 Ctrl+点击；统一元素捕获在录制中可直接点击
                need_ctrl = (
                    self_ref._record_mode
                    and not self_ref._unified_mode
                    and self_ref._recording
                    and not _ctrl_pressed()
                )
                if need_ctrl:
                    return ctypes.windll.user32.CallNextHookEx(
                        self_ref._hook_id, n_code, w_param, l_param
                    )
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = int(ms.pt.x), int(ms.pt.y)
                try:
                    pick = _pick_control_at(x, y, self_ref._toolbar_hwnds)
                    if pick and self_ref._root:
                        self_ref._pick_queue.put(pick)
                        self_ref._root.after(0, self_ref._drain_pick_queue)
                except Exception as exc:
                    _set_session(error=str(exc), armed=False)
                    self_ref._armed = False
                    if self_ref._root:
                        self_ref._root.after(0, self_ref._sync_buttons)
            return ctypes.windll.user32.CallNextHookEx(self_ref._hook_id, n_code, w_param, l_param)

        self._hook_proc = _proc
        self._hook_id = ctypes.windll.user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._hook_proc, ctypes.windll.kernel32.GetModuleHandleW(None), 0
        )

    def _uninstall_hook(self) -> None:
        import ctypes

        if self._hook_id:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None
        self._uninstall_kb_hook()

    def _install_kb_hook(self) -> None:
        import ctypes
        from ctypes import wintypes

        if self._kb_hook_id:
            return
        WH_KEYBOARD_LL = 13
        WM_KEYDOWN = 0x0100
        VK_ESCAPE = 0x1B
        self_ref = self

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        def _kb_proc(n_code, w_param, l_param):
            if n_code >= 0 and w_param == WM_KEYDOWN and (
                self_ref._record_mode or self_ref._unified_mode
            ):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == VK_ESCAPE and self_ref._root:
                    self_ref._root.after(0, self_ref._end_recording)
            return ctypes.windll.user32.CallNextHookEx(self_ref._kb_hook_id, n_code, w_param, l_param)

        self._kb_hook_proc = _kb_proc
        self._kb_hook_id = ctypes.windll.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kb_hook_proc, ctypes.windll.kernel32.GetModuleHandleW(None), 0
        )

    def _uninstall_kb_hook(self) -> None:
        import ctypes

        if self._kb_hook_id:
            ctypes.windll.user32.UnhookWindowsHookEx(self._kb_hook_id)
            self._kb_hook_id = None

    def _ensure_hook(self) -> None:
        if not self._hook_id:
            try:
                self._install_hook()
            except Exception as exc:
                _set_session(error=f"鼠标钩子安装失败: {exc}")
                raise

    def _sync_buttons(self) -> None:
        if not self._root or not self._root.winfo_exists():
            return
        if self._record_mode:
            if self._btn_pause and self._btn_pause.winfo_exists():
                if self._paused:
                    self._btn_pause.config(text="继续录制", state="normal")
                elif self._recording:
                    self._btn_pause.config(text="暂停录制", state="normal")
                else:
                    self._btn_pause.config(text="暂停录制", state="disabled")
            if hasattr(self, "_tip") and self._tip.winfo_exists():
                mode = (self._action_var.get() if self._action_var else "auto")
                mode_tip = (
                    "自动：编辑框→输入、按钮→单击"
                    if mode == "auto"
                    else f"下一步将录为：{_action_label(mode)}"
                )
                if self._recording and not self._paused:
                    n = len(_session_snapshot().get("recorded_steps") or [])
                    self._tip.config(
                        text=f"录制中 · 已录 {n} 步 · Ctrl+点击捕获 · {mode_tip}"
                    )
                elif self._recording and self._paused:
                    n = len(_session_snapshot().get("recorded_steps") or [])
                    self._tip.config(text=f"已暂停 · 已录 {n} 步 · 点「继续录制」恢复")
                else:
                    self._tip.config(
                        text="Ctrl + 点击 捕获元素；识别不准请改「步骤类型」；ESC 结束录制"
                    )
            if self._btn_insert and self._btn_insert.winfo_exists():
                act = (self._action_var.get() if self._action_var else "auto").strip().lower()
                self._btn_insert.config(
                    state="normal" if act in ("wait", "hotkey") else "disabled"
                )
            self._sync_param_rows()
        elif self._btn_arm and self._btn_arm.winfo_exists():
            if self._armed:
                self._btn_arm.config(text="取消拾取", bg="#ef4444")
            else:
                self._btn_arm.config(text="拾取控件", bg="#3b82f6")
            if hasattr(self, "_tip") and self._tip.winfo_exists():
                if self._armed:
                    self._tip.config(text="请在目标窗口点击要拾取的控件…")
                else:
                    self._tip.config(text="点「拾取控件」后在桌面点击目标")

    def _selected_action_mode(self) -> str:
        return (self._action_var.get() if self._action_var else "auto").strip().lower() or "auto"

    def _sync_param_rows(self) -> None:
        if not self._action_var:
            return
        act = self._selected_action_mode()
        param_labels = {
            "input": "预填输入（可留空，点选后弹窗填写）",
            "wait": "等待秒数",
            "hotkey": "快捷键（如 ^c、%+{F4}）",
        }
        if self._param_row:
            if act in param_labels:
                self._param_row.pack(fill="x", pady=(4, 0))
                if self._param_label:
                    self._param_label.config(text=param_labels[act])
            else:
                self._param_row.pack_forget()
        if self._verify_row:
            if act == "verify":
                self._verify_row.pack(fill="x", pady=(4, 0))
            else:
                self._verify_row.pack_forget()
        if act in ("wait", "hotkey") and self._param_var:
            defaults = {"wait": "1", "hotkey": "^v"}
            cur = (self._param_var.get() or "").strip()
            if act == "wait" and (not cur or cur == "^v"):
                self._param_var.set(defaults["wait"])
            elif act == "hotkey" and (not cur or re.match(r"^[\d.]+$", cur)):
                self._param_var.set(defaults["hotkey"])

    def _resolve_action_for_pick(self, pick: Dict[str, Any]) -> Tuple[str, bool]:
        mode = self._selected_action_mode()
        if mode == "auto":
            if _ctrl_pressed() and _shift_pressed():
                return "double_click", True
            return _infer_record_action(pick), True
        return mode, False

    def _param_preset(self) -> str:
        return (self._param_var.get() if self._param_var else "").strip()

    def _verify_preset(self) -> str:
        return (self._verify_var.get() if self._verify_var else "auto").strip() or "auto"

    def _brief_show_window(self) -> None:
        if not self._root:
            return
        try:
            self._root.deiconify()
            self._root.lift()
            self._root.attributes("-topmost", True)
        except Exception:
            pass

    def _brief_hide_window(self) -> None:
        if not self._root or not self._recording or self._paused:
            return
        try:
            self._root.iconify()
        except Exception:
            pass

    def _prompt_input_value(self, pick: Dict[str, Any], default: str = "") -> str:
        from tkinter import simpledialog

        self._brief_show_window()
        label = pick.get("label") or "控件"
        hint = pick.get("value_text") or ""
        initial = (default or hint or "").strip()
        val = simpledialog.askstring(
            "录制输入",
            f"请输入「{label}」的内容：",
            initialvalue=initial,
            parent=self._root,
        )
        self._brief_hide_window()
        return (val or "").strip()

    def _build_step_params(
        self, act: str, pick: Dict[str, Any], *, inferred: bool
    ) -> Tuple[str, str, bool]:
        """返回 (input_value, compare_type, ok)。"""
        iv = ""
        ct = ""
        preset = self._param_preset()

        if act == "input":
            iv = preset
            if not iv:
                iv = self._prompt_input_value(pick, pick.get("value_text") or "")
            if not iv:
                return "", "", False
        elif act == "verify":
            iv = self._verify_preset()
            ct = iv
        elif act == "wait":
            iv = preset or "1"
            if not re.match(r"^[\d.]+$", iv):
                iv = "1"
        elif act == "hotkey":
            iv = preset or "^v"
            if not iv:
                return "", "", False
        return iv, ct, True

    def _record_pick_step(self, pick: Dict[str, Any]) -> None:
        spec = dict(pick.get("desktop_spec") or {})
        if (self._record_mode or self._unified_mode) and spec:
            _maybe_append_attach_step(spec)
        act, inferred = self._resolve_action_for_pick(pick)
        iv, ct, ok = self._build_step_params(act, pick, inferred=inferred)
        if not ok:
            _set_session(message="已取消：未填写必要参数")
            return
        _append_recorded_step(
            pick,
            action=act,
            input_value=iv,
            compare_type=ct,
            inferred=inferred,
            desktop_spec_override=spec,
        )
        _set_session(
            armed=True,
            recording=True,
            paused=False,
            message=_session_snapshot().get("message") or "已录制",
        )

    def _insert_manual_step(self) -> None:
        act = self._selected_action_mode()
        if act not in ("wait", "hotkey"):
            return
        iv, ct, ok = self._build_step_params(act, {}, inferred=False)
        if not ok:
            return
        _append_recorded_step(None, action=act, input_value=iv, compare_type=ct, inferred=False)
        _set_session(message=_session_snapshot().get("message") or "已插入")
        self._sync_buttons()

    def _finish_pick(self, pick: Dict[str, Any]) -> None:
        if (self._record_mode or self._unified_mode) and self._recording and not self._paused:
            snap_rec = _session_snapshot().get("record_mode") or self._record_mode
            if snap_rec or self._record_mode:
                mode = self._selected_action_mode()
                if mode in ("wait", "hotkey"):
                    _set_session(
                        message=f"「{_action_label(mode)}」请点「插入步骤」，无需点选控件"
                    )
                    if self._root:
                        self._root.after(0, self._sync_buttons)
                    return
                self._record_pick_step(pick)
            else:
                act, inferred = self._resolve_action_for_pick(pick)
                iv, ct, ok = self._build_step_params(act, pick, inferred=inferred)
                if ok:
                    _set_session(
                        last_pick={
                            **pick,
                            "record_action": act,
                            "input_value": iv,
                            "compare_type": ct,
                            "automation_layer": "desktop",
                        },
                        message="已捕获桌面元素",
                    )
            if self._root:
                self._root.after(0, self._sync_buttons)
            return
        if not self._record_mode and not self._unified_mode:
            _set_session(
                last_pick={**pick, "record_action": "click"},
                message="已拾取控件",
            )
            self._armed = False
            _set_session(armed=False, message=_session_snapshot().get("message") or "已拾取")
            self._sync_buttons()

    def _toggle_arm(self) -> None:
        self._armed = not self._armed
        _set_session(armed=self._armed, error="")
        if self._armed:
            try:
                self._ensure_hook()
            except Exception:
                self._armed = False
                _set_session(armed=False)
        self._sync_buttons()

    def _start_recording(self) -> None:
        if self._recording and self._paused:
            self._paused = False
            self._recording = True
            self._armed = True
            _set_session(recording=True, paused=False, armed=True, error="")
            try:
                self._ensure_hook()
                self._install_kb_hook()
            except Exception:
                return
            if self._border:
                self._border.show()
            _set_session(message="录制中：Ctrl + 点击目标控件")
            self._sync_buttons()
            return

        self._recording = True
        self._paused = False
        self._armed = True
        _set_session(recording=True, paused=False, armed=True, error="")
        try:
            self._ensure_hook()
            self._install_kb_hook()
        except Exception:
            self._recording = False
            self._armed = False
            _set_session(recording=False, armed=False)
            return
        if self._border:
            self._border.show()
        _set_session(message="录制中：Ctrl + 点击目标控件")
        self._sync_buttons()

    def _pause_recording(self) -> None:
        if self._paused:
            self._start_recording()
            return
        if not self._recording:
            return
        self._paused = True
        self._armed = False
        _set_session(recording=True, paused=True, armed=False, message="录制已暂停")
        if self._border:
            self._border.hide()
        if self._root:
            try:
                self._root.deiconify()
                self._root.lift()
                self._root.attributes("-topmost", True)
            except Exception:
                pass
        self._sync_buttons()

    def _end_recording(self) -> None:
        self._on_close()

    def _on_close(self) -> None:
        self._stop_flag = True
        self._armed = False
        self._recording = False
        self._paused = False
        self._uninstall_hook()
        if self._border:
            self._border.hide()
        msg = "录制已结束" if self._record_mode else "拾取器已关闭"
        _set_session(
            active=False,
            armed=False,
            recording=False,
            paused=False,
            picker_closed=True,
            message=msg,
        )
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass

    def run(self) -> None:
        if not _PICKER_AVAILABLE:
            _set_session(active=False, error="仅支持 Windows")
            return

        if self._desktop_spec:
            try:
                from desktop_automation import sync_desktop_attach_from_spec

                sync_desktop_attach_from_spec(self._desktop_spec)
            except Exception as exc:
                _set_session(active=False, error=str(exc))
                return

        import tkinter as tk

        root = tk.Tk()
        self._root = root
        rec = self._record_mode or self._unified_mode
        ui_bg = "#ffffff" if rec else "#1f2937"
        ui_fg = "#111827" if rec else "#f9fafb"
        ui_sub = "#4b5563" if rec else "#cbd5e1"
        if self._unified_mode:
            win_title = "捕获元素接口"
        elif self._record_mode:
            win_title = "桌面录制"
        else:
            win_title = "桌面拾取"
        root.title(win_title)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.configure(bg=ui_bg, padx=12, pady=10)
        try:
            root.update_idletasks()
            root.geometry("+16+16" if rec else f"+{max(0, root.winfo_screenwidth() - 360)}+24")
        except Exception:
            pass

        if rec:
            self._border = _ScreenBorderOverlay(root)

        bar = tk.Frame(root, bg=ui_bg)
        bar.pack(fill="x")
        if rec:
            tk.Label(
                bar,
                text="点击桌面控件即可捕获（统一模式）"
                if self._unified_mode
                else "按下  Ctrl  +  点击  捕获桌面元素",
                fg="#111827",
                bg=ui_bg,
                font=("Segoe UI", 11, "bold"),
            ).pack(anchor="w")
            if self._unified_mode:
                tk.Label(
                    bar,
                    text="网页：在已打开的浏览器窗口直接点击元素",
                    fg=ui_sub,
                    bg=ui_bg,
                    font=("Segoe UI", 9),
                ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                bar,
                text="双击捕获  Ctrl + Shift + 点击",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                bar,
                text="结束捕获  ESC",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=(0, 6))
        title = tk.Label(
            bar,
            text="" if rec else "桌面拾取",
            fg=ui_fg,
            bg=ui_bg,
            font=("Segoe UI", 10, "bold"),
        )
        if not rec:
            title.pack(anchor="w")
        self._tip = tk.Label(
            bar,
            text="点击桌面控件即可捕获；浏览器内直接点击网页元素；ESC 结束"
            if self._unified_mode
            else (
                "Ctrl + 点击 捕获元素；ESC 结束录制"
                if rec
                else "点「拾取控件」后在桌面点击目标"
            ),
            fg=ui_sub,
            bg=ui_bg,
            font=("Segoe UI", 9),
            wraplength=300,
            justify="left",
        )
        self._tip.pack(anchor="w", pady=(0, 6))

        if self._record_mode or self._unified_mode:
            self._action_row = tk.Frame(bar, bg=ui_bg)
            self._action_row.pack(fill="x", pady=(0, 4))
            tk.Label(
                self._action_row,
                text="步骤类型（下一步 Ctrl+点击）",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 8),
            ).pack(anchor="w")
            self._action_var = tk.StringVar(value="auto")
            act_menu = tk.OptionMenu(
                self._action_row,
                self._action_var,
                _action_label("auto"),
            )
            act_menu.config(
                bg="#f3f4f6",
                fg=ui_fg,
                activebackground="#e5e7eb",
                highlightthickness=0,
                font=("Segoe UI", 9),
            )
            m = act_menu["menu"]
            m.delete(0, "end")
            m.config(bg="#f9fafb", fg=ui_fg)

            def _set_action(key: str, label: str) -> None:
                self._action_var.set(key)
                act_menu.config(text=label)
                self._sync_buttons()

            for key, label in RECORD_ACTION_CHOICES:
                m.add_command(label=label, command=lambda k=key, lb=label: _set_action(k, lb))
            act_menu.config(text=_action_label("auto"))
            act_menu.pack(anchor="w", fill="x")

            self._param_row = tk.Frame(bar, bg=ui_bg)
            self._param_label = tk.Label(
                self._param_row,
                text="预填输入",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 8),
            )
            self._param_label.pack(anchor="w")
            self._param_var = tk.StringVar(value="")
            tk.Entry(
                self._param_row,
                textvariable=self._param_var,
                bg="#f9fafb",
                fg=ui_fg,
                insertbackground=ui_fg,
                font=("Segoe UI", 9),
            ).pack(fill="x", pady=(2, 0))

            self._verify_row = tk.Frame(bar, bg=ui_bg)
            tk.Label(
                self._verify_row,
                text="验证码类型",
                fg=ui_sub,
                bg=ui_bg,
                font=("Segoe UI", 8),
            ).pack(anchor="w")
            self._verify_var = tk.StringVar(value="auto")
            vmenu = tk.OptionMenu(self._verify_row, self._verify_var, "自动识别")
            vmenu.config(bg="#f3f4f6", fg=ui_fg, highlightthickness=0, font=("Segoe UI", 9))
            vm = vmenu["menu"]
            vm.delete(0, "end")
            vm.config(bg="#f9fafb", fg=ui_fg)

            def _set_verify(key: str, label: str) -> None:
                self._verify_var.set(key)
                vmenu.config(text=label)

            for key, label in VERIFY_TYPE_CHOICES:
                vm.add_command(label=label, command=lambda k=key, lb=label: _set_verify(k, lb))
            vmenu.pack(anchor="w", fill="x")
            self._sync_param_rows()

        btn_row = tk.Frame(bar, bg=ui_bg)
        btn_row.pack(fill="x")

        if self._record_mode or self._unified_mode:
            self._btn_pause = tk.Button(
                btn_row,
                text="暂停录制",
                command=self._pause_recording,
                bg="#f59e0b",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
                state="disabled",
            )
            self._btn_pause.pack(side="left", padx=(0, 6))
            self._btn_end = tk.Button(
                btn_row,
                text="结束捕获" if self._unified_mode else "结束录制",
                command=self._end_recording,
                bg="#ef4444",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
            )
            self._btn_end.pack(side="left", padx=(0, 6))
            self._btn_insert = tk.Button(
                btn_row,
                text="插入步骤",
                command=self._insert_manual_step,
                bg="#6366f1",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
                state="disabled",
            )
            self._btn_insert.pack(side="left")
        else:
            self._btn_arm = tk.Button(
                btn_row,
                text="拾取控件",
                command=self._toggle_arm,
                bg="#3b82f6",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
            )
            self._btn_arm.pack(side="left", padx=(0, 6))
            tk.Button(
                btn_row,
                text="关闭",
                command=self._on_close,
                bg="#4b5563",
                fg="white",
                relief="flat",
                padx=10,
                pady=4,
                font=("Segoe UI", 9),
            ).pack(side="left")

        def _collect_hwnds(widget) -> None:
            try:
                h = _hwnd_for_tk(widget)
                self._toolbar_hwnds.add(h)
                _PICKER_UI_HWNDS.add(h)
            except Exception:
                pass
            for ch in widget.winfo_children():
                _collect_hwnds(ch)

        root.update_idletasks()
        _collect_hwnds(root)
        root.bind("<Escape>", lambda _e: self._end_recording())

        _set_session(
            active=True,
            picker_closed=False,
            record_mode=self._record_mode,
            unified_mode=self._unified_mode,
            recording=False,
            paused=False,
            desktop_spec=self._desktop_spec,
            message="元素捕获已启动"
            if self._unified_mode
            else ("录制器已启动" if self._record_mode else "拾取器已启动"),
        )

        def _pump_hook() -> None:
            if self._stop_flag:
                return
            if self._hook_id:
                import ctypes

                msg = ctypes.wintypes.MSG()
                while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            root.after(40, _pump_hook)

        root.after(80, _pump_hook)
        self._sync_buttons()
        if self._record_mode or self._unified_mode:
            root.after(0, self._start_recording)
        try:
            root.protocol("WM_DELETE_WINDOW", self._on_close)
            root.mainloop()
        finally:
            self._uninstall_hook()


_ui_thread: Optional[_DesktopPickerUI] = None
_picker_ui_lock = threading.RLock()


def _join_picker_ui_thread(timeout: float = 8.0) -> None:
    """等待拾取悬浮窗线程完全退出，避免快速重启时 tkinter 跨线程析构导致进程崩溃。"""
    global _ui_thread
    t = _ui_thread
    if t and t.is_alive():
        try:
            if t._root:
                t._root.after(0, t._on_close)
        except Exception:
            pass
        t.join(timeout=timeout)
    _ui_thread = None


def start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
) -> Dict[str, Any]:
    """启动桌面拾取/录制/统一捕获悬浮窗。"""
    global _ui_thread
    del record_action, input_value, verify_type
    if not desktop_picker_available():
        return {"success": False, "error": "桌面拾取仅支持 Windows，且需安装 pywinauto（见 requirements-windows.txt）"}

    with _picker_ui_lock:
        stop_desktop_picker()
        _reset_attach_tracking()
        _PICKER_UI_HWNDS.clear()
        _set_session(
            active=False,
            record_mode=record_mode,
            unified_mode=unified_mode,
            recording=False,
            paused=False,
            armed=False,
            desktop_spec=dict(desktop_spec or {}),
            last_pick=None,
            recorded_steps=[],
            _sent_count=0,
            error="",
            picker_closed=False,
            message="",
        )

        if not desktop_spec and not record_mode and not unified_mode:
            return {"success": False, "error": "请先选择要操作的应用窗口（从运行中窗口列表点选）"}

        _ui_thread = _DesktopPickerUI(desktop_spec or {}, record_mode, unified_mode=unified_mode)
        _ui_thread.start()
        deadline = time.time() + 6.0
        snap: Dict[str, Any] = {}
        while time.time() < deadline:
            time.sleep(0.12)
            snap = _session_snapshot()
            if snap.get("error"):
                _join_picker_ui_thread(timeout=4.0)
                return {"success": False, "error": snap["error"]}
            if snap.get("active") or snap.get("message"):
                break
        if snap.get("error"):
            return {"success": False, "error": snap["error"]}
        return {
            "success": True,
            "record_mode": record_mode,
            "message": snap.get("message") or "已启动",
        }


def stop_desktop_picker() -> Dict[str, Any]:
    """关闭拾取悬浮窗。"""
    with _picker_ui_lock:
        with _session_lock:
            was_active = bool(_session.get("active"))
            recorded = list(_session.get("recorded_steps") or [])
            last_pick = _session.get("last_pick")

        _join_picker_ui_thread(timeout=8.0)
        time.sleep(0.15)

        try:
            from desktop_automation import sync_reset_desktop_automation

            sync_reset_desktop_automation()
        except Exception:
            pass

        _set_session(active=False, armed=False, recording=False, paused=False, picker_closed=True)
        return {
            "success": True,
            "stopped": True,
            "recorded_steps": recorded,
            "last_pick": last_pick,
            "was_active": was_active,
        }


def get_desktop_picker_status(*, consume_last_pick: bool = False) -> Dict[str, Any]:
    """供前端轮询；consume_last_pick 为真时返回 last_pick 后清空（避免重复填入）。"""
    snap = _session_snapshot()
    last = snap.get("last_pick")
    new_steps: List[Dict[str, Any]] = []
    with _session_lock:
        if snap.get("record_mode"):
            recorded = list(_session.get("recorded_steps") or [])
            sent = int(_session.get("_sent_count") or 0)
            if len(recorded) > sent:
                new_steps = recorded[sent:]
                _session["_sent_count"] = len(recorded)
        if consume_last_pick and last and _session.get("last_pick") == last:
            _session["last_pick"] = None
            last = None
    out = {
        "success": True,
        **snap,
        "last_pick": last,
        "new_recorded_steps": new_steps,
    }
    if snap.get("picker_closed"):
        out["recorded_steps"] = list(snap.get("recorded_steps") or [])
    return out


def sync_start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
) -> Dict[str, Any]:
    return start_desktop_picker(
        desktop_spec,
        record_mode=record_mode,
        unified_mode=unified_mode,
        record_action=record_action,
        input_value=input_value,
        verify_type=verify_type,
    )


def sync_stop_desktop_picker() -> Dict[str, Any]:
    return stop_desktop_picker()


def sync_get_desktop_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_desktop_picker_status(**kwargs)
