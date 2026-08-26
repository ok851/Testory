# -*- coding: utf-8 -*-
"""
桌面 SysListView32 后台消息点击：不移动物理光标、不受前台窗口遮挡。

适用于桌面图标（ListItem）类步骤；通过 ListView 消息直接双击图标。
Win10/11 上 ListView 项文本常为空，需配合 UIA/视觉坐标走坐标兜底。
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

if sys.platform != "win32":
    raise RuntimeError("desktop_shell_listview 仅支持 Windows")

LVM_GETITEMCOUNT = 0x1004
LVM_GETITEMTEXTW = 0x1073
LVM_GETITEMRECT = 0x100E
LVM_HITTEST = 0x1012
LVM_SUBITEMHITTEST = 0x1039
LVIR_BOUNDS = 0
LVIR_ICON = 1
LVIR_LABEL = 2
LVHT_ONITEMICON = 0x0002
LVHT_ONITEMLABEL = 0x0004
LVHT_ONITEMSTATEICON = 0x0008
LVHT_ONITEM = LVHT_ONITEMICON | LVHT_ONITEMLABEL | LVHT_ONITEMSTATEICON
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002


def shell_message_enabled() -> bool:
    raw = (os.environ.get("DESKTOP_SHELL_MESSAGE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass
class ShellIconTarget:
    listview_hwnd: int
    index: int
    icon_name: str
    client_x: int
    client_y: int
    screen_x: int
    screen_y: int
    screen_rect: Optional[Tuple[int, int, int, int]] = None
    control_type: str = "ListItem"


def _user32():
    import ctypes

    return ctypes.windll.user32


def _kernel32():
    import ctypes

    return ctypes.windll.kernel32


def _window_pid(hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    _user32().GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value or 0)


class _RemoteBuffer:
    """
    跨进程 ListView 消息缓冲。

    绝不能把本进程指针直接 SendMessage 给 explorer 的 SysListView32：
    否则 explorer 在错误地址读写，会卡死桌面（黑屏），恢复时常伴随音量 OSD。
    """

    def __init__(self, hwnd: int, size: int):
        import ctypes

        self._k32 = _kernel32()
        self._hwnd = int(hwnd)
        self._size = int(size)
        self._hprocess = 0
        self.remote = 0
        pid = _window_pid(hwnd)
        if not pid:
            raise OSError("无法获取 ListView 进程 PID")
        access = 0x0008 | 0x0010 | 0x0020 | 0x0400  # VM_OP|VM_READ|VM_WRITE|QUERY
        self._hprocess = int(self._k32.OpenProcess(access, False, pid) or 0)
        if not self._hprocess:
            raise OSError(f"OpenProcess 失败 pid={pid}")
        self.remote = int(
            self._k32.VirtualAllocEx(
                self._hprocess, None, self._size, 0x1000 | 0x2000, 0x04
            )
            or 0
        )
        if not self.remote:
            self.close()
            raise OSError("VirtualAllocEx 失败")

    def write(self, local_ptr, size: Optional[int] = None) -> None:
        import ctypes

        n = int(size if size is not None else self._size)
        written = ctypes.c_size_t(0)
        ok = self._k32.WriteProcessMemory(
            self._hprocess, self.remote, local_ptr, n, ctypes.byref(written)
        )
        if not ok:
            raise OSError("WriteProcessMemory 失败")

    def read(self, local_ptr, size: Optional[int] = None) -> None:
        import ctypes

        n = int(size if size is not None else self._size)
        readn = ctypes.c_size_t(0)
        ok = self._k32.ReadProcessMemory(
            self._hprocess, self.remote, local_ptr, n, ctypes.byref(readn)
        )
        if not ok:
            raise OSError("ReadProcessMemory 失败")

    def close(self) -> None:
        if self.remote and self._hprocess:
            try:
                self._k32.VirtualFreeEx(self._hprocess, self.remote, 0, 0x8000)
            except Exception:
                pass
            self.remote = 0
        if self._hprocess:
            try:
                self._k32.CloseHandle(self._hprocess)
            except Exception:
                pass
            self._hprocess = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _send_listview_struct(hwnd: int, msg: int, wparam: int, struct_obj) -> bool:
    """将结构体放到目标进程再 SendMessageTimeout，避免卡死 explorer。"""
    import ctypes
    from ctypes import wintypes

    user32 = _user32()
    size = ctypes.sizeof(struct_obj)
    # 64 位下结果缓冲必须是指针宽度
    result = ctypes.c_size_t(0)
    try:
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            ctypes.c_size_t,
            ctypes.c_void_p,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = wintypes.BOOL
    except Exception:
        pass
    with _RemoteBuffer(hwnd, size) as remote:
        remote.write(ctypes.byref(struct_obj), size)
        ok = user32.SendMessageTimeoutW(
            int(hwnd),
            int(msg),
            int(wparam),
            remote.remote,
            0x0002,  # SMTO_ABORTIFHUNG
            180,
            ctypes.byref(result),
        )
        if not ok:
            return False
        remote.read(ctypes.byref(struct_obj), size)
        return True


def _get_listview_item_rect(
    listview_hwnd: int, index: int, *, code: int = LVIR_BOUNDS
) -> Optional[Tuple[int, int, int, int]]:
    """返回客户区矩形 (l,t,r,b)。"""
    import ctypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rect = RECT()
    rect.left = int(code)
    try:
        if not _send_listview_struct(int(listview_hwnd), LVM_GETITEMRECT, int(index), rect):
            return None
    except OSError as exc:
        logger.debug("shell_listview GETITEMRECT 跨进程失败: %s", exc)
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _client_rect_to_screen(
    hwnd: int, crect: Tuple[int, int, int, int]
) -> Tuple[int, int, int, int]:
    l, t = _client_to_screen(hwnd, crect[0], crect[1])
    r, b = _client_to_screen(hwnd, crect[2], crect[3])
    return int(l), int(t), int(r), int(b)


def hit_test_listview_item(
    listview_hwnd: int, screen_x: int, screen_y: int
) -> Optional[Tuple[int, Tuple[int, int, int, int], str]]:
    """
    LVM_HITTEST（跨进程安全）：返回 (index, screen_rect, icon_name)。
    """
    import ctypes
    from ctypes import wintypes

    cx, cy = _screen_to_client(int(listview_hwnd), int(screen_x), int(screen_y))

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class LVHITTESTINFO(ctypes.Structure):
        _fields_ = [
            ("pt", POINT),
            ("flags", wintypes.UINT),
            ("iItem", ctypes.c_int),
            ("iSubItem", ctypes.c_int),
        ]

    info = LVHITTESTINFO()
    info.pt.x = int(cx)
    info.pt.y = int(cy)
    try:
        if not _send_listview_struct(int(listview_hwnd), LVM_SUBITEMHITTEST, 0, info):
            info = LVHITTESTINFO()
            info.pt.x = int(cx)
            info.pt.y = int(cy)
            if not _send_listview_struct(int(listview_hwnd), LVM_HITTEST, 0, info):
                return None
    except OSError as exc:
        logger.debug("shell_listview HITTEST 失败: %s", exc)
        return None

    item = int(info.iItem)
    if item < 0:
        return None

    flags = int(info.flags or 0)
    if flags and not (flags & LVHT_ONITEM) and item < 0:
        return None

    crect = _get_listview_item_rect(listview_hwnd, item, code=LVIR_BOUNDS)
    if not crect:
        icon_r = _get_listview_item_rect(listview_hwnd, item, code=LVIR_ICON)
        label_r = _get_listview_item_rect(listview_hwnd, item, code=LVIR_LABEL)
        parts = [p for p in (icon_r, label_r) if p]
        if not parts:
            return None
        crect = (
            min(p[0] for p in parts),
            min(p[1] for p in parts),
            max(p[2] for p in parts),
            max(p[3] for p in parts),
        )
    srect = _client_rect_to_screen(int(listview_hwnd), crect)
    name = _get_listview_item_text(listview_hwnd, item)
    return item, srect, name


def peek_desktop_icon_at_point(
    screen_x: int, screen_y: int, *, allow_ocr: bool = False
) -> Optional[ShellIconTarget]:
    """悬停/捕获：点在桌面图标上时返回紧贴图标的边界与名称。悬停勿开 OCR。"""
    lv = get_desktop_listview_hwnd()
    if not lv:
        return None
    lv_rect = None
    try:
        from modules.desktop.desktop_win32_snapshot import get_window_rect

        lv_rect = get_window_rect(lv)
    except Exception:
        lv_rect = None
    if lv_rect:
        if not (lv_rect[0] <= int(screen_x) <= lv_rect[2] and lv_rect[1] <= int(screen_y) <= lv_rect[3]):
            return None

    hit = hit_test_listview_item(lv, int(screen_x), int(screen_y))
    if not hit:
        return None
    idx, srect, name = hit
    # 悬停路径禁止 OCR（会 DXGI 截屏 + 推理，易卡死）
    if allow_ocr and not name and srect:
        try:
            from modules.desktop.desktop_ocr_locate import locate_element_via_ocr

            cx = (srect[0] + srect[2]) // 2
            cy = (srect[1] + srect[3]) // 2
            ocr = locate_element_via_ocr(
                cx, cy, search_radius=max(48, (srect[3] - srect[1]) // 2 + 24)
            )
            if ocr and (ocr.get("text") or "").strip():
                t = str(ocr["text"]).strip()
                low = t.lower()
                if t and low not in ("folderview", "desktop", "桌面") and "listview" not in low:
                    name = t
        except Exception:
            pass

    cx = (srect[0] + srect[2]) // 2
    cy = (srect[1] + srect[3]) // 2
    cl_x, cl_y = _screen_to_client(lv, cx, cy)
    return ShellIconTarget(
        listview_hwnd=lv,
        index=idx,
        icon_name=name or f"桌面图标#{idx + 1}",
        client_x=cl_x,
        client_y=cl_y,
        screen_x=cx,
        screen_y=cy,
        screen_rect=srect,
        control_type="ListItem",
    )


def capture_desktop_icon_snapshot_at_point(screen_x: int, screen_y: int) -> Optional[dict]:
    """供 capture/peek 使用的结构化桌面图标快照。"""
    target = peek_desktop_icon_at_point(int(screen_x), int(screen_y), allow_ocr=True)
    if not target or not target.screen_rect:
        return None
    name = (target.icon_name or "").strip()
    selector = {
        "anchor_props": "ListItem",
        "key_candidates": [
            {"property": "uia-name", "value": name, "match": "equals"},
        ],
        "parent_chain": [
            {"control_type": "List", "class_name": "SysListView32", "name": "FolderView"},
        ],
        "resolved_via": "shell_listview",
    }
    return {
        "ok": True,
        "element_label": name,
        "control_type": "ListItem",
        "bounding_rect": target.screen_rect,
        "screen_center": (target.screen_x, target.screen_y),
        "element_snapshot": {"selector": selector, "class_name": "ListItem"},
        "index": target.index,
        "listview_hwnd": target.listview_hwnd,
    }


def _match_icon_name(actual: str, expected: str) -> bool:
    a = (actual or "").strip()
    e = (expected or "").strip()
    if not a or not e:
        return False
    if a == e:
        return True
    al, el = a.lower(), e.lower()
    if al == el:
        return True
    if el in al or al in el:
        return True
    return False


def icon_name_from_step(step: dict) -> str:
    from modules.desktop.desktop_hybrid_locator import element_snapshot_for_step, _effect_keyword_from_step

    kw = _effect_keyword_from_step(step)
    if kw:
        return kw
    snap = element_snapshot_for_step(step)
    if not snap:
        return ""
    sel = snap.get("selector") or snap
    for cand in sel.get("key_candidates") or []:
        prop = (cand.get("property") or "").strip().lower()
        if prop in ("uia-name", "name"):
            val = (cand.get("value") or "").strip()
            if val and val not in ("桌面", "Desktop", "桌面 1"):
                return val
    chain = sel.get("parent_chain") or []
    if chain:
        nm = (chain[-1].get("name") or "").strip()
        if nm:
            return nm
    return ""


def is_desktop_listitem_step(step: dict) -> bool:
    from modules.desktop.desktop_hybrid_locator import element_snapshot_for_step

    if not icon_name_from_step(step):
        return False
    snap = element_snapshot_for_step(step)
    if snap:
        sel = snap.get("selector") or snap
        anchor = (sel.get("anchor_props") or "").lower()
        if "listitem" in anchor:
            return True
        for node in sel.get("parent_chain") or []:
            if (node.get("class_name") or "").lower() == "syslistview32":
                return True
    spec = step.get("desktop_spec")
    if isinstance(spec, dict) and (
        spec.get("hybrid_capture") or spec.get("desktop_shell")
    ):
        return True
    if isinstance(spec, str) and spec.strip():
        try:
            import json

            sd = json.loads(spec)
            if sd.get("hybrid_capture") or sd.get("desktop_shell"):
                return True
        except Exception:
            pass
    desc = (step.get("description") or "").lower()
    return "listitem" in desc or "控制面板" in desc or "桌面" in desc


def _find_listview_in_parent(parent_hwnd: int) -> int:
    user32 = _user32()
    shell = int(user32.FindWindowExW(int(parent_hwnd), 0, "SHELLDLL_DefView", None) or 0)
    if not shell:
        return 0
    lv = int(user32.FindWindowExW(shell, 0, "SysListView32", None) or 0)
    return lv


def get_desktop_listview_hwnd() -> int:
    """查找桌面 SysListView32（Progman 或可见 WorkerW）。"""
    user32 = _user32()
    progman = int(user32.FindWindowW("Progman", None) or 0)
    if progman:
        lv = _find_listview_in_parent(progman)
        if lv:
            return lv

    found: List[int] = []

    import ctypes
    from ctypes import wintypes

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def _enum_worker(hwnd, _lparam):
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(int(hwnd), buf, 256)
            if (buf.value or "").strip() != "WorkerW":
                return True
            lv = _find_listview_in_parent(int(hwnd))
            if lv:
                found.append(lv)
                return False
        except Exception:
            pass
        return True

    user32.EnumWindows(_enum_worker, 0)
    return int(found[0]) if found else 0


def _get_listview_item_text(listview_hwnd: int, index: int) -> str:
    """跨进程安全读取 ListView 项文本。"""
    import ctypes
    from ctypes import wintypes

    text_chars = 512
    text_bytes = text_chars * 2

    class LVITEMW(ctypes.Structure):
        _fields_ = [
            ("mask", wintypes.UINT),
            ("iItem", ctypes.c_int),
            ("iSubItem", ctypes.c_int),
            ("state", wintypes.UINT),
            ("stateMask", wintypes.UINT),
            ("pszText", ctypes.c_void_p),
            ("cchTextMax", ctypes.c_int),
            ("iImage", ctypes.c_int),
            ("lParam", ctypes.c_ssize_t),
            ("iIndent", ctypes.c_int),
        ]

    try:
        with _RemoteBuffer(int(listview_hwnd), ctypes.sizeof(LVITEMW) + text_bytes) as buf:
            # 布局：[LVITEMW][wchar text...]
            remote_item = int(buf.remote)
            remote_text = remote_item + ctypes.sizeof(LVITEMW)
            item = LVITEMW()
            item.mask = 0x0001  # LVIF_TEXT
            item.iItem = int(index)
            item.iSubItem = 0
            item.pszText = remote_text
            item.cchTextMax = text_chars - 1
            # 先写 LVITEM，文本区清零
            import ctypes as ct

            raw = (ct.c_ubyte * (ctypes.sizeof(LVITEMW) + text_bytes))()
            ct.memmove(raw, ct.byref(item), ctypes.sizeof(LVITEMW))
            buf.write(raw, ctypes.sizeof(LVITEMW) + text_bytes)

            user32 = _user32()
            result = ctypes.c_size_t(0)
            try:
                user32.SendMessageTimeoutW.argtypes = [
                    wintypes.HWND,
                    wintypes.UINT,
                    ctypes.c_size_t,
                    ctypes.c_void_p,
                    wintypes.UINT,
                    wintypes.UINT,
                    ctypes.POINTER(ctypes.c_size_t),
                ]
                user32.SendMessageTimeoutW.restype = wintypes.BOOL
            except Exception:
                pass
            ok = user32.SendMessageTimeoutW(
                int(listview_hwnd),
                LVM_GETITEMTEXTW,
                int(index),
                remote_item,
                0x0002,
                180,
                ct.byref(result),
            )
            if not ok:
                return ""
            out = (ct.c_ubyte * (ctypes.sizeof(LVITEMW) + text_bytes))()
            buf.read(out, ctypes.sizeof(LVITEMW) + text_bytes)
            text_buf = ct.create_unicode_buffer(text_chars)
            ct.memmove(text_buf, ct.byref(out, ctypes.sizeof(LVITEMW)), text_bytes)
            return (text_buf.value or "").strip()
    except OSError as exc:
        logger.debug("shell_listview GETITEMTEXT 跨进程失败: %s", exc)
        return ""


def _get_listview_item_center(listview_hwnd: int, index: int) -> Tuple[int, int]:
    crect = _get_listview_item_rect(listview_hwnd, index, code=LVIR_BOUNDS)
    if not crect:
        raise RuntimeError(f"无法获取 ListView 项 #{index} 的矩形")
    cx = int((crect[0] + crect[2]) // 2)
    cy = int((crect[1] + crect[3]) // 2)
    return cx, cy


def _screen_to_client(hwnd: int, sx: int, sy: int) -> Tuple[int, int]:
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(sx), int(sy))
    if not _user32().ScreenToClient(int(hwnd), ctypes.byref(pt)):
        return int(sx), int(sy)
    return int(pt.x), int(pt.y)


def _client_to_screen(hwnd: int, cx: int, cy: int) -> Tuple[int, int]:
    import ctypes

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT(int(cx), int(cy))
    if not _user32().ClientToScreen(int(hwnd), ctypes.byref(pt)):
        return int(cx), int(cy)
    return int(pt.x), int(pt.y)


def find_icon_index_by_name(listview_hwnd: int, target_name: str) -> int:
    user32 = _user32()
    count = int(user32.SendMessageW(int(listview_hwnd), LVM_GETITEMCOUNT, 0, 0) or 0)
    for i in range(count):
        text = _get_listview_item_text(listview_hwnd, i)
        if _match_icon_name(text, target_name):
            return i
    return -1


def resolve_shell_listview_at_screen(
    screen_x: int,
    screen_y: int,
    *,
    icon_name: str = "",
    listview_hwnd: int = 0,
) -> Optional[ShellIconTarget]:
    """
    按屏幕坐标向桌面 SysListView32 发送消息（Win10/11 图标名常为空，需 UIA/视觉坐标兜底）。
    """
    lv = int(listview_hwnd or 0) or get_desktop_listview_hwnd()
    if not lv:
        logger.info("shell_listview: 未找到桌面 SysListView32")
        return None
    cx, cy = _screen_to_client(lv, int(screen_x), int(screen_y))
    sx, sy = _client_to_screen(lv, cx, cy)
    return ShellIconTarget(
        listview_hwnd=lv,
        index=-1,
        icon_name=(icon_name or "").strip(),
        client_x=cx,
        client_y=cy,
        screen_x=sx,
        screen_y=sy,
    )


def resolve_shell_listview_icon(icon_name: str) -> Optional[ShellIconTarget]:
    name = (icon_name or "").strip()
    if not name:
        return None
    lv = get_desktop_listview_hwnd()
    if not lv:
        logger.info("shell_listview: 按名称查找失败，未找到 ListView hwnd")
        return None
    idx = find_icon_index_by_name(lv, name)
    if idx < 0:
        logger.info(
            "shell_listview: ListView hwnd=%s 中未按名称命中「%s」（Win10/11 项文本可能为空）",
            lv,
            name,
        )
        return None
    try:
        cx, cy = _get_listview_item_center(lv, idx)
    except RuntimeError as exc:
        logger.info("shell_listview: 名称命中 index=%s 但无法取矩形: %s", idx, exc)
        return None
    sx, sy = _client_to_screen(lv, cx, cy)
    return ShellIconTarget(
        listview_hwnd=lv,
        index=idx,
        icon_name=name,
        client_x=cx,
        client_y=cy,
        screen_x=sx,
        screen_y=sy,
    )


def _post_lparam(cx: int, cy: int) -> int:
    return (int(cy) << 16) | (int(cx) & 0xFFFF)


def post_listview_pointer(
    listview_hwnd: int,
    client_x: int,
    client_y: int,
    action: str,
) -> None:
    """向 SysListView32 客户区坐标发送鼠标消息（PostMessage，不移动光标）。"""
    user32 = _user32()
    hwnd = int(listview_hwnd)
    lp = _post_lparam(client_x, client_y)
    act = (action or "click").strip().lower()

    if act == "double_click":
        gap = max(0.12, min(0.45, float(os.environ.get("DESKTOP_SHELL_DBLCLICK_GAP", "0.18") or 0.18)))
        for i in range(2):
            user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
            time.sleep(0.03)
            user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
            if i == 0:
                time.sleep(gap)
        user32.PostMessageW(hwnd, WM_LBUTTONDBLCLK, MK_LBUTTON, lp)
    elif act == "right_click":
        user32.PostMessageW(hwnd, WM_RBUTTONDOWN, MK_RBUTTON, lp)
        time.sleep(0.03)
        user32.PostMessageW(hwnd, WM_RBUTTONUP, 0, lp)
    else:
        user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.03)
        user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)


def execute_shell_listview_action(
    step: dict,
    action: str,
    *,
    target: Optional[ShellIconTarget] = None,
) -> ShellIconTarget:
    resolved = target
    if not resolved:
        name = icon_name_from_step(step)
        if not name:
            raise RuntimeError("桌面 ListView 消息点击缺少图标名称")
        resolved = resolve_shell_listview_icon(name)
        if not resolved:
            raise RuntimeError(f"桌面 ListView 中未找到图标「{name}」")
    post_listview_pointer(
        resolved.listview_hwnd,
        resolved.client_x,
        resolved.client_y,
        action,
    )
    return resolved


def try_resolve_shell_listview_step(step: dict) -> Optional[ShellIconTarget]:
    if not shell_message_enabled():
        return None
    if not is_desktop_listitem_step(step):
        return None
    name = icon_name_from_step(step)
    if not name:
        return None
    return resolve_shell_listview_icon(name)
