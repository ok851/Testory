# -*- coding: utf-8 -*-
"""屏幕观察者 V2：实时屏幕观察 + 按需视觉分析。

替代旧版 ai_screen_observer.py 的被动且失效的实现。
支持三种模式：被动、主动（共享屏幕）、事件驱动（工具失败时）。
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from logger import uat_logger


class ScreenObserverV2:
    """实时屏幕观察者。

    工作模式:
    1. 被动模式 (默认): 仅在 get_latest_analysis() 被调用时触发截图
    2. 主动模式 (共享屏幕开启): 每 interval_sec 秒自动截图 + 轻量 OCR
    3. 事件驱动模式: on_tool_failure() 立即截图分析
    """

    def __init__(self, interval_sec: float = 3.0, enable_vlm: bool = False):
        self._interval = max(1.0, float(interval_sec))
        self._enable_vlm = enable_vlm
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_frame: Optional[bytes] = None
        self._last_frame_hash: str = ""
        self._last_analysis: Optional[Dict[str, Any]] = None
        self._frame_count: int = 0
        self._observers: List[Callable[[Dict[str, Any]], None]] = []
        self._mode: str = "passive"  # passive / active / event_driven
        self._consecutive_failures: int = 0

    def start(self, mode: str = "active") -> None:
        """启动后台屏幕观察线程。"""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._mode = mode
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._observe_loop, daemon=True, name="screen_observer_v2")
            self._thread.start()
            uat_logger.info("ScreenObserverV2 started in '%s' mode, interval=%.1fs", mode, self._interval)

    def stop(self) -> None:
        """停止后台屏幕观察。"""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            self._mode = "passive"
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        uat_logger.info("ScreenObserverV2 stopped (frames captured: %d)", self._frame_count)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def mode(self) -> str:
        return self._mode

    def get_latest_frame(self) -> Optional[bytes]:
        """获取最新截图（字节）。"""
        with self._lock:
            return self._last_frame

    def get_latest_analysis(self, force_refresh: bool = False) -> Dict[str, Any]:
        """获取最新分析结果（OCR + 可选 VLM）。

        Args:
            force_refresh: 强制重新截图分析，忽略缓存

        Returns:
            分析结果字典，包含:
                texts: OCR 识别的文本列表
                blocks: OCR 块信息
                frame_hash: 帧哈希
                timestamp: 时间戳
                source: 分析来源
        """
        if not force_refresh:
            with self._lock:
                if self._last_analysis:
                    age = time.time() - self._last_analysis.get("timestamp", 0)
                    if age < self._interval * 2:
                        return self._last_analysis
        return self._capture_and_analyze()

    def on_tool_failure(self, tool_name: str, error: str) -> Dict[str, Any]:
        """工具失败时立即触发屏幕分析，返回给 Agent 作为上下文。"""
        self._consecutive_failures += 1
        analysis = self._capture_and_analyze()
        failure_context = {
            "tool_name": tool_name,
            "error": error,
            "failure_count": self._consecutive_failures,
            **analysis,
        }
        uat_logger.warning(
            "tool_failure[%s]: %s (fail#%d, frame_hash=%s)",
            tool_name, error[:100], self._consecutive_failures,
            analysis.get("frame_hash", "")[:16],
        )
        if self._enable_vlm and self._consecutive_failures >= 2:
            try:
                vlm_frame = self._last_frame
                if not vlm_frame:
                    vlm_frame = analysis.get("frame_bytes", b"")
                if vlm_frame:
                    vlm_result = self._vlm_analyze(vlm_frame, tool_name, error)
                    if vlm_result:
                        failure_context["vlm_analysis"] = vlm_result
            except Exception as e:
                uat_logger.debug("vlm failure analysis error: %s", e)
        self._notify_observers(failure_context)
        self._trim_frame_cache()
        return failure_context

    def on_tool_success(self) -> None:
        """工具成功时重置连续失败计数。"""
        self._consecutive_failures = 0

    def add_observer(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """添加状态变更观察者。"""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def _observe_loop(self) -> None:
        """后台观察循环。"""
        while not self._stop_event.is_set():
            try:
                frame_hash = self._capture_and_store()
                if frame_hash:
                    self._frame_count += 1
                    if self._frame_count % 10 == 0:
                        uat_logger.debug("screen_observer captured %d frames", self._frame_count)
            except Exception as e:
                uat_logger.debug("screen_observe_loop error: %s", e)
            self._stop_event.wait(self._interval)

    def _capture_and_store(self) -> str:
        """捕获并存储最新帧。返回帧哈希。"""
        try:
            from screen_tools import capture_for_observation
            png, meta = capture_for_observation(prefer_foreground=True, ensure_valid=True)
            if png:
                if len(png) > 4 * 1024 * 1024:
                    png = png[:4 * 1024 * 1024]
                frame_hash = hashlib.md5(png[::16]).hexdigest()
                with self._lock:
                    self._last_frame = png
                    self._last_frame_hash = frame_hash
                return frame_hash
        except Exception as e:
            uat_logger.debug("screen capture in observer failed: %s", e)
        return ""

    def _trim_frame_cache(self) -> None:
        """防止截图缓存膨胀：限制帧大小和分析结果大小。"""
        with self._lock:
            if self._last_frame and len(self._last_frame) > 4 * 1024 * 1024:
                self._last_frame = self._last_frame[:4 * 1024 * 1024]
            if self._last_analysis and isinstance(self._last_analysis, dict):
                if len(self._last_analysis.get("frame_bytes", b"")) > 4 * 1024 * 1024:
                    self._last_analysis["frame_bytes"] = self._last_analysis["frame_bytes"][:4 * 1024 * 1024]

    def _capture_and_analyze(self) -> Dict[str, Any]:
        """截图 + OCR 分析。"""
        frame_bytes = None
        frame_hash = ""
        meta: Dict[str, Any] = {}
        try:
            from screen_tools import capture_for_observation
            png, meta = capture_for_observation(prefer_foreground=True, ensure_valid=True)
            if png:
                if len(png) > 4 * 1024 * 1024:
                    png = png[:4 * 1024 * 1024]
                frame_bytes = png
                frame_hash = hashlib.md5(png[::16]).hexdigest()
                with self._lock:
                    self._last_frame = png
                    self._last_frame_hash = frame_hash
        except Exception as e:
            uat_logger.warning("screen capture for analysis failed: %s", e)
            return {"texts": [], "blocks": [], "frame_hash": "", "timestamp": time.time(),
                    "error": str(e), "source": "capture_failed"}
        texts: List[Dict[str, Any]] = []
        blocks: List[Dict[str, Any]] = []
        try:
            from desktop_ocr import get_ocr_engine
            engine = get_ocr_engine()
            if engine and frame_bytes:
                ocr_result = engine.recognize_bytes(frame_bytes)
                texts = ocr_result.get("texts", [])
                blocks = ocr_result.get("blocks", [])
        except ImportError:
            uat_logger.debug("desktop_ocr not available for screen analysis")
        except Exception as e:
            uat_logger.debug("OCR analysis failed: %s", e)
        analysis = {
            "texts": texts,
            "blocks": blocks,
            "frame_hash": frame_hash,
            "timestamp": time.time(),
            "source": "screen_observer_v2",
            "window_title": meta.get("window_title", ""),
            "frame_bytes": frame_bytes or b"",
            "frame_meta": meta,
        }
        with self._lock:
            self._last_analysis = analysis
        return analysis

    def _vlm_analyze(self, frame_bytes: bytes, tool_name: str, error: str) -> Optional[Dict[str, Any]]:
        """在连续失败时调用 VLM 分析屏幕状态。"""
        if not frame_bytes:
            return None
        if not self._enable_vlm:
            return None
        try:
            from vlm_grounding import get_vlm
            vlm = get_vlm()
            if not vlm.is_available():
                return None
            prompt = (
                f"The tool '{tool_name}' failed with error: {error[:200]}. "
                f"Look at this screenshot and tell me what's on screen, "
                f"what might have gone wrong, and what element I should try interacting with instead."
            )
            return vlm.analyze_screen(frame_bytes, prompt)
        except Exception as e:
            uat_logger.debug("vlm analysis error: %s", e)
            return None

    def _notify_observers(self, event: Dict[str, Any]) -> None:
        for cb in self._observers:
            try:
                cb(event)
            except Exception as e:
                uat_logger.debug("observer callback error: %s", e)


_default_observer: Optional[ScreenObserverV2] = None


def get_screen_observer() -> ScreenObserverV2:
    global _default_observer
    if _default_observer is None:
        _default_observer = ScreenObserverV2()
    return _default_observer


def start_screen_sharing(interval_sec: float = 3.0, enable_vlm: bool = False) -> ScreenObserverV2:
    """开启共享屏幕：创建并启动主动模式的观察者。"""
    global _default_observer
    if _default_observer is None:
        _default_observer = ScreenObserverV2(interval_sec=interval_sec, enable_vlm=enable_vlm)
    _default_observer._interval = interval_sec
    _default_observer._enable_vlm = enable_vlm
    _default_observer.start(mode="active")
    return _default_observer


def stop_screen_sharing() -> None:
    """关闭共享屏幕。"""
    global _default_observer
    if _default_observer:
        _default_observer.stop()


def get_screen_sharing_status() -> Dict[str, Any]:
    """获取共享屏幕状态。"""
    obs = get_screen_observer()
    return {
        "active": obs.is_running,
        "mode": obs.mode,
        "interval": obs._interval,
        "frames_captured": obs._frame_count,
        "last_frame_hash": obs._last_frame_hash[:16] if obs._last_frame_hash else "",
        "has_latest_frame": obs._last_frame is not None,
        "consecutive_failures": obs._consecutive_failures,
    }
