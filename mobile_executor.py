# -*- coding: utf-8 -*-
"""
Android 移动端 Appium 执行器（单会话）。
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from mobile_automation import (
    mobile_action_requires_locator,
    normalize_mobile_action,
    normalize_strategy,
    parse_tap_coordinates,
    prepare_mobile_step,
    save_screenshot_bytes,
    validate_step_for_mobile,
)
from mobile_device_manager import check_appium_server, set_connected_udid
from mobile_env_config import (
    build_default_capabilities,
    default_app_activity,
    default_app_package,
    mobile_enabled,
    mobile_runtime_available,
    mobile_runtime_unavailable_reason,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)

_session_lock = threading.Lock()
_default_executor: Optional["MobileExecutor"] = None


class MobileExecutor:
    """Appium Android 单会话执行器。"""

    def __init__(self, capabilities: Optional[Dict[str, Any]] = None) -> None:
        self._capabilities = dict(capabilities or {})
        self._driver: Any = None
        self._connected_udid: Optional[str] = None
        self._screenshot_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "static", "mobile_screenshots"
        )
        os.makedirs(self._screenshot_dir, exist_ok=True)

    @classmethod
    def is_enabled(cls) -> bool:
        return mobile_enabled()

    def check_appium_server(self) -> Tuple[bool, str]:
        return check_appium_server()

    @property
    def is_connected(self) -> bool:
        return self._driver is not None

    @property
    def connected_udid(self) -> Optional[str]:
        return self._connected_udid

    def connect(self, capabilities: Optional[Dict[str, Any]] = None) -> None:
        """
        建立 Appium 会话。capabilities 会与 .env 默认值合并。
        """
        if not mobile_runtime_available():
            reason = mobile_runtime_unavailable_reason() or "移动端不可用"
            raise RuntimeError(reason)
        ok, msg = self.check_appium_server()
        if not ok:
            raise RuntimeError(msg)

        from appium import webdriver
        from appium.options.android import UiAutomator2Options

        caps_in = dict(self._capabilities)
        if capabilities:
            caps_in.update(capabilities)
        udid = (caps_in.get("udid") or caps_in.pop("udid", "") or "").strip()
        base_caps = build_default_capabilities(udid)
        base_caps.update({k: v for k, v in caps_in.items() if v not in (None, "")})

        options = UiAutomator2Options().load_capabilities(base_caps)

        from mobile_env_config import appium_server_url

        server = appium_server_url().rstrip("/")
        uat_logger.info("连接 Appium: server=%s udid=%s", server, udid or "(default)")

        with _session_lock:
            self.disconnect()
            self._driver = webdriver.Remote(server, options=options)
            self._connected_udid = udid or None
            self._capabilities = base_caps
            set_connected_udid(self._connected_udid)

    def disconnect(self) -> None:
        """关闭 Appium 会话。"""
        with _session_lock:
            if self._driver is not None:
                try:
                    self._driver.quit()
                except Exception as exc:
                    uat_logger.warning("关闭 Appium 会话异常: %s", exc)
                finally:
                    self._driver = None
            self._connected_udid = None
            set_connected_udid(None)

    def execute_steps(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """顺序执行步骤列表。"""
        results: List[Dict[str, Any]] = []
        for step in steps or []:
            results.append(self.execute_step(step))
            if results[-1].get("status") == "error":
                break
        return results

    def execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """执行单步并返回结果 dict。"""
        prepared = prepare_mobile_step(step)
        action = prepared.get("action") or ""
        description = (prepared.get("description") or "").strip()
        err = validate_step_for_mobile(action)
        if err:
            return {"status": "error", "error": err, "action": action, "description": description}

        if self._driver is None:
            try:
                spec = prepared.get("mobile_spec") if isinstance(prepared.get("mobile_spec"), dict) else {}
                caps: Dict[str, Any] = {}
                if spec.get("udid"):
                    caps["udid"] = spec["udid"]
                if spec.get("appPackage"):
                    caps["appPackage"] = spec["appPackage"]
                if spec.get("appActivity"):
                    caps["appActivity"] = spec["appActivity"]
                self.connect(caps or None)
            except Exception as exc:
                return {
                    "status": "error",
                    "error": str(exc),
                    "action": action,
                    "description": description,
                }

        started = time.time()
        try:
            result = self._dispatch_action(prepared)
            result.setdefault("status", "success")
            result["action"] = action
            result["description"] = description
            result["duration"] = round(time.time() - started, 3)
            uat_logger.info("移动端步骤成功: %s | %s", action, description[:60])
            return result
        except Exception as exc:
            shot = self._safe_screenshot()
            uat_logger.error("移动端步骤失败: %s | %s | %s", action, description, exc)
            return {
                "status": "error",
                "error": str(exc),
                "action": action,
                "description": description,
                "duration": round(time.time() - started, 3),
                "screenshot": shot,
            }

    def tap_at_coordinates(self, x: int, y: int) -> Dict[str, Any]:
        """在设备坐标点击（供 canvas 手动操作）；Appium 不可用时回退 ADB/u2。"""
        from mobile_adb_control import smart_tap

        udid = self._connected_udid or ""
        if self._driver is not None:
            try:
                self._driver.execute_script(
                    "mobile: clickGesture",
                    {"x": int(x), "y": int(y)},
                )
                return {"status": "success", "x": x, "y": y, "via": "appium"}
            except Exception:
                try:
                    self._driver.tap([(int(x), int(y))], 100)
                    return {"status": "success", "x": x, "y": y, "via": "appium_tap"}
                except Exception:
                    pass
        result = smart_tap(udid, int(x), int(y))
        return {"status": "success", **result}

    def _dispatch_action(self, step: Dict[str, Any]) -> Dict[str, Any]:
        action = step.get("action") or ""
        if action == "open_app":
            return self._action_open_app(step)
        if action == "close_app":
            return self._action_close_app(step)
        if action == "tap":
            return self._action_tap(step)
        if action == "input_text":
            return self._action_input_text(step)
        if action == "swipe":
            return self._action_swipe(step)
        if action == "wait":
            return self._action_wait(step)
        if action == "assert_text":
            return self._action_assert_text(step)
        if action == "assert_element":
            return self._action_assert_element(step)
        if action == "screenshot":
            return self._action_screenshot(step)
        if action == "tap_image":
            return self._action_tap_image(step)
        if action == "wait_image":
            return self._action_wait_image(step)
        if action == "assert_image":
            return self._action_assert_image(step)
        raise RuntimeError(f"未实现的移动端动作: {action}")

    def _action_open_app(self, step: Dict[str, Any]) -> Dict[str, Any]:
        spec = step.get("mobile_spec") if isinstance(step.get("mobile_spec"), dict) else {}
        pkg = (
            (step.get("input_value") or "").strip()
            or (spec.get("appPackage") or "").strip()
            or default_app_package()
        )
        act = (spec.get("appActivity") or "").strip() or default_app_activity()
        if not pkg:
            raise RuntimeError("open_app 需要 appPackage（步骤 input_value 或 mobile_spec 或 .env ANDROID_APP_PACKAGE）")
        if act:
            self._driver.start_activity(pkg, act)
        else:
            self._driver.activate_app(pkg)
        return {"app_package": pkg, "app_activity": act}

    def _action_close_app(self, step: Dict[str, Any]) -> Dict[str, Any]:
        spec = step.get("mobile_spec") if isinstance(step.get("mobile_spec"), dict) else {}
        pkg = (
            (step.get("input_value") or "").strip()
            or (spec.get("appPackage") or "").strip()
            or default_app_package()
        )
        if not pkg:
            raise RuntimeError("close_app 需要 appPackage")
        self._driver.terminate_app(pkg)
        return {"app_package": pkg}

    def _action_tap(self, step: Dict[str, Any]) -> Dict[str, Any]:
        coords = parse_tap_coordinates(step)
        if coords is not None:
            return self.tap_at_coordinates(coords[0], coords[1])
        strategy = normalize_strategy(step)
        if strategy == "visual_template":
            return self._action_tap_image(step)
        el = self._find_element(step)
        el.click()
        return {"strategy": strategy, "selector_value": step.get("selector_value") or ""}

    def _screen_png_for_visual(self) -> bytes:
        from mobile_device_manager import capture_screenshot_png

        udid = self._connected_udid or ""
        png = capture_screenshot_png(udid)
        if png:
            return png
        if self._driver is None:
            raise RuntimeError("无法获取设备截图")
        return self._driver.get_screenshot_as_png()

    def _visual_anchor(self, step: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
        spec = step.get("mobile_spec") if isinstance(step.get("mobile_spec"), dict) else {}
        ax = spec.get("anchor_x")
        ay = spec.get("anchor_y")
        if ax is not None and ay is not None:
            try:
                return int(ax), int(ay)
            except (TypeError, ValueError):
                pass
        coords = parse_tap_coordinates(step)
        if coords:
            return coords[0], coords[1]
        return None, None

    def _action_tap_image(self, step: Dict[str, Any]) -> Dict[str, Any]:
        from mobile_image_engine import resolve_tap_point_on_screen

        value = (step.get("selector_value") or "").strip()
        if not value:
            raise RuntimeError("tap_image 需要 visual_template（selector_value）")
        ax, ay = self._visual_anchor(step)
        png = self._screen_png_for_visual()
        x, y, score = resolve_tap_point_on_screen(
            png, value, anchor_x=ax, anchor_y=ay
        )
        result = self.tap_at_coordinates(x, y)
        result["match_score"] = score
        result["matched_at"] = (x, y)
        return result

    def _action_wait_image(self, step: Dict[str, Any]) -> Dict[str, Any]:
        from mobile_image_engine import wait_for_template

        value = (step.get("selector_value") or "").strip()
        if not value:
            raise RuntimeError("wait_image 需要 visual_template")
        raw = (step.get("input_value") or "5").strip()
        try:
            timeout = float(raw)
        except ValueError:
            timeout = 5.0
        if timeout > 120:
            timeout = timeout / 1000.0
        timeout = max(0.5, min(120.0, timeout))
        ax, ay = self._visual_anchor(step)
        deadline = time.time() + timeout
        last_err = ""
        while time.time() < deadline:
            try:
                png = self._screen_png_for_visual()
                x, y, score = wait_for_template(
                    png, value, anchor_x=ax, anchor_y=ay
                )
                return {"found": True, "x": x, "y": y, "match_score": score}
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.4)
        raise RuntimeError(last_err or f"wait_image 超时（{timeout}s）")

    def _action_assert_image(self, step: Dict[str, Any]) -> Dict[str, Any]:
        result = self._action_wait_image(step)
        result["asserted"] = True
        return result

    def _action_input_text(self, step: Dict[str, Any]) -> Dict[str, Any]:
        text = (step.get("input_value") or "").strip()
        if not text:
            raise RuntimeError("input_text 的 input_value 不能为空")
        el = self._find_element(step)
        try:
            el.clear()
        except Exception:
            pass
        el.send_keys(text)
        return {"input_value": text, "strategy": normalize_strategy(step)}

    def _action_swipe(self, step: Dict[str, Any]) -> Dict[str, Any]:
        try:
            sx = int(step.get("swipe_x") or 0)
            sy = int(step.get("swipe_y") or 0)
        except (TypeError, ValueError):
            sx, sy = 0, 0
        size = self._driver.get_window_size()
        w, h = size["width"], size["height"]
        start_x, start_y = w // 2, int(h * 0.7)
        end_x, end_y = w // 2, int(h * 0.3)
        if sx or sy:
            end_x, end_y = start_x + sx, start_y + sy
        self._driver.swipe(start_x, start_y, end_x, end_y, 800)
        return {"swipe": {"start": (start_x, start_y), "end": (end_x, end_y)}}

    def _action_wait(self, step: Dict[str, Any]) -> Dict[str, Any]:
        raw = (step.get("input_value") or "1").strip()
        try:
            val = float(raw)
        except ValueError:
            val = 1.0
        seconds = val / 1000.0 if val > 120 else val
        seconds = max(0.1, min(120.0, seconds))
        time.sleep(seconds)
        return {"wait_seconds": seconds}

    def _action_assert_text(self, step: Dict[str, Any]) -> Dict[str, Any]:
        expected = (step.get("input_value") or "").strip()
        if not expected:
            raise RuntimeError("assert_text 的 input_value 不能为空")
        compare = (step.get("compare_type") or "text_equals").strip().lower()
        if mobile_action_requires_locator("assert_text") and (step.get("selector_value") or "").strip():
            el = self._find_element(step)
            actual = (el.text or "").strip()
        else:
            actual = ""
            try:
                from appium.webdriver.common.appiumby import AppiumBy

                actual = (self._driver.find_element(AppiumBy.TAG_NAME, "body").text or "").strip()
            except Exception:
                actual = (self._driver.page_source or "")
        if compare in ("text_equals", "equals"):
            from auth_batch_helpers import page_text_has_exact_snippet

            if (step.get("selector_value") or "").strip():
                if actual != expected:
                    raise RuntimeError(f"文本断言失败：期望「{expected}」，实际「{actual[:200]}」")
            elif not page_text_has_exact_snippet(actual, expected):
                raise RuntimeError(
                    f"文本相等断言失败：页面未出现与预期完全一致的文案「{expected}」"
                )
        elif compare in ("text_regex", "regex"):
            if not re.search(expected, actual):
                raise RuntimeError(f"文本正则断言失败：pattern={expected}")
        else:
            if expected not in actual:
                raise RuntimeError(f"文本包含断言失败：未找到「{expected}」")
        return {"expected": expected, "compare_type": compare}

    def _action_assert_element(self, step: Dict[str, Any]) -> Dict[str, Any]:
        el = self._find_element(step)
        if not el.is_displayed():
            raise RuntimeError(f"元素不可见: {step.get('selector_value')}")
        return {"strategy": normalize_strategy(step), "visible": True}

    def _action_screenshot(self, step: Dict[str, Any]) -> Dict[str, Any]:
        path = self._safe_screenshot()
        return {"screenshot": path}

    def _find_element(self, step: Dict[str, Any]) -> Any:
        from appium.webdriver.common.appiumby import AppiumBy
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        strategy = normalize_strategy(step)
        value = (step.get("selector_value") or "").strip()
        if not value:
            raise RuntimeError(f"步骤缺少 selector_value（strategy={strategy}）")

        by_map = {
            "id": AppiumBy.ID,
            "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
            "xpath": AppiumBy.XPATH,
            "class_name": AppiumBy.CLASS_NAME,
            "android_uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
        }
        by = by_map.get(strategy, AppiumBy.ACCESSIBILITY_ID)
        locator = (by, value)
        wait = WebDriverWait(self._driver, 15)
        return wait.until(EC.presence_of_element_located(locator))

    def _safe_screenshot(self) -> Optional[str]:
        if self._driver is None:
            return None
        try:
            png = self._driver.get_screenshot_as_png()
            return save_screenshot_bytes(png)
        except Exception:
            return None


def get_mobile_executor() -> MobileExecutor:
    """获取进程内单例 MobileExecutor。"""
    global _default_executor
    if _default_executor is None:
        _default_executor = MobileExecutor()
    return _default_executor
