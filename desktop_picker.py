# -*- coding: utf-8 -*-
"""
Windows 桌面控件拾取 / 录制器（悬浮工具条 + 鼠标点选 UIA 控件）。

拾取模式：点「拾取控件」后在目标程序上点击，将定位写入步骤表单。
录制模式：无需预先选择窗口；启动后显示左上角悬浮条与屏幕红框提示。
录制中按住 Ctrl 并点击目标控件即可捕获（自动识别单击/输入等），ESC 结束。
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
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
    "shutdown_requested": False,
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
    proc_dead = _picker_proc is not None and _picker_proc.poll() is not None
    src = _load_session_from_disk() if (_picker_proc and not proc_dead) else None
    if src is None:
        with _session_lock:
            src = dict(_session)
    if proc_dead and not src.get("picker_closed"):
        src = {
            **src,
            "active": False,
            "picker_closed": True,
            "error": src.get("error") or "桌面捕获窗口已退出，请重新启动捕获",
        }
    return {
        "active": bool(src.get("active")),
        "record_mode": bool(src.get("record_mode")),
        "unified_mode": bool(src.get("unified_mode")),
        "recording": bool(src.get("recording")),
        "paused": bool(src.get("paused")),
        "armed": bool(src.get("armed")),
        "last_pick": src.get("last_pick"),
        "recorded_steps": list(src.get("recorded_steps") or []),
        "error": src.get("error") or "",
        "picker_closed": bool(src.get("picker_closed")),
        "message": src.get("message") or "",
        "desktop_spec": dict(src.get("desktop_spec") or {}),
    }


def _session_file_path() -> Path:
    raw = (os.environ.get("UAT_DESKTOP_PICKER_SESSION") or "").strip()
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "uat_desktop_picker_session.json"


def _is_picker_child_process() -> bool:
    return os.environ.get("UAT_PICKER_CHILD") == "1"


def _persist_session_to_disk() -> None:
    try:
        with _session_lock:
            payload = dict(_session)
        _session_file_path().write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _load_session_from_disk() -> Optional[Dict[str, Any]]:
    path = _session_file_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _clear_last_pick_on_disk() -> None:
    data = _load_session_from_disk()
    if not data:
        return
    if not data.get("last_pick"):
        return
    data["last_pick"] = None
    try:
        _session_file_path().write_text(
            json.dumps(data, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _set_session(**kwargs: Any) -> None:
    with _session_lock:
        _session.update(kwargs)
    if _is_picker_child_process():
        _persist_session_to_disk()


def _clear_pick_buffers() -> None:
    _set_session(last_pick=None, error="")


def _reset_attach_tracking() -> None:
    global _last_attach_spec_key
    _last_attach_spec_key = None


_BROWSER_CLASS_HINTS = (
    "Chrome_WidgetWin",
    "MozillaWindowClass",
    "ApplicationFrameWindow",
    "IEFrame",
)


def _is_browser_top_window(hwnd: int) -> bool:
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    if not ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256):
        return False
    cls = buf.value or ""
    return any(h in cls for h in _BROWSER_CLASS_HINTS)


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

    spec, title = attachment_spec_for_window(hwnd)
    if (title or "").strip().lower() in ("program manager", "progman"):
        spec["surface"] = "desktop_shell"
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
        desktop_spec_override=spec,
        description=f"附着窗口：{title[:60]}",
    )


def _normalize_desktop_uia_path(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    对齐 RPA 竞品路径：Progman → SHELLDLL_DefView → SysListView32(桌面) → ListItem。
    捕获链若缺少 DefView，在 SysListView32 前自动补一层。
    """
    out = [dict(n) for n in (nodes or [])]
    cls_set = {(n.get("class_name") or "").strip() for n in out}
    if "SHELLDLL_DefView" not in cls_set:
        insert_at = 0
        for i, n in enumerate(out):
            if (n.get("class_name") or "").strip() == "SysListView32":
                insert_at = i
                break
        if insert_at == 0 and out:
            insert_at = max(0, len(out) - 1)
        out.insert(
            insert_at,
            {
                "automation_id": "",
                "name": "",
                "control_type": "Pane",
                "class_name": "SHELLDLL_DefView",
            },
        )
    for n in out:
        ct = (n.get("control_type") or "").strip()
        if ct == "List" and not (n.get("class_name") or "").strip():
            n["class_name"] = "SysListView32"
    return out


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
                    "class_name": (getattr(pei, "class_name", None) or "").strip(),
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

    trimmed = path_nodes[-8:] if len(path_nodes) > 8 else path_nodes
    last_ct = str((trimmed[-1] or {}).get("control_type") or "").lower()
    is_desktop_icon = "listitem" in last_ct or (
        trimmed
        and str((trimmed[0] or {}).get("name") or "").strip() in ("桌面", "Desktop")
    )

    rel_path = [
        n
        for n in trimmed
        if (
            n.get("automation_id")
            or n.get("name")
            or n.get("control_type")
            or n.get("class_name")
        )
    ]
    if is_desktop_icon and len(rel_path) >= 1:
        rel_path = _normalize_desktop_uia_path(rel_path)
        return "uia_path", json.dumps(rel_path, ensure_ascii=False), rel_path

    if aid and len(aid) <= 200 and aid.lower() not in ("", "titlebar"):
        return "automation_id", aid, trimmed
    if name and 0 < len(name) <= 120:
        return "name", name, trimmed

    if len(rel_path) >= 1:
        return "uia_path", json.dumps(rel_path, ensure_ascii=False), rel_path

    try:
        rect = wrapper.rectangle()
        cx = int((rect.left + rect.right) / 2)
        cy = int((rect.top + rect.bottom) / 2)
        return "coordinate", f"{cx},{cy}", trimmed
    except Exception:
        return "coordinate", "0,0", path_nodes


def _hwnd_class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256)
        return (buf.value or "").strip()
    except Exception:
        return ""


def _minimal_desktop_spec_at(x: int, y: int, exclude: set) -> Optional[Dict[str, Any]]:
    """轻量判断是否在桌面 Progman 上（悬停路径避免 attachment_spec_for_window）。"""
    hwnd = _top_level_hwnd_at(x, y, exclude)
    if not hwnd or _hwnd_class_name(hwnd) != "Progman":
        return None
    return {
        "hwnd": int(hwnd),
        "surface": "desktop_shell",
        "window_title": "Program Manager",
        "process": "explorer.exe",
    }


def _desktop_shell_wrapper_at(x: int, y: int, exclude: set) -> Optional[Any]:
    """桌面图标层：点击时解析 ListItem（使用矩形缓存 + 按名解析，避免每次全树扫描）。"""
    spec = _minimal_desktop_spec_at(x, y, exclude)
    if not spec:
        return None
    from desktop_locator import attach_desktop_shell, desktop_listitem_at_screen_point

    try:
        app, win = attach_desktop_shell(spec)
        return desktop_listitem_at_screen_point(x, y, win, spec, app)
    except Exception:
        return None


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

    root_handle = int(spec.get("hwnd") or 0)
    if not root_handle:
        try:
            worker = _get_worker()
            if worker.automation._window is not None:
                root_handle = int(getattr(worker.automation._window, "handle", 0) or 0)
        except Exception:
            pass

    wrapper = _desktop_shell_wrapper_at(x, y, exclude)
    if wrapper is None:
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


def _prewarm_desktop_icon_cache(_spec: Optional[Dict[str, Any]] = None) -> None:
    """后台用 Win32 ListView 预热桌面图标矩形（无 UIA）。"""
    try:
        from desktop_shell_win32 import schedule_win32_desktop_icon_cache_refresh

        schedule_win32_desktop_icon_cache_refresh()
    except Exception:
        pass


def _hover_highlight_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_PICKER_HOVER") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _lbutton_pressed() -> bool:
    import ctypes

    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _control_rect_at(x: int, y: int, exclude: set) -> Optional[Tuple[int, int, int, int]]:
    """
    悬停高亮专用：桌面(Progman/WorkerW)仅图标小矩形；禁止对桌面用窗口外框（会整屏遮罩黑屏）。
    """
    hwnd = _top_level_hwnd_at(x, y, exclude)
    if not hwnd:
        return None
    try:
        from desktop_shell_win32 import (
            desktop_icon_rect_at_win32,
            hwnd_screen_rect,
            is_desktop_root_hwnd,
        )

        if is_desktop_root_hwnd(hwnd):
            return desktop_icon_rect_at_win32(x, y, allow_sync_build=False)
        return hwnd_screen_rect(hwnd)
    except Exception:
        return None


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
    description: str = "",
) -> Dict[str, Any]:
    spec = dict(
        desktop_spec_override
        or (pick or {}).get("desktop_spec")
        or _session_snapshot().get("desktop_spec")
        or {}
    )
    if pick and pick.get("rectangle"):
        try:
            r = pick["rectangle"]
            spec["pick_center"] = (
                f"{int((int(r['left']) + int(r['right'])) / 2)},"
                f"{int((int(r['top']) + int(r['bottom'])) / 2)}"
            )
        except Exception:
            pass
    if pick and pick.get("uia_path"):
        spec["uia_path"] = pick["uia_path"]
    label = (pick or {}).get("label") or (pick or {}).get("name") or ""
    if label:
        spec["target_name"] = str(label).strip()[:120]
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

    desc = (description or "").strip() or f"录制：{_action_label(act)}"
    if not description:
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


def _hook_module_handle() -> int:
    """低级全局钩子须传 NULL；子进程用 GetModuleHandleW(None) 会报 error 126。"""
    return 0


def _configure_user32_hooks() -> None:
    """64 位下为 CallNextHookEx 声明正确签名，避免 LPARAM 溢出。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if getattr(user32, "_uat_picker_hooks_ready", False):
        return
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32._uat_picker_hooks_ready = True


def _hook_lparam_type():
    import ctypes

    return ctypes.c_void_p if ctypes.sizeof(ctypes.c_void_p) >= 8 else ctypes.c_int


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


class _ElementHighlightOverlay:
    """悬停控件高亮边框（按住 Ctrl 移动鼠标时显示）。"""

    BORDER = 2
    COLOR = "#2f80ff"

    def __init__(self, master: Any) -> None:
        self._master = master
        self._tops: List[Any] = []
        self._hwnds: set = set()

    @property
    def hwnds(self) -> set:
        return set(self._hwnds)

    def show_rect(self, left: int, top: int, right: int, bottom: int) -> None:
        if right <= left or bottom <= top:
            self.hide()
            return
        b = self.BORDER
        segments = (
            (left, top, right - left, b),
            (left, bottom - b, right - left, b),
            (left, top, b, bottom - top),
            (right - b, top, b, bottom - top),
        )
        if len(self._tops) == 4:
            ok = True
            for t, (x, y, w, h) in zip(self._tops, segments):
                if w <= 0 or h <= 0:
                    ok = False
                    break
                try:
                    t.geometry(f"{w}x{h}+{x}+{y}")
                except Exception:
                    ok = False
                    break
            if ok:
                return
        self.hide()
        import tkinter as tk

        for x, y, w, h in segments:
            if w <= 0 or h <= 0:
                continue
            t = tk.Toplevel(self._master)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            t.configure(bg=self.COLOR)
            t.geometry(f"{w}x{h}+{x}+{y}")
            self._tops.append(t)
            try:
                hwd = _hwnd_for_tk(t)
                self._hwnds.add(hwd)
                _PICKER_UI_HWNDS.add(hwd)
            except Exception:
                pass

    def hide(self) -> None:
        for t in self._tops:
            try:
                t.destroy()
            except Exception:
                pass
        self._tops = []
        self._hwnds = set()


class _DesktopPickerUI:
    """悬浮工具条（tkinter + 低级鼠标钩子，须在独立进程主线程运行）。"""

    def __init__(
        self,
        desktop_spec: Dict[str, Any],
        record_mode: bool,
        *,
        unified_mode: bool = False,
        prefer_web_clicks: bool = False,
    ):
        self._desktop_spec = dict(desktop_spec or {})
        self._record_mode = bool(record_mode)
        self._unified_mode = bool(unified_mode)
        self._prefer_web_clicks = bool(prefer_web_clicks)
        self._recording = False
        self._paused = False
        self._armed = False
        self._toolbar_hwnds: set = set()
        self._root = None
        self._stop_flag = False
        self._btn_start = None
        self._btn_pause = None
        self._btn_end = None
        self._btn_arm = None
        self._action_var = None
        self._param_var = None
        self._verify_var = None
        self._param_row = None
        self._param_label = None
        self._verify_row = None
        self._action_row = None
        self._kb_hook_id = None
        self._border = None
        self._highlight: Optional[_ElementHighlightOverlay] = None
        self._pick_queue: queue.Queue = queue.Queue()
        self._input_poll_after_id: Optional[str] = None
        self._last_hover_rect: Optional[Tuple[int, int, int, int]] = None
        self._prev_lbutton_down = False
        self._pending_end = False
        self._last_pick_ts = 0.0
        self._last_pick_sig: Optional[Tuple[Any, ...]] = None

    def _exclude_hwnds(self) -> set:
        ex = set(self._toolbar_hwnds) | set(_PICKER_UI_HWNDS)
        if self._highlight:
            ex |= self._highlight.hwnds
        return ex

    def _capture_active(self) -> bool:
        """录制/统一捕获：recording 即可拾取；纯拾取模式仍依赖 armed。"""
        if self._paused:
            return False
        if self._record_mode or self._unified_mode:
            return bool(self._recording)
        return bool(self._armed)

    def _clear_hover_preview(self) -> None:
        self._last_hover_rect = None
        if self._highlight:
            self._highlight.hide()

    def _cancel_input_poll(self) -> None:
        if self._root and self._input_poll_after_id:
            try:
                self._root.after_cancel(self._input_poll_after_id)
            except Exception:
                pass
        self._input_poll_after_id = None

    def _enqueue_pick_at(self, x: int, y: int) -> None:
        self._pick_queue.put((int(x), int(y)))

    def _run_pick_job(self, x: int, y: int) -> None:
        try:
            pick = _pick_control_at(x, y, self._exclude_hwnds())
            if not pick:
                return

            def _finish() -> None:
                try:
                    sig = (
                        pick.get("selector_type"),
                        pick.get("selector_value"),
                        int((pick.get("rectangle") or {}).get("left") or 0),
                        int((pick.get("rectangle") or {}).get("top") or 0),
                    )
                    now = time.time()
                    if (
                        sig == self._last_pick_sig
                        and now - self._last_pick_ts < 0.55
                    ):
                        return
                    self._last_pick_sig = sig
                    self._last_pick_ts = now
                    self._finish_pick(pick)
                except Exception as exc:
                    _set_session(error=str(exc))
                    if not (self._recording or self._unified_mode):
                        self._armed = False
                        _set_session(armed=False)
                    self._sync_buttons()

            if self._root:
                self._root.after(0, _finish)
        except Exception as exc:
            if self._root:
                self._root.after(0, lambda: _set_session(error=str(exc)))

    def _schedule_input_poll(self) -> None:
        """
        定时轮询 Ctrl+左键与悬停高亮。
        不使用 WH_MOUSE_LL 全局鼠标钩子（易导致系统输入卡顿/黑屏）。
        """
        if not self._root:
            return
        self._cancel_input_poll()

        def _tick() -> None:
            if self._stop_flag:
                return
            try:
                active = self._capture_active()
                ctrl = _ctrl_pressed()
                lmb = _lbutton_pressed()

                if active and ctrl and lmb and not self._prev_lbutton_down:
                    import ctypes

                    class _POINT(ctypes.Structure):
                        _fields_ = [
                            ("x", ctypes.c_long),
                            ("y", ctypes.c_long),
                        ]

                    pt = _POINT()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                    x, y = int(pt.x), int(pt.y)
                    if self._prefer_web_clicks:
                        hwnd = _top_level_hwnd_at(x, y, self._toolbar_hwnds)
                        if not (hwnd and _is_browser_top_window(hwnd)):
                            threading.Thread(
                                target=self._run_pick_job,
                                args=(x, y),
                                daemon=True,
                                name="desktop-pick",
                            ).start()
                    else:
                        threading.Thread(
                            target=self._run_pick_job,
                            args=(x, y),
                            daemon=True,
                            name="desktop-pick",
                        ).start()

                self._prev_lbutton_down = lmb

                if (
                    _hover_highlight_enabled()
                    and active
                    and ctrl
                    and not self._paused
                ):
                    import ctypes

                    class _POINT2(ctypes.Structure):
                        _fields_ = [
                            ("x", ctypes.c_long),
                            ("y", ctypes.c_long),
                        ]

                    pt2 = _POINT2()
                    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt2))
                    self._update_hover_highlight(int(pt2.x), int(pt2.y))
                else:
                    self._clear_hover_preview()
            except Exception:
                pass
            if self._root and not self._stop_flag:
                self._input_poll_after_id = self._root.after(360, _tick)

        self._input_poll_after_id = self._root.after(360, _tick)

    def _ensure_capture_window_visible(self) -> None:
        if not self._root:
            return
        try:
            self._root.deiconify()
            self._root.lift()
            self._root.attributes("-topmost", True)
        except Exception:
            pass

    def _update_hover_highlight(self, x: int, y: int) -> None:
        if not self._highlight or self._paused or not _ctrl_pressed():
            self._clear_hover_preview()
            return
        rect = _control_rect_at(x, y, self._exclude_hwnds())
        if not rect:
            self._last_hover_rect = None
            self._highlight.hide()
            return
        if self._last_hover_rect == rect:
            return
        self._last_hover_rect = rect
        self._highlight.show_rect(*rect)

    def _drain_pick_queue(self) -> None:
        """处理由其它模块入队的 dict 拾取结果（鼠标捕获已改为轮询+后台线程）。"""
        while True:
            try:
                item = self._pick_queue.get_nowait()
            except queue.Empty:
                break
            if not isinstance(item, dict):
                continue
            try:
                pick = item
                sig = (
                    pick.get("selector_type"),
                    pick.get("selector_value"),
                    int((pick.get("rectangle") or {}).get("left") or 0),
                    int((pick.get("rectangle") or {}).get("top") or 0),
                )
                now = time.time()
                if (
                    sig == self._last_pick_sig
                    and now - self._last_pick_ts < 0.55
                ):
                    continue
                self._last_pick_sig = sig
                self._last_pick_ts = now
                self._finish_pick(pick)
            except Exception as exc:
                _set_session(error=str(exc))
                if not (self._recording or self._unified_mode):
                    self._armed = False
                    _set_session(armed=False)
                self._sync_buttons()

    def _uninstall_hooks(self) -> None:
        self._uninstall_kb_hook()

    def _install_kb_hook(self) -> None:
        import ctypes
        from ctypes import wintypes

        _configure_user32_hooks()
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

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, _hook_lparam_type())
        def _kb_proc(n_code, w_param, l_param):
            if n_code >= 0 and w_param == WM_KEYDOWN and (
                self_ref._record_mode or self_ref._unified_mode
            ):
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if kb.vkCode == VK_ESCAPE:
                    self_ref._pending_end = True
            return ctypes.windll.user32.CallNextHookEx(self_ref._kb_hook_id, n_code, w_param, l_param)

        self._kb_hook_proc = _kb_proc
        self._kb_hook_id = ctypes.windll.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._kb_hook_proc, _hook_module_handle(), 0
        )

    def _uninstall_kb_hook(self) -> None:
        import ctypes

        if self._kb_hook_id:
            ctypes.windll.user32.UnhookWindowsHookEx(self._kb_hook_id)
            self._kb_hook_id = None

    def _ensure_hooks(self) -> None:
        """仅安装 Esc 键盘钩子；鼠标捕获用轮询，避免 WH_MOUSE_LL 导致黑屏/卡顿。"""
        if not self._kb_hook_id:
            try:
                self._install_kb_hook()
            except Exception as exc:
                _set_session(error=f"键盘钩子安装失败: {exc}")
                raise

    def _sync_buttons(self) -> None:
        if not self._root or not self._root.winfo_exists():
            return
        if self._record_mode or self._unified_mode:
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
                        text="Ctrl + 点击录入；按住 Ctrl 移动可预览；ESC 结束录制"
                    )
            self._sync_param_rows()
        elif self._btn_arm and self._btn_arm.winfo_exists():
            if self._armed:
                self._btn_arm.config(text="取消拾取", bg="#ef4444")
            else:
                self._btn_arm.config(text="拾取控件", bg="#3b82f6")
            if hasattr(self, "_tip") and self._tip.winfo_exists():
                if self._armed:
                    self._tip.config(text="请在目标窗口按住 Ctrl 并点击要拾取的控件…")
                else:
                    self._tip.config(text="点「拾取控件」后 Ctrl + 点击目标")

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

    def _prompt_input_value(self, pick: Dict[str, Any], default: str = "") -> str:
        from tkinter import simpledialog

        self._ensure_capture_window_visible()
        label = pick.get("label") or "控件"
        hint = pick.get("value_text") or ""
        initial = (default or hint or "").strip()
        val = simpledialog.askstring(
            "录制输入",
            f"请输入「{label}」的内容：",
            initialvalue=initial,
            parent=self._root,
        )
        self._ensure_capture_window_visible()
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
        self._armed = True
        self._ensure_capture_window_visible()
        self._sync_buttons()

    def _record_manual_action_from_toolbar(self) -> None:
        """等待/热键：填写参数后按 Enter 写入步骤（已移除「插入步骤」按钮）。"""
        act = self._selected_action_mode()
        if act not in ("wait", "hotkey"):
            return
        iv, ct, ok = self._build_step_params(act, {}, inferred=False)
        if not ok:
            return
        _append_recorded_step(None, action=act, input_value=iv, compare_type=ct, inferred=False)
        _set_session(message=_session_snapshot().get("message") or "已录入")
        self._sync_buttons()

    def _finish_pick(self, pick: Dict[str, Any]) -> None:
        if (self._record_mode or self._unified_mode) and self._recording and not self._paused:
            snap_rec = bool(
                _session_snapshot().get("record_mode") or self._record_mode
            )
            if snap_rec:
                mode = self._selected_action_mode()
                if mode in ("wait", "hotkey"):
                    _set_session(
                        message=f"「{_action_label(mode)}」请填写参数后按 Enter 录入，无需点选控件"
                    )
                    self._sync_buttons()
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
                        armed=True,
                        message="已捕获桌面元素",
                    )
                    self._armed = True
                    self._ensure_capture_window_visible()
            self._sync_buttons()
            return
        if not self._record_mode and not self._unified_mode:
            _set_session(
                last_pick={**pick, "record_action": "click"},
                message="已拾取控件",
            )
            self._armed = False
            _set_session(armed=False, message=_session_snapshot().get("message") or "已拾取")
            if self._highlight:
                self._highlight.hide()
            self._sync_buttons()

    def _toggle_arm(self) -> None:
        self._armed = not self._armed
        _set_session(armed=self._armed, error="")
        if self._armed:
            try:
                self._ensure_hooks()
            except Exception:
                self._armed = False
                _set_session(armed=False)
        self._sync_buttons()

    def _start_recording(self) -> None:
        if self._recording and self._paused:
            self._paused = False
            self._recording = True
            self._armed = True
            self._clear_hover_preview()
            _set_session(recording=True, paused=False, armed=True, error="")
            try:
                self._ensure_hooks()
                self._install_kb_hook()
            except Exception as exc:
                self._recording = False
                self._paused = False
                self._armed = False
                _set_session(
                    recording=False,
                    paused=False,
                    armed=False,
                    error=f"无法启动捕获钩子: {exc}",
                )
                self._sync_buttons()
                return
            if self._border:
                self._border.show()
            _set_session(message="录制中：按住 Ctrl 可预览，Ctrl + 点击录入")
            _prewarm_desktop_icon_cache(self._desktop_spec)
            self._sync_buttons()
            return

        self._recording = True
        self._paused = False
        self._armed = True
        self._clear_hover_preview()
        _set_session(recording=True, paused=False, armed=True, error="")
        try:
            self._ensure_hooks()
        except Exception as exc:
            self._recording = False
            self._armed = False
            _set_session(
                recording=False,
                armed=False,
                error=f"无法启动捕获钩子: {exc}",
            )
            self._sync_buttons()
            return
        if self._border:
            self._border.show()
        _set_session(message="录制中：按住 Ctrl 可预览，Ctrl + 点击录入")
        _prewarm_desktop_icon_cache(self._desktop_spec)
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
        if self._highlight:
            self._highlight.hide()
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
        self._uninstall_hooks()
        if self._border:
            self._border.hide()
        self._clear_hover_preview()
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

    def run_ui(self) -> None:
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

        self._highlight = _ElementHighlightOverlay(root)
        if rec:
            self._border = _ScreenBorderOverlay(root)

        bar = tk.Frame(root, bg=ui_bg)
        bar.pack(fill="x")
        if rec:
            tk.Label(
                bar,
                text="按下  Ctrl  +  点击  捕获元素（桌面 / 统一模式）",
                fg="#111827",
                bg=ui_bg,
                font=("Segoe UI", 11, "bold"),
            ).pack(anchor="w")
            if self._unified_mode:
                tk.Label(
                    bar,
                    text="网页：在浏览器窗口按住 Ctrl 并点击页面元素",
                    fg=ui_sub,
                    bg=ui_bg,
                    font=("Segoe UI", 9),
                ).pack(anchor="w", pady=(2, 0))
            tk.Label(
                bar,
                text="悬停预览：按住 Ctrl 移动鼠标显示边框",
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
            text=(
                "Ctrl + 点击 捕获桌面控件；浏览器内 Ctrl + 点击网页元素；ESC 结束"
                if self._unified_mode
                else (
                    "Ctrl + 点击录入；按住 Ctrl 可预览；ESC 结束"
                    if rec
                    else "点「拾取控件」后 Ctrl + 点击目标"
                )
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
            self._param_entry = tk.Entry(
                self._param_row,
                textvariable=self._param_var,
                bg="#f9fafb",
                fg=ui_fg,
                insertbackground=ui_fg,
                font=("Segoe UI", 9),
            )
            self._param_entry.pack(fill="x", pady=(2, 0))
            self._param_entry.bind(
                "<Return>",
                lambda _e: self._record_manual_action_from_toolbar(),
            )

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
            disk = _load_session_from_disk() or {}
            if disk.get("shutdown_requested"):
                self._on_close()
                return
            if self._pending_end:
                self._pending_end = False
                self._end_recording()
                return
            try:
                self._drain_pick_queue()
            except Exception as exc:
                _set_session(error=str(exc))
            if self._kb_hook_id:
                import ctypes

                msg = ctypes.wintypes.MSG()
                if ctypes.windll.user32.PeekMessageW(
                    ctypes.byref(msg), None, 0, 0, 1
                ):
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            root.after(120, _pump_hook)

        root.after(120, _pump_hook)
        root.after(200, self._schedule_input_poll)
        self._sync_buttons()
        if self._record_mode or self._unified_mode:
            root.after(0, self._clear_hover_preview)
            root.after(50, self._start_recording)
        try:
            root.protocol("WM_DELETE_WINDOW", self._on_close)
            root.mainloop()
        finally:
            self._cancel_input_poll()
            self._uninstall_hooks()
            if self._border:
                try:
                    self._border.hide()
                except Exception:
                    pass
            if self._highlight:
                try:
                    self._highlight.hide()
                except Exception:
                    pass


_picker_proc: Optional[subprocess.Popen] = None
_picker_ui_lock = threading.RLock()


def _request_picker_shutdown() -> None:
    """通知子进程自行关闭 UI（先隐藏高亮边框，再 destroy）。"""
    data = _load_session_from_disk() or {}
    with _session_lock:
        merged = {**dict(_session), **data}
        merged["shutdown_requested"] = True
        merged["active"] = False
        merged["armed"] = False
        merged["recording"] = False
        _session.update(merged)
    try:
        _session_file_path().write_text(
            json.dumps(merged, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def _stop_picker_process(timeout: float = 8.0) -> None:
    """结束桌面拾取子进程（tkinter 必须在子进程主线程，不可在 Flask 线程里跑）。"""
    global _picker_proc
    proc = _picker_proc
    _picker_proc = None
    if not proc:
        return
    if proc.poll() is None:
        _request_picker_shutdown()
        deadline = time.time() + min(2.5, float(timeout))
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.12)
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=3.0)
                except Exception:
                    pass
    _PICKER_UI_HWNDS.clear()


def _picker_child_main(cfg_path: str) -> None:
    """子进程入口：在本进程主线程运行 tkinter。"""
    os.environ["UAT_PICKER_CHILD"] = "1"
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    os.environ["UAT_DESKTOP_PICKER_SESSION"] = str(cfg.get("session_path") or _session_file_path())
    disk = _load_session_from_disk()
    if disk:
        with _session_lock:
            _session.clear()
            _session.update(disk)
    ui = _DesktopPickerUI(
        cfg.get("desktop_spec") or {},
        bool(cfg.get("record_mode")),
        unified_mode=bool(cfg.get("unified_mode")),
        prefer_web_clicks=bool(cfg.get("prefer_web_clicks")),
    )
    ui.run_ui()


def _spawn_picker_process(
    desktop_spec: Dict[str, Any],
    record_mode: bool,
    unified_mode: bool,
    *,
    prefer_web_clicks: bool = False,
) -> None:
    global _picker_proc
    session_path = Path(tempfile.gettempdir()) / "uat_desktop_picker_session.json"
    try:
        session_path.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ["UAT_DESKTOP_PICKER_SESSION"] = str(session_path)
    cfg_path = Path(tempfile.gettempdir()) / "uat_desktop_picker_cfg.json"
    cfg_path.write_text(
        json.dumps(
            {
                "desktop_spec": desktop_spec,
                "record_mode": record_mode,
                "unified_mode": unified_mode,
                "prefer_web_clicks": prefer_web_clicks,
                "session_path": str(session_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = str(Path(__file__).resolve())
    _picker_proc = subprocess.Popen(
        [sys.executable, script, "--picker-child", str(cfg_path)],
        cwd=str(Path(__file__).resolve().parent),
        env={
            **os.environ,
            "UAT_PICKER_CHILD": "1",
            "UAT_DESKTOP_PICKER_SESSION": str(session_path),
        },
    )


def start_desktop_picker(
    desktop_spec: Dict[str, Any],
    *,
    record_mode: bool = False,
    unified_mode: bool = False,
    prefer_web_clicks: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
) -> Dict[str, Any]:
    """启动桌面拾取/录制/统一捕获悬浮窗（独立子进程，避免 tkinter 崩溃）。"""
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
            shutdown_requested=False,
            recorded_steps=[],
            _sent_count=0,
            error="",
            picker_closed=False,
            message="",
        )
        _persist_session_to_disk()

        if not desktop_spec and not record_mode and not unified_mode:
            return {"success": False, "error": "请先选择要操作的应用窗口（从运行中窗口列表点选）"}

        try:
            _spawn_picker_process(
                desktop_spec or {},
                record_mode,
                unified_mode,
                prefer_web_clicks=prefer_web_clicks,
            )
        except Exception as exc:
            return {"success": False, "error": f"无法启动桌面捕获悬浮窗: {exc}"}

        deadline = time.time() + 10.0
        snap: Dict[str, Any] = {}
        while time.time() < deadline:
            time.sleep(0.15)
            if _picker_proc and _picker_proc.poll() is not None:
                err = (_load_session_from_disk() or {}).get("error") or "桌面捕获进程已退出"
                return {"success": False, "error": err}
            snap = _session_snapshot()
            if snap.get("error"):
                _stop_picker_process(timeout=4.0)
                return {"success": False, "error": snap["error"]}
            if snap.get("active") or (snap.get("message") and "启动" in snap.get("message", "")):
                break
        if snap.get("error"):
            return {"success": False, "error": snap["error"]}
        if not (snap.get("active") or snap.get("message")):
            return {
                "success": False,
                "error": "桌面悬浮窗未就绪，请查看是否被安全软件拦截",
            }
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

        disk = _load_session_from_disk()
        _stop_picker_process(timeout=8.0)
        time.sleep(0.1)
        if disk:
            with _session_lock:
                _session.update(disk)

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
            recorded = list(snap.get("recorded_steps") or [])
            sent = int(snap.get("_sent_count") or _session.get("_sent_count") or 0)
            if len(recorded) > sent:
                new_steps = recorded[sent:]
                _session["_sent_count"] = len(recorded)
                if _picker_proc and _picker_proc.poll() is None:
                    disk = _load_session_from_disk()
                    if disk is not None:
                        disk["_sent_count"] = len(recorded)
                        try:
                            _session_file_path().write_text(
                                json.dumps(disk, ensure_ascii=False, default=str),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
        if consume_last_pick and last:
            if _picker_proc and _picker_proc.poll() is None:
                _clear_last_pick_on_disk()
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
    prefer_web_clicks: bool = False,
    record_action: str = "click",
    input_value: str = "",
    verify_type: str = "auto",
) -> Dict[str, Any]:
    return start_desktop_picker(
        desktop_spec,
        record_mode=record_mode,
        unified_mode=unified_mode,
        prefer_web_clicks=prefer_web_clicks,
        record_action=record_action,
        input_value=input_value,
        verify_type=verify_type,
    )


def sync_stop_desktop_picker() -> Dict[str, Any]:
    return stop_desktop_picker()


def sync_get_desktop_picker_status(**kwargs: Any) -> Dict[str, Any]:
    return get_desktop_picker_status(**kwargs)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--picker-child":
        _picker_child_main(sys.argv[2])
    else:
        print("桌面拾取模块需由平台服务调用，或: python desktop_picker.py --picker-child <cfg.json>")
        sys.exit(1)
