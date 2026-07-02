# -*- coding: utf-8 -*-
"""
移动引擎分发器。

统一入口：策略选择 → Maestro (主力) → VisualFallback (兜底)。

集成现有 ExecutorFactory 模式，提供:
- get_mobile_dispatcher(): 全局单例
- MobileEngineDispatcher: 引擎选择 + 故障转移
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from mobile_engine.engine_interface import (
    DeviceInfo,
    EngineType,
    FlowResult,
    FlowStep,
    StepResult,
    StepStatus,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class MobileEngineDispatcher:
    """
    移动引擎分发器 — 按策略选择引擎，处理故障转移。

    执行流程:
      1. 默认使用 Maestro 引擎
      2. 步骤失败 → 触发 VisualHealer 自愈
      3. 引擎级异常 → 回退到 VisualFallbackAdapter
    """

    def __init__(self):
        self._maestro = None          # 延迟初始化
        self._visual = None           # 延迟初始化
        self._healer = None           # 延迟初始化
        self._current_device: Optional[DeviceInfo] = None
        self._engine_order = self._default_engine_order()

    @property
    def maestro(self):
        if self._maestro is None:
            from mobile_engine.maestro.maestro_adapter import MaestroAdapter

            self._maestro = MaestroAdapter()
        return self._maestro

    @property
    def visual(self):
        if self._visual is None:
            from mobile_engine.visual.visual_adapter import VisualFallbackAdapter

            self._visual = VisualFallbackAdapter()
        return self._visual

    @property
    def healer(self):
        if self._healer is None:
            from mobile_engine.visual.visual_healer import VisualHealer

            self._healer = VisualHealer()
        return self._healer

    @property
    def current_device(self) -> Optional[DeviceInfo]:
        return self._current_device

    @classmethod
    def _default_engine_order(cls) -> List[Tuple[EngineType, int]]:
        """默认引擎优先级: Maestro > VisualFallback"""
        return [
            (EngineType.MAESTRO, 10),
            (EngineType.VISUAL_FALLBACK, 5),
        ]

    # ------------------------------------------------------------------
    # 设备管理
    # ------------------------------------------------------------------

    def connect_device(self, device: DeviceInfo) -> bool:
        """连接设备到所有引擎"""
        self._current_device = device

        # Maestro 连接
        if not self.maestro.connect_device(device):
            uat_logger.warning("Maestro 设备连接失败，将使用视觉兜底")
        else:
            uat_logger.info("Maestro 设备连接成功: %s", device.udid)

        # 视觉引擎连接
        self.visual.connect_device(device)

        return True

    def disconnect_device(self) -> None:
        self.maestro.disconnect_device()
        self.visual.disconnect_device()
        self._current_device = None

    # ------------------------------------------------------------------
    # 测试流执行 (核心)
    # ------------------------------------------------------------------

    def execute_flow(
        self,
        flow: List[FlowStep],
        device: Optional[DeviceInfo] = None,
    ) -> FlowResult:
        """
        执行声明式测试流。

        策略:
        1. 主力: Maestro (执行整个流)
        2. 单步失败 → VisualHealer 自愈
        3. 引擎级异常 → 回退到 VisualFallback
        """
        if device:
            self.connect_device(device)

        if not self._current_device:
            return FlowResult(
                steps=[], total_duration_ms=0,
                passed_count=0, failed_count=0,
            )

        if not flow:
            return FlowResult(
                steps=[], total_duration_ms=0,
                passed_count=0, failed_count=0,
            )

        # 尝试 Maestro 主力执行
        try:
            flow_result = self.maestro.execute_flow(flow)
        except Exception as exc:
            uat_logger.error("Maestro 引擎执行异常: %s", exc)
            # 回退
            return self._fallback_execute_flow(flow)

        # 处理失败步骤 — 触发自愈
        healed_count = 0
        for i, step_result in enumerate(flow_result.steps):
            if step_result.is_failed and i < len(flow):
                original_step = flow[i]
                if original_step.locator:
                    healed = self.healer.try_heal(
                        original_step, self._current_device, step_result,
                    )
                    if healed:
                        flow_result.steps[i] = healed
                        flow_result.passed_count += 1
                        flow_result.failed_count -= 1
                        healed_count += 1

        if healed_count > 0:
            uat_logger.info("自愈完成: 修复了 %d 个失败步骤", healed_count)

        return flow_result

    def execute_step(self, step: FlowStep) -> StepResult:
        """执行单个步骤 (主力 Maestro，失败自愈)"""
        try:
            result = self.maestro.execute_step(step)
            if result.is_failed and step.locator:
                healed = self.healer.try_heal(step, self._current_device, result)
                if healed:
                    return healed
            return result
        except Exception as exc:
            uat_logger.error("Maestro 单步执行异常: %s", exc)
            # 视觉兜底
            try:
                return self.visual.execute_step(step)
            except Exception as exc2:
                return StepResult(
                    status=StepStatus.FAILED,
                    action=step.action,
                    error=str(exc2),
                )

    # ------------------------------------------------------------------
    # YAML 流执行
    # ------------------------------------------------------------------

    def execute_yaml_file(self, yaml_path: str) -> FlowResult:
        """执行现有 Maestro YAML 文件"""
        return self.maestro.execute_yaml_file(yaml_path)

    def record_flow(self, output_path: str = "", timeout: int = 600) -> str:
        """录制模式"""
        return self.maestro.record_flow(output_path, timeout=timeout)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _fallback_execute_flow(self, flow: List[FlowStep]) -> FlowResult:
        """完整回退到视觉引擎执行"""
        uat_logger.warning("回退到视觉兜底引擎执行流 (%d 步骤)", len(flow))
        return self.visual.execute_flow(flow)

    def get_engine_status(self) -> Dict[str, bool]:
        """获取各引擎就绪状态"""
        return {
            "maestro": self.maestro.is_connected,
            "visual_fallback": self.visual.is_connected,
        }

    def reset(self) -> None:
        """重置分发器状态"""
        self._maestro = None
        self._visual = None
        self._healer = None
        self._current_device = None


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_default_dispatcher: Optional[MobileEngineDispatcher] = None


def get_mobile_dispatcher() -> MobileEngineDispatcher:
    """获取全局 MobileEngineDispatcher 单例"""
    global _default_dispatcher
    if _default_dispatcher is None:
        _default_dispatcher = MobileEngineDispatcher()
    return _default_dispatcher


def reset_mobile_dispatcher() -> None:
    """重置全局分发器 (测试/调试用)"""
    global _default_dispatcher
    if _default_dispatcher:
        _default_dispatcher.reset()
    _default_dispatcher = None
