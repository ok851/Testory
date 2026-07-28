# -*- coding: utf-8 -*-
"""执行农场节点登记（Phase B 企业雏形）。

诚实约束：
- 仅登记与探测节点；**不**在无节点时假称已并行执行成功
- 真正跨机调度仍依赖 ``DESKTOP_EXECUTION_MODE=remote`` 与网络可达
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen


def _root() -> Path:
    env = (os.environ.get("UAT_DATA_DIR") or "").strip()
    if env:
        base = Path(env).expanduser().resolve()
    else:
        base = Path(__file__).resolve().parents[2] / "data"
    d = base / "execution_farm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store() -> Path:
    return _root() / "nodes.json"


def list_nodes() -> List[Dict[str, Any]]:
    path = _store()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    nodes = data.get("nodes") if isinstance(data, dict) else data
    return [n for n in (nodes or []) if isinstance(n, dict)]


def _save(nodes: List[Dict[str, Any]]) -> None:
    _store().write_text(
        json.dumps({"nodes": nodes, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register_node(
    *,
    name: str,
    base_url: str,
    capabilities: Optional[List[str]] = None,
    node_id: str = "",
) -> Dict[str, Any]:
    url = (base_url or "").strip().rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        raise ValueError("base_url 须为 http(s) URL")
    nid = (node_id or "").strip() or f"node-{uuid.uuid4().hex[:10]}"
    node = {
        "node_id": nid,
        "name": (name or nid).strip() or nid,
        "base_url": url,
        "capabilities": list(capabilities or ["desktop", "web", "api"]),
        "registered_at": _now(),
        "last_probe": None,
        "last_ok": None,
    }
    nodes = [n for n in list_nodes() if n.get("node_id") != nid]
    nodes.append(node)
    _save(nodes)
    return node


def remove_node(node_id: str) -> bool:
    nid = (node_id or "").strip()
    nodes = list_nodes()
    new_nodes = [n for n in nodes if n.get("node_id") != nid]
    if len(new_nodes) == len(nodes):
        return False
    _save(new_nodes)
    return True


def probe_node(node_id: str, *, timeout_s: float = 3.0) -> Dict[str, Any]:
    """探测节点健康；失败返回 ok=False，不假绿。"""
    nodes = list_nodes()
    target = None
    for n in nodes:
        if n.get("node_id") == node_id:
            target = n
            break
    if not target:
        return {"ok": False, "error_code": "NODE_NOT_FOUND", "error": "节点不存在"}
    url = str(target.get("base_url") or "").rstrip("/") + "/health"
    ok = False
    err = None
    status_code = None
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=max(0.5, float(timeout_s))) as resp:
            status_code = getattr(resp, "status", None) or resp.getcode()
            ok = 200 <= int(status_code) < 300
            if not ok:
                err = f"http_{status_code}"
    except Exception as e:
        err = str(e)[:200]
        ok = False
    target["last_probe"] = _now()
    target["last_ok"] = ok
    target["last_error"] = err
    target["last_status_code"] = status_code
    _save(nodes)
    return {
        "ok": ok,
        "node_id": node_id,
        "probe_url": url,
        "status_code": status_code,
        "error": err,
        "error_code": None if ok else "NODE_UNREACHABLE",
        "disclaimer": "探测成功仅表示节点可达，不表示用例已并行跑通",
    }


def farm_summary() -> Dict[str, Any]:
    nodes = list_nodes()
    online = sum(1 for n in nodes if n.get("last_ok") is True)
    return {
        "node_count": len(nodes),
        "online_count": online,
        "nodes": nodes,
        "execution_mode_hint": (os.environ.get("DESKTOP_EXECUTION_MODE") or "").strip() or "inprocess",
        "disclaimer": "农场登记 ≠ 已完成跨机并行；调度需 remote 模式与真实节点",
    }


def select_preferred_node(
    *,
    capability: str = "",
    require_online: bool = True,
) -> Optional[Dict[str, Any]]:
    """挑选优先节点：默认要求 last_ok=True；不假装已调度成功。"""
    cap = (capability or "").strip().lower()
    candidates = list_nodes()
    if require_online:
        candidates = [n for n in candidates if n.get("last_ok") is True]
    if cap:
        filtered = [
            n
            for n in candidates
            if cap in [str(c).lower() for c in (n.get("capabilities") or [])]
        ]
        if filtered:
            candidates = filtered
    if not candidates:
        return None
    # 最近探测成功优先（字符串时间 ISO 可比较）
    candidates = sorted(
        candidates,
        key=lambda n: str(n.get("last_probe") or n.get("registered_at") or ""),
        reverse=True,
    )
    return dict(candidates[0])


def dispatch_hint(
    *,
    node_id: str = "",
    capability: str = "desktop",
) -> Dict[str, Any]:
    """给出环境变量建议；不自动改进程环境，也不宣称用例已跑通。"""
    node: Optional[Dict[str, Any]] = None
    nid = (node_id or "").strip()
    if nid:
        for n in list_nodes():
            if n.get("node_id") == nid:
                node = dict(n)
                break
        if not node:
            return {
                "ok": False,
                "error_code": "NODE_NOT_FOUND",
                "error": "节点不存在",
                "disclaimer": "调度建议未生成",
            }
    else:
        node = select_preferred_node(capability=capability, require_online=True)
        if not node:
            return {
                "ok": False,
                "error_code": "NO_ONLINE_NODE",
                "error": "无探测成功的在线节点；请先 probe",
                "disclaimer": "不可据此宣称并行执行已就绪",
            }

    base = str(node.get("base_url") or "").rstrip("/")
    mode = (os.environ.get("DESKTOP_EXECUTION_MODE") or "").strip().lower() or "inprocess"
    env_suggestions = {
        "DESKTOP_EXECUTION_MODE": "remote",
        "DESKTOP_AGENT_GATEWAY_URL": base,
        "DESKTOP_AGENT_GATEWAY_SECRET": "(填写与节点一致的密钥，勿写入仓库)",
        "DESKTOP_FARM_GATEWAY": "1",
    }
    blockers: List[str] = []
    if mode != "remote":
        blockers.append(f"当前 DESKTOP_EXECUTION_MODE={mode!r}，建议改为 remote")
    if node.get("last_ok") is not True:
        blockers.append("节点最近探测未成功")
    if not (os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET") or "").strip():
        blockers.append("进程未设置 DESKTOP_AGENT_GATEWAY_SECRET（仅提示，不自动写入）")

    return {
        "ok": True,
        "ready_to_suggest": True,
        "dispatch_ready": len(blockers) == 0 and mode == "remote",
        "node": node,
        "env_suggestions": env_suggestions,
        "blockers": blockers,
        "disclaimer": (
            "本接口仅输出调度建议；不会自动改 .env，也不会把探测成功记为用例/并行通过。"
        ),
    }


def dispatch_readiness() -> Dict[str, Any]:
    """调度就绪检查（诚实：未探测成功不得称就绪）。"""
    summary = farm_summary()
    preferred = select_preferred_node(capability="desktop", require_online=True)
    mode = summary.get("execution_mode_hint") or "inprocess"
    gateway = (os.environ.get("DESKTOP_AGENT_GATEWAY_URL") or "").strip()
    secret_set = bool((os.environ.get("DESKTOP_AGENT_GATEWAY_SECRET") or "").strip())
    checks = [
        {
            "id": "has_nodes",
            "ok": int(summary.get("node_count") or 0) > 0,
            "detail": f"已登记 {summary.get('node_count')} 节点",
        },
        {
            "id": "online_node",
            "ok": preferred is not None,
            "detail": (
                f"在线优先节点={preferred.get('node_id')}"
                if preferred
                else "无 last_ok=True 节点"
            ),
        },
        {
            "id": "execution_mode_remote",
            "ok": mode == "remote",
            "detail": f"DESKTOP_EXECUTION_MODE={mode}",
        },
        {
            "id": "gateway_url",
            "ok": bool(gateway) or preferred is not None,
            "detail": gateway or (preferred.get("base_url") if preferred else "未配置"),
        },
        {
            "id": "gateway_secret",
            "ok": secret_set,
            "detail": "已设置" if secret_set else "未设置 DESKTOP_AGENT_GATEWAY_SECRET",
        },
    ]
    try:
        from ai_modules.enterprise.gateway_resolve import farm_gateway_opt_in, resolve_desktop_gateway

        resolved = resolve_desktop_gateway()
        checks.append(
            {
                "id": "gateway_resolve",
                "ok": bool(resolved.get("base_url")),
                "detail": (
                    f"source={resolved.get('source')} "
                    f"farm_opt_in={farm_gateway_opt_in()} "
                    f"url={resolved.get('base_url') or '—'}"
                ),
            }
        )
    except Exception as e:
        checks.append(
            {
                "id": "gateway_resolve",
                "ok": False,
                "detail": str(e)[:120],
            }
        )
    all_ok = all(bool(c.get("ok")) for c in checks)
    return {
        "ok": True,
        "dispatch_ready": all_ok,
        "checks": checks,
        "preferred_node": preferred,
        "summary": summary,
        "disclaimer": (
            "dispatch_ready=true 仅表示远程桌面调度前置条件齐备，"
            "不表示并行用例已成功，也不构成 SLA 达标。"
            "设 DESKTOP_FARM_GATEWAY=1 可用在线农场节点补全 Gateway URL。"
        ),
    }
