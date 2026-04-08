# -*- coding: utf-8 -*-
"""
将 Playwright Codegen 生成的 Python / JavaScript 片段解析为本平台 test_steps 批量插入格式。
不要求可执行，仅做常见 API 行的正则提取；无法识别的行写入 warnings。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, MutableSequence, Optional, Set, Tuple

# 与页面录制器 buildLocatorPack 一致：score 越高越优先（执行降级与默认主选 XPath 靠前）

_Q = r'(["\'])'  # quote capture group name handled inline


def _base_step(
    action: str,
    selector_type: str,
    selector_value: str,
    input_value: str,
    description: str,
) -> Dict[str, Any]:
    return {
        "action": action,
        "selector_type": selector_type,
        "selector_value": selector_value or "",
        "input_value": input_value if input_value is not None else "",
        "description": description,
    }


def _decode_js_string_escapes(s: str) -> str:
    """与 TS/JS 字符串字面量一致的反斜杠转义（用于 locator('...\\'...\\'') 导入与手粘源码）。"""
    if "\\" not in s:
        return s
    out: List[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            c = s[i + 1]
            if c in "'\"\\":
                out.append(c)
                i += 2
                continue
            if c == "n":
                out.append("\n")
                i += 2
                continue
            if c == "r":
                out.append("\r")
                i += 2
                continue
            if c == "t":
                out.append("\t")
                i += 2
                continue
            if (
                c == "u"
                and i + 6 <= n
                and all(ch in "0123456789abcdefABCDEF" for ch in s[i + 2 : i + 6])
            ):
                try:
                    out.append(chr(int(s[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
        out.append(s[i])
        i += 1
    return "".join(out)


def _scan_js_quoted_string(line: str, start: int) -> Optional[Tuple[str, int]]:
    """从 line[start] 的起始引号解析到闭合引号，返回 (解码后的内容, 闭合引号后下标)。失败返回 None。"""
    if start >= len(line) or line[start] not in "\"'":
        return None
    q = line[start]
    i = start + 1
    out: List[str] = []
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            nxt = line[i + 1]
            if nxt in "\"'\\":
                out.append(nxt)
                i += 2
                continue
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
        if ch == q:
            return ("".join(out), i + 1)
        out.append(ch)
        i += 1
    return None


def _try_page_locator_click_or_fill(ln: str) -> Optional[Tuple[str, str, str]]:
    """
    解析 page.locator('...').click() / .fill('...')，支持单引号串内 \\'。
    返回 (click|fill, selector 原文已解码, fill 时文本已解码或 click 时 '')；无法解析返回 None。
    """
    m = re.search(r"page\.locator\s*\(", ln)
    if not m:
        return None
    qpos = m.end()
    while qpos < len(ln) and ln[qpos] in " \t\n":
        qpos += 1
    scanned = _scan_js_quoted_string(ln, qpos)
    if not scanned:
        return None
    sel_raw, after_sel = scanned
    tail = ln[after_sel:].lstrip()
    if not tail.startswith(")"):
        return None
    tail = tail[1:].lstrip()
    if re.match(r"\.click\s*\(\s*\)", tail):
        return ("click", sel_raw, "")
    mfill = re.match(r"\.fill\s*\(\s*", tail)
    if mfill:
        rest = tail[mfill.end() :]
        qs = 0
        while qs < len(rest) and rest[qs] in " \t\n":
            qs += 1
        vscan = _scan_js_quoted_string(rest, qs)
        if not vscan:
            return None
        val_raw, _ = vscan
        return ("fill", sel_raw, val_raw)
    return None


def _parse_locator_literal(raw: str) -> Tuple[str, str]:
    """
    解析 Codegen 里 locator('...') / click('...') 的字符串。
    Playwright 写法可能是 '//div'、'/html/body/...' 或 'xpath=//div/...'。
    返回 (写入步骤的 selector_value, selector_type)，xpath 统一去掉 xpath= 前缀（执行层会再补）。
    """
    s = (raw or "").strip()
    if "\\" in s:
        s = _decode_js_string_escapes(s)
    low = s.lower()
    if low.startswith("xpath="):
        return s[6:].lstrip(), "xpath"
    if s.startswith("//") or (s.startswith("/") and not s.startswith("//")):
        return s, "xpath"
    return s, "css"


_ATTR_XPATH_ESCAPES = (
    ('&', '&amp;'),
    ('"', '&quot;'),
    ("'", '&apos;'),
)


def _xpath_quote_attr(val: str) -> str:
    s = val or ""
    if '"' not in s:
        return f'"{s}"'
    for ch, esc in _ATTR_XPATH_ESCAPES:
        s = s.replace(ch, esc)
    return f'"{s}"'


def _step_click_text_partial(t: str, desc: str) -> Dict[str, Any]:
    """Codegen 文本点击：平台执行层用 partial_text → XPath contains。"""
    return _base_step("click", "partial_text", t, "", desc)


def _step_click_in_role_then_text(role: str, text: str) -> Dict[str, Any]:
    """
    getByRole(role).getByText(text) 链式调用：优先用限定在 role 容器内的 XPath，
    避免页面上同名文案多处命中。
    """
    r = (role or "").strip().strip("\"'")
    t = (text or "").strip()
    if (
        len(t) <= 48
        and "\n" not in t
        and '"' not in t
        and "'" not in t
        and re.match(r"^[a-zA-Z][\w\-]*$", r)
    ):
        xp = f'//*[@role="{r}"]//*[contains(normalize-space(.),"{t}")]'
        return _base_step(
            "click",
            "xpath",
            xp,
            "",
            f"在 role={r} 内点击文本「{t[:40]}」",
        )
    return _step_click_text_partial(
        t, f"在 getByRole({r}) 内点击文本「{t[:40]}」"
    )


def _step_click_role_name_then_text(role: str, named: str, text: str) -> Dict[str, Any]:
    """
    getByRole(role, { name / name= }).getByText(text)：
    用「同节点 role + 文案含无障碍名称」近似限定父级，再在其下匹配目标文本。
    """
    r = (role or "").strip().strip("\"'")
    n = (named or "").strip()
    t = (text or "").strip()
    if (
        len(n) <= 48
        and len(t) <= 48
        and "\n" not in n
        and "\n" not in t
        and '"' not in n
        and '"' not in t
        and "'" not in n
        and "'" not in t
        and re.match(r"^[a-zA-Z][\w\-]*$", r)
    ):
        xp = (
            f'//*[@role="{r}"][contains(normalize-space(.),"{n}")]'
            f'//*[contains(normalize-space(.),"{t}")]'
        )
        return _base_step(
            "click",
            "xpath",
            xp,
            "",
            f"在 role={r}、名称含「{n[:30]}」内点击文本「{t[:40]}」",
        )
    return _step_click_text_partial(
        t,
        f"getByRole({r}, name「{n[:30]}」).getByText「{t[:40]}」",
    )


def _pack_append(
    pack: MutableSequence[Dict[str, Any]], sel_type: str, value: str, score: int
) -> None:
    value = (value or "").strip()
    if not value:
        return
    for p in pack:
        if p.get("type") == sel_type and p.get("value") == value:
            return
    pack.append({"type": sel_type, "value": value, "score": score})


def _append_xpath_button_link_text_fallbacks(
    pack: MutableSequence[Dict[str, Any]], sv: str
) -> None:
    """
    Codegen 常见 //button|//a[normalize-space(.)='文案']，运行时多为 span/div/el-dropdown 等。
    追加宽一些的 XPath / partial_text 备选（不含主选本身）。
    """
    m_txt_exact = re.search(
        r"//(?P<tag>button|a)\s*\["
        r"\s*normalize-space\s*\(\s*\.\s*\)\s*=\s*"
        r"(?P<q>['\"])(?P<txt>.*?)(?P=q)\s*"
        r"\]",
        (sv or "").strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not m_txt_exact:
        return
    tag = (m_txt_exact.group("tag") or "button").lower()
    txt = (m_txt_exact.group("txt") or "").strip()
    if not txt:
        return
    lit = _xpath_quote_attr(txt)
    # 图标+文案（如 button > .fa-user-circle + span#userLabel）：整节点 normalize-space(.) 常被图标/空白干扰，不等于纯文案
    _pack_append(
        pack,
        "xpath",
        f"//{tag}[.//span[normalize-space(.)={lit}]]",
        99,
    )
    _pack_append(
        pack,
        "xpath",
        f"//{tag}[.//*[normalize-space(.)={lit}]]",
        98,
    )
    _pack_append(
        pack,
        "xpath",
        f"//*[(self::button or self::a) and normalize-space(.)={lit}]",
        96,
    )
    _pack_append(
        pack,
        "xpath",
        f"//*[(@role='button' or @role='menuitem') and normalize-space(.)={lit}]",
        93,
    )
    _pack_append(
        pack,
        "xpath",
        f"(//*[normalize-space(.)={lit}])[1]",
        90,
    )
    _pack_append(
        pack,
        "xpath",
        f"//*[self::button and contains(normalize-space(.),{lit})]",
        84,
    )
    if len(txt) <= 48 and "\n" not in txt and '"' not in txt and "'" not in txt:
        _pack_append(pack, "partial_text", txt, 76)


def runtime_xpath_button_link_fallback_items(selector: str) -> List[Dict[str, Any]]:
    """供执行层在主选失败且步骤未存 locator_candidates 时使用（与 enrich 逻辑一致）。"""
    p: List[Dict[str, Any]] = []
    _append_xpath_button_link_text_fallbacks(p, selector)
    return p


def xpath_click_attempt_variants(selector: str) -> List[str]:
    """
    对 //button|//a[normalize-space(.)='文案'] 返回应依次尝试的 XPath。
    含「图标 + span 文案」时原 XPath 常无法匹配；优先 //button[.//span[...]] 再 //button[.//*[...]]，最后保留原始串。
    """
    s = (selector or "").strip()
    m = re.search(
        r"//(?P<tag>button|a)\s*\["
        r"\s*normalize-space\s*\(\s*\.\s*\)\s*=\s*"
        r"(?P<q>['\"])(?P<txt>.*?)(?P=q)\s*"
        r"\]",
        s,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return [s]
    tag = (m.group("tag") or "button").lower()
    txt = (m.group("txt") or "").strip()
    if not txt:
        return [s]
    lit = _xpath_quote_attr(txt)
    out: List[str] = []
    seen: Set[str] = set()
    for candidate in (
        f"//{tag}[.//span[normalize-space(.)={lit}]]",
        f"//{tag}[.//*[normalize-space(.)={lit}]]",
        s,
    ):
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def _enrich_single_step_locator_candidates(step: Dict[str, Any]) -> Dict[str, Any]:
    """根据主定位串推导 XPath/CSS 备选（XPath score 更高），写入 locator_candidates JSON。"""
    if step.get("locator_candidates"):
        return step
    action = (step.get("action") or "").strip().lower()
    if action in ("navigate", "wait", "scroll", ""):
        return step

    st = (step.get("selector_type") or "css").strip().lower()
    sv = (step.get("selector_value") or "").strip()
    if not sv and st not in ("placeholder", "label", "partial_text"):
        return step

    pack: List[Dict[str, Any]] = []
    primary_t = st
    primary_v = sv

    if st == "xpath":
        _pack_append(pack, "xpath", sv, 102)
        id_match = re.search(r"@id\s*=\s*(['\"])(?P<id>[^'\"]+)\1", sv)
        if id_match:
            idv = id_match.group("id")
            _pack_append(pack, "css", f'[id="{idv}"]', 100)
            if re.match(r"^[A-Za-z][\w\-]*$", idv):
                _pack_append(pack, "css", f"#{idv}", 99)
        for attr in ("data-testid", "data-cy", "data-test", "data-qa"):
            am = re.search(rf"@{re.escape(attr)}\s*=\s*(['\"])(?P<v>[^'\"]+)\1", sv)
            if am:
                v = am.group("v")
                _pack_append(pack, "css", f'[{attr}="{v}"]', 96)
        _append_xpath_button_link_text_fallbacks(pack, sv)
    elif st == "css":
        m = re.match(r"^#([A-Za-z_][\w\-]*)$", sv)
        if m:
            idv = m.group(1)
            _pack_append(pack, "xpath", f'//*[@id="{idv}"]', 102)
            _pack_append(pack, "css", f"#{idv}", 100)
        else:
            mid = re.match(r"^\[id\s*=\s*(['\"])([^'\"]+)\1\]$", sv)
            if mid:
                idv = mid.group(2)
                _pack_append(pack, "xpath", f'//*[@id="{idv}"]', 102)
                _pack_append(pack, "css", f'[id="{idv}"]', 100)
            else:
                for attr in ("data-testid", "data-cy", "data-test", "data-qa"):
                    pat = rf"^\[[\s]*{re.escape(attr)}\s*=\s*(['\"])([^'\"]+)\1[\s]*\]$"
                    dm = re.match(pat, sv)
                    if dm:
                        v = dm.group(2)
                        aq = _xpath_quote_attr(v)
                        _pack_append(
                            pack,
                            "xpath",
                            f"//*[@{attr}={aq}]",
                            98,
                        )
                        _pack_append(pack, "css", sv, 96)
                        break
                if not pack:
                    tag_m = re.match(
                        r"^([a-zA-Z][\w]*)?\[([a-zA-Z][\w\-]*)\s*=\s*(['\"])([^'\"]*)\3\]$", sv
                    )
                    if tag_m:
                        tag = tag_m.group(1) or "*"
                        attr = tag_m.group(2)
                        val = tag_m.group(4)
                        aq = _xpath_quote_attr(val)
                        if tag == "*":
                            _pack_append(pack, "xpath", f"//*[@{attr}={aq}]", 90)
                        else:
                            _pack_append(
                                pack, "xpath", f"//{tag}[@{attr}={aq}]", 92
                            )
                        _pack_append(pack, "css", sv, 88)
    elif st == "placeholder":
        aq = _xpath_quote_attr(sv)
        _pack_append(pack, "xpath", f"//input[@placeholder={aq}]", 73)
        _pack_append(pack, "xpath", f"//textarea[@placeholder={aq}]", 72)
        esc = sv.replace("\\", "\\\\").replace('"', '\\"')
        _pack_append(pack, "css", f'input[placeholder="{esc}"]', 71)
    elif st == "label":
        aq = _xpath_quote_attr(sv)
        _pack_append(
            pack,
            "xpath",
            f"//*[self::label][normalize-space()={aq}]",
            70,
        )
    elif st == "partial_text":
        if len(sv) <= 48 and "\n" not in sv and '"' not in sv and "'" not in sv:
            _pack_append(
                pack,
                "xpath",
                f'//*[contains(normalize-space(.),"{sv}")]',
                55,
            )

    if not pack:
        return step

    pack.sort(key=lambda x: -int(x.get("score") or 0))
    seen = {(primary_t, primary_v)}
    filtered: List[Dict[str, Any]] = []
    for p in pack:
        key = (str(p.get("type") or "").lower(), str(p.get("value") or ""))
        if key in seen:
            continue
        seen.add(key)
        filtered.append(p)
    if not filtered:
        return step

    xpath_first = (
        filtered and str(filtered[0].get("type") or "").lower() == "xpath"
    )
    swappable_css = st == "css" and bool(sv) and (
        re.match(r"^#([A-Za-z_][\w\-]*)$", sv)
        or re.match(r"^\[id\s*=", sv)
        or any(
            re.match(rf"^\[[\s]*{re.escape(a)}\s*=", sv)
            for a in ("data-testid", "data-cy", "data-test", "data-qa")
        )
    )
    if xpath_first and swappable_css:
        out = dict(step)
        xp_val = str(filtered[0].get("value") or "")
        rest = [
            c
            for c in filtered
            if not (
                str(c.get("type") or "").lower() == "xpath"
                and str(c.get("value") or "") == xp_val
            )
        ]
        rest.append({"type": st, "value": sv, "score": 60})
        rest.sort(key=lambda x: -int(x.get("score") or 0))
        out["selector_type"] = "xpath"
        out["selector_value"] = xp_val
        out["locator_candidates"] = json.dumps(rest, ensure_ascii=False)
        return out

    out = dict(step)
    out["locator_candidates"] = json.dumps(filtered, ensure_ascii=False)
    return out


def enrich_steps_with_xpath_priority(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_enrich_single_step_locator_candidates(dict(s)) for s in steps]


def parse_playwright_codegen_to_steps(code: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    steps: List[Dict[str, Any]] = []
    if not code or not code.strip():
        return steps, ["代码为空"]

    lines = code.replace("\r\n", "\n").split("\n")
    # 合并逻辑行：去掉纯注释与 import
    merged: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("import ") or line.startswith("from "):
            continue
        if line.startswith("async def ") or line.startswith("def "):
            continue
        # Playwright Test JS/TS：test('...', async ({ page }) => {  与  }); / 单独大括号
        if re.match(r"test\s*\(\s*", line) or line.startswith("});") or line in ("{", "}"):
            continue
        merged.append(line)

    # 亦处理同一行多条语句（少见）：); await page1.goto / page2.locator 等
    _multi_stmt_after_paren = re.compile(
        r"(?<=\))\s*(?=(?:await\s+)?[A-Za-z_$][\w$]*\.)"
    )
    expanded: List[str] = []
    for line in merged:
        for part in _multi_stmt_after_paren.split(line):
            part = part.strip().rstrip(";")
            if part:
                expanded.append(part)

    for line in expanded:
        # 去掉 await，并把行首 Playwright 页面对象统一成 page（page1 / p / context 绑定名等）
        ln = re.sub(r"^\s*await\s+", "", line)
        ln = re.sub(r"^\s*[A-Za-z_$][\w$]*\.", "page.", ln, count=1)
        if not ln.startswith("page."):
            if "page." in ln:
                idx = ln.index("page.")
                ln = ln[idx:]
            else:
                continue

        plc = _try_page_locator_click_or_fill(ln)
        if plc:
            action, sel_lit, fill_lit = plc
            val, st = _parse_locator_literal(sel_lit)
            if action == "click":
                steps.append(_base_step("click", st, val, "", f"点击 {val[:60]}"))
            else:
                steps.append(
                    _base_step("input", st, val, fill_lit, f"输入：{fill_lit[:40]}")
                )
            continue

        handled = False

        # page.goto('url')
        m = re.search(r"page\.goto\(\s*(['\"])(?P<u>.*?)\1\s*\)", ln)
        if m:
            url = m.group("u")
            steps.append(
                _base_step(
                    "navigate",
                    "css",
                    "",
                    url,
                    f"打开页面 {url[:80]}",
                )
            )
            handled = True
        if handled:
            continue

        # page.fill / get_by_*.fill
        m = re.search(
            r"page\.fill\(\s*(['\"])(?P<s>.*?)\1\s*,\s*(['\"])(?P<v>.*?)\3",
            ln,
        )
        if m:
            sel, v = m.group("s"), m.group("v")
            val, st = _parse_locator_literal(sel)
            steps.append(
                _base_step("input", st, val, v, f"输入：{v[:40]}")
            )
            continue
        m = re.search(
            r"page\.locator\(\s*(['\"])(?P<s>.*?)\1\)\.fill\(\s*(['\"])(?P<v>.*?)\3",
            ln,
        )
        if m:
            sel, v = m.group("s"), m.group("v")
            val, st = _parse_locator_literal(sel)
            steps.append(
                _base_step("input", st, val, v, f"输入：{v[:40]}")
            )
            continue
        m = re.search(
            r"page\.get_by_label\(\s*(['\"])(?P<lb>.*?)\1\)\.fill\(\s*(['\"])(?P<v>.*?)\3",
            ln,
        )
        if m:
            lb, v = m.group("lb"), m.group("v")
            steps.append(
                _base_step("input", "label", lb, v, f"按标签「{lb}」输入")
            )
            continue
        m = re.search(
            r"page\.get_by_placeholder\(\s*(['\"])(?P<ph>.*?)\1\)\.fill\(\s*(['\"])(?P<v>.*?)\3",
            ln,
        )
        if m:
            ph, v = m.group("ph"), m.group("v")
            steps.append(
                _base_step("input", "placeholder", ph, v, f"占位符「{ph}」输入")
            )
            continue

        # JavaScript / TypeScript Codegen：camelCase API + { name: '...' }
        m = re.search(
            r"page\.getByRole\(\s*(?P<q1>['\"])(?P<role>.*?)(?P=q1)\s*,\s*\{\s*name\s*:\s*(?P<q2>['\"])(?P<name>.*?)(?P=q2)\s*[^}]*\}\s*\)\.fill\(\s*(?P<q3>['\"])(?P<v>.*?)(?P=q3)\s*\)",
            ln,
        )
        if m:
            name, v = m.group("name"), m.group("v")
            steps.append(
                _base_step(
                    "input",
                    "partial_text",
                    name,
                    v,
                    f"按无障碍名称「{name[:30]}」输入",
                )
            )
            continue
        m = re.search(
            r"page\.getByLabel\(\s*(?P<q1>['\"])(?P<lb>.*?)(?P=q1)\s*\)\.fill\(\s*(?P<q2>['\"])(?P<v>.*?)(?P=q2)\s*\)",
            ln,
        )
        if m:
            lb, v = m.group("lb"), m.group("v")
            steps.append(
                _base_step("input", "label", lb, v, f"按标签「{lb}」输入")
            )
            continue
        m = re.search(
            r"page\.getByPlaceholder\(\s*(?P<q1>['\"])(?P<ph>.*?)(?P=q1)\s*\)\.fill\(\s*(?P<q2>['\"])(?P<v>.*?)(?P=q2)\s*\)",
            ln,
        )
        if m:
            ph, v = m.group("ph"), m.group("v")
            steps.append(
                _base_step("input", "placeholder", ph, v, f"占位符「{ph}」输入")
            )
            continue

        # page.click / locator().click / get_by_role().click / get_by_text().click
        m = re.search(r"page\.click\(\s*(['\"])(?P<s>.*?)\1", ln)
        if m:
            sel = m.group("s")
            val, st = _parse_locator_literal(sel)
            steps.append(_base_step("click", st, val, "", f"点击 {val[:60]}"))
            continue
        m = re.search(
            r"page\.locator\(\s*(['\"])(?P<s>.*?)\1\)\.click\(\s*\)",
            ln,
        )
        if m:
            sel = m.group("s")
            val, st = _parse_locator_literal(sel)
            steps.append(_base_step("click", st, val, "", f"点击 {val[:60]}"))
            continue
        m = re.search(
            r"page\.get_by_role\(\s*(['\"])(?P<role>.*?)\1\s*,\s*name\s*=\s*(['\"])(?P<name>.*?)\3\)\s*\.get_by_text\(\s*(['\"])(?P<t>.*?)\5\)\.click\(\s*\)",
            ln,
        )
        if m:
            role, name, t = m.group("role"), m.group("name"), m.group("t")
            steps.append(_step_click_role_name_then_text(role, name, t))
            continue
        m = re.search(
            r"page\.get_by_role\(\s*(['\"])(?P<role>.*?)\1\s*,\s*name\s*=\s*(['\"])(?P<name>.*?)\3\)\.click\(\s*\)",
            ln,
        )
        if m:
            name = m.group("name")
            steps.append(
                _base_step(
                    "click",
                    "partial_text",
                    name,
                    "",
                    f"点击角色按钮/链接（文本含「{name[:40]}」）",
                )
            )
            continue
        m = re.search(
            r"page\.get_by_role\(\s*(['\"])(?P<role>.*?)\1\s*\)\s*\.get_by_text\(\s*(['\"])(?P<t>.*?)\3\)\.click\(\s*\)",
            ln,
        )
        if m:
            role, t = m.group("role"), m.group("t")
            steps.append(_step_click_in_role_then_text(role, t))
            continue
        m = re.search(
            r"page\.get_by_text\(\s*(['\"])(?P<t>.*?)\1\)\s*(?:\.first\(\)|\.last\(\)|\.nth\s*\(\s*\d+\s*\))\s*\.click\(\s*\)",
            ln,
        )
        if m:
            t = m.group("t")
            steps.append(_step_click_text_partial(t, f"点击文本「{t[:40]}」"))
            continue
        m = re.search(
            r"page\.get_by_text\(\s*(['\"])(?P<t>.*?)\1\)\.click\(\s*\)",
            ln,
        )
        if m:
            t = m.group("t")
            steps.append(_step_click_text_partial(t, f"点击文本「{t[:40]}」"))
            continue
        m = re.search(
            r"page\.getByRole\(\s*(?P<q1>['\"])(?P<role>.*?)(?P=q1)\s*,\s*\{\s*name\s*:\s*(?P<q2>['\"])(?P<name>.*?)(?P=q2)\s*[^}]*\}\s*\)\s*\.getByText\(\s*(?P<q3>['\"])(?P<t>.*?)(?P=q3)\s*\)\.click\(\s*\)",
            ln,
        )
        if m:
            role, name, t = m.group("role"), m.group("name"), m.group("t")
            steps.append(_step_click_role_name_then_text(role, name, t))
            continue
        m = re.search(
            r"page\.getByRole\(\s*(?P<q1>['\"])(?P<role>.*?)(?P=q1)\s*\)\s*\.getByText\(\s*(?P<q2>['\"])(?P<t>.*?)(?P=q2)\s*\)\.click\(\s*\)",
            ln,
        )
        if m:
            role, t = m.group("role"), m.group("t")
            steps.append(_step_click_in_role_then_text(role, t))
            continue
        m = re.search(
            r"page\.getByRole\(\s*(?P<q1>['\"])(?P<role>.*?)(?P=q1)\s*,\s*\{\s*name\s*:\s*(?P<q2>['\"])(?P<name>.*?)(?P=q2)\s*[^}]*\}\s*\)\.click\(\s*\)",
            ln,
        )
        if m:
            name = m.group("name")
            steps.append(
                _base_step(
                    "click",
                    "partial_text",
                    name,
                    "",
                    f"点击（getByRole 名称「{name[:40]}」）",
                )
            )
            continue
        m = re.search(
            r"page\.getByText\(\s*(?P<q>['\"])(?P<t>.*?)(?P=q)\s*\)\s*(?:\.first\(\)|\.last\(\)|\.nth\s*\(\s*\d+\s*\))\s*\.click\(\s*\)",
            ln,
        )
        if m:
            t = m.group("t")
            steps.append(_step_click_text_partial(t, f"点击文本「{t[:40]}」"))
            continue
        m = re.search(
            r"page\.getByText\(\s*(?P<q>['\"])(?P<t>.*?)(?P=q)\s*\)\.click\(\s*\)",
            ln,
        )
        if m:
            t = m.group("t")
            steps.append(_step_click_text_partial(t, f"点击文本「{t[:40]}」"))
            continue

        # select_option
        m = re.search(
            r"page\.select_option\(\s*(['\"])(?P<s>.*?)\1\s*,\s*(['\"])(?P<v>.*?)\3",
            ln,
        )
        if m:
            sel, v = m.group("s"), m.group("v")
            val, st = _parse_locator_literal(sel)
            steps.append(
                _base_step("select", st, val, v, f"下拉选择 {v}")
            )
            continue

        # press Enter
        m = re.search(
            r"page\.press\(\s*(['\"])(?P<s>.*?)\1\s*,\s*(['\"])(?P<k>.*?)\3",
            ln,
        )
        if m:
            sel, k = m.group("s"), m.group("k")
            val, st = _parse_locator_literal(sel)
            steps.append(
                _base_step("keypress", st, val, k, f"在元素上按键 {k}")
            )
            continue

        # dblclick
        m = re.search(r"page\.dblclick\(\s*(['\"])(?P<s>.*?)\1", ln)
        if m:
            sel = m.group("s")
            val, st = _parse_locator_literal(sel)
            steps.append(
                _base_step("double_click", st, val, "", f"双击 {val[:60]}")
            )
            continue

        # wait_for_timeout / waitForTimeout(ms) -> wait 秒
        m = re.search(r"page\.wait_for_timeout\(\s*(\d+)\s*\)", ln)
        if m:
            ms = int(m.group(1))
            sec = max(1, ms // 1000) if ms >= 1000 else 1
            steps.append(
                _base_step("wait", "css", "", str(sec), f"等待约 {sec} 秒")
            )
            continue
        m = re.search(r"page\.waitForTimeout\(\s*(\d+)\s*\)", ln)
        if m:
            ms = int(m.group(1))
            sec = max(1, ms // 1000) if ms >= 1000 else 1
            steps.append(
                _base_step("wait", "css", "", str(sec), f"等待约 {sec} 秒")
            )
            continue

        if re.search(r"page\.waitForEvent\s*\(\s*['\"]popup['\"]\s*\)", ln):
            warnings.append(
                "已跳过：新窗口/popup（waitForEvent('popup')）。请在平台中改为「当前页」可定位的步骤，或拆成用例。"
            )
            continue

        # hover
        m = re.search(r"page\.hover\(\s*(['\"])(?P<s>.*?)\1", ln)
        if m:
            sel = m.group("s")
            val, st = _parse_locator_literal(sel)
            steps.append(_base_step("hover", st, val, "", f"悬停 {val[:60]}"))
            continue

        if re.search(r"page\.(check|uncheck|set_checked)", ln):
            warnings.append(f"未转换（请手改步骤）：{ln[:120]}")
            continue
        if "page." in ln and not ln.startswith("#"):
            if any(
                x in ln
                for x in (
                    "expect(",
                    "add_init_script",
                    "route(",
                    "context.",
                    "new_page",
                    "pause",
                )
            ):
                warnings.append(f"已跳过：{ln[:100]}")
            elif re.match(r"page\.\w+", ln):
                warnings.append(f"未识别的 page 调用（可手改）：{ln[:120]}")

    return steps, warnings
