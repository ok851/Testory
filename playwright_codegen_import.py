# -*- coding: utf-8 -*-
"""
将 Playwright Codegen 生成的 Python / JavaScript 片段解析为本平台 test_steps 批量插入格式。
不要求可执行，仅做常见 API 行的正则提取；无法识别的行写入 warnings。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

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


def _parse_locator_literal(raw: str) -> Tuple[str, str]:
    """
    解析 Codegen 里 locator('...') / click('...') 的字符串。
    Playwright 写法可能是 '//div'、'/html/body/...' 或 'xpath=//div/...'。
    返回 (写入步骤的 selector_value, selector_type)，xpath 统一去掉 xpath= 前缀（执行层会再补）。
    """
    s = (raw or "").strip()
    low = s.lower()
    if low.startswith("xpath="):
        return s[6:].lstrip(), "xpath"
    if s.startswith("//") or (s.startswith("/") and not s.startswith("//")):
        return s, "xpath"
    return s, "css"


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

    # 亦处理同一行多条语句（少见）
    expanded: List[str] = []
    for line in merged:
        for part in re.split(r"(?<=\))\s*(?=page\.)", line):
            part = part.strip().rstrip(";")
            if part:
                expanded.append(part)

    for line in expanded:
        # 去掉 await / 变量 page
        ln = re.sub(r"^\s*await\s+", "", line)
        ln = re.sub(r"^\s*\w+\.", "page.", ln, count=1) if re.match(r"^\s*\w+\.goto", ln) else ln
        if not ln.startswith("page."):
            if "page." in ln:
                idx = ln.index("page.")
                ln = ln[idx:]
            else:
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
            r"page\.get_by_text\(\s*(['\"])(?P<t>.*?)\1\)\.click\(\s*\)",
            ln,
        )
        if m:
            t = m.group("t")
            steps.append(
                _base_step("click", "partial_text", t, "", f"点击文本「{t[:40]}」")
            )
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
            r"page\.getByText\(\s*(?P<q>['\"])(?P<t>.*?)(?P=q)\s*\)\.click\(\s*\)",
            ln,
        )
        if m:
            t = m.group("t")
            steps.append(
                _base_step("click", "partial_text", t, "", f"点击文本「{t[:40]}」")
            )
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
