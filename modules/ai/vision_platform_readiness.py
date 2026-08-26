"""
面向用户的视觉自动化就绪检查（Phase 0）：CDP/画布、网关、本地视觉模型。

不暴露技术细节；供 AI 测试页状态条与 /api/ai/vision/readiness 使用。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from modules.ai.ai_vision_grounding import locator_tier_vlm_enabled
from modules.ai.ai_vision_insight import insight_enabled, wait_vision_enabled
from modules.ai.ai_vision_local import vision_enabled
from modules.web.embedded_browser_client import embedded_gateway_config, embedded_gateway_enabled, embedded_gateway_json
from modules.core.logger import uat_logger

import time

_readiness_cache: dict = {}
_readiness_cache_ttl = 30  # 秒
_readiness_cache_lock = __import__('threading').Lock()


def _ollama_base() -> str:
    return (os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _check_ollama_vision() -> Dict[str, Any]:
    if not vision_enabled():
        return {
            "id": "vision_model",
            "ok": False,
            "label": "智能画面识别",
            "message": "视觉功能未启用",
            "hint": "请联系管理员检查安装配置",
        }
    try:
        resp = requests.get(f"{_ollama_base()}/api/tags", timeout=4)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        names = [
            (m.get("name") or "").strip()
            for m in (data.get("models") or [])
            if isinstance(m, dict)
        ]
        model = (os.environ.get("LOCAL_VISION_MODEL") or "llava:7b").strip()
        has_model = any(model in n or n.startswith(model.split(":")[0]) for n in names if n)
        if has_model or names:
            return {
                "id": "vision_model",
                "ok": True,
                "label": "智能画面识别",
                "message": "本地视觉模型已就绪",
                "hint": "",
            }
        return {
            "id": "vision_model",
            "ok": False,
            "label": "智能画面识别",
            "message": "未检测到可用的视觉模型",
            "hint": f"请在终端执行 ollama pull {model.split(':')[0]} 后重试",
        }
    except Exception as e:
        uat_logger.debug("vision readiness ollama: %s", e)
        return {
            "id": "vision_model",
            "ok": False,
            "label": "智能画面识别",
            "message": "暂未连接本地视觉服务",
            "hint": "安装并启动 Ollama 后，智能点击与画面确认将自动可用；在此之前将使用常规方式操作页面",
        }


def _check_embedded_gateway() -> Dict[str, Any]:
    if not embedded_gateway_enabled():
        return {
            "id": "embedded_gateway",
            "ok": True,
            "label": "测试浏览器",
            "message": "使用主浏览器模式",
            "hint": "",
            "optional": True,
        }
    base, _, _ = embedded_gateway_config()
    try:
        j, err = embedded_gateway_json("GET", "/health", timeout_sec=5.0)
        if j and j.get("ok") is not False:
            return {
                "id": "embedded_gateway",
                "ok": True,
                "label": "测试浏览器",
                "message": "内置测试浏览器已连接",
                "hint": "",
            }
        return {
            "id": "embedded_gateway",
            "ok": False,
            "label": "测试浏览器",
            "message": "内置测试浏览器未启动",
            "hint": "请先在上方打开测试浏览器，再开始 AI 探索",
        }
    except Exception as e:
        uat_logger.debug("embedded gateway readiness: %s", e)
        return {
            "id": "embedded_gateway",
            "ok": False,
            "label": "测试浏览器",
            "message": "无法连接内置测试浏览器",
            "hint": "请确认测试浏览器服务已启动",
        }


def _check_embedded_session(user_id: Optional[int], embedded_sid: str) -> Dict[str, Any]:
    sid = (embedded_sid or "").strip()
    if not sid:
        return {
            "id": "canvas_session",
            "ok": False,
            "label": "测试画面",
            "message": "尚未打开测试画面",
            "hint": "请先在上方打开测试浏览器，再开始 AI 探索",
            "optional": True,
        }
    if not embedded_gateway_enabled():
        return {
            "id": "canvas_session",
            "ok": True,
            "label": "测试画面",
            "message": "主浏览器模式",
            "hint": "",
            "optional": True,
        }
    j, err = embedded_gateway_json(
        "GET",
        f"/internal/session/{sid}/inspect",
        user_id=user_id,
        timeout_sec=8.0,
    )
    if j and j.get("success"):
        return {
            "id": "canvas_session",
            "ok": True,
            "label": "测试画面",
            "message": "测试画面已连接，可以开始探索",
            "hint": "",
        }
    return {
        "id": "canvas_session",
        "ok": False,
        "label": "测试画面",
        "message": "测试画面未就绪",
        "hint": str(err or (j or {}).get("detail") or "请重新打开测试浏览器")[:200],
    }


def check_vision_automation_readiness(
    *,
    user_id: Optional[int] = None,
    embedded_session_id: str = "",
) -> Dict[str, Any]:
    """汇总就绪状态，供 API 与前端展示。"""
    cache_key = "vision_readiness"
    with _readiness_cache_lock:
        cached = _readiness_cache.get(cache_key)
        if cached and time.time() - cached[1] < _readiness_cache_ttl:
            return cached[0]

    items: List[Dict[str, Any]] = [
        _check_ollama_vision(),
        _check_embedded_gateway(),
    ]
    if embedded_gateway_enabled():
        items.append(_check_embedded_session(user_id, embedded_session_id))

    core_ok = all(it.get("ok") for it in items if not it.get("optional"))
    vision_features = {
        "vlm_grounding": locator_tier_vlm_enabled(),
        "insight": insight_enabled(),
        "wait_vision": wait_vision_enabled(),
    }
    hints = [it.get("hint") for it in items if it.get("hint") and not it.get("ok")]
    result = {
        "ready": core_ok,
        "items": items,
        "vision_features": vision_features,
        "summary": "一切就绪，可以开始智能测试" if core_ok else (hints[0] if hints else "部分功能暂不可用，将自动使用常规方式"),
    }
    with _readiness_cache_lock:
        _readiness_cache[cache_key] = (result, time.time())
    return result
