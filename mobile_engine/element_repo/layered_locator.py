# -*- coding: utf-8 -*-
"""
分层定位策略引擎。

实现优先级级联的定位符回退机制:
  Level 1: text / id → 精确匹配 (Maestro 原生)
  Level 2: semantic → 语义描述 (Maestro AI)
  Level 3: relative → 相对位置
  Level 4: visual → AI/OpenCV 视觉匹配

策略模式:
  - strict: 仅用最高优先级定位符
  - cascade: 按优先级级联回退 (默认)
  - ai_first: AI 视觉优先
"""

from __future__ import annotations

from typing import List, Optional

from mobile_engine.engine_interface import (
    DeviceInfo,
    FlowStep,
    LocatorInfo,
    LocatorStrategy,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class LayeredLocator:
    """分层定位策略引擎"""

    def __init__(self, strategy: str = "cascade"):
        """
        Args:
            strategy: 策略模式
              - "strict": 仅用最高优先级
              - "cascade": 级联回退
              - "ai_first": AI 优先
        """
        self._strategy = strategy

    # ------------------------------------------------------------------
    # 策略获取
    # ------------------------------------------------------------------

    def get_locator_chain(self, locator: LocatorInfo) -> List[LocatorInfo]:
        """
        获取定位符的级联回退链。

        Returns:
            按优先级排序的 LocatorInfo 列表 (优先级高的在前)
        """
        chain = [locator]

        if self._strategy == "strict":
            return chain

        if self._strategy == "ai_first":
            # AI 优先 (semantic + visual)
            ai = [
                LocatorInfo(
                    strategy=LocatorStrategy.SEMANTIC,
                    value=locator.semantic_desc or locator.value,
                    semantic_desc=locator.semantic_desc or locator.value,
                ),
                LocatorInfo(
                    strategy=LocatorStrategy.VISUAL,
                    value=locator.value,
                    visual_template_path=locator.visual_template_path,
                ),
            ]
            chain = ai + [locator]
            return chain

        # cascade: 按优先级添加备选策略
        fallback_order = [
            LocatorStrategy.TEXT,
            LocatorStrategy.ID,
            LocatorStrategy.SEMANTIC,
            LocatorStrategy.RELATIVE,
            LocatorStrategy.VISUAL,
            LocatorStrategy.COORDINATE,
        ]

        for strat in fallback_order:
            if strat == locator.strategy:
                continue
            fb = LocatorInfo(
                strategy=strat,
                value=locator.value,
                semantic_desc=locator.semantic_desc or locator.value,
                visual_template_path=locator.visual_template_path,
                relative_direction=locator.relative_direction,
                relative_target=locator.relative_target,
            )
            chain.append(fb)

        return chain

    def enhance_step(self, step: FlowStep) -> FlowStep:
        """
        为步骤的 LocatorInfo 添加 fallback 值。
        (不影响 Maestro 原生执行，但供自愈阶段使用)
        """
        if not step.locator:
            return step

        chain = self.get_locator_chain(step.locator)
        if chain:
            fallback_values = [
                c.value for c in chain[1:] if c.value
            ]
            step.locator.fallback_values = fallback_values
        return step

    # ------------------------------------------------------------------
    # 元素仓库集成
    # ------------------------------------------------------------------

    def resolve_from_repo(
        self,
        project_id: int,
        alias: str,
        platform: str = "android",
    ) -> Optional[LocatorInfo]:
        """
        从元素仓库解析定位符 (含缓存的自愈候选项)。
        """
        from mobile_engine.element_repo.element_repository import ElementRepository

        repo = ElementRepository()
        return repo.resolve_locator(project_id, alias, platform)

    # ------------------------------------------------------------------
    # 兼容现有 database 步骤
    # ------------------------------------------------------------------

    @staticmethod
    def from_db_step(step: dict) -> LocatorInfo:
        """从数据库 test_steps 记录的 selector_type/selector_value 构建 LocatorInfo"""
        sel_type = (step.get("selector_type") or step.get("strategy") or "").strip()
        sel_value = (step.get("selector_value") or "").strip()

        if not sel_value:
            return None

        # 映射 selector_type → LocatorStrategy
        type_map = {
            "text": LocatorStrategy.TEXT,
            "name": LocatorStrategy.TEXT,
            "id": LocatorStrategy.ID,
            "css": LocatorStrategy.ID,
            "accessibility_id": LocatorStrategy.ACCESSIBILITY_ID,
            "xpath": LocatorStrategy.SEMANTIC,
            "semantic": LocatorStrategy.SEMANTIC,
            "visual": LocatorStrategy.VISUAL,
            "visual_template": LocatorStrategy.VISUAL,
            "coord": LocatorStrategy.COORDINATE,
            "coordinates": LocatorStrategy.COORDINATE,
            "viewport_coord": LocatorStrategy.COORDINATE,
        }

        strat = type_map.get(sel_type, LocatorStrategy.ACCESSIBILITY_ID)
        return LocatorInfo(
            strategy=strat,
            value=sel_value,
            semantic_desc=step.get("description", ""),
            visual_template_path=step.get("visual_template_path", ""),
        )


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_default_locator: Optional[LayeredLocator] = None


def get_layered_locator(strategy: str = "cascade") -> LayeredLocator:
    """获取分层定位器实例"""
    from mobile_env_config import layered_locator_strategy

    global _default_locator
    if _default_locator is None:
        active = strategy or layered_locator_strategy()
        _default_locator = LayeredLocator(strategy=active)
    return _default_locator
