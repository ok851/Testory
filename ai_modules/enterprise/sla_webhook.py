# -*- coding: utf-8 -*-
"""SLA 告警可选 Webhook（合同通道雏形）。

仅当 ``SLA_ALERT_WEBHOOK_URL`` 已配置且 ``has_warning`` 时 POST JSON。
失败写入返回值，不抛到业务执行路径；``sla_met`` 仍恒 false。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .sla_alerts import evaluate_sla_alerts


def maybe_post_sla_webhook(
    *,
    force: bool = False,
    limit: int = 200,
) -> Dict[str, Any]:
    """评估告警并可选推送。未配置 URL 时 skipped。"""
    alerts = evaluate_sla_alerts(limit=limit)
    url = (os.environ.get("SLA_ALERT_WEBHOOK_URL") or "").strip()
    out: Dict[str, Any] = {
        "ok": True,
        "posted": False,
        "skipped": True,
        "sla_met": False,
        "sla_claim": False,
        "alerts": alerts,
        "disclaimer": "Webhook 仅为通知通道，不构成 SLA 达标判定",
    }
    if not url:
        out["skip_reason"] = "SLA_ALERT_WEBHOOK_URL 未配置"
        return out
    if not alerts.get("has_warning") and not force:
        out["skip_reason"] = "无 warning，跳过推送（可用 force=true）"
        return out

    body = {
        "source": "testory.sla_alerts",
        "sla_met": False,
        "sla_claim": False,
        "has_warning": bool(alerts.get("has_warning")),
        "alerts": alerts.get("alerts") or [],
        "summary": alerts.get("summary") or {},
        "disclaimer": out["disclaimer"],
    }
    try:
        req = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=5.0) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
        out["skipped"] = False
        out["posted"] = 200 <= int(status) < 300
        out["status_code"] = status
        if not out["posted"]:
            out["ok"] = False
            out["error"] = f"webhook_http_{status}"
    except (HTTPError, URLError, TimeoutError, OSError) as e:
        out["ok"] = False
        out["skipped"] = False
        out["posted"] = False
        out["error"] = str(e)[:200]
    return out
