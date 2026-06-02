"""
本地视觉 / OCR 辅助：Ollama 多模态（如 moondream、llava）+ 可选 Tesseract。
环境：LOCAL_VISION_ENABLE=1，LOCAL_VISION_MODEL；OCR：LOCAL_OCR_ENABLE=1。
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

import requests
from requests.exceptions import RequestException

from ai_local_inference import _ollama_api_chat_assistant_text
from logger import uat_logger


def _env_bool(name: str, default: bool) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def vision_enabled() -> bool:
    return _env_bool("LOCAL_VISION_ENABLE", False)


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
    return 180


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


def vision_describe(image_bytes: bytes, instruction: str, model: Optional[str] = None) -> str:
    """
    Ollama /api/chat：单张图 + 文本指令。image 为 PNG/JPEG 原始字节。
    """
    if not image_bytes or not (instruction or "").strip():
        return ""
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
    try:
        resp = requests.post(url, json=payload, timeout=_vision_timeout())
        resp.raise_for_status()
    except RequestException as e:
        raise ValueError(
            f"本地视觉模型请求失败（确认 ollama pull {m} 且支持 /api/chat 图片）: {e}"
        ) from e
    data = resp.json() if resp.content else {}
    text = _ollama_api_chat_assistant_text(data) if isinstance(data, dict) else ""
    return (text or "").strip()


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


