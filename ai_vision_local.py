"""
本地视觉 / OCR 辅助：Ollama 多模态（如 moondream、llava）+ 可选 Tesseract。
环境：LOCAL_VISION_ENABLE（面向用户默认开）；LOCAL_VISION_MODEL；OCR：LOCAL_OCR_ENABLE=1。
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import threading as _threading
import time as _time
from typing import Any, Dict, Optional

import requests
from requests.exceptions import RequestException

from ai_local_inference import _ollama_api_chat_assistant_text
from logger import uat_logger


class _VisionCircuitBreaker:
    def __init__(self, threshold=3, recovery=60):
        self._fails = 0
        self._threshold = threshold
        self._recovery = recovery
        self._open_until = 0.0
        self._lock = _threading.Lock()

    def allow(self):
        with self._lock:
            return _time.time() >= self._open_until

    def record_success(self):
        with self._lock:
            self._fails = 0

    def record_failure(self):
        with self._lock:
            self._fails += 1
            if self._fails >= self._threshold:
                self._open_until = _time.time() + self._recovery


_vision_breaker = _VisionCircuitBreaker()
_cloud_vision_breaker = _VisionCircuitBreaker()


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def vision_enabled() -> bool:
    return _env_bool("LOCAL_VISION_ENABLE", True)


def ocr_enabled() -> bool:
    return _env_bool("LOCAL_OCR_ENABLE", False)


def _base_url() -> str:
    return (os.environ.get("LOCAL_LLM_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def _vision_model() -> str:
    return (os.environ.get("LOCAL_VISION_MODEL") or "llava:7b").strip() or "llava:7b"


def _vision_timeout() -> int:
    raw = (os.environ.get("LOCAL_VISION_TIMEOUT") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 60


def _get_cloud_vision_config() -> dict:
    """从环境变量读取云端视觉模型配置。"""
    return {
        "provider": os.environ.get("CLOUD_VISION_PROVIDER", "").strip(),
        "api_key": os.environ.get("CLOUD_VISION_API_KEY", "").strip(),
        "base_url": os.environ.get("CLOUD_VISION_BASE_URL", "").strip(),
        "model": os.environ.get("CLOUD_VISION_MODEL", "gpt-4o").strip(),
    }


def ocr_region_png(image_bytes: bytes) -> str:
    """
    对整图或裁剪 PNG 做 OCR。需系统安装 tesseract，可选 chi_sim+eng。
    未安装或失败时返回空串。
    """
    if not image_bytes:
        return ""
    tess = shutil.which("tesseract")
    if not tess:
        uat_logger.debug("OCR: tesseract not on PATH")
        return ""
    lang = (os.environ.get("LOCAL_OCR_TESSERACT_LANG") or "chi_sim+eng").strip() or "chi_sim+eng"
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            path = f.name
        try:
            proc = subprocess.run(
                [tess, path, "stdout", "-l", lang],
                capture_output=True,
                text=True,
                timeout=90,
            )
            return (proc.stdout or "").strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except (OSError, subprocess.SubprocessError, FileNotFoundError) as e:
        uat_logger.debug("OCR failed: %s", e)
    return ""


def vision_describe_cloud(
    image_bytes: bytes,
    instruction: str,
    *,
    provider: str = "openai",
    api_key: str = "",
    base_url: str = "",
    model: str = "gpt-4o",
    timeout: int = 60,
) -> str:
    """调用云端多模态视觉模型（OpenAI 兼容 / Anthropic）。"""
    if not image_bytes:
        raise ValueError("image_bytes 不能为空")
    inst = (instruction or "").strip()
    if not inst:
        raise ValueError("instruction 不能为空")
    if not api_key:
        raise ValueError("api_key 不能为空")

    # 仅对 instruction 文本做脱敏，image 数据跳过
    try:
        from cloud_desensitizer import CloudDataDesensitizer

        inst, _, _ = CloudDataDesensitizer().sanitize_payload(inst)
    except Exception:
        pass

    b64 = base64.b64encode(image_bytes).decode("ascii")
    prov = (provider or "openai").strip().lower()
    base = (base_url or "").rstrip("/")
    if not base:
        if prov in ("openai", "openai_compatible"):
            base = "https://api.openai.com"
        elif prov == "anthropic":
            base = "https://api.anthropic.com"

    if prov in ("openai", "openai_compatible"):
        url = f"{base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": inst},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }
    elif prov == "anthropic":
        url = f"{base}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": inst},
                    ],
                }
            ],
        }
    else:
        raise ValueError(f"不支持的云端视觉 provider: {provider!r}")

    max_retries = 2
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            if prov == "anthropic":
                content_list = data.get("content") or []
                text = content_list[0].get("text", "") if content_list else ""
            else:
                choices = data.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    text = msg.get("content") or ""
                else:
                    text = ""
            return (text or "").strip()
        except RequestException as e:
            last_exc = e
            if attempt < max_retries:
                _time.sleep(2 ** (attempt + 1))
    raise ValueError(
        f"云端视觉模型请求失败 (provider={prov}, model={model}): {last_exc}"
    ) from last_exc


def vision_describe(image_bytes: bytes, instruction: str, model: Optional[str] = None) -> str:
    """
    Ollama /api/chat：单张图 + 文本指令。image 为 PNG/JPEG 原始字节。
    带熔断器 + 最多 2 次重试（指数退避 2s/4s）。
    当 CLOUD_VISION_PROVIDER 环境变量已配置时，优先走云端视觉模型；失败则回退本地。
    """
    if not image_bytes or not (instruction or "").strip():
        return ""

    # ── 云端优先 ──
    cloud_cfg = _get_cloud_vision_config()
    if cloud_cfg.get("provider"):
        if _cloud_vision_breaker.allow():
            try:
                result = vision_describe_cloud(
                    image_bytes,
                    instruction,
                    provider=cloud_cfg["provider"],
                    api_key=cloud_cfg["api_key"],
                    base_url=cloud_cfg["base_url"],
                    model=cloud_cfg.get("model") or model or "gpt-4o",
                )
                _cloud_vision_breaker.record_success()
                return result
            except Exception as e:
                _cloud_vision_breaker.record_failure()
                uat_logger.warning("云端视觉调用失败，回退本地 Ollama: %s", e)

    # ── 本地 Ollama ──
    if not _vision_breaker.allow():
        raise ValueError("本地视觉模型熔断中（连续失败过多，暂不可用）")
    b64 = base64.b64encode(image_bytes).decode("ascii")
    m = (model or _vision_model()).strip()
    url = f"{_base_url()}/api/chat"
    payload: Dict[str, Any] = {
        "model": m,
        "messages": [
            {
                "role": "user",
                "content": (instruction or "").strip(),
                "images": [b64],
            }
        ],
        "stream": False,
    }
    max_retries = 2
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=_vision_timeout())
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
            text = _ollama_api_chat_assistant_text(data) if isinstance(data, dict) else ""
            _vision_breaker.record_success()
            return (text or "").strip()
        except RequestException as e:
            last_exc = e
            if attempt < max_retries:
                _time.sleep(2 ** (attempt + 1))  # 2s, 4s
    _vision_breaker.record_failure()
    raise ValueError(
        f"本地视觉模型请求失败（确认 ollama pull {m} 且支持 /api/chat 图片）: {last_exc}"
    ) from last_exc


def text_visible_in_screenshot(
    image_bytes: bytes,
    expected_substring: str,
) -> bool:
    """
    在启用 LOCAL_VISION_ENABLE 时用语义模型判断是否「大致可见」expected；
    否则在 LOCAL_OCR_ENABLE 下用 tesseract 子串匹配。
    """
    exp = (expected_substring or "").strip()
    if not exp or not image_bytes:
        return False
    if vision_enabled():
        ins = (
            f'Does the UI in this image clearly show text that means or includes this substring (be lenient for CJK/encoding): "{exp[:500]}"? '
            "Reply with exactly YES or NO on the first line, then one short reason."
        )
        try:
            out = vision_describe(image_bytes, ins)
        except ValueError as e:
            uat_logger.warning("vision text check: %s", e)
            return False
        first = (out.splitlines() or [""])[0].strip().lower()
        return first.startswith("yes")
    if ocr_enabled():
        otxt = ocr_region_png(image_bytes)
        if not otxt:
            return False
        return exp.lower() in otxt.lower() or otxt.lower() in exp.lower()
    return False


def captcha_vision_solve(
    image_bytes: bytes,
    instruction: str = "",
    captcha_hint: str = "",
    model: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> str:
    """
    验证码 VLM 兜底：识别类型并返回结构化 JSON。
    示例：{"type":"slider","distance":123} 或 {"type":"click","points":[{"x":50,"y":80}]}
    """
    if not image_bytes or not vision_enabled():
        return ""
    ins_parts = [
        "You are a captcha-solving assistant. Analyze this captcha widget screenshot.",
        "Identify the captcha type and the action needed to pass it.",
        "Reply with ONLY a JSON object (no markdown), one of:",
        '  {"type":"slider","distance":<pixels to drag horizontally>}',
        '  {"type":"curve","distance":<pixels>}',
        '  {"type":"rotate","angle":<degrees>}',
        '  {"type":"click","points":[{"x":<px>,"y":<px>}, ...]}',
        '  {"type":"unknown"}',
        "Coordinates x,y are relative to the TOP-LEFT of the captcha image.",
    ]
    if instruction:
        ins_parts.append(f"Page instruction text: {instruction[:500]}")
    if captcha_hint:
        ins_parts.append(f"Hint: {captcha_hint[:300]}")
    ins = "\n".join(ins_parts)
    to = timeout_sec if timeout_sec is not None else int(
        (os.environ.get("CAPTCHA_VISION_TIMEOUT") or "25").strip() or "25"
    )
    to = max(5, min(to, 120))
    try:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        m = (model or _vision_model()).strip()
        url = f"{_base_url()}/api/chat"
        payload: Dict[str, Any] = {
            "model": m,
            "messages": [
                {
                    "role": "user",
                    "content": ins,
                    "images": [b64],
                }
            ],
            "stream": False,
        }
        resp = requests.post(url, json=payload, timeout=to)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        text = _ollama_api_chat_assistant_text(data) if isinstance(data, dict) else ""
        return (text or "").strip()
    except RequestException as e:
        uat_logger.warning("captcha vision solve timeout/error (%ss): %s", to, e)
        return ""
    except ValueError as e:
        uat_logger.warning("captcha vision solve: %s", e)
        return ""


