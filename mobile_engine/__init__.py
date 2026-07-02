# -*- coding: utf-8 -*-
"""
移动端测试引擎包 — 统一抽象接口 + 多引擎适配架构。

引擎列表:
- MaestroAdapter: 主力引擎，基于 Maestro CLI 驱动设备
- VisualFallbackAdapter: 视觉兜底，基于 OpenCV/AI 截图操作
- [预留] AppiumAdapter, EspressoAdapter
"""

from mobile_engine.engine_interface import (
    DeviceInfo,
    EngineType,
    FlowResult,
    FlowStep,
    LocatorInfo,
    MobileTestEngine,
    StepResult,
    StepStatus,
)

__all__ = [
    "MobileTestEngine",
    "EngineType",
    "StepStatus",
    "DeviceInfo",
    "LocatorInfo",
    "FlowStep",
    "StepResult",
    "FlowResult",
]
