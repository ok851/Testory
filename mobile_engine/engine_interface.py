# -*- coding: utf-8 -*-
"""
移动端测试引擎抽象接口。

定义:
  - MobileTestEngine: 所有引擎适配器必须实现的抽象基类
  - 核心数据类型: DeviceInfo, LocatorInfo, FlowStep, StepResult, FlowResult
  - 枚举: EngineType, StepStatus
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class EngineType(Enum):
    """引擎类型枚举"""
    MAESTRO = "maestro"
    VISUAL_FALLBACK = "visual_fallback"
    APPIUM = "appium"           # 预留
    ESPRESSO = "espresso"       # 预留


class StepStatus(Enum):
    """步骤执行状态"""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class LocatorStrategy:
    """分层定位策略常量"""
    TEXT = "text"
    ID = "id"
    ACCESSIBILITY_ID = "accessibility_id"
    SEMANTIC = "semantic"          # 语义描述，Maestro AI 匹配
    RELATIVE = "relative"          # 相对位置 (below/rightOf)
    VISUAL = "visual"              # AI/OpenCV 视觉匹配
    COORDINATE = "coordinate"      # 百分比坐标


# 定位策略优先级映射 (值越小越优先)
LOCATOR_PRIORITY_MAP: Dict[str, int] = {
    LocatorStrategy.TEXT: 1,
    LocatorStrategy.ID: 1,
    LocatorStrategy.ACCESSIBILITY_ID: 1,
    LocatorStrategy.SEMANTIC: 2,
    LocatorStrategy.RELATIVE: 3,
    LocatorStrategy.VISUAL: 4,
    LocatorStrategy.COORDINATE: 5,
}


@dataclass
class DeviceInfo:
    """设备信息"""
    udid: str
    platform: str = "android"       # "android" | "ios"
    model: str = ""
    os_version: str = ""
    screen_width: int = 1080
    screen_height: int = 1920
    density: int = 420
    is_emulator: bool = False
    connection_type: str = "usb"    # "usb" | "wireless" | "cloud"
    brand: str = ""
    app_package: str = ""
    app_activity: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "udid": self.udid,
            "platform": self.platform,
            "model": self.model,
            "os_version": self.os_version,
            "screen_width": self.screen_width,
            "screen_height": self.screen_height,
            "density": self.density,
            "is_emulator": self.is_emulator,
            "connection_type": self.connection_type,
            "brand": self.brand,
            "app_package": self.app_package,
            "app_activity": self.app_activity,
        }


@dataclass
class LocatorInfo:
    """分层定位信息"""
    strategy: str = ""              # LocatorStrategy 常量之一
    value: str = ""                 # 定位符值
    priority: int = 1               # 优先级 (1-5, 越小越优先)
    fallback_values: List[str] = field(default_factory=list)
    # 相对定位扩展字段
    relative_direction: str = ""    # "below" | "rightOf" | "above" | "leftOf"
    relative_target: str = ""       # 相对目标元素的描述
    # 视觉匹配扩展字段
    visual_template_path: str = ""  # 视觉模板图片路径
    semantic_desc: str = ""         # 语义描述 (供 Maestro AI)

    @staticmethod
    def from_text(text: str) -> "LocatorInfo":
        return LocatorInfo(strategy=LocatorStrategy.TEXT, value=text, priority=1)

    @staticmethod
    def from_id(element_id: str) -> "LocatorInfo":
        return LocatorInfo(strategy=LocatorStrategy.ID, value=element_id, priority=1)

    @staticmethod
    def from_semantic(desc: str) -> "LocatorInfo":
        return LocatorInfo(strategy=LocatorStrategy.SEMANTIC, value=desc,
                           semantic_desc=desc, priority=2)

    @staticmethod
    def from_relative(direction: str, target: str) -> "LocatorInfo":
        return LocatorInfo(strategy=LocatorStrategy.RELATIVE, value=target,
                           relative_direction=direction, relative_target=target, priority=3)


@dataclass
class FlowStep:
    """声明式流程步骤 — 引擎无关的内部 DSL"""
    action: str                     # "launch_app"|"stop_app"|"tap"|"input"|"swipe"|
                                    # "scroll"|"assert"|"wait"|"screenshot"|"back"|
                                    # "press_key"|"long_press"
    description: str = ""
    locator: Optional[LocatorInfo] = None
    input_value: str = ""
    swipe_direction: str = ""       # "up"|"down"|"left"|"right"
    swipe_start: str = ""           # "50%,80%" 起始点百分比
    swipe_end: str = ""             # "50%,20%" 结束点百分比
    swipe_duration_ms: int = 400
    assert_type: str = ""           # "visible"|"not_visible"|"contains_text"
    wait_timeout_ms: int = 10000
    # Maestro 扩展字段
    maestro_label: str = ""         # 步骤标签 (录像中显示)
    maestro_optional: bool = False  # 可选步骤，失败不终止
    maestro_retry: int = 0          # 重试次数
    # 坐标模式
    tap_x: Optional[int] = None
    tap_y: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"action": self.action, "description": self.description}
        if self.locator:
            d["locator"] = {
                "strategy": self.locator.strategy,
                "value": self.locator.value,
                "priority": self.locator.priority,
            }
        if self.input_value:
            d["input_value"] = self.input_value
        if self.swipe_direction:
            d["swipe_direction"] = self.swipe_direction
        if self.swipe_start:
            d["swipe_start"] = self.swipe_start
        if self.swipe_end:
            d["swipe_end"] = self.swipe_end
        if self.assert_type:
            d["assert_type"] = self.assert_type
        return d


@dataclass
class StepResult:
    """单步执行结果"""
    status: StepStatus
    action: str
    description: str = ""
    duration_ms: float = 0.0
    error: str = ""
    screenshot_path: str = ""
    device_log_path: str = ""
    dump_path: str = ""             # 层级视图 XML dump
    locator_used: Optional[LocatorInfo] = None
    healed_locator: Optional[LocatorInfo] = None
    # 扩展信息
    match_confidence: float = 0.0   # 视觉匹配置信度
    raw_output: str = ""            # 引擎原始输出

    @property
    def is_success(self) -> bool:
        return self.status == StepStatus.SUCCESS

    @property
    def is_failed(self) -> bool:
        return self.status == StepStatus.FAILED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "action": self.action,
            "description": self.description,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
            "device_log_path": self.device_log_path,
            "dump_path": self.dump_path,
            "locator_used": self.locator_used.value if self.locator_used else "",
            "healed": self.healed_locator is not None,
            "match_confidence": self.match_confidence,
        }


@dataclass
class FlowResult:
    """测试流执行结果"""
    steps: List[StepResult]
    total_duration_ms: float
    passed_count: int
    failed_count: int
    skipped_count: int = 0
    video_path: str = ""
    raw_report_path: str = ""
    flow_name: str = ""

    @property
    def pass_rate(self) -> float:
        total = self.passed_count + self.failed_count
        if total == 0:
            return 0.0
        return self.passed_count / total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_name": self.flow_name,
            "steps": [s.to_dict() for s in self.steps],
            "total_duration_ms": self.total_duration_ms,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "pass_rate": self.pass_rate,
            "video_path": self.video_path,
        }


class MobileTestEngine(ABC):
    """移动端测试引擎抽象接口 — 所有适配器必须实现"""

    # ========================================================================
    # 元信息
    # ========================================================================

    @property
    @abstractmethod
    def engine_type(self) -> EngineType:
        """返回引擎类型"""
        ...

    @property
    @abstractmethod
    def engine_version(self) -> str:
        """返回引擎版本号"""
        ...

    @property
    def is_connected(self) -> bool:
        """是否已连接设备"""
        return self._device is not None

    @property
    def device(self) -> Optional[DeviceInfo]:
        """当前连接的设备信息"""
        return self._device

    # ========================================================================
    # 设备管理
    # ========================================================================

    @abstractmethod
    def connect_device(self, device: DeviceInfo) -> bool:
        """连接设备并校验就绪状态，返回是否成功"""
        ...

    @abstractmethod
    def disconnect_device(self) -> None:
        """断开设备连接"""
        ...

    @abstractmethod
    def check_device_readiness(self) -> Dict[str, Any]:
        """
        设备前置自检。
        Returns:
            {"all_passed": bool, "checks": [...], "warnings": [...], "errors": [...]}
        """
        ...

    @abstractmethod
    def install_app(self, app_path: str) -> bool:
        """安装应用到设备"""
        ...

    @abstractmethod
    def uninstall_app(self, package_name: str) -> bool:
        """卸载应用"""
        ...

    @abstractmethod
    def launch_app(self, package_name: str, activity: str = "") -> bool:
        """启动应用"""
        ...

    @abstractmethod
    def stop_app(self, package_name: str) -> bool:
        """停止应用"""
        ...

    @abstractmethod
    def capture_screenshot(self) -> bytes:
        """截取设备屏幕，返回 PNG 字节"""
        ...

    @abstractmethod
    def capture_screenshot_to_file(self, output_path: str) -> str:
        """截取屏幕保存到文件，返回文件路径"""
        ...

    def start_recording(self) -> None:
        """开始录像（默认空实现，子类可选重写）"""
        pass

    def stop_recording(self) -> str:
        """停止录像，返回文件路径（默认空实现）"""
        return ""

    # ========================================================================
    # 测试流执行
    # ========================================================================

    @abstractmethod
    def execute_flow(self, flow: List[FlowStep]) -> FlowResult:
        """执行声明式测试流"""
        ...

    @abstractmethod
    def execute_step(self, step: FlowStep) -> StepResult:
        """执行单个步骤"""
        ...

    def resume_flow(self, flow: List[FlowStep], from_index: int = 0) -> FlowResult:
        """
        从指定索引恢复执行。
        默认实现：截取 flow[from_index:] 重新执行。
        """
        remaining = flow[from_index:]
        return self.execute_flow(remaining)

    # ========================================================================
    # 原子元素交互 (供 execute_step 内部调用)
    # ========================================================================

    @abstractmethod
    def tap(self, locator: LocatorInfo) -> StepResult:
        """点击元素"""
        ...

    @abstractmethod
    def tap_coordinates(self, x: int, y: int) -> StepResult:
        """点击坐标"""
        ...

    @abstractmethod
    def input_text(self, locator: LocatorInfo, text: str) -> StepResult:
        """输入文本"""
        ...

    @abstractmethod
    def swipe(self, direction: str, duration_ms: int = 400) -> StepResult:
        """滑动 (up/down/left/right)"""
        ...

    @abstractmethod
    def assert_element(self, locator: LocatorInfo, condition: str) -> StepResult:
        """断言元素状态 (visible/not_visible/contains_text)"""
        ...

    @abstractmethod
    def press_back(self) -> StepResult:
        """按返回键"""
        ...

    # ========================================================================
    # 报告
    # ========================================================================

    @abstractmethod
    def get_structured_log(self) -> Dict[str, Any]:
        """获取结构化执行日志"""
        ...

    # ========================================================================
    # 内部状态
    # ========================================================================

    def __init__(self) -> None:
        self._device: Optional[DeviceInfo] = None
