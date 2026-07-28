# -*- coding: utf-8 -*-
"""Desktop Gateway 解析：环境变量优先，可选农场在线节点回退。

诚实约束：
- 仅当 ``DESKTOP_FARM_GATEWAY=1`` 时才用农场优选节点填 URL
- 回填 URL ≠ 用例已跑通；仍需 SECRET 与 remote/gateway 模式
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple


def _truthy(name: str, default: str = "0") -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def farm_gateway_opt_in() -> bool:
    """显式 opt-in：允许用农场在线节点补全 DESKTOP_AGENT_GATEWAY_URL。"""
    return _truthy("DESKTOP_FARM_GATEWAY", "0")


def resolve_desktop_gateway() -> Dict[str, Any]:
    """解析当前应使用的 Gateway URL / 密钥是否就绪。"""
    base = (os.environ.get("DESKTOP_AGENT_GATEWAY_URL") or "").strip().rstrip("/")
    secret = (os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET") or "").strip()
    source = "env" if base else "none"
    farm_node: Optional[Dict[str, Any]] = None
    farm_used = False

    if not base and farm_gateway_opt_in():
        try:
            from ai_modules.enterprise.execution_farm import select_preferred_node

            farm_node = select_preferred_node(capability="desktop", require_online=True)
            if farm_node and farm_node.get("base_url"):
                base = str(farm_node.get("base_url") or "").rstrip("/")
                source = "farm"
                farm_used = True
        except Exception as e:
            return {
                "ok": False,
                "base_url": "",
                "secret_set": bool(secret),
                "source": "none",
                "farm_opt_in": True,
                "farm_used": False,
                "error": str(e)[:200],
                "disclaimer": "农场回退失败；未配置 URL 时不得宣称 remote 已就绪",
            }

    enabled = bool(base and secret)
    return {
        "ok": True,
        "base_url": base,
        "secret_set": bool(secret),
        "enabled": enabled,
        "source": source,
        "farm_opt_in": farm_gateway_opt_in(),
        "farm_used": farm_used,
        "farm_node_id": (farm_node or {}).get("node_id") if farm_used else None,
        "disclaimer": (
            "source=farm 仅表示 URL 来自在线节点登记；"
            "不表示并行用例已通过，也不自动写入 .env。"
        ),
    }


def desktop_agent_config() -> Tuple[str, str]:
    """返回 (base_url, secret)；base 可经农场 opt-in 回退。"""
    resolved = resolve_desktop_gateway()
    base = str(resolved.get("base_url") or "").strip().rstrip("/")
    secret = (os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET") or "").strip()
    return base, secret
