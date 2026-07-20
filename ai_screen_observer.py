# -*- coding: utf-8 -*-
"""
屏幕观察者：支持异步/同步视觉分析、目标表面截图、变化检测、隐私过滤。
结构化感知（DOM/UIA）之外的「眼睛」通道；结果应注入 Hermes，而非仅平台 outer loop。
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Callable, Optional

from logger import uat_logger


class ScreenObserver:
    """屏幕观察者：封装截图 -> 变化检测 -> 隐私过滤 -> 视觉分析。"""

    _PRIVACY_PATTERNS = [
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
        (re.compile(r"\b1[3-9]\d{9}\b"), "[PHONE]"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
        (re.compile(r"\b\d{17}[\dXx]|\d{15}\b"), "[ID]"),
    ]

    def __init__(self, platform_type: str = "web", interval_sec: int = 3):
        self.platform_type = (platform_type or "web").strip().lower()
        self.interval_sec = interval_sec
        self._last_capture_time = 0.0
        self._last_analysis = ""
        self._last_image_hash = ""
        self._pending_result: Optional[str] = None
        self._lock = threading.Lock()
        self._capture_count = 0
        # desktop|web|"" — 覆盖 auto 时的截图优先级
        self.prefer_surface: str = ""
        # 共享屏幕：必须整屏，禁止截前台窗（否则常截到 Testory 自身）
        self.full_desktop: bool = False

    def set_prefer_surface(self, surface: str) -> None:
        self.prefer_surface = (surface or "").strip().lower()

    def set_full_desktop(self, enabled: bool = True) -> None:
        self.full_desktop = bool(enabled)

    def should_capture(self) -> bool:
        return time.time() - self._last_capture_time >= self.interval_sec

    def _image_hash(self, png_bytes: bytes) -> str:
        return hashlib.md5(png_bytes[::16]).hexdigest()

    def _has_significant_change(self, png_bytes: bytes, *, force: bool = False) -> bool:
        current_hash = self._image_hash(png_bytes)
        if not force and current_hash == self._last_image_hash:
            return False
        self._last_image_hash = current_hash
        return True

    def _filter_privacy(self, text: str) -> str:
        for pattern, replacement in self._PRIVACY_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def _capture_web_png(self) -> Optional[bytes]:
        # 目标表面：活动 CDP / bridge 页面，避免整屏乱拍
        try:
            from web_capture.cdp_browser import get_active_page

            _pw_page = get_active_page()
            if _pw_page:
                return _pw_page.screenshot(type="png")
        except Exception:
            pass
        try:
            from ai_external_browser_bridge import capture_screenshot

            png = capture_screenshot()
            if png:
                return png
        except Exception:
            pass
        return None

    def _is_testory_hwnd(self, hwnd: int) -> bool:
        """避免共享屏幕时把自己的窗口当成「桌面画面」。"""
        if not hwnd:
            return False
        try:
            import ctypes

            user32 = ctypes.windll.user32
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(int(hwnd), buf, 512)
            title = (buf.value or "").lower()
            markers = (
                "testory",
                "ai 自动化测试",
                "自动化测试平台",
                "ai test",
                "newuitestplatform",
            )
            return any(m in title for m in markers)
        except Exception:
            return False

    def _capture_full_monitor_png(self) -> Optional[bytes]:
        """主显示器整屏（共享屏幕专用）。"""
        try:
            import mss
            from mss.tools import to_png

            with mss.mss() as sct:
                # monitors[0]=虚拟全屏；[1]=主显示器
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                return to_png(shot.rgb, shot.size)
        except Exception as e:
            uat_logger.warning("ScreenObserver full monitor capture failed: %s", e)
            return None

    def _capture_desktop_png(self) -> Optional[bytes]:
        # 共享屏幕 / full_desktop：必须整屏，绝不能截 Testory 前台窗
        if self.full_desktop or self.prefer_surface == "desktop":
            png = self._capture_full_monitor_png()
            if png:
                return png

        # 非共享场景：可截前台窗，但跳过 Testory 自身
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd and not self._is_testory_hwnd(hwnd):
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
                    w, h = max(0, right - left), max(0, bottom - top)
                    if w > 80 and h > 80:
                        import mss
                        from mss.tools import to_png

                        with mss.mss() as sct:
                            mon = {"left": left, "top": top, "width": w, "height": h}
                            shot = sct.grab(mon)
                            return to_png(shot.rgb, shot.size)
        except Exception as e:
            uat_logger.debug("ScreenObserver foreground window capture failed: %s", e)
        return self._capture_full_monitor_png()

    def _capture_mobile_png(self) -> Optional[bytes]:
        try:
            from mobile_device_manager import get_connected_udid
            import subprocess
            import tempfile
            import os

            udid = get_connected_udid()
            if not udid:
                return None
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                path = f.name
            try:
                r = subprocess.run(
                    ["adb", "-s", udid, "exec-out", "screencap", "-p"],
                    check=True,
                    capture_output=True,
                    timeout=8,
                )
                if r.stdout:
                    return bytes(r.stdout)
            finally:
                try:
                    os.unlink(path)
                except Exception:
                    pass
        except Exception as e:
            uat_logger.debug("ScreenObserver mobile capture failed: %s", e)
        return None

    def _grab_png(self) -> Optional[bytes]:
        plat = self.platform_type
        # 共享屏幕开启时强制整桌面，不受 web/auto 影响
        if self.full_desktop:
            return self._capture_full_monitor_png()
        if plat in ("web",):
            png = self._capture_web_png()
            return png or self._capture_desktop_png()
        if plat in ("android", "mobile"):
            return self._capture_mobile_png() or self._capture_desktop_png()
        if plat in ("desktop", "auto"):
            if self.prefer_surface == "desktop" or plat == "desktop":
                return self._capture_desktop_png()
            png = self._capture_web_png()
            return png or self._capture_desktop_png()
        return self._capture_desktop_png()

    def capture_and_analyze_async(
        self,
        instruction_hint: str = "",
        on_result: Optional[Callable[[str], Any]] = None,
    ) -> None:
        def _do():
            result = self._do_capture_and_analyze(instruction_hint, force=False)
            with self._lock:
                self._pending_result = result
            if on_result:
                try:
                    on_result(result)
                except Exception:
                    pass

        threading.Thread(target=_do, daemon=True).start()

    def capture_and_analyze_sync(self, instruction_hint: str = "", *, force: bool = True) -> str:
        """同步截图分析（动作后门闩用）。"""
        return self._do_capture_and_analyze(instruction_hint, force=force)

    def _do_capture_and_analyze(self, instruction_hint: str = "", *, force: bool = False) -> str:
        png = self._grab_png()
        if not png:
            return ""

        if not self._has_significant_change(png, force=force):
            return ""

        hint = instruction_hint or (
            "Analyze this screenshot for UI testing purposes. "
            "Reply in Chinese with STRICT format (max 300 chars):\n"
            "活跃窗口/页面: <title>\n"
            "关键UI元素: <list of visible interactive elements>\n"
            "异常/弹窗: <any popup, error, or unexpected state>\n"
            "与上一帧变化: <what changed compared to previous state, or '无变化'>"
        )

        if self._last_analysis and not force:
            hint += (
                "\n\n【差异模式】上一帧分析结果如下，请仅描述与上一帧相比发生的变化，"
                "无变化则直接回复'无变化'，控制在150字以内：\n"
                f"上一帧: {self._last_analysis[:200]}"
            )

        from ai_vision_local import vision_describe

        try:
            result = vision_describe(png, hint)
            result = self._filter_privacy(result)
            self._last_analysis = result
            self._last_capture_time = time.time()
            self._capture_count += 1
            return result
        except Exception as e:
            uat_logger.warning("ScreenObserver vision analysis failed: %s", e)
            return ""

    def pop_pending_result(self) -> Optional[str]:
        with self._lock:
            result = self._pending_result
            self._pending_result = None
            return result

    def get_last_analysis(self) -> str:
        return self._last_analysis

    def get_capture_count(self) -> int:
        return self._capture_count
