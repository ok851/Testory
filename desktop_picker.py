# -*- coding: utf-8 -*-
"""
Windows 桌面控件拾取 / 录制器（悬浮工具条 + 鼠标点选 UIA 控件）。

与 Web 拾取器类似：用户无需手写定位值；点选后自动生成 automation_id / name / uia_path。
录制模式下每次点选追加一条桌面步骤（单击/双击/输入/验证码）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_PICKER_AVAILABLE = sys.platform == "win32"

RECORD_ACTION_CHOICES: List[Tuple[str, str]] = [
    ("click", "单击"),
    ("double_click", "双击"),
    ("input", "输入文字"),
    ("verify", "验证码"),
]

VERIFY_TYPE_CHOICES: List[Tuple[str, str]] = [
    ("auto", "自动识别"),
    ("slider", "滑动方块"),
    ("image", "点击图片文字"),
]

_session_lock = threading.Lock()
_session: Dict[str, Any] = {
    "active": False,
    "record_mode": False,
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
    import ctypes

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt_hwnd = ctypes.windll.user32.WindowFromPoint(_POINT(x, y))
    if pt_hwnd and int(pt_hwnd) in toolbar_hwnds:
        return None

    from pywinauto import Desktop
    from pywinauto.controls.uiawrapper import UIAWrapper

    from desktop_automation import sync_desktop_attach_from_spec, _get_worker

    spec = _session_snapshot().get("desktop_spec") or {}
    sync_desktop_attach_from_spec(spec)

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
    }
    return pick


def _action_label(action: str) -> str:
    for k, label in RECORD_ACTION_CHOICES:
        if k == action:
            return label
    return action or "操作"


def _append_recorded_step(
    pick: Dict[str, Any],
    *,
    action: str = "click",
    input_value: str = "",
    compare_type: str = "",
) -> Dict[str, Any]:
    spec = _session_snapshot().get("desktop_spec") or {}
    act = (action or "click").strip().lower()
    iv = (input_value or "").strip()
    ct = (compare_type or "").strip()
    if act == "verify" and not iv:
        iv = ct or "auto"
        ct = iv
    label = pick.get("label") or "控件"
    desc = f"录制：{_action_label(act)}「{label}」"
    if act == "input" and iv:
        desc += f" → {iv[:40]}"
    elif act == "verify":
        desc += f"（{iv or 'auto'}）"
    step = {
        "action": act,
        "automation_layer": "desktop",
        "selector_type": pick.get("selector_type") or "automation_id",
        "selector_value": pick.get("selector_value") or "",
        "input_value": iv,
        "compare_type": ct,
        "description": desc,
        "desktop_spec": spec,
        "record_meta": {"pick": pick},
    }
    with _session_lock:
        recorded = list(_session.get("recorded_steps") or [])
        recorded.append(step)
        _session["recorded_steps"] = recorded
        _session["last_pick"] = {**pick, "record_action": act, "input_value": iv, "compare_type": ct}
        _session["message"] = f"已录制第 {len(recorded)} 步（{_action_label(act)}）"
    return step


class _DesktopPickerUI(threading.Thread):
    """悬浮工具条线程（tkinter + 低级鼠标钩子）。"""

    def __init__(
        self,
        desktop_spec: Dict[str, Any],
        record_mode: bool,
        *,
        default_action: str = "click",
        default_input: str = "",
        default_verify_type: str = "auto",
    ):
        super().__init__(daemon=True, name="desktop-picker-ui")
        self._desktop_spec = dict(desktop_spec or {})
        self._record_mode = bool(record_mode)
        self._default_action = (default_action or "click").strip().lower()
        self._default_input = (default_input or "").strip()
        self._default_verify_type = (default_verify_type or "auto").strip().lower()
        self._armed = False
        self._hook_id = None
        self._toolbar_hwnds: set = set()
        self._root = None
        self._stop_flag = False
        self._action_var = None
        self._input_var = None
        self._verify_var = None
        self._input_row = None
        self._verify_row = None

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
                ms = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = int(ms.pt.x), int(ms.pt.y)
                try:
                    pick = _pick_control_at(x, y, self_ref._toolbar_hwnds)
                    if pick and self_ref._root:
                        self_ref._root.after(0, lambda p=pick: self_ref._finish_pick(p))
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

    def _sync_buttons(self) -> None:
        if not self._root or not self._root.winfo_exists():
            return
        if hasattr(self, "_btn_arm") and self._btn_arm.winfo_exists():
            if self._armed:
                self._btn_arm.config(text="取消拾取", bg="#ef4444")
            else:
                self._btn_arm.config(text="拾取控件", bg="#3b82f6")
        if hasattr(self, "_tip") and self._tip.winfo_exists():
            if self._armed:
                act = (self._action_var.get() if self._action_var else "click")
                self._tip.config(text=f"请在目标窗口点击控件（将录制：{_action_label(act)}）…")
            elif self._record_mode:
                n = len(_session_snapshot().get("recorded_steps") or [])
                self._tip.config(text=f"录制模式 · 已录 {n} 步 · 选操作类型后点「拾取控件」")
            else:
                self._tip.config(text="点「拾取控件」后在桌面点击目标")
        self._sync_action_rows()

    def _sync_action_rows(self) -> None:
        if not self._action_var:
            return
        act = (self._action_var.get() or "click").strip().lower()
        if self._input_row:
            self._input_row.pack(fill="x", pady=(4, 0)) if act == "input" else self._input_row.pack_forget()
        if self._verify_row:
            self._verify_row.pack(fill="x", pady=(4, 0)) if act == "verify" else self._verify_row.pack_forget()

    def _finish_pick(self, pick: Dict[str, Any]) -> None:
        act = (self._action_var.get() if self._action_var else self._default_action).strip().lower()
        iv = ""
        ct = ""
        if act == "input":
            iv = (self._input_var.get() if self._input_var else self._default_input).strip()
            if not iv:
                from tkinter import messagebox

                messagebox.showwarning("录制输入", "请先在工具条填写「输入内容」", parent=self._root)
                return
        elif act == "verify":
            iv = (self._verify_var.get() if self._verify_var else self._default_verify_type).strip() or "auto"
            ct = iv
        if self._record_mode:
            _append_recorded_step(pick, action=act, input_value=iv, compare_type=ct)
        else:
            _set_session(
                last_pick={**pick, "record_action": act, "input_value": iv, "compare_type": ct},
                message="已拾取控件",
            )
        self._armed = False
        _set_session(armed=False, message=_session_snapshot().get("message") or "已拾取")
        self._sync_buttons()

    def _toggle_arm(self) -> None:
        self._armed = not self._armed
        _set_session(armed=self._armed, error="")
        if self._armed and not self._hook_id:
            try:
                self._install_hook()
            except Exception as exc:
                self._armed = False
                _set_session(armed=False, error=f"鼠标钩子安装失败: {exc}")
        self._sync_buttons()

    def _on_close(self) -> None:
        self._stop_flag = True
        self._armed = False
        _uninstall_hook = self._uninstall_hook
        _uninstall_hook()
        _set_session(active=False, armed=False, picker_closed=True, message="录制器已关闭")
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass

    def run(self) -> None:
        if not _PICKER_AVAILABLE:
            _set_session(active=False, error="仅支持 Windows")
            return

        try:
            from desktop_automation import sync_desktop_attach_from_spec

            sync_desktop_attach_from_spec(self._desktop_spec)
        except Exception as exc:
            _set_session(active=False, error=str(exc))
            return

        import tkinter as tk

        root = tk.Tk()
        self._root = root
        root.title("HuFirst 桌面拾取")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.configure(bg="#1f2937", padx=10, pady=8)
        try:
            root.update_idletasks()
            sw = root.winfo_screenwidth()
            root.geometry(f"+{max(0, sw - 360)}+24")
        except Exception:
            pass

        bar = tk.Frame(root, bg="#1f2937")
        bar.pack(fill="x")
        title = tk.Label(
            bar,
            text="桌面录制" if self._record_mode else "桌面拾取",
            fg="#f9fafb",
            bg="#1f2937",
            font=("Segoe UI", 10, "bold"),
        )
        title.pack(anchor="w")
        self._tip = tk.Label(
            bar,
            text="点「拾取控件」后在桌面点击目标",
            fg="#cbd5e1",
            bg="#1f2937",
            font=("Segoe UI", 9),
            wraplength=320,
            justify="left",
        )
        self._tip.pack(anchor="w", pady=(4, 6))

        act_row = tk.Frame(bar, bg="#1f2937")
        act_row.pack(fill="x", pady=(0, 4))
        tk.Label(act_row, text="录制操作", fg="#94a3b8", bg="#1f2937", font=("Segoe UI", 8)).pack(anchor="w")
        self._action_var = tk.StringVar(value=self._default_action)
        act_menu = tk.OptionMenu(
            act_row,
            self._action_var,
            *[k for k, _ in RECORD_ACTION_CHOICES],
        )
        act_menu.config(
            bg="#374151",
            fg="white",
            activebackground="#4b5563",
            highlightthickness=0,
            font=("Segoe UI", 9),
        )
        act_menu["menu"].config(bg="#374151", fg="white")
        act_menu.pack(anchor="w", fill="x")
        self._action_var.trace_add("write", lambda *_: self._sync_action_rows())

        self._input_row = tk.Frame(bar, bg="#1f2937")
        tk.Label(self._input_row, text="输入内容", fg="#94a3b8", bg="#1f2937", font=("Segoe UI", 8)).pack(anchor="w")
        self._input_var = tk.StringVar(value=self._default_input)
        tk.Entry(
            self._input_row,
            textvariable=self._input_var,
            bg="#111827",
            fg="#f9fafb",
            insertbackground="white",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(2, 0))

        self._verify_row = tk.Frame(bar, bg="#1f2937")
        tk.Label(self._verify_row, text="验证码类型", fg="#94a3b8", bg="#1f2937", font=("Segoe UI", 8)).pack(anchor="w")
        self._verify_var = tk.StringVar(value=self._default_verify_type)
        vmenu = tk.OptionMenu(
            self._verify_row,
            self._verify_var,
            *[k for k, _ in VERIFY_TYPE_CHOICES],
        )
        vmenu.config(bg="#374151", fg="white", highlightthickness=0, font=("Segoe UI", 9))
        vmenu["menu"].config(bg="#374151", fg="white")
        vmenu.pack(anchor="w", fill="x")
        self._sync_action_rows()

        btn_row = tk.Frame(bar, bg="#1f2937")
        btn_row.pack(fill="x")
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
                self._toolbar_hwnds.add(int(widget.winfo_id()))
            except Exception:
                pass
            for ch in widget.winfo_children():
                _collect_hwnds(ch)

        root.update_idletasks()
        _collect_hwnds(root)

        _set_session(
            active=True,
            picker_closed=False,
            record_mode=self._record_mode,
            desktop_spec=self._desktop_spec,
            message="录制器已启动" if self._record_mode else "拾取器已启动",
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
        try:
            root.protocol("WM_DELETE_WINDOW", self._on_close)
            root.mainloop()
        finally:
            self._uninstall_hook()


_ui_thread: Optional[_DesktopPickerUI] = None


def start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
) -> Dict[str, Any]:
    """启动桌面拾取/录制悬浮窗。"""
    global _ui_thread
    if not desktop_picker_available():
        return {"success": False, "error": "桌面拾取仅支持 Windows，且需安装 pywinauto（见 requirements-windows.txt）"}

    stop_desktop_picker()
    _set_session(
        active=False,
        record_mode=record_mode,
        armed=False,
        desktop_spec=dict(desktop_spec or {}),
        last_pick=None,
        recorded_steps=[],
        _sent_count=0,
        error="",
        picker_closed=False,
        message="",
    )

    if not desktop_spec:
        return {"success": False, "error": "请先选择要录制的窗口（从运行中窗口列表点选）"}

    _ui_thread = _DesktopPickerUI(
        desktop_spec,
        record_mode,
        default_action=record_action,
        default_input=input_value,
        default_verify_type=verify_type,
    )
    _ui_thread.start()
    time.sleep(0.35)
    snap = _session_snapshot()
    if snap.get("error"):
        return {"success": False, "error": snap["error"]}
    return {
        "success": True,
        "record_mode": record_mode,
        "message": snap.get("message") or "已启动",
    }


def stop_desktop_picker() -> Dict[str, Any]:
    """关闭拾取悬浮窗。"""
    global _ui_thread
    with _session_lock:
        was_active = bool(_session.get("active"))
        recorded = list(_session.get("recorded_steps") or [])
        last_pick = _session.get("last_pick")

    if _ui_thread and _ui_thread.is_alive() and _ui_thread._root:
        try:
            _ui_thread._root.after(0, _ui_thread._on_close)
        except Exception:
            pass
    _ui_thread = None

    try:
        from desktop_automation import sync_reset_desktop_automation

        sync_reset_desktop_automation()
    except Exception:
        pass

    _set_session(active=False, armed=False, picker_closed=True)
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
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
) -> Dict[str, Any]:
    return start_desktop_picker(
        desktop_spec,
        record_mode=record_mode,
        record_action=record_action,
        input_value=input_value,
        verify_type=verify_type,
    )


def sync_stop_desktop_picker() -> Dict[str, Any]:
    return stop_desktop_picker()


def sync_get_desktop_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_desktop_picker_status(**kwargs)
