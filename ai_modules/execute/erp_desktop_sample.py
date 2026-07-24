# -*- coding: utf-8 -*-
"""Windows ERP 桌面样例计划：API 种子订单号 ↔ Fake ERP / 客户别名窗口标题核对。"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]
_FAKE_ERP = _ROOT / "demos" / "erp_desktop_sample" / "fake_erp_client.py"


def fake_erp_script_path() -> Path:
    return _FAKE_ERP


def suggest_fake_erp_alias_entry(order_id: str = "{order_id}", hold_sec: float = 60.0) -> Dict[str, Any]:
    """生成可写入 DESKTOP_APP_ALIASES 的 Fake ERP 对象别名（演示用）。"""
    return {
        "path": sys.executable,
        "args": [
            str(fake_erp_script_path().resolve()),
            "--order-id",
            order_id,
            "--hold-sec",
            str(hold_sec),
        ],
        "window_title_re": f"(?i)^{order_id}$" if "{" not in order_id else "(?i)^{order_id}$",
    }


def resolve_erp_alias(
    alias: str = "erp",
    *,
    order_id: str = "",
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """解析 ERP 启动别名；缺失时返回诚实错误码文案。"""
    from desktop_env_config import load_app_alias_specs, resolve_launch_spec

    key = (alias or "erp").strip().lstrip("@").lower() or "erp"
    specs = load_app_alias_specs()
    if key not in specs:
        return None, (
            f"DESKTOP_ALIAS_MISSING: 未配置别名「{key}」。"
            f"请在 .env 设置 DESKTOP_APP_ALIASES，例如 "
            f'{{"{key}":"C:\\\\ERP\\\\client.exe"}} 或带 args 的对象形式。'
        )
    vars_map = {"order_id": order_id, "api_order_id": order_id}
    launch = resolve_launch_spec(f"@{key}", variables=vars_map)
    if not launch or not launch.get("path"):
        return None, f"DESKTOP_ALIAS_INVALID: 别名「{key}」无法解析 path"
    return launch, None


def build_erp_desktop_sample_plan(
    *,
    order_id: str = "ORD-DEMO-404",
    project_id: Optional[Any] = None,
    plan_id: str = "erp-desktop-sample",
    hold_sec: float = 60.0,
    launch_mode: str = "fake",
    alias: str = "erp",
    window_title_re: Optional[str] = None,
) -> Dict[str, Any]:
    """构建可真机跑的 ERP 样例计划。

    - ``launch_mode=fake``：内置 Fake ERP（Tk，标题=订单号）
    - ``launch_mode=alias``：经 ``DESKTOP_APP_ALIASES`` 的 ``@erp``（或指定 alias）启动客户客户端
    - ``variables.api_order_id``：模拟上游 API 已查出的订单号
    - ``assertions``：api_order_id 与 erp_order_id 一致；不一致不得假绿
    """
    oid = (order_id or "ORD-DEMO-404").strip() or "ORD-DEMO-404"
    mode = (launch_mode or "fake").strip().lower()
    if mode in ("alias", "app_alias", "desktop_alias", "@alias"):
        mode = "alias"
    else:
        mode = "fake"

    alias_error: Optional[str] = None
    alias_key = (alias or "erp").strip().lstrip("@").lower() or "erp"
    title_re = (window_title_re or "").strip()
    if not title_re:
        title_re = (os.environ.get("DESKTOP_ERP_WINDOW_TITLE_RE") or "").strip()
    if not title_re:
        title_re = f"(?i)^{re.escape(oid)}$"

    if mode == "alias":
        launch, alias_error = resolve_erp_alias(alias_key, order_id=oid)
        if launch and launch.get("window_title_re") and not (window_title_re or "").strip():
            # 别名自带标题优先于默认 order_id 精确匹配（客户窗体标题往往更复杂）
            if not (os.environ.get("DESKTOP_ERP_WINDOW_TITLE_RE") or "").strip():
                title_re = str(launch["window_title_re"])
        if launch:
            launch_step = {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": f"@{alias_key}",
                "desktop_spec": {
                    "alias": alias_key,
                    "path": launch["path"],
                    **({"args": list(launch["args"])} if launch.get("args") else {}),
                },
                "description": f"经别名 @{alias_key} 启动 ERP 客户端",
            }
            stage_name = f"ERP 别名 @{alias_key} 订单窗口核对"
            scenario = f"ERP 别名样例：API 订单 {oid} ↔ @{alias_key} 窗口"
        else:
            # 诚实：计划仍返回，但 launch 用不可执行占位，meta 标错；API 层应 ready_to_run=false
            launch_step = {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": f"@{alias_key}",
                "desktop_spec": {"alias": alias_key},
                "description": f"经别名 @{alias_key} 启动（未配置将失败）",
            }
            stage_name = f"ERP 别名 @{alias_key}（未就绪）"
            scenario = f"ERP 别名样例（别名缺失）：{oid}"
        meta_extra = {
            "launch_mode": "alias",
            "alias": alias_key,
            "alias_error": alias_error,
            "fake_erp": False,
        }
    else:
        script = str(fake_erp_script_path().resolve())
        py = sys.executable
        launch_step = {
            "action": "launch_app",
            "automation_layer": "desktop",
            "input_value": py,
            "desktop_spec": {
                "path": py,
                "args": [script, "--order-id", oid, "--hold-sec", str(hold_sec)],
            },
            "description": "启动 Fake ERP 样例窗口",
        }
        stage_name = "Fake ERP 订单窗口核对"
        scenario = f"ERP 桌面样例：API 订单 {oid} ↔ Fake ERP 窗口"
        meta_extra = {
            "launch_mode": "fake",
            "fake_erp": True,
            "fake_erp_script": script,
            "suggest_alias_entry": suggest_fake_erp_alias_entry("{order_id}", hold_sec),
        }

    stages = [
        {
            "id": "stage-desktop-erp",
            "layer": "desktop",
            "name": stage_name,
            "steps": [
                launch_step,
                {
                    "action": "wait",
                    "automation_layer": "desktop",
                    "input_value": "1.5",
                    "description": "等待 ERP 窗口",
                },
                {
                    "action": "attach_window",
                    "automation_layer": "desktop",
                    "desktop_spec": {"window_title_re": title_re},
                    "store_as": "erp_order_id",
                    "description": "附着 ERP 并抽取订单号（窗口标题）",
                },
                {
                    "action": "verify",
                    "automation_layer": "desktop",
                    "selector_type": "window",
                    "selector_value": oid,
                    "desktop_spec": {"window_title_re": title_re},
                    "description": "校验 ERP 订单窗口仍在",
                },
            ],
            "vars_to_store": {
                "erp_order_id": {"optional": False},
            },
        }
    ]
    plan: Dict[str, Any] = {
        "plan_id": plan_id if mode == "fake" else f"{plan_id}-alias",
        "scenario": scenario,
        "variables": {"api_order_id": oid},
        "stages": stages,
        "assertions": [
            {
                "field": "order_id",
                "label": "API 与 ERP 订单号一致",
                "api": "api_order_id",
                "desktop": "erp_order_id",
                "type": "string",
            }
        ],
        "meta": {
            "template": "erp_desktop_sample",
            "honesty": "订单号不一致或窗口未附着 → 失败，不假绿",
            "window_title_re": title_re,
            **meta_extra,
        },
    }
    if project_id is not None and str(project_id).strip() != "":
        try:
            plan["project_id"] = int(project_id)
        except (TypeError, ValueError):
            plan["project_id"] = project_id
    return plan
