# -*- coding: utf-8 -*-
"""
执行器工厂：统一步骤 → Web（Playwright）/ 桌面（pywinauto）/ Mobile（Maestro/Appium）分发。

v2.0: 集成 Maestro 引擎作为移动端主力执行器。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from desktop_automation import sync_desktop_execute_step
from step_executor import (
    async_execute_step_by_layer,
    case_steps_include_android,
    case_steps_include_desktop,
    case_steps_include_web,
    convert_db_step_to_flow_step,
    ensure_mixed_run_environment,
    enrich_execution_step,
    is_desktop_step,
    is_mobile_step,
    normalize_automation_layer,
    validate_desktop_step_result,
    validate_step_for_layer,
)


class ExecutorFactory:
    """架构图「执行器工厂」：按 automation_layer 路由单步与用例环境校验。"""

    def __init__(self):
        self._mobile_dispatcher = None  # 延迟初始化

    def _get_mobile_dispatcher(self):
        if self._mobile_dispatcher is None:
            from mobile_engine.engine_dispatcher import MobileEngineDispatcher

            self._mobile_dispatcher = MobileEngineDispatcher()
        return self._mobile_dispatcher

    def validate_case_environment(self, steps: List[Dict[str, Any]]) -> Optional[str]:
        return ensure_mixed_run_environment(steps)

    def case_includes_desktop(self, steps: List[Dict[str, Any]]) -> bool:
        return case_steps_include_desktop(steps)

    def case_includes_android(self, steps: List[Dict[str, Any]]) -> bool:
        return case_steps_include_android(steps)

    def case_includes_web(self, steps: List[Dict[str, Any]]) -> bool:
        return case_steps_include_web(steps)

    def prepare_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        return enrich_execution_step(step)

    def validate_step(self, step: Dict[str, Any]) -> Optional[str]:
        layer = normalize_automation_layer(step)
        return validate_step_for_layer((step.get("action") or "").strip(), layer)

    def is_desktop_step(self, step: Dict[str, Any]) -> bool:
        return is_desktop_step(step)

    def is_mobile_step(self, step: Dict[str, Any]) -> bool:
        return is_mobile_step(step)

    def execute_desktop_step(
        self,
        step: Dict[str, Any],
        *,
        selector_value: str = "",
        input_value: str = "",
    ) -> Dict[str, Any]:
        exec_step = self.prepare_step(step)
        if selector_value:
            exec_step["selector_value"] = selector_value
        if input_value:
            exec_step["input_value"] = input_value
        err = self.validate_step(exec_step)
        if err:
            raise ValueError(err)
        result = sync_desktop_execute_step(exec_step)
        return validate_desktop_step_result(
            result, (exec_step.get("action") or "").strip()
        )

    def execute_mobile_step(
        self,
        step: Dict[str, Any],
        *,
        selector_value: str = "",
        input_value: str = "",
    ) -> Dict[str, Any]:
        """
        移动端步骤执行 — 通过 Maestro 引擎或视觉兜底。
        v2.0: 恢复 PC 端移动执行能力，移除旧 Appium 依赖。
        """
        dispatcher = self._get_mobile_dispatcher()

        # 确保设备已连接
        if not dispatcher.current_device:
            from mobile_device_manager import get_connected_udid, get_device_info

            udid = get_connected_udid() or ""
            if not udid:
                raise RuntimeError("未连接 Android 设备，请先连接真机或模拟器")
            info = get_device_info(udid)
            from mobile_engine.engine_interface import DeviceInfo

            device = DeviceInfo(
                udid=udid,
                platform="android",
                model=info.get("model", ""),
                screen_width=info.get("width", 1080),
                screen_height=info.get("height", 1920),
                density=info.get("density", 420),
                is_emulator=udid.startswith("emulator-"),
            )
            dispatcher._maestro.connect_device(device)

        # 将数据库步骤转为 FlowStep 并执行
        flow_step = convert_db_step_to_flow_step(step)
        result = dispatcher._maestro.execute_step(flow_step)

        # 转为兼容现有 dict 格式
        return {
            "status": "success" if result.is_success else "error",
            "action": result.action,
            "description": result.description,
            "duration": result.duration_ms / 1000.0,
            "error": result.error,
            "screenshot": result.screenshot_path,
            "healed": result.healed_locator is not None,
        }

    def execute_mobile_flow(
        self,
        db_steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        执行完整的移动端测试流（从数据库步骤列表）。

        Returns:
            包含 FlowResult 摘要的 dict
        """
        dispatcher = self._get_mobile_dispatcher()

        # 转换步骤
        from mobile_engine.maestro.maestro_flow_generator import MaestroFlowGenerator

        gen = MaestroFlowGenerator()
        flow = gen._convert_db_steps(db_steps)

        # 执行
        result = dispatcher._maestro.execute_flow(flow)

        return result.to_dict()

    def execute_step(
        self,
        step: Dict[str, Any],
        *,
        web_executor: Optional[Callable[[Dict[str, Any]], None]] = None,
        selector_value: str = "",
        input_value: str = "",
    ) -> Dict[str, Any]:
        """
        同步执行单步。桌面/Android 返回结果 dict；Web 调用 web_executor 后返回 delegated 标记。
        """
        if self.is_desktop_step(step):
            return self.execute_desktop_step(
                step, selector_value=selector_value, input_value=input_value
            )
        if self.is_mobile_step(step):
            return self.execute_mobile_step(
                step, selector_value=selector_value, input_value=input_value
            )
        err = self.validate_step(step)
        if err:
            raise ValueError(err)
        if not web_executor:
            raise ValueError("Web 步骤需要提供 web_executor 回调")
        exec_step = dict(step)
        if selector_value:
            exec_step["selector_value"] = selector_value
        if input_value:
            exec_step["input_value"] = input_value
        web_executor(exec_step)
        return {"status": "delegated_web"}

    async def execute_step_async(
        self, step: Dict[str, Any], automation: Any
    ) -> List[Dict[str, Any]]:
        """Playwright 批量路径：桌面/Android to_thread，Web 走 automation.execute_single_step。"""
        return await async_execute_step_by_layer(step, automation)


_default_factory: Optional[ExecutorFactory] = None


def get_executor_factory() -> ExecutorFactory:
    global _default_factory
    if _default_factory is None:
        _default_factory = ExecutorFactory()
    return _default_factory
