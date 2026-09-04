"""
AI 用例计划步骤的统一规范化入口：所有来自模型的 steps 在持久化或返回给前端前经同一去重/校验。
与 app 中写入 DB 的逻辑对齐（action/selector 字段）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# 单独标签名、无 # . [ 组合符时极易误点多个元素，平台不鼓励作为最终定位
_OVERLY_BROAD_SINGLE_TAGS = frozenset(
    {
        "button",
        "input",
        "a",
        "div",
        "span",
        "form",
        "select",
        "label",
        "img",
        "li",
        "ul",
        "ol",
        "td",
        "tr",
        "table",
        "p",
        "section",
        "article",
        "html",
        "body",
        "h1",
        "h2",
        "h3",
        "h4",
    }
)


def is_overly_broad_css_selector(selector_value: str) -> bool:
    """是否为「仅标签名」等过宽 CSS（易与平台稳定定位要求不符）。"""
    s = _str(selector_value)
    if not s:
        return False
    if is_weak_generic_css_selector(s):
        return True
    low = s.lower()
    if low.startswith(("xpath:", "//", "xpath=", "text=", "role=")):
        return False
    if any(c in s for c in " \t\n>+~"):
        return False
    if "#" in s or "[" in s:
        return False
    if "." in s:
        return False
    return low in _OVERLY_BROAD_SINGLE_TAGS


_WEAK_GENERIC_CSS_RE = re.compile(
    r"^(?:input|button|textarea|select)"
    r"\[type\s*=\s*['\"]?(?:text|password|submit|button)['\"]?\]$",
    re.I,
)


def is_weak_generic_css_selector(selector_value: str) -> bool:
    """AI 常臆造的泛化属性选择器（无 id/name/class 区分度）。"""
    s = _str(selector_value)
    if not s:
        return False
    if _WEAK_GENERIC_CSS_RE.match(s):
        return True
    low = s.lower()
    if low in (
        "input[type='text']",
        'input[type="text"]',
        "input[type='password']",
        'input[type="password"]',
        "button[type='submit']",
        'button[type="submit"]',
    ):
        return True
    return False


def _looks_like_url_bar_expected_text(iv: str) -> bool:
    v = _str(iv)
    if not v:
        return False
    if v.startswith(("http://", "https://")):
        return True
    if re.search(r"[%][0-9A-Fa-f]{2}", v) and ("=" in v or "&" in v):
        return True
    if re.match(r"^[a-z_][a-z0-9_]*=", v, re.I):
        return True
    return False


def _description_suggests_url_assertion(desc: str) -> bool:
    d = desc or ""
    if not d.strip():
        return False
    dl = d.lower()
    if "url" in dl or "网址" in d or "地址栏" in d or "页面地址" in d:
        if any(k in d for k in ("验证", "检查", "断言", "确认")) or any(
            k in dl for k in ("verify", "assert", "check")
        ):
            return True
        if any(k in d for k in ("包含", "等于", "一致")):
            return True
        if "encoded" in dl or "query" in dl or "address" in dl or "href" in dl:
            return True
    return False


def repair_single_assert_step_inplace(step: dict) -> List[str]:
    """单条 assert 步骤就地修复（执行前/落库前均可调用）。返回告警文案。"""
    warns: List[str] = []
    if not isinstance(step, dict):
        return warns
    if _str(step.get("action")).lower() != "assert":
        return warns
    raw_ct = _str(step.get("compare_type")).lower() or "text_contains"
    if raw_ct == "equals":
        ct = "text_equals"
    elif raw_ct == "contains":
        ct = "text_contains"
    else:
        ct = raw_ct
    step["compare_type"] = ct
    iv = _str(step.get("input_value"))
    desc = _str(step.get("description"))
    sv = _str(step.get("selector_value"))
    st = _str(step.get("selector_type")).lower() or "css"

    try:
        from modules.ai.ai_page_probe import repair_message_toast_assert_step_inplace

        toast_msg = repair_message_toast_assert_step_inplace(step)
        if toast_msg:
            warns.append(toast_msg)
            return warns
    except Exception:
        pass

    url_cts = ("url_equals", "url_contains")
    if ct in url_cts:
        if sv or _str(step.get("selector_type")):
            step["selector_value"] = ""
            step["selector_type"] = ""
            step.pop("locator_candidates", None)
            warns.append("URL 断言已清空多余 selector，以符合平台格式")
        return warns

    text_like = ("text_equals", "text_contains", "text_regex", "")
    if ct not in text_like:
        return warns
    if _description_suggests_url_assertion(desc) or _looks_like_url_bar_expected_text(iv):
        step["compare_type"] = "url_contains"
        step["selector_value"] = ""
        step["selector_type"] = ""
        step.pop("locator_candidates", None)
        try:
            from urllib.parse import unquote

            m = re.match(r"^(wd|word|query|q)=([^&]+)\s*$", iv, re.I)
            if m:
                dec = unquote(m.group(2)).strip()
                if dec and "%" not in dec:
                    step["input_value"] = dec
        except Exception:
            pass
        warns.append("已改为 URL 断言(url_contains)，selector 已清空")
        return warns

    page_content_assert = any(
        k in desc for k in ("包含", "标题", "页面", "出现", "显示", "展示", "可见")
    )
    if st == "text" and sv:
        expect_text = iv or sv
        if page_content_assert or (not iv and sv):
            if ct == "text_equals":
                step["compare_type"] = "page_text_equals"
            elif ct == "text_regex":
                step["compare_type"] = "page_text_regex"
            else:
                step["compare_type"] = "page_text_contains"
            step["input_value"] = expect_text
            step["selector_type"] = ""
            step["selector_value"] = ""
            step.pop("locator_candidates", None)
            step.pop("probe_index", None)
            warns.append(
                f"text 定位断言已改为整页可见文本断言({step['compare_type']})，预期 {expect_text!r}"
            )
            return warns

    if not sv and iv:
        if ct == "text_equals":
            step["compare_type"] = "page_text_equals"
        elif ct == "text_regex":
            step["compare_type"] = "page_text_regex"
        else:
            step["compare_type"] = "page_text_contains"
        warns.append(
            f"无 selector，已改为整页可见文本断言({step['compare_type']})"
        )
    return warns


def repair_raw_ai_steps_for_platform(steps: Any) -> List[str]:
    """
    在归一化/探测 clamp 之前就地修正常见「模型格式」问题：
    - 校验地址栏 / URL / 查询串却使用 text_equals + CSS → 改为 url_contains 并清空 selector；
    - url_* 断言若仍带 selector → 清空以符合本平台执行路径；
    - assert 仅有预期字符串、无 selector 且非 URL 语义 → 改为 page_text_* 整页可见文本断言。
    返回人类可读告警列表（可并入 API warnings）。
    """
    warns: List[str] = []
    if not isinstance(steps, list):
        return warns
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step_warns = repair_single_assert_step_inplace(step)
        for w in step_warns:
            if w.startswith("无 selector"):
                warns.append(
                    f"第{idx}步{w}；仅检查主文档 body 文本，跨 iframe 内容可能取不到。"
                )
            else:
                warns.append(f"第{idx}步{w}")
    return warns


def normalize_ai_step(step: dict) -> dict:
    layer = _str(step.get("automation_layer")).lower() or "web"
    if layer not in ("web", "desktop", "android", "cross_end"):
        layer = "web"
    # 跨端层：extract_otp / api_call 直接放行
    cross_end_actions = {"extract_otp", "api_call"}
    # 桌面步骤类型：覆盖所有 windows_* 工具
    desktop_actions = {
        "launch_app", "attach_window", "click", "input", "wait", "verify",
        "extract_text", "assert", "hotkey", "screenshot", "double_click", "right_click",
        "focus_app", "press_key", "scroll", "get_screen_text",
    }
    desktop_actions |= cross_end_actions  # 桌面用例可含跨端步骤
    # 移动端步骤类型：覆盖所有 mobile_* 工具 + scrcpy_* 视觉工具
    android_actions = {
        "open_app", "close_app", "tap", "input_text", "swipe", "wait",
        "assert_text", "assert_element", "screenshot", "click", "input", "verify", "assert",
        "ai_tap", "ai_input", "assert_vision", "wait_vision", "extract_vision",
        "scroll", "extract_otp", "extract_text", "press_key", "back", "home",
    }
    # scrcpy 视觉/控制步骤类型
    scrcpy_actions = {
        "capture_frame", "ocr_device", "navigate_to_messages",
        "scrcpy_tap", "scrcpy_swipe", "scrcpy_type_text",
    }
    android_actions |= scrcpy_actions
    android_actions |= cross_end_actions  # 移动端用例可含跨端步骤
    # Web 浏览器步骤类型：覆盖所有 browser_* 工具
    allowed_actions = {
        "navigate", "click", "input", "wait", "verify", "extract_text", "assert",
        "ai_tap", "ai_input", "ai_scroll",
        "assert_vision", "wait_vision", "extract_vision",
        "snapshot", "scroll", "press_key", "type", "fill", "hover",
        "double_click", "right_click", "select",
    }
    allowed_actions |= cross_end_actions  # web 用例也可含 api_call
    if layer == "desktop":
        allowed_actions = desktop_actions
    elif layer == "android":
        allowed_actions = android_actions
    elif layer == "cross_end":
        allowed_actions = cross_end_actions | {"input", "wait", "tap", "click"}
    action = _str(step.get("action")).lower()
    # 全端别名：保证执行器/编辑器识别
    web_alias = {"type": "input", "fill": "input", "goto": "navigate", "click_element": "click"}
    if layer == "android":
        alias = {"click": "tap", "input": "input_text", "fill": "input_text", "type": "input_text",
                 "verify": "assert_element", "assert": "assert_text", "click_element": "tap"}
        action = alias.get(action, action)
    else:
        action = web_alias.get(action, action)
    if action not in allowed_actions:
        # 未知动作：保留原值并告警式降级（避免把 hotkey 静默改成 launch_app）
        if layer == "desktop":
            if action in ("click_element",):
                action = "click"
            elif action in ("type_text", "type", "fill"):
                action = "input"
            elif action in ("press_key",):
                action = "hotkey"
            else:
                # 不静默改写为 launch_app（会导致用例步骤类型全错）
                action = action or "click"
                if action not in allowed_actions:
                    action = "click"
        elif layer == "android":
            action = "open_app" if not _str(step.get("selector_value")) else "tap"
        else:
            action = "click" if action not in ("navigate", "input", "wait") else action
    strategy = _str(step.get("strategy")) or _str(step.get("selector_type")) or ""
    if layer == "android":
        selector_type = strategy or "accessibility_id"
    else:
        selector_type = _str(step.get("selector_type")).lower()
    selector_value = _str(step.get("selector_value"))
    input_value = _str(step.get("input_value"))
    description = _str(step.get("description"))
    locate_prompt = _str(step.get("locate_prompt"))
    # 拒绝工具名占位（browser_type / navigate 等）污染选择器与输入值
    try:
        from modules.ai.ai_action_recorder import is_tool_name_placeholder as _is_tool_ph
    except Exception:
        def _is_tool_ph(v: str) -> bool:  # type: ignore
            return False
    if _is_tool_ph(selector_value):
        selector_value = ""
    if _is_tool_ph(input_value) and action != "hotkey":
        input_value = ""
    if _is_tool_ph(description):
        description = ""
    if _is_tool_ph(locate_prompt):
        locate_prompt = ""
    # 从 target/locator 回填（录制器中间字段，落库前必须提升）
    if not selector_value:
        for alt_key in ("locator", "target"):
            alt = _str(step.get(alt_key))
            if not alt or _is_tool_ph(alt):
                continue
            # 跳过 ephemeral hermes ref
            if alt.startswith("@") and len(alt) <= 6:
                if not locate_prompt:
                    locate_prompt = description or alt
                continue
            if action == "navigate" and (alt.startswith("http://") or alt.startswith("https://")):
                if not input_value:
                    input_value = alt
                continue
            selector_value = alt
            if not selector_type:
                if alt.startswith(("/", "(")) or alt.startswith("xpath="):
                    selector_type = "xpath"
                elif alt.startswith(("#", ".", "[")) or (layer == "web" and ("#" in alt or "." in alt)):
                    selector_type = "css"
                elif layer == "desktop":
                    selector_type = "name"
                elif layer == "android":
                    selector_type = "text"
                else:
                    selector_type = "text"
            break
    if action == "navigate" and not input_value:
        # URL 也可能误放在 selector_value
        maybe_url = selector_value or _str(step.get("url")) or _str(step.get("target"))
        if maybe_url.startswith("http://") or maybe_url.startswith("https://") or maybe_url.startswith("/"):
            input_value = maybe_url
            selector_value = ""
            selector_type = ""
        elif _is_tool_ph(maybe_url):
            selector_value = ""
            selector_type = ""
    # extract_otp：层固定 android，避免误标成 Web
    if action == "extract_otp" and layer in ("web", "cross_end", ""):
        layer = "android"
        if not description:
            description = "提取手机短信验证码"
    if not locate_prompt and description and action in ("click", "input", "tap", "input_text"):
        locate_prompt = description
    compare_type = _str(step.get("compare_type"))
    if action == "assert":
        from modules.auth.auth_batch_helpers import normalize_assert_compare_type

        compare_type = normalize_assert_compare_type(
            compare_type,
            selector_value=selector_value,
            input_value=input_value,
        )
    lc = step.get("locator_candidates")
    if lc is not None and not isinstance(lc, str):
        try:
            lc = json.dumps(lc, ensure_ascii=False)
        except Exception:
            lc = ""
    elif lc is None:
        lc = ""
    else:
        lc = _str(lc)
    out: Dict[str, Any] = {
        "action": action,
        "selector_type": selector_type,
        "selector_value": selector_value,
        "input_value": input_value,
        "description": description,
        "automation_layer": layer,
    }
    ds = step.get("desktop_spec")
    if ds is not None:
        if isinstance(ds, str):
            out["desktop_spec"] = ds
        else:
            try:
                out["desktop_spec"] = json.dumps(ds, ensure_ascii=False)
            except Exception:
                out["desktop_spec"] = ""
    if compare_type and action == "assert":
        out["compare_type"] = compare_type
    if lc:
        out["locator_candidates"] = lc
    if locate_prompt:
        out["locate_prompt"] = locate_prompt
        if not description:
            out["description"] = locate_prompt
    if layer == "android":
        out["strategy"] = selector_type or "accessibility_id"
    ms = step.get("mobile_spec")
    if ms is not None and layer == "android":
        if isinstance(ms, str):
            out["mobile_spec"] = ms
        else:
            try:
                out["mobile_spec"] = json.dumps(ms, ensure_ascii=False)
            except Exception:
                out["mobile_spec"] = ""
    # 跨端步骤：保留 cross_end_spec
    ces = step.get("cross_end_spec")
    if ces is not None:
        if isinstance(ces, str):
            out["cross_end_spec"] = ces
        else:
            try:
                out["cross_end_spec"] = json.dumps(ces, ensure_ascii=False)
            except Exception:
                out["cross_end_spec"] = ""
    # 阶段1/3 补录字段：UIA 锚点 / 树级校验快照 / 录制 serial —— 白名单透传，防 normalize 裁剪
    for _extra_key, _extra_src in (
        ("uia_anchor", step.get("uia_anchor")),
        ("verification", step.get("verification")),
        ("stage_info", step.get("stage_info")),
    ):
        if _extra_src is not None:
            if isinstance(_extra_src, str):
                out[_extra_key] = _extra_src
            else:
                try:
                    out[_extra_key] = json.dumps(_extra_src, ensure_ascii=False)
                except Exception:
                    pass
    if step.get("device_id"):
        out["device_id"] = _str(step.get("device_id"))[:80]
    return out


def infer_plan_platform_type(plan: Optional[Dict[str, Any]] = None, steps: Any = None) -> str:
    """从 plan.meta / plan.platform / 步骤 automation_layer 推断平台。"""
    if isinstance(plan, dict):
        meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
        for key in ("platform_type", "platform"):
            pt = _str(plan.get(key) if key == "platform" else meta.get(key)).lower()
            if pt in ("web", "desktop", "android", "cross_end"):
                return pt
    rows = steps if isinstance(steps, list) else (plan.get("steps") if isinstance(plan, dict) else [])
    if not isinstance(rows, list):
        return "web"
    layers = {
        _str(s.get("automation_layer")).lower()
        for s in rows
        if isinstance(s, dict) and _str(s.get("automation_layer"))
    }
    if "cross_end" in layers:
        return "cross_end"
    if "desktop" in layers:
        return "desktop"
    if "android" in layers:
        return "android"
    actions = {_str(s.get("action")).lower() for s in rows if isinstance(s, dict)}
    if actions & {"extract_otp", "api_call"}:
        return "cross_end"
    if actions & {"launch_app", "attach_window", "hotkey"}:
        return "desktop"
    if actions & {"open_app", "tap", "input_text"}:
        return "android"
    return "web"


_DESKTOP_SHELL_LAUNCH = frozenset({
    "control", "control.exe", "notepad", "notepad.exe", "calc", "calc.exe",
    "mspaint", "mspaint.exe", "explorer", "explorer.exe", "cmd", "cmd.exe",
})


def _guess_desktop_app_from_text(text: str) -> str:
    t = _str(text)
    if not t:
        return ""
    low = t.lower()
    if "控制面板" in t or "control panel" in low:
        return "control"
    if "记事本" in t or "notepad" in low:
        return "notepad"
    if "计算器" in t or "calculator" in low or "calc.exe" in low:
        return "calc"
    if "资源管理器" in t or "文件管理器" in t or "explorer" in low:
        return "explorer"
    return ""


def desktop_template_steps_for_goal(goal: str) -> List[dict]:
    """模型输出不可执行时的桌面步骤兜底（常见系统应用）。"""
    app = _guess_desktop_app_from_text(goal)
    if app == "control":
        title = "控制面板"
        return [
            {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": "control",
                "desktop_spec": json.dumps({"app": "control"}, ensure_ascii=False),
                "selector_type": "",
                "selector_value": "",
                "description": "启动控制面板",
            },
            {
                "action": "wait",
                "automation_layer": "desktop",
                "input_value": "3",
                "selector_type": "",
                "selector_value": "",
                "description": "等待控制面板窗口出现",
            },
            {
                "action": "attach_window",
                "automation_layer": "desktop",
                "desktop_spec": json.dumps({"title_contains": title}, ensure_ascii=False),
                "selector_type": "",
                "selector_value": "",
                "description": f"附着到「{title}」窗口",
            },
            {
                "action": "verify",
                "automation_layer": "desktop",
                "selector_type": "window",
                "selector_value": title,
                "input_value": "exist",
                "description": "确认控制面板窗口已显示",
            },
        ]
    if app:
        return [
            {
                "action": "launch_app",
                "automation_layer": "desktop",
                "input_value": app,
                "selector_type": "",
                "selector_value": "",
                "description": f"启动 {app}",
            },
            {
                "action": "wait",
                "automation_layer": "desktop",
                "input_value": "2",
                "selector_type": "",
                "selector_value": "",
                "description": "等待应用窗口",
            },
        ]
    return []


def repair_desktop_ai_steps_inplace(steps: Any) -> List[str]:
    """将模型误生成的 Web 风格步骤纠正为桌面可执行格式。"""
    warns: List[str] = []
    if not isinstance(steps, list):
        return warns
    last_launch_app = ""
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        step["automation_layer"] = "desktop"
        action = _str(step.get("action")).lower()
        iv = _str(step.get("input_value"))
        sv = _str(step.get("selector_value"))
        st = _str(step.get("selector_type")).lower()
        desc = _str(step.get("description"))

        iv_low = iv.lower()
        if action in ("click", "input", "navigate", "extract_text") and (
            iv_low in _DESKTOP_SHELL_LAUNCH or _guess_desktop_app_from_text(iv or desc)
        ):
            app = iv_low if iv_low in _DESKTOP_SHELL_LAUNCH else _guess_desktop_app_from_text(iv or desc)
            step["action"] = "launch_app"
            step["input_value"] = app
            step["selector_type"] = ""
            step["selector_value"] = ""
            warns.append(f"第{idx}步已从 {action} 纠正为 launch_app（启动 {app}）")
            action = "launch_app"
        elif action == "navigate":
            step["action"] = "launch_app"
            step["selector_type"] = ""
            step["selector_value"] = ""
            warns.append(f"第{idx}步：桌面场景不使用 navigate，已改为 launch_app")
            action = "launch_app"

        if action in ("verify", "assert") and st in ("css", "xpath", "text", ""):
            title_hint = sv or _guess_window_title_from_desc(desc) or iv
            if title_hint and not any(c in title_hint for c in "#.[/") and len(title_hint) < 120:
                step["selector_type"] = "window"
                step["input_value"] = step.get("input_value") or "exist"
                spec_obj: Dict[str, Any] = {}
                raw_ds = step.get("desktop_spec")
                if isinstance(raw_ds, str) and raw_ds.strip():
                    try:
                        spec_obj = json.loads(raw_ds)
                    except Exception:
                        spec_obj = {}
                elif isinstance(raw_ds, dict):
                    spec_obj = dict(raw_ds)
                if not spec_obj.get("title_contains") and not spec_obj.get("title"):
                    spec_obj["title_contains"] = title_hint
                    step["desktop_spec"] = json.dumps(spec_obj, ensure_ascii=False)
                warns.append(f"第{idx}步：窗口校验已改为 window + desktop_spec.title_contains")

        if action == "attach_window":
            spec_obj = {}
            raw_ds = step.get("desktop_spec")
            if isinstance(raw_ds, str) and raw_ds.strip():
                try:
                    spec_obj = json.loads(raw_ds)
                except Exception:
                    spec_obj = {}
            elif isinstance(raw_ds, dict):
                spec_obj = dict(raw_ds)
            if not spec_obj.get("title_contains") and not spec_obj.get("title"):
                from modules.desktop.desktop_run_context import window_hints_for_launch

                title_hint = (
                    sv
                    or _guess_window_title_from_desc(desc)
                    or (window_hints_for_launch(last_launch_app)[0] if last_launch_app else "")
                )
                if title_hint:
                    spec_obj["title_contains"] = title_hint
                    step["desktop_spec"] = json.dumps(spec_obj, ensure_ascii=False)
                    if not sv:
                        step["selector_value"] = title_hint
                    warns.append(f"第{idx}步：已补充 attach_window 的 title_contains")

        if action == "launch_app":
            last_launch_app = _str(step.get("input_value")) or _guess_desktop_app_from_text(desc)

        if action == "launch_app" and not _str(step.get("input_value")):
            app = _guess_desktop_app_from_text(desc)
            if app:
                step["input_value"] = app
                warns.append(f"第{idx}步：已根据描述补全 launch_app → {app!r}")

    return warns


def _guess_window_title_from_desc(desc: str) -> str:
    d = _str(desc)
    if "控制面板" in d:
        return "控制面板"
    if "记事本" in d:
        return "记事本"
    if "计算器" in d:
        return "计算器"
    return ""


def dedupe_and_validate_ai_steps(steps: list, *, platform: str = "web") -> Tuple[List[dict], List[str]]:
    """
    去重 + 非阻断校验提示。
    Returns: (clean_steps, warnings)
    """
    warnings: List[str] = []
    clean_steps: List[dict] = []
    seen = set()

    platform = (platform or "web").strip().lower()
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        step = normalize_ai_step(raw)
        key = (
            step["action"],
            step["selector_type"],
            step["selector_value"],
            step["input_value"],
            _str(step.get("compare_type")),
        )
        if key in seen:
            warnings.append(f"检测到重复步骤并已去重: {step['action']} {step['selector_value']}")
            continue
        seen.add(key)
        clean_steps.append(step)

    if clean_steps:
        first_action = clean_steps[0].get("action")
        first_layer = _str(clean_steps[0].get("automation_layer")).lower() or platform
        if platform == "desktop" or first_layer == "desktop":
            if first_action not in ("launch_app", "attach_window"):
                warnings.append(
                    "建议首步使用 launch_app 或 attach_window 打开目标应用（桌面自动化不使用 navigate）。"
                )
        elif platform == "android" or first_layer == "android":
            if first_action not in ("open_app", "tap", "attach_window"):
                warnings.append("建议首步使用 open_app 启动目标 Android 应用。")
        elif first_action != "navigate":
            warnings.append("建议首步使用 navigate 进入目标页面，以提升执行稳定性。")

    for idx, step in enumerate(clean_steps, start=1):
        layer = _str(step.get("automation_layer")).lower() or platform
        if layer == "desktop" or platform == "desktop":
            act = step.get("action")
            if act == "launch_app" and not step.get("input_value") and not step.get("desktop_spec"):
                warnings.append(f"第{idx}步 launch_app 缺少 input_value 或 desktop_spec.path，运行时可能失败。")
            elif act == "attach_window" and not step.get("desktop_spec"):
                warnings.append(f"第{idx}步 attach_window 缺少 desktop_spec（如 title_contains），运行时可能失败。")
            elif act in {"click", "input", "double_click", "right_click"} and not (
                step.get("selector_value") or step.get("locate_prompt") or step.get("desktop_spec")
            ):
                warnings.append(f"第{idx}步桌面点击/输入缺少定位（selector_value、locate_prompt 或 desktop_spec）。")
            continue
        if layer == "android" or platform == "android":
            if step["action"] in {"tap", "click", "input", "input_text"} and not step.get("selector_value"):
                warnings.append(f"第{idx}步缺少 Android 定位 selector_value，运行时可能失败。")
            continue
        if step["action"] in {"ai_tap", "ai_input", "ai_scroll"}:
            lp = _str(step.get("locate_prompt")) or _str(step.get("description"))
            if not lp:
                warnings.append(f"第{idx}步缺少元素描述（locate_prompt / description），运行时可能失败。")
        elif step["action"] in {"assert_vision", "wait_vision", "extract_vision"}:
            desc = _str(step.get("description")) or _str(step.get("input_value")) or _str(step.get("locate_prompt"))
            if not desc:
                warnings.append(f"第{idx}步缺少画面描述（description / input_value），运行时可能失败。")
        elif step["action"] in {"click", "input", "verify", "extract_text", "assert"} and not step["selector_value"]:
            ct = _str(step.get("compare_type")).lower()
            if step["action"] == "assert" and ct in (
                "url_equals",
                "url_contains",
                "page_text_contains",
                "page_text_equals",
                "page_text_regex",
                "vision_contains",
            ):
                pass
            else:
                warnings.append(f"第{idx}步缺少 selector_value，运行时可能失败。")
        if step["action"] == "input" and not step["input_value"]:
            desc = _str(step.get("description"))
            if not any(
                m in desc or m.lower() in desc.lower()
                for m in (
                    "留空", "为空", "清空", "不填", "空账号", "空密码", "空白",
                    "leave empty", "leave blank", "empty field", "clear field",
                )
            ):
                warnings.append(f"第{idx}步 input 未填写输入值，请在步骤编辑中补充或重新生成。")
        if step["action"] == "ai_input" and not step["input_value"]:
            warnings.append(f"第{idx}步 ai_input 未填写输入内容。")
        if step["action"] == "navigate" and not step["input_value"]:
            warnings.append(f"第{idx}步 navigate 未填写 URL，请在步骤编辑中补充或重新生成。")
        if step["action"] == "wait":
            try:
                ms = int(step["input_value"] or "0")
                if ms > 15000:
                    warnings.append(f"第{idx}步等待时间较长({ms}ms)，建议改为显式条件等待。")
            except Exception:
                warnings.append(f'第{idx}步 wait 参数非数字: {step["input_value"]}')

    return clean_steps, warnings


def _steps_mixed_multi_end(steps: Any) -> bool:
    """步骤集合是否多端混合（PC 侧 + 手机侧并存）。

    多端联动用例（per-step layer 已按工具前缀归因）绝不能走单平台 repair：
    repair_desktop_ai_steps_inplace 会强制全部步骤 automation_layer='desktop'，
    把 android 步骤污染成桌面步骤（阶段1 修过录制侧，此处在 normalize 管线兜底）。
    """
    layers = {
        _str(s.get("automation_layer")).lower()
        for s in steps
        if isinstance(s, dict) and _str(s.get("automation_layer"))
    }
    pc_side = bool(layers & {"web", "desktop"})
    mob_side = bool(layers & {"android", "mobile"})
    return pc_side and mob_side


def apply_step_normalization_to_plan(plan: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    就地更新 plan['steps']，并将 warnings 写入 plan['meta']['normalization_warnings']（合并已有 meta）。
    返回 (plan, warnings) 便于 API 同时设置顶层 warnings 字段。
    """
    if not isinstance(plan, dict):
        return plan, []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return plan, []
    platform = infer_plan_platform_type(plan, steps)
    if platform == "desktop" and not _steps_mixed_multi_end(steps):
        repair_warns = repair_desktop_ai_steps_inplace(steps)
    elif platform == "android":
        repair_warns = []
    else:
        repair_warns = repair_raw_ai_steps_for_platform(steps)
    clean, warnings = dedupe_and_validate_ai_steps(steps, platform=platform)
    warnings = list(repair_warns) + list(warnings)
    plan["steps"] = clean
    meta = plan.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        plan["meta"] = meta
    if warnings:
        meta["normalization_warnings"] = warnings
    if platform != "web":
        meta["platform_type"] = platform
        plan["platform"] = platform
    extra: List[str] = []
    sr = meta.get("step_repair_warnings")
    if isinstance(sr, list):
        extra.extend(str(x) for x in sr if str(x).strip())
    cw = meta.get("selector_clamp_warnings")
    if isinstance(cw, list):
        extra.extend(str(x) for x in cw if str(x).strip())
    lv = meta.get("locator_validation")
    if isinstance(lv, list):
        extra.extend(str(x) for x in lv if str(x).strip())
    merged_warnings = extra + warnings
    return plan, merged_warnings


def _wait_ms_from_ai_input(input_value: str) -> int:
    """AI 步骤 wait：<=120 视为秒，否则视为毫秒（与模型提示一致）。"""
    raw = _str(input_value)
    if not raw:
        return 1000
    try:
        v = int(float(raw))
    except Exception:
        return 1000
    if v <= 0:
        return 1000
    if v <= 120:
        return min(v * 1000, 120_000)
    return min(v, 600_000)


def _parse_locator_candidates(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    s = _str(val)
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return s


def ai_plan_steps_to_playwright_script_steps(steps: Any) -> List[Dict[str, Any]]:
    """
    将 AI 规划步骤转为 playwright_automation.execute_script_steps 可执行的步骤列表。
    （navigate 使用 url；click/input 使用 selector；wait 使用 time 毫秒）
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(steps, list):
        return out
    for raw in steps:
        if not isinstance(raw, dict):
            continue
        step = normalize_ai_step(raw)
        action = step["action"]
        st = step["selector_type"] or "css"
        sv = step["selector_value"]
        iv = step["input_value"]
        desc = step["description"]
        lc = _parse_locator_candidates(step.get("locator_candidates"))

        if action == "navigate":
            url = iv or sv
            if url:
                out.append({"action": "navigate", "url": url, "description": desc})
            continue
        if action == "click":
            if not sv:
                continue
            row: Dict[str, Any] = {
                "action": "click",
                "selector": sv,
                "selector_type": st,
                "iframe_selector": "",
                "description": desc,
            }
            if lc is not None:
                row["locator_candidates"] = lc
            out.append(row)
            continue
        if action == "input":
            if not sv or not iv:
                continue
            row = {
                "action": "input",
                "selector": sv,
                "selector_type": st,
                "iframe_selector": "",
                "text": iv,
                "input_value": iv,
                "description": desc,
            }
            if lc is not None:
                row["locator_candidates"] = lc
            out.append(row)
            continue
        if action == "wait":
            out.append({"action": "wait", "time": _wait_ms_from_ai_input(iv), "description": desc})
            continue
        if action == "verify":
            if not sv:
                continue
            vt = (iv or "auto").strip().lower()
            if vt not in ("auto", "slider", "image", "visible", "exist", "clickable"):
                vt = "auto"
            out.append(
                {
                    "action": "verify",
                    "selector": sv,
                    "selector_type": st,
                    "iframe_selector": "",
                    "verify_type": vt,
                    "input_value": iv,
                    "text": iv,
                    "description": desc,
                }
            )
            continue
        if action == "assert":
            ct = _str(step.get("compare_type")).lower() or "text_contains"
            out.append(
                {
                    "action": "assert",
                    "selector": sv,
                    "selector_type": st,
                    "iframe_selector": "",
                    "input_value": iv,
                    "text": iv,
                    "compare_type": ct,
                    "description": desc,
                }
            )
            continue
        if action == "extract_text":
            row = {
                "action": "extract_text",
                "selector": sv,
                "selector_type": st,
                "iframe_selector": "",
                "description": desc,
            }
            out.append(row)
            continue
    return out
