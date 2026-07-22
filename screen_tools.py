# -*- coding: utf-8 -*-
"""按需屏幕观察工具：get_screen_text / get_screen_description。

供外层 Function Calling 与 MCP 共用。不做定时注入，由 Agent 主动调用。
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger

_TOOL_TIMEOUT_SEC = 6.0
_DESC_TIMEOUT_SEC = 6.0
_HASH_SIMILARITY_THRESHOLD = 0.95
_DESC_MAX_CHARS = 300
_MIN_OCR_CONFIDENCE = 0.45

_PRIVACY_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "[PHONE]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    (re.compile(r"\b\d{17}[\dXx]|\d{15}\b"), "[ID]"),
]

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="screen_tools")
_cache_lock = threading.Lock()
_ocr_cache: Dict[str, Any] = {
    "hash": "",
    "blocks": [],
    "texts": [],
    "ts": 0.0,
}


def filter_privacy(text: str) -> str:
    out = text or ""
    for pattern, replacement in _PRIVACY_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def capture_primary_monitor_png(region: Optional[Dict[str, int]] = None) -> Optional[bytes]:
    """截取主显示器；region 可为 {left, top, width, height}（相对主显示器）。"""
    try:
        import mss
        from mss.tools import to_png

        mss_cls = getattr(mss, "MSS", None) or mss.mss
        with mss_cls() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            if region:
                mon = {
                    "left": int(monitor["left"]) + int(region.get("left", 0)),
                    "top": int(monitor["top"]) + int(region.get("top", 0)),
                    "width": max(1, int(region.get("width") or monitor["width"])),
                    "height": max(1, int(region.get("height") or monitor["height"])),
                }
            else:
                mon = monitor
            shot = sct.grab(mon)
            return to_png(shot.rgb, shot.size)
    except Exception as e:
        uat_logger.warning("screen_tools capture failed: %s", e)
        return None


def _is_testory_title(title: str) -> bool:
    t = (title or "").lower()
    return any(
        m in t
        for m in (
            "testory",
            "ai 自动化测试",
            "自动化测试平台",
            "ai test",
            "newuitestplatform",
        )
    )


def capture_hwnd_png(hwnd: int) -> Tuple[Optional[bytes], Dict[str, Any]]:
    """按 hwnd 截取窗口矩形（不强制前台），观察对比时优先用此路径。"""
    meta: Dict[str, Any] = {"surface": "hwnd"}
    hwnd = int(hwnd or 0)
    if not hwnd:
        return None, meta
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return None, meta
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        title = (buf.value or "").strip()
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None, meta
        left, top = int(rect.left), int(rect.top)
        w = max(0, int(rect.right - rect.left))
        h = max(0, int(rect.bottom - rect.top))
        if w < 40 or h < 40:
            return None, {**meta, "hwnd": hwnd, "title": title, "width": w, "height": h}
        import mss
        from mss.tools import to_png

        mss_cls = getattr(mss, "MSS", None) or mss.mss
        with mss_cls() as sct:
            mon = {"left": left, "top": top, "width": w, "height": h}
            shot = sct.grab(mon)
            png = to_png(shot.rgb, shot.size)
            meta = {
                "surface": "hwnd",
                "hwnd": hwnd,
                "title": title,
                "left": left,
                "top": top,
                "width": w,
                "height": h,
            }
            return png, meta
    except Exception as e:
        uat_logger.debug("hwnd capture failed: %s", e)
        return None, meta


def capture_foreground_window_png() -> Tuple[Optional[bytes], Dict[str, Any]]:
    """截取前台窗口客户区（跳过 Testory 自身，回退主屏）。返回 (png, meta)。"""
    meta: Dict[str, Any] = {"surface": "primary_monitor"}
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if hwnd:
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            title = (buf.value or "").strip()
            if title and not _is_testory_title(title):
                png, meta_h = capture_hwnd_png(hwnd)
                if png:
                    meta_h["surface"] = "foreground_window"
                    return png, meta_h
    except Exception as e:
        uat_logger.debug("foreground capture failed: %s", e)
    return capture_primary_monitor_png(), meta


def capture_for_observation(
    region: Optional[Dict[str, int]] = None,
    *,
    prefer_foreground: bool = True,
) -> Tuple[Optional[bytes], Dict[str, Any]]:
    if region:
        return capture_primary_monitor_png(region), {"surface": "region", "region": region}
    if prefer_foreground:
        return capture_foreground_window_png()
    return capture_primary_monitor_png(), {"surface": "primary_monitor"}


def _image_hash(png_bytes: bytes) -> str:
    return hashlib.md5(png_bytes[::16]).hexdigest()


def _hash_similarity(a: str, b: str) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / float(len(a))


def _run_with_timeout(fn, timeout: float = _TOOL_TIMEOUT_SEC):
    fut = _executor.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeout:
        raise TimeoutError(f"操作超时（>{int(timeout)}s），请缩小区域后重试")


def _aggregate_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按纵向邻近聚合文本块，剔除噪声。"""
    from desktop_ocr import _is_noise_text

    filtered = []
    for b in blocks:
        text = (b.get("text") or "").strip()
        conf = float(b.get("confidence") or 0)
        if conf < _MIN_OCR_CONFIDENCE:
            continue
        if _is_noise_text(text):
            continue
        filtered.append(b)
    if not filtered:
        return []

    filtered.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
    groups: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for b in filtered:
        l, t, r, bot = b["bbox"]
        if cur is None:
            cur = {
                "text": b["text"],
                "bbox": [l, t, r, bot],
                "confidence": b["confidence"],
            }
            continue
        cl, ct, cr, cb = cur["bbox"]
        # 同一行或紧邻行合并
        if abs(t - ct) < 28 and l - cr < 80:
            cur["text"] = (cur["text"] + " " + b["text"]).strip()
            cur["bbox"] = [min(cl, l), min(ct, t), max(cr, r), max(cb, bot)]
            cur["confidence"] = min(float(cur["confidence"]), float(b["confidence"]))
        else:
            groups.append(cur)
            cur = {
                "text": b["text"],
                "bbox": [l, t, r, bot],
                "confidence": b["confidence"],
            }
    if cur:
        groups.append(cur)
    return groups


def _ocr_blocks_uncached(png_bytes: bytes) -> List[Dict[str, Any]]:
    from desktop_ocr import extract_text_blocks

    raw = extract_text_blocks(png_bytes, min_confidence=0.0)
    return _aggregate_blocks(raw)


def get_screen_text(region: Optional[Any] = None) -> Dict[str, Any]:
    """获取当前屏幕可见文字及位置。region 可选 dict 或窗口标题提示字符串。"""
    region_dict: Optional[Dict[str, int]] = None
    region_hint = ""
    if isinstance(region, dict):
        region_dict = {
            "left": int(region.get("left", 0)),
            "top": int(region.get("top", 0)),
            "width": int(region.get("width", 0) or 0),
            "height": int(region.get("height", 0) or 0),
        }
        if not region_dict["width"] or not region_dict["height"]:
            region_dict = None
    elif isinstance(region, str) and region.strip():
        region_hint = region.strip()

    def _work() -> Dict[str, Any]:
        from desktop_ocr import engine_name, ocr_available

        eng = engine_name()
        if not ocr_available() or eng == "none":
            return {
                "success": False,
                "error": "OCR 引擎不可用（未安装 PaddleOCR/Tesseract）",
                "texts": [],
                "blocks": [],
                "ocr_engine": eng,
                "suggestion": (
                    "请确认已安装系统 Tesseract（或 paddleocr/ddddocr）后重启服务；"
                    "微信场景可先 windows_press_key('Ctrl+F') 打开搜索，再 windows_type_text。"
                ),
            }

        png, cap_meta = capture_for_observation(region_dict, prefer_foreground=True)
        if not png:
            return {
                "success": False,
                "error": "截屏失败",
                "texts": [],
                "blocks": [],
                "ocr_engine": eng,
                "suggestion": "请确认本机可访问主显示器（mss）。",
            }
        img_hash = _image_hash(png)
        with _cache_lock:
            cached_hash = _ocr_cache.get("hash") or ""
            sim = _hash_similarity(img_hash, cached_hash)
            if sim >= _HASH_SIMILARITY_THRESHOLD and _ocr_cache.get("blocks") is not None:
                blocks = list(_ocr_cache["blocks"])
                texts = list(_ocr_cache.get("texts") or [])
                return {
                    "success": True,
                    "cached": True,
                    "similarity": round(sim, 4),
                    "texts": texts,
                    "blocks": blocks,
                    "ocr_engine": eng,
                    "capture": cap_meta,
                    "region_hint": region_hint or None,
                }

        blocks = _ocr_blocks_uncached(png)
        texts = [b["text"] for b in blocks]
        with _cache_lock:
            _ocr_cache["hash"] = img_hash
            _ocr_cache["blocks"] = blocks
            _ocr_cache["texts"] = texts
            _ocr_cache["ts"] = time.time()
        out: Dict[str, Any] = {
            "success": True,
            "cached": False,
            "texts": texts,
            "blocks": blocks,
            "ocr_engine": eng,
            "capture": cap_meta,
            "region_hint": region_hint or None,
        }
        if not texts:
            out["warning"] = "OCR 未识别到置信文字；可改用 get_screen_description 或微信 Ctrl+F 搜索捷径"
        return out

    try:
        return _run_with_timeout(_work)
    except TimeoutError as e:
        return {
            "success": False,
            "error": str(e),
            "texts": [],
            "blocks": [],
            "suggestion": "可缩小 region 后重试，或稍后再次调用 get_screen_text。",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)[:300],
            "texts": [],
            "blocks": [],
            "suggestion": "检查 OCR 引擎（PaddleOCR/Tesseract）是否可用。",
        }


def get_screen_description(hint: str = "") -> Dict[str, Any]:
    """多模态视觉描述，硬截断 ≤300 字。"""

    def _work() -> Dict[str, Any]:
        png, cap_meta = capture_for_observation(prefer_foreground=True)
        if not png:
            return {
                "success": False,
                "error": "截屏失败",
                "description": "",
                "suggestion": "请确认本机可访问主显示器。",
            }
        focus = (hint or "").strip() or "当前聚焦的窗口和其中的按钮、输入框"
        win_hint = ""
        if isinstance(cap_meta, dict) and cap_meta.get("title"):
            win_hint = f"（截图来自前台窗口「{cap_meta.get('title')}」）"
        instruction = (
            "你是桌面 UI 观察助手。根据截图用中文给出结构化短描述（严格不超过 280 字）。\n"
            f"关注点: {focus}{win_hint}\n"
            "必须包含：1) 前台窗口标题 2) 焦点/选中元素 3) 可见按钮与输入框 "
            "4) 异常弹窗（若有）。\n"
            "禁止描述桌面壁纸、任务栏无关图标、背景装饰、Testory 界面。不要罗列无关数字。\n"
            "格式示例：\n窗口: ...\n焦点: ...\n可操作: ...\n异常: 无/..."
        )
        from ai_vision_local import vision_describe

        raw = vision_describe(png, instruction) or ""
        raw = filter_privacy(raw).strip()
        if len(raw) > _DESC_MAX_CHARS:
            raw = raw[: _DESC_MAX_CHARS - 1] + "…"
        return {
            "success": bool(raw),
            "description": raw,
            "hint": focus,
            "capture": cap_meta,
            "error": "" if raw else "视觉模型未返回描述",
        }

    try:
        return _run_with_timeout(_work, timeout=_DESC_TIMEOUT_SEC)
    except TimeoutError as e:
        return {
            "success": False,
            "error": str(e),
            "description": "",
            "suggestion": "视觉模型响应慢，可改用 get_screen_text 或稍后重试。",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)[:300],
            "description": "",
            "suggestion": "检查云端/本地视觉模型配置。",
        }



def wait_screen_stable(
    *,
    timeout_ms: int = 3000,
    poll_ms: int = 200,
    change_ratio_threshold: float = 0.02,
) -> Dict[str, Any]:
    """监测主屏像素变化率，连续两帧低于阈值则视为稳定。"""

    def _pixel_sample(png: bytes) -> bytes:
        # 下采样哈希用
        return png[::32]

    try:
        deadline = time.time() + max(0.2, timeout_ms / 1000.0)
        prev: Optional[bytes] = None
        stable_hits = 0
        while time.time() < deadline:
            png = capture_primary_monitor_png()
            if not png:
                time.sleep(poll_ms / 1000.0)
                continue
            sample = _pixel_sample(png)
            if prev is not None:
                n = min(len(prev), len(sample), 8000)
                if n > 0:
                    diff = sum(1 for i in range(n) if prev[i] != sample[i]) / float(n)
                    if diff <= change_ratio_threshold:
                        stable_hits += 1
                        if stable_hits >= 2:
                            return {"success": True, "stable": True, "change_ratio": round(diff, 4)}
                    else:
                        stable_hits = 0
            prev = sample
            time.sleep(max(0.05, poll_ms / 1000.0))
        return {"success": True, "stable": False, "error": "等待界面稳定超时", "suggestion": "可增大 duration_ms 或忽略后继续操作"}
    except Exception as e:
        return {"success": False, "stable": False, "error": str(e)[:200]}


def clear_ocr_cache() -> None:
    with _cache_lock:
        _ocr_cache["hash"] = ""
        _ocr_cache["blocks"] = []
        _ocr_cache["texts"] = []
        _ocr_cache["ts"] = 0.0
