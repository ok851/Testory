# -*- coding: utf-8 -*-
"""
执行器工厂：统一步骤 → Web（Playwright）/ 桌面（pywinauto）分发。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from desktop_automation import sync_desktop_execute_step
from step_executor import (
    async_execute_step_by_layer,
    case_steps_include_desktop,
    case_steps_include_web,
    ensure_mixed_run_environment,
    enrich_execution_step,
    is_desktop_step,
    normalize_automation_layer,
    validate_desktop_step_result,
    validate_step_for_layer,
)


class ExecutorFactory:
    """架构图「执行器工厂」：按 automation_layer 路由单步与用例环境校验。"""

    def validate_case_environment(self, steps: List[Dict[str, Any]]) -> Optional[str]:
        return ensure_mixed_run_environment(steps)

    def case_includes_desktop(self, steps: List[Dict[str, Any]]) -> bool:
        return case_steps_include_desktop(steps)

    def case_includes_web(self, steps: List[Dict[str, Any]]) -> bool:
        return case_steps_include_web(steps)

    def prepare_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        return enrich_execution_step(step)

    def validate_step(self, step: Dict[str, Any]) -> Optional[str]:
        layer = normalize_automation_layer(step)
        return validate_step_for_layer((step.get("action") or "").strip(), layer)

    def is_desktop_step(self, step: Dict[str, Any]) -> bool:
        return is_desktop_step(step)

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

    def execute_step(
        self,
        step: Dict[str, Any],
        *,
        web_executor: Optional[Callable[[Dict[str, Any]], None]] = None,
        selector_value: str = "",
        input_value: str = "",
    ) -> Dict[str, Any]:
        """
        同步执行单步。桌面返回结果 dict；Web 调用 web_executor 后返回 delegated 标记。
        """
        if self.is_desktop_step(step):
            return self.execute_desktop_step(
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
        """Playwright 批量路径：桌面 to_thread，Web 走 automation.execute_single_step。"""
        return await async_execute_step_by_layer(step, automation)


_default_factory: Optional[ExecutorFactory] = None


def get_executor_factory() -> ExecutorFactory:
    global _default_factory
    if _default_factory is None:
        _default_factory = ExecutorFactory()
    return _default_factory
