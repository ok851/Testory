# -*- coding: utf-8 -*-
"""语义化 Windows 桌面操作工具（FC + MCP 共用实现）。"""
from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger

_TOOL_TIMEOUT_SEC = 10.0
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="win_desk_tools")

# 当前桌面操作目标（不绑定微信）：focus 成功后记录，后续按键校验/观察都相对它
_desktop_target: Dict[str, Any] = {
    "hwnd": 0,
    "label": "",
    "title": "",
    "process": "",
    # 搜索框武装：点击搜索成功后记下坐标，输入前再点一次，避免顶层 SetFocus 抢回聊天框
    "search_xy": None,
    "search_armed_at": 0.0,
    # search=正在搜联系人；compose=会话消息栏（禁止再点回搜索）
    "input_phase": "",
    "last_search_query": "",
}

# 单一最优节奏：在可验证投递前提下压短空等（失败时各策略仍会自行加长/回退）
_PACE: Dict[str, float] = {
    "post_click_stable_ms": 220,
    "post_key_stable_ms": 160,
    "type_settle_sec": 0.14,
    "reclick_settle_sec": 0.07,
    "strategy_gap_sec": 0.05,
    "focus_gap_sec": 0.035,
}


def set_desktop_action_pace(pace: Optional[str] = None) -> str:
    """兼容旧调用：桌面节奏已固定为单一最优档，忽略入参。"""
    return "default"


def get_desktop_action_pace() -> str:
    return "default"


def _pace_val(key: str) -> float:
    return float(_PACE.get(key) or 0)


def set_desktop_target(
    *,
    hwnd: int = 0,
    label: str = "",
    title: str = "",
    process: str = "",
) -> None:
    _desktop_target["hwnd"] = int(hwnd or 0)
    _desktop_target["label"] = (label or title or "").strip()
    _desktop_target["title"] = (title or "").strip()
    _desktop_target["process"] = (process or "").strip()


def get_desktop_target() -> Dict[str, Any]:
    return dict(_desktop_target)


def clear_desktop_target() -> None:
    set_desktop_target(hwnd=0, label="", title="", process="")
    _desktop_target["search_xy"] = None
    _desktop_target["search_armed_at"] = 0.0
    _desktop_target["input_phase"] = ""
    _desktop_target["last_search_query"] = ""


def arm_search_input_focus(x: int, y: int) -> None:
    """记录搜索框屏幕坐标，供后续 type 前复点（Qt 微信无 UIA Edit）。"""
    try:
        _desktop_target["search_xy"] = (int(x), int(y))
        _desktop_target["search_armed_at"] = float(time.time())
        _desktop_target["input_phase"] = "search"
    except Exception:
        _desktop_target["search_xy"] = None
        _desktop_target["search_armed_at"] = 0.0


def clear_search_input_focus() -> None:
    _desktop_target["search_xy"] = None
    _desktop_target["search_armed_at"] = 0.0


def mark_compose_input_phase() -> None:
    """会话已打开 / 消息栏就绪：解除搜索武装，后续 type 不得再点回搜索框。"""
    clear_search_input_focus()
    _desktop_target["input_phase"] = "compose"


def get_input_phase() -> str:
    return str(_desktop_target.get("input_phase") or "").strip().lower()


def _reclick_armed_search_if_needed() -> Dict[str, Any]:
    """若近期点过搜索，输入前再物理点击搜索坐标，保住搜索框焦点。"""
    if get_input_phase() == "compose":
        clear_search_input_focus()
        return {"ok": False, "skipped": True, "reason": "compose_phase"}
    xy = _desktop_target.get("search_xy")
    armed_at = float(_desktop_target.get("search_armed_at") or 0)
    if not xy or not isinstance(xy, (tuple, list)) or len(xy) != 2:
        return {"ok": False, "skipped": True, "reason": "no_armed_search"}
    if time.time() - armed_at > 45.0:
        clear_search_input_focus()
        return {"ok": False, "skipped": True, "reason": "search_arm_expired"}
    x, y = int(xy[0]), int(xy[1]) + 4
    try:
        from desktop_input import force_focus_hwnd, screen_click

        hwnd = int(get_desktop_target().get("hwnd") or 0)
        if hwnd:
            force_focus_hwnd(hwnd)
            time.sleep(0.05)
        # 必须物理点击：PostMessage 点不到 Qt 键盘焦点
        screen_click(x, y)
        time.sleep(_pace_val("reclick_settle_sec"))
        return {"ok": True, "x": x, "y": y, "via": "physical_reclick_search"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

_KEY_ALIASES = {
    "enter": "enter",
    "return": "enter",
    "esc": "esc",
    "escape": "esc",
    "tab": "tab",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "space": "space",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "home": "home",
    "end": "end",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "cmd": "win",
}

_VK = {
    "ctrl": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "space": 0x20,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
}


def _run_with_timeout(fn, timeout: float = _TOOL_TIMEOUT_SEC):
    fut = _executor.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeout:
        raise TimeoutError(f"操作超时（>{int(timeout)}s）")


@contextmanager
def _steal_focus_enabled():
    prev = os.environ.get("DESKTOP_STEAL_FOCUS")
    os.environ["DESKTOP_STEAL_FOCUS"] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("DESKTOP_STEAL_FOCUS", None)
        else:
            os.environ["DESKTOP_STEAL_FOCUS"] = prev


def _wait_stable_quiet(timeout_ms: Optional[int] = None) -> None:
    ms = int(timeout_ms if timeout_ms is not None else _pace_val("post_click_stable_ms"))
    try:
        from screen_tools import wait_screen_stable

        wait_screen_stable(timeout_ms=max(80, ms), poll_ms=100)
    except Exception:
        time.sleep(min(0.25, max(0.05, ms / 1000.0)))


def _hwnd_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(int(hwnd), buf, 256)
        return (buf.value or "").strip()
    except Exception:
        return ""


# 常见应用别名：用户说「微信」时应对 Weixin.exe / 最小化标题栏等
_APP_FOCUS_ALIASES: Dict[str, List[str]] = {
    "微信": ["微信", "weixin", "wechat", "weixin.exe", "wechat.exe"],
    "wechat": ["微信", "weixin", "wechat", "weixin.exe", "wechat.exe"],
    "weixin": ["微信", "weixin", "wechat", "weixin.exe", "wechat.exe"],
    "企业微信": ["企业微信", "wxwork", "wecom"],
    "qq": ["qq", "tencent"],
    "记事本": ["记事本", "notepad", "notepad.exe"],
    "notepad": ["记事本", "notepad", "notepad.exe"],
    "计算器": ["计算器", "calculator", "calc", "calc.exe"],
    "calc": ["计算器", "calculator", "calc", "calc.exe"],
    "calculator": ["计算器", "calculator", "calc", "calc.exe"],
}

_SKIP_FOCUS_CLASSES = frozenset(
    {
        "ime",
        "msctfime ui",
        "sopy_hint",
        "sopy_ui",
        "sopy_status",
        "sogou_tsf_ui",
        "base_powermessagewindow",
        "displayicc_systemmessagewindow",
        "chrome_systemmessagewindow",
        "tooltips_class32",
        "gdi+ hook window class",
        "gdi+ window",
    }
)


def _is_noise_focus_window(title: str, process: str, class_name: str) -> bool:
    """过滤 GDI+ 钩子窗、无意义辅助窗，避免误 focus / 误以为多开了应用。"""
    t = (title or "").strip().lower()
    p = (process or "").strip().lower()
    c = (class_name or "").strip().lower()
    if "gdi+" in t or "gdi+" in p or "gdi+" in c:
        return True
    if t in ("gdi+ window", "gdi+windows", "gdi+windows.exe"):
        return True
    if p in ("gdi+windows.exe", "gdi+windows"):
        return True
    return False


def _focus_needles(app_name: str) -> List[str]:
    name = (app_name or "").strip()
    if not name:
        return []
    keys = [name]
    low = name.lower()
    for k, aliases in _APP_FOCUS_ALIASES.items():
        if k.lower() == low or low in [a.lower() for a in aliases] or name in aliases:
            keys.extend(aliases)
            break
    out: List[str] = []
    for k in keys:
        kl = (k or "").strip().lower()
        if kl and kl not in out:
            out.append(kl)
    return out


def _enum_focus_candidate_windows() -> List[Dict[str, Any]]:
    """枚举可用于激活的顶层窗口（含最小化；不过滤小尺寸标题栏）。"""
    rows: List[Dict[str, Any]] = []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        try:
            import psutil
        except ImportError:
            psutil = None  # type: ignore

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _callback(hwnd, _lparam):
            try:
                if not user32.IsWindow(hwnd):
                    return True
                visible = bool(user32.IsWindowVisible(hwnd))
                iconic = bool(user32.IsIconic(hwnd))
                # 完全不可见且非最小化：跳过（托盘隐藏主窗常为 iconic 或仍 visible）
                if not visible and not iconic:
                    # 仍保留有标题的微信类窗口（部分版本最小化后可见性异常）
                    length0 = user32.GetWindowTextLengthW(hwnd)
                    if length0 <= 0:
                        return True
                length = user32.GetWindowTextLengthW(hwnd)
                title = ""
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = (buff.value or "").strip()
                cls = _hwnd_class(int(hwnd))
                cls_l = (cls or "").lower()
                if cls_l in _SKIP_FOCUS_CLASSES:
                    return True
                if any(skip in cls_l for skip in ("tooltip", "ime", "sopy_", "gdi+")):
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pid_val = int(pid.value or 0)
                proc_name = ""
                if psutil and pid_val:
                    try:
                        proc_name = psutil.Process(pid_val).name() or ""
                    except Exception:
                        proc_name = ""
                if _is_noise_focus_window(title, proc_name, cls):
                    return True
                rect = wintypes.RECT()
                w = h = 0
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    w = int(rect.right - rect.left)
                    h = int(rect.bottom - rect.top)
                # 无标题且面积极小的无意义窗口
                if not title and w * h < 2000 and not iconic:
                    return True
                rows.append(
                    {
                        "hwnd": int(hwnd),
                        "title": title,
                        "pid": pid_val,
                        "process": proc_name,
                        "class_name": cls,
                        "width": w,
                        "height": h,
                        "visible": visible,
                        "iconic": iconic,
                    }
                )
            except Exception:
                pass
            return True

        user32.EnumWindows(_callback, 0)
    except Exception as e:
        uat_logger.debug("enum focus windows failed: %s", e)
    return rows


def _score_focus_candidate(w: Dict[str, Any], needles: List[str]) -> float:
    title = (w.get("title") or "").lower()
    proc = (w.get("process") or "").lower()
    cls = (w.get("class_name") or "").lower()
    score = 0.0
    for n in needles:
        if n and n in title:
            score += 10.0
            if title == n or title.startswith(n):
                score += 3.0
        if n and (n in proc or proc.replace(".exe", "") == n.replace(".exe", "")):
            score += 6.0
        if n and n in cls:
            score += 2.0
    if score <= 0:
        return 0.0
    # 主窗口偏好：有标题、可还原、面积更大（最小化标题栏面积很小）
    if w.get("title"):
        score += 1.5
    if w.get("iconic"):
        score += 2.0  # 最小化主窗正是要还原的目标
    area = max(0, int(w.get("width") or 0) * int(w.get("height") or 0))
    if area >= 200_000:
        score += 4.0
    elif area >= 40_000:
        score += 2.0
    elif area > 0 and area < 8_000 and w.get("iconic"):
        score += 1.0  # 任务栏最小化缩略条
    # 微信/Qt 主窗强化：进程名精确 + Qt 壳类名
    wechat_like = any(
        n in ("微信", "weixin", "wechat", "weixin.exe", "wechat.exe") for n in needles
    )
    if wechat_like:
        if proc in ("weixin.exe", "wechat.exe"):
            score += 10.0
        if "qt" in cls and "qwindow" in cls:
            score += 6.0
        if title in ("微信", "weixin", "wechat") or title.startswith("微信"):
            score += 4.0
    # 排除托盘消息类伪主窗（大但 invisible）
    if "trayicon" in cls:
        score -= 8.0
    if "toolsavebits" in cls:
        score -= 3.0
    # 排除浏览器误匹配（needle 含通用词时）
    if any(b in proc for b in ("chrome.exe", "msedge.exe", "firefox.exe", "cursor.exe")):
        score -= 12.0
    return score


def refresh_desktop_target_hwnd() -> Dict[str, Any]:
    """校验并刷新当前桌面目标 hwnd：失效时按 process/title 重新捕获。"""
    tgt = get_desktop_target()
    hwnd = int(tgt.get("hwnd") or 0)
    label = (tgt.get("label") or tgt.get("title") or "").strip()
    process = (tgt.get("process") or "").strip()
    title = (tgt.get("title") or "").strip()

    def _bind(w: Dict[str, Any], *, via: str) -> Dict[str, Any]:
        nh = int(w.get("hwnd") or 0)
        set_desktop_target(
            hwnd=nh,
            label=label or str(w.get("title") or ""),
            title=str(w.get("title") or title),
            process=str(w.get("process") or process),
        )
        return {
            "ok": True,
            "hwnd": nh,
            "refreshed": True,
            "via": via,
            "title": w.get("title"),
            "process": w.get("process"),
        }

    if hwnd:
        try:
            from desktop_input import is_valid_hwnd

            if is_valid_hwnd(hwnd):
                return {
                    "ok": True,
                    "hwnd": hwnd,
                    "refreshed": False,
                    "via": "existing",
                    "title": title,
                    "process": process,
                }
        except Exception:
            pass

    needles = _focus_needles(label or title or process)
    if process:
        pl = process.lower()
        if pl not in needles:
            needles = list(needles) + [pl, pl.replace(".exe", "")]
    windows = _enum_focus_candidate_windows()
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for w in windows:
        # 优先同进程名精确命中
        wp = (w.get("process") or "").lower()
        if process and wp == process.lower() and wp:
            s = _score_focus_candidate(w, needles or [process.lower()])
            scored.append((max(s, 20.0), w))
            continue
        s = _score_focus_candidate(w, needles) if needles else 0.0
        if s > 0:
            scored.append((s, w))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return _bind(scored[0][1], via="rebind_process_or_title")
    return {
        "ok": False,
        "hwnd": 0,
        "error": f"目标窗口已失效且无法按「{label or process or title}」重新捕获",
        "suggestion": "请重新调用 windows_focus_app。",
    }


def capture_desktop_target_foreground(*, step: str = "") -> Dict[str, Any]:
    """操作前：刷新目标绑定；默认多策略抢前台。DESKTOP_NO_FOCUS_STEAL=1 时不抢前台。"""
    refreshed = refresh_desktop_target_hwnd()
    if not refreshed.get("ok"):
        return {
            "ok": False,
            "error": refreshed.get("error")
            or f"{step or '操作'}前没有有效目标窗口",
            "suggestion": refreshed.get("suggestion")
            or "先调用 windows_focus_app 指定应用。",
            "refresh": refreshed,
        }
    hwnd = int(refreshed.get("hwnd") or get_desktop_target().get("hwnd") or 0)
    label = (get_desktop_target().get("label") or get_desktop_target().get("title") or "目标窗口").strip()
    steal_xy = None
    xy = _desktop_target.get("search_xy")
    if xy and isinstance(xy, (tuple, list)) and len(xy) == 2:
        steal_xy = (int(xy[0]), int(xy[1]) + 4)
    try:
        from desktop_input import get_foreground_hwnd, reclaim_foreground_hwnd, no_focus_steal_enabled

        if int(get_foreground_hwnd() or 0) == hwnd:
            return {
                "ok": True,
                "hwnd": hwnd,
                "already_fg": True,
                "fg_title": _hwnd_title(hwnd),
                "refresh": refreshed,
            }
        if no_focus_steal_enabled():
            # 后台模式：仍绑定目标，不抢前台；后续依赖 UIA/PostMessage
            return {
                "ok": True,
                "hwnd": hwnd,
                "already_fg": False,
                "no_focus_steal": True,
                "fg_title": _hwnd_title(hwnd),
                "refresh": refreshed,
                "note": "DESKTOP_NO_FOCUS_STEAL：未抢前台，使用后台投递",
            }
        reclaim = reclaim_foreground_hwnd(hwnd, retries=4, steal_click_xy=steal_xy)
        if reclaim.get("ok"):
            # 若用了标题栏抢前台，尽量把搜索焦点补回来
            reclick = _reclick_armed_search_if_needed() if steal_xy else {"skipped": True}
            return {
                "ok": True,
                "hwnd": hwnd,
                "fg_title": reclaim.get("fg_title") or _hwnd_title(hwnd),
                "reclaim": reclaim,
                "refresh": refreshed,
                "reclick_search": reclick,
            }
        chk = _ensure_foreground_hwnd(hwnd, label=label)
        chk["refresh"] = refreshed
        chk["reclaim"] = reclaim
        if chk.get("ok") and steal_xy:
            chk["reclick_search"] = _reclick_armed_search_if_needed()
        return chk
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "hwnd": hwnd, "refresh": refreshed}


def _pick_topmost_match(matches: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not matches:
        return None
    try:
        import ctypes

        fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        for m in matches:
            if int(m.get("hwnd") or 0) == fg:
                return m
    except Exception:
        pass
    return max(matches, key=lambda x: int(x.get("width") or 0) * int(x.get("height") or 0))


def _hwnd_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        n = user32.GetWindowTextLengthW(int(hwnd))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(int(hwnd), buf, n + 1)
        return (buf.value or "").strip()
    except Exception:
        return ""


def _force_foreground_hwnd(hwnd: int) -> bool:
    """强制把 hwnd 提到前台（委托 desktop_input 多策略捕获）。"""
    if not hwnd:
        return False
    try:
        from desktop_input import force_focus_hwnd

        return bool(force_focus_hwnd(int(hwnd), retries=4))
    except Exception as e:
        uat_logger.debug("force foreground failed: %s", e)
        return False


@contextmanager
def _hold_input_focus(hwnd: int):
    """短附着：仅在 yield 期间 AttachThreadInput，结束必须解开，避免微信输入队列卡死。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = int(hwnd or 0)
    attached_fg = False
    attached_target = False
    tid_fg = 0
    tid_target = 0
    tid_cur = int(kernel32.GetCurrentThreadId() or 0)
    try:
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            fg = int(user32.GetForegroundWindow() or 0)
            pid = wintypes.DWORD()
            tid_target = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or 0)
            tid_fg = int(user32.GetWindowThreadProcessId(fg, ctypes.byref(pid)) or 0) if fg else 0
            try:
                user32.AllowSetForegroundWindow(-1)
            except Exception:
                pass
            if tid_fg and tid_fg != tid_cur:
                attached_fg = bool(user32.AttachThreadInput(tid_cur, tid_fg, True))
            if tid_target and tid_target != tid_cur and tid_target != tid_fg:
                attached_target = bool(user32.AttachThreadInput(tid_cur, tid_target, True))
            user32.SetForegroundWindow(hwnd)
        yield
    finally:
        try:
            if attached_target and tid_target:
                user32.AttachThreadInput(tid_cur, tid_target, False)
        except Exception:
            pass
        try:
            if attached_fg and tid_fg:
                user32.AttachThreadInput(tid_cur, tid_fg, False)
        except Exception:
            pass


def _capture_target_hash(hwnd: int = 0) -> str:
    """截取目标窗口并返回哈希。优先按 hwnd 截取，避免观察步骤抢前台。"""
    try:
        from screen_tools import (
            capture_hwnd_png,
            capture_foreground_window_png,
            capture_primary_monitor_png,
            _image_hash,
        )

        png = None
        hwnd = int(hwnd or 0)
        if hwnd:
            png, _meta = capture_hwnd_png(hwnd)
        if not png:
            png, _meta = capture_foreground_window_png()
        if not png:
            png = capture_primary_monitor_png()
        return _image_hash(png) if png else ""
    except Exception:
        return ""


def _ensure_foreground_hwnd(hwnd: int, *, label: str = "") -> Dict[str, Any]:
    """确保目标窗口真是前台；失败时返回结构化错误（勿假装按键成功）。"""
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = int(hwnd or 0)
    label = (label or _hwnd_title(hwnd) or str(hwnd)).strip()
    if not hwnd:
        return {"ok": False, "error": "目标窗口 hwnd 无效"}
    ok = _force_foreground_hwnd(hwnd)
    fg = int(user32.GetForegroundWindow() or 0)
    if ok and fg == hwnd:
        return {"ok": True, "hwnd": hwnd, "fg_title": _hwnd_title(hwnd)}
    ok = _force_foreground_hwnd(hwnd)
    fg = int(user32.GetForegroundWindow() or 0)
    if ok and fg == hwnd:
        return {"ok": True, "hwnd": hwnd, "fg_title": _hwnd_title(hwnd)}
    return {
        "ok": False,
        "error": (
            f"未能把目标窗口「{label}」保持为前台。"
            f"当前前台是「{_hwnd_title(fg) or fg}」。"
            "按键会打到错误窗口，已中止后续输入。"
        ),
        "hwnd": hwnd,
        "fg_hwnd": fg,
        "fg_title": _hwnd_title(fg),
        "suggestion": (
            f"请先手动点一下「{label}」使其真正前台，或暂时把 Testory 浏览器最小化后再试。"
        ),
    }


def begin_desktop_action_frame(*, step: str = "") -> Dict[str, Any]:
    """OpenClaw 式动作帧：捕获目标前台 + 记录 before 哈希/frame_id，动作后必须再观察。"""
    cap = capture_desktop_target_foreground(step=step or "desktop_action")
    if not cap.get("ok"):
        return {
            "ok": False,
            "error": cap.get("error") or f"{step or '操作'}前未能捕获目标窗口",
            "suggestion": cap.get("suggestion"),
            "capture": cap,
        }
    hwnd = int(cap.get("hwnd") or get_desktop_target().get("hwnd") or 0)
    before_hash = _capture_target_hash(hwnd)
    frame_id = f"{hwnd}:{(before_hash or '0')[:16]}:{int(time.time() * 1000)}"
    _desktop_target["action_frame_id"] = frame_id
    _desktop_target["action_before_hash"] = before_hash
    return {
        "ok": True,
        "hwnd": hwnd,
        "frame_id": frame_id,
        "before_hash": before_hash,
        "capture": cap,
        "fg_title": cap.get("fg_title") or _hwnd_title(hwnd),
    }


def _is_qt_wechat_target(hwnd: int = 0) -> bool:
    hwnd = int(hwnd or get_desktop_target().get("hwnd") or 0)
    tgt = get_desktop_target()
    blob = (
        f"{_hwnd_title(hwnd)} {tgt.get('label') or ''} "
        f"{tgt.get('title') or ''} {tgt.get('process') or ''} {_hwnd_class(hwnd)}"
    ).lower()
    return any(k in blob for k in ("微信", "wechat", "weixin", "qt515", "qt5", "qwindow"))


def ensure_desktop_target_foreground(*, step: str = "") -> Dict[str, Any]:
    """相对当前桌面目标：刷新绑定并捕获前台（任意应用）。"""
    return capture_desktop_target_foreground(step=step)


def observe_screen_texts(*, prefer_foreground: bool = True) -> Dict[str, Any]:
    """用本地 OCR 观察当前画面（共享屏幕同源能力），返回 texts 摘要。"""
    try:
        from screen_tools import get_screen_text, clear_ocr_cache

        # 操作后画面变化，避免命中旧缓存
        try:
            clear_ocr_cache()
        except Exception:
            pass
        res = get_screen_text()
        if not isinstance(res, dict):
            return {"ok": False, "texts": [], "error": "观察返回异常"}
        texts = [str(t) for t in (res.get("texts") or []) if t]
        return {
            "ok": bool(res.get("success")),
            "texts": texts[:40],
            "error": res.get("error") or "",
            "ocr_engine": res.get("ocr_engine"),
            "capture": res.get("capture"),
        }
    except Exception as e:
        return {"ok": False, "texts": [], "error": str(e)[:200]}


def verify_screen_contains(
    needles: List[str],
    *,
    min_hits: int = 1,
) -> Dict[str, Any]:
    """屏幕观察核验：OCR 文本是否包含期望片段（needles 由调用方传入，不写死应用文案）。"""
    obs = observe_screen_texts()
    texts = obs.get("texts") or []
    blob = " ".join(texts)
    hits = []
    missing = []
    for n in needles:
        n = (n or "").strip()
        if not n:
            continue
        matched = False
        # 短 needle（<2 汉字/字元）要求整段 OCR 文本相等或独立词命中，避免「为」⊂「行为」
        short = len(n) < 2
        if not short and n in blob:
            matched = True
        else:
            for t in texts:
                t = (t or "").strip()
                if not t:
                    continue
                if short:
                    if t == n or t.startswith(n + " ") or t.endswith(" " + n):
                        matched = True
                        break
                    continue
                if n in t:
                    matched = True
                    break
                # 仅当 needle 足够长时才允许「文本被 needle 包含」
                if len(n) >= 4 and t in n:
                    matched = True
                    break
        if matched:
            hits.append(n)
        else:
            missing.append(n)
    ok = len(hits) >= max(1, int(min_hits)) and len(hits) > 0
    return {
        "ok": ok and bool(obs.get("ok")),
        "hits": hits,
        "missing": missing,
        "texts_preview": texts[:16],
        "observe_error": obs.get("error") or "",
    }


def capture_after_action(
    hwnd: int = 0,
    *,
    before_hash: str = "",
    expect_texts: Optional[List[str]] = None,
    require_change: bool = True,
) -> Dict[str, Any]:
    """Hermes 式 capture_after：返回 before/after 哈希、是否变化、可选 OCR 核验。"""
    hwnd = int(hwnd or get_desktop_target().get("hwnd") or 0)
    after_hash = _capture_target_hash(hwnd)
    changed = bool(before_hash and after_hash and before_hash != after_hash)
    unchanged = bool(before_hash and after_hash and before_hash == after_hash)
    out: Dict[str, Any] = {
        "before_hash": before_hash or "",
        "after_hash": after_hash or "",
        "changed": changed,
        "unchanged": unchanged,
        "hwnd": hwnd,
        "texts_preview": [],
    }
    needles = [str(x).strip() for x in (expect_texts or []) if str(x or "").strip()]
    if needles:
        v = verify_screen_contains(needles, min_hits=1)
        out["texts_preview"] = v.get("texts_preview") or []
        out["verify"] = v
        out["verified"] = bool(v.get("ok"))
    elif unchanged is False and after_hash:
        # 无期望文本时仍附带短 OCR 摘要，供思考区展示
        try:
            obs = observe_screen_texts()
            out["texts_preview"] = (obs.get("texts") or [])[:12]
        except Exception:
            pass
    if require_change and before_hash and after_hash and unchanged:
        out["ok"] = False
        out["error"] = (
            "已尝试操作目标窗口，但画面无变化——按键/输入很可能未进入该窗口，不能认定已发生。"
        )
    else:
        out["ok"] = True if not needles else bool(out.get("verified"))
        if needles and not out.get("verified"):
            out["error"] = (
                "已尝试操作，但屏幕观察未看到期望文字，不能认定输入/点击已成功。"
            )
            out["ok"] = False
    return out


def _restore_and_focus_hwnd(hwnd: int) -> bool:
    with _steal_focus_enabled():
        try:
            from desktop_input import focus_hwnd

            focus_hwnd(int(hwnd))
        except Exception:
            pass
        return _force_foreground_hwnd(int(hwnd))


def _windows_focus_app_impl(app_name: str) -> Dict[str, Any]:
    """聚焦实现：枚举识别 → 多策略抢前台 → 绑定 desktop_target（可刷新）。"""
    name = (app_name or "").strip()
    if not name:
        return {
            "success": False,
            "error": "app_name 为空",
            "suggestion": "请传入窗口标题或应用名，如「记事本」「微信」。",
        }
    needles = _focus_needles(name)
    windows = _enum_focus_candidate_windows()
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for w in windows:
        s = _score_focus_candidate(w, needles)
        if s > 0:
            scored.append((s, w))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = scored[0][1] if scored else None
    via = "title_or_process"
    if not picked:
        try:
            from desktop_discovery import list_visible_windows

            for w in list_visible_windows() or []:
                item = dict(w)
                item["class_name"] = _hwnd_class(int(w.get("hwnd") or 0))
                s = _score_focus_candidate(item, needles)
                if s > 0:
                    scored.append((s, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            picked = scored[0][1] if scored else None
            via = "visible_fallback"
        except Exception:
            pass
    if not picked:
        titles = [w.get("title") for w in windows if w.get("title")][:20]
        procs = sorted(
            {
                (w.get("process") or "")
                for w in windows
                if any(
                    n.replace(".exe", "") in (w.get("process") or "").lower()
                    for n in needles
                )
            }
        )
        return {
            "success": False,
            "error": f"未找到「{name}」对应窗口",
            "visible_titles": titles,
            "matched_processes": procs,
            "suggestion": (
                "应用可能未启动。请调用 windows_launch_app(应用名) 启动后再操作；"
                "若已在托盘，可先手动点开主窗口。"
            ),
            "candidates_scanned": len(windows),
            "can_launch": True,
        }
    hwnd = int(picked.get("hwnd") or 0)
    # 先绑定目标，再抢前台（后续 refresh 依赖 process/title）
    set_desktop_target(
        hwnd=hwnd,
        label=name,
        title=str(picked.get("title") or ""),
        process=str(picked.get("process") or ""),
    )
    from desktop_input import reclaim_foreground_hwnd

    reclaim = reclaim_foreground_hwnd(hwnd, retries=5)
    focused = bool(reclaim.get("ok"))
    time.sleep(0.12)
    if not focused:
        chk = _ensure_foreground_hwnd(hwnd, label=name)
        focused = bool(chk.get("ok"))
        if not focused:
            return {
                "success": False,
                "error": chk.get("error") or reclaim.get("error") or "无法捕获应用前台",
                "suggestion": chk.get("suggestion")
                or "请确认窗口可见；Agent 会再试多策略抢前台，也可手动点一下目标窗后重试。",
                "hwnd": hwnd,
                "matched_title": picked.get("title"),
                "matched_process": picked.get("process"),
                "score": scored[0][0] if scored else 0,
                "fg_title": chk.get("fg_title") or reclaim.get("fg_title"),
                "was_minimized": bool(picked.get("iconic")),
                "reclaim": reclaim,
            }
    # 再确认绑定仍有效
    refresh_desktop_target_hwnd()
    return {
        "success": True,
        "app_name": name,
        "matched_title": picked.get("title"),
        "hwnd": int(get_desktop_target().get("hwnd") or hwnd),
        "class_name": picked.get("class_name"),
        "process": picked.get("process"),
        "via": via,
        "score": scored[0][0] if scored else 0,
        "was_minimized": bool(picked.get("iconic")),
        "foreground_ok": True,
        "reclaim": reclaim,
        "candidates_scanned": len(windows),
    }


def windows_focus_app(app_name: str, *, auto_launch: bool = True) -> Dict[str, Any]:
    """聚焦已打开窗口；若未找到且 auto_launch=True，则自动 windows_launch_app。"""
    try:
        r = _run_with_timeout(lambda: _windows_focus_app_impl(app_name), timeout=8.0)
    except TimeoutError as e:
        return {"success": False, "error": str(e), "suggestion": "稍后重试 windows_focus_app。"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300], "suggestion": "检查窗口是否可见。"}
    if r.get("success") or not auto_launch:
        return r
    # 仅在「未找到窗口」时自动启动；抢前台失败等不要二次 launch
    if r.get("can_launch") is not True:
        return r
    launched = windows_launch_app(app_name)
    if launched.get("success"):
        launched["auto_launched_after_focus_miss"] = True
        return launched
    # 合并提示
    r["launch_attempt"] = {
        "success": False,
        "error": launched.get("error"),
        "suggestion": launched.get("suggestion"),
    }
    r["suggestion"] = (
        launched.get("suggestion")
        or r.get("suggestion")
        or "请调用 windows_launch_app 启动应用。"
    )
    return r


def _resolve_launch_input(app_name: str) -> Tuple[str, str]:
    """返回 (launch_input_value, display_name)。"""
    name = (app_name or "").strip()
    if not name:
        return "", ""
    try:
        from agent_desktop_fastpath import resolve_desktop_launch_target

        hit = resolve_desktop_launch_target(f"打开{name}")
        if hit:
            return hit[0], hit[1]
    except Exception:
        pass
    low = name.lower()
    alias_map = {
        "notepad": ("notepad", "记事本"),
        "notepad.exe": ("notepad", "记事本"),
        "记事本": ("notepad", "记事本"),
        "calc": ("calc", "计算器"),
        "calc.exe": ("calc", "计算器"),
        "计算器": ("calc", "计算器"),
        "calculator": ("calc", "计算器"),
        "explorer": ("explorer", "资源管理器"),
        "cmd": ("cmd", "命令提示符"),
        "powershell": ("powershell", "PowerShell"),
    }
    if low in alias_map:
        return alias_map[low]
    if name in alias_map:
        return alias_map[name]
    return name, name


def _windows_launch_app_impl(app_name: str) -> Dict[str, Any]:
    """启动应用（未运行也可）：resolve → startfile/gateway → 等待窗口 → focus 绑定。"""
    name = (app_name or "").strip()
    if not name:
        return {
            "success": False,
            "error": "app_name 为空",
            "suggestion": "请传入应用名，如「记事本」「计算器」「notepad」。",
        }
    launch_val, display = _resolve_launch_input(name)
    if not launch_val:
        return {"success": False, "error": "无法解析启动目标", "suggestion": "请给出程序名或别名。"}

    already = _windows_focus_app_impl(display if display else name)
    if already.get("success"):
        already["via"] = "already_running_focus"
        already["launched"] = False
        return already

    path = launch_val
    try:
        from desktop_env_config import smart_resolve_launch_path

        path = smart_resolve_launch_path(launch_val) or launch_val
    except Exception:
        path = launch_val

    launched_via = "os_startfile"
    try:
        from desktop_agent_client import desktop_agent_enabled, remote_execute_step

        if desktop_agent_enabled():
            step = {
                "action": "launch_app",
                "input_value": launch_val,
                "description": f"打开{display}",
                "automation_layer": "desktop",
            }
            r0 = remote_execute_step(step)
            if isinstance(r0, dict) and (
                r0.get("ok") is True
                or r0.get("success") is True
                or str(r0.get("status") or "").lower() in ("success", "ok", "passed")
            ):
                launched_via = "desktop_gateway"
            else:
                import sys

                if sys.platform == "win32":
                    os.startfile(path)  # type: ignore[attr-defined]
                else:
                    import subprocess

                    subprocess.Popen([path], shell=False)
        else:
            import sys

            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import subprocess

                subprocess.Popen([path], shell=False)
    except Exception as e:
        return {
            "success": False,
            "error": f"启动失败: {e}",
            "app_name": name,
            "launch_value": launch_val,
            "resolved_path": path,
            "suggestion": "请确认程序已安装，或改用完整 exe 路径。",
        }

    deadline = time.time() + 12.0
    last_focus: Dict[str, Any] = {}
    while time.time() < deadline:
        time.sleep(0.4)
        last_focus = _windows_focus_app_impl(display if display else name)
        if last_focus.get("success"):
            last_focus["launched"] = True
            last_focus["via"] = launched_via
            last_focus["launch_value"] = launch_val
            last_focus["resolved_path"] = path
            last_focus["display"] = display
            return last_focus

    return {
        "success": False,
        "error": last_focus.get("error")
        or f"已尝试启动「{display}」，但未出现可聚焦窗口",
        "launched": True,
        "via": launched_via,
        "launch_value": launch_val,
        "resolved_path": path,
        "focus_detail": last_focus,
        "suggestion": "请目视确认应用是否弹出；可手动点开后说「继续」。",
    }


def windows_launch_app(app_name: str) -> Dict[str, Any]:
    try:
        return _run_with_timeout(lambda: _windows_launch_app_impl(app_name), timeout=20.0)
    except TimeoutError as e:
        return {
            "success": False,
            "error": str(e),
            "suggestion": "启动超时；请手动打开应用后用 windows_focus_app。",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)[:300],
            "suggestion": "稍后重试 windows_launch_app，或手动启动应用。",
        }


def _type_text_impl(text: str, *, clear: bool = False, hwnd: int = 0) -> Dict[str, Any]:
    """向目标 hwnd 投递文本（UIA/粘贴优先）；无 hwnd 时降级全局 SendInput。"""
    from desktop_input import deliver_text_to_hwnd, sendinput_type_text

    hwnd = int(hwnd or get_desktop_target().get("hwnd") or 0)
    # 搜索框武装时强制剪贴板粘贴（中文联系人 + Qt 微信）
    force_paste = bool(get_desktop_target().get("search_xy"))
    if hwnd:
        return deliver_text_to_hwnd(
            hwnd, text or "", clear=bool(clear), force_paste=force_paste
        )
    if clear:
        try:
            _send_keys_named(["ctrl", "a"])
            time.sleep(0.05)
            _send_keys_named(["delete"])
            time.sleep(0.05)
        except Exception:
            pass
    if force_paste or any(ord(c) > 127 for c in (text or "")):
        try:
            from desktop_input import _paste_unicode_via_clipboard

            _paste_unicode_via_clipboard(text or "")
            return {"ok": True, "via": "clipboard_paste", "hwnd": 0, "text_length": len(text or "")}
        except Exception:
            pass
    sendinput_type_text(text or "")
    return {"ok": True, "via": "sendinput_fallback", "hwnd": 0, "text_length": len(text or "")}


def wechat_send_message(contact: str, text: str) -> Dict[str, Any]:
    """已废弃的应用专用宏。请用通用 windows_* + Skill 配方，勿在核心路径调用。

    保留函数仅为兼容旧 MCP/测试；默认不注册到外层 FC。
    """
    return {
        "success": False,
        "verified": False,
        "deprecated": True,
        "error": (
            "wechat_send_message 已从核心路径移除（禁止应用死模板）。"
            "请：windows_focus_app → windows_press_key / windows_type_text，"
            "并用 get_screen_text 观察；或让 Hermes skill_view(testory-windows-desktop) 按通用原语编排。"
        ),
        "suggestion": (
            "应用专属流程（如某 IM 发消息）应写在 Skill 文档中，由 Agent 逐步调用通用工具完成。"
        ),
        "contact": (contact or "").strip(),
        "text": (text or "").strip(),
    }


def _is_type_content_click_phrase(description: str) -> bool:
    """用户把「编辑内容为…」误当成 click 目标时识别。"""
    d = (description or "").strip()
    if not d:
        return False
    if re.search(r"(编辑内容|输入内容|写入内容|填写内容)", d):
        return True
    if re.match(r"^(编辑|输入|写入|填写)(内容)?[为：:].+", d):
        return True
    return False


def _normalize_click_search_terms(desc: str) -> List[str]:
    """从点击描述抽出短标签；丢掉易误命中菜单的「编辑内容」类短语。"""
    quoted = re.findall(r"[「『\"'‘’“”]([^「『\"'‘’””]+)[」』\"'‘’””]", desc or "")
    search_terms = [q.strip() for q in quoted if q.strip()] or [(desc or "").strip()]
    out: List[str] = []
    for term in search_terms:
        cleaned = re.sub(
            r"(按钮|输入框|搜索框|文本框|链接|图标|联系人|会话|窗口|菜单项)$",
            "",
            term,
        ).strip()
        if cleaned in ("编辑内容", "输入内容", "写入内容", "填写内容"):
            continue
        cleaned2 = re.sub(r"内容$", "", cleaned).strip()
        for cand in (cleaned, cleaned2, term):
            if not cand or cand in out or cand in ("编辑内容", "输入内容"):
                continue
            if len(cand) > 24 or re.search(r"[为：:，,。]", cand):
                continue
            out.append(cand)
    return out or ([((desc or "").strip()[:24])] if (desc or "").strip() else [])


def _uia_score_name_term(name: str, term: str) -> float:
    """名称匹配分；禁止短菜单名靠「在长 term 里」误高分（编辑 ⊂ 编辑内容）。"""
    name_l = (name or "").strip().lower()
    tl = (term or "").strip().lower()
    if not name_l or not tl:
        return 0.0
    if name_l == tl:
        return 1.0
    if tl in name_l:
        return 0.9
    if name_l in tl:
        if len(name_l) <= 4 and len(tl) >= len(name_l) + 2:
            return 0.35
        return 0.7
    return 0.0


def _uia_find_candidates(description: str, max_candidates: int = 8) -> List[Dict[str, Any]]:
    desc = (description or "").strip()
    if not desc:
        return []
    candidates: List[Dict[str, Any]] = []
    try:
        from pywinauto import Desktop  # type: ignore

        desktop = Desktop(backend="uia")
        # 在前台窗口内搜
        try:
            import ctypes

            fg = ctypes.windll.user32.GetForegroundWindow()
            roots = []
            if fg:
                try:
                    roots.append(desktop.window(handle=fg))
                except Exception:
                    pass
            if not roots:
                roots = list(desktop.windows())[:6]
        except Exception:
            roots = list(desktop.windows())[:6]

        needle = desc.lower()
        search_terms = _normalize_click_search_terms(desc)

        seen = set()
        for root in roots:
            try:
                descendants = root.descendants()
            except Exception:
                continue
            for el in descendants:
                try:
                    name = (el.window_text() or el.element_info.name or "").strip()
                    if not name:
                        continue
                    name_l = name.lower()
                    score = 0.0
                    for term in search_terms:
                        score = max(score, _uia_score_name_term(name, term))
                    if score < 0.7 and needle not in name_l:
                        continue
                    if score < 0.7:
                        continue
                    rect = el.rectangle()
                    cx = int((rect.left + rect.right) / 2)
                    cy = int((rect.top + rect.bottom) / 2)
                    key = (name, cx, cy)
                    if key in seen:
                        continue
                    seen.add(key)
                    ctype = ""
                    try:
                        ctype = str(el.element_info.control_type or "")
                    except Exception:
                        pass
                    ct_l = ctype.lower()
                    if ct_l in ("menuitem", "menu", "menubar") and score < 0.99:
                        score *= 0.45
                    candidates.append(
                        {
                            "name": name,
                            "x": cx,
                            "y": cy,
                            "control_type": ctype,
                            "score": score,
                            "via": "uia",
                        }
                    )
                    if len(candidates) >= max_candidates * 2:
                        break
                except Exception:
                    continue
            if len(candidates) >= max_candidates * 2:
                break
    except Exception as e:
        uat_logger.debug("UIA find candidates failed: %s", e)

    candidates.sort(key=lambda c: (-float(c.get("score") or 0), c.get("name") or ""))
    return candidates[:max_candidates]


def _ocr_text_matches_term(text: str, term: str) -> bool:
    """OCR 模糊匹配：容忍「搜索」被拆字/形近误识。"""
    t = (text or "").strip()
    term = (term or "").strip()
    if not t or not term:
        return False
    # 短 OCR 块嵌在长「编辑内容」类 term 中：不当作命中
    if t in term and len(t) <= 4 and len(term) >= len(t) + 2:
        if term.startswith(t) and not term == t:
            return False
    if term in t or t in term:
        return True
    tl, term_l = t.lower(), term.lower()
    if term_l in tl or tl in term_l:
        if tl in term_l and len(tl) <= 4 and len(term_l) >= len(tl) + 2:
            return False
        return True
    # 搜索相关：常见误识
    if term in ("搜索", "搜索框") or "search" in term_l:
        compact = re.sub(r"\s+", "", t)
        if any(k in compact for k in ("搜索", "搜素", "索搜", "Search", "search", "SEARCH")):
            return True
        # 单字「搜」且块很短（占位灰字常被拆）
        if compact in ("搜", "索") and len(compact) <= 2:
            return True
    return False


def _ocr_find_candidates(description: str, png: bytes) -> List[Dict[str, Any]]:
    from desktop_ocr import extract_text_blocks, extract_text_blocks_roi

    search_like = any(k in (description or "").lower() for k in ("搜索框", "搜索", "search"))
    min_conf = 0.28 if search_like else 0.5
    blocks = extract_text_blocks(png, min_confidence=min_conf)
    # 搜索：额外扫左上 ROI（放大），微信灰字占位 Tesseract 整图常漏
    if search_like and png:
        try:
            roi = extract_text_blocks_roi(
                png,
                left_ratio=0.0,
                top_ratio=0.0,
                right_ratio=0.45,
                bottom_ratio=0.22,
                scale=2.5,
                min_confidence=0.2,
            )
            blocks = list(blocks) + list(roi)
        except Exception:
            pass

    quoted = re.findall(r"[「『\"'‘’“”]([^「『\"'‘’””]+)[」』\"'‘’””]", description or "")
    terms = [q.strip() for q in quoted if q.strip()]
    if not terms:
        cleaned = re.sub(
            r"(按钮|输入框|搜索框|文本框|链接|图标|联系人|会话|窗口|菜单项)",
            "",
            description or "",
        ).strip()
        terms = [cleaned] if cleaned else [description or ""]
    if search_like and "搜索" not in terms:
        terms = ["搜索", "Search"] + terms

    out: List[Dict[str, Any]] = []
    seen_xy = set()
    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        for term in terms:
            if term and _ocr_text_matches_term(text, term):
                l, t, r, bot = b["bbox"]
                cx, cy = int((l + r) / 2), int((t + bot) / 2)
                key = (cx // 4, cy // 4)
                if key in seen_xy:
                    break
                seen_xy.add(key)
                out.append(
                    {
                        "name": text,
                        "x": cx,
                        "y": cy,
                        "bbox": b["bbox"],
                        "confidence": b.get("confidence"),
                        "score": 0.75,
                        "via": "ocr",
                    }
                )
                break
    return out


def _verify_typed_text_on_screen(
    token: str, *, field: str = "auto"
) -> Dict[str, Any]:
    """OCR 真实验证：输入内容是否出现在屏幕上（禁止假成功）。"""
    token = (token or "").strip()
    out: Dict[str, Any] = {"ok": False, "token": token, "texts": []}
    if not token:
        return out
    try:
        from screen_tools import capture_hwnd_png, capture_for_observation, clear_ocr_cache
        from desktop_ocr import extract_text_blocks_roi, extract_text_from_bytes

        try:
            clear_ocr_cache()
        except Exception:
            pass
        hwnd = int(get_desktop_target().get("hwnd") or 0)
        png = None
        if hwnd:
            png, _ = capture_hwnd_png(hwnd)
        if not png:
            png, _ = capture_for_observation(prefer_foreground=True)
        if not png:
            out["error"] = "截屏失败"
            return out
        phase = (field or "auto").strip().lower()
        if phase == "auto":
            phase = "compose" if get_input_phase() == "compose" else "search"
        # 搜索栏 ROI（上半偏左）或消息栏 ROI（底部）+ 全图
        if phase == "compose":
            blocks = extract_text_blocks_roi(
                png,
                left_ratio=0.25,
                top_ratio=0.72,
                right_ratio=1.0,
                bottom_ratio=1.0,
                scale=2.2,
                min_confidence=0.12,
            )
        else:
            blocks = extract_text_blocks_roi(
                png,
                left_ratio=0.0,
                top_ratio=0.0,
                right_ratio=0.55,
                bottom_ratio=0.45,
                scale=2.5,
                min_confidence=0.12,
            )
        texts = [str(b.get("text") or "") for b in blocks if b.get("text")]
        full = extract_text_from_bytes(png) or ""
        blob = " ".join(texts) + " " + full
        out["texts"] = texts[:24]
        out["field"] = phase
        compact_blob = re.sub(r"\s+", "", blob)
        compact_tok = re.sub(r"\s+", "", token)
        if compact_tok and compact_tok in compact_blob:
            out["ok"] = True
            out["match"] = "exact"
            return out
        if compact_tok.isascii() and compact_tok.lower() in compact_blob.lower():
            out["ok"] = True
            out["match"] = "ascii_ci"
            return out
        if (not compact_tok.isascii()) and len(compact_tok) >= 2:
            # 连续 2～3 字子串（中文 OCR 常漏字，但结果列表可能露出联系人名）
            ok_sub = False
            win = 3 if len(compact_tok) >= 3 else 2
            for i in range(0, max(1, len(compact_tok) - win + 1)):
                sub = compact_tok[i : i + win]
                if sub and sub in compact_blob:
                    ok_sub = True
                    out["matched_sub"] = sub
                    break
            if ok_sub:
                out["ok"] = True
                out["partial"] = True
                out["match"] = "cjk_sub"
                return out
        out["error"] = "OCR 未在画面上看到输入内容"
        out["blob_preview"] = compact_blob[:120]
        return out
    except Exception as e:
        out["error"] = str(e)[:200]
        return out


def _run_one_type_strategy(hwnd: int, text: str, strategy: str, *, clear: bool = False) -> Dict[str, Any]:
    """执行单一灌字策略（不清空以外的策略编排）。"""
    from desktop_input import (
        paste_text_via_ctrl_v,
        paste_text_via_wm_paste,
        postmessage_type_text_to_hwnd,
        sendinput_type_text,
        force_focus_hwnd,
        deliver_keys_to_hwnd,
        uia_set_value_in_hwnd,
        _paste_unicode_via_clipboard,
    )

    hwnd = int(hwnd or 0)
    raw = str(text if text is not None else "")
    if clear and hwnd:
        try:
            deliver_keys_to_hwnd(hwnd, ["ctrl", "a"])
            time.sleep(0.04)
            for _ in range(6):
                postmessage_type_text_to_hwnd(hwnd, "\b")
            time.sleep(0.05)
        except Exception:
            pass
    try:
        if strategy == "uia":
            ok = bool(hwnd and uia_set_value_in_hwnd(hwnd, raw))
            return {
                "ok": ok,
                "via": "uia_value",
                "hwnd": hwnd,
                "text_length": len(raw),
                "error": "" if ok else "uia set_value 失败",
            }
        if strategy == "clipboard_ctrl_v":
            return paste_text_via_ctrl_v(hwnd, raw)
        if strategy == "wm_paste":
            return paste_text_via_wm_paste(hwnd, raw)
        if strategy == "wm_char":
            return postmessage_type_text_to_hwnd(hwnd, raw)
        if strategy == "sendinput_unicode":
            if hwnd:
                force_focus_hwnd(hwnd, retries=2)
            sendinput_type_text(raw)
            return {"ok": True, "via": "sendinput_unicode", "hwnd": hwnd, "text_length": len(raw)}
        if strategy == "sendinput":
            if hwnd:
                force_focus_hwnd(hwnd, retries=2)
            if any(ord(c) > 127 for c in raw):
                _paste_unicode_via_clipboard(raw)
                return {"ok": True, "via": "clipboard_paste", "hwnd": hwnd, "text_length": len(raw)}
            sendinput_type_text(raw)
            return {"ok": True, "via": "sendinput_fallback", "hwnd": hwnd, "text_length": len(raw)}
        return {"ok": False, "via": strategy, "error": f"未知策略 {strategy}"}
    except Exception as e:
        return {"ok": False, "via": strategy, "error": str(e)[:200]}


def _type_observe_act_verify(
    hwnd: int,
    text: str,
    *,
    clear: bool,
    search_armed: bool,
    before_hash: str,
    frame_id: str,
    field: str = "auto",
) -> Dict[str, Any]:
    """观察→灌字→再观察：多策略，画面证据才算成功（禁止 soft_verify 假成功）。"""
    expect = (text or "").strip()
    prefer_qt = bool(search_armed or _is_qt_wechat_target(hwnd))
    has_cjk = any(ord(c) > 127 for c in expect)
    if prefer_qt:
        order = (
            ["clipboard_ctrl_v", "wm_paste", "wm_char", "sendinput_unicode"]
            if has_cjk
            else ["wm_char", "clipboard_ctrl_v", "wm_paste"]
        )
    else:
        order = ["uia", "clipboard_ctrl_v", "wm_char", "sendinput"]

    attempts: List[Dict[str, Any]] = []
    last_ocr: Dict[str, Any] = {}
    last_delivery: Dict[str, Any] = {}
    need_clear = bool(clear) or search_armed
    verify_field = (field or "auto").strip().lower()
    if verify_field == "auto":
        verify_field = "search" if search_armed else (
            "compose" if get_input_phase() == "compose" else "auto"
        )

    for i, strategy in enumerate(order):
        # 仅首轮策略复点搜索；后续换通道不再反复物理点击
        if search_armed and i == 0:
            _reclick_armed_search_if_needed()
            time.sleep(_pace_val("strategy_gap_sec"))
        delivered = _run_one_type_strategy(
            hwnd, text, strategy, clear=(need_clear and i == 0)
        )
        time.sleep(_pace_val("type_settle_sec"))
        ocr_check = (
            _verify_typed_text_on_screen(expect, field=verify_field)
            if expect
            else {"ok": False}
        )
        last_ocr = ocr_check
        last_delivery = delivered
        attempts.append(
            {
                "strategy": strategy,
                "delivery": {k: delivered.get(k) for k in ("ok", "via", "error", "chars")},
                "ocr_ok": bool(ocr_check.get("ok")),
                "ocr_match": ocr_check.get("match"),
            }
        )
        if ocr_check.get("ok"):
            cap = capture_after_action(hwnd, before_hash=before_hash, require_change=False)
            return {
                "ok": True,
                "verified": True,
                "delivery": delivered,
                "ocr_check": ocr_check,
                "capture_after": cap,
                "attempts": attempts,
                "frame_id": frame_id,
                "strategy": strategy,
            }
        # 投递失败则继续下一策略；投递成功但 OCR 未看到也继续（换通道）
        if search_armed and i < len(order) - 1:
            # 清空后再试下一策略，避免残留混字
            try:
                _run_one_type_strategy(hwnd, "", "wm_char", clear=True)
            except Exception:
                pass

    cap = capture_after_action(hwnd, before_hash=before_hash, require_change=False)
    return {
        "ok": False,
        "verified": False,
        "delivery": last_delivery,
        "ocr_check": last_ocr,
        "capture_after": cap,
        "attempts": attempts,
        "frame_id": frame_id,
        "error": (last_ocr.get("error") if last_ocr else None)
        or "多策略输入后屏幕仍未见文字",
    }


def _geometry_wechat_search_target(hwnd: int = 0) -> Optional[Dict[str, Any]]:
    """微信会话列表顶部搜索栏布局估算（OCR 常漏灰字「搜索」时的兜底）。"""
    hwnd = int(hwnd or get_desktop_target().get("hwnd") or 0)
    title = _hwnd_title(hwnd)
    tgt = get_desktop_target()
    blob = f"{title} {tgt.get('label') or ''} {tgt.get('title') or ''} {tgt.get('process') or ''}".lower()
    if not any(k in blob for k in ("微信", "wechat", "weixin")):
        return None
    if not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left, top = int(rect.left), int(rect.top)
        w = max(1, int(rect.right - rect.left))
        h = max(1, int(rect.bottom - rect.top))
        # 左窄栏图标 + 会话列表顶栏搜索输入区
        x = left + max(72, int(w * 0.20))
        y = top + max(40, int(h * 0.08))
        x = min(x, left + int(w * 0.36))
        y = min(y, top + int(h * 0.14))
        return {
            "name": "搜索栏(布局估算)",
            "x": int(x),
            "y": int(y),
            "score": 0.58,
            "via": "geometry_wechat_search",
        }
    except Exception:
        return None


def _vlm_find_point(description: str, png: bytes) -> Optional[Dict[str, Any]]:
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(png))
        w, h = img.size
        from ai_vision_grounding import ground_element_from_png

        hit = ground_element_from_png(png, description, viewport_w=w, viewport_h=h)
        if hit:
            return {
                "name": description,
                "x": int(hit.cx),
                "y": int(hit.cy),
                "score": 0.6,
                "via": "vlm",
            }
    except Exception as e:
        uat_logger.debug("vlm ground failed: %s", e)
    return None


def _nearby_texts(x: int, y: int, radius: int = 120) -> List[str]:
    try:
        from screen_tools import get_screen_text

        res = get_screen_text()
        blocks = res.get("blocks") or []
        near = []
        for b in blocks:
            l, t, r, bot = b.get("bbox") or [0, 0, 0, 0]
            cx, cy = (l + r) / 2, (t + bot) / 2
            if abs(cx - x) <= radius and abs(cy - y) <= radius:
                near.append(b.get("text") or "")
        return [t for t in near if t][:12]
    except Exception:
        return []


def _screen_text_list() -> List[str]:
    try:
        from screen_tools import get_screen_text

        res = get_screen_text()
        return list(res.get("texts") or [])[:40]
    except Exception:
        return []


def _foreground_is_wechat() -> bool:
    """兼容旧测试；核心路径不应依赖应用名判断。"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return False
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = (buf.value or "").lower()
        return any(k in title for k in ("微信", "wechat", "weixin"))
    except Exception:
        return False


def _try_search_hotkey_shortcut(description: str) -> Optional[Dict[str, Any]]:
    """通用：描述像「搜索框」时尝试 Ctrl+F。

    组合键必须走 SendInput；不能仅凭像素变化判成功（打出 f 也会变）。
    """
    desc = (description or "").strip()
    if not desc:
        return None
    search_like = any(k in desc.lower() for k in ("搜索框", "搜索", "search"))
    if not search_like:
        return None
    hwnd = int(get_desktop_target().get("hwnd") or 0)
    try:
        from desktop_input import deliver_keys_to_hwnd, force_focus_hwnd

        if hwnd:
            force_focus_hwnd(hwnd)
            time.sleep(0.1)
        before = _capture_target_hash(hwnd)
        delivery = deliver_keys_to_hwnd(hwnd, ["ctrl", "f"]) if hwnd else {"ok": False}
        if not delivery.get("ok"):
            _send_keys_named(["ctrl", "f"])
            delivery = {"ok": True, "via": "send_keys_named"}
        _wait_stable_quiet(500)
        verified = _search_ui_looks_open(hwnd)
        cap = capture_after_action(hwnd, before_hash=before, require_change=False)
        if not verified:
            return {
                "success": False,
                "verified": False,
                "error": "Ctrl+F 后未确认搜索框已打开（可能键入了普通字符）",
                "delivery": delivery,
                "capture_after": cap,
                "suggestion": "请改用 windows_click_element('搜索') 点击左侧搜索框，或先 get_screen_text 再点选。",
            }
        # 尽量武装搜索坐标，供后续 type 复点
        try:
            from screen_tools import capture_for_observation

            png, cap_meta = capture_for_observation(prefer_foreground=True)
            if png:
                off_x = int((cap_meta or {}).get("left") or 0)
                off_y = int((cap_meta or {}).get("top") or 0)
                ocr_cands = _ocr_find_candidates("搜索", png)
                if ocr_cands:
                    ocr_cands.sort(key=lambda c: int(c.get("y") or 0))
                    c0 = ocr_cands[0]
                    arm_search_input_focus(int(c0["x"]) + off_x, int(c0["y"]) + off_y)
        except Exception:
            pass
        return {
            "success": True,
            "verified": True,
            "description": desc,
            "via": "search_ctrl_f",
            "matched": "搜索快捷键(Ctrl+F)",
            "delivery": delivery,
            "capture_after": cap,
            "search_armed": True,
            "suggestion": "搜索已打开，请接着 windows_type_text 输入关键词。",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Ctrl+F 失败: {e}",
            "suggestion": "确认目标窗口可交互后再试；或直接点击「搜索」控件。",
        }


def _search_ui_looks_open(hwnd: int) -> bool:
    """粗判搜索框是否已激活。

    新版微信为 Qt 壳，UIA 几乎无 Edit；需结合焦点名 + OCR 特征（取消/联系人分组等）。
    """
    try:
        from pywinauto import Desktop  # type: ignore

        if not hwnd:
            return False
        win = Desktop(backend="uia").window(handle=int(hwnd))
        focused = None
        try:
            focused = win.get_focus()
        except Exception:
            focused = None
        if focused is not None:
            name = ""
            ctype = ""
            try:
                name = (focused.window_text() or focused.element_info.name or "").strip()
            except Exception:
                pass
            try:
                ctype = str(focused.element_info.control_type or "")
            except Exception:
                pass
            blob = f"{name} {ctype}".lower()
            if "search" in blob or "搜索" in name:
                return True
        wr = win.rectangle()
        top_band = wr.top + max(40, int(wr.height() * 0.35))
        for el in win.descendants(control_type="Edit")[:40]:
            try:
                name = (el.window_text() or el.element_info.name or "").strip()
                if "搜索" in name or "search" in name.lower():
                    rect = el.rectangle()
                    if rect.top < top_band:
                        return True
            except Exception:
                continue
    except Exception:
        pass

    # Qt 微信：OCR 兜底（登录页勿误判为搜索已开）
    try:
        obs = observe_screen_texts(prefer_foreground=True)
        texts = [str(t) for t in (obs.get("texts") or []) if t]
        blob = " ".join(texts)
        if any(k in blob for k in ("扫码登录", "登录微信", "进入微信", "二维码")) and "取消" not in blob:
            return False
        has_search = any(("搜索" in t) or ("search" in t.lower()) for t in texts)
        markers = ("取消", "联系人", "群聊", "功能", "搜一搜", "网络查找", "最常使用")
        if has_search and any(m in blob for m in markers):
            return True
        # 刚点开搜索时常仅剩占位「搜索」+ 左侧列表变化；有「取消」更稳
        if "取消" in blob:
            return True
    except Exception:
        pass
    return False


def _probe_search_accepts_input(hwnd: int) -> Dict[str, Any]:
    """轻量探针：不再往搜索框打可见字符（避免残留 Z/字母混进联系人）。

    仅检查目标窗是否仍在前台；真正进字由后续 type_text 验证。
    """
    from desktop_input import get_foreground_hwnd

    hwnd = int(hwnd or 0)
    fg = int(get_foreground_hwnd() or 0)
    ok = bool(hwnd and fg == hwnd)
    return {
        "ok": ok,
        "fg_hwnd": fg,
        "hwnd": hwnd,
        "note": "no_visible_probe_char",
    }


def _activate_wechat_search_for_input() -> Dict[str, Any]:
    """激活微信搜索：刷新目标绑定 → 多策略捕获前台 → 物理点搜索栏 → Ctrl+F。"""
    from desktop_input import deliver_keys_to_hwnd, get_foreground_hwnd, screen_click

    cap = capture_desktop_target_foreground(step="激活搜索")
    if not cap.get("ok"):
        return {
            "ok": False,
            "error": cap.get("error") or "未能捕获微信窗口",
            "suggestion": cap.get("suggestion"),
            "capture": cap,
        }
    hwnd = int(cap.get("hwnd") or get_desktop_target().get("hwnd") or 0)
    geo = _geometry_wechat_search_target(hwnd)
    if not geo:
        return {"ok": False, "error": "无法估算搜索栏坐标", "hwnd": hwnd}
    x, y = int(geo["x"]), int(geo["y"]) + 4
    steps: List[str] = ["capture_fg_ok"]

    # 物理单击搜索栏
    screen_click(x, y)
    time.sleep(0.2)
    steps.append("physical_click_search")
    arm_search_input_focus(x, y)

    # 若被浏览器抢回，再捕获一次再 Ctrl+F
    if int(get_foreground_hwnd() or 0) != hwnd:
        cap2 = capture_desktop_target_foreground(step="Ctrl+F前")
        steps.append("recapture_before_hotkey")
        hwnd = int(cap2.get("hwnd") or hwnd)
        if not cap2.get("ok"):
            return {
                "ok": False,
                "error": "点击搜索后目标窗失去前台且未能重新捕获",
                "x": x,
                "y": y,
                "steps": steps,
                "capture": cap2,
            }

    delivery = deliver_keys_to_hwnd(hwnd, ["ctrl", "f"])
    steps.append("ctrl_f_after_capture")
    time.sleep(0.15)
    if int(get_foreground_hwnd() or 0) == hwnd:
        screen_click(x, y)
        time.sleep(0.12)
        steps.append("physical_click_after_hotkey")
        arm_search_input_focus(x, y)

    probe = _probe_search_accepts_input(hwnd)
    if not probe.get("ok"):
        # 再抢一次前台，不消极放弃
        cap3 = capture_desktop_target_foreground(step="搜索激活核验")
        probe = _probe_search_accepts_input(int(cap3.get("hwnd") or hwnd))
        steps.append("recapture_probe")
        if not probe.get("ok"):
            return {
                "ok": False,
                "error": "已尝试捕获窗口并激活搜索，但目标窗仍未保持前台",
                "x": x,
                "y": y,
                "steps": steps,
                "probe": probe,
                "delivery_ctrl_f": delivery,
                "suggestion": (
                    "请确认目标应用主窗口可见；可再调 windows_focus_app(应用名)。"
                    if not _is_wechat_desktop_target()
                    else "请确认微信主窗口可见；可再调 windows_focus_app('微信')。"
                ),
            }
    return {
        "ok": True,
        "x": x,
        "y": y,
        "via": "wechat_search_activate",
        "matched": geo.get("name") or "搜索栏",
        "steps": steps,
        "probe": probe,
        "delivery_ctrl_f": delivery,
        "search_armed": True,
        "capture": cap,
    }


def _try_wechat_search_shortcut(description: str) -> Optional[Dict[str, Any]]:
    """兼容旧名：转发到通用搜索快捷键。"""
    return _try_search_hotkey_shortcut(description)


def _pick_search_uia_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从多个「搜索」相关 UIA 候选中挑最像全局搜索框的一个。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _name(c: Dict[str, Any]) -> str:
        return str(c.get("name") or "").strip()

    # 精确优先
    for want in ("搜索", "Search", "search"):
        for c in candidates:
            if _name(c) == want:
                return c
    # 微信常见：用户搜索：
    for c in candidates:
        n = _name(c)
        if n.startswith("用户搜索") or n in ("用户搜索：", "用户搜索:"):
            return c
    # 排除标签页搜索等噪音
    filtered = [
        c
        for c in candidates
        if "标签" not in _name(c) and "tab" not in _name(c).lower()
    ]
    pool = filtered or list(candidates)
    pool.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return pool[0]


def _desktop_target_blob() -> str:
    return (
        f"{get_desktop_target().get('label') or ''} "
        f"{get_desktop_target().get('title') or ''} "
        f"{get_desktop_target().get('process') or ''} "
        f"{_hwnd_title(int(get_desktop_target().get('hwnd') or 0))}"
    ).lower()


def _is_wechat_desktop_target() -> bool:
    blob = _desktop_target_blob()
    return any(k in blob for k in ("微信", "wechat", "weixin"))


def _pick_search_list_candidate(
    candidates: List[Dict[str, Any]],
    *,
    query: str = "",
    all_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """搜索下拉多候选：优先主列表首条，避开搜索框原文与次要分区（网络/历史等）。

    适用于任意带搜索结果列表的桌面应用；分区标题含中英常见词。
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    q = (query or "").strip()
    section_y: Dict[str, int] = {}
    for b in all_blocks or []:
        text = str(b.get("text") or "").strip()
        if not text:
            continue
        bbox = b.get("bbox")
        try:
            if bbox and len(bbox) >= 4:
                cy = int((int(bbox[1]) + int(bbox[3])) / 2)
            else:
                cy = int(b.get("y") or 0)
        except Exception:
            continue
        if not cy:
            continue
        if text in ("联系人",) or text.startswith("联系人"):
            section_y["contact"] = min(section_y.get("contact", cy), cy)
        elif any(k in text for k in ("搜索网络", "搜一搜", "网络结果")):
            section_y["web"] = min(section_y.get("web", cy), cy)
        elif text in ("群聊", "群聊聊天") or text.startswith("群聊"):
            section_y["group"] = min(section_y.get("group", cy), cy)
        elif text in ("聊天记录",) or text.startswith("聊天记录"):
            section_y["history"] = min(section_y.get("history", cy), cy)

    contact_top = section_y.get("contact")
    # 网络/群聊/聊天记录 作为联系人区下界
    contact_bottom = min(
        [v for k, v in section_y.items() if k != "contact"] or [10**9]
    )

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in candidates:
        try:
            y = int(c.get("y") or 0)
            x = int(c.get("x") or 0)
        except Exception:
            continue
        name = str(c.get("name") or "").strip()
        # 过靠上：多半是搜索框里刚输入的查询串
        if y < 70:
            score = -50.0
        else:
            score = 10.0
        # 精确等于查询：列表行；若仍在搜索框高度则降权
        if q and (name == q or q in name):
            score += 8.0
        if contact_top is not None and y >= contact_top - 8:
            score += 40.0
            if y < contact_bottom:
                score += 30.0
            else:
                score -= 25.0  # 落在网络/群聊区
        elif section_y.get("web") is not None and y >= int(section_y["web"]) - 8:
            score -= 40.0
        # 同分区内越靠上越好（首条联系人）
        score -= y * 0.01
        score -= x * 0.001
        score += float(c.get("score") or 0) * 0.5
        scored.append((score, c))

    if not scored:
        return candidates[0]
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best = scored[0]
    # 全部被判为搜索框噪音时，退回按 Y 排序的第一条（跳过最顶部）
    if best_score < 0:
        below = [c for c in candidates if int(c.get("y") or 0) >= 90]
        pool = below or list(candidates)
        pool.sort(key=lambda c: (int(c.get("y") or 0), int(c.get("x") or 0)))
        return pool[0]
    best = dict(best)
    best["via"] = f"{best.get('via') or 'ocr'}_search_list_pick"
    best["pick_score"] = best_score
    return best


def _pick_wechat_search_result_candidate(
    candidates: List[Dict[str, Any]],
    *,
    query: str = "",
    all_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """兼容旧名 → `_pick_search_list_candidate`。"""
    picked = _pick_search_list_candidate(
        candidates, query=query, all_blocks=all_blocks
    )
    if isinstance(picked, dict) and "search_list_pick" in str(picked.get("via") or ""):
        picked = dict(picked)
        picked["via"] = str(picked["via"]).replace("search_list_pick", "wechat_result_pick")
    return picked


def windows_click_element(description: str) -> Dict[str, Any]:
    desc = (description or "").strip()
    if not desc:
        return {
            "success": False,
            "error": "description 为空",
            "suggestion": "请描述要点击的元素，如「确定按钮」或「搜索框」。",
        }
    if _is_type_content_click_phrase(desc):
        return {
            "success": False,
            "error": "该描述像「输入/编辑正文」，不是可点击控件名",
            "suggestion": (
                "请改用 windows_type_text(要写入的正文)；"
                "新建页用 windows_press_key(Ctrl+N)。不要点菜单「编辑」。"
            ),
            "redirect": "windows_type_text",
        }

    def _work() -> Dict[str, Any]:
        from screen_tools import capture_for_observation

        search_like = any(k in desc.lower() for k in ("搜索框", "搜索", "search"))
        wechat_tgt = _is_wechat_desktop_target()

        # 微信：直接走「Ctrl+F + 物理点击 + WM_CHAR 探针」，避免假激活
        if search_like and wechat_tgt:
            act = _activate_wechat_search_for_input()
            if act.get("ok"):
                return {
                    "success": True,
                    "verified": True,
                    "description": desc,
                    "x": act.get("x"),
                    "y": act.get("y"),
                    "matched": act.get("matched"),
                    "via": act.get("via"),
                    "search_armed": True,
                    "probe": act.get("probe"),
                    "steps_done": act.get("steps") or ["search_activate"],
                    "flow_halt": False,
                    "suggestion": "搜索已可输入，请立刻 windows_type_text 输入联系人。",
                }
            return {
                "success": False,
                "verified": False,
                "flow_halt": True,
                "error": act.get("error") or "无法激活可输入的搜索框",
                "probe": act.get("probe"),
                "steps_done": act.get("steps") or [],
                "suggestion": act.get("suggestion")
                or "请手动点击搜索框出现光标后重试。",
            }

        png, cap_meta = capture_for_observation(prefer_foreground=True)
        off_x = int((cap_meta or {}).get("left") or 0)
        off_y = int((cap_meta or {}).get("top") or 0)
        target: Optional[Dict[str, Any]] = None
        all_ocr_blocks: List[Dict[str, Any]] = []
        if png and not search_like:
            try:
                from desktop_ocr import extract_text_blocks

                all_ocr_blocks = list(extract_text_blocks(png, min_confidence=0.25) or [])
            except Exception:
                all_ocr_blocks = []

        # 搜索：OCR（含左上放大）→ UIA → 微信布局估算 → Ctrl+F
        if search_like and png:
            ocr_cands = _ocr_find_candidates("搜索", png)
            for c in ocr_cands:
                c["x"] = int(c["x"]) + off_x
                c["y"] = int(c["y"]) + off_y
            if ocr_cands:
                # 优先窗口上半、偏左的候选（会话列表搜索）
                ocr_cands.sort(key=lambda c: (int(c.get("y") or 0), int(c.get("x") or 0)))
                target = ocr_cands[0]

        if target is None:
            uia_cands = _uia_find_candidates(desc)
            if search_like:
                for c in _uia_find_candidates("搜索"):
                    if c not in uia_cands:
                        uia_cands.append(c)
                target = _pick_search_uia_candidate(uia_cands)
            elif len(uia_cands) > 1:
                # 通用：多候选时挑列表项（避开搜索框原文/次要分区），不再直接 flow_halt
                target = _pick_wechat_search_result_candidate(
                    uia_cands, query=desc, all_blocks=all_ocr_blocks
                ) or uia_cands[0]
            elif len(uia_cands) == 1:
                target = uia_cands[0]
            elif uia_cands and float(uia_cands[0].get("score") or 0) >= 0.99:
                target = uia_cands[0]

        if target is None and search_like:
            hwnd0 = int(get_desktop_target().get("hwnd") or 0)
            geo = _geometry_wechat_search_target(hwnd0)
            if geo:
                target = geo

        if target is None and png and not search_like:
            ocr_cands = _ocr_find_candidates(desc, png)
            for c in ocr_cands:
                c["x"] = int(c["x"]) + off_x
                c["y"] = int(c["y"]) + off_y
            if len(ocr_cands) > 1:
                blocks_screen: List[Dict[str, Any]] = []
                for b in all_ocr_blocks:
                    bb = b.get("bbox")
                    if not bb or len(bb) < 4:
                        continue
                    blocks_screen.append(
                        {
                            "text": b.get("text"),
                            "bbox": (
                                int(bb[0]) + off_x,
                                int(bb[1]) + off_y,
                                int(bb[2]) + off_x,
                                int(bb[3]) + off_y,
                            ),
                        }
                    )
                target = _pick_wechat_search_result_candidate(
                    ocr_cands, query=desc, all_blocks=blocks_screen or all_ocr_blocks
                )
            elif len(ocr_cands) == 1:
                target = ocr_cands[0]

        # 搜索仍找不到：Ctrl+F（SendInput + UI 核验）
        if target is None and search_like:
            shortcut = _try_search_hotkey_shortcut(desc)
            if shortcut and shortcut.get("success"):
                return shortcut
            # 热键也失败则直接失败，避免模型空转点「左上角图标」直到超时
            return {
                "success": False,
                "verified": False,
                "flow_halt": True,
                "error": "无法定位搜索框（OCR 未识别到「搜索」，布局/Ctrl+F 也未确认成功）",
                "suggestion": (
                    "请确认目标应用主界面可见；可手动点开搜索后说「继续」。"
                    "长期可安装 PaddleOCR 提升中文识别：pip install paddleocr。"
                ),
                "screen_text": _screen_text_list(),
            }

        # 3) VLM grounding（默认关闭）
        if (
            target is None
            and png
            and os.environ.get("WINDOWS_CLICK_VLM", "").strip().lower()
            in ("1", "true", "yes", "on")
        ):
            vlm = _vlm_find_point(desc, png)
            if vlm:
                vlm["x"] = int(vlm["x"]) + off_x
                vlm["y"] = int(vlm["y"]) + off_y
                target = vlm

        if not target:
            texts = _screen_text_list()
            from desktop_ocr import engine_name, ocr_available

            eng = engine_name()
            return {
                "success": False,
                "error": "未找到元素",
                "screen_text": texts,
                "ocr_engine": eng,
                "ocr_available": ocr_available(),
                "capture": cap_meta,
                "suggestion": (
                    "请先 get_screen_text / get_screen_description 观察，"
                    "再按用户目标点击对应控件；勿默认点「搜索」或单独按 Ctrl。"
                    + (
                        " 当前 OCR 引擎不可用，请安装 paddleocr/tesseract。"
                        if not ocr_available()
                        else ""
                    )
                    + (f" 当前文字：{texts[:8]}" if texts else "")
                ),
            }

        x, y = int(target["x"]), int(target["y"])
        hwnd = int(get_desktop_target().get("hwnd") or 0)
        before = _capture_target_hash(hwnd)
        click_via = ""
        try:
            from desktop_input import (
                force_focus_hwnd,
                message_click_at_screen,
                screen_click,
                uia_invoke_or_click_at_screen,
                physical_mouse_enabled,
                no_focus_steal_enabled,
            )

            # 搜索框 / 微信列表：必须物理点击，PostMessage 对 Qt 微信经常无效
            if search_like or wechat_tgt:
                if hwnd and not no_focus_steal_enabled():
                    force_focus_hwnd(hwnd)
                    time.sleep(0.05)
                screen_click(x, y)
                click_via = "physical"
            else:
                # 非 Qt：UIA Invoke → PostMessage → 物理（仅 opt-in）
                uia = uia_invoke_or_click_at_screen(x, y, hwnd=hwnd)
                if uia.get("ok") and uia.get("via") == "uia_invoke":
                    click_via = "uia_invoke"
                else:
                    try:
                        message_click_at_screen(x, y)
                        click_via = "postmessage"
                    except Exception:
                        if physical_mouse_enabled() or not no_focus_steal_enabled():
                            screen_click(x, y)
                            click_via = "physical_fallback"
                        else:
                            raise RuntimeError(
                                uia.get("error")
                                or "后台点击失败（UIA/PostMessage）；可设 DESKTOP_PHYSICAL_MOUSE=1"
                            )
        except Exception as e:
            return {
                "success": False,
                "error": f"点击失败: {e}",
                "x": x,
                "y": y,
                "suggestion": "确认目标窗口可见；后台模式可关闭 DESKTOP_NO_FOCUS_STEAL 或开启物理鼠标。",
            }

        _wait_stable_quiet()
        cap = capture_after_action(hwnd, before_hash=before, require_change=False)
        nearby = _nearby_texts(x, y)
        verified = bool(cap.get("changed") or nearby)
        if search_like:
            search_open = _search_ui_looks_open(hwnd)
            if not search_open and not verified:
                return {
                    "success": False,
                    "verified": False,
                    "error": "已点击搜索相关位置，但未确认搜索框已激活",
                    "description": desc,
                    "x": x,
                    "y": y,
                    "matched": target.get("name"),
                    "via": target.get("via"),
                    "nearby_text": nearby,
                    "capture_after": cap,
                    "suggestion": "请 get_screen_text 确认后重试 click('搜索')；不要继续输入联系人。",
                    "steps_done": ["click_attempted"],
                    "flow_halt": True,
                }
            if search_open:
                verified = True
        if cap.get("unchanged") and not nearby and not (search_like and verified):
            return {
                "success": False,
                "verified": False,
                "error": "已尝试点击，但画面无变化且附近无相关文字，不能认定点击生效。",
                "description": desc,
                "x": x,
                "y": y,
                "matched": target.get("name"),
                "via": target.get("via"),
                "nearby_text": nearby,
                "capture_after": cap,
                "steps_done": ["click_attempted"],
                "flow_halt": True,
            }
        if search_like:
            # 无论 UIA 能否确认，只要本次是搜索点击就武装坐标，供 type 前复点
            arm_search_input_focus(x, y)
        else:
            # 点开会话/消息区：解除搜索武装，进入消息栏阶段
            if wechat_tgt:
                mark_compose_input_phase()
            else:
                clear_search_input_focus()
        return {
            "success": True,
            "verified": verified,
            "description": desc,
            "x": x,
            "y": y,
            "matched": target.get("name"),
            "via": target.get("via"),
            "click_via": click_via,
            "nearby_text": nearby,
            "capture_after": cap,
            "search_armed": bool(search_like),
            "steps_done": ["click_attempted", "click"] if verified else ["click_attempted"],
            "flow_halt": False,
            "suggestion": (
                "搜索已点击，请立刻 windows_type_text 输入关键词（勿先点其他区域）。"
                if search_like
                else (
                    "若已打开会话，请 windows_type_text 输入消息后 Enter；"
                    "若仍在搜索列表，可 windows_press_key('Enter') 打开首条。"
                    if wechat_tgt
                    else "请根据画面继续下一步操作。"
                )
            ),
        }

    try:
        return _run_with_timeout(_work, timeout=12.0)
    except TimeoutError as e:
        return {
            "success": False,
            "error": str(e),
            "screen_text": _screen_text_list(),
            "suggestion": "缩小搜索范围；或用 windows_press_key + windows_type_text。",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)[:300],
            "screen_text": _screen_text_list(),
            "suggestion": "可调用 get_screen_text 重新分析界面。",
        }


def windows_type_text(
    text: str,
    clear: bool = False,
    *,
    expect_text: Optional[str] = None,
    require_change: bool = True,
    field: Optional[str] = None,
) -> Dict[str, Any]:
    raw = str(text if text is not None else "")
    if raw == "" and not clear:
        return {"success": False, "error": "text 为空", "suggestion": "请传入要输入的字符串。"}

    def _work() -> Dict[str, Any]:
        frame = begin_desktop_action_frame(step="windows_type_text")
        if not frame.get("ok"):
            return {
                "success": False,
                "verified": False,
                "flow_halt": True,
                "error": frame.get("error") or "目标窗口不在前台",
                "suggestion": frame.get("suggestion"),
                "fg_title": frame.get("fg_title"),
                "steps_done": ["type_attempted"],
            }
        hwnd = int(frame.get("hwnd") or get_desktop_target().get("hwnd") or 0)
        phase = get_input_phase()
        field_norm = (field or "").strip().lower()
        last_q = str(_desktop_target.get("last_search_query") or "").strip()
        # 已进入会话消息栏，或输入内容明显不是上次搜索词 → 禁止再点回搜索框
        compose_mode = (
            field_norm in ("compose", "message", "chat", "消息", "消息栏")
            or phase == "compose"
            or (bool(last_q) and raw.strip() and raw.strip() != last_q and phase != "search")
        )
        if compose_mode:
            mark_compose_input_phase()
            reclick = {"ok": False, "skipped": True, "reason": "compose_mode"}
            search_armed = False
            type_field = "compose"
        else:
            prior_q = str(_desktop_target.get("last_search_query") or "").strip()
            # 同一搜索词已灌过：禁止再次 type（否则 clear 失败就会叠成「词词」）
            if (
                prior_q
                and raw.strip()
                and (raw.strip() == prior_q or raw.strip() == prior_q + prior_q)
                and bool(get_desktop_target().get("search_xy"))
            ):
                return {
                    "success": True,
                    "verified": True,
                    "skipped": True,
                    "reason": "same_search_query_already_typed",
                    "text_length": len(raw),
                    "search_armed": True,
                    "input_phase": get_input_phase(),
                    "steps_done": ["frame_captured", "skip_duplicate_type"],
                    "suggestion": "搜索词已输入，请 windows_press_key('Enter') 确认结果，勿再 type。",
                }
            reclick = _reclick_armed_search_if_needed()
            search_armed = bool(get_desktop_target().get("search_xy"))
            type_field = "search" if search_armed else (field_norm or "auto")
            if search_armed and raw.strip():
                _desktop_target["last_search_query"] = raw.strip()
        steps = ["frame_captured"]
        if reclick.get("ok"):
            steps.append("reclick_search")

        expect = (expect_text if expect_text is not None else raw).strip()
        # 无期望文本且仅 clear：走旧投递
        if not expect and clear:
            delivered = _type_text_impl("", clear=True, hwnd=hwnd)
            steps.append("clear_only")
            return {
                "success": bool(delivered.get("ok", True)),
                "verified": False,
                "delivery": delivered,
                "frame_id": frame.get("frame_id"),
                "steps_done": steps,
            }

        # 有期望内容：多策略 + OCR 核验（禁止 soft 假成功）
        result = _type_observe_act_verify(
            hwnd,
            raw,
            clear=bool(clear) or search_armed,
            search_armed=search_armed,
            before_hash=str(frame.get("before_hash") or ""),
            frame_id=str(frame.get("frame_id") or ""),
            field=type_field,
        )
        steps.append("type_verify_loop")
        if result.get("ok") and result.get("verified"):
            steps.append("type")
            return {
                "success": True,
                "verified": True,
                "text_length": len(raw),
                "cleared": bool(clear) or search_armed,
                "delivery": result.get("delivery"),
                "reclick_search": reclick,
                "ocr_check": result.get("ocr_check"),
                "capture_after": result.get("capture_after"),
                "attempts": result.get("attempts"),
                "strategy": result.get("strategy"),
                "frame_id": result.get("frame_id"),
                "target": get_desktop_target(),
                "search_armed": search_armed,
                "input_phase": get_input_phase(),
                "steps_done": steps,
                "reply": (
                    "画面已确认输入内容。"
                    if search_armed
                    else "屏幕观察已确认输入。"
                ),
            }

        # 非搜索场景且 require_change=False：允许仅投递成功（兼容旧调用）
        if (
            (not search_armed)
            and (not require_change)
            and bool((result.get("delivery") or {}).get("ok"))
            and not expect
        ):
            steps.append("type_unverified")
            return {
                "success": True,
                "verified": False,
                "text_length": len(raw),
                "delivery": result.get("delivery"),
                "frame_id": frame.get("frame_id"),
                "steps_done": steps,
                "reply": "已投递输入（未要求画面核验）。",
            }

        steps.append("type_ocr_miss")
        return {
            "success": False,
            "verified": False,
            "flow_halt": True,
            "error": result.get("error")
            or ((result.get("ocr_check") or {}).get("error"))
            or "已尝试输入，但屏幕未看到文字，不能认定成功。",
            "text_length": len(raw),
            "cleared": bool(clear) or search_armed,
            "delivery": result.get("delivery"),
            "reclick_search": reclick,
            "ocr_check": result.get("ocr_check"),
            "capture_after": result.get("capture_after"),
            "attempts": result.get("attempts"),
            "frame_id": frame.get("frame_id"),
            "target": get_desktop_target(),
            "search_armed": search_armed,
            "input_phase": get_input_phase(),
            "steps_done": steps,
            "suggestion": (
                "搜索框需先有光标；平台已尝试剪贴板Ctrl+V / WM_PASTE / WM_CHAR。"
                "请确认目标应用前台后重试，或手动点搜索框再说「继续」。"
                if search_armed
                else (
                    "会话消息栏已聚焦时请直接 windows_type_text 消息正文（勿再点搜索）。"
                    "若仍失败：先 click 底部消息输入框，再 type，最后 Enter 发送。"
                    if compose_mode or get_input_phase() == "compose"
                    else "请先 windows_click_element 聚焦输入框，再 windows_type_text。"
                )
            ),
        }

    try:
        return _run_with_timeout(_work, timeout=25.0)
    except TimeoutError as e:
        return {"success": False, "verified": False, "error": str(e), "steps_done": ["type_attempted"]}
    except Exception as e:
        return {
            "success": False,
            "verified": False,
            "error": str(e)[:300],
            "suggestion": "确认输入框已聚焦。",
            "steps_done": ["type_attempted"],
        }


def _parse_key_combo(key: str) -> List[str]:
    """支持 'Enter'、'Ctrl+V'、'ctrl+shift+esc'、'^f'（pywinauto）。

    注意：不能把「Ctrl+F」里的连接符 + 当成 pywinauto 的 Shift(+) 前缀，
    否则会整串当成 ['ctrl+f'] 导致热键失效。
    """
    s = (key or "").strip()
    if not s:
        return []
    # pywinauto 风格：以 ^ % + 开头，或含 {ENTER} 这类
    if s[0] in "^%+" or "{" in s:
        return [s]
    parts = re.split(r"[+\s]+", s)
    out = []
    for p in parts:
        pl = p.strip().lower()
        if not pl:
            continue
        out.append(_KEY_ALIASES.get(pl, pl))
    return out


def _send_keys_named(parts: List[str]) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

    def _key(vk: int, down: bool) -> None:
        inp = INPUT()
        inp.type = 1
        inp.ki = KEYBDINPUT(vk, 0, 0 if down else 0x0002, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    # 单一 pywinauto 风格串
    if len(parts) == 1 and any(ch in parts[0] for ch in "^+%{"):
        from desktop_automation import DesktopAutomation

        DesktopAutomation()._send_hotkey(parts[0])
        return

    mods = []
    main = None
    for p in parts:
        if p in ("ctrl", "shift", "alt", "win"):
            mods.append(_VK[p])
        else:
            main = _VK.get(p) or (ord(p.upper()) if len(p) == 1 else 0)
    if not main and mods:
        # 仅修饰键无意义
        return
    for m in mods:
        _key(m, True)
    if main:
        _key(main, True)
        _key(main, False)
    for m in reversed(mods):
        _key(m, False)


def windows_press_key(key: str, *, require_change: Optional[bool] = None) -> Dict[str, Any]:
    k = (key or "").strip()
    if not k:
        return {"success": False, "error": "key 为空", "suggestion": "例如 Enter、Esc、Tab、Ctrl+F。"}

    def _work() -> Dict[str, Any]:
        parts = _parse_key_combo(k)
        if not parts:
            return {"success": False, "verified": False, "error": f"无法解析按键: {k}"}
        parts_l = [str(p).lower() for p in parts]
        # 禁止单独 Ctrl/Alt：会变成无效操作，后续 f 打进聊天框
        if parts_l and all(p in ("ctrl", "control", "shift", "alt", "win") for p in parts_l):
            return {
                "success": False,
                "verified": False,
                "error": f"不能只按修饰键「{k}」",
                "suggestion": "请一次传入完整组合键，例如 Ctrl+F；微信搜索更推荐 windows_click_element('搜索')。",
            }
        hwnd = int(get_desktop_target().get("hwnd") or 0)
        if hwnd:
            frame = begin_desktop_action_frame(step="windows_press_key")
            if not frame.get("ok"):
                return {
                    "success": False,
                    "verified": False,
                    "flow_halt": True,
                    "error": frame.get("error") or "目标窗口不在前台",
                    "suggestion": frame.get("suggestion"),
                    "fg_title": frame.get("fg_title"),
                    "steps_done": ["press_attempted"],
                }
            before = str(frame.get("before_hash") or "")
            frame_id = str(frame.get("frame_id") or "")
        else:
            before = _capture_target_hash(0)
            frame_id = ""
        # 组合热键默认要求画面变化；纯 Enter/Esc 可由调用方关闭
        auto_require = any(
            p in ("ctrl", "alt", "win", "control") for p in parts_l
        ) or any(p in ("f", "s", "a", "v", "c", "x", "n", "o") for p in parts_l if len(p) == 1)
        need_change = bool(auto_require if require_change is None else require_change)
        delivery: Dict[str, Any] = {}
        try:
            from desktop_input import deliver_keys_to_hwnd

            if hwnd:
                delivery = deliver_keys_to_hwnd(hwnd, parts)
            else:
                _send_keys_named(parts)
                delivery = {"ok": True, "via": "send_keys_named"}
        except Exception as e:
            return {"success": False, "verified": False, "error": f"按键失败: {e}", "key": k}

        if delivery.get("ok") is False:
            return {
                "success": False,
                "verified": False,
                "error": delivery.get("error") or "按键投递失败",
                "key": k,
                "delivery": delivery,
                "suggestion": "请使用完整组合键如 Ctrl+F；搜索优先 windows_click_element('搜索')。",
            }

        # Ctrl+F：额外核验搜索 UI，避免「打出 f」被当成成功
        if (
            "ctrl" in parts_l or "control" in parts_l
        ) and "f" in parts_l:
            _wait_stable_quiet(int(_pace_val("post_key_stable_ms")))
            if not _search_ui_looks_open(hwnd):
                cap = capture_after_action(hwnd, before_hash=before, require_change=False)
                return {
                    "success": False,
                    "verified": False,
                    "flow_halt": True,
                    "error": "Ctrl+F 未打开搜索框（可能只输入了字母 f）",
                    "key": k,
                    "delivery": delivery,
                    "capture_after": cap,
                    "suggestion": "请改用 windows_click_element('搜索')，或先 get_screen_text 定位后再点。",
                }
            cap = capture_after_action(hwnd, before_hash=before, require_change=False)
            return {
                "success": True,
                "verified": True,
                "key": k,
                "via": delivery.get("via"),
                "delivery": delivery,
                "capture_after": cap,
                "frame_id": frame_id,
                "suggestion": "搜索已打开，请 windows_type_text 输入关键词。",
            }

        _wait_stable_quiet(int(_pace_val("post_key_stable_ms")))
        cap = capture_after_action(hwnd, before_hash=before, require_change=need_change)
        if need_change and not cap.get("ok") and not cap.get("changed"):
            return {
                "success": False,
                "verified": False,
                "error": cap.get("error") or "按键后画面无变化",
                "key": k,
                "delivery": delivery,
                "capture_after": cap,
                "frame_id": frame_id,
            }
        # Enter 打开搜索结果会话后：解除搜索武装，后续 type 进消息栏而非再点搜索
        if "enter" in parts_l and (
            bool(get_desktop_target().get("search_xy")) or get_input_phase() == "search"
        ):
            mark_compose_input_phase()
        return {
            "success": True,
            "verified": bool(cap.get("changed") or not need_change),
            "key": k,
            "via": delivery.get("via"),
            "delivery": delivery,
            "capture_after": cap,
            "frame_id": frame_id,
            "input_phase": get_input_phase(),
        }

    return _run_with_timeout(_work, timeout=12.0)


def windows_wait(
    duration_ms: Optional[int] = None,
    condition: Optional[str] = None,
) -> Dict[str, Any]:
    cond = (condition or "").strip().lower()
    if cond in ("stable", "界面稳定", "screen_stable"):
        from screen_tools import wait_screen_stable

        ms = int(duration_ms) if duration_ms else 3000
        return wait_screen_stable(timeout_ms=max(200, ms))
    if cond in ("desktop_change", "window_change", "桌面变化", "窗口变化"):
        from desktop_input import wait_for_desktop_change

        ms = int(duration_ms) if duration_ms else 8000
        return wait_for_desktop_change(timeout_ms=max(300, ms))
    if cond.startswith("window:") or cond.startswith("title:"):
        from desktop_input import wait_for_window_title_keyword

        key = cond.split(":", 1)[-1].strip()
        ms = int(duration_ms) if duration_ms else 8000
        ok = wait_for_window_title_keyword(key, timeout=max(0.3, ms / 1000.0))
        return {
            "success": bool(ok),
            "ok": bool(ok),
            "condition": condition,
            "title_filter": key,
            "error": "" if ok else f"未等到标题含「{key}」的窗口",
        }
    if duration_ms is not None:
        ms = max(0, int(duration_ms))
        time.sleep(ms / 1000.0)
        return {"success": True, "waited_ms": ms}
    if cond:
        # 未知条件：当作短等待
        time.sleep(0.5)
        return {
            "success": False,
            "error": f"未知 condition: {condition}",
            "suggestion": "使用 condition='stable' / 'desktop_change' / 'window:标题' 或传入 duration_ms。",
        }
    return {
        "success": False,
        "error": "请提供 duration_ms 或 condition",
        "suggestion": "例如 duration_ms=800 或 condition='stable' / 'desktop_change'。",
    }


# ---- schema helpers for FC / MCP ----

# 核心通用原语（不含应用死模板）
WINDOWS_TOOL_NAMES = (
    "windows_focus_app",
    "windows_launch_app",
    "windows_click_element",
    "windows_type_text",
    "windows_press_key",
    "windows_wait",
)

# 兼容旧名：仍可 dispatch，但不进默认 FC schema
WINDOWS_COMPAT_TOOL_NAMES = ("wechat_send_message",)

SCREEN_TOOL_NAMES = (
    "get_screen_text",
    "get_screen_description",
)

# 对话框默认不刷屏的内部工具（仍可在右侧 think/报告中体现关键结果）
CHAT_QUIET_TOOL_NAMES = frozenset(
    {
        "windows_wait",
        "get_screen_text",
        "get_screen_description",
    }
)


def dispatch_windows_or_screen_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """统一分发，供 chat tool loop / MCP 调用。"""
    n = (name or "").strip()
    a = args or {}
    if n == "wechat_send_message":
        return wechat_send_message(
            a.get("contact") or a.get("to") or "",
            a.get("text") if a.get("text") is not None else (a.get("message") or ""),
        )
    if n == "windows_focus_app":
        return windows_focus_app(a.get("app_name") or a.get("name") or "")
    if n == "windows_launch_app":
        return windows_launch_app(a.get("app_name") or a.get("name") or a.get("path") or "")
    if n == "windows_click_element":
        return windows_click_element(a.get("description") or a.get("locate") or "")
    if n == "windows_type_text":
        return windows_type_text(
            a.get("text") if a.get("text") is not None else "",
            clear=bool(a.get("clear") or False),
            expect_text=a.get("expect_text"),
            require_change=bool(a.get("require_change", True)),
            field=a.get("field") or a.get("target_field"),
        )
    if n == "windows_press_key":
        rc = a.get("require_change", None)
        if rc is None or rc == "":
            req = None
        else:
            req = bool(rc) if not isinstance(rc, str) else rc.strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
        return windows_press_key(a.get("key") or "", require_change=req)
    if n == "windows_wait":
        return windows_wait(
            duration_ms=a.get("duration_ms"),
            condition=a.get("condition"),
        )
    if n == "get_screen_text":
        from screen_tools import get_screen_text

        return get_screen_text(a.get("region"))
    if n == "get_screen_description":
        from screen_tools import get_screen_description

        return get_screen_description(a.get("hint") or "")
    return {"success": False, "error": f"未知工具 {n}"}
