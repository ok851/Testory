import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.exceptions import RequestException

from ai_page_probe import (
    build_locator_candidates_from_probe_entry,
    extract_http_urls,
    registry_step_selector_warnings,
    validate_plan_locators,
)
from ai_step_normalization import is_overly_broad_css_selector, repair_raw_ai_steps_for_platform

_log = logging.getLogger(__name__)


def _norm_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _ollama_request_user_message(detail: str, *, tools: bool = False) -> str:
    """
    将底层 HTTP 异常转成面向用户的说明。
    「能列出模型」只调用轻量接口；改步骤会走 /api/chat，耗时可差 orders of magnitude。
    """
    d = (detail or "").strip() or "请求异常（无详细文本）"
    head = "本地模型推理失败（带工具调用）。" if tools else "本地模型推理失败。"
    low = d.lower()
    timed_out = "read timed out" in low or "timed out" in low
    parts = [
        head,
        f"底层信息：{d}",
        "",
        "说明：平台「刷新模型列表」只访问 Ollama 的轻量接口；真正生成/改写步骤会 POST /api/chat，负载大得多，耗时可从几秒到十几分钟（取决于模型体积、是否 GPU、是否首次加载权重）。",
        "",
    ]
    if timed_out:
        parts.extend(
            [
                "本次表现为等待超时（读超时由 LOCAL_LLM_TIMEOUT_CHAT 或 LOCAL_LLM_TIMEOUT 控制，单位：秒；"
                "未设置任一变量时，/api/chat 默认 600 秒）。建议：",
                "1）若模型名含「-vl」等多模态后缀，多为视觉模型，在本场景只做文字 JSON 时往往极慢，建议在「AI测试」(/ai-test) 换成纯文本 instruct（如 qwen2.5、llama3 等）；",
                "2）在本机终端执行：ollama run <模型名>，随意对话一行，确认首次拉权重与速度正常；",
                "3）机器较慢时设置 LOCAL_LLM_TIMEOUT_CHAT（仅影响生成/对话类 POST）或 LOCAL_LLM_TIMEOUT 后重启 HuFirst；",
                "4）确认 LOCAL_LLM_BASE_URL 指向正在跑推理的地址（默认 http://127.0.0.1:11434）。",
            ]
        )
    else:
        parts.extend(
            [
                "请确认：ollama serve 已运行；ollama list 包含所选模型；模型名称与配置一致；",
                "必要时检查 LOCAL_LLM_BASE_URL；若是 HTTP 4xx/5xx，可看上述底层信息中的状态码与返回摘要。",
            ]
        )
    return "\n".join(parts)


def _strip_markdown_code_fence(text: str) -> str:
    """Remove leading/trailing ``` or ```json fences often added by chat models."""
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _strip_llm_noise(text: str) -> str:
    """去掉零宽字符与部分模型包裹的推理块（避免在源码中写入易被工具误处理的长标签名）。"""
    s = (text or "").strip().replace("\ufeff", "")
    if not s:
        return s
    # 常见推理标签（拆成 chr 拼接，避免静态扫描误伤）
    _ot = chr(60) + chr(116) + chr(104) + chr(105) + chr(110) + chr(107) + chr(62)
    _ct = chr(60) + chr(47) + chr(116) + chr(104) + chr(105) + chr(110) + chr(107) + chr(62)
    pat_think = re.escape(_ot) + r"[\s\S]*?" + re.escape(_ct)
    noise_patterns = (
        pat_think,
        r"<reasoning\b[^>]*>[\s\S]*?</reasoning>",
    )
    for _ in range(6):
        prev = s
        for pat in noise_patterns:
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
        if s == prev:
            break
    return s.strip()


def _ollama_api_chat_assistant_text(data: Any) -> str:
    """
    将 Ollama POST /api/chat 的非流式 JSON 中的 assistant 文本规整为单字符串。
    兼容 content 为 str、多段 list、以及仅填充 message.thinking 的推理模型。
    若存在顶层 error 字段则抛出说明。
    """
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if err is not None and err != "" and err != False:
        if isinstance(err, dict):
            em = _norm_str(err.get("message")) or json.dumps(err, ensure_ascii=False)[:400]
            raise ValueError(f"Ollama 返回错误：{em}")
        raise ValueError(f"Ollama 返回错误：{err!r}")

    msg = data.get("message")
    if not isinstance(msg, dict):
        return ""

    def _chunk_to_str(raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            parts: List[str] = []
            for item in raw:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    tx = item.get("text")
                    if isinstance(tx, str):
                        parts.append(tx)
                    else:
                        c = item.get("content")
                        if isinstance(c, str):
                            parts.append(c)
                        elif isinstance(c, list):
                            parts.append(_chunk_to_str(c))
            return "".join(parts)
        return str(raw)

    main = _chunk_to_str(msg.get("content")).strip()
    if main:
        return main
    think = msg.get("thinking")
    if isinstance(think, str) and think.strip():
        return think.strip()
    return ""


def _normalize_smart_quotes_for_json(text: str) -> str:
    """部分中文模型会输出弯引号，尝试替换为 ASCII 引号后再解析。"""
    s = text or ""
    return (
        s.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """First top-level `{ ... }` span; respects double-quoted JSON strings."""
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        i = start
        while i < len(text):
            c = text[i]
            if escape:
                escape = False
                i += 1
                continue
            if in_string:
                if c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
                i += 1
                continue
            if c == '"':
                in_string = True
                i += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
            i += 1
        start = text.find("{", start + 1)
    return None


def _trim_leading_until_first_brace(text: str) -> str:
    s = (text or "").strip()
    i = s.find("{")
    return s[i:] if i >= 0 else s


def _repair_json_trailing_commas(text: str) -> str:
    s = text or ""
    for _ in range(32):
        s2 = re.sub(r",(\s*[}\]])", r"\1", s)
        if s2 == s:
            break
        s = s2
    return s


def _fence_extract_blocks(text: str) -> List[str]:
    if not (text or "").strip():
        return []
    out: List[str] = []
    for m in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", text, re.I):
        inner = (m.group(1) or "").strip()
        if inner:
            out.append(inner)
    return out


def _deep_json_parse_string(val: Any) -> Any:
    """若值为可被 json.loads 的字符串（含多重字符串包裹），尽量剥到 dict/list。"""
    cur: Any = val
    for _ in range(5):
        if not isinstance(cur, str):
            return cur
        s = cur.strip()
        if not s:
            return cur
        try:
            cur = json.loads(s)
        except json.JSONDecodeError:
            return cur
    return cur


def _coerce_plan_root(val: Any) -> Optional[Dict[str, Any]]:
    val = _deep_json_parse_string(val)
    if isinstance(val, dict):
        return val
    if isinstance(val, list):
        if not val:
            return None
        if not all(isinstance(x, dict) for x in val):
            return None
        if val and _norm_str(val[0].get("action")):
            return {
                "case_name": "",
                "case_url": "",
                "description": "",
                "precondition": "",
                "expected_result": "",
                "steps": val,
            }
        for x in val:
            if isinstance(x, dict) and isinstance(x.get("steps"), list):
                return x
    return None


def _collect_json_try_strings(raw: str) -> List[str]:
    """生成一组待尝试解析的子串。"""
    s = (raw or "").strip()
    out: List[str] = []
    seen = set()

    def push(x: str) -> None:
        t = (x or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    push(s)
    for blk in _fence_extract_blocks(s):
        push(blk)
    trimmed = _trim_leading_until_first_brace(s)
    push(trimmed)
    push(_strip_markdown_code_fence(trimmed))
    push(_strip_markdown_code_fence(s))

    pos = 0
    while pos < len(s):
        j = s.find("{", pos)
        if j < 0:
            break
        frag = _extract_balanced_json_object(s[j:])
        if frag:
            push(frag)
            pos = j + max(len(frag), 1)
        else:
            pos = j + 1
    return out


def _infer_input_value_for_input_action(description: str, goal: str) -> str:
    """
    When the model leaves input_value empty for action=input, recover text from
    description (e.g. 「例如：自动化测试」) or goal (e.g. 在百度搜索自动化测试).
    """
    desc = _norm_str(description)
    goal = _norm_str(goal)
    # （例如：自动化测试）
    m = re.search(r"（\s*(?:例如|比如|如)\s*[：:]\s*([^）]+)）", desc)
    if m:
        return _norm_str(m.group(1))
    # (e.g. foo)
    m = re.search(r"\(\s*(?:e\.g\.|eg\.)\s*[：:]?\s*([^)]+)\)", desc, re.I)
    if m:
        return _norm_str(m.group(1))
    # 例如：xxx / 比如：xxx（行内，不含括号包裹）
    m = re.search(r"(?:例如|比如|如)\s*[：:]\s*([^\s）\)\n。,，;；]+)", desc)
    if m:
        return _norm_str(m.group(1))
    # 「关键词」
    m = re.search(r"「([^」]{1,200})」", desc)
    if m:
        return _norm_str(m.group(1))
    for text in (goal, desc):
        if not text:
            continue
        m = re.search(r"搜索\s*[「\"]?([^」\"\n]{1,120}?)(?:\s|$|的|，|。|？|；|；)", text)
        if m:
            cand = _norm_str(m.group(1))
            if cand and len(cand) < 220:
                return cand
        m = re.search(r"搜索\s*([^\s，。,；;\n]{1,120})", text)
        if m:
            cand = _norm_str(m.group(1))
            if cand and len(cand) < 220:
                return cand
    return ""


def _infer_verify_type_for_captcha_action(description: str, goal: str = "") -> str:
    """
    平台「验证操作」步骤的 input_value 表示验证码/人机校验类型（auto|slider|image|visible|exist|clickable），
    不是页面文案断言；文案类校验应使用 action assert + compare_type。
    仅在描述/目标明显涉及验证码时推断，否则返回空字符串（运行时默认 auto）。
    """
    blob = (_norm_str(description) + " " + _norm_str(goal)).lower()
    zh = _norm_str(description) + _norm_str(goal)
    if any(k in zh for k in ("滑块", "拖动验证")) or "slider" in blob:
        return "slider"
    if any(k in zh for k in ("曲线", "旋转", "滑动还原", "滑动曲线")) or "curve" in blob or "rotate" in blob:
        return "auto"
    if any(k in zh for k in ("拼图", "点选验证", "图片验证码", "图形验证")) or "image captcha" in blob:
        return "image"
    if any(k in zh for k in ("验证码", "人机验证", "智能验证", "安全验证", "天爱", "tianai")) or "captcha" in blob:
        return "auto"
    if "可见" in zh or re.search(r"\bvisible\b", blob):
        return "visible"
    if "可点击" in zh or "clickable" in blob:
        return "clickable"
    if "存在" in zh and "验证码" not in zh and "人机" not in zh:
        return "exist"
    return ""


def _first_http_url(*parts: str) -> str:
    for p in parts:
        for u in extract_http_urls(p or ""):
            u = u.rstrip(").,]}>'\"")
            if u.startswith("http://") or u.startswith("https://"):
                return u.split()[0]
    return ""


def _placeholder_template_case_url(case_url: str) -> bool:
    """模型常误填的占位站点，与中文「百度搜索」等目标冲突时应丢弃。"""
    cu = (case_url or "").lower()
    if not cu:
        return False
    return (
        "example.com" in cu
        or "example.org" in cu
        or "example.net" in cu
        or "testpages.adobe.com" in cu
        or "saucedemo.com" in cu
    )


def _goal_suggests_seed_url(goal: str) -> str:
    """
    当目标里未出现可解析的 http(s) 链接时，用常见门户补全 navigate/case_url，
    以便站点级选择器回退（如百度 #kw）能生效。
    """
    raw = _norm_str(goal)
    if not raw:
        return ""
    g = raw.lower()
    if "百度" in raw or "baidu" in g:
        if any(x in g for x in ("搜索", "搜一下", "检索", "query", "search")):
            return "https://www.baidu.com/"
        if any(k in raw for k in ("百度首页", "打开百度", "百度网站", "上百度")):
            return "https://www.baidu.com/"
    return ""


def _same_nav_host(url_a: str, url_b: str) -> bool:
    try:
        from urllib.parse import urlparse

        ha = urlparse(url_a or "").netloc.lower()
        hb = urlparse(url_b or "").netloc.lower()
        return bool(ha and hb and ha == hb)
    except Exception:
        return False


def _infer_navigate_url(
    goal: str,
    description: str,
    case_url: str,
    mandatory_base_url: str = "",
) -> str:
    mb = _norm_str(mandatory_base_url)
    if mb:
        return mb
    u = _first_http_url(case_url, goal, description)
    if u:
        return u
    return _goal_suggests_seed_url(goal)


def _infer_wait_input_value(description: str) -> str:
    """Seconds 1–120 or milliseconds; default a small wait if model left empty."""
    desc = _norm_str(description)
    if not desc:
        return "3"
    m = re.search(r"(\d{1,6})\s*(?:毫秒|ms)\b", desc, re.I)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*(?:秒|秒钟)(?!\s*毫秒)", desc)
    if m:
        sec = int(m.group(1))
        return str(min(120, max(1, sec)))
    m = re.search(r"(\d+)\s*[-~～]\s*(\d+)\s*秒", desc)
    if m:
        lo = int(m.group(1))
        return str(min(120, max(1, lo)))
    m = re.search(r"(?:等待|延时|停顿)\s*(\d{1,4})", desc)
    if m:
        v = int(m.group(1))
        if v > 120:
            return str(v)
        return str(max(1, v))
    return "3"


def _extract_selector_from_description(description: str) -> Tuple[str, str]:
    """Parse css:/xpath:/裸 #id from model text."""
    desc = _norm_str(description)
    if not desc:
        return "", ""
    m = re.search(r"(?:css|CSS)[:：]\s*([^\s|，。\n]+)", desc)
    if m:
        cap = m.group(1).strip()
        # 模型常把快照行号误写成 css:12；纯数字不是合法 CSS，忽略以免污染 selector_value
        if cap.isdigit():
            return "", ""
        return "css", cap
    m = re.search(r"(?:xpath|XPath)[:：]\s*([^\n]+)", desc)
    if m:
        return "xpath", m.group(1).strip()[:800]
    m = re.search(r"(?<![\w#])(\#[\w\-]{1,120})\b", desc)
    if m:
        return "css", m.group(1)
    m = re.search(r"\b(id|name)\s*[:=]\s*([\w\-]{1,120})", desc, re.I)
    if m:
        key, val = m.group(1).lower(), m.group(2)
        if key == "id":
            return "css", f"#{val}"
        return "css", f'[{key}="{val}"]'
    return "", ""


def _map_probe_selector_type(rty: str) -> str:
    rty = (rty or "").strip().lower()
    if rty in ("css", "xpath", "text"):
        return rty
    if rty == "partial_text":
        return "text"
    return "css"


def _probe_pick_selector(
    description: str,
    registry: Optional[List[Dict[str, Any]]],
    action: str,
) -> Tuple[str, str]:
    """Match probe registry row to description keywords (click vs input)."""
    if not registry or not isinstance(registry, list):
        return "", ""
    desc = _norm_str(description)
    if not desc:
        return "", ""
    prefer_in = action == "input"
    prefer_click = action in ("click", "extract_text")
    prefer_captcha = action == "verify"
    prefer_assert = action == "assert"
    best: Tuple[int, str, str] = (-1, "", "")
    threshold = 8 if (prefer_captcha or prefer_assert) else 5

    for ent in registry:
        if not isinstance(ent, dict):
            continue
        rec = _norm_str(ent.get("recommended_selector"))
        rty = _map_probe_selector_type(_norm_str(ent.get("recommended_selector_type")))
        if not rec:
            continue
        tag = _norm_str(ent.get("tag")).lower()
        typ = _norm_str(ent.get("typ")).lower()
        txt = _norm_str(ent.get("txt"))
        al = _norm_str(ent.get("al"))
        ph = _norm_str(ent.get("ph"))
        rid = _norm_str(ent.get("rid")).lower()
        blob = f"{txt} {al} {ph}".lower()
        score = 0
        if prefer_captcha:
            if any(k in desc for k in ("验证码", "人机", "滑块", "拼图", "点选", "安全验证")):
                rl = rec.lower()
                if any(x in rl for x in ("verify", "captcha", "geetest", "tcaptcha", "slider", "seccode")):
                    score += 14
                if "iframe" in rl or "shadow" in rl:
                    score += 3
        elif prefer_assert:
            if any(k in desc for k in ("标题", "结果", "页面", "包含", "预期", "断言")):
                rl = rec.lower()
                if any(x in rl for x in ("result", "title", "c-container", "content_left")):
                    score += 12
                if tag in ("h3", "a", "div"):
                    score += 2
        elif prefer_in:
            if tag in ("input", "textarea") or rid in ("textbox", "searchbox", "combobox"):
                score += 4
            if typ in ("text", "search", "textarea", ""):
                score += 1
            if any(k in desc for k in ("搜索", "关键词", "输入", "填写")) and (
                "搜" in blob or "search" in blob or ph or tag == "textarea"
            ):
                score += 8
        elif prefer_click:
            if tag in ("button", "a", "input") or rid == "button":
                score += 3
            if typ == "submit":
                score += 5
            if any(k in desc for k in ("点击", "按钮", "提交", "搜索")) and (
                "搜索" in desc
                or "百度" in desc
                or "提交" in blob
                or "搜" in blob
                or typ == "submit"
            ):
                score += 7
            if "按钮" in desc and tag == "button":
                score += 4
            # 描述中的具体文案与控件可见文本/aria 对齐（导出、订单列表等），避免只匹配到首个 primary 按钮
            dlow = desc.lower()
            if txt and len(txt) >= 2 and txt.lower() in dlow:
                score += 22
            if al and len(al) >= 2 and al.lower() in dlow:
                score += 20
            if ph and len(ph) >= 2 and ph.lower() in dlow:
                score += 16
            for pat in (
                r"「([^」]{2,40})」",
                "\u201c([^\u201d]{2,40})\u201d",
                r'"([^"]{2,40})"',
                r"'([^']{2,40})'",
            ):
                for qm in re.finditer(pat, desc):
                    qn = (qm.group(1) or "").strip()
                    if len(qn) >= 2 and qn.lower() in blob:
                        score += 26
        if score > best[0]:
            best = (score, rty, rec)

    if best[0] >= threshold:
        return best[1], best[2]
    return "", ""


def _fallback_site_selectors(case_url: str, description: str, action: str, goal: str = "") -> Tuple[str, str]:
    """Known baidu.com layouts when model omits selectors."""
    seed = _goal_suggests_seed_url(goal or "")
    u = (case_url or "").lower()
    if "baidu.com" not in u and seed and "baidu.com" in seed.lower():
        u = "https://www.baidu.com/"
    d = _norm_str(description)
    if "baidu.com" not in u or not d:
        return "", ""
    if action == "input" and any(k in d for k in ("搜索", "关键词", "输入")):
        if any(k in d for k in ("对话", "chat", "Chat", "AI")):
            return "css", "#chat-textarea"
        return "css", "#kw"
    if action == "click" and any(k in d for k in ("搜索", "提交", "百度一下")):
        # 「在搜索框输入…」类描述更像 input(#kw)，勿误匹配「百度一下」按钮
        if any(k in d for k in ("搜索框", "输入框", "输入关键词", "键入", "填入")):
            return "", ""
        if any(k in d for k in ("对话", "chat", "AI")):
            return "css", "#chat-submit-button"
        return "css", "#su"
    if action == "extract_text" and "结果" in d:
        return "css", ".result"
    if action == "assert" and any(k in d for k in ("结果", "标题", "搜索", "断言", "预期")):
        return "css", ".result-title"
    return "", ""


_VERIFY_INPUT_TYPES = frozenset({"auto", "slider", "image", "visible", "exist", "clickable"})


def _coerce_misused_verify_to_assert(step: Dict[str, Any]) -> None:
    """若 verify 的 input_value 不是合法 verify_type，视为误用；改为 assert（文案/元素断言）。"""
    action = _norm_str(step.get("action")).lower()
    if action != "verify":
        return
    iv = _norm_str(step.get("input_value"))
    if not iv:
        return
    if iv.lower() in _VERIFY_INPUT_TYPES:
        return
    step["action"] = "assert"
    desc = _norm_str(step.get("description"))
    ct = "text_contains"
    if any(x in desc for x in ("等于", "完全一致", "精确匹配")) or "equals" in desc.lower():
        ct = "text_equals"
    if "正则" in desc or "regex" in desc.lower():
        ct = "text_regex"
    step["compare_type"] = ct


def clamp_plan_steps_to_probe_registry(
    steps: List[Dict[str, Any]],
    probe_registry: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """
    当存在页面探测注册表时，将步骤中的 selector 约束在「真实 DOM 探测」结果内：
    - 若填了 probe_index，则强制使用该行的 recommended_selector（覆盖模型乱写的值）；
    - 若未填 probe_index 但 selector 与任一行均不一致，则尝试按步骤描述从注册表重选，否则仅记录告警。
    可通过 LOCAL_AI_SELECTOR_CLAMP=0 关闭。
    """
    warnings: List[str] = []
    if not probe_registry or not isinstance(probe_registry, list):
        return warnings
    if os.environ.get("LOCAL_AI_SELECTOR_CLAMP", "1").strip().lower() in ("0", "false", "no", "off"):
        return warnings

    probe_by_index: Dict[int, Dict[str, Any]] = {}
    for ent in probe_registry:
        if not isinstance(ent, dict):
            continue
        try:
            ii = int(ent.get("i"))
        except (TypeError, ValueError):
            continue
        probe_by_index[ii] = ent

    def _step_skip_clamp(step: Dict[str, Any]) -> bool:
        st = _norm_str(step.get("selector_type")).lower()
        if st in ("viewport_coord", "visual_template"):
            return True
        sv = _norm_str(step.get("selector_value"))
        if sv.startswith("{") and ("fx" in sv or "fy" in sv or "png_b64" in sv):
            return True
        return False

    def _selector_authorized(sv: str, st: str, ent: Dict[str, Any]) -> bool:
        if not sv:
            return False
        st = (st or "css").lower()
        rec = _norm_str(ent.get("recommended_selector"))
        if rec:
            if sv == rec:
                return True
            rx = rec.strip()
            sx = sv.strip()
            if sx.lower().startswith("xpath:"):
                sx = sx[6:].strip()
            if rx.lower().startswith("xpath:"):
                rx = rx[6:].strip()
            if sx == rx:
                return True
        css = _norm_str(ent.get("css"))
        if css and sv == css:
            return True
        tid = _norm_str(ent.get("id"))
        if tid and re.match(r"^[\w-]+$", tid) and sv in (f"#{tid}", f"#{tid.lower()}"):
            return True
        if st == "text":
            for k in ("ph", "al", "txt"):
                v = _norm_str(ent.get(k))
                if v and sv == v:
                    return True
        return False

    for idx, step in enumerate(steps):
        if not isinstance(step, dict) or _step_skip_clamp(step):
            continue
        action = _norm_str(step.get("action")).lower()
        if action in ("navigate", "wait", ""):
            continue
        if action not in ("click", "input", "verify", "extract_text", "assert"):
            continue
        if action == "assert":
            ct = _norm_str(step.get("compare_type")).lower()
            if ct in ("url_equals", "url_contains") and not _norm_str(step.get("selector_value")):
                continue

        desc = _norm_str(step.get("description"))
        st = _norm_str(step.get("selector_type")).lower() or "css"
        sv = _norm_str(step.get("selector_value"))

        pi: Optional[int] = None
        raw_pi = step.get("probe_index")
        if raw_pi is not None and _norm_str(raw_pi) != "":
            try:
                pi = int(float(str(raw_pi).strip()))
            except (TypeError, ValueError):
                pi = None

        if pi is not None and pi in probe_by_index:
            ent = probe_by_index[pi]
            rec = _norm_str(ent.get("recommended_selector"))
            rty = _map_probe_selector_type(_norm_str(ent.get("recommended_selector_type")))
            if rec:
                if sv and sv != rec and not _selector_authorized(sv, st, ent):
                    warnings.append(
                        f"第{idx + 1}步 probe_index={pi} 与 selector_value 不一致，已强制改为该行 recommended：{rec}"
                    )
                step["selector_value"] = rec
                step["selector_type"] = rty if rty in ("css", "xpath", "text") else "css"
                try:
                    lc = build_locator_candidates_from_probe_entry(ent)
                    if lc:
                        step["locator_candidates"] = lc
                except Exception:
                    pass
            continue

        if not sv:
            st2, sv2 = _probe_pick_selector(desc, probe_registry, action)
            if sv2:
                step["selector_type"] = st2 or "css"
                step["selector_value"] = sv2
                warnings.append(f"第{idx + 1}步缺少选择器，已按描述从 LIVE 注册表补全：{sv2}")
            continue

        ok = False
        for ent in probe_registry:
            if isinstance(ent, dict) and _selector_authorized(sv, st, ent):
                ok = True
                break
        if ok and action in ("click", "input") and is_overly_broad_css_selector(sv):
            ok = False
        if ok:
            continue

        st2, sv2 = _probe_pick_selector(desc, probe_registry, action)
        if sv2:
            warnings.append(
                f"第{idx + 1}步 selector {sv!r} 未出现在 LIVE 探测结果中，已按描述改为注册表内定位：{sv2}"
            )
            step["selector_type"] = st2 or "css"
            step["selector_value"] = sv2
        else:
            warnings.append(
                f"第{idx + 1}步 selector {sv!r} 与 LIVE 列表均不匹配且无法自动重选，"
                f"请使用 probe_index 绑定 [n] 或人工修改（描述：{desc[:60]!r}…）。"
            )
    return warnings


class LocalAIService:
    """
    Local LLM inference service (Ollama-compatible by default).
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.model_light = os.environ.get("LOCAL_LLM_MODEL_LIGHT", "qwen2:1.5b")
        self.model_mid = os.environ.get("LOCAL_LLM_MODEL_MID", "llama3:8b-instruct")
        try:
            self.connect_timeout = max(
                5,
                int((os.environ.get("LOCAL_LLM_CONNECT_TIMEOUT") or "30").strip() or "30"),
            )
        except ValueError:
            self.connect_timeout = 30

        def _read_timeout_seconds() -> int:
            """Ollama /api/chat 读超时：CHAT 优先，否则通用 TIMEOUT；都未设时默认 600。"""
            chat_raw = (os.environ.get("LOCAL_LLM_TIMEOUT_CHAT") or "").strip()
            base_raw = (os.environ.get("LOCAL_LLM_TIMEOUT") or "").strip()
            for raw in (chat_raw, base_raw):
                if not raw:
                    continue
                try:
                    return max(30, int(raw))
                except ValueError:
                    continue
            return 600

        self.chat_read_timeout = _read_timeout_seconds()
        # 与历史代码兼容：外部若读取 .timeout，视为当前 chat 读超时
        self.timeout = self.chat_read_timeout

    def _ollama_http_timeout(self) -> Tuple[int, int]:
        """(connect, read) for requests — 连接失败尽快报错，生成允许长时间读。"""
        return (self.connect_timeout, self.chat_read_timeout)

    def list_installed_models(self, timeout: int = 8) -> Tuple[List[str], Optional[str]]:
        """
        Query Ollama-compatible /api/tags. Returns (model_names, error_message).
        """
        url = f"{self.base_url}/api/tags"
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
        except RequestException as e:
            return [], f"无法连接本地模型服务（{self.base_url}）：{e}"
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            return [], "本地模型服务返回了非 JSON 响应"
        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            return [], None
        names: List[str] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            n = _norm_str(item.get("name"))
            if n:
                names.append(n)
        names = sorted(set(names))
        return names, None

    def generate_case_and_steps(
        self,
        goal: str,
        project_name: str = "",
        model: str = "",
        profile: Optional[Dict[str, Any]] = None,
        page_snapshot: Optional[str] = None,
        probe_registry: Optional[List[Dict[str, Any]]] = None,
        probe_url: Optional[str] = None,
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
        platform_type: str = "web",
        mandatory_base_url: str = "",
    ) -> Dict[str, Any]:
        snap_t = (page_snapshot or "").strip()
        pr: List[Dict[str, Any]] = list(probe_registry) if probe_registry else []
        pu = (probe_url or "").strip() or None
        dom_t = (dom_context_pack or "").strip()
        if dom_t:
            dom_t = self._maybe_compress_dom_pack(dom_t, profile, model)
        mbu = (mandatory_base_url or probe_url or "").strip()
        prompt = self._build_prompt(
            goal,
            project_name,
            page_snapshot=snap_t,
            memory_context=(memory_context or "").strip() or None,
            dom_context_pack=dom_t or None,
            platform_type=(platform_type or "web").strip().lower(),
            mandatory_base_url=mbu,
        )
        using_model, content = self._complete_for_model(
            prompt, model, profile, meta_fallback=self.model_mid
        )
        parsed = self._parse_plan_with_plain_retry(content, prompt=prompt, model=model, profile=profile)
        out = self._normalize_output(
            parsed, goal, project_name, using_model, probe_registry=pr if pr else None
        )
        meta = out.setdefault("meta", {})
        if profile and isinstance(profile, dict):
            meta["provider"] = profile.get("provider") or "cloud"
            meta["profile_id"] = profile.get("id") or ""
            meta["model"] = using_model
        else:
            meta["provider"] = "local"
        self._attach_locator_validation(meta, pu, out.get("steps") or [], probe_registry=pr if pr else None)
        return out

    def refine_case_and_steps(
        self,
        user_message: str,
        project_name: str = "",
        current_plan: Dict[str, Any] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        model: str = "",
        profile: Optional[Dict[str, Any]] = None,
        page_snapshot: Optional[str] = None,
        probe_registry: Optional[List[Dict[str, Any]]] = None,
        probe_url: Optional[str] = None,
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
        interaction_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snap_t = (page_snapshot or "").strip()
        pr: List[Dict[str, Any]] = list(probe_registry) if probe_registry else []
        pu = (probe_url or "").strip() or None
        dom_t = (dom_context_pack or "").strip()
        if dom_t:
            dom_t = self._maybe_compress_dom_pack(dom_t, profile, model)
        prompt = self._build_refine_prompt(
            user_message=user_message,
            project_name=project_name,
            current_plan=current_plan or {},
            history=history or [],
            page_snapshot=snap_t,
            memory_context=(memory_context or "").strip() or None,
            dom_context_pack=dom_t or None,
            interaction_context=interaction_context,
        )
        using_model, content = self._complete_for_model(
            prompt, model, profile, meta_fallback=self.model_mid
        )
        parsed = self._parse_plan_with_plain_retry(content, prompt=prompt, model=model, profile=profile)
        out = self._normalize_output(
            parsed, user_message, project_name, using_model, probe_registry=pr if pr else None
        )
        meta = out.setdefault("meta", {})
        if profile and isinstance(profile, dict):
            meta["provider"] = profile.get("provider") or "cloud"
            meta["profile_id"] = profile.get("id") or ""
            meta["model"] = using_model
        else:
            meta["provider"] = "local"
        self._attach_locator_validation(meta, pu, out.get("steps") or [], probe_registry=pr if pr else None)
        return out

    def _complete_for_model(
        self,
        prompt: str,
        model: str,
        profile: Optional[Dict[str, Any]],
        meta_fallback: str,
    ) -> Tuple[str, str]:
        if profile and isinstance(profile, dict):
            from ai_multi_provider import dispatch_chat

            mid = (profile.get("model_id") or "").strip() or meta_fallback
            label = (profile.get("label") or "").strip()
            using_model = label or mid
            content = dispatch_chat(prompt, profile, self)
            return using_model, content
        using_model = (model or "").strip() or meta_fallback
        content = self._chat_completion(prompt, using_model)
        return using_model, content

    def _parse_plan_with_plain_retry(
        self,
        content: str,
        *,
        prompt: str,
        model: str,
        profile: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            return self._parse_json_response(content)
        except ValueError as first_err:
            if profile or os.environ.get("LOCAL_LLM_JSON_RETRY_PLAIN", "1").strip().lower() in (
                "0",
                "false",
                "no",
                "off",
            ):
                raise
            mid = (model or "").strip() or self.model_mid
            _log.warning("AI 用例 JSON 解析失败，将在不使用 Ollama format=json 的情况下重试一次（model=%s）", mid)
            try:
                content2 = self._chat_completion(prompt, mid, json_format=False)
                return self._parse_json_response(content2)
            except Exception:
                raise first_err

    def chat_ollama(self, prompt: str, model: str, base_url: Optional[str] = None) -> str:
        root = (base_url or "").strip().rstrip("/") or self.base_url
        return self._chat_completion_at(prompt, model, root)

    def _chat_completion(self, prompt: str, model: str, *, json_format: Optional[bool] = None) -> str:
        return self._chat_completion_at(prompt, model, self.base_url, json_format=json_format)

    def _chat_completion_at(
        self,
        prompt: str,
        model: str,
        base_url: str,
        *,
        json_format: Optional[bool] = None,
    ) -> str:
        url = f"{base_url.rstrip('/')}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior QA engineer. Output must be exactly one JSON object—nothing else. "
                        "No markdown fences, no commentary, no trailing text. "
                        "First non-whitespace character must be '{'; last must be '}'. "
                        "Schema: AI-assisted web test plan with case_name, case_url, description, precondition, expected_result, steps[]."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": self._ollama_options(),
        }
        use_json_format = json_format
        if use_json_format is None:
            use_json_format = os.environ.get("LOCAL_LLM_JSON_FORMAT", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        if use_json_format:
            payload["format"] = "json"
        try:
            resp = requests.post(url, json=payload, timeout=self._ollama_http_timeout())
            resp.raise_for_status()
        except RequestException as e:
            detail = str(e).strip() or type(e).__name__
            response = getattr(e, "response", None)
            if response is not None:
                try:
                    body = (response.text or "").strip().replace("\n", " ")[:480]
                    detail = f"HTTP {response.status_code}" + (f": {body}" if body else "")
                except Exception:
                    detail = f"HTTP {response.status_code}: {detail}"
            raise ValueError(_ollama_request_user_message(detail, tools=False)) from e
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            raise ValueError("本地模型返回非 JSON 或结构异常")
        text = _ollama_api_chat_assistant_text(data)
        if not text:
            preview = ""
            try:
                preview = json.dumps(data, ensure_ascii=False)[:500]
            except Exception:
                preview = str(data)[:500]
            raise ValueError(
                "本地模型返回为空（assistant 无文本）。可尝试：① 换用 qwen2.5、llama3.1 等 instruct 模型；"
                "② 设置环境变量 LOCAL_LLM_JSON_FORMAT=0 后重试；③ 执行 ollama ps 确认无卡死、显存足够。"
                f" 响应摘要：{preview!r}"
            )
        return text.strip()

    def chat_ollama_messages(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        base_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Multi-turn Ollama /api/chat with optional tool definitions (OpenAI-compatible tool schema).
        Returns assistant message dict: content (optional), tool_calls (optional).
        """
        root = (base_url or "").strip().rstrip("/") or self.base_url
        url = f"{root}/api/chat"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": self._ollama_options(),
        }
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(url, json=payload, timeout=self._ollama_http_timeout())
            resp.raise_for_status()
        except RequestException as e:
            detail = str(e).strip() or type(e).__name__
            response = getattr(e, "response", None)
            if response is not None:
                try:
                    body = (response.text or "").strip().replace("\n", " ")[:480]
                    detail = f"HTTP {response.status_code}" + (f": {body}" if body else "")
                except Exception:
                    detail = f"HTTP {response.status_code}: {detail}"
            base = _ollama_request_user_message(detail, tools=True)
            raise ValueError(base + "\n\n若持续失败：请确认模型支持 tool calling，或关闭环境变量 AI_CHAT_TOOLS_ENABLE 后重试。") from e
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        text_plain = _ollama_api_chat_assistant_text(data)
        out: Dict[str, Any] = {
            "role": msg.get("role") or "assistant",
            "content": text_plain if text_plain else None,
        }
        if msg.get("tool_calls"):
            out["tool_calls"] = msg["tool_calls"]
        return out

    def _ollama_options(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {"temperature": 0.2}
        # 未设置时给足 num_predict：不少模型/Modelfile 默认很小，JSON 用例会在 steps 中途被截断导致解析失败。
        np_raw = (os.environ.get("LOCAL_LLM_NUM_PREDICT") or "").strip()
        if np_raw:
            try:
                n = int(np_raw, 10)
                if n != 0:
                    opts["num_predict"] = n
            except ValueError:
                pass
        else:
            opts["num_predict"] = 4096
        nt = (os.environ.get("LOCAL_LLM_NUM_THREAD") or "").strip()
        if nt.isdigit():
            opts["num_thread"] = int(nt)
        return opts

    def _fill_missing_step_payloads(
        self,
        steps: List[Dict[str, Any]],
        goal: str,
        case_url: str = "",
        probe_registry: Optional[List[Dict[str, Any]]] = None,
        mandatory_base_url: str = "",
    ) -> None:
        """补全模型漏填的 URL、等待时长、输入/校验文案、以及选择器（描述/探测/站点回退）。"""
        g = _norm_str(goal)
        cu = _norm_str(case_url) or _norm_str(mandatory_base_url)
        mbu = _norm_str(mandatory_base_url)
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = _norm_str(step.get("action")).lower()
            iv = _norm_str(step.get("input_value"))
            sv = _norm_str(step.get("selector_value"))
            desc = _norm_str(step.get("description"))

            if action == "navigate" and not iv:
                inferred = _infer_navigate_url(g, desc, cu, mbu)
                if inferred:
                    step["input_value"] = inferred
                    cu = cu or inferred
            elif action == "navigate" and iv and mbu and not _same_nav_host(iv, mbu):
                step["input_value"] = mbu
                cu = mbu

            elif action == "wait" and not iv:
                step["input_value"] = _infer_wait_input_value(desc)

            elif action == "input" and not iv:
                inferred = _infer_input_value_for_input_action(desc, g)
                if inferred:
                    step["input_value"] = inferred

            elif action == "verify" and not iv:
                inferred = _infer_verify_type_for_captcha_action(desc, g)
                if inferred:
                    step["input_value"] = inferred

            sv = _norm_str(step.get("selector_value"))

            if action in ("click", "extract_text", "verify", "input", "assert") and not sv:
                st2, sv2 = _extract_selector_from_description(desc)
                if not sv2 and probe_registry:
                    st2, sv2 = _probe_pick_selector(desc, probe_registry, action)
                if not sv2:
                    st2, sv2 = _fallback_site_selectors(cu, desc, action, g)
                if sv2:
                    step["selector_type"] = st2 or "css"
                    step["selector_value"] = sv2

        for step in steps:
            if isinstance(step, dict):
                _coerce_misused_verify_to_assert(step)

    def _maybe_compress_dom_pack(
        self,
        text: str,
        profile: Optional[Dict[str, Any]],
        model: str,
    ) -> str:
        if (os.environ.get("LOCAL_AI_DOM_PACK_COMPRESS", "0").strip().lower() not in ("1", "true", "yes", "on")):
            return text
        try:
            min_len = int((os.environ.get("LOCAL_AI_DOM_PACK_COMPRESS_MIN_CHARS") or "12000").strip() or "12000")
        except ValueError:
            min_len = 12000
        if len(text) < min_len:
            return text
        light = (os.environ.get("LOCAL_LLM_MODEL_LIGHT") or "qwen2:1.5b").strip() or "qwen2-1.5b"
        prompt = (
            "Condense the following DOM context to at most 40 short lines. "
            "Do NOT invent controls, URLs, or probe indices. Preserve any [n] or [Region …] labels that appear.\n\n"
        ) + text[:20000]
        try:
            using_model, content = self._complete_for_model(prompt, light, profile, meta_fallback=light)
            out = (content or "").strip()
            return out if out else text
        except ValueError as e:
            if (os.environ.get("LOCAL_AI_DEBUG", "").strip() == "1"):
                raise
            return text

    def _attach_locator_validation(
        self,
        meta: Dict[str, Any],
        probe_url: Optional[str],
        steps: List[Dict[str, Any]],
        probe_registry: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        u = _norm_str(probe_url)
        all_warns: List[str] = []
        if probe_registry and steps:
            snap_warns = registry_step_selector_warnings(probe_registry, steps)
            if snap_warns:
                all_warns.extend(snap_warns)
        probe_ok = (os.environ.get("LOCAL_AI_PROBE_VALIDATE", "1").strip().lower() not in ("0", "false", "no"))
        if probe_ok and u and steps:
            warnings, verr = validate_plan_locators(u, steps)
            if verr:
                meta["locator_validation_error"] = verr
            if warnings:
                all_warns.extend(warnings)
        if all_warns:
            meta["locator_validation"] = all_warns

    def _build_prompt(
        self,
        goal: str,
        project_name: str,
        page_snapshot: str = "",
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
        platform_type: str = "web",
        mandatory_base_url: str = "",
    ) -> str:
        platform = (platform_type or "web").strip().lower()
        if platform == "android":
            return self._build_android_prompt(goal, project_name, memory_context=memory_context)
        mem_block = ""
        if memory_context:
            mem_block = f"\nRetrieved similar context (may be from past runs; verify against the LIVE snapshot):\n{memory_context}\n\n"
        snap_block = ""
        if (page_snapshot or "").strip():
            snap_block = (
                "\nBelow is a LIVE snapshot of interactive elements from the target page "
                "(use ONLY selectors that appear here or can be derived from id/name/placeholder shown; "
                "do not invent class names):\n"
                f"{page_snapshot.strip()}\n\n"
            )
        dom_block = ""
        if (dom_context_pack or "").strip():
            dom_block = (
                "\nDOM structure hint (grouped by vertical region; optional a11y trim — use only to disambiguate, "
                "not as source of truth for selectors if it conflicts with the LIVE list above):\n"
                f"{dom_context_pack.strip()}\n\n"
            )
        mandatory_block = ""
        mbu = (mandatory_base_url or "").strip()
        if mbu:
            mandatory_block = (
                f"\nMANDATORY application base URL (user-provided): {mbu}\n"
                "All navigate steps MUST use this exact URL (or same-host path) in input_value. "
                "case_url MUST match. Do NOT invent other domains.\n"
            )
        return (
            "You are the reasoning brain; when a LIVE page snapshot is included below, the server already used "
            "Playwright headless to list real interactive elements—your locators MUST prefer those lines "
            "(id/css/placeholder/aria-label) and MUST NOT invent class names absent from the snapshot.\n"
            "When a LIVE snapshot exists: STRONGLY prefer setting probe_index to the line number [n] for each step, "
            "and set selector_value to the EXACT substring shown as recommended=(type)… on that SAME line "
            "(copy-paste; do not paraphrase or guess CSS classes).\n"
            "NEVER assume conventional English field names (e.g. name=password) if the snapshot line shows a different "
            "name= value (many Chinese admin UIs use name=pwd or vendor-specific names)—copy the name= from the snapshot row.\n"
            "Each snapshot line starts with [n] — that integer is ONLY for the JSON field probe_index. "
            "NEVER put the line number in selector_value (e.g. selector_value must NOT be \"1\" or \"12\" alone). "
            "Copy the real locator from that line into selector_type/selector_value (e.g. css #kw, [name=\\\"wd\\\"], xpath …). "
            "If you use probe_index=n, still prefer selectors shown on that same line; the server maps probe_index to stable locators.\n"
            "Generate one executable AI-assisted web test case with steps from this natural language goal.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"Goal: {goal}\n"
            f"{mandatory_block}"
            f"{mem_block}"
            f"{snap_block}"
            f"{dom_block}"
            "Output strict JSON with this schema:\n"
            "{\n"
            '  "case_name": "string",\n'
            '  "case_url": "string or empty",\n'
            '  "description": "string",\n'
            '  "precondition": "string",\n'
            '  "expected_result": "string",\n'
            '  "steps": [\n'
            "    {\n"
            '      "action": "navigate|click|input|wait|verify|assert|extract_text",\n'
            '      "selector_type": "css|xpath|text",\n'
            '      "selector_value": "string",\n'
            '      "input_value": "string",\n'
            '      "description": "string",\n'
            '      "compare_type": "string (only when action is assert; e.g. text_equals|text_contains|text_regex|element_visible|element_exists|url_contains|url_equals)",\n'
            '      "probe_index": "integer or empty string if not tied to a snapshot line"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Field rules:\n"
            "- navigate: put the full URL ONLY in input_value; selector_type and selector_value MUST be empty strings; probe_index empty.\n"
            "- wait: input_value MUST be a non-empty integer duration — SECONDS 1–120, OR milliseconds if value > 120 (e.g. 1500); never leave empty.\n"
            "- input: input_value MUST be the exact characters to type into the field (never empty). Put the typed text in input_value, not only in description (e.g. to search for X, input_value must be X).\n"
            "- verify: RESERVED for captcha / human-verification widgets only. input_value MUST be one of: "
            "auto, slider, image (captcha kinds), or visible/exist/clickable for element state — NOT natural-language expected text. "
            "Use auto for tianai-captcha (TAC) and mixed types (curve slider, rotate, click-text); runtime auto-detects.\n"
            "- assert: use ONLY when the goal explicitly needs a check. Prefer fewer steps; do NOT add a trailing "
            "assert \"just in case\" if the user did not ask for verification.\n"
            "  For checks against the browser address bar / current page URL / query string (e.g. wd=…, http…, percent-encoded), "
            "MUST use compare_type url_contains or url_equals, put the expected substring in input_value, and set "
            "selector_type and selector_value to empty strings (never bind a random <a> CSS for URL checks).\n"
            "  For element text checks, use compare_type text_contains|text_equals|… and a real selector from the snapshot; "
            "put the expected substring or pattern in input_value.\n"
            "- click: input_value usually empty; selector_value MUST be a real css/xpath/text from the snapshot (never a lone digit). "
            "Never use a bare tag-only selector like \"button\" or \"input\" — use id/css from the snapshot or probe_index.\n"
            "Use probe_index for [n], not selector_value.\n"
            "- navigate: input_value MUST be the full http(s) URL (never empty when a URL is known from the goal). For search goals (Baidu/Google/etc.), ALWAYS start with navigate to the HOME page (e.g. https://www.baidu.com/), then use input on common selectors (#kw, [name=wd], input[title*='搜索'], etc.) + click on search button (#su, [value*='百度一下'], button[type=submit], etc.). ONLY fall back to direct /s?wd=... URL param as LAST RESORT when input+click has failed twice in previous attempts.\n"
            "- input/assert/extract_text: selector_value must be concrete when probe_index is empty (assert url_* types may omit selector).\n"
            "- Usually 3–8 steps; start with navigate to base URL if known. Prefer realistic user flow (navigate → input → click → wait → assert) over clever URL shortcuts. Do not pad with redundant wait/assert steps.\n"
            "- Never omit JSON keys; use \"\" only where the rules above allow empty.\n"
            "- Never invent placeholder hosts like example.com / example.org unless the goal explicitly names them; "
            "use the real site implied by the goal (e.g. Baidu search → https://www.baidu.com/ ).\n"
            "OUTPUT FORMAT: respond with that single JSON object only—no markdown, no prose."
        )

    @staticmethod
    def _build_android_prompt(
        goal: str,
        project_name: str,
        memory_context: Optional[str] = None,
    ) -> str:
        mem_block = ""
        if memory_context:
            mem_block = f"\nRetrieved similar context (verify against the target Android app):\n{memory_context}\n\n"
        return (
            "You are generating an Android native UI test case for Appium / UiAutomator2.\n"
            "Each step MUST include:\n"
            '  "automation_layer": "android",\n'
            '  "strategy": "accessibility_id|id|xpath|class_name|android_uiautomator",\n'
            '  "selector_type": same as strategy,\n'
            '  "selector_value": "<locator>",\n'
            '  "description": "<Chinese step description>"\n\n'
            "Allowed actions: open_app, close_app, tap, input_text, swipe, wait, assert_text, assert_element, screenshot, tap_image, wait_image, assert_image.\n"
            "Use open_app as first step when launching an app (put appPackage in input_value or mobile_spec).\n"
            "Use tap not click; input_text not input.\n"
            "Prefer accessibility_id from content-desc; avoid brittle xpath.\n"
            "Do NOT use navigate, url, or CSS selectors.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"Goal: {goal}\n"
            f"{mem_block}"
            "Output strict JSON with this schema:\n"
            "{\n"
            '  "case_name": "string",\n'
            '  "case_url": "",\n'
            '  "description": "string",\n'
            '  "precondition": "string",\n'
            '  "expected_result": "string",\n'
            '  "steps": [\n'
            "    {\n"
            '      "action": "open_app|close_app|tap|input_text|swipe|wait|assert_text|assert_element|screenshot|tap_image|wait_image|assert_image",\n'
            '      "automation_layer": "android",\n'
            '      "strategy": "accessibility_id|id|xpath|class_name|android_uiautomator",\n'
            '      "selector_type": "accessibility_id|id|xpath|class_name|android_uiautomator",\n'
            '      "selector_value": "string",\n'
            '      "input_value": "string",\n'
            '      "description": "string",\n'
            '      "compare_type": "text_contains|text_equals (assert_text only)",\n'
            '      "mobile_spec": {"appPackage": "com.example.app", "appActivity": ".MainActivity"}\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Usually 3–8 steps. Never omit JSON keys; use \"\" where empty is allowed.\n"
            "OUTPUT FORMAT: respond with that single JSON object only—no markdown, no prose."
        )

    @staticmethod
    def _format_interaction_context(ctx: Optional[Dict[str, Any]]) -> str:
        """UI 传入的结构化情境（高亮步骤、划词、操作类型），与对话气泡/右键优化对齐。"""
        if not ctx or not isinstance(ctx, dict):
            return ""
        parts: List[str] = []
        raw_ix = ctx.get("focus_step_index")
        if raw_ix is not None and str(raw_ix).strip() != "":
            try:
                parts.append(
                    f"User focused on step index (1-based, same order as steps array): {int(float(str(raw_ix)))}"
                )
            except (TypeError, ValueError):
                pass
        multi = ctx.get("focus_step_indices")
        if isinstance(multi, (list, tuple)) and multi:
            try:
                norm = [int(float(str(x))) for x in multi[:24]]
                parts.append(f"Relevant step indices (1-based): {norm}")
            except (TypeError, ValueError):
                pass
        sel = (ctx.get("browser_selection_text") or ctx.get("selection_text") or "").strip()
        if sel:
            cap = 2400
            if len(sel) > cap:
                sel = sel[: cap - 1] + "…"
            parts.append(
                "User highlighted text in the page — treat as expected visible text for assertions or target copy: "
                f"{sel}"
            )
        kind = (ctx.get("action_kind") or ctx.get("intent") or "").strip()
        if kind:
            parts.append(
                f"UI intent hint: {kind}. If merge_steps: merge the indicated steps into one atomic step while "
                "keeping probe_index valid. If assert_from_selection: add an assert step (compare_type text_contains or text_equals) using "
                "the highlighted text as input_value, with a selector from the LIVE snapshot. "
                "If optimize_step: adjust only the focused step (retry, wait, selectors)."
            )
        rm = (ctx.get("response_mode") or "").strip().lower()
        if rm == "full":
            parts.append(
                "response_mode=full: return the COMPLETE updated steps array for the whole case "
                "(same length or adjusted count), not a delta fragment."
            )
        elif rm == "delta":
            parts.append(
                "response_mode=delta: return ONLY new steps to append at the end unless user asked to replace."
            )
        if not parts:
            return ""
        return "\nInteraction context (from editor / browser; obey when consistent with the LIVE snapshot):\n" + "\n".join(
            f"- {p}" for p in parts
        ) + "\n\n"

    @staticmethod
    def _sanitize_chat_history_for_prompt(history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
        """Drop UI-only keys (warningsList, etc.), cap size — keeps refine requests small and on-topic."""
        out: List[Dict[str, str]] = []
        if not history:
            return out
        for item in history:
            if not isinstance(item, dict):
                continue
            role = _norm_str(item.get("role")).lower() or "user"
            if role not in ("user", "assistant", "system"):
                role = "user"
            content = _norm_str(item.get("content"))
            if not content:
                continue
            if len(content) > 12000:
                content = content[:11999] + "…"
            out.append({"role": role, "content": content})
        return out[-12:]

    def _build_refine_prompt(
        self,
        user_message: str,
        project_name: str,
        current_plan: Dict[str, Any],
        history: Optional[List[Dict[str, Any]]],
        page_snapshot: str = "",
        memory_context: Optional[str] = None,
        dom_context_pack: Optional[str] = None,
        interaction_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        hist_clean = self._sanitize_chat_history_for_prompt(history)[-6:]
        history_text = "\n".join(
            [f"- {item.get('role', 'user')}: {item.get('content', '')}" for item in hist_clean]
        )
        plan_text = json.dumps(current_plan or {}, ensure_ascii=False)
        iact = self._format_interaction_context(interaction_context)
        mem_block = ""
        if memory_context:
            mem_block = f"\nRetrieved similar context (past cases; verify against LIVE snapshot):\n{memory_context}\n\n"
        snap_block = ""
        if (page_snapshot or "").strip():
            snap_block = (
                "\nLIVE page element snapshot (prefer these locators; do not invent):\n"
                f"{page_snapshot.strip()}\n\n"
            )
        dom_block = ""
        if (dom_context_pack or "").strip():
            dom_block = "\nDOM structure hint (grouped):\n" + dom_context_pack.strip() + "\n\n"
        return (
            "You refine plans using the same rules: if a LIVE snapshot is present, selectors must align with "
            "those real elements only. Prefer probe_index=[n] plus the exact recommended=() string from that line; "
            "never invent selectors not shown in the snapshot.\n"
            "You are refining an existing AI-assisted web test case plan.\n"
            f"Project: {project_name or 'unknown'}\n"
            f"{iact}"
            f"User latest instruction: {user_message}\n"
            f"Chat history:\n{history_text or '- none'}\n"
            f"Current plan JSON:\n{plan_text}\n"
            f"{mem_block}"
            f"{snap_block}"
            f"{dom_block}"
            "Return strict JSON only, with full schema fields and a full updated steps list:\n"
            "{\n"
            '  "case_name": "string",\n'
            '  "case_url": "string or empty",\n'
            '  "description": "string",\n'
            '  "precondition": "string",\n'
            '  "expected_result": "string",\n'
            '  "steps": [\n'
            "    {\n"
            '      "action": "navigate|click|input|wait|verify|assert|extract_text",\n'
            '      "selector_type": "css|xpath|text",\n'
            '      "selector_value": "string",\n'
            '      "input_value": "string",\n'
            '      "description": "string",\n'
            '      "compare_type": "string (only for assert; e.g. text_equals|text_contains|text_regex|element_visible|element_exists|url_contains|url_equals)",\n'
            '      "probe_index": "integer or empty"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "navigate step: URL only in input_value; empty selector fields. "
            "wait: seconds 1-120 OR milliseconds if >120. "
            "input: input_value must contain the exact text to type (never empty). "
            "click: selector_value must be a real locator from the snapshot, never a lone digit; use probe_index for line [n]. "
            "Optional per-step \"locator_candidates\": JSON array of {selector_type, selector_value, score}. "
            "Execution order is DOM strategies first, then selector_type \"visual_template\" (selector_value: base64 PNG or JSON with png_b64), "
            "then \"viewport_coord\" (selector_value: JSON {\"fx\":0..1,\"fy\":0..1} for click center in viewport). "
            "Use viewport_coord when snapshot provides stable geometry but CSS is volatile; avoid huge base64. "
            "verify: captcha/human-check only — input_value one of auto|slider|image|visible|exist|clickable, never a sentence. "
            "Prefer auto for tianai-captcha (TAC): curve slider, rotate, click-text are auto-detected at runtime. "
            "assert: optional — only if the user asks for verification. For URL/address-bar/query checks use compare_type "
            "url_contains|url_equals with input_value as substring and EMPTY selector fields; for element text use text_* "
            "with a real snapshot selector. Never use bare tag-only CSS like \"button\" for named controls. "
            "Do not return markdown. OUTPUT FORMAT: one JSON object only—no fences, no prose."
        )

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        raw0 = (content or "").strip()
        raw = _strip_llm_noise(raw0)
        # 部分模型把整段 JSON 放在 <think>…</think> 内，去标签后为空，需回退到原文再抽 JSON
        if not raw:
            raw = raw0
        if not raw:
            raise ValueError("本地模型返回为空")

        bases = _collect_json_try_strings(raw)
        variants: List[str] = []
        seen = set()
        for b in bases:
            for v in (
                b,
                _normalize_smart_quotes_for_json(b),
                _repair_json_trailing_commas(b),
                _repair_json_trailing_commas(_normalize_smart_quotes_for_json(b)),
            ):
                t = (v or "").strip()
                if t and t not in seen:
                    seen.add(t)
                    variants.append(t)

        last_err: Optional[Exception] = None
        for cand in variants:
            try:
                val = json.loads(cand)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                last_err = e
                continue
            root = _coerce_plan_root(val)
            if isinstance(root, dict):
                return root
            last_err = ValueError("JSON 已解析但不是预期的用例对象或步骤列表")

        preview = raw.replace("\r", " ").replace("\n", " ").strip()[:220]
        msg = (
            "无法将模型应答解析为用例 JSON（已尝试：markdown 代码块、截取首个对象、修复尾随逗号、弯引号等）。"
            "若摘要在引号或数组处突然结束，多为生成长度被截断：在 .env 设置 LOCAL_LLM_NUM_PREDICT=8192（或 -1 不限制），"
            "并检查 Ollama 模型 Modelfile 的 num_predict。"
            "其他建议：① 重启本 Web 服务（避免加载旧的 .pyc）；② 升级 Ollama；③ LOCAL_LLM_JSON_FORMAT=0 关闭 JSON 约束；"
            "④ LOCAL_LLM_JSON_RETRY_PLAIN=0 可禁用「无 format 重试」；⑤ 换用 qwen2.5、llama3.1 等指令模型。"
            f" 应答摘要：{preview!r}"
        )
        if last_err is not None:
            raise ValueError(msg) from last_err
        raise ValueError(msg)

    def _normalize_output(
        self,
        data: Dict[str, Any],
        goal: str,
        project_name: str,
        using_model: str,
        probe_registry: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        goal_s = _norm_str(goal)
        case_name = _norm_str(data.get("case_name")) or f"AI生成用例-{goal_s[:30]}"
        case_url = _norm_str(data.get("case_url") or data.get("caseUrl"))
        seed_url = _goal_suggests_seed_url(goal_s)
        if seed_url and (not case_url or _placeholder_template_case_url(case_url)):
            case_url = seed_url
        description = _norm_str(data.get("description"))
        precondition = _norm_str(data.get("precondition"))
        expected_result = _norm_str(data.get("expected_result"))
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            raw_steps = []

        step_repair_warnings = repair_raw_ai_steps_for_platform(raw_steps)

        probe_by_index: Dict[int, Dict[str, Any]] = {}
        if probe_registry:
            for ent in probe_registry:
                if not isinstance(ent, dict):
                    continue
                try:
                    ii = int(ent.get("i"))
                except (TypeError, ValueError):
                    continue
                probe_by_index[ii] = ent

        steps: List[Dict[str, Any]] = []
        allowed_actions = {"navigate", "click", "input", "wait", "verify", "extract_text", "assert"}
        allowed_selector_types = {"css", "xpath", "text", ""}

        for i, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                continue
            action = _norm_str(step.get("action")).lower()
            if action not in allowed_actions:
                action = "click"
            selector_type = _norm_str(step.get("selector_type")).lower()
            if selector_type not in allowed_selector_types:
                selector_type = "css"
            selector_value = _norm_str(step.get("selector_value"))
            input_value = _norm_str(step.get("input_value"))
            description_step = _norm_str(step.get("description")) or f"步骤{i}: {action}"

            pi_raw = step.get("probe_index")
            pi: Optional[int] = None
            if pi_raw is not None and _norm_str(pi_raw) != "":
                try:
                    pi = int(float(str(pi_raw).strip()))
                except (TypeError, ValueError):
                    pi = None
            if (
                pi is None
                and action in ("click", "input", "verify", "extract_text", "assert")
                and selector_value.isdigit()
            ):
                try:
                    cand_pi = int(selector_value)
                    if probe_by_index and cand_pi in probe_by_index:
                        pi = cand_pi
                    selector_value = ""
                except ValueError:
                    selector_value = ""
            if pi is not None and pi in probe_by_index:
                ct_for_probe = _norm_str(step.get("compare_type")).lower()
                if not (action == "assert" and ct_for_probe in ("url_equals", "url_contains")):
                    ent = probe_by_index[pi]
                    rec = _norm_str(ent.get("recommended_selector"))
                    rty = _norm_str(ent.get("recommended_selector_type")).lower()
                    if rec:
                        selector_value = rec
                        if rty in ("css", "xpath", "text"):
                            selector_type = rty

            if action == "navigate":
                url_guess = _norm_str(input_value or selector_value or case_url)
                if url_guess.startswith("//"):
                    url_guess = "https:" + url_guess
                if _placeholder_template_case_url(url_guess):
                    if case_url and not _placeholder_template_case_url(case_url):
                        url_guess = case_url
                    elif seed_url:
                        url_guess = seed_url
                steps.append(
                    {
                        "action": "navigate",
                        "selector_type": "",
                        "selector_value": "",
                        "input_value": url_guess,
                        "description": description_step,
                    }
                )
                if url_guess and not case_url:
                    case_url = url_guess
                continue

            row_step: Dict[str, Any] = {
                "action": action,
                "selector_type": selector_type,
                "selector_value": selector_value,
                "input_value": input_value,
                "description": description_step,
            }
            if action == "assert":
                ct_a = _norm_str(step.get("compare_type"))
                if ct_a:
                    row_step["compare_type"] = ct_a
            if pi is not None:
                row_step["probe_index"] = str(pi)
                ct_row = _norm_str(row_step.get("compare_type")).lower()
                if pi in probe_by_index and not (action == "assert" and ct_row in ("url_equals", "url_contains")):
                    lc = build_locator_candidates_from_probe_entry(probe_by_index[pi])
                    if lc:
                        row_step["locator_candidates"] = lc
            steps.append(row_step)

        if (
            seed_url
            and steps
            and all(str(s.get("action") or "").lower() != "navigate" for s in steps)
        ):
            steps.insert(
                0,
                {
                    "action": "navigate",
                    "selector_type": "",
                    "selector_value": "",
                    "input_value": seed_url,
                    "description": "打开目标站点",
                },
            )

        if case_url and steps and steps[0].get("action") == "navigate":
            if not _norm_str(steps[0].get("input_value")):
                steps[0]["input_value"] = case_url

        if len(steps) < 1:
            steps = [
                {
                    "action": "navigate",
                    "selector_type": "",
                    "selector_value": "",
                    "input_value": case_url,
                    "description": "导航到目标页面",
                },
                {
                    "action": "wait",
                    "selector_type": "",
                    "selector_value": "",
                    "input_value": "1500",
                    "description": "等待页面加载稳定",
                },
            ]

        self._fill_missing_step_payloads(steps, goal_s, case_url, probe_registry)
        clamp_warnings = clamp_plan_steps_to_probe_registry(steps, probe_registry)

        if steps and str(steps[0].get("action") or "").lower() == "navigate":
            u0 = _norm_str(steps[0].get("input_value"))
            if u0 and not case_url:
                case_url = u0

        meta_out: Dict[str, Any] = {
            "provider": "local",
            "model": using_model,
            "project_name": project_name or "",
        }
        if step_repair_warnings:
            meta_out["step_repair_warnings"] = step_repair_warnings
        if clamp_warnings:
            meta_out["selector_clamp_warnings"] = clamp_warnings

        return {
            "case_name": case_name,
            "case_url": case_url,
            "description": description or f"AI根据自然语言目标自动生成：{goal_s}",
            "precondition": precondition,
            "expected_result": expected_result,
            "steps": steps,
            "meta": meta_out,
        }


local_ai_service = LocalAIService()
