# -*- coding: utf-8 -*-
"""
单引擎桌面视觉：截图裁切、ORB 定位、步骤 JSON 契约。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

VISUAL_SELECTOR_TYPE = "visual"

# 窗口级原生定位（AI / 步骤编辑常用，不走 visual 框选）
_NATIVE_WINDOW_SELECTORS = frozenset({
    "window",
    "title",
    "hwnd",
    "process",
})

_LEGACY_DESKTOP_SELECTORS = frozenset({
    "automation_id",
    "auto_id",
    "name",
    "uia_path",
    "coordinate",
    "client_coord",
    "relative_coord",
    "control_type",
    "class_name",
    "visual_template",
})


class VisualMatchFailed(RuntimeError):
    """屏幕未找到足够置信度的视觉模板。"""

    def __init__(
        self,
        message: str,
        *,
        failure_screenshot: Optional[str] = None,
        selector_value: Optional[str] = None,
        need_relearn: bool = False,
        best_score: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.failure_screenshot = failure_screenshot
        self.selector_value = selector_value
        self.need_relearn = need_relearn
        self.best_score = best_score


@dataclass
class VisualStepPayload:
    template_image_base64: str
    click_offset_x: int
    click_offset_y: int
    match_threshold: float
    match_method: str
    template_width: int
    template_height: int
    record_virtual_left: int = 0
    record_virtual_top: int = 0
    search_anchor_x: int = 0
    search_anchor_y: int = 0
    element_snapshot: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        body: Dict[str, Any] = {
            "template_image_base64": self.template_image_base64,
            "click_offset": {"x": self.click_offset_x, "y": self.click_offset_y},
            "match_threshold": self.match_threshold,
            "match_method": self.match_method,
            "template_size": {
                "w": self.template_width,
                "h": self.template_height,
            },
            "record_virtual_origin": {
                "left": self.record_virtual_left,
                "top": self.record_virtual_top,
            },
        }
        if self.search_anchor_x or self.search_anchor_y:
            body["search_anchor"] = {
                "x": self.search_anchor_x,
                "y": self.search_anchor_y,
            }
        if self.element_snapshot:
            body["element_snapshot"] = self.element_snapshot
        return json.dumps(body, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "VisualStepPayload":
        data = json.loads((raw or "").strip() or "{}")
        if not isinstance(data, dict):
            raise ValueError("visual 步骤 JSON 无效")
        b64 = (data.get("template_image_base64") or data.get("png_b64") or "").strip()
        if not b64 and isinstance(data.get("selector_value"), str):
            return cls.from_json(data["selector_value"])
        off = data.get("click_offset") or {}
        size = data.get("template_size") or {}
        origin = data.get("record_virtual_origin") or {}
        anchor = data.get("search_anchor") or {}
        if not b64:
            raise ValueError("visual 步骤缺少 template_image_base64")
        snap = data.get("element_snapshot")
        ax = int(anchor.get("x", 0) or 0)
        ay = int(anchor.get("y", 0) or 0)
        return cls(
            template_image_base64=b64,
            click_offset_x=int(off.get("x", 0)),
            click_offset_y=int(off.get("y", 0)),
            match_threshold=float(
                data.get("match_threshold", data.get("threshold", 0.75))
            ),
            match_method=str(data.get("match_method") or "auto").lower(),
            template_width=int(size.get("w", 0)),
            template_height=int(size.get("h", 0)),
            record_virtual_left=int(origin.get("left", 0)),
            record_virtual_top=int(origin.get("top", 0)),
            search_anchor_x=ax,
            search_anchor_y=ay,
            element_snapshot=snap if isinstance(snap, dict) else None,
        )

    def merge_element_snapshot(self, snap: Optional[Dict[str, Any]]) -> "VisualStepPayload":
        if not snap:
            return self
        return VisualStepPayload(
            template_image_base64=self.template_image_base64,
            click_offset_x=self.click_offset_x,
            click_offset_y=self.click_offset_y,
            match_threshold=self.match_threshold,
            match_method=self.match_method,
            template_width=self.template_width,
            template_height=self.template_height,
            record_virtual_left=self.record_virtual_left,
            record_virtual_top=self.record_virtual_top,
            search_anchor_x=self.search_anchor_x,
            search_anchor_y=self.search_anchor_y,
            element_snapshot=snap,
        )


@dataclass
class VisualMatchResult:
    left: int
    top: int
    score: float
    method: str


def _payload_has_element_snapshot(step: Dict[str, Any]) -> bool:
    st = (step.get("selector_type") or "").strip().lower()
    if st != VISUAL_SELECTOR_TYPE:
        return False
    sv = (step.get("selector_value") or "").strip()
    if not sv:
        return False
    try:
        data = json.loads(sv)
        return bool(isinstance(data, dict) and data.get("element_snapshot"))
    except json.JSONDecodeError:
        return False


def is_legacy_desktop_step(step: Dict[str, Any]) -> bool:
    layer = (step.get("automation_layer") or "").strip().lower()
    if layer != "desktop":
        return False
    st = (step.get("selector_type") or "").strip().lower()
    if st == VISUAL_SELECTOR_TYPE:
        return False
    action = (step.get("action") or "").strip().lower()
    if action in (
        "launch_app",
        "wait",
        "hotkey",
        "screenshot",
        "attach_window",
    ):
        return False
    if st in _NATIVE_WINDOW_SELECTORS:
        return False
    if action in ("verify", "assert"):
        sv = (step.get("selector_value") or "").strip()
        spec = step.get("desktop_spec")
        if isinstance(spec, str) and spec.strip():
            try:
                spec = json.loads(spec)
            except json.JSONDecodeError:
                spec = {}
        if not isinstance(spec, dict):
            spec = {}
        if sv or spec.get("title_contains") or spec.get("window_title") or spec.get("window_title_re"):
            return False
    if st in _LEGACY_DESKTOP_SELECTORS:
        return True
    if step.get("locator_candidates") or step.get("desktop_spec"):
        return bool(st)
    return False


def assert_visual_desktop_step(step: Dict[str, Any]) -> VisualStepPayload:
    if is_legacy_desktop_step(step):
        raise RuntimeError(
            "该步骤使用已废弃的 UIA/坐标定位，请用「框选录制」重新捕获为 visual 步骤"
        )
    st = (step.get("selector_type") or "").strip().lower()
    if st != VISUAL_SELECTOR_TYPE:
        raise RuntimeError(
            f"桌面指针步骤 selector_type 必须为 '{VISUAL_SELECTOR_TYPE}'，"
            f"当前为 '{st or '(空)'}'"
        )
    sv = (step.get("selector_value") or "").strip()
    if not sv:
        raise ValueError("visual 步骤缺少 selector_value")
    return VisualStepPayload.from_json(sv)


def virtual_screen_rect() -> Tuple[int, int, int, int]:
    from desktop_input import virtual_screen_rect as _vsr

    return _vsr()


def virtual_screen_origin() -> Tuple[int, int]:
    left, top, _w, _h = virtual_screen_rect()
    return int(left), int(top)


def capture_virtual_desktop_png() -> bytes:
    import mss  # type: ignore
    import mss.tools  # type: ignore

    vl, vt, vw, vh = virtual_screen_rect()
    with mss.mss() as sct:
        shot = sct.grab({"left": vl, "top": vt, "width": vw, "height": vh})
        return mss.tools.to_png(shot.rgb, shot.size)


def capture_region_png(
    left: int, top: int, right: int, bottom: int, *, padding: int = 2
) -> bytes:
    import mss  # type: ignore
    import mss.tools  # type: ignore

    l = int(min(left, right)) - padding
    t = int(min(top, bottom)) - padding
    w = max(4, int(max(left, right) - min(left, right)) + 2 * padding)
    h = max(4, int(max(top, bottom) - min(top, bottom)) + 2 * padding)
    with mss.mss() as sct:
        shot = sct.grab({"left": l, "top": t, "width": w, "height": h})
        return mss.tools.to_png(shot.rgb, shot.size)


def _bgr_from_png(png: bytes):
    import cv2
    import numpy as np

    arr = np.frombuffer(png, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def refine_template_by_corners(bgr_roi, *, max_side: int = 64) -> Tuple[Any, int, int]:
    import cv2

    if bgr_roi is None or bgr_roi.size == 0:
        raise ValueError("框选区域无效")
    h, w = bgr_roi.shape[:2]
    gray = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY)
    corners = cv2.goodFeaturesToTrack(
        gray, maxCorners=8, qualityLevel=0.01, minDistance=8, blockSize=5
    )
    if corners is not None and len(corners) >= 1:
        xs = [int(c[0][0]) for c in corners]
        ys = [int(c[0][1]) for c in corners]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        half = max(12, min(max_side // 2, min(w, h) // 3))
        x0 = max(0, min(w - 4, cx - half))
        y0 = max(0, min(h - 4, cy - half))
        x1 = min(w, x0 + max_side)
        y1 = min(h, y0 + max_side)
        return bgr_roi[y0:y1, x0:x1], x0, y0
    side = min(max_side, w, h)
    return bgr_roi[0:side, 0:side], 0, 0


def shrink_template_bgr(bgr, max_side: int = 192):
    import cv2

    h, w = bgr.shape[:2]
    m = max(h, w)
    if m <= max_side:
        return bgr
    scale = max_side / float(m)
    nw = max(4, int(w * scale))
    nh = max(4, int(h * scale))
    return cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)


def encode_png_bgr(bgr) -> bytes:
    import cv2

    ok, buf = cv2.imencode(".png", bgr)
    if not ok or buf is None:
        return b""
    return bytes(buf)


def build_visual_step_payload(
    left: int,
    top: int,
    right: int,
    bottom: int,
    click_x: int,
    click_y: int,
    *,
    match_threshold: float = 0.72,
    element_snapshot: Optional[Dict[str, Any]] = None,
) -> VisualStepPayload:
    png = capture_region_png(left, top, right, bottom, padding=0)
    bgr = _bgr_from_png(png)
    refined, ox, oy = refine_template_by_corners(bgr)
    refined = shrink_template_bgr(refined)
    rh, rw = refined.shape[:2]
    b64 = base64.b64encode(encode_png_bgr(refined)).decode("ascii")
    rel_x = int(click_x - min(left, right)) - ox
    rel_y = int(click_y - min(top, bottom)) - oy
    rel_x = max(0, min(max(rw - 1, 0), rel_x))
    rel_y = max(0, min(max(rh - 1, 0), rel_y))
    vl, vt = virtual_screen_origin()
    return VisualStepPayload(
        template_image_base64=b64,
        click_offset_x=rel_x,
        click_offset_y=rel_y,
        match_threshold=float(match_threshold),
        match_method="auto",
        template_width=rw,
        template_height=rh,
        record_virtual_left=vl,
        record_virtual_top=vt,
        search_anchor_x=int(click_x),
        search_anchor_y=int(click_y),
        element_snapshot=element_snapshot,
    )


def _orb_match(template_bgr, scene_bgr) -> Optional[VisualMatchResult]:
    import cv2
    import numpy as np

    tpl_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    scn_gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=500)
    kp1, des1 = orb.detectAndCompute(tpl_gray, None)
    kp2, des2 = orb.detectAndCompute(scn_gray, None)
    if des1 is None or des2 is None or len(kp1) < 4 or len(kp2) < 4:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    matches = bf.knnMatch(des1, des2, k=2)
    good = []
    for pair in matches:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) < 4:
        return None
    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if M is None:
        return None
    th, tw = tpl_gray.shape[:2]
    corners = np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, M)
    xs = projected[:, 0, 0]
    ys = projected[:, 0, 1]
    left = int(min(xs))
    top = int(min(ys))
    inliers = int(mask.sum()) if mask is not None else len(good)
    score = inliers / max(1, len(good))
    return VisualMatchResult(left=left, top=top, score=float(score), method="orb")


def _template_match(template_bgr, scene_bgr) -> Optional[VisualMatchResult]:
    import cv2

    tpl = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    scn = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
    best: Optional[VisualMatchResult] = None
    for scale in (0.75, 0.85, 1.0, 1.15, 1.25):
        th, tw = tpl.shape[:2]
        nw = max(4, int(tw * scale))
        nh = max(4, int(th * scale))
        if nh > scn.shape[0] or nw > scn.shape[1]:
            continue
        scaled = cv2.resize(tpl, (nw, nh), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(scn, scaled, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(res)
        if best is None or max_v > best.score:
            best = VisualMatchResult(
                left=int(max_loc[0]),
                top=int(max_loc[1]),
                score=float(max_v),
                method="template",
            )
    return best


def find_best_template_match(
    template_png: bytes,
    screen_png: bytes,
    *,
    match_method: str = "auto",
) -> Optional[VisualMatchResult]:
    """返回最佳匹配（忽略 threshold），供失败诊断裁剪 ROI 使用。"""
    tpl = _bgr_from_png(template_png)
    scene = _bgr_from_png(screen_png)
    if tpl is None or scene is None:
        return None
    method = (match_method or "auto").lower()
    candidates: List[VisualMatchResult] = []
    if method in ("auto", "template"):
        tm = _template_match(tpl, scene)
        if tm:
            candidates.append(tm)
    if method in ("auto", "orb"):
        orb = _orb_match(tpl, scene)
        if orb:
            candidates.append(orb)
    if not candidates:
        return None
    return max(candidates, key=lambda h: h.score)


def _min_score_for_hit(threshold: float, method: str, *, roi: bool = False) -> float:
    th = float(threshold)
    if method == "template":
        if roi:
            return max(0.52, th - 0.12)
        return max(0.58, th - 0.08)
    return max(0.40, th * 0.55)


def _need_relearn_score(best_score: float, threshold: float, method: str) -> bool:
    min_score = _min_score_for_hit(threshold, method)
    edge_low = max(0.55, min_score - 0.12)
    return edge_low <= best_score < min_score


def locate_template_on_screen(
    template_png: bytes,
    screen_png: bytes,
    *,
    match_method: str = "auto",
    threshold: float = 0.72,
    roi_origin: Optional[Tuple[int, int]] = None,
    roi_search: bool = False,
) -> VisualMatchResult:
    tpl = _bgr_from_png(template_png)
    scene = _bgr_from_png(screen_png)
    if tpl is None or scene is None:
        raise VisualMatchFailed("无法解码模板或屏幕截图")
    hit = find_best_template_match(
        template_png, screen_png, match_method=match_method
    )
    if hit is None:
        raise VisualMatchFailed(f"视觉匹配失败（未找到特征，threshold={threshold}）")
    in_roi = bool(roi_search or roi_origin is not None)
    min_score = _min_score_for_hit(threshold, hit.method, roi=in_roi)
    if hit.score < min_score:
        if (match_method or "auto").lower() in ("auto", "orb") and hit.method == "template":
            orb_only = _orb_match(tpl, scene)
            if orb_only is not None:
                orb_min = _min_score_for_hit(threshold, "orb", roi=in_roi)
                if orb_only.score >= orb_min:
                    hit = orb_only
                    min_score = orb_min
    if hit.score < min_score:
        need_relearn = _need_relearn_score(hit.score, threshold, hit.method)
        raise VisualMatchFailed(
            f"视觉匹配失败（score={hit.score:.3f} < {min_score:.3f}，method={hit.method}）",
            need_relearn=need_relearn,
            best_score=hit.score,
        )
    if roi_origin:
        hit = VisualMatchResult(
            left=hit.left + roi_origin[0],
            top=hit.top + roi_origin[1],
            score=hit.score,
            method=hit.method,
        )
    return hit


def _roi_around_center(
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    *,
    padding_factor: float = 2.5,
    min_side: int = 300,
) -> Tuple[int, int, int, int]:
    vl, vt, vw, vh = virtual_screen_rect()
    pad = max(min_side // 2, int(max(width, height) * padding_factor))
    half_w = max(min_side // 2, width // 2 + pad)
    half_h = max(min_side // 2, height // 2 + pad)
    left = max(vl, int(center_x) - half_w)
    top = max(vt, int(center_y) - half_h)
    right = min(vl + vw, int(center_x) + half_w)
    bottom = min(vt + vh, int(center_y) + half_h)
    if right - left < 8:
        right = min(vl + vw, left + 8)
    if bottom - top < 8:
        bottom = min(vt + vh, top + 8)
    return left, top, right, bottom


def build_visual_failure_artifact_png(payload: VisualStepPayload) -> bytes:
    """
    匹配失败诊断图：左侧为录制模板，右侧为当前屏幕上的搜索 ROI（非整屏）。
    """
    import cv2
    import numpy as np

    tpl_png = base64.b64decode(payload.template_image_base64)
    screen_png = capture_virtual_desktop_png()
    tpl_bgr = _bgr_from_png(tpl_png)
    if tpl_bgr is None:
        raise VisualMatchFailed("无法解码模板图像")
    tw = payload.template_width or tpl_bgr.shape[1]
    th = payload.template_height or tpl_bgr.shape[0]
    vl, vt = virtual_screen_origin()
    best = find_best_template_match(
        tpl_png, screen_png, match_method=payload.match_method
    )
    if payload.search_anchor_x or payload.search_anchor_y:
        center_x = int(payload.search_anchor_x)
        center_y = int(payload.search_anchor_y)
    elif best:
        center_x = vl + best.left + tw // 2
        center_y = vt + best.top + th // 2
    else:
        _vl, _vt, vw, vh = virtual_screen_rect()
        center_x = _vl + vw // 2
        center_y = _vt + vh // 2
    left, top, right, bottom = _roi_around_center(center_x, center_y, tw, th)
    roi_bgr = _bgr_from_png(capture_region_png(left, top, right, bottom, padding=0))
    if roi_bgr is None:
        roi_bgr = np.zeros((max(th, 48), max(tw, 48), 3), dtype=np.uint8)

    target_h = max(120, roi_bgr.shape[0])
    tpl_scale = target_h / max(1, tpl_bgr.shape[0])
    tpl_disp = cv2.resize(
        tpl_bgr,
        (max(4, int(tpl_bgr.shape[1] * tpl_scale)), target_h),
        interpolation=cv2.INTER_AREA,
    )
    if roi_bgr.shape[0] != target_h:
        roi_disp = cv2.resize(
            roi_bgr,
            (max(4, int(roi_bgr.shape[1] * target_h / max(1, roi_bgr.shape[0]))), target_h),
            interpolation=cv2.INTER_AREA,
        )
    else:
        roi_disp = roi_bgr
    gap = np.full((target_h, 6, 3), 200, dtype=np.uint8)
    combined = np.hstack([tpl_disp, gap, roi_disp])
    return encode_png_bgr(combined)


def _resolve_visual_on_roi(
    payload: VisualStepPayload,
    anchor_x: int,
    anchor_y: int,
    *,
    expand: float = 1.0,
) -> Tuple[int, int, float]:
    tpl_png = base64.b64decode(payload.template_image_base64)
    tw = payload.template_width or 48
    th = payload.template_height or 48
    factor = 2.5 * expand
    left, top, right, bottom = _roi_around_center(
        anchor_x, anchor_y, tw, th, padding_factor=factor, min_side=300
    )
    roi_png = capture_region_png(left, top, right, bottom, padding=0)
    hit = locate_template_on_screen(
        tpl_png,
        roi_png,
        match_method=payload.match_method,
        threshold=payload.match_threshold,
        roi_search=True,
    )
    abs_left = left + hit.left
    abs_top = top + hit.top
    x = abs_left + payload.click_offset_x
    y = abs_top + payload.click_offset_y
    return int(x), int(y), hit.score


def resolve_visual_click_point(
    payload: VisualStepPayload,
    *,
    anchor_x: Optional[int] = None,
    anchor_y: Optional[int] = None,
) -> Tuple[int, int, float]:
    ax = int(anchor_x if anchor_x is not None else payload.search_anchor_x)
    ay = int(anchor_y if anchor_y is not None else payload.search_anchor_y)
    tpl_png = base64.b64decode(payload.template_image_base64)
    tw = payload.template_width or 48
    th = payload.template_height or 48

    if ax or ay:
        try:
            return _resolve_visual_on_roi(payload, ax, ay, expand=1.0)
        except VisualMatchFailed:
            try:
                return _resolve_visual_on_roi(payload, ax, ay, expand=1.5)
            except VisualMatchFailed:
                pass

    screen_png = capture_virtual_desktop_png()
    try:
        hit = locate_template_on_screen(
            tpl_png,
            screen_png,
            match_method=payload.match_method,
            threshold=payload.match_threshold,
        )
    except VisualMatchFailed as exc:
        if ax or ay:
            raise
        raise
    x = hit.left + payload.click_offset_x
    y = hit.top + payload.click_offset_y
    vl, vt = virtual_screen_origin()
    return vl + x, vt + y, hit.score


def update_visual_template_at_click(
    payload_json: str, click_x: int, click_y: int, *, half_size: int = 24
) -> str:
    half = max(8, int(half_size))
    payload = VisualStepPayload.from_json(payload_json)
    png = capture_region_png(
        click_x - half,
        click_y - half,
        click_x + half,
        click_y + half,
        padding=0,
    )
    bgr = _bgr_from_png(png)
    refined, ox, oy = refine_template_by_corners(bgr, max_side=half * 2)
    refined = shrink_template_bgr(refined)
    rh, rw = refined.shape[:2]
    vl, vt = virtual_screen_origin()
    rel_x = half - ox
    rel_y = half - oy
    new_payload = VisualStepPayload(
        template_image_base64=base64.b64encode(encode_png_bgr(refined)).decode(
            "ascii"
        ),
        click_offset_x=max(0, min(max(rw - 1, 0), rel_x)),
        click_offset_y=max(0, min(max(rh - 1, 0), rel_y)),
        match_threshold=payload.match_threshold,
        match_method=payload.match_method,
        template_width=rw,
        template_height=rh,
        record_virtual_left=vl,
        record_virtual_top=vt,
        search_anchor_x=int(click_x),
        search_anchor_y=int(click_y),
        element_snapshot=payload.element_snapshot,
    )
    return new_payload.to_json()
