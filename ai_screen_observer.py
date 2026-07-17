"""
屏幕观察者：支持异步视觉分析、变化检测、隐私过滤。
在 AI 工具循环中按策略截取屏幕并调用多模态模型分析，
让 Agent 能够"看到"当前屏幕状态。
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any, Callable, Optional

from logger import uat_logger


class ScreenObserver:
    """屏幕观察者：封装截图 -> 变化检测 -> 隐私过滤 -> 异步视觉分析 -> 差异摘要全链路。"""

    # 隐私信息正则过滤模式（邮箱、手机号、IP、身份证号）
    _PRIVACY_PATTERNS = [
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
        (re.compile(r"\b1[3-9]\d{9}\b"), "[PHONE]"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
        (re.compile(r"\b\d{17}[\dXx]|\d{15}\b"), "[ID]"),
    ]

    def __init__(self, platform_type: str = "web", interval_sec: int = 3):
        self.platform_type = platform_type
        self.interval_sec = interval_sec
        self._last_capture_time = 0.0
        self._last_analysis = ""
        self._last_image_hash = ""
        self._pending_result: Optional[str] = None
        self._lock = threading.Lock()
        self._capture_count = 0

    def should_capture(self) -> bool:
        return time.time() - self._last_capture_time >= self.interval_sec

    def _image_hash(self, png_bytes: bytes) -> str:
        """计算图片采样哈希用于快速变化检测。"""
        return hashlib.md5(png_bytes[::16]).hexdigest()

    def _has_significant_change(self, png_bytes: bytes) -> bool:
        """检测画面是否发生显著变化。"""
        current_hash = self._image_hash(png_bytes)
        if current_hash == self._last_image_hash:
            return False
        self._last_image_hash = current_hash
        return True

    def _filter_privacy(self, text: str) -> str:
        """过滤敏感信息。"""
        for pattern, replacement in self._PRIVACY_PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def capture_and_analyze_async(
        self,
        instruction_hint: str = "",
        on_result: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """异步截图并分析，不阻塞调用线程。"""

        def _do():
            result = self._do_capture_and_analyze(instruction_hint)
            with self._lock:
                self._pending_result = result
            if on_result:
                try:
                    on_result(result)
                except Exception:
                    pass

        threading.Thread(target=_do, daemon=True).start()

    def _do_capture_and_analyze(self, instruction_hint: str = "") -> str:
        """同步执行截图 + 分析。"""
        png = None
        if self.platform_type == "web":
            from ai_external_browser_bridge import capture_screenshot

            png = capture_screenshot()
        else:
            try:
                import mss
                from mss.tools import to_png

                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    shot = sct.grab(monitor)
                    png = to_png(shot.rgb, shot.size)
            except Exception as e:
                uat_logger.warning("ScreenObserver desktop screenshot failed: %s", e)

        if not png:
            return ""

        # 变化检测：画面未变则跳过分析
        if not self._has_significant_change(png):
            return ""

        # 构建结构化视觉分析指令（控制输出长度和格式）
        hint = instruction_hint or (
            "Analyze this screenshot for UI testing purposes. "
            "Reply in Chinese with STRICT format (max 300 chars):\n"
            "活跃窗口/页面: <title>\n"
            "关键UI元素: <list of visible interactive elements>\n"
            "异常/弹窗: <any popup, error, or unexpected state>\n"
            "与上一帧变化: <what changed compared to previous state, or '无变化'>"
        )

        # 差异对比：如果已有上一帧分析，要求模型只输出变化部分
        if self._last_analysis:
            hint += (
                "\n\n【差异模式】上一帧分析结果如下，请仅描述与上一帧相比发生的变化，"
                "无变化则直接回复'无变化'，控制在150字以内：\n"
                f"上一帧: {self._last_analysis[:200]}"
            )

        # 调用 vision_describe（自动支持本地 Ollama / 云端）
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
        """取出待处理的分析结果（非阻塞）。"""
        with self._lock:
            result = self._pending_result
            self._pending_result = None
            return result

    def get_last_analysis(self) -> str:
        return self._last_analysis

    def get_capture_count(self) -> int:
        return self._capture_count
