# -*- coding: utf-8 -*-
"""
桌面自动化技能框架

提供功能：
1. DesktopSkill 抽象基类 - 所有技能的统一接口
2. SkillRegistry - 技能注册与发现
3. SkillContext - 技能执行上下文
4. SkillResult - 技能执行结果
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Type, Union
from enum import Enum, auto


class SkillStatus(Enum):
    """技能执行状态。"""
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    PARTIAL_SUCCESS = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class SkillResult:
    """技能执行结果。"""
    status: SkillStatus = SkillStatus.PENDING
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Exception] = None
    trace: str = ""
    duration_sec: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.name,
            "message": self.message,
            "data": self.data,
            "error": str(self.error) if self.error else None,
            "trace": self.trace,
            "duration_sec": self.duration_sec,
            "timestamp": self.timestamp,
        }

    @classmethod
    def success(cls, message: str = "", data: Dict[str, Any] = None) -> "SkillResult":
        return cls(
            status=SkillStatus.SUCCESS,
            message=message,
            data=data or {},
        )

    @classmethod
    def failure(cls, message: str, error: Exception = None) -> "SkillResult":
        return cls(
            status=SkillStatus.FAILED,
            message=message,
            error=error,
            trace=traceback.format_exc() if error else "",
        )

    @classmethod
    def partial(cls, message: str, data: Dict[str, Any]) -> "SkillResult":
        return cls(
            status=SkillStatus.PARTIAL_SUCCESS,
            message=message,
            data=data,
        )


@dataclass
class SkillContext:
    """
    技能执行上下文。

    包含执行技能所需的所有上下文信息。
    """
    # 窗口/应用上下文
    window: Any = None
    app: Any = None
    desktop_spec: Dict[str, Any] = field(default_factory=dict)

    # 全局状态
    variables: Dict[str, Any] = field(default_factory=dict)

    # 用户输入
    user_input: str = ""
    parsed_intent: Dict[str, Any] = field(default_factory=dict)

    # 执行历史
    action_history: List[Dict[str, Any]] = field(default_factory=list)

    # 配置项
    config: Dict[str, Any] = field(default_factory=dict)

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取变量值。"""
        return self.variables.get(key, default)

    def set_variable(self, key: str, value: Any) -> None:
        """设置变量值。"""
        self.variables[key] = value

    def record_action(self, action: str, result: Dict[str, Any]) -> None:
        """记录执行动作。"""
        self.action_history.append({
            "action": action,
            "result": result,
            "timestamp": datetime.now().isoformat(),
        })

    def get_last_action(self) -> Optional[Dict[str, Any]]:
        """获取上一个执行的动作。"""
        return self.action_history[-1] if self.action_history else None


class DesktopSkill(ABC):
    """
    桌面自动化技能抽象基类。

    所有具体的技能都应该继承此类并实现必要的方法。
    """

    # 技能元数据（子类应覆盖）
    skill_id: str = ""
    skill_name: str = ""
    skill_description: str = ""
    skill_version: str = "1.0.0"
    skill_author: str = ""
    skill_tags: List[str] = field(default_factory=list)

    # 意图模式（自然语言匹配）
    intent_patterns: List[str] = field(default_factory=list)

    # 所需参数定义
    required_params: List[str] = field(default_factory=list)
    optional_params: Dict[str, Any] = field(default_factory=dict)

    def __init__(self):
        # 确保子类正确设置了元数据
        if not self.skill_id:
            self.skill_id = self.__class__.__name__.lower().replace("skill", "")
        if not self.skill_name:
            self.skill_name = self.__class__.__name__

    @abstractmethod
    def can_handle(self, context: SkillContext) -> bool:
        """
        判断此技能是否能处理给定的上下文。

        Args:
            context: 执行上下文

        Returns:
            True 表示可以处理，False 表示不能处理
        """
        pass

    @abstractmethod
    def execute(self, context: SkillContext) -> SkillResult:
        """
        执行技能。

        Args:
            context: 执行上下文

        Returns:
            SkillResult 执行结果
        """
        pass

    def validate_params(self, context: SkillContext) -> List[str]:
        """
        验证所需参数是否存在。

        Returns:
            缺失的参数名列表，空列表表示所有必需参数都已提供
        """
        missing = []
        for param in self.required_params:
            if param not in context.variables and param not in context.parsed_intent.get("parameters", {}):
                missing.append(param)
        return missing

    def get_param(self, context: SkillContext, key: str, default: Any = None) -> Any:
        """从上下文中获取参数值。"""
        # 优先从 variables 获取
        if key in context.variables:
            return context.variables[key]
        # 其次从 parsed_intent.parameters 获取
        intent_params = context.parsed_intent.get("parameters", {})
        if key in intent_params:
            return intent_params[key]
        # 最后返回默认值
        return self.optional_params.get(key, default)

    def match_intent(self, user_input: str) -> float:
        """
        计算用户输入与技能的匹配分数。

        Returns:
            0.0-1.0 的匹配分数
        """
        if not self.intent_patterns:
            return 0.0

        input_lower = user_input.lower()
        scores = []

        for pattern in self.intent_patterns:
            pattern_lower = pattern.lower()
            # 完全匹配
            if input_lower == pattern_lower:
                scores.append(1.0)
            # 包含匹配
            elif pattern_lower in input_lower or input_lower in pattern_lower:
                scores.append(0.8)
            # 关键词匹配
            else:
                pattern_words = set(pattern_lower.split())
                input_words = set(input_lower.split())
                if pattern_words & input_words:
                    overlap = len(pattern_words & input_words)
                    total = len(pattern_words | input_words)
                    scores.append(0.6 * (overlap / total))

        return max(scores) if scores else 0.0

    def to_dict(self) -> Dict[str, Any]:
        """返回技能元数据字典。"""
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "skill_description": self.skill_description,
            "skill_version": self.skill_version,
            "skill_author": self.skill_author,
            "skill_tags": self.skill_tags,
            "intent_patterns": self.intent_patterns,
            "required_params": self.required_params,
            "optional_params": self.optional_params,
        }


class SkillRegistry:
    """
    技能注册表。

    管理所有可用技能的注册、发现和路由。
    """

    def __init__(self):
        self._skills: Dict[str, DesktopSkill] = {}
        self._skill_order: List[str] = []  # 注册顺序

    def register(self, skill_class: Type[DesktopSkill]) -> None:
        """
        注册一个技能类。

        Args:
            skill_class: 继承自 DesktopSkill 的类
        """
        try:
            instance = skill_class()
            skill_id = instance.skill_id

            if skill_id in self._skills:
                print(f"警告: 技能 '{skill_id}' 已存在，将被覆盖")

            self._skills[skill_id] = instance
            if skill_id not in self._skill_order:
                self._skill_order.append(skill_id)

        except Exception as e:
            print(f"注册技能失败: {skill_class.__name__}, 错误: {e}")

    def unregister(self, skill_id: str) -> bool:
        """注销一个技能。"""
        if skill_id in self._skills:
            del self._skills[skill_id]
            if skill_id in self._skill_order:
                self._skill_order.remove(skill_id)
            return True
        return False

    def get(self, skill_id: str) -> Optional[DesktopSkill]:
        """获取指定 ID 的技能实例。"""
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有已注册的技能元数据。"""
        return [skill.to_dict() for skill in self._skills.values()]

    def find_skill_for_intent(self, user_input: str, min_score: float = 0.3) -> Optional[Tuple[DesktopSkill, float]]:
        """
        根据用户输入找到最匹配的技能。

        Args:
            user_input: 用户输入的自然语言
            min_score: 最低匹配分数阈值

        Returns:
            (skill, score) 或 None
        """
        best_skill: Optional[DesktopSkill] = None
        best_score = 0.0

        for skill in self._skills.values():
            score = skill.match_intent(user_input)
            if score > best_score and score >= min_score:
                best_score = score
                best_skill = skill

        return (best_skill, best_score) if best_skill else None

    def find_skill_for_context(self, context: SkillContext) -> Optional[DesktopSkill]:
        """
        根据上下文找到能够处理该上下文的技能。

        遍历所有技能，返回第一个 can_handle 返回 True 的技能。
        """
        for skill_id in self._skill_order:
            skill = self._skills[skill_id]
            try:
                if skill.can_handle(context):
                    return skill
            except Exception:
                continue
        return None

    def execute_skill(
        self,
        skill_id: str,
        context: SkillContext,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> SkillResult:
        """
        执行指定技能。

        Args:
            skill_id: 技能 ID
            context: 执行上下文
            on_progress: 进度回调函数

        Returns:
            SkillResult 执行结果
        """
        import time

        skill = self._skills.get(skill_id)
        if not skill:
            return SkillResult.failure(f"未找到技能: {skill_id}")

        # 验证参数
        missing = skill.validate_params(context)
        if missing:
            return SkillResult.failure(f"缺少必需参数: {', '.join(missing)}")

        start_time = time.time()
        try:
            if on_progress:
                on_progress(f"开始执行技能: {skill.skill_name}")

            result = skill.execute(context)
            result.duration_sec = time.time() - start_time

            if on_progress:
                on_progress(f"技能执行完成: {skill.skill_name}, 状态: {result.status.name}")

            return result

        except Exception as e:
            return SkillResult.failure(
                f"技能执行异常: {str(e)}",
                error=e,
            )

    def auto_route_and_execute(
        self,
        user_input: str,
        context: SkillContext,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> SkillResult:
        """
        自动路由并执行技能。

        根据用户输入自动找到最匹配的技能并执行。

        Args:
            user_input: 用户输入
            context: 执行上下文
            on_progress: 进度回调

        Returns:
            SkillResult 执行结果
        """
        # 更新上下文
        context.user_input = user_input

        # 1. 尝试通过意图匹配
        matched = self.find_skill_for_intent(user_input)
        if matched:
            skill, score = matched
            if on_progress:
                on_progress(f"匹配到技能 '{skill.skill_name}' (置信度: {score:.2f})")
            return self.execute_skill(skill.skill_id, context, on_progress)

        # 2. 尝试通过上下文匹配
        skill = self.find_skill_for_context(context)
        if skill:
            if on_progress:
                on_progress(f"通过上下文匹配到技能 '{skill.skill_name}'")
            return self.execute_skill(skill.skill_id, context, on_progress)

        return SkillResult.failure("无法找到匹配的技能来处理该请求")


# 全局注册表实例
_global_registry: Optional[SkillRegistry] = None


def get_global_registry() -> SkillRegistry:
    """获取全局技能注册表实例。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
    return _global_registry


def register_skill(skill_class: Type[DesktopSkill]) -> None:
    """
    注册技能到全局注册表。
    """
    registry = get_global_registry()
    registry.register(skill_class)


def list_all_skills() -> List[Dict[str, Any]]:
    """列出所有已注册的技能。"""
    return get_global_registry().list_skills()


def execute_skill_by_id(
    skill_id: str,
    context: SkillContext,
    on_progress: Optional[Callable[[str], None]] = None,
) -> SkillResult:
    """通过 ID 执行技能。"""
    return get_global_registry().execute_skill(skill_id, context, on_progress)


def auto_execute(
    user_input: str,
    context: SkillContext,
    on_progress: Optional[Callable[[str], None]] = None,
) -> SkillResult:
    """自动路由并执行技能。"""
    return get_global_registry().auto_route_and_execute(user_input, context, on_progress)
