# -*- coding: utf-8 -*-
"""
自愈定位器。

当 Maestro 步骤失败时:
1. 截取当前屏幕
2. 逐级回退尝试定位
3. 若匹配成功 → 执行操作 → 记录新的定位符
4. 持久化到 element_repository

分层策略 (按优先级):
  Level 1: text / id → 精确匹配
  Level 2: semantic → 语义描述 (AI 匹配)
  Level 3: relative → 相对位置
  Level 4: visual → AI/OpenCV 视觉匹配
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from mobile_engine.engine_interface import (
    DeviceInfo,
    FlowStep,
    LocatorInfo,
    LocatorStrategy,
    StepResult,
    StepStatus,
)

try:
    from uat_logger import uat_logger
except ImportError:
    import logging

    uat_logger = logging.getLogger(__name__)


class VisualHealer:
    """
    自愈定位器 — Maestro 执行失败时自动触发视觉兜底修复。
    """

    def __init__(self):
        self._heal_records: List[Dict[str, Any]] = []
        self._visual_adapter = None  # 延迟初始化

    def _get_visual(self):
        if self._visual_adapter is None:
            from mobile_engine.visual.visual_adapter import VisualFallbackAdapter

            self._visual_adapter = VisualFallbackAdapter()
        return self._visual_adapter

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def try_heal(
        self,
        failed_step: FlowStep,
        device: DeviceInfo,
        failed_result: StepResult,
    ) -> Optional[StepResult]:
        """
        尝试自愈一个失败步骤。

        Args:
            failed_step: 失败的步骤
            device: 设备信息
            failed_result: Maestro 返回的失败结果

        Returns:
            成功则返回 StepResult (status=SUCCESS + healed_locator),
            失败则返回 None
        """
        from modules.mobile.mobile_env_config import self_healing_enabled

        if not self_healing_enabled():
            return None

        if not failed_step.locator:
            uat_logger.debug("自愈跳过: 步骤无定位符")
            return None

        uat_logger.info("自愈尝试: action=%s locator=%s:%s",
                        failed_step.action,
                        failed_step.locator.strategy,
                        failed_step.locator.value)

        visual = self._get_visual()
        if not visual._device:
            visual.connect_device(device)

        # 按分层策略逐级尝试
        strategies = self._get_fallback_strategies(failed_step.locator)

        for strategy in strategies:
            attempt_locator = LocatorInfo(
                strategy=strategy,
                value=failed_step.locator.value,
                semantic_desc=failed_step.locator.semantic_desc or failed_step.locator.value,
                visual_template_path=failed_step.locator.visual_template_path,
            )

            uat_logger.debug("自愈尝试策略: %s", strategy)
            x, y, conf = visual.find_element_visual(attempt_locator)

            if conf >= 0.6:  # 自愈使用稍低的置信度阈值
                uat_logger.info("自愈成功: strategy=%s conf=%.2f pos=(%d,%d)",
                                strategy, conf, x, y)

                # 执行原始操作
                healed_result = self._execute_healed_action(
                    failed_step, visual, x, y, conf,
                )

                # 记录自愈信息
                healed_result.healed_locator = attempt_locator
                self._heal_records.append({
                    "original_locator": {
                        "strategy": failed_step.locator.strategy,
                        "value": failed_step.locator.value,
                    },
                    "healed_strategy": strategy,
                    "confidence": conf,
                    "position": (x, y),
                })

                return healed_result

        uat_logger.warning("自愈失败: 所有策略均未匹配")
        return None

    def get_heal_records(self) -> List[Dict[str, Any]]:
        """获取本次执行的所有自愈记录"""
        return self._heal_records

    def get_heal_count(self) -> int:
        """获取自愈成功次数"""
        return len([r for r in self._heal_records if r.get("confidence", 0) > 0])

    def clear_records(self) -> None:
        """清空自愈记录"""
        self._heal_records.clear()

    def persist_healed_locator(
        self,
        project_id: int,
        alias: str,
        healed_locator: LocatorInfo,
        confidence: float,
    ) -> bool:
        """
        将自愈后的定位符持久化到 element_repository 表。

        Args:
            project_id: 项目 ID
            alias: 元素别名
            healed_locator: 自愈后的定位符
            confidence: 匹配置信度

        Returns:
            是否保存成功
        """
        try:
            from database import Database

            db = Database()
            conn = db._sqlite_connect()
            cursor = conn.cursor()

            import json

            candidates = json.dumps([{
                "strategy": healed_locator.strategy,
                "value": healed_locator.value,
                "confidence": confidence,
                "last_used": time.strftime("%Y-%m-%d %H:%M:%S"),
            }])

            cursor.execute(
                """UPDATE element_repository
                   SET heuristic_selector = ?,
                       locator_candidates = ?,
                       last_success_at = CURRENT_TIMESTAMP,
                       success_count = COALESCE(success_count, 0) + 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE project_id = ? AND alias = ? AND platform = 'android'""",
                (healed_locator.value, candidates, project_id, alias),
            )
            conn.commit()
            conn.close()
            return cursor.rowcount > 0
        except Exception as exc:
            uat_logger.error("持久化自愈定位符失败: %s", exc)
            return False

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _get_fallback_strategies(original: LocatorInfo) -> List[str]:
        """
        获取回退策略列表 (按优先级排序)。
        排除原始策略后，返回剩余的备选策略。
        """
        all_strategies = [
            LocatorStrategy.TEXT,
            LocatorStrategy.ID,
            LocatorStrategy.SEMANTIC,
            LocatorStrategy.VISUAL,
            LocatorStrategy.COORDINATE,
        ]

        priorities = {
            LocatorStrategy.TEXT: 1,
            LocatorStrategy.ID: 1,
            LocatorStrategy.ACCESSIBILITY_ID: 1,
            LocatorStrategy.SEMANTIC: 2,
            LocatorStrategy.RELATIVE: 3,
            LocatorStrategy.VISUAL: 4,
            LocatorStrategy.COORDINATE: 5,
        }

        # 按优先级排序，跳过原始策略
        ranked = sorted(
            [s for s in all_strategies if s != original.strategy],
            key=lambda s: priorities.get(s, 99),
        )
        return ranked

    def _execute_healed_action(
        self,
        step: FlowStep,
        visual,
        x: int,
        y: int,
        confidence: float,
    ) -> StepResult:
        """执行自愈后的操作"""
        action = step.action.strip().lower()

        if action == "tap":
            visual.tap_coordinates(x, y)
            return StepResult(
                status=StepStatus.SUCCESS,
                action="tap",
                match_confidence=confidence,
            )

        elif action in ("input", "input_text"):
            visual.tap_coordinates(x, y)
            time.sleep(0.15)
            visual._adb_input_text(step.input_value or "")
            return StepResult(
                status=StepStatus.SUCCESS,
                action="input",
                match_confidence=confidence,
            )

        elif action == "long_press":
            # 长按 = tap + hold (通过 ADB swipe with same start/end)
            visual._adb_swipe("up", step.swipe_duration_ms or 600)
            return StepResult(
                status=StepStatus.SUCCESS,
                action="long_press",
                match_confidence=confidence,
            )

        elif action == "assert":
            if confidence >= 0.7:
                return StepResult(
                    status=StepStatus.SUCCESS,
                    action="assert",
                    match_confidence=confidence,
                )
            else:
                return StepResult(
                    status=StepStatus.FAILED,
                    action="assert",
                    error=f"视觉匹配置信度不足 ({confidence:.2f})",
                    match_confidence=confidence,
                )

        else:
            return StepResult(
                status=StepStatus.FAILED,
                action=action,
                error=f"自愈不支持的动作: {action}",
                match_confidence=confidence,
            )


# ------------------------------------------------------------------
# 便捷工厂函数
# ------------------------------------------------------------------

_default_healer: Optional[VisualHealer] = None


def get_visual_healer() -> VisualHealer:
    global _default_healer
    if _default_healer is None:
        _default_healer = VisualHealer()
    return _default_healer
