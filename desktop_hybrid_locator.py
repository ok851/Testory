# -*- coding: utf-8 -*-
"""
混合桌面定位：UIA 结构优先，视觉 ROI 兜底。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from desktop_shell_application import ShellComTarget
    from desktop_shell_listview import ShellIconTarget

from desktop_visual_engine import (
    VisualMatchFailed,
    assert_visual_desktop_step,
    build_visual_failure_artifact_png,
    resolve_visual_click_point,
)


@dataclass
class DesktopResolveResult:
    x: int
    y: int
    score: float
    resolved_via: str
    need_relearn: bool = False
    best_score: float = 0.0
    updated_anchor: Optional[Tuple[int, int]] = None
    shell_target: Optional["ShellIconTarget"] = None
    shell_com_target: Optional["ShellComTarget"] = None


def _shell_com_result(icon_name: str, matched_name: str) -> DesktopResolveResult:
    from desktop_shell_application import ShellComTarget

    return DesktopResolveResult(
        x=0,
        y=0,
        score=1.0,
        resolved_via="shell_com",
        shell_com_target=ShellComTarget(icon_name=icon_name, matched_name=matched_name),
    )


def _shell_result_from_target(
    shell: "ShellIconTarget",
    off_x: int,
    off_y: int,
    tw: int,
    th: int,
) -> DesktopResolveResult:
    sx = int(shell.screen_x) + int(off_x) - max(1, tw // 2)
    sy = int(shell.screen_y) + int(off_y) - max(1, th // 2)
    return DesktopResolveResult(
        x=sx,
        y=sy,
        score=1.0,
        resolved_via="shell_listview",
        updated_anchor=(shell.screen_x, shell.screen_y),
        shell_target=shell,
    )


def _try_shell_at_screen_for_listitem(
    step: Dict[str, Any],
    screen_x: int,
    screen_y: int,
) -> Optional["ShellIconTarget"]:
    from desktop_shell_listview import (
        icon_name_from_step,
        is_desktop_listitem_step,
        resolve_shell_listview_at_screen,
        shell_message_enabled,
    )

    if not shell_message_enabled() or not is_desktop_listitem_step(step):
        return None
    return resolve_shell_listview_at_screen(
        int(screen_x),
        int(screen_y),
        icon_name=icon_name_from_step(step),
    )


def _parse_locator_candidates(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = step.get("locator_candidates")
    if not raw:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except json.JSONDecodeError:
            return []
    return []


def _element_snapshot_from_uia_nodes(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """将录制页 locator_candidates 中的 uia_path 转为 UIA 快照结构。"""
    if not nodes:
        return {}
    chain_nodes = [dict(n) for n in nodes]
    target = chain_nodes[-1]
    parent_chain = chain_nodes[:-1] if len(chain_nodes) > 1 else []
    anchor = (target.get("control_type") or "ListItem").strip()
    if "listitem" in anchor.lower():
        anchor = "ListItem"
    key_candidates: List[Dict[str, str]] = []
    aid = (target.get("automation_id") or "").strip()
    nm = (target.get("name") or "").strip()
    if aid:
        key_candidates.append(
            {"property": "automation_id", "value": aid, "match": "equals"}
        )
    if nm:
        key_candidates.append({"property": "uia-name", "value": nm, "match": "equals"})
    return {
        "selector": {
            "anchor_props": anchor,
            "key_candidates": key_candidates,
            "parent_chain": parent_chain,
        }
    }


def element_snapshot_for_step(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """优先 selector_value 内嵌快照，其次 locator_candidates.uia_path。"""
    try:
        payload = assert_visual_desktop_step(step)
        if payload.element_snapshot:
            return payload.element_snapshot
    except Exception:
        pass
    for cand in _parse_locator_candidates(step):
        st = (cand.get("selector_type") or "").strip().lower()
        if st != "uia_path":
            continue
        sv = cand.get("selector_value") or ""
        try:
            nodes = json.loads(sv) if isinstance(sv, str) else sv
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(nodes, list) and nodes:
            snap = _element_snapshot_from_uia_nodes(nodes)
            if snap.get("selector"):
                return snap
    return None


def _effect_keyword_from_step(step: Dict[str, Any]) -> str:
    from desktop_input import infer_effect_keyword

    spec = step.get("desktop_spec")
    if isinstance(spec, str) and spec.strip():
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError:
            spec = {}
    if not isinstance(spec, dict):
        spec = {}
    desc = (step.get("description") or "").strip()
    kw = infer_effect_keyword(spec, desc)
    if kw:
        return kw
    snap = element_snapshot_for_step(step)
    if snap:
        sel = snap.get("selector") or snap
        for cand in sel.get("key_candidates") or []:
            val = (cand.get("value") or "").strip()
            if val and val not in ("桌面", "Desktop", "桌面 1"):
                return val
    return ""


def _uia_click_from_center(
    cx: int, cy: int, off_x: int, off_y: int, tw: int, th: int
) -> Tuple[int, int]:
    """将模板左上角偏移换算为以控件中心为参考的屏幕坐标。"""
    half_w = max(1, tw // 2)
    half_h = max(1, th // 2)
    return int(cx - half_w + off_x), int(cy - half_h + off_y)


def _try_uia_core_locate(step: Dict[str, Any], off_x: int, off_y: int, tw: int, th: int) -> Optional[DesktopResolveResult]:
    """使用UIAutomationCore原生接口通过acc-name定位。"""
    try:
        from desktop_uia_core import find_element_by_acc_name

        kw = _effect_keyword_from_step(step)
        if not kw:
            return None

        result = find_element_by_acc_name(kw)
        if result.get("ok") and result.get("bounding_rect"):
            rect = result["bounding_rect"]
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            click_x, click_y = _uia_click_from_center(cx, cy, off_x, off_y, tw, th)
            click_x, click_y = _try_dpi_calibration(click_x, click_y)
            return DesktopResolveResult(
                x=click_x,
                y=click_y,
                score=0.9,
                resolved_via="uia_core",
                updated_anchor=(cx, cy),
            )
    except Exception:
        pass
    return None


def _try_ocr_locate(step: Dict[str, Any], off_x: int, off_y: int, tw: int, th: int) -> Optional[DesktopResolveResult]:
    """使用OCR文本定位作为回退方案。"""
    try:
        from desktop_ocr_locate import locate_element_via_ocr

        kw = _effect_keyword_from_step(step)
        if not kw:
            return None

        from desktop_input import get_cursor_pos

        cursor_x, cursor_y = get_cursor_pos()
        ocr_result = locate_element_via_ocr(cursor_x, cursor_y, search_radius=150)
        if ocr_result and ocr_result.get('rect'):
            rect = ocr_result['rect']
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            click_x, click_y = _uia_click_from_center(cx, cy, off_x, off_y, tw, th)
            return DesktopResolveResult(
                x=click_x,
                y=click_y,
                score=0.75,
                resolved_via="ocr",
                updated_anchor=(cx, cy),
            )
    except Exception:
        pass
    return None


def _try_dpi_calibration(x: int, y: int) -> Tuple[int, int]:
    """DPI坐标校准。"""
    try:
        from desktop_precise_locator import get_dpi_scale_factor

        scale = get_dpi_scale_factor()
        if scale != 1.0:
            return int(x * scale), int(y * scale)
    except Exception:
        pass
    return x, y


def resolve_desktop_click_point(step: Dict[str, Any]) -> DesktopResolveResult:
    # 桌面图标：Shell.Application COM（零 UI、不截图，最高优先级）
    try:
        from desktop_shell_application import try_resolve_shell_application_step

        com = try_resolve_shell_application_step(step)
        if com:
            return _shell_com_result(com.icon_name, com.matched_name)
    except ImportError:
        pass

    payload = assert_visual_desktop_step(step)
    off_x = int(payload.click_offset_x)
    off_y = int(payload.click_offset_y)
    tw = int(payload.template_width or 48)
    th = int(payload.template_height or 48)
    anchor_x = payload.search_anchor_x
    anchor_y = payload.search_anchor_y
    snap = payload.element_snapshot or element_snapshot_for_step(step)

    use_ax, use_ay = anchor_x, anchor_y

    # 桌面图标：SysListView32 后台消息（不抢鼠标、无视遮挡）
    try:
        from desktop_shell_listview import try_resolve_shell_listview_step

        shell = try_resolve_shell_listview_step(step)
        if shell:
            return _shell_result_from_target(shell, off_x, off_y, tw, th)
    except ImportError:
        pass

    if snap:
        try:
            from desktop_uia_snapshot import resolve_uia_click_point

            uia = resolve_uia_click_point(snap, timeout_sec=3.0)
            if uia.ok:
                cx, cy = uia.x, uia.y
                if uia.anchor:
                    cx, cy = uia.anchor
                    use_ax, use_ay = cx, cy
                click_x, click_y = _uia_click_from_center(cx, cy, off_x, off_y, tw, th)
                click_x, click_y = _try_dpi_calibration(click_x, click_y)
                shell = _try_shell_at_screen_for_listitem(step, cx, cy)
                if shell:
                    res = _shell_result_from_target(shell, off_x, off_y, tw, th)
                    res.x = click_x
                    res.y = click_y
                    return res
                return DesktopResolveResult(
                    x=click_x,
                    y=click_y,
                    score=float(uia.score),
                    resolved_via="uia",
                    updated_anchor=(cx, cy),
                )
        except ImportError:
            pass

    if snap:
        sel = snap.get("selector") or {}
        if sel.get("resolved_via") == "win32":
            try:
                from desktop_uia_snapshot import resolve_win32_click_point

                win32 = resolve_win32_click_point(snap, timeout_sec=3.0)
                if win32.ok:
                    cx, cy = win32.x, win32.y
                    if win32.anchor:
                        cx, cy = win32.anchor
                        use_ax, use_ay = cx, cy
                    click_x, click_y = _uia_click_from_center(cx, cy, off_x, off_y, tw, th)
                    click_x, click_y = _try_dpi_calibration(click_x, click_y)
                    return DesktopResolveResult(
                        x=click_x,
                        y=click_y,
                        score=float(win32.score),
                        resolved_via="win32",
                        updated_anchor=(cx, cy),
                    )
            except ImportError:
                pass

    if use_ax or use_ay:
        shell = _try_shell_at_screen_for_listitem(step, int(use_ax), int(use_ay))
        if shell:
            return _shell_result_from_target(shell, off_x, off_y, tw, th)

    # UIAutomationCore acc-name定位作为回退方案
    uia_core_result = _try_uia_core_locate(step, off_x, off_y, tw, th)
    if uia_core_result:
        return uia_core_result

    # OCR文本定位作为回退方案
    ocr_result = _try_ocr_locate(step, off_x, off_y, tw, th)
    if ocr_result:
        return ocr_result

    # 有录制锚点时优先 ROI，减少全屏截图导致的黑屏/卡顿
    visual_attempts: List[Tuple[Optional[int], Optional[int], str]] = [
        (use_ax or None, use_ay or None, "visual_roi"),
        (None, None, "visual"),
    ]
    last_exc: Optional[VisualMatchFailed] = None
    for ax_try, ay_try, via in visual_attempts:
        if via == "visual_roi" and not (ax_try or ay_try):
            continue
        try:
            x, y, score = resolve_visual_click_point(
                payload,
                anchor_x=ax_try,
                anchor_y=ay_try,
            )
            x, y = _try_dpi_calibration(x, y)
            if via in ("visual", "visual_roi"):
                shell = _try_shell_at_screen_for_listitem(step, x, y)
                if shell:
                    return _shell_result_from_target(shell, off_x, off_y, tw, th)
            return DesktopResolveResult(
                x=x,
                y=y,
                score=score,
                resolved_via=via,
                updated_anchor=(use_ax, use_ay) if (use_ax or use_ay) else None,
            )
        except VisualMatchFailed as exc:
            last_exc = exc
            continue
    if last_exc:
        exc = last_exc
    else:
        exc = VisualMatchFailed("视觉匹配失败")
    try:
        raise exc
    except VisualMatchFailed as exc:
        shot = None
        try:
            import os
            import time

            png = build_visual_failure_artifact_png(payload)
            d = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "static",
                "desktop_screenshots",
            )
            os.makedirs(d, exist_ok=True)
            fname = f"desktop_fail_{int(time.time() * 1000)}.png"
            with open(os.path.join(d, fname), "wb") as f:
                f.write(png)
            shot = f"/static/desktop_screenshots/{fname}"
        except Exception:
            shot = getattr(exc, "failure_screenshot", None)
        raise VisualMatchFailed(
            str(exc),
            failure_screenshot=shot,
            selector_value=(step.get("selector_value") or "").strip() or None,
            need_relearn=getattr(exc, "need_relearn", False),
            best_score=getattr(exc, "best_score", 0.0),
        ) from exc
