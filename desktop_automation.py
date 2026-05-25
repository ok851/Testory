# -*- coding: utf-8 -*-
"""
Windows 桌面 UI 自动化引擎（pywinauto）。
与 Playwright 并行：通过 step_executor 按 automation_layer 分发。
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from desktop_input import (
    client_to_screen_xy,
    focus_hwnd,
    infer_effect_keyword,
    is_valid_hwnd,
    message_click_at_client,
    resolve_hwnd_from_spec,
    physical_mouse_enabled,
    pointer_action_at_screen,
    screen_click,
    screen_coords_in_virtual_bounds,
    screen_double_click,
    screen_right_click,
    screen_point_on_desktop_shell,
    screen_to_client_xy,
    should_verify_desktop_effect,
    steal_focus_enabled,
    visible_window_effect_for_spec,
    virtual_screen_rect,
    wait_for_desktop_effect,
)
from desktop_locator import (
    attach_application,
    attach_desktop_shell,
    desktop_icon_hit_at_screen_point,
    desktop_listitem_at_screen_point,
    desktop_runtime_available,
    is_desktop_shell_spec,
    parse_desktop_spec,
    resolve_control,
    resolve_desktop_icon_at_point,
    shell_open_folder,
    _split_coordinate,
)
from desktop_env_config import (
    desktop_execution_mode,
    desktop_operation_timeout,
    prepare_desktop_step,
    remote_desktop_enabled,
    validate_launch_app_ready,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_DESKTOP_ACTIONS = frozenset({
    "launch_app",
    "attach_window",
    "click",
    "double_click",
    "right_click",
    "input",
    "hotkey",
    "wait",
    "screenshot",
    "assert",
    "verify",
})

_SPURIOUS_SELECTOR_NAMES = frozenset({
    "folderview",
    "桌面",
    "desktop",
    "syslistview32",
    "shelldll_defview",
})

_WEB_ONLY_ACTIONS = frozenset({
    "navigate",
    "enter_iframe",
    "exit_iframe",
    "scroll",
    "swipe",
    "batch_input",
    "extract_text",
    "text_compare",
    "extract_json",
    "select",
    "date",
    "submit",
    "keypress",
    "wait_for_selector",
    "wait_for_element_visible",
    "api_request",
})


def normalize_automation_layer(step: Dict[str, Any]) -> str:
    layer = (step.get("automation_layer") or "").strip().lower()
    if layer in ("web", "desktop"):
        return layer
    action = (step.get("action") or "").strip()
    if action in _DESKTOP_ACTIONS or action in ("fill",):
        return "desktop"
    raw_spec = step.get("desktop_spec")
    if raw_spec:
        spec = parse_desktop_spec(raw_spec)
        if spec and (
            spec.get("hwnd")
            or spec.get("surface") == "desktop_shell"
            or spec.get("process")
            or spec.get("window_title")
            or spec.get("pick_center")
        ):
            return "desktop"
    return "web"


def _parse_locator_candidate_attempts(raw: Any) -> List[Tuple[str, str]]:
    """步骤 locator_candidates 备选定位（录制时生成，按 score 降序）。"""
    if not raw:
        return []
    data = raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    rows: List[Tuple[int, str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        st = (item.get("selector_type") or "").strip().lower()
        sv = (item.get("selector_value") or "").strip()
        if not st or not sv:
            continue
        if st == "name" and sv.strip().lower() in _SPURIOUS_SELECTOR_NAMES:
            continue
        try:
            score = int(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        rows.append((score, st, sv))
    rows.sort(key=lambda r: r[0], reverse=True)
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for _score, st, sv in rows:
        key = (st, sv)
        if key in seen:
            continue
        seen.add(key)
        out.append((st, sv))
    return out


def _is_screen_position_selector(selector_type: str) -> bool:
    """屏幕/窗口客户区坐标：按像素执行，coordinate 不 attach；client_coord/relative_coord 需 desktop_spec.hwnd。"""
    return (selector_type or "").strip().lower() in (
        "coordinate",
        "client_coord",
        "relative_coord",
    )


def _is_client_coord_selector(selector_type: str) -> bool:
    return (selector_type or "").strip().lower() == "client_coord"


def _is_relative_coord_selector(selector_type: str) -> bool:
    return (selector_type or "").strip().lower() == "relative_coord"


def _desktop_shell_physical_enabled() -> bool:
    """桌面图标物理鼠标回退（默认关闭，避免遮挡时点到顶层窗）。"""
    raw = (os.environ.get("DESKTOP_SHELL_PHYSICAL") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _icon_name_from_step(
    spec: Dict[str, Any],
    description: str = "",
    case_name: str = "",
) -> str:
    import re

    cn = (case_name or (spec or {}).get("_case_name") or "").strip()
    if cn:
        if "记事本" in cn or "notepad" in cn.lower():
            return "记事本"
        if "回收站" in cn:
            return "回收站"
        if "此电脑" in cn or "我的电脑" in cn:
            return "此电脑"
    m = re.search(r"「([^」]+)」", description or "")
    if m:
        name = (m.group(1) or "").strip()
        if name and name.lower() not in ("folderview", "桌面", "desktop"):
            return name
    tn = (spec.get("target_name") or "").strip()
    if tn and tn.lower() not in (
        "folderview",
        "桌面",
        "desktop",
        "桌面 1",
        "syslistview32",
    ):
        return tn
    return infer_effect_keyword(spec, description)


def _is_misbound_desktop_icon_capture(spec: Dict[str, Any]) -> bool:
    """捕获误把桌面图标绑到设置/UWP 等非 Shell 顶层窗口。"""
    try:
        from desktop_precise_locator import is_misbound_overlay_spec

        return is_misbound_overlay_spec(spec)
    except ImportError:
        pass
    s = spec or {}
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


def _desktop_step_retry_count() -> int:
    try:
        return max(0, min(5, int(os.environ.get("DESKTOP_STEP_RETRY", "1") or "1")))
    except (TypeError, ValueError):
        return 1


def _coordinate_prefer_physical(spec: Optional[Dict[str, Any]]) -> bool:
    """默认后台消息点击（不移动用户鼠标）；仅显式开启时物理点击。"""
    if physical_mouse_enabled():
        return True
    raw = (os.environ.get("DESKTOP_COORDINATE_PHYSICAL") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def validate_step_for_layer(action: str, layer: str) -> Optional[str]:
    act = (action or "").strip()
    if not act:
        return "步骤 action 不能为空"
    if layer == "desktop":
        if act in _WEB_ONLY_ACTIONS:
            return f"桌面步骤不允许 Web 专用动作：{act}"
        if act not in _DESKTOP_ACTIONS and act not in ("fill",):
            return f"不支持的桌面动作：{act}"
    elif layer == "web" and act in ("launch_app", "attach_window"):
        return f"Web 步骤不允许桌面专用动作：{act}，请将自动化层切换为「桌面」"
    return None


class DesktopAutomation:
    """单会话桌面自动化状态。"""

    def __init__(self):
        self._app: Any = None
        self._window: Any = None
        self._attach_fp: Optional[Tuple[Any, ...]] = None
        self._last_pointer_delivery: str = ""
        self._last_client_hwnd: int = 0
        self._last_client_xy: Tuple[int, int] = (0, 0)
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static", "desktop_screenshots"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)

    def reset_session(self) -> None:
        """释放当前附着（停止执行或浏览器强制重置时调用）。"""
        self._app = None
        self._window = None
        self._attach_fp = None

    @property
    def has_window(self) -> bool:
        return self._window is not None

    @staticmethod
    def _attach_fingerprint(spec: Dict[str, Any]) -> Tuple[Any, ...]:
        s = spec or {}
        return (
            int(s.get("hwnd") or 0),
            int(s.get("pid") or 0),
            (s.get("process") or "").strip().lower(),
            (s.get("window_title_re") or s.get("window_title") or "").strip(),
            (s.get("surface") or "").strip().lower(),
        )

    def _activate_attached_window(self, spec: Dict[str, Any]) -> None:
        """默认不抢焦点；仅 DESKTOP_STEAL_FOCUS=1 时置前目标窗口。"""
        if not steal_focus_enabled():
            return
        hwnd = int((spec or {}).get("hwnd") or 0)
        if not hwnd and self._window is not None:
            try:
                hwnd = int(getattr(self._window, "handle", 0) or 0)
            except Exception:
                hwnd = 0
        if hwnd:
            focus_hwnd(hwnd)
            time.sleep(0.12)
            return
        if self._window is not None:
            try:
                self._window.set_focus()
                time.sleep(0.08)
            except Exception:
                pass

    @staticmethod
    def _control_root_hwnd(ctrl: Any) -> int:
        import ctypes

        raw = int(
            getattr(getattr(ctrl, "element_info", None), "handle", 0)
            or getattr(ctrl, "handle", 0)
            or 0
        )
        if not raw:
            return 0
        return int(ctypes.windll.user32.GetAncestor(raw, 2) or raw)

    def _assert_control_on_target(self, ctrl: Any, spec: Dict[str, Any]) -> None:
        """确保解析到的控件属于步骤指定的目标窗口，避免点到当前屏幕其它区域。"""
        if is_desktop_shell_spec(spec):
            return
        spec_hwnd = int(spec.get("hwnd") or 0)
        if not spec_hwnd:
            return
        root = self._control_root_hwnd(ctrl)
        if root and root != spec_hwnd:
            raise RuntimeError(
                f"控件不在目标窗口内（期望 hwnd={spec_hwnd}，实际顶层 hwnd={root}）。"
                "请确认目标应用未关闭，且步骤 desktop_spec 与捕获时一致。"
            )

    @staticmethod
    def _assert_control_actionable(ctrl: Any) -> None:
        if type(ctrl).__name__ in ("_CoordTarget", "_ClientCoordTarget"):
            return
        try:
            rect = ctrl.rectangle()
        except Exception as exc:
            raise RuntimeError(f"无法读取控件位置: {exc}") from exc
        w = int(rect.right) - int(rect.left)
        h = int(rect.bottom) - int(rect.top)
        if w < 2 or h < 2:
            raise RuntimeError("目标控件尺寸无效或不可见")
        if hasattr(ctrl, "is_enabled") and not ctrl.is_enabled():
            raise RuntimeError("目标控件当前不可用（disabled）")

    def _ensure_attached(self, spec: Dict[str, Any], action: str, selector_type: str) -> None:
        """步骤含 desktop_spec 时按需附着/切换窗口（同进程内 hwnd 变化会重新附着）。"""
        st = (selector_type or "").strip().lower()
        if action in ("click", "double_click", "right_click") and (
            (selector_type or "").strip().lower() == "coordinate"
        ):
            return
        if action in ("click", "double_click", "right_click") and (
            _is_client_coord_selector(st) or _is_relative_coord_selector(st)
        ):
            if not int(spec.get("hwnd") or 0):
                raise RuntimeError(
                    f"{st} 步骤需在 desktop_spec 中保存 hwnd（请重新元素捕获）"
                )
            return
        if not spec:
            if self._window is not None:
                return
            raise RuntimeError(
                "未附着桌面窗口：请在步骤中保存 desktop_spec（hwnd/窗口标题），"
                "或使用 launch_app / attach_window"
            )
        if not (
            spec.get("hwnd")
            or spec.get("pid")
            or spec.get("process")
            or spec.get("path")
            or spec.get("exe")
            or spec.get("window_title")
            or spec.get("window_title_re")
            or spec.get("cmd_line")
        ):
            raise RuntimeError(
                "未附着桌面窗口：desktop_spec 缺少 hwnd/process/窗口标题等字段，"
                "请重新捕获元素或填写 launch_app"
            )
        fp = self._attach_fingerprint(spec)
        if self._window is not None and self._attach_fp == fp:
            self._activate_attached_window(spec)
            return
        if is_desktop_shell_spec(spec):
            self._app, self._window = attach_desktop_shell(spec)
        else:
            self._app, self._window = attach_application(spec)
        self._attach_fp = fp
        self._activate_attached_window(spec)

    def _uia_path_json(self, spec: Dict[str, Any]) -> str:
        uia_nodes = spec.get("uia_path")
        if isinstance(uia_nodes, list) and uia_nodes:
            return json.dumps(uia_nodes, ensure_ascii=False)
        if isinstance(uia_nodes, str) and uia_nodes.strip():
            return uia_nodes.strip()
        return ""

    def _build_resolve_attempts(
        self,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
        *,
        locator_candidates: Any = None,
    ) -> List[Tuple[str, str]]:
        """桌面图标层优先 UIA/图标命中，坐标仅作最后回退。"""
        st = (selector_type or "").strip().lower()
        sv = (selector_value or "").strip()
        center = (spec.get("pick_center") or "").strip()
        uia_json = self._uia_path_json(spec)
        attempts: List[Tuple[str, str]] = []
        seen: set = set()

        def _add(try_st: str, try_sv: str) -> None:
            key = (try_st, try_sv)
            if key in seen or (not try_sv and try_st != "coordinate"):
                return
            seen.add(key)
            attempts.append(key)

        if _is_client_coord_selector(st) and sv:
            _add("client_coord", sv)
            return attempts
        if _is_relative_coord_selector(st) and sv:
            _add("relative_coord", sv)
            return attempts
        if (selector_type or "").strip().lower() == "coordinate" and sv:
            if uia_json:
                _add("uia_path", uia_json)
            else:
                try:
                    from desktop_precise_locator import (
                        uia_path_from_locator_candidates,
                    )

                    cand_uia = uia_path_from_locator_candidates(locator_candidates)
                    if cand_uia:
                        _add("uia_path", cand_uia)
                except ImportError:
                    pass
            try:
                from desktop_precise_locator import visual_template_from_candidates

                vt = visual_template_from_candidates(locator_candidates)
                if vt:
                    _add("visual_template", vt)
            except ImportError:
                pass
            _add("coordinate", sv)
            return attempts

        for try_st, try_sv in _parse_locator_candidate_attempts(locator_candidates):
            if try_st == "coordinate":
                continue
            _add(try_st, try_sv)

        if is_desktop_shell_spec(spec):
            if uia_json:
                _add("uia_path", uia_json)
            if center:
                _add("__desktop_hit__", center)
            if st == "name" and sv and sv.strip().lower() not in _SPURIOUS_SELECTOR_NAMES:
                _add("name", sv)
            elif st not in ("uia_path", "__desktop_hit__", "coordinate") and sv:
                _add(st, sv)
            if st == "coordinate" and sv:
                _add("coordinate", sv)
            elif center:
                _add("coordinate", center)
            return attempts

        if st == "coordinate" and sv:
            if center and center != sv:
                _add("__desktop_hit__", center)
            if uia_json:
                _add("uia_path", uia_json)
            _add(st, sv)
        elif st and sv:
            _add(st, sv)
        if uia_json:
            _add("uia_path", uia_json)
        if center and ("coordinate", center) not in seen:
            _add("__desktop_hit__", center)
        if center and ("coordinate", center) not in seen:
            _add("coordinate", center)
        return attempts

    @staticmethod
    def _control_screen_center(ctrl: Any) -> Tuple[int, int]:
        rect = ctrl.rectangle()
        return (
            int((rect.left + rect.right) / 2),
            int((rect.top + rect.bottom) / 2),
        )

    @staticmethod
    def _assert_screen_coords(x: int, y: int) -> None:
        if sys.platform != "win32":
            return
        if not screen_coords_in_virtual_bounds(x, y):
            left, top, w, h = virtual_screen_rect()
            raise RuntimeError(
                f"点击坐标 ({x},{y}) 超出虚拟桌面范围 "
                f"[{left},{top}]~[{left + w},{top + h}]，请重新捕获元素"
            )

    @staticmethod
    def _verify_resolved_name(ctrl: Any, expected: str, selector_type: str) -> None:
        st = (selector_type or "").strip().lower()
        exp = (expected or "").strip()
        if st != "name" or not exp:
            return
        try:
            actual = (getattr(ctrl.element_info, "name", None) or "").strip()
        except Exception:
            actual = ""
        if not actual:
            raise RuntimeError(f"无法确认控件名称是否为「{exp}」")
        if exp not in actual and actual not in exp:
            raise RuntimeError(
                f"解析到的控件为「{actual}」，与期望「{exp}」不一致，将尝试其他定位方式"
            )

    def _screen_pointer_action(
        self,
        action: str,
        x: int,
        y: int,
        spec: Optional[Dict[str, Any]] = None,
        *,
        position_only: bool = False,
    ) -> None:
        """
        对屏幕像素 (x,y) 执行指针动作。
        position_only 仅表示「不 attach 窗口」，不表示移动真实鼠标。
        有 desktop_spec.hwnd 时优先向该窗口客户区发消息，避免 WindowFromPoint 点到前台其它窗口。
        """
        del position_only  # 保留参数兼容；不再用于决定是否物理点击
        step_spec = spec or {}
        self._assert_screen_coords(x, y)
        px, py = int(x), int(y)
        use_physical = _coordinate_prefer_physical(step_spec)
        self._last_pointer_physical = bool(use_physical)
        act_l = (action or "click").strip().lower()
        anchor = resolve_hwnd_from_spec(step_spec)

        if use_physical:
            uat_logger.info(
                "桌面指针: %s @ (%s, %s) [physical=1，将移动鼠标]",
                act_l,
                px,
                py,
            )
            pointer_action_at_screen(px, py, act_l, force_physical=True)
            return

        if anchor and is_valid_hwnd(anchor):
            try:
                cx, cy = screen_to_client_xy(anchor, px, py)
                dbl = act_l == "double_click"
                right = act_l == "right_click"
                message_click_at_client(
                    anchor, cx, cy, double=dbl, right=right
                )
                self._last_pointer_delivery = "client"
                self._last_client_hwnd = anchor
                self._last_client_xy = (cx, cy)
                uat_logger.info(
                    "桌面指针: %s 客户区 (%s,%s) hwnd=%s [message→目标窗口]",
                    act_l,
                    cx,
                    cy,
                    anchor,
                )
                return
            except Exception as exc:
                uat_logger.warning(
                    "目标窗口 hwnd=%s 客户区消息点击失败，回退屏幕坐标: %s",
                    anchor,
                    exc,
                )

        uat_logger.info(
            "桌面指针: %s @ (%s, %s) [screen message，无有效 hwnd]",
            act_l,
            px,
            py,
        )
        pointer_action_at_screen(px, py, act_l, force_physical=False)

    def _verify_pointer_effect(
        self,
        action: str,
        spec: Dict[str, Any],
        description: str = "",
        *,
        fg_before: int = 0,
        selector_type: str = "",
        click_x: int = 0,
        click_y: int = 0,
    ) -> None:
        """双击等步骤校验是否产生预期窗口变化。"""
        if action != "double_click":
            return
        from desktop_input import get_foreground_hwnd

        st = (selector_type or "").strip().lower()
        keyword = infer_effect_keyword(spec, description)
        icon_name = _icon_name_from_step(
            spec, description, (spec or {}).get("_case_name") or ""
        )
        if _is_screen_position_selector(st) and click_x and click_y:
            if screen_point_on_desktop_shell(click_x, click_y) or desktop_icon_hit_at_screen_point(
                click_x, click_y, {"surface": "desktop_shell"}
            ):
                shell_spec = {**spec, "surface": "desktop_shell"}
                if not wait_for_desktop_effect(
                    keyword or icon_name,
                    fg_before=fg_before,
                    timeout=8.0,
                    desktop_shell=True,
                    require_verify=should_verify_desktop_effect(shell_spec),
                ):
                    icon = icon_name or "目标"
                    raise RuntimeError(
                        f"已双击桌面图标区域，但 {8.0:.0f}s 内未检测到「{icon}」相关窗口。"
                        "请确认图标可手动双击打开，并关闭遮挡桌面的全屏窗口后重新捕获。"
                    )
                return
            if not keyword and not icon_name:
                raise RuntimeError(
                    f"坐标 ({click_x},{click_y}) 双击未检测到桌面图标或应用打开。"
                    "坐标可能落在其它窗口（如「设置」）上，请关闭遮挡窗口后在桌面图标中心重新捕获。"
                )
            if keyword or icon_name:
                if not wait_for_desktop_effect(
                    keyword or icon_name,
                    fg_before=fg_before,
                    timeout=8.0,
                    desktop_shell=False,
                    require_verify=True,
                ):
                    raise RuntimeError(
                        f"已执行双击，但 {8.0:.0f}s 内未检测到与「{keyword or icon_name}」相关的窗口。"
                    )
                return
        if visible_window_effect_for_spec(spec, keyword, fg_before=fg_before):
            return
        shell = is_desktop_shell_spec(spec)
        require = should_verify_desktop_effect(spec)
        timeout = 8.0 if shell else 5.0
        if shell:
            if not wait_for_desktop_effect(
                keyword,
                fg_before=fg_before,
                timeout=timeout,
                desktop_shell=True,
                require_verify=True,
            ):
                hint = "资源管理器窗口（如「回收站」文件夹）"
                raise RuntimeError(
                    f"已执行双击，但 {timeout:.0f}s 内未检测到与「{keyword or '目标'}」相关的{hint}。"
                    "请重新捕获该桌面图标，并确认手动双击可正常打开。"
                )
            return
        if keyword and require:
            if not wait_for_desktop_effect(
                keyword,
                fg_before=fg_before,
                timeout=timeout,
                desktop_shell=False,
                require_verify=True,
            ):
                raise RuntimeError(
                    f"已执行双击，但 {timeout:.0f}s 内未检测到与「{keyword}」相关的窗口变化。"
                    "请确认目标应用已打开且元素可双击。"
                )
            return
        if not steal_focus_enabled():
            if visible_window_effect_for_spec(spec, keyword, fg_before=fg_before):
                return
        fg_after = get_foreground_hwnd()
        if fg_after and fg_after != fg_before:
            return
        raise RuntimeError(
            "已执行双击，但未检测到目标窗口变化（已尝试可见窗口标题匹配）。"
            "请重新捕获元素，并确认 desktop_spec 指向目标应用窗口（勿使用 explorer/WorkerW）。"
        )

    def _verify_pointer_step(
        self,
        action: str,
        spec: Dict[str, Any],
        description: str,
        *,
        ctrl: Any,
        fg_before: int,
        selector_type: str = "",
        click_x: int = 0,
        click_y: int = 0,
    ) -> None:
        """校验元素可执行：UIA 控件已解析；纯坐标回退则校验消息送达。"""
        self._assert_control_actionable(ctrl)
        act = (action or "click").strip().lower()
        if type(ctrl).__name__ in ("_CoordTarget", "_ClientCoordTarget"):
            from desktop_input import (
                hwnd_at_screen_point,
                resolve_hwnd_from_spec,
                screen_to_client_xy,
                verify_pointer_delivered,
            )

            used_phys = bool(getattr(self, "_last_pointer_physical", False))
            delivery = (getattr(self, "_last_pointer_delivery", "") or "").strip().lower()
            target_hwnd = resolve_hwnd_from_spec(spec) or int(
                getattr(ctrl, "_anchor_hwnd", 0) or 0
            )
            client_x: Optional[int] = None
            client_y: Optional[int] = None
            if type(ctrl).__name__ == "_ClientCoordTarget":
                client_x = int(getattr(ctrl, "_cx", 0))
                client_y = int(getattr(ctrl, "_cy", 0))
            elif target_hwnd:
                try:
                    client_x, client_y = screen_to_client_xy(
                        target_hwnd, click_x, click_y
                    )
                except Exception:
                    client_x, client_y = None, None
            if delivery == "client" and target_hwnd and client_x is not None:
                if not verify_pointer_delivered(
                    click_x,
                    click_y,
                    target_hwnd=target_hwnd,
                    client_x=client_x,
                    client_y=client_y,
                    delivery_mode="client",
                    spec=spec,
                ):
                    raise RuntimeError(
                        f"目标窗口 hwnd={target_hwnd} 客户区 ({client_x},{client_y}) 无效，"
                        "请重新捕获元素或确认窗口未关闭。"
                    )
                if _is_misbound_desktop_icon_capture(spec) and act == "double_click":
                    raise RuntimeError(
                        f"坐标 ({click_x},{click_y}) 误绑定到「{spec.get('window_title') or '其它应用'}」，"
                        "无法通过后台消息打开桌面图标。将改走桌面 Shell 路径；"
                        "若仍失败请关闭遮挡窗口后重新捕获。"
                    )
            if used_phys and not hwnd_at_screen_point(click_x, click_y):
                raise RuntimeError(
                    f"坐标 ({click_x},{click_y}) 下无有效窗口，无法执行点击。"
                    "请重新捕获坐标并确认该位置未被完全遮挡。"
                )
            if not verify_pointer_delivered(
                click_x,
                click_y,
                desktop_shell=is_desktop_shell_spec(spec),
                target_hwnd=target_hwnd,
                used_physical_click=used_phys,
                client_x=client_x,
                client_y=client_y,
                delivery_mode=delivery,
                spec=spec,
            ):
                raise RuntimeError(
                    f"坐标 ({click_x},{click_y}) 点击未送达目标窗口"
                    f"{(' hwnd=' + str(target_hwnd)) if target_hwnd else ''}。"
                    "（其它窗口可能遮挡该屏幕位置；后台模式已向目标窗口发消息，"
                    "若应用无响应可改用 uia_path 或 DESKTOP_COORDINATE_PHYSICAL=1 调试。）"
                )
            if act == "double_click":
                self._verify_pointer_effect(
                    action,
                    spec,
                    description,
                    fg_before=fg_before,
                    selector_type=selector_type,
                    click_x=click_x,
                    click_y=click_y,
                )
            return
        try:
            ctrl.rectangle()
        except Exception as exc:
            raise RuntimeError(f"无法确认目标控件仍有效: {exc}") from exc
        if act == "double_click":
            self._verify_pointer_effect(
                action,
                spec,
                description,
                fg_before=fg_before,
                selector_type=selector_type,
                click_x=click_x,
                click_y=click_y,
            )
            return
        if act in ("click", "right_click"):
            if is_desktop_shell_spec(spec):
                delivery = (getattr(self, "_last_pointer_delivery", "") or "").strip().lower()
                st_low = (selector_type or "").strip().lower()
                if delivery in ("uia", "shell_open") or st_low == "uia_path":
                    return
                from desktop_input import verify_pointer_delivered

                if not verify_pointer_delivered(
                    click_x,
                    click_y,
                    desktop_shell=True,
                ):
                    raise RuntimeError(
                        f"桌面 Shell 单击未命中桌面层 ({click_x},{click_y})，请重新捕获"
                    )
                return
            spec_hwnd = int(spec.get("hwnd") or 0)
            root = self._control_root_hwnd(ctrl)
            if spec_hwnd and root and root != spec_hwnd:
                raise RuntimeError(
                    f"执行后控件窗口校验失败（期望 hwnd={spec_hwnd}，实际 {root}）"
                )
            if steal_focus_enabled() and spec_hwnd:
                from desktop_input import get_foreground_hwnd

                fg = get_foreground_hwnd()
                if fg and fg != spec_hwnd and fg == fg_before:
                    uat_logger.warning(
                        "点击后前台未切换到目标窗口（未开启抢焦点时属正常）"
                    )

    def _resolve_step_control(
        self,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
        *,
        locator_candidates: Any = None,
    ) -> Any:
        """解析控件；按场景排序多种定位策略。"""
        last_err: Optional[Exception] = None
        for try_st, try_sv in self._build_resolve_attempts(
            selector_type,
            selector_value,
            spec,
            locator_candidates=locator_candidates,
        ):
            if not try_sv and try_st not in ("coordinate", "client_coord", "relative_coord"):
                continue
            try:
                if try_st == "visual_template":
                    vx, vy = self._match_visual_template_screen(try_sv)
                    self._assert_screen_coords(vx, vy)
                    if self._should_route_desktop_shell_pointer(
                        vx, vy, spec, action=""
                    ):
                        return resolve_desktop_icon_at_point(
                            vx, vy, self._window, spec, self._app
                        )
                    class _VisualCoordTarget:
                        def __init__(
                            self,
                            px: int,
                            py: int,
                            owner: "DesktopAutomation",
                            step_spec: Dict[str, Any],
                        ):
                            self._x, self._y = px, py
                            self._owner = owner
                            self._spec = step_spec

                        def rectangle(self):
                            from types import SimpleNamespace

                            return SimpleNamespace(
                                left=self._x,
                                top=self._y,
                                right=self._x + 1,
                                bottom=self._y + 1,
                            )

                        def _do(self, act: str) -> None:
                            self._owner._last_resolved_via = "visual_template"
                            self._owner._screen_pointer_action(
                                act, self._x, self._y, spec=self._spec
                            )

                        def click_input(self, **kwargs):
                            self._do("click")

                        def double_click_input(self, **kwargs):
                            self._do("double_click")

                        def right_click_input(self, **kwargs):
                            self._do("right_click")

                        def click(self, **kwargs):
                            self._do("click")

                        def double_click(self, **kwargs):
                            self._do("double_click")

                        def right_click(self, **kwargs):
                            self._do("right_click")

                    self._last_resolved_via = "visual_template"
                    return _VisualCoordTarget(vx, vy, self, spec)
                if try_st == "__desktop_hit__":
                    x, y = _split_coordinate(try_sv)
                    self._assert_screen_coords(x, y)
                    ctrl = resolve_desktop_icon_at_point(
                        x, y, self._window, spec, self._app
                    )
                    self._last_resolved_via = "desktop_hit"
                    self._verify_resolved_name(
                        ctrl, (spec.get("target_name") or ""), "name"
                    )
                    return ctrl
                if try_st == "client_coord":
                    anchor = resolve_hwnd_from_spec(spec)
                    if not anchor:
                        raise RuntimeError(
                            "client_coord 需要有效 desktop_spec（hwnd 或窗口标题），请重新捕获元素"
                        )
                    cx, cy = _split_coordinate(try_sv)
                    sx, sy = client_to_screen_xy(anchor, cx, cy)
                    self._assert_screen_coords(sx, sy)

                    class _ClientCoordTarget:
                        def __init__(
                            self,
                            hwnd: int,
                            client_x: int,
                            client_y: int,
                            owner: "DesktopAutomation",
                            step_spec: Dict[str, Any],
                        ):
                            self._anchor_hwnd = int(hwnd)
                            self._cx, self._cy = int(client_x), int(client_y)
                            self._owner = owner
                            self._spec = step_spec

                        def rectangle(self):
                            from types import SimpleNamespace

                            px, py = client_to_screen_xy(
                                self._anchor_hwnd, self._cx, self._cy
                            )
                            return SimpleNamespace(
                                left=px,
                                top=py,
                                right=px + 1,
                                bottom=py + 1,
                            )

                        def _do(self, act: str) -> None:
                            self._owner._last_resolved_via = "client_coord"
                            anchor = resolve_hwnd_from_spec(self._spec)
                            if not anchor:
                                raise RuntimeError(
                                    "client_coord 目标窗口不存在，请重新捕获或先 launch_app"
                                )
                            use_physical = (
                                physical_mouse_enabled()
                                or _coordinate_prefer_physical(self._spec)
                            )
                            self._owner._last_pointer_physical = use_physical
                            act_l = (act or "click").strip().lower()
                            if use_physical:
                                sx, sy = client_to_screen_xy(
                                    anchor, self._cx, self._cy
                                )
                                self._owner._screen_pointer_action(
                                    act_l,
                                    sx,
                                    sy,
                                    spec=self._spec,
                                )
                                return
                            dbl = act_l == "double_click"
                            right = act_l == "right_click"
                            message_click_at_client(
                                anchor,
                                self._cx,
                                self._cy,
                                double=dbl,
                                right=right,
                            )
                            self._owner._last_pointer_delivery = "client"
                            self._owner._last_client_hwnd = anchor
                            self._owner._last_client_xy = (
                                self._cx,
                                self._cy,
                            )

                        def click_input(self, **kwargs):
                            self._do("click")

                        def double_click_input(self, **kwargs):
                            self._do("double_click")

                        def right_click_input(self, **kwargs):
                            self._do("right_click")

                        def click(self, **kwargs):
                            self._do("click")

                        def double_click(self, **kwargs):
                            self._do("double_click")

                        def right_click(self, **kwargs):
                            self._do("right_click")

                    self._last_resolved_via = "client_coord"
                    return _ClientCoordTarget(anchor, cx, cy, self, spec)
                if try_st == "relative_coord":
                    anchor = resolve_hwnd_from_spec(spec)
                    if not anchor:
                        raise RuntimeError(
                            "relative_coord 需要有效 desktop_spec（hwnd 或窗口标题），请重新捕获元素"
                        )
                    from desktop_precise_locator import relative_coord_to_client_xy

                    cx, cy = relative_coord_to_client_xy(spec, try_sv)
                    sx, sy = client_to_screen_xy(anchor, cx, cy)
                    self._assert_screen_coords(sx, sy)

                    class _RelativeCoordTarget:
                        def __init__(
                            self,
                            hwnd: int,
                            client_x: int,
                            client_y: int,
                            owner: "DesktopAutomation",
                            step_spec: Dict[str, Any],
                        ):
                            self._anchor_hwnd = int(hwnd)
                            self._cx, self._cy = int(client_x), int(client_y)
                            self._owner = owner
                            self._spec = step_spec

                        def rectangle(self):
                            from types import SimpleNamespace

                            px, py = client_to_screen_xy(
                                self._anchor_hwnd, self._cx, self._cy
                            )
                            return SimpleNamespace(
                                left=px,
                                top=py,
                                right=px + 1,
                                bottom=py + 1,
                            )

                        def _do(self, act: str) -> None:
                            self._owner._last_resolved_via = "relative_coord"
                            anchor = resolve_hwnd_from_spec(self._spec)
                            if not anchor:
                                raise RuntimeError(
                                    "relative_coord 目标窗口不存在，请重新捕获或先 launch_app"
                                )
                            use_physical = (
                                physical_mouse_enabled()
                                or _coordinate_prefer_physical(self._spec)
                            )
                            self._owner._last_pointer_physical = use_physical
                            act_l = (act or "click").strip().lower()
                            if use_physical:
                                sx, sy = client_to_screen_xy(
                                    anchor, self._cx, self._cy
                                )
                                self._owner._screen_pointer_action(
                                    act_l,
                                    sx,
                                    sy,
                                    spec=self._spec,
                                )
                                return
                            dbl = act_l == "double_click"
                            right = act_l == "right_click"
                            message_click_at_client(
                                anchor,
                                self._cx,
                                self._cy,
                                double=dbl,
                                right=right,
                            )
                            self._owner._last_pointer_delivery = "client"
                            self._owner._last_client_hwnd = anchor
                            self._owner._last_client_xy = (
                                self._cx,
                                self._cy,
                            )

                        def click_input(self, **kwargs):
                            self._do("click")

                        def double_click_input(self, **kwargs):
                            self._do("double_click")

                        def right_click_input(self, **kwargs):
                            self._do("right_click")

                        def click(self, **kwargs):
                            self._do("click")

                        def double_click(self, **kwargs):
                            self._do("double_click")

                        def right_click(self, **kwargs):
                            self._do("right_click")

                    self._last_resolved_via = "relative_coord"
                    return _RelativeCoordTarget(anchor, cx, cy, self, spec)
                if try_st == "coordinate":
                    x, y = _split_coordinate(try_sv)
                    self._assert_screen_coords(x, y)

                    class _CoordTarget:
                        def __init__(
                            self,
                            px: int,
                            py: int,
                            owner: "DesktopAutomation",
                            step_spec: Dict[str, Any],
                        ):
                            self._x, self._y = px, py
                            self._anchor_hwnd = int(step_spec.get("hwnd") or 0)
                            self._owner = owner
                            self._spec = step_spec

                        def rectangle(self):
                            from types import SimpleNamespace

                            return SimpleNamespace(
                                left=self._x,
                                top=self._y,
                                right=self._x + 1,
                                bottom=self._y + 1,
                            )

                        def _do(self, act: str) -> None:
                            self._owner._last_resolved_via = "coordinate"
                            self._owner._screen_pointer_action(
                                act,
                                self._x,
                                self._y,
                                spec=self._spec,
                            )

                        def click_input(self, **kwargs):
                            self._do("click")

                        def double_click_input(self, **kwargs):
                            self._do("double_click")

                        def right_click_input(self, **kwargs):
                            self._do("right_click")

                        def click(self, **kwargs):
                            self._do("click")

                        def double_click(self, **kwargs):
                            self._do("double_click")

                        def right_click(self, **kwargs):
                            self._do("right_click")

                    self._last_resolved_via = "coordinate"
                    return _CoordTarget(x, y, self, spec)

                ctrl = resolve_control(
                    self._window, try_st, try_sv, spec, app=self._app
                )
                self._last_resolved_via = try_st
                self._verify_resolved_name(ctrl, selector_value, try_st)
                if try_st != "coordinate":
                    self._assert_control_actionable(ctrl)
                return ctrl
            except Exception as exc:
                last_err = exc
        if last_err:
            raise last_err
        raise RuntimeError("无法解析桌面控件：缺少有效的定位信息")

    @staticmethod
    def _is_uia_listitem(ctrl: Any) -> bool:
        try:
            ct = str(getattr(ctrl.element_info, "control_type", "") or "").lower()
            return "listitem" in ct
        except Exception:
            return False

    def _desktop_icon_name(self, ctrl: Any, spec: Dict[str, Any]) -> str:
        try:
            n = (getattr(ctrl.element_info, "name", None) or "").strip()
            if n:
                return n
        except Exception:
            pass
        return infer_effect_keyword(spec, "")

    def _invoke_control_pointer(
        self, ctrl: Any, action: str, *, spec: Optional[Dict[str, Any]] = None
    ) -> None:
        """默认 UIA 消息点击（不移动用户鼠标）；物理鼠标仅 DESKTOP_PHYSICAL_MOUSE=1。"""
        spec = spec or {}
        act = (action or "click").strip().lower()
        if steal_focus_enabled():
            try:
                ctrl.set_focus()
            except Exception:
                pass
            time.sleep(0.05)

        if act == "double_click" and self._is_uia_listitem(ctrl) and is_desktop_shell_spec(spec):
            icon_name = self._desktop_icon_name(ctrl, spec)
            skip_shell = icon_name in ("FolderView", "桌面", "Desktop", "桌面 1", "")
            if icon_name and not skip_shell and shell_open_folder(icon_name):
                time.sleep(0.25)
                return
            for method_name in ("invoke", "double_click", "double_click_input"):
                if method_name.endswith("_input") and not physical_mouse_enabled():
                    continue
                try:
                    getattr(ctrl, method_name)()
                    time.sleep(0.15)
                    return
                except Exception:
                    continue
            cx, cy = self._control_screen_center(ctrl)
            dbl = act == "double_click"
            right = act == "right_click"
            from desktop_input import message_click_at_screen

            message_click_at_screen(
                cx, cy, double=dbl, right=right
            )
            return

        if physical_mouse_enabled():
            if act == "click":
                ctrl.click_input()
            elif act == "double_click":
                ctrl.double_click_input()
            else:
                ctrl.right_click_input()
            return

        if act == "click":
            ctrl.click()
        elif act == "double_click":
            ctrl.double_click()
        elif act == "right_click":
            ctrl.right_click()
        else:
            raise ValueError(f"不支持的指针动作：{act}")

    def _should_route_desktop_shell_pointer(
        self,
        x: int,
        y: int,
        spec: Dict[str, Any],
        *,
        action: str = "",
    ) -> bool:
        """是否应按桌面图标层执行（避免误绑到设置等覆盖窗口）。"""
        if is_desktop_shell_spec(spec) and self._uia_path_json(spec):
            return True
        if _is_misbound_desktop_icon_capture(spec):
            return True
        shell_hint = {"surface": "desktop_shell"}
        if desktop_icon_hit_at_screen_point(x, y, shell_hint):
            return True
        if is_desktop_shell_spec(spec) or screen_point_on_desktop_shell(x, y):
            return True
        return False

    @staticmethod
    def _capture_virtual_desktop_png() -> bytes:
        import mss  # type: ignore
        import mss.tools  # type: ignore

        left, top, w, h = virtual_screen_rect()
        with mss.mss() as sct:
            shot = sct.grab({"left": left, "top": top, "width": w, "height": h})
            return mss.tools.to_png(shot.rgb, shot.size)

    def _match_visual_template_screen(
        self, selector_value: str
    ) -> Tuple[int, int]:
        from locator_visual_fallback import match_template_in_viewport_png

        left, top, _w, _h = virtual_screen_rect()
        png = self._capture_virtual_desktop_png()
        hit = match_template_in_viewport_png(png, selector_value)
        if not hit:
            raise RuntimeError(
                "截图模板未在虚拟桌面匹配到目标，请重新捕获元素或降低遮挡后重试"
            )
        cx, cy, score = hit
        uat_logger.info(
            "visual_template 命中 (%s,%s) score=%.3f",
            left + cx,
            top + cy,
            score,
        )
        return left + int(cx), top + int(cy)

    def _perform_desktop_shell_precise_step(
        self,
        action: str,
        spec: Dict[str, Any],
        *,
        description: str = "",
        fg_before: int = 0,
        selector_type: str = "",
    ) -> Dict[str, Any]:
        """UIA 精准链执行桌面图标（不依赖 Z 序/遮挡）。"""
        shell_spec: Dict[str, Any] = dict(spec)
        shell_spec["surface"] = "desktop_shell"
        shell_spec.pop("hwnd", None)
        self._app, self._window = attach_desktop_shell(shell_spec)
        uia_json = self._uia_path_json(shell_spec)
        if not uia_json:
            raise RuntimeError("desktop_shell 步骤缺少 uia_path，请重新捕获")
        ctrl = resolve_control(
            self._window, "uia_path", uia_json, shell_spec, app=self._app
        )
        click_x, click_y = self._control_screen_center(ctrl)
        icon_name = self._desktop_icon_name(ctrl, shell_spec)
        act = (action or "click").strip().lower()
        open_name = icon_name or _icon_name_from_step(
            shell_spec, description, (shell_spec or {}).get("_case_name") or ""
        )
        resolved_via = "uia_path"

        if act == "double_click" and open_name and shell_open_folder(open_name):
            time.sleep(0.5)
            resolved_via = "shell_open_folder"
            self._last_pointer_delivery = "shell_open"
        else:
            invoked = False
            try:
                self._invoke_control_pointer(ctrl, act, spec=shell_spec)
                invoked = True
                self._last_pointer_delivery = "uia"
            except Exception as exc:
                uat_logger.warning("UIA 精准链指针失败: %s", exc)
            if _desktop_shell_physical_enabled() or not invoked:
                pointer_action_at_screen(
                    click_x, click_y, act, force_physical=True
                )
                self._last_pointer_physical = True
                self._last_pointer_delivery = "desktop_shell_physical"
                resolved_via = "uia_path+physical" if invoked else "uia_path_physical"

        merged = {**spec, **shell_spec}
        if open_name:
            merged["target_name"] = open_name
        self._verify_pointer_effect(
            act,
            merged,
            description,
            fg_before=fg_before,
            selector_type=selector_type or "uia_path",
            click_x=click_x,
            click_y=click_y,
        )
        return {
            "status": "success",
            "action": action,
            "resolved_via": resolved_via,
            "coords": f"{click_x},{click_y}",
            "verified": True,
            "pointer_executed": True,
        }

    def _perform_desktop_shell_pointer_step(
        self,
        action: str,
        x: int,
        y: int,
        spec: Dict[str, Any],
        *,
        description: str = "",
        fg_before: int = 0,
        selector_type: str = "",
    ) -> Dict[str, Any]:
        """桌面图标层指针：UIA/物理双击 + 校验是否打开目标（不依赖是否被遮挡）。"""
        uia_json = self._uia_path_json(spec)
        if uia_json:
            merged = {**spec, "surface": "desktop_shell"}
            merged.pop("hwnd", None)
            if isinstance(merged.get("uia_path"), str):
                try:
                    merged["uia_path"] = json.loads(uia_json)
                except json.JSONDecodeError:
                    pass
            return self._perform_desktop_shell_precise_step(
                action,
                merged,
                description=description,
                fg_before=fg_before,
                selector_type=selector_type or "uia_path",
            )

        shell_spec: Dict[str, Any] = {"surface": "desktop_shell"}
        hit = desktop_icon_hit_at_screen_point(x, y, shell_spec, background_refresh=False)
        if not hit:
            hit = desktop_icon_hit_at_screen_point(x, y, shell_spec, background_refresh=True)
        click_x, click_y = int(x), int(y)
        icon_name = ""
        if hit:
            left, top, right, bottom, icon_name = hit
            click_x = int((left + right) / 2)
            click_y = int((top + bottom) / 2)
            shell_spec["target_name"] = icon_name
            uat_logger.info(
                "桌面图标命中: 「%s」中心 (%s,%s)",
                icon_name or "?",
                click_x,
                click_y,
            )
        elif not screen_point_on_desktop_shell(x, y):
            raise RuntimeError(
                f"坐标 ({x},{y}) 未命中桌面图标。"
                "请使用「精准定位」重新捕获（生成 uia_path），或对准图标中心后重试。"
            )

        self._app, self._window = attach_desktop_shell(shell_spec)
        act = (action or "click").strip().lower()
        resolved_via = "desktop_shell"
        open_name = icon_name or _icon_name_from_step(
            spec, description, (spec or {}).get("_case_name") or ""
        )

        if act == "double_click" and open_name and shell_open_folder(open_name):
            time.sleep(0.5)
            resolved_via = "shell_open_folder"
            self._last_pointer_delivery = "shell_open"
        else:
            invoked = False
            try:
                ctrl = resolve_desktop_icon_at_point(
                    click_x, click_y, self._window, shell_spec, self._app
                )
                self._invoke_control_pointer(ctrl, act, spec=shell_spec)
                invoked = True
                resolved_via = "desktop_icon_uia"
            except Exception as exc:
                uat_logger.warning("桌面图标 UIA 双击失败: %s", exc)
            if _desktop_shell_physical_enabled() or not invoked:
                pointer_action_at_screen(
                    click_x, click_y, act, force_physical=True
                )
                self._last_pointer_physical = True
                self._last_pointer_delivery = "physical"
                resolved_via = "desktop_shell_physical"
            else:
                self._last_pointer_delivery = "uia"

        merged_spec = {**spec, **shell_spec}
        if open_name:
            merged_spec["target_name"] = open_name
        self._verify_pointer_effect(
            act,
            merged_spec,
            description,
            fg_before=fg_before,
            selector_type=selector_type,
            click_x=click_x,
            click_y=click_y,
        )
        return {
            "status": "success",
            "action": action,
            "resolved_via": resolved_via,
            "coords": f"{click_x},{click_y}",
            "verified": True,
            "pointer_executed": True,
        }

    def _capture_failure_screenshot(self, tag: str = "pointer") -> str:
        """指针/校验失败时保存虚拟桌面截图（DESKTOP_FAILURE_SCREENSHOT=0 可关闭）。"""
        raw = (os.environ.get("DESKTOP_FAILURE_SCREENSHOT") or "1").strip().lower()
        if raw in ("0", "false", "no", "off"):
            return ""
        try:
            import mss  # type: ignore
            import mss.tools  # type: ignore

            left, top, w, h = virtual_screen_rect()
            fname = f"fail_{tag}_{int(time.time() * 1000)}.png"
            out_path = os.path.join(self._screenshot_dir, fname)
            with mss.mss() as sct:
                mon = {"left": left, "top": top, "width": w, "height": h}
                shot = sct.grab(mon)
                mss.tools.to_png(shot.rgb, shot.size, output=out_path)
            return f"/static/desktop_screenshots/{fname}"
        except Exception as exc:
            uat_logger.warning("失败截图保存异常: %s", exc)
            return ""

    def _perform_pointer_step(
        self,
        action: str,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
        *,
        description: str = "",
        locator_candidates: Any = None,
    ) -> Dict[str, Any]:
        """指针步骤：支持重试（DESKTOP_STEP_RETRY）与失败截图。"""
        retries = _desktop_step_retry_count()
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                return self._perform_pointer_step_once(
                    action,
                    selector_type,
                    selector_value,
                    spec,
                    description=description,
                    locator_candidates=locator_candidates,
                )
            except RuntimeError as exc:
                last_err = exc
                if attempt < retries:
                    uat_logger.warning(
                        "桌面指针步骤重试 %s/%s: %s",
                        attempt + 1,
                        retries,
                        exc,
                    )
                    time.sleep(0.35)
                    continue
                shot = self._capture_failure_screenshot("pointer")
                if shot:
                    raise RuntimeError(f"{exc}（失败截图: {shot}）") from exc
                raise
        if last_err:
            raise last_err
        raise RuntimeError("桌面指针步骤执行失败")

    def _perform_pointer_step_once(
        self,
        action: str,
        selector_type: str,
        selector_value: str,
        spec: Dict[str, Any],
        *,
        description: str = "",
        locator_candidates: Any = None,
    ) -> Dict[str, Any]:
        """指针步骤单次执行：屏幕/客户区坐标或 UIA 解析控件。"""
        from desktop_input import get_foreground_hwnd

        fg_before = get_foreground_hwnd()
        st = (selector_type or "").strip().lower()

        if is_desktop_shell_spec(spec) and self._uia_path_json(spec):
            uat_logger.info(
                "桌面精准定位: uia_path target=%s",
                (spec.get("target_name") or "?"),
            )
            return self._perform_desktop_shell_precise_step(
                action,
                spec,
                description=description,
                fg_before=fg_before,
                selector_type=selector_type,
            )

        if st == "visual_template" and (selector_value or "").strip():
            vx, vy = self._match_visual_template_screen(selector_value)
            if self._should_route_desktop_shell_pointer(vx, vy, spec, action=action):
                return self._perform_desktop_shell_pointer_step(
                    action,
                    vx,
                    vy,
                    spec,
                    description=description,
                    fg_before=fg_before,
                    selector_type=selector_type,
                )

        if st in ("coordinate", "client_coord", "relative_coord"):
            if st == "client_coord":
                anchor = resolve_hwnd_from_spec(spec)
                if anchor:
                    cx, cy = _split_coordinate(selector_value)
                    sx, sy = client_to_screen_xy(anchor, cx, cy)
                else:
                    sx, sy = _split_coordinate(selector_value)
            elif st == "relative_coord":
                anchor = resolve_hwnd_from_spec(spec)
                if not anchor:
                    raise RuntimeError(
                        "relative_coord 需要有效 desktop_spec（hwnd），请重新捕获元素"
                    )
                from desktop_precise_locator import relative_coord_to_client_xy

                cx, cy = relative_coord_to_client_xy(spec, selector_value)
                sx, sy = client_to_screen_xy(anchor, cx, cy)
            else:
                sx, sy = _split_coordinate(selector_value)
            if self._should_route_desktop_shell_pointer(
                sx, sy, spec, action=action
            ):
                if _is_misbound_desktop_icon_capture(spec):
                    uat_logger.warning(
                        "桌面步骤误绑到「%s」(process=%s)，坐标 (%s,%s) 改走桌面 Shell 双击",
                        spec.get("window_title") or "?",
                        spec.get("process") or "?",
                        sx,
                        sy,
                    )
                return self._perform_desktop_shell_pointer_step(
                    action,
                    sx,
                    sy,
                    spec,
                    description=description,
                    fg_before=fg_before,
                    selector_type=selector_type,
                )

        shell = is_desktop_shell_spec(spec)
        position_only = _is_screen_position_selector(selector_type)
        self._last_resolved_via = (selector_type or "uia").strip().lower()
        ctrl = self._resolve_step_control(
            selector_type,
            selector_value,
            spec,
            locator_candidates=locator_candidates,
        )
        if not position_only:
            self._assert_control_on_target(ctrl, spec)
        self._assert_control_actionable(ctrl)
        click_x, click_y = self._control_screen_center(ctrl)
        self._invoke_control_pointer(ctrl, action, spec=spec)
        resolved_via = (
            getattr(self, "_last_resolved_via", None)
            or (selector_type or "uia").strip().lower()
        )
        verified = False

        try:
            self._verify_pointer_step(
                action,
                spec,
                description,
                ctrl=ctrl,
                fg_before=fg_before,
                selector_type=selector_type,
                click_x=click_x,
                click_y=click_y,
            )
            verified = True
        except RuntimeError:
            if (
                (action or "").strip().lower() == "double_click"
                and shell
                and (selector_type or "").strip().lower()
                not in ("coordinate", "client_coord", "relative_coord")
            ):
                keyword = infer_effect_keyword(spec, description)
                if keyword and shell_open_folder(keyword):
                    time.sleep(0.5)
                    if wait_for_desktop_effect(
                        keyword,
                        fg_before=fg_before,
                        timeout=6.0,
                        desktop_shell=True,
                        require_verify=True,
                    ):
                        uat_logger.info("桌面双击 Shell 回退打开: %s", keyword)
                        return {
                            "status": "success",
                            "action": action,
                            "resolved_via": "shell_open_folder",
                            "coords": "",
                            "verified": True,
                        }
            raise
        uat_logger.info(
            "桌面指针操作完成: %s via %s @ (%s,%s) verified=%s",
            action,
            resolved_via,
            click_x,
            click_y,
            verified,
        )
        return {
            "status": "success",
            "action": action,
            "resolved_via": resolved_via,
            "coords": f"{click_x},{click_y}",
            "verified": verified,
            "pointer_executed": verified,
        }

    def execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        if not desktop_runtime_available():
            raise RuntimeError(
                "桌面自动化仅支持 Windows，且需安装 pywinauto、pyperclip（见 requirements-windows.txt）"
            )

        step = prepare_desktop_step(step)
        action = (step.get("action") or "").strip()
        spec = parse_desktop_spec(step.get("desktop_spec"))
        selector_type = step.get("selector_type") or "automation_id"
        selector_value = step.get("selector_value") or ""
        try:
            from desktop_precise_locator import enrich_desktop_spec_for_precise_run

            spec = enrich_desktop_spec_for_precise_run(
                spec,
                step.get("locator_candidates"),
                description=(step.get("description") or ""),
                case_name=(
                    step.get("_case_name")
                    or (spec or {}).get("_case_name")
                    or ""
                ),
                selector_type=selector_type,
                selector_value=selector_value,
            )
            step = {**step, "desktop_spec": spec}
        except ImportError:
            pass
        input_value = step.get("input_value") or ""
        compare_type = (step.get("compare_type") or "equals").strip().lower()

        if action == "launch_app":
            err = validate_launch_app_ready(step)
            if err:
                raise ValueError(err)
            path = (input_value or spec.get("path") or spec.get("exe") or "").strip()
            spec = {**spec, "path": path}
            self._attach_fp = None
            self._app, self._window = attach_application(spec)
            self._attach_fp = self._attach_fingerprint(spec)
            self._activate_attached_window(spec)
            return {"status": "success", "action": action, "verified": True}

        if action == "attach_window":
            self._attach_fp = None
            self._app, self._window = attach_application(spec)
            self._attach_fp = self._attach_fingerprint(spec)
            self._activate_attached_window(spec)
            return {"status": "success", "action": action, "verified": True}

        if action in ("click", "double_click", "right_click"):
            self._ensure_attached(spec, action, selector_type)
            return self._perform_pointer_step(
                action,
                selector_type,
                selector_value,
                spec,
                description=(step.get("description") or ""),
                locator_candidates=step.get("locator_candidates"),
            )

        if action in ("input", "fill"):
            if not str(input_value):
                raise ValueError("桌面输入步骤缺少 input_value")
            self._ensure_attached(spec, action, selector_type)
            if selector_value:
                ctrl = self._resolve_step_control(
                    selector_type, selector_value, spec
                )
                ctrl.set_focus()
                try:
                    ctrl.set_edit_text(str(input_value))
                except Exception:
                    ctrl.type_keys(str(input_value), with_spaces=True, set_foreground=True)
            else:
                self._window.set_focus()
                self._window.type_keys(
                    str(input_value), with_spaces=True, set_foreground=True
                )
            return {"status": "success", "action": action}

        if action == "hotkey":
            keys = (input_value or "").strip()
            if not keys:
                raise ValueError("hotkey 需要 input_value，如 ^c 或 %{F4}")
            self._ensure_attached(spec, action, selector_type)
            self._window.set_focus()
            self._window.type_keys(keys, set_foreground=True)
            return {"status": "success", "action": action}

        if action == "wait":
            mode = (compare_type or "fixed").strip().lower()
            if mode == "control" and selector_value:
                self._ensure_attached(spec, action, selector_type)
            if mode == "window" or spec.get("wait_for") == "window":
                title_re = spec.get("window_title_re") or input_value or ".*"
                timeout = float(spec.get("timeout", 30))
                from pywinauto import Application  # type: ignore

                be = spec.get("backend") or "uia"
                Application(backend=be).connect(
                    title_re=title_re, timeout=int(timeout)
                )
            elif mode == "control" and selector_value:
                timeout = float(spec.get("timeout", 30))
                deadline = time.time() + timeout
                last_err = None
                while time.time() < deadline:
                    try:
                        self._resolve_step_control(
                            selector_type, selector_value, spec
                        )
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        time.sleep(0.3)
                if last_err:
                    raise last_err
            else:
                sec = 1.0
                try:
                    sec = float(input_value or "1")
                except ValueError:
                    pass
                if sec <= 120:
                    time.sleep(sec)
                else:
                    time.sleep(sec / 1000.0)
            return {"status": "success", "action": action}

        if action == "screenshot":
            import mss  # type: ignore
            import mss.tools  # type: ignore

            fname = f"desktop_{int(time.time() * 1000)}.png"
            out_path = os.path.join(self._screenshot_dir, fname)
            region = spec.get("region")
            with mss.mss() as sct:
                if region and isinstance(region, (list, tuple)) and len(region) == 4:
                    mon = {
                        "left": int(region[0]),
                        "top": int(region[1]),
                        "width": int(region[2]),
                        "height": int(region[3]),
                    }
                    shot = sct.grab(mon)
                else:
                    shot = sct.grab(sct.monitors[1])
                mss.tools.to_png(shot.rgb, shot.size, output=out_path)
            rel = f"/static/desktop_screenshots/{fname}"
            return {"status": "success", "action": action, "screenshot": rel}

        if action == "verify":
            self._ensure_attached(spec, action, selector_type)
            vt = (
                (input_value or "").strip()
                or (compare_type or "").strip()
                or "auto"
            ).lower()
            if vt in ("auto", "slider", "image", "visible", "exist", "clickable"):
                from desktop_captcha import run_desktop_verify

                return run_desktop_verify(
                    self._window,
                    selector_type,
                    selector_value,
                    spec,
                    vt,
                    app=self._app,
                )
            expected = str(input_value or "")
            if selector_value:
                ctrl = self._resolve_step_control(
                    selector_type, selector_value, spec
                )
                try:
                    actual = ctrl.window_text()
                except Exception:
                    actual = str(getattr(ctrl, "texts", lambda: [""])())
            else:
                actual = self._window.window_text()
            actual = actual or ""
            ct = compare_type or "contains"
            ok = False
            if ct in ("equals", "text_equals"):
                ok = actual == expected
            elif ct in ("contains", "text_contains"):
                ok = expected in actual
            else:
                ok = expected in actual
            if not ok:
                raise AssertionError(
                    f"桌面断言失败：期望「{expected}」({ct})，实际「{actual[:200]}」"
                )
            return {"status": "success", "action": action, "actual": actual}

        if action == "assert":
            self._ensure_attached(spec, action, selector_type)
            expected = str(input_value or "")
            if selector_value:
                ctrl = self._resolve_step_control(
                    selector_type, selector_value, spec
                )
                try:
                    actual = ctrl.window_text()
                except Exception:
                    actual = str(getattr(ctrl, "texts", lambda: [""])())
            else:
                actual = self._window.window_text()
            actual = actual or ""
            ct = compare_type or "contains"
            ok = False
            if ct in ("equals", "text_equals"):
                ok = actual == expected
            elif ct in ("contains", "text_contains"):
                ok = expected in actual
            else:
                ok = expected in actual
            if not ok:
                raise AssertionError(
                    f"桌面断言失败：期望「{expected}」({ct})，实际「{actual[:200]}」"
                )
            return {"status": "success", "action": action, "actual": actual}

        raise ValueError(f"未实现的桌面动作：{action}")

    def inspect_uia_tree(self, max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
        """返回当前窗口 UIA 树片段（供设计期探测）。"""
        if self._window is None:
            raise RuntimeError("请先 attach_window 或 launch_app")

        nodes: List[Dict[str, Any]] = []

        def walk(ctrl, depth: int) -> None:
            if len(nodes) >= max_nodes or depth > max_depth:
                return
            try:
                info = ctrl.element_info
                nodes.append(
                    {
                        "name": getattr(info, "name", "") or "",
                        "automation_id": getattr(info, "automation_id", "") or "",
                        "control_type": str(getattr(info, "control_type", "")),
                        "class_name": getattr(info, "class_name", "") or "",
                        "depth": depth,
                    }
                )
                for ch in ctrl.children():
                    walk(ch, depth + 1)
                    if len(nodes) >= max_nodes:
                        break
            except Exception:
                pass

        walk(self._window, 0)
        return nodes


class DesktopWorker:
    """专用线程执行桌面操作，避免与 Playwright 事件循环冲突。"""

    def __init__(self):
        self.task_queue: queue.Queue = queue.Queue()
        self.result_queue: queue.Queue = queue.Queue()
        self._orphan_results: Dict[str, Tuple[Any, Optional[Exception]]] = {}
        self._orphan_lock = threading.Lock()
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.automation = DesktopAutomation()
        self.thread.start()

    def _worker_loop(self) -> None:
        while True:
            task = self.task_queue.get()
            if task is None:
                break
            task_id, func, args, kwargs = task
            try:
                result = func(*args, **kwargs)
                self.result_queue.put((task_id, result, None))
            except Exception as e:
                self.result_queue.put((task_id, None, e))

    def execute(self, func, *args, timeout: float = 90, **kwargs):
        import uuid

        task_id = str(uuid.uuid4())
        with self._orphan_lock:
            orphan = self._orphan_results.pop(task_id, None)
        if orphan is not None:
            result, err = orphan
            if err:
                raise err
            return result
        self.task_queue.put((task_id, func, args, kwargs))
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._orphan_lock:
                orphan = self._orphan_results.pop(task_id, None)
            if orphan is not None:
                result, err = orphan
                if err:
                    raise err
                return result
            try:
                rid, result, err = self.result_queue.get(timeout=0.3)
                if rid == task_id:
                    if err:
                        raise err
                    return result
                with self._orphan_lock:
                    self._orphan_results[rid] = (result, err)
            except queue.Empty:
                continue
        raise TimeoutError("桌面自动化操作超时")


_worker: Optional[DesktopWorker] = None


def _get_worker() -> DesktopWorker:
    global _worker
    if _worker is None:
        _worker = DesktopWorker()
    return _worker


def _sync_desktop_execute_inprocess(step: Dict[str, Any]) -> Dict[str, Any]:
    w = _get_worker()
    action = (step.get("action") or "").strip()
    timeout = desktop_operation_timeout(action)
    return w.execute(w.automation.execute_step, step, timeout=timeout)


def _sync_desktop_execute_via_gateway(step: Dict[str, Any]) -> Dict[str, Any]:
    from desktop_agent_client import desktop_agent_enabled, remote_execute_step

    if not desktop_agent_enabled():
        raise RuntimeError(
            "DESKTOP_EXECUTION_MODE=gateway 但未配置 DESKTOP_AGENT_GATEWAY_URL / "
            "DESKTOP_AGENT_GATEWAY_SECRET，或网关未启动"
        )
    spec = parse_desktop_spec(step.get("desktop_spec"))
    sid = spec.get("agent_session_id") or spec.get("session_id")
    return remote_execute_step(step, session_id=sid)


def _sync_desktop_execute_remote(step: Dict[str, Any]) -> Dict[str, Any]:
    from desktop_agent_client import desktop_agent_enabled, remote_execute_step

    if not remote_desktop_enabled():
        raise RuntimeError(
            "远程桌面执行已禁用。本地版请使用 DESKTOP_EXECUTION_MODE=inprocess；"
            "企业多机场景请设置 DEPLOYMENT_PROFILE=enterprise"
        )
    if not desktop_agent_enabled():
        raise RuntimeError("DESKTOP_EXECUTION_MODE=remote 但未配置远程 Agent URL/密钥")
    spec = parse_desktop_spec(step.get("desktop_spec"))
    sid = spec.get("agent_session_id") or spec.get("session_id")
    return remote_execute_step(step, session_id=sid)


def sync_desktop_execute_step(step: Dict[str, Any]) -> Dict[str, Any]:
    step = prepare_desktop_step(step)
    if os.environ.get("DESKTOP_GATEWAY_INPROCESS", "").strip() in ("1", "true", "yes"):
        return _sync_desktop_execute_inprocess(step)

    mode = desktop_execution_mode()
    if mode == "inprocess":
        return _sync_desktop_execute_inprocess(step)
    if mode == "gateway":
        return _sync_desktop_execute_via_gateway(step)
    if mode == "remote":
        return _sync_desktop_execute_remote(step)
    return _sync_desktop_execute_inprocess(step)


def sync_desktop_inspect(max_depth: int = 4, max_nodes: int = 120) -> List[Dict[str, Any]]:
    w = _get_worker()
    return w.execute(
        w.automation.inspect_uia_tree, max_depth, max_nodes=max_nodes
    )


def sync_desktop_verify_element(
    selector_type: str,
    selector_value: str,
    desktop_spec: Any = None,
    locator_candidates: Any = None,
) -> Dict[str, Any]:
    """校验桌面元素是否可解析（不依赖 Z 序/遮挡）。"""
    if not desktop_runtime_available():
        return {
            "success": False,
            "error": "桌面自动化不可用（需 Windows + pywinauto）",
        }
    spec = parse_desktop_spec(desktop_spec)
    st = (selector_type or "").strip().lower()
    sv = (selector_value or "").strip()
    try:
        from desktop_precise_locator import enrich_desktop_spec_for_precise_run

        spec = enrich_desktop_spec_for_precise_run(
            spec,
            locator_candidates,
            selector_type=st,
            selector_value=sv,
        )
    except ImportError:
        pass

    w = _get_worker()

    def _resolve() -> Dict[str, Any]:
        auto = w.automation
        uia_json = auto._uia_path_json(spec)
        resolved_via = st or "uia"
        ctrl: Any = None
        if uia_json and (is_desktop_shell_spec(spec) or st == "uia_path"):
            auto._app, auto._window = attach_desktop_shell(spec)
            ctrl = resolve_control(
                auto._window, "uia_path", uia_json, spec, app=auto._app
            )
            resolved_via = "uia_path"
        elif st == "name" and sv and is_desktop_shell_spec(spec):
            auto._app, auto._window = attach_desktop_shell(spec)
            ctrl = resolve_control(
                auto._window, "name", sv, spec, app=auto._app
            )
            resolved_via = "name"
        else:
            auto._app, auto._window = attach_application(spec)
            try_st, try_sv = st, sv
            if uia_json:
                try_st, try_sv = "uia_path", uia_json
            ctrl = resolve_control(
                auto._window, try_st, try_sv, spec, app=auto._app
            )
            resolved_via = try_st
        rect = ctrl.rectangle()
        name = ""
        try:
            name = (getattr(ctrl.element_info, "name", None) or "").strip()
        except Exception:
            pass
        return {
            "success": True,
            "resolved_via": resolved_via,
            "name": name,
            "rectangle": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
            },
            "message": f"已解析控件「{name or resolved_via}」",
        }

    try:
        return w.execute(_resolve, timeout=desktop_operation_timeout())
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def sync_desktop_attach_from_spec(desktop_spec: Dict[str, Any]) -> None:
    w = _get_worker()

    def _attach():
        w.automation._app, w.automation._window = attach_application(desktop_spec)

    w.execute(_attach)


def sync_reset_desktop_automation() -> None:
    """停止用例或强制重置时清理桌面会话，避免对已退出进程调用 top_window。"""
    try:
        w = _get_worker()
        w.execute(w.automation.reset_session, timeout=5)
    except Exception:
        pass
