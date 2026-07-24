# -*- coding: utf-8 -*-
"""跨端执行错误码 → 用户可读提示（前端日志 / API 均可复用）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

# error_code → (短标题, 怎么办)
_HINTS: Dict[str, tuple[str, str]] = {
    "EMPTY_SELECTOR": (
        "缺少页面元素定位",
        "请在 Web 步骤填写 selector（如 #login-btn 或 button:has-text(\"登录\")），"
        "或在场景描述里写清要点击/输入的控件名称后重新 AI 分解。",
    ),
    "EMPTY_URL": (
        "缺少打开地址",
        "请为导航步骤填写 url，或勾选允许跳过（allow_skip）仅用于可选导航。",
    ),
    "EMPTY_ASSERT": (
        "断言不完整",
        "请补充要检查的元素 selector，或填写预期文本/URL 关键字。",
    ),
    "EMPTY_SELECT_VALUE": (
        "下拉选项值为空",
        "请为 select 步骤填写要选择的选项值（value）。",
    ),
    "EMPTY_ACTION": (
        "步骤缺少动作类型",
        "每个 Web 步骤需要 action（如 click / input / navigate / assert）。",
    ),
    "UNKNOWN_ACTION": (
        "不支持的操作类型",
        "请改用平台已支持的动作：navigate、click、input、select、wait、assert、extract_text、screenshot。",
    ),
    "NO_BROWSER_PAGE": (
        "浏览器未就绪",
        "请先在本机启动并连接浏览器（CDP），确认预览页已打开后再执行跨端 Web 阶段。",
    ),
    "SYNC_DATA_TIMEOUT": (
        "等待变量超时",
        "下游需要的变量尚未产生。请检查上一阶段是否成功抽取（vars_to_store），"
        "以及 vars_to_read 名称是否与抽取名一致。",
    ),
    "SYNC_DATA_EMPTY_KEYS": (
        "同步配置不完整",
        "data_sync / vars_to_read 未声明要等待的变量名。",
    ),
    "SYNC_API_TIMEOUT": (
        "等待接口状态超时",
        "轮询接口未达到目标状态。请确认接口地址、json_path 与期望值是否正确，或加大 timeout_s。",
    ),
    "SYNC_API_NO_REQUEST": (
        "接口同步缺少请求",
        "api_state_sync 需要配置 request（method + url）。",
    ),
    "SYNC_UI_NO_PAGE": (
        "界面同步需要浏览器",
        "state_sync 使用了页面 selector，但当前没有可用浏览器页面。",
    ),
    "SYNC_UI_NO_TARGET": (
        "界面同步目标不完整",
        "state_sync 请提供 variable（上下文变量条件）或 selector（页面元素）。",
    ),
    "SYNC_UI_VAR_TIMEOUT": (
        "界面条件未满足",
        "等待的上下文变量条件在超时前未变为期望值。",
    ),
    "SYNC_UI_SELECTOR_TIMEOUT": (
        "页面元素未出现",
        "等待的 selector 在超时前不可见。请检查选择器或页面是否已加载。",
    ),
    "SYNC_HITL_FAILED": (
        "人工确认未完成",
        "人机协作门禁超时或已取消。如需继续，请在提示出现后点击「继续」。",
    ),
    "HITL_TIMEOUT_OR_CANCEL": (
        "需要人工确认，但已超时或被取消",
        "请处理验证码/登录后点「继续」，或在阶段配置中加大 timeout_s。超时与取消均记失败，不会假绿。",
    ),
    "HITL_TIMEOUT": (
        "人工确认超时",
        "请在时限内完成验证码/登录并继续；超时按失败计入历史与 Trace。",
    ),
    "HITL_CANCELLED": (
        "人工确认已取消",
        "流程按失败结束，不会记为成功。",
    ),
    "HITL_ERROR": (
        "人工确认门禁异常",
        "请查看平台日志后重试。",
    ),
    "RISK_APPROVAL_REQUIRED": (
        "高风险动作待审批",
        "该阶段为 L2，需审批令牌后才能执行。请在 RiskGuard 批准后重试，或把 approval_token 写入计划。",
    ),
    "RISK_TOKEN_INVALID": (
        "审批令牌无效",
        "令牌不存在、已拒绝或未批准。请重新申请 L2 审批。",
    ),
    "RISK_TOKEN_STAGE_MISMATCH": (
        "审批令牌与阶段不匹配",
        "该令牌绑定了其他阶段，不能用于当前动作。",
    ),
    "RISK_DENIED": (
        "RiskGuard 拒绝执行",
        "安全策略阻止了本阶段。请检查风险等级与审批状态。",
    ),
    "DESKTOP_SOFT_FAIL": (
        "桌面步骤未真正成功",
        "含 status=warning 或未核验命中。跨端不会记为通过；请检查目标窗口与控件。",
    ),
    "DESKTOP_STEP_FAILED": (
        "桌面步骤执行失败",
        "请确认 Desktop Gateway 可用，并用 UIA/视觉重新定位控件。",
    ),
    "DESKTOP_NO_SESSION": (
        "桌面会话不可用",
        "请先启动智能体或 Desktop Gateway，再执行 desktop 阶段。",
    ),
    "SYNC_UNKNOWN_TYPE": (
        "未知同步类型",
        "支持：data_sync、api_state_sync、state_sync、time_sync、human_sync。",
    ),
    "SYNC_FAILED": (
        "阶段同步未通过",
        "执行前同步门禁失败，本阶段未开始执行业务步骤。",
    ),
    "DEPENDS_ON_UNSATISFIED": (
        "依赖阶段未通过",
        "本阶段 depends_on 指向的上游同步点尚未成功完成（含被跳过的失败）。请先修复上游阶段。",
    ),
    "RECOVERY_SKIP_BLOCKS_SUCCESS": (
        "存在跳过的失败阶段",
        "部分阶段失败后按策略跳过继续跑完，但默认不算整体成功。"
        "若业务允许，可在计划中设置 allow_skipped_failures=true。",
    ),
    "VAR_EXTRACT_MISSING": (
        "变量抽取失败",
        "声明要保存的变量未取到非空值。请检查选择器 / JSONPath，或将字段标为非必选。",
    ),
    "CROSS_END_ASSERT_FAILED": (
        "跨端断言未通过",
        "多端数据不一致，或某个来源变量/页面元素未读到。请检查上一阶段抽取名、UI 选择器，或断言 sources 配置。",
    ),
    "ASSERT_SOURCE_MISSING": (
        "断言来源缺失",
        "声明的 API/UI 变量尚未产生，或浏览器未打开导致无法读取页面。请先保证上游阶段成功并抽出变量。",
    ),
    "HERMES_UNAVAILABLE": (
        "Hermes 执行器不可用",
        "本阶段指定了 Hermes，但 Gateway 未配置或未就绪。请先启动 Hermes，或去掉 executor/use_hermes 改用经典步骤执行。",
    ),
    "HERMES_FAILED": (
        "Hermes 未明确成功",
        "Agent 回复未包含 [RESULT] ok。请检查浏览器/设备是否就绪，或改用带明确 selector 的经典步骤。",
    ),
    "ALL_STEPS_SKIPPED": (
        "阶段步骤全部被跳过",
        "没有实际执行任何步骤。请补全 URL/选择器，或仅对可选步骤设置 allow_skip。",
    ),
    "EXECUTION_LOCK_BUSY": (
        "本机正忙",
        "已有自动化任务在执行。请等待当前任务结束，或在执行锁释放后再试。",
    ),
    "EXECUTION_LOCK_UNAVAILABLE": (
        "执行锁不可用",
        "本机执行互斥组件未能加载。请检查平台安装与日志后重试。",
    ),
}


def user_hint_for_code(error_code: Optional[str]) -> Optional[str]:
    code = str(error_code or "").strip()
    if not code or code not in _HINTS:
        return None
    title, tip = _HINTS[code]
    return f"{title}：{tip}"


def enrich_result_with_user_hint(result: Dict[str, Any]) -> Dict[str, Any]:
    """就地补充 user_hint；阶段结果列表同样处理。"""
    if not isinstance(result, dict):
        return result
    code = result.get("error_code")
    hint = user_hint_for_code(code if isinstance(code, str) else None)
    if hint and not result.get("user_hint"):
        result["user_hint"] = hint
    stages = result.get("stage_results")
    if isinstance(stages, list):
        for sr in stages:
            if isinstance(sr, dict):
                sc = sr.get("error_code")
                sh = user_hint_for_code(sc if isinstance(sc, str) else None)
                if sh and not sr.get("user_hint"):
                    sr["user_hint"] = sh
                # 无 error_code 时，从常见中文 error 推断
                elif not sr.get("user_hint") and sr.get("ok_assert") is False:
                    inferred = _infer_code_from_error(str(sr.get("error") or ""))
                    if inferred:
                        sr["error_code"] = sr.get("error_code") or inferred
                        sr["user_hint"] = user_hint_for_code(inferred)
    return result


def _infer_code_from_error(msg: str) -> Optional[str]:
    m = msg.lower()
    if "selector" in m or "选择器" in msg:
        return "EMPTY_SELECTOR"
    if "url" in m and ("空" in msg or "empty" in m or "跳过占位" in msg):
        return "EMPTY_URL"
    if "浏览器" in msg or "no_browser" in m:
        return "NO_BROWSER_PAGE"
    if "依赖同步" in msg or "depends_on" in m:
        return "DEPENDS_ON_UNSATISFIED"
    if "data_sync" in m or "缺失变量" in msg:
        return "SYNC_DATA_TIMEOUT"
    return None
