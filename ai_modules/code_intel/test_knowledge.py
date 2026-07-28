# -*- coding: utf-8 -*-
"""自动化测试知识：稳定选择器策略、反脆弱用例设计（注入 LLM 系统提示）。"""

from __future__ import annotations

AUTOMATION_KNOWLEDGE_PROMPT = """
你是资深 UI 自动化测试架构师，专长从前端代码生成**可靠、稳定、可维护**的 Web 自动化用例。

## 选择器稳定性铁律（必须遵守，按优先级）

1. **最高优先**：`data-testid` / `data-cy` / `data-qa` → selector_type=css，selector_value=`[data-testid="..."]`
2. **次优**：无障碍 role + accessible name（按钮/链接/文本框）
3. **再次**：`aria-label`、表单 `name`、有意义的 `placeholder`
4. **谨慎**：可见文案（text）——必须在 description 标明「视觉可定位，文案变更需自愈」
5. **禁止作为主定位**：
   - 绝对 XPath（/html/body/...）
   - 脆弱 class（css-module hash、emotion 随机类）
   - nth-child / 纯下标
   - 屏幕坐标 / 图像模板（除非无 DOM）

## 用例设计知识

- 每条用例职责单一：一个业务意图（登录成功 / 提交订单校验等），避免「巨型脚本」
- 步骤遵循：准备(navigate/前置) → 操作(click/input) → 等待(必要时) → 断言(assert)
- **显式断言**：每个关键操作后要有可验证结果（可见、文案、URL、禁用态）
- 分支覆盖：代码中有 v-if / 条件渲染 / disabled 时，至少覆盖主路径 + 1 条受限/失败路径
- 表单：按字段顺序填写；提交按钮用稳定定位；校验错误文案可 assert
- Dialog/Modal：先断言弹层出现，再操作内部控件，最后断言关闭或结果
- Table：优先行内 testid 或 role=row + 文本；避免「第 N 行」硬编码除非业务如此
- 等待：优先断言可见/可点，禁止固定 sleep 作为主路径

## 平台步骤约定（Testory）

- automation_layer 默认 `web`
- action 使用：navigate | click | input | wait | assert | select | hover
- **navigate / case_url 仅在用户提供了 URL 或代码中有明确路由时使用；禁止编造域名**
- description 可用语义工具描述：`browser_click_element(description="...")`、`browser_fill(...)`
- 不要写死像素坐标
- 每条用例 description 必须以 `[待审核][由代码自动生成][review_status:pending]` 开头
- 在 description 注明 `source_commit=`（若有）与选用定位策略（testid/role/text）

## 质量门槛（达不到则宁缺毋滥）

- 无任何高/中稳定性定位线索的节点：可跳过或仅生成「人工补定位」占位，并 warnings 说明
- 不要编造代码中不存在的 testid、文案、路由
- 输出严格 JSON，不要 markdown
""".strip()


SELECTOR_STRATEGY_ORDER = (
    "testid",
    "role_name",
    "aria_label",
    "name",
    "placeholder",
    "id",
    "text",
    "css_class",
)


def score_locator_stability(strategy: str) -> str:
    s = (strategy or "").lower()
    if s in ("testid", "role_name"):
        return "high"
    if s in ("aria_label", "name", "placeholder", "id"):
        return "medium"
    return "low"


def step_from_locator(
    *,
    action: str,
    locator: Dict[str, Any],
    description: str = "",
    input_value: str = "",
    accessible_name: str = "",
) -> Dict[str, Any]:
    """把解析出的 best_locator 转为平台步骤。"""
    strategy = str(locator.get("strategy") or "")
    st = str(locator.get("selector_type") or "css")
    sv = str(locator.get("selector_value") or "")
    stability = str(locator.get("stability") or score_locator_stability(strategy))
    name = accessible_name or sv
    if strategy == "testid" and "data-testid" in sv:
        desc = description or f'browser_click_element(description="testid:{name}")'
        if action == "input":
            desc = description or f'browser_fill(description="testid:{name}")'
    elif strategy == "role_name":
        desc = description or f'browser_click_element(description="{name}")'
    elif stability == "low":
        desc = (description or f"操作 {name}") + " 【视觉可定位，文案变更需复核】"
    else:
        desc = description or f"操作元素 {name}"

    step = {
        "action": action,
        "selector_type": "css" if st == "role" else st,
        "selector_value": sv,
        "input_value": input_value,
        "description": desc[:2000],
        "automation_layer": "web",
        "locator_stability": stability,
        "locator_strategy": strategy,
    }
    # role 策略：用可见名作文案定位，description 保留语义
    if st == "role":
        import re
        m = re.search(r'name="([^"]+)"', sv)
        step["selector_type"] = "text"
        step["selector_value"] = m.group(1) if m else (accessible_name or "")
    return step
