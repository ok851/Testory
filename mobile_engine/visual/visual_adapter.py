# -*- coding: utf-8 -*-
"""
视觉兜底适配器。

纯截图操作引擎 — 用于 Maestro 无法处理的场景:
- 游戏引擎 (Unity/Unreal) 自定义控件
- Canvas 绘制界面
- 无无障碍服务的定制 ROM 应用

复用现有基础设施:
- mobile_image_engine.py (OpenCV ORB/template matching)
- mobile_agent_client.py (AI Vision via Gateway)
- mobile_adb_control.py (ADB 坐标点击/滑动)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import (
    DeviceInfo,
    EngineType,
    FlowResult,
    FlowStep,
    LocatorInfo,
    LocatorStrategy,
    MobileTestEngine,
    StepResult,
    StepStatus,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class VisualFallbackAdapter(MobileTestEngine):
    """视觉兜底引擎 — 基于截图的纯视觉操作"""

    def __init__(self):
        super().__init__()
        self._screenshots: List[bytes] = []

    # ========================================================================
    # 元信息
    # ========================================================================

    @property
    def engine_type(self) -> EngineType:
        return EngineType.VISUAL_FALLBACK

    @property
    def engine_version(self) -> str:
        return "1.0.0"

    # ========================================================================
    # 设备管理
    # ========================================================================

    def connect_device(self, device: DeviceInfo) -> bool:
        self._device = device
        uat_logger.info("视觉引擎设备就绪: %s", device.udid)
        return True

    def disconnect_device(self) -> None:
        self._device = None
        self._screenshots.clear()

    def check_device_readiness(self) -> Dict[str, Any]:
        if not self._device:
            return {"all_passed": False, "errors": ["未连接设备"], "warnings": [], "checks": []}
        return {"all_passed": True, "checks": [], "warnings": [], "errors": []}

    def install_app(self, app_path: str) -> bool:
        return self._adb_install(app_path)

    def uninstall_app(self, package_name: str) -> bool:
        return self._adb_uninstall(package_name)

    def launch_app(self, package_name: str, activity: str = "") -> bool:
        return self._adb_launch(package_name, activity)

    def stop_app(self, package_name: str) -> bool:
        return self._adb_stop(package_name)

    def capture_screenshot(self) -> bytes:
        png = self._adb_screencap()
        if png:
            self._screenshots.append(png)
        return png or b""

    def capture_screenshot_to_file(self, output_path: str) -> str:
        import io
        from pathlib import Path

        png = self.capture_screenshot()
        Path(output_path).write_bytes(png)
        return output_path

    # ========================================================================
    # 测试流执行
    # ========================================================================

    def execute_flow(self, flow: List[FlowStep]) -> FlowResult:
        results: List[StepResult] = []
        for step in flow:
            result = self.execute_step(step)
            results.append(result)
            if result.is_failed:
                break

        return FlowResult(
            steps=results,
            total_duration_ms=sum(r.duration_ms for r in results),
            passed_count=sum(1 for r in results if r.is_success),
            failed_count=sum(1 for r in results if r.is_failed),
        )

    def execute_step(self, step: FlowStep) -> StepResult:
        started = time.time()
        try:
            action = step.action.strip().lower()
            if action == "tap":
                result = self._do_tap(step)
            elif action in ("input", "input_text"):
                result = self._do_input(step)
            elif action == "swipe":
                result = self._do_swipe(step)
            elif action == "scroll":
                result = self._do_swipe(step)  # scroll ≈ swipe
            elif action == "launch_app":
                ok = self.launch_app(step.input_value)
                result = StepResult(
                    status=StepStatus.SUCCESS if ok else StepStatus.FAILED,
                    action=action, error="" if ok else "启动失败",
                )
            elif action == "stop_app":
                ok = self.stop_app(step.input_value)
                result = StepResult(
                    status=StepStatus.SUCCESS if ok else StepStatus.FAILED,
                    action=action,
                )
            elif action == "back":
                self._adb_back()
                result = StepResult(status=StepStatus.SUCCESS, action=action)
            elif action == "screenshot":
                png = self.capture_screenshot()
                result = StepResult(
                    status=StepStatus.SUCCESS if png else StepStatus.FAILED,
                    action=action,
                )
            elif action == "wait":
                ms = step.wait_timeout_ms or 1000
                time.sleep(ms / 1000.0)
                result = StepResult(status=StepStatus.SUCCESS, action=action)
            elif action == "assert":
                result = self._do_assert(step)
            else:
                result = StepResult(
                    status=StepStatus.FAILED,
                    action=action,
                    error=f"视觉引擎不支持的动作: {action}",
                )
        except Exception as exc:
            result = StepResult(
                status=StepStatus.FAILED,
                action=step.action,
                error=str(exc),
            )

        result.duration_ms = (time.time() - started) * 1000
        result.action = step.action
        result.description = step.description or step.action
        return result

    def resume_flow(self, flow: List[FlowStep], from_index: int = 0) -> FlowResult:
        return self.execute_flow(flow[from_index:])

    # ========================================================================
    # 原子交互
    # ========================================================================

    def tap(self, locator: LocatorInfo) -> StepResult:
        step = FlowStep(action="tap", locator=locator)
        return self.execute_step(step)

    def tap_coordinates(self, x: int, y: int) -> StepResult:
        self._adb_tap(x, y)
        return StepResult(status=StepStatus.SUCCESS, action="tap")

    def input_text(self, locator: LocatorInfo, text: str) -> StepResult:
        # 视觉引擎: 先点击定位元素, 再 adb input text
        if locator:
            pt = self._find_visual(locator)
            if pt:
                self._adb_tap(pt[0], pt[1])
        self._adb_input_text(text)
        return StepResult(status=StepStatus.SUCCESS, action="input")

    def swipe(self, direction: str, duration_ms: int = 400) -> StepResult:
        self._adb_swipe(direction, duration_ms)
        return StepResult(status=StepStatus.SUCCESS, action="swipe")

    def assert_element(self, locator: LocatorInfo, condition: str) -> StepResult:
        pt = self._find_visual(locator)
        if condition == "visible" and pt:
            return StepResult(status=StepStatus.SUCCESS, action="assert", match_confidence=pt[2])
        if condition == "not_visible" and not pt:
            return StepResult(status=StepStatus.SUCCESS, action="assert")
        return StepResult(status=StepStatus.FAILED, action="assert", error="断言失败")

    def press_back(self) -> StepResult:
        self._adb_back()
        return StepResult(status=StepStatus.SUCCESS, action="back")

    # ========================================================================
    # 报告
    # ========================================================================

    def get_structured_log(self) -> Dict[str, Any]:
        return {
            "engine": "visual_fallback",
            "screenshots_count": len(self._screenshots),
        }

    # ========================================================================
    # 核心视觉匹配
    # ========================================================================

    def find_element_visual(self, locator: LocatorInfo) -> Tuple[int, int, float]:
        """
        从截图中匹配元素 → (x, y, confidence)

        优先使用 AI Vision (mobile_agent_client), 回退 OpenCV。
        """
        png = self.capture_screenshot()
        if not png:
            return (0, 0, 0.0)

        conf_threshold = 0.75

        # 策略1: AI Vision (mobile_agent_client)
        if locator.strategy in (LocatorStrategy.SEMANTIC, LocatorStrategy.VISUAL):
            ai_result = self._ai_vision_match(png, locator)
            if ai_result and ai_result[2] >= conf_threshold:
                return ai_result

        # 策略2: OpenCV 模板匹配 (mobile_image_engine)
        if locator.visual_template_path:
            cv_result = self._opencv_match(png, locator)
            if cv_result and cv_result[2] >= conf_threshold:
                return cv_result

        return (0, 0, 0.0)

    def _find_visual(self, locator: LocatorInfo) -> Optional[Tuple[int, int, float]]:
        """内部便捷方法"""
        x, y, conf = self.find_element_visual(locator)
        if conf > 0:
            return (x, y, conf)
        return None

    # ========================================================================
    # OpenCV 模板匹配 (复用 mobile_image_engine)
    # ========================================================================

    def _opencv_match(self, png: bytes,
                      locator: LocatorInfo) -> Optional[Tuple[int, int, float]]:
        """OpenCV 模板匹配"""
        try:
            from mobile_image_engine import resolve_tap_point_on_screen

            x, y, score = resolve_tap_point_on_screen(
                png, locator.visual_template_path or locator.value,
            )
            return (x, y, score)
        except ImportError:
            uat_logger.warning("mobile_image_engine 未安装, OpenCV 匹配不可用")
            return None
        except Exception as exc:
            uat_logger.debug("OpenCV 匹配失败: %s", exc)
            return None

    # ========================================================================
    # AI Vision 匹配 (复用 mobile_agent_client)
    # ========================================================================

    def _ai_vision_match(self, png: bytes,
                         locator: LocatorInfo) -> Optional[Tuple[int, int, float]]:
        """通过 Mobile Agent Gateway AI Vision 匹配"""
        try:
            from mobile_agent_client import mobile_agent_enabled, agent_vision_query

            if not mobile_agent_enabled():
                return None

            desc = locator.semantic_desc or locator.value
            result = agent_vision_query(png, desc)
            if result and result.get("found"):
                x = int(result.get("x", 0))
                y = int(result.get("y", 0))
                conf = float(result.get("confidence", 0.8))
                return (x, y, conf)
            return None
        except ImportError:
            return None
        except Exception as exc:
            uat_logger.debug("AI Vision 匹配失败: %s", exc)
            return None

    # ========================================================================
    # 动作实现 (基于 ADB)
    # ========================================================================

    def _do_tap(self, step: FlowStep) -> StepResult:
        if step.tap_x is not None and step.tap_y is not None:
            self._adb_tap(step.tap_x, step.tap_y)
            return StepResult(status=StepStatus.SUCCESS, action="tap")

        if step.locator:
            pt = self._find_visual(step.locator)
            if pt:
                self._adb_tap(pt[0], pt[1])
                return StepResult(
                    status=StepStatus.SUCCESS, action="tap",
                    match_confidence=pt[2],
                    locator_used=step.locator,
                )

        return StepResult(status=StepStatus.FAILED, action="tap",
                          error="视觉匹配失败，未找到目标元素")

    def _do_input(self, step: FlowStep) -> StepResult:
        text = step.input_value or ""
        # 点击目标输入框
        if step.locator:
            pt = self._find_visual(step.locator)
            if pt:
                self._adb_tap(pt[0], pt[1])
                time.sleep(0.2)
        # 输入文本
        self._adb_input_text(text)
        return StepResult(status=StepStatus.SUCCESS, action="input")

    def _do_swipe(self, step: FlowStep) -> StepResult:
        direction = step.swipe_direction or "up"
        duration = step.swipe_duration_ms or 400
        self._adb_swipe(direction, duration)
        return StepResult(status=StepStatus.SUCCESS, action="swipe")

    def _do_assert(self, step: FlowStep) -> StepResult:
        if not step.locator:
            return StepResult(status=StepStatus.FAILED, action="assert",
                              error="缺少定位符")
        condition = step.assert_type or "visible"
        pt = self._find_visual(step.locator)
        if condition in ("visible", "contains_text"):
            if pt:
                return StepResult(status=StepStatus.SUCCESS, action="assert",
                                  match_confidence=pt[2])
            return StepResult(status=StepStatus.FAILED, action="assert",
                              error="目标不可见")
        elif condition == "not_visible":
            if not pt:
                return StepResult(status=StepStatus.SUCCESS, action="assert")
            return StepResult(status=StepStatus.FAILED, action="assert",
                              error="目标仍然可见")
        return StepResult(status=StepStatus.FAILED, action="assert",
                          error=f"未知断言类型: {condition}")

    # ========================================================================
    # ADB 底层操作
    # ========================================================================

    def _get_udid(self) -> str:
        return self._device.udid if self._device else ""

    def _adb_tap(self, x: int, y: int) -> None:
        import subprocess

        args = ["adb"]
        udid = self._get_udid()
        if udid:
            args.extend(["-s", udid])
        args.extend(["shell", "input", "tap", str(x), str(y)])
        subprocess.run(args, timeout=10, check=False)

    def _adb_input_text(self, text: str) -> None:
        import subprocess

        safe = text.replace(" ", "%s").replace("&", "\\&")
        args = ["adb"]
        udid = self._get_udid()
        if udid:
            args.extend(["-s", udid])
        args.extend(["shell", "input", "text", safe])
        subprocess.run(args, timeout=10, check=False)

    def _adb_swipe(self, direction: str, duration_ms: int = 400) -> None:
        import subprocess

        w = self._device.screen_width if self._device else 1080
        h = self._device.screen_height if self._device else 1920

        maps = {
            "up": (w // 2, h * 3 // 4, w // 2, h // 4),
            "down": (w // 2, h // 4, w // 2, h * 3 // 4),
            "left": (w * 3 // 4, h // 2, w // 4, h // 2),
            "right": (w // 4, h // 2, w * 3 // 4, h // 2),
        }
        x1, y1, x2, y2 = maps.get(direction.lower(), maps["up"])

        args = ["adb"]
        udid = self._get_udid()
        if udid:
            args.extend(["-s", udid])
        args.extend(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])
        subprocess.run(args, timeout=15, check=False)

    def _adb_back(self) -> None:
        import subprocess

        args = ["adb"]
        udid = self._get_udid()
        if udid:
            args.extend(["-s", udid])
        args.extend(["shell", "input", "keyevent", "4"])
        subprocess.run(args, timeout=10, check=False)

    def _adb_screencap(self) -> Optional[bytes]:
        try:
            from mobile_device_manager import capture_screenshot_png

            return capture_screenshot_png(self._get_udid())
        except Exception:
            return None

    def _adb_install(self, apk_path: str) -> bool:
        import subprocess

        try:
            args = ["adb"]
            udid = self._get_udid()
            if udid:
                args.extend(["-s", udid])
            args.extend(["install", "-r", apk_path])
            proc = subprocess.run(args, capture_output=True, timeout=120, check=False)
            return proc.returncode == 0
        except Exception:
            return False

    def _adb_uninstall(self, package: str) -> bool:
        import subprocess

        try:
            args = ["adb"]
            udid = self._get_udid()
            if udid:
                args.extend(["-s", udid])
            args.extend(["uninstall", package])
            proc = subprocess.run(args, capture_output=True, timeout=30, check=False)
            return proc.returncode == 0
        except Exception:
            return False

    def _adb_launch(self, package: str, activity: str = "") -> bool:
        import subprocess

        try:
            args = ["adb"]
            udid = self._get_udid()
            if udid:
                args.extend(["-s", udid])
            if activity:
                args.extend(["shell", "am", "start", "-n", f"{package}/{activity}"])
            else:
                args.extend(["shell", "monkey", "-p", package, "-c",
                             "android.intent.category.LAUNCHER", "1"])
            subprocess.run(args, timeout=15, check=False)
            return True
        except Exception:
            return False

    def _adb_stop(self, package: str) -> bool:
        import subprocess

        try:
            args = ["adb"]
            udid = self._get_udid()
            if udid:
                args.extend(["-s", udid])
            args.extend(["shell", "am", "force-stop", package])
            subprocess.run(args, timeout=10, check=False)
            return True
        except Exception:
            return False
