# -*- coding: utf-8 -*-
"""跨端场景模板库：行业模板 + 参数化实例化。

内置模板覆盖常见行业场景，用户可通过参数化快速创建自定义计划。
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_BUILTIN_TEMPLATES: List[Dict[str, Any]] = [
    # ---- 1. 电商：API 创建订单 + Web 验证 + 手机通知 ----
    {
        "template_id": "ecommerce-order-verify",
        "name": "电商下单验证",
        "industry": "电商",
        "description": "API 创建订单 → Web 页面验证订单详情 → 手机推送通知确认",
        "difficulty": "medium",
        "tags": ["电商", "订单", "通知", "API", "Web", "Mobile"],
        "parameters": [
            {"name": "api_base_url", "type": "string", "required": True, "description": "API 基础 URL"},
            {"name": "product_id", "type": "string", "required": True, "description": "商品 ID"},
            {"name": "web_order_url", "type": "string", "required": True, "description": "订单详情页 URL"},
            {"name": "order_amount", "type": "string", "required": False, "description": "预期金额", "default": "99.00"},
        ],
        "plan_template": {
            "schema_version": "1.0",
            "scenario": "电商下单验证：API 创建 → Web 确认 → 手机通知",
            "stages": [
                {
                    "id": "stage-1-create-order",
                    "layer": "api",
                    "label": "API 创建测试订单",
                    "request": {"method": "POST", "url": "{{api_base_url}}/api/orders", "headers": {"Content-Type": "application/json"}, "body": {"product_id": "{{product_id}}", "quantity": 1}},
                    "assert": {"status": 201},
                    "extract": {"order_id": {"json_path": "$.data.order_id", "type": "string"}},
                    "timeout_seconds": 30,
                },
                {
                    "id": "stage-2-web-verify",
                    "layer": "web",
                    "label": "Web 页面验证订单",
                    "depends_on": ["stage-1-create-order"],
                    "steps": [
                        {"action": "navigate", "url": "{{web_order_url}}?order_id={{order_id}}"},
                        {"action": "assert", "selector": ".order-amount", "input_value": "{{order_amount}}"},
                        {"action": "assert", "selector": ".order-status", "input_value": "待支付"},
                    ],
                    "timeout_seconds": 60,
                },
                {
                    "id": "stage-3-mobile-notify",
                    "layer": "mobile",
                    "label": "手机验证推送通知",
                    "depends_on": ["stage-1-create-order"],
                    "skill": "extract_otp",
                    "await_device_run": True,
                    "await_timeout_sec": 60,
                    "steps": [{"action": "extract_otp", "description": "等待手机收到订单通知"}],
                },
                {
                    "id": "cleanup",
                    "layer": "api",
                    "label": "清理测试订单",
                    "cleanup": True,
                    "on_failure": "continue",
                    "request": {"method": "DELETE", "url": "{{api_base_url}}/api/orders/{{order_id}}"},
                    "assert": {"status_in": [200, 204, 404]},
                },
            ],
        },
    },

    # ---- 2. 金融：API 转账 + 桌面 ERP 确认 + 手机 OTP ----
    {
        "template_id": "finance-transfer-otp",
        "name": "金融转账 OTP 验证",
        "industry": "金融",
        "description": "API 发起转账 → 桌面 ERP 审批 → 手机取 OTP → API 确认",
        "difficulty": "hard",
        "tags": ["金融", "转账", "OTP", "ERP", "Desktop", "Mobile"],
        "parameters": [
            {"name": "api_base_url", "type": "string", "required": True, "description": "API 基础 URL"},
            {"name": "from_account", "type": "string", "required": True, "description": "转出账号"},
            {"name": "to_account", "type": "string", "required": True, "description": "转入账号"},
            {"name": "amount", "type": "string", "required": True, "description": "转账金额"},
            {"name": "erp_app_name", "type": "string", "required": False, "description": "ERP 应用名", "default": "财务系统"},
        ],
        "plan_template": {
            "schema_version": "1.0",
            "scenario": "金融转账：API 发起 → ERP 审批 → OTP 确认",
            "stages": [
                {
                    "id": "stage-1-initiate",
                    "layer": "api",
                    "label": "API 发起转账请求",
                    "request": {"method": "POST", "url": "{{api_base_url}}/api/transfers", "body": {"from": "{{from_account}}", "to": "{{to_account}}", "amount": "{{amount}}"}},
                    "assert": {"status": 200},
                    "extract": {"transfer_id": {"json_path": "$.transfer_id", "type": "string"}, "otp_sent": {"json_path": "$.otp_sent", "type": "boolean"}},
                    "timeout_seconds": 30,
                },
                {
                    "id": "stage-2-erp-approve",
                    "layer": "desktop",
                    "label": "桌面 ERP 审批转账",
                    "depends_on": ["stage-1-initiate"],
                    "steps": [
                        {"action": "launch_app", "input_value": "{{erp_app_name}}"},
                        {"action": "click", "selector_value": "待审批"},
                        {"action": "click", "selector_value": "{{transfer_id}}"},
                        {"action": "click", "selector_value": "批准"},
                    ],
                    "timeout_seconds": 120,
                },
                {
                    "id": "stage-3-mobile-otp",
                    "layer": "mobile",
                    "label": "手机提取 OTP",
                    "depends_on": ["stage-1-initiate"],
                    "skill": "extract_otp",
                    "await_device_run": True,
                    "await_timeout_sec": 120,
                    "extract": {"sms_otp": {"required": True}},
                    "steps": [{"action": "extract_otp", "store_as": "sms_otp"}],
                },
                {
                    "id": "stage-4-confirm",
                    "layer": "api",
                    "label": "API 确认转账（OTP）",
                    "depends_on": ["stage-2-erp-approve", "stage-3-mobile-otp"],
                    "request": {"method": "POST", "url": "{{api_base_url}}/api/transfers/{{transfer_id}}/confirm", "body": {"otp": "{{sms_otp}}"}},
                    "assert": {"status": 200},
                    "extract": {"final_status": {"json_path": "$.status", "type": "string"}},
                    "timeout_seconds": 30,
                },
                {
                    "id": "cleanup",
                    "layer": "api",
                    "label": "清理测试转账",
                    "cleanup": True,
                    "on_failure": "continue",
                    "request": {"method": "DELETE", "url": "{{api_base_url}}/api/transfers/{{transfer_id}}"},
                    "assert": {"status_in": [200, 204, 404]},
                },
            ],
        },
    },

    # ---- 3. 社交：Web 注册 + 手机验证码 + 桌面客户端登录 ----
    {
        "template_id": "social-register-login",
        "name": "社交平台注册登录",
        "industry": "社交",
        "description": "Web 注册账号 → 手机取验证码 → Web 完成注册 → 桌面客户端登录验证",
        "difficulty": "medium",
        "tags": ["社交", "注册", "验证码", "登录", "Web", "Mobile", "Desktop"],
        "parameters": [
            {"name": "register_url", "type": "string", "required": True, "description": "注册页面 URL"},
            {"name": "phone_number", "type": "string", "required": True, "description": "测试手机号"},
            {"name": "password", "type": "string", "required": True, "description": "测试密码"},
            {"name": "desktop_app_name", "type": "string", "required": False, "description": "桌面客户端名称", "default": "社交App"},
        ],
        "plan_template": {
            "schema_version": "1.0",
            "scenario": "社交注册登录：Web 注册 → 手机验证码 → 桌面登录",
            "stages": [
                {
                    "id": "stage-1-web-register",
                    "layer": "web",
                    "label": "Web 填写注册表单",
                    "steps": [
                        {"action": "navigate", "url": "{{register_url}}"},
                        {"action": "input", "selector": "#phone", "input_value": "{{phone_number}}"},
                        {"action": "click", "selector": "#send-code-btn"},
                    ],
                    "timeout_seconds": 60,
                },
                {
                    "id": "stage-2-mobile-code",
                    "layer": "mobile",
                    "label": "手机提取验证码",
                    "depends_on": ["stage-1-web-register"],
                    "skill": "extract_otp",
                    "await_device_run": True,
                    "await_timeout_sec": 120,
                    "extract": {"sms_otp": {"required": True}},
                    "steps": [{"action": "extract_otp", "store_as": "sms_otp"}],
                },
                {
                    "id": "stage-3-web-complete",
                    "layer": "web",
                    "label": "Web 填入验证码完成注册",
                    "depends_on": ["stage-2-mobile-code"],
                    "steps": [
                        {"action": "input", "selector": "#code", "input_value": "{{sms_otp}}"},
                        {"action": "input", "selector": "#password", "input_value": "{{password}}"},
                        {"action": "click", "selector": "#register-btn"},
                        {"action": "assert", "selector": ".welcome", "input_value": "欢迎"},
                    ],
                    "vars_to_store": {"username": {"selector": ".username", "source": "text"}},
                    "timeout_seconds": 60,
                },
                {
                    "id": "stage-4-desktop-login",
                    "layer": "desktop",
                    "label": "桌面客户端登录验证",
                    "depends_on": ["stage-3-web-complete"],
                    "steps": [
                        {"action": "launch_app", "input_value": "{{desktop_app_name}}"},
                        {"action": "input", "selector_value": "手机号", "input_value": "{{phone_number}}"},
                        {"action": "input", "selector_value": "密码", "input_value": "{{password}}"},
                        {"action": "click", "selector_value": "登录"},
                        {"action": "assert", "selector_value": "{{username}}"},
                    ],
                    "timeout_seconds": 90,
                },
            ],
        },
    },

    # ---- 4. ERP：桌面录入 + API 验证 + Web 报表 ----
    {
        "template_id": "erp-data-entry-report",
        "name": "ERP 数据录入与报表",
        "industry": "ERP",
        "description": "桌面 ERP 录入数据 → API 验证数据库 → Web 生成报表",
        "difficulty": "medium",
        "tags": ["ERP", "数据录入", "报表", "Desktop", "API", "Web"],
        "parameters": [
            {"name": "erp_app_name", "type": "string", "required": True, "description": "ERP 应用名"},
            {"name": "api_base_url", "type": "string", "required": True, "description": "API 基础 URL"},
            {"name": "report_url", "type": "string", "required": True, "description": "报表页面 URL"},
            {"name": "entry_data", "type": "string", "required": True, "description": "录入数据"},
        ],
        "plan_template": {
            "schema_version": "1.0",
            "scenario": "ERP 数据录入与报表：桌面录入 → API 验证 → Web 报表",
            "stages": [
                {
                    "id": "stage-1-desktop-entry",
                    "layer": "desktop",
                    "label": "桌面 ERP 录入数据",
                    "steps": [
                        {"action": "launch_app", "input_value": "{{erp_app_name}}"},
                        {"action": "click", "selector_value": "新建"},
                        {"action": "input", "selector_value": "数据项", "input_value": "{{entry_data}}"},
                        {"action": "click", "selector_value": "保存"},
                    ],
                    "vars_to_store": {"record_id": {"selector": "单据号", "source": "text"}},
                    "timeout_seconds": 120,
                },
                {
                    "id": "stage-2-api-verify",
                    "layer": "api",
                    "label": "API 验证数据库记录",
                    "depends_on": ["stage-1-desktop-entry"],
                    "request": {"method": "GET", "url": "{{api_base_url}}/api/records/{{record_id}}"},
                    "assert": {"status": 200},
                    "timeout_seconds": 30,
                },
                {
                    "id": "stage-3-web-report",
                    "layer": "web",
                    "label": "Web 验证报表数据",
                    "depends_on": ["stage-2-api-verify"],
                    "steps": [
                        {"action": "navigate", "url": "{{report_url}}"},
                        {"action": "assert", "selector": ".record-{{record_id}}", "input_value": "{{entry_data}}"},
                    ],
                    "timeout_seconds": 60,
                },
            ],
        },
    },

    # ---- 5. 医疗：API 创建预约 + Web 确认 + 手机通知 ----
    {
        "template_id": "healthcare-appointment",
        "name": "医疗预约确认",
        "industry": "医疗",
        "description": "API 创建预约 → Web 确认排班 → 手机接收预约通知",
        "difficulty": "easy",
        "tags": ["医疗", "预约", "通知", "API", "Web", "Mobile"],
        "parameters": [
            {"name": "api_base_url", "type": "string", "required": True, "description": "API 基础 URL"},
            {"name": "patient_id", "type": "string", "required": True, "description": "患者 ID"},
            {"name": "doctor_id", "type": "string", "required": True, "description": "医生 ID"},
            {"name": "appointment_date", "type": "string", "required": True, "description": "预约日期"},
        ],
        "plan_template": {
            "schema_version": "1.0",
            "scenario": "医疗预约：API 创建 → Web 确认 → 手机通知",
            "stages": [
                {
                    "id": "stage-1-create",
                    "layer": "api",
                    "label": "API 创建预约",
                    "request": {"method": "POST", "url": "{{api_base_url}}/api/appointments", "body": {"patient_id": "{{patient_id}}", "doctor_id": "{{doctor_id}}", "date": "{{appointment_date}}"}},
                    "assert": {"status": 201},
                    "extract": {"appointment_id": {"json_path": "$.appointment_id", "type": "string"}},
                },
                {
                    "id": "stage-2-web-verify",
                    "layer": "web",
                    "label": "Web 确认排班",
                    "depends_on": ["stage-1-create"],
                    "steps": [
                        {"action": "navigate", "url": "{{api_base_url}}/appointments/{{appointment_id}}"},
                        {"action": "assert", "selector": ".status", "input_value": "已确认"},
                    ],
                },
                {
                    "id": "stage-3-mobile-notify",
                    "layer": "mobile",
                    "label": "手机接收预约通知",
                    "depends_on": ["stage-1-create"],
                    "skill": "extract_otp",
                    "await_device_run": True,
                    "await_timeout_sec": 60,
                    "steps": [{"action": "extract_otp", "description": "等待手机收到预约确认通知"}],
                },
                {
                    "id": "cleanup",
                    "layer": "api",
                    "label": "取消测试预约",
                    "cleanup": True,
                    "on_failure": "continue",
                    "request": {"method": "DELETE", "url": "{{api_base_url}}/api/appointments/{{appointment_id}}"},
                    "assert": {"status_in": [200, 204, 404]},
                },
            ],
        },
    },
]


def _templates_dir() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    root = Path(env).expanduser().resolve() if env else Path(__file__).resolve().parents[2] / "data"
    d = root / "scenario_templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_templates(industry: str = "", tag: str = "") -> List[Dict[str, Any]]:
    """列出所有模板（不含 plan_template，减少传输）。"""
    templates = list(_BUILTIN_TEMPLATES)
    # 加载自定义模板
    for p in _templates_dir().glob("*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("template_id"):
                templates.append(raw)
        except Exception:
            pass

    out = []
    for t in templates:
        if industry and industry.lower() not in str(t.get("industry", "")).lower():
            continue
        if tag and tag.lower() not in [str(tg).lower() for tg in t.get("tags", [])]:
            continue
        out.append({
            "template_id": t["template_id"],
            "name": t.get("name", ""),
            "industry": t.get("industry", ""),
            "description": t.get("description", ""),
            "difficulty": t.get("difficulty", "medium"),
            "tags": t.get("tags", []),
            "parameters": t.get("parameters", []),
            "stage_count": len((t.get("plan_template") or {}).get("stages") or []),
        })
    return out


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """获取完整模板（含 plan_template）。"""
    for t in _BUILTIN_TEMPLATES:
        if t["template_id"] == template_id:
            return copy.deepcopy(t)
    # 自定义模板
    p = _templates_dir() / f"{template_id}.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def instantiate_template(
    template_id: str,
    parameters: Dict[str, str],
    *,
    scenario_name: str = "",
) -> Dict[str, Any]:
    """用参数实例化模板，生成可执行的 CrossEndPlan。"""
    template = get_template(template_id)
    if not template:
        return {"success": False, "error": f"模板 {template_id} 不存在"}

    plan = copy.deepcopy(template.get("plan_template") or {})
    if not plan.get("stages"):
        return {"success": False, "error": "模板缺少 plan_template.stages"}

    # 填充默认值
    params = {}
    for p in template.get("parameters", []):
        name = p.get("name", "")
        if name:
            params[name] = parameters.get(name, p.get("default", ""))
    # 用户参数覆盖
    for k, v in (parameters or {}).items():
        if v:
            params[k] = v

    # 检查必填参数
    missing = []
    for p in template.get("parameters", []):
        if p.get("required") and not params.get(p["name"]):
            missing.append(p["name"])
    if missing:
        return {"success": False, "error": f"缺少必填参数: {', '.join(missing)}"}

    # 模板变量替换
    plan = _replace_params(plan, params)

    # 设置 plan_id 和 scenario
    plan["plan_id"] = f"tpl-{template_id}-{uuid.uuid4().hex[:8]}"
    if scenario_name:
        plan["scenario"] = scenario_name

    return {
        "success": True,
        "plan": plan,
        "template_id": template_id,
        "parameters_used": params,
    }


def save_custom_template(payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存自定义模板。"""
    tid = (payload.get("template_id") or "").strip()
    if not tid:
        tid = f"custom-{uuid.uuid4().hex[:8]}"
        payload["template_id"] = tid
    p = _templates_dir() / f"{tid}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"success": True, "template_id": tid}


def delete_custom_template(template_id: str) -> Dict[str, Any]:
    """删除自定义模板（内置模板不可删除）。"""
    for t in _BUILTIN_TEMPLATES:
        if t["template_id"] == template_id:
            return {"success": False, "error": "内置模板不可删除"}
    p = _templates_dir() / f"{template_id}.json"
    if not p.is_file():
        return {"success": False, "error": "模板不存在"}
    p.unlink()
    return {"success": True, "template_id": template_id}


def _replace_params(obj: Any, params: Dict[str, str]) -> Any:
    """递归替换 {{param}} 占位符。"""
    if isinstance(obj, str):
        def replacer(m):
            key = m.group(1).strip()
            return params.get(key, m.group(0))
        return re.sub(r'\{\{(\w+)\}\}', replacer, obj)
    if isinstance(obj, dict):
        return {k: _replace_params(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_params(item, params) for item in obj]
    return obj
