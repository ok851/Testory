"""
画面智能确认（Insight）：自然语言断言、等待、信息提取。

面向普通用户默认开启；模型不可用时返回友好说明并由执行层降级或失败提示。
环境（仅显式 0/off 时关闭）：
  AI_VISION_INSIGHT_ENABLE=1
  AI_WAIT_VISION_ENABLE=1
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from modules.core.logger import uat_logger


def _env_bool(name: str, default: bool) -> bool:
    import os

    v = (os.environ.get(name) or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def insight_enabled() -> bool:
    return _env_bool("AI_VISION_INSIGHT_ENABLE", True)


def wait_vision_enabled() -> bool:
    return _env_bool("AI_WAIT_VISION_ENABLE", True)


def parse_yes_no_first_line(text: str) -> Optional[bool]:
    """解析模型首行 YES/NO。"""
    first = ((text or "").splitlines() or [""])[0].strip().lower()
    if not first:
        return None
    if first.startswith("yes") or first.startswith("是") or first in ("对", "true", "通过", "符合"):
        return True
    if first.startswith("no") or first.startswith("否") or first.startswith("不") or first in ("false", "未通过", "不符合"):
        return False
    if re.search(r"\byes\b", first):
        return True
    if re.search(r"\bno\b", first):
        return False
    return None


def _insight_model() -> str:
    import os

    return (
        (os.environ.get("AI_VISION_INSIGHT_MODEL") or os.environ.get("LOCAL_VISION_MODEL") or "llava:7b")
        .strip()
        or "llava:7b"
    )


def _vision_unavailable_reason() -> str:
    from modules.ai.ai_vision_local import vision_enabled

    if not insight_enabled():
        return "画面智能确认已关闭"
    if not vision_enabled():
        return "视觉服务暂不可用，请确认本机已安装并启动 Ollama 视觉模型"
    return "视觉服务暂不可用"


def assert_vision_condition_on_png(png_bytes: bytes, condition_nl: str) -> Tuple[bool, str]:
    """
    判断截图是否满足自然语言描述。返回 (通过, 用户可读原因)。
    """
    cond = (condition_nl or "").strip()
    if not cond:
        return False, "缺少画面确认描述"
    if not png_bytes:
        return False, "无法获取当前页面画面"
    if not insight_enabled():
        return False, _vision_unavailable_reason()
    from modules.ai.ai_vision_local import vision_enabled, vision_describe

    if not vision_enabled():
        return False, _vision_unavailable_reason()
    ins = (
        "请查看这张浏览器截图，判断下面这句话对当前用户所见画面是否成立。\n"
        f"描述：{cond[:800]}\n\n"
        "第一行仅回复 YES 或 NO；第二行用一句简短中文说明原因（给用户看）。"
    )
    try:
        raw = vision_describe(png_bytes, ins, model=_insight_model())
    except ValueError as e:
        uat_logger.warning("[VISION_INSIGHT] assert failed: %s", e)
        return False, "无法连接视觉模型，请稍后再试"
    verdict = parse_yes_no_first_line(raw)
    reason_lines = [ln.strip() for ln in (raw or "").splitlines()[1:] if ln.strip()]
    reason = reason_lines[0] if reason_lines else ""
    if verdict is True:
        return True, reason or "画面与描述一致"
    if verdict is False:
        friendly = reason or f"当前画面与您描述的不一致：{cond[:120]}"
        return False, friendly
    return False, reason or "无法确认画面是否符合描述，请换一种说法或稍后重试"


def extract_vision_from_png(png_bytes: bytes, prompt_nl: str) -> Tuple[str, str]:
    """从截图提取信息。返回 (文本, 错误说明)；成功时错误为空。"""
    prompt = (prompt_nl or "").strip()
    if not prompt:
        return "", "缺少提取说明"
    if not png_bytes:
        return "", "无法获取当前页面画面"
    if not insight_enabled():
        return "", _vision_unavailable_reason()
    from modules.ai.ai_vision_local import vision_enabled, vision_describe

    if not vision_enabled():
        return "", _vision_unavailable_reason()
    ins = (
        "请根据这张浏览器截图回答用户问题，只输出简洁中文结果，不要 JSON。\n"
        f"问题：{prompt[:800]}"
    )
    try:
        raw = vision_describe(png_bytes, ins, model=_insight_model())
    except ValueError as e:
        uat_logger.warning("[VISION_INSIGHT] extract failed: %s", e)
        return "", "无法连接视觉模型，请稍后再试"
    text = (raw or "").strip()
    if not text:
        return "", "未能从画面中提取到信息"
    return text, ""
