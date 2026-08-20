# -*- coding: utf-8 -*-
"""视觉语言模型（VLM）元素定位接口。

云端 API 支持：
- 优先复用 ai_vision_local.py 的 vision_describe_cloud()
  已支持 openai / openai_compatible / anthropic 三种 provider
  已带熔断器、自动重试、数据脱敏
- 独立 VLM_BACKEND / VLM_API_KEY 配置支持
  支持 qwen_vl (DashScope) 直接调用
  支持自定义 base_url 的 OpenAI 兼容接口

借鉴 SWE-Agent / OpenDevin 的视觉 grounding 设计。
"""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from logger import uat_logger


_VLM_PROMPT_TEMPLATE = """This is a screenshot of a {platform} application.

Find the element described as "{description}" in this image.
Return its approximate position as a bounding box using percentages (0-100 scale relative to the image dimensions).

IMPORTANT: Your response MUST be ONLY valid JSON, with no other text:
{{
  "found": true/false,
  "x": left edge percentage (0-100),
  "y": top edge percentage (0-100),
  "width": width percentage (0-100),
  "height": height percentage (0-100),
  "text": the visible text on or near the element,
  "confidence": 0.0-1.0,
  "reason": brief explanation of what you found
}}

If you cannot find the element, return ONLY: {{"found": false, "reason": "explanation"}}"""

_VLM_SCREEN_ANALYSIS_PROMPT = """Analyze this screenshot of a {platform} application.

Context: The tool "{tool_name}" recently failed with error: {error}

IMPORTANT: Your response MUST be ONLY valid JSON:
{{
  "screen_description": "brief description of what's on screen",
  "failure_cause": "likely cause of the failure",
  "recovery_element": {{
    "found": true/false,
    "x": percentage left,
    "y": percentage top,
    "width": percentage width,
    "height": percentage height,
    "text": "text on the recovery element",
    "confidence": 0.0-1.0
  }},
  "alternative_action": "what the agent should try next"
}}"""

_VISUAL_MODEL_KEYWORDS = (
    "gpt-4o", "gpt-4o-mini", "gpt-4o-mobile", "gpt-4o-mini-search",
    "gpt-4.1",
    "vision", "vl", "vl-max", "vl-plus",
    "claude-3", "claude-3.5", "claude-3-haiku", "claude-3-sonnet", "claude-3-opus",
    "sonnet", "opus", "haiku",
    "gemini-1.5", "gemini-2.0", "gemini-pro", "gemini-flash", "gemini-ultra",
    "qwen-vl", "qwen2.5-vl", "qwen-vl-max", "qwen-vl-plus",
    "qwen-omni", "qwen3.7-vl",
    "doubao-vision", "doubao-omni",
    "glm-4v", "glm-4-flash", "glm-4v-flash",
    "internvl",
    "llava", "llama-vision",
    "phi-vision", "phi-3.5-vision",
    "omni", "multimodal", "multilingual-vl",
    "dashscope-vision", "wanx-v",
)


def _model_supports_vision(model_id: str) -> bool:
    """根据模型 ID 关键词判断是否支持视觉/多模态能力。"""
    mid = (model_id or "").lower()
    if not mid:
        return False
    return any(kw in mid for kw in _VISUAL_MODEL_KEYWORDS)


def _profile_to_vlm_config(profile: Dict[str, Any]) -> Dict[str, Any]:
    """将 ai_model_registry profile 转换为 VLM 配置。"""
    provider = (profile.get("provider") or profile.get("api_style") or "").lower()
    model_id = profile.get("model_id") or profile.get("model") or ""
    api_key = profile.get("api_key") or ""
    base_url = profile.get("base_url") or ""
    if not provider or not api_key or not model_id:
        return {}
    if not _model_supports_vision(model_id):
        return {}
    backend = provider
    if provider in ("ollama", "ollama_server"):
        backend = "ollama"
    elif provider in ("anthropic", "claude"):
        backend = "anthropic"
    elif provider == "qwen_vl":
        backend = "qwen_vl"
    elif provider in ("openai", "openai_compatible", "azure", ""):
        backend = "openai"
    return {
        "backend": backend,
        "api_key": api_key,
        "base_url": base_url,
        "model": model_id,
        "enabled": True,
        "source": "model_registry",
    }


def _detect_screen_size() -> Tuple[int, int]:
    """检测屏幕分辨率，失败时返回默认值。"""
    global _SCREEN_SIZE_CACHE
    if _SCREEN_SIZE_CACHE is not None:
        return _SCREEN_SIZE_CACHE
    try:
        import ctypes
        user32 = ctypes.windll.user32
        sm_cxscreen = 0
        sm_cyscreen = 1
        width = int(user32.GetSystemMetrics(sm_cxscreen) or 1920)
        height = int(user32.GetSystemMetrics(sm_cyscreen) or 1080)
        _SCREEN_SIZE_CACHE = (max(640, width), max(480, height))
        return _SCREEN_SIZE_CACHE
    except Exception:
        _SCREEN_SIZE_CACHE = (1920, 1080)
        return _SCREEN_SIZE_CACHE


_SCREEN_SIZE_CACHE: Optional[Tuple[int, int]] = None


class VLMGrounding:
    """视觉语言模型元素定位接口。

    自动复用平台 ai_model_registry.json 中当前激活的模型配置。
    若当前模型支持视觉能力（gpt-4o, claude-3, qwen-vl 等），
    则无需任何额外配置即可启用 VLM 视觉分析。

    手动覆盖优先级：
    1. configure_vlm() 运行时配置（最高）
    2. data/vlm_config.json 文件配置
    3. 环境变量 VLM_* / CLOUD_VISION_*
    4. ai_model_registry.json 自动复用（默认）
    """

    def __init__(self, backend: Optional[str] = None):
        self._backend = backend or self._detect_backend()
        self._client: Optional[Dict[str, Any]] = None
        self._model: str = ""
        self._enabled = self._backend is not None
        self._screen_size = _detect_screen_size()
        self._use_cloud_vision_local = False
        if self._enabled:
            self._init_client()

    def _load_from_model_registry(self) -> Optional[Dict[str, Any]]:
        """从 ai_model_registry.json 读取当前激活 profile，判断是否支持视觉。"""
        try:
            from ai_selector_recovery import load_active_profile_for_inference
            profile = load_active_profile_for_inference()
            if not isinstance(profile, dict):
                return None
            cfg = _profile_to_vlm_config(profile)
            if cfg:
                uat_logger.info("VLM auto-configured from model_registry: model=%s", cfg.get("model"))
            return cfg or None
        except Exception:
            return None

    def _detect_backend(self) -> Optional[str]:
        """自动检测可用的 VLM 后端。优先级：运行时 > 文件 > 环境变量 > 模型注册表。"""
        global _runtime_vlm_config
        if _runtime_vlm_config and _runtime_vlm_config.get("enabled"):
            return _runtime_vlm_config.get("backend", "auto")
        try:
            from desktop_env_config import get_vlm_config
            cfg = get_vlm_config()
            if cfg and cfg.get("enabled"):
                return cfg.get("backend", "auto")
        except Exception:
            pass
        registry_cfg = self._load_from_model_registry()
        if registry_cfg and registry_cfg.get("enabled"):
            return registry_cfg.get("backend", "auto")
        return None

    def _init_client(self) -> None:
        global _runtime_vlm_config
        try:
            cfg = None
            if _runtime_vlm_config and _runtime_vlm_config.get("enabled"):
                cfg = _runtime_vlm_config
            else:
                try:
                    from desktop_env_config import get_vlm_config
                    cfg = get_vlm_config()
                except Exception:
                    pass
                if not cfg or not cfg.get("enabled"):
                    registry_cfg = self._load_from_model_registry()
                    if registry_cfg and registry_cfg.get("enabled"):
                        cfg = registry_cfg
            if not cfg:
                self._enabled = False
                return
            backend = cfg.get("backend", "auto")
            api_key = cfg.get("api_key", "")
            base_url = cfg.get("base_url", "")
            model = cfg.get("model", "gpt-4o")
            if backend in ("auto", "cloud", "cloud_vision") or backend in (
                "openai", "openai_compatible", "anthropic"
            ):
                self._init_cloud_vision_local(backend, api_key, base_url, model)
            elif backend == "qwen_vl":
                self._init_qwen_vl_client(api_key, base_url, model)
            elif backend == "ollama":
                self._init_ollama_client(model)
            else:
                self._init_cloud_vision_local(backend, api_key, base_url, model)
        except Exception as e:
            uat_logger.warning("Failed to init VLM client: %s", e)
            self._enabled = False

    def _init_cloud_vision_local(
        self, provider: str, api_key: str, base_url: str, model: str
    ) -> None:
        self._use_cloud_vision_local = True
        self._client = {
            "type": "cloud_vision",
            "provider": provider or "openai",
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        }
        self._model = model
        uat_logger.info("VLM grounding initialized via ai_vision_local, provider=%s model=%s", provider, model)

    def _init_qwen_vl_client(
        self, api_key: str, base_url: str, model: str
    ) -> None:
        if not base_url:
            base_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc"
        self._client = {
            "type": "qwen_vl",
            "api_key": api_key,
            "base_url": base_url,
            "model": model or "qwen-vl-max",
        }
        self._model = model or "qwen-vl-max"
        uat_logger.info("VLM grounding initialized with qwen-vl, model=%s", self._model)

    def _init_ollama_client(self, model: str) -> None:
        self._client = {
            "type": "ollama",
            "url": "http://localhost:11434",
            "model": model or "qwen2.5-vl",
        }
        self._model = model or "qwen2.5-vl"
        uat_logger.info("VLM grounding initialized with Ollama, model=%s", self._model)

    def set_screen_size(self, width: int, height: int) -> None:
        global _SCREEN_SIZE_CACHE
        _SCREEN_SIZE_CACHE = (max(640, width), max(480, height))
        self._screen_size = _SCREEN_SIZE_CACHE

    def is_available(self) -> bool:
        return self._enabled and self._client is not None

    def find_element(
        self,
        screenshot: bytes,
        description: str,
        platform: str = "desktop",
    ) -> Optional[Dict[str, Any]]:
        if not self.is_available() or not screenshot:
            return None
        prompt = _VLM_PROMPT_TEMPLATE.format(
            platform=platform, description=description)
        response = self._call_vlm(screenshot, prompt)
        if not response:
            return None
        parsed = self._parse_element_response(response)
        if not parsed or not parsed.get("found"):
            return None
        pct_x = float(parsed.get("x", 0))
        pct_y = float(parsed.get("y", 0))
        pct_w = float(parsed.get("width", 0))
        pct_h = float(parsed.get("height", 0))
        sw, sh = self._screen_size
        x = int(pct_x / 100.0 * sw)
        y = int(pct_y / 100.0 * sh)
        w = int(pct_w / 100.0 * sw)
        h = int(pct_h / 100.0 * sh)
        return {
            "x": x,
            "y": y,
            "width": max(4, w),
            "height": max(4, h),
            "confidence": float(parsed.get("confidence", 0.5)),
            "text": str(parsed.get("text", "")),
            "model": self._model,
            "raw_confidence": float(parsed.get("confidence", 0.5)),
            "reason": str(parsed.get("reason", "")),
        }

    def analyze_screen(
        self,
        screenshot: bytes,
        prompt: str,
        platform: str = "desktop",
    ) -> Optional[Dict[str, Any]]:
        if not self.is_available() or not screenshot:
            return None
        response = self._call_vlm(screenshot, prompt)
        if not response:
            return None
        return self._parse_analysis_response(response)

    def should_use_vlm(self, confidence: float, platform: str) -> bool:
        return confidence < 0.6 and platform in ("desktop", "mobile")

    def _call_vlm(self, screenshot: bytes, prompt: str) -> Optional[str]:
        if self._client is None:
            return None
        try:
            if self._use_cloud_vision_local:
                return self._call_cloud_vision_local(screenshot, prompt)
            client_type = self._client.get("type", "")
            if client_type == "ollama":
                return self._call_ollama(screenshot, prompt)
            elif client_type == "qwen_vl":
                return self._call_qwen_vl(screenshot, prompt)
            elif client_type == "cloud_vision":
                return self._call_cloud_vision_local(screenshot, prompt)
        except Exception as e:
            uat_logger.warning("VLM call failed: %s", e)
        return None

    def _call_cloud_vision_local(self, screenshot: bytes, prompt: str) -> Optional[str]:
        """复用 ai_vision_local.vision_describe_cloud() 的完整云端调用链。"""
        try:
            from ai_vision_local import vision_describe_cloud
            provider = self._client.get("provider", "openai")
            api_key = self._client.get("api_key", "")
            base_url = self._client.get("base_url", "")
            model = self._client.get("model", self._model)
            return vision_describe_cloud(
                screenshot,
                prompt,
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=30,
            )
        except ImportError:
            uat_logger.warning("ai_vision_local not available for VLM call")
            return self._call_cloud_direct(screenshot, prompt)
        except Exception as e:
            uat_logger.warning("cloud_vision_local call failed: %s, falling back to direct", e)
            return self._call_cloud_direct(screenshot, prompt)

    def _call_cloud_direct(self, screenshot: bytes, prompt: str) -> Optional[str]:
        """直接调用云端 API（ai_vision_local 不可用时的后备路径）。"""
        import urllib.request
        b64 = base64.b64encode(screenshot).decode("utf-8")
        api_key = self._client.get("api_key", "")
        base_url = self._client.get("base_url", "").rstrip("/")
        model = self._client.get("model", self._model)
        provider = self._client.get("provider", "openai")
        if provider in ("openai", "openai_compatible", ""):
            base = base_url or "https://api.openai.com"
            url = f"{base}/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    }
                ],
                "max_tokens": 800,
                "temperature": 0.1,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "") or ""
        elif provider == "anthropic":
            base = base_url or "https://api.anthropic.com"
            url = f"{base}/v1/messages"
            payload = {
                "model": model,
                "max_tokens": 800,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image",
                             "source": {"type": "base64",
                                        "media_type": "image/png",
                                        "data": b64}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            }
            headers = {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
            content = result.get("content", [])
            if content:
                return content[0].get("text", "") or ""
        return None

    def _call_qwen_vl(self, screenshot: bytes, prompt: str) -> Optional[str]:
        import urllib.request
        b64 = base64.b64encode(screenshot).decode("utf-8")
        base_url = self._client.get("base_url", "").rstrip("/")
        url = f"{base_url}/multimodal/generation"
        payload = {
            "model": self._model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": f"data:image/png;base64,{b64}"},
                            {"text": prompt},
                        ]
                    }
                ]
            },
            "parameters": {"max_tokens": 800, "temperature": 0.1},
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._client['api_key']}",
        }
        req = urllib.request.Request(url, data=data, headers=headers)
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        outputs = result.get("output", {}).get("choices", [])
        if outputs:
            msg = outputs[0].get("message", {})
            return msg.get("content", "") or ""
        return result.get("output", {}).get("text", "") or ""

    def _call_ollama(self, screenshot: bytes, prompt: str) -> Optional[str]:
        import urllib.request
        b64 = base64.b64encode(screenshot).decode("utf-8")
        url = f"{self._client['url']}/api/chat"
        payload = {
            "model": self._client["model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "data": b64},
                    ],
                }
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read())
        msg = result.get("message", {})
        return msg.get("content", "") or ""

    @staticmethod
    def _parse_element_response(response: str) -> Dict[str, Any]:
        if not response:
            return {"found": False}
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
        return {"found": False, "reason": "parse_error", "raw": response[:200]}

    @staticmethod
    def _parse_analysis_response(response: str) -> Dict[str, Any]:
        if not response:
            return {"screen_description": "", "failure_cause": "", "alternative_action": ""}
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
        return {
            "screen_description": response[:300],
            "failure_cause": "",
            "alternative_action": "",
            "raw": response,
        }


_cached_vlm: Optional[VLMGrounding] = None
_runtime_vlm_config: Optional[Dict[str, Any]] = None
_cached_vlm_signature: str = ""


def _get_current_model_registry_signature() -> str:
    """获取当前模型注册表的签名（用于检测模型变更）。"""
    try:
        from ai_selector_recovery import load_active_profile_for_inference
        profile = load_active_profile_for_inference()
        if isinstance(profile, dict):
            mid = profile.get("model_id") or ""
            key = profile.get("api_key") or ""
            bu = profile.get("base_url") or ""
            prov = profile.get("provider") or ""
            return f"{prov}|{mid}|{key}|{bu}"
    except Exception:
        pass
    return ""


def _vlm_config_signature() -> str:
    """获取当前 VLM 配置的签名。"""
    if _runtime_vlm_config:
        c = _runtime_vlm_config
        return f"runtime|{c.get('backend')}|{c.get('model')}|{c.get('api_key')}|{c.get('base_url')}"
    try:
        from desktop_env_config import load_vlm_config
        fc = load_vlm_config()
        if fc:
            return f"file|{fc.get('backend')}|{fc.get('model')}|{fc.get('api_key')}"
    except Exception:
        pass
    reg_sig = _get_current_model_registry_signature()
    if reg_sig:
        return f"registry|{reg_sig}"
    return ""


def _vvlm_needs_refresh() -> bool:
    """检测 VLM 缓存是否需要刷新（模型已变更）。"""
    global _cached_vlm_signature
    if _cached_vlm is None:
        return False
    current_sig = _vlm_config_signature()
    if not current_sig:
        return _cached_vlm._enabled
    if current_sig != _cached_vlm_signature:
        return True
    return False


def configure_vlm(config: Optional[Dict[str, Any]]) -> None:
    """运行时配置 VLM，无需修改环境变量或重启。

    Args:
        config: 配置字典，需包含 backend/api_key 等字段；传 None 清除运行时配置
    """
    global _runtime_vlm_config, _cached_vlm, _cached_vlm_signature
    _runtime_vlm_config = dict(config) if config else None
    _cached_vlm = None
    _cached_vlm_signature = ""
    if _runtime_vlm_config:
        uat_logger.info("VLM configured at runtime: backend=%s model=%s",
                        _runtime_vlm_config.get("backend"),
                        _runtime_vlm_config.get("model"))
    else:
        uat_logger.info("VLM runtime config cleared")


def get_runtime_vlm_config() -> Optional[Dict[str, Any]]:
    """获取当前运行时 VLM 配置（仅用于界面回显）。"""
    global _runtime_vlm_config
    if not _runtime_vlm_config:
        return None
    safe = dict(_runtime_vlm_config)
    if safe.get("api_key"):
        safe["api_key"] = safe["api_key"][:8] + "***"
    return safe


def is_vlm_ready() -> bool:
    """检查 VLM 是否就绪（自动复用当前模型配置）。"""
    vlm = get_vlm()
    return vlm.is_available()


def get_vlm_status() -> Dict[str, Any]:
    """获取 VLM 状态信息（供 UI 展示）。"""
    vlm = get_vlm()
    status = {
        "available": vlm.is_available(),
        "model": vlm._model,
        "backend": vlm._backend,
    }
    if _runtime_vlm_config:
        status["source"] = "runtime"
    else:
        try:
            from desktop_env_config import load_vlm_config
            if load_vlm_config():
                status["source"] = "file"
            else:
                from ai_selector_recovery import load_active_profile_for_inference
                profile = load_active_profile_for_inference()
                mid = (profile.get("model_id") or "") if isinstance(profile, dict) else ""
                status["source"] = "model_registry" if _model_supports_vision(mid) else "not_configured"
                status["current_model"] = mid
                status["model_supports_vision"] = _model_supports_vision(mid)
        except Exception:
            status["source"] = "unknown"
    return status


def get_vlm() -> VLMGrounding:
    global _cached_vlm, _cached_vlm_signature
    if _cached_vlm is not None and _vvlm_needs_refresh():
        uat_logger.info("VLM config changed, refreshing cached instance")
        _cached_vlm = None
        _cached_vlm_signature = ""
    if _cached_vlm is None:
        _cached_vlm = VLMGrounding()
        _cached_vlm_signature = _vlm_config_signature() or "__empty__"
    return _cached_vlm


def find_element_via_vlm(
    screenshot: bytes,
    description: str,
    platform: str = "desktop",
    screen_size: Optional[Tuple[int, int]] = None,
) -> Optional[Dict[str, Any]]:
    vlm = get_vlm()
    if screen_size:
        vlm.set_screen_size(screen_size[0], screen_size[1])
    return vlm.find_element(screenshot, description, platform)
