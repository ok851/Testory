# -*- coding: utf-8 -*-
"""
将 Selenium IDE 项目（.side JSON）解析为本平台 test_steps 批量插入格式。
支持 Selenium IDE 3.x 常见 command；无法识别的命令计入 warnings 并跳过。
文档：https://www.selenium.dev/selenium-ide/docs/en/api/commands
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin


def _base_step(
    action: str,
    selector_type: str,
    selector_value: str,
    input_value: str,
    description: str,
) -> Dict[str, Any]:
    return {
        "action": action,
        "selector_type": selector_type or "css",
        "selector_value": selector_value or "",
        "input_value": input_value if input_value is not None else "",
        "description": description,
    }


# CSS Modules / 随机后缀 class，录制时有效、再次打开页面易失效
_CSS_MODULE_HASH = re.compile(
    r"(?:^|\s|>|\+|~)\.[a-zA-Z][a-zA-Z0-9_-]*_[a-zA-Z0-9]{4,}\b"
)


def _iter_target_entries(raw: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Selenium IDE 每条命令：主 target + targets[][] 备选。(locator, hint)。"""
    out: List[Tuple[str, str]] = []
    primary = str(raw.get("target") or "").strip()
    if primary:
        out.append((primary, "primary"))
    for item in raw.get("targets") or []:
        if isinstance(item, (list, tuple)) and len(item) >= 1:
            loc = str(item[0]).strip()
            hint = str(item[1]).strip() if len(item) > 1 else ""
            if loc:
                out.append((loc, hint))
        elif isinstance(item, str) and item.strip():
            out.append((item.strip(), ""))
    return out


def _target_stability_score(loc: str, hint: str) -> int:
    """
    分数越低越优先。避免「.foo_a8f3c > #bar」这类带哈希的 css:finder，
    改用 targets 里带 @id= 的 xpath:attributes 等。
    """
    t = (loc or "").strip()
    if not t:
        return 9999
    tl = t.lower()
    hint_l = (hint or "").lower()

    if tl.startswith("id="):
        return 0
    if tl.startswith("xpath="):
        body = t[6:].strip()
        if "@id=" in body:
            if "xpath:attributes" in hint_l:
                return 3
            if "xpath:idrelative" in hint_l:
                return 12
            if "xpath:position" in hint_l:
                return 35
            return 8
        if "xpath:attributes" in hint_l:
            return 25
        if "xpath:position" in hint_l:
            return 60
        return 45
    if tl.startswith("css="):
        rest = t[4:].strip()
        if re.match(r"^#[\w-]+$", rest):
            return 6
        if _CSS_MODULE_HASH.search(rest):
            return 90
        return 32
    if _CSS_MODULE_HASH.search(t):
        return 90
    return 35


def _choose_best_target(raw: Dict[str, Any]) -> Tuple[str, bool]:
    """
    返回 (选中的定位字符串, 是否与主 target 不同)。
    主 target 在 IDE 里常为 css:finder，可能含易变 class。
    """
    entries = _iter_target_entries(raw)
    if not entries:
        return "", False
    primary_loc = entries[0][0]
    best_loc, best_score = primary_loc, _target_stability_score(primary_loc, "primary")
    for loc, h in entries:
        sc = _target_stability_score(loc, h)
        if sc < best_score:
            best_loc, best_score = loc, sc
            if sc <= 5:  # 已经足够好
                break
    return best_loc, (best_loc != primary_loc)


def _parse_selenium_target(target: str) -> Tuple[str, str]:
    """
    Selenium IDE 的 target：id= / css= / xpath= / linkText= / name= 等；无前缀时按规则猜测。
    返回 (selector_type, selector_value)，xpath 存不含 xpath= 前缀的表达式（与 Playwright 导入一致）。
    """
    t = (target or "").strip()
    if not t:
        return "css", ""

    tl = t.lower()
    prefixes = (
        ("xpath=", "xpath", 6),
        ("css=", "css", 4),
        ("id=", "id", 3),
        ("name=", "name", 5),
        ("linktext=", "text", 9),
        ("partialLinkText=", "partial_text", 16),
        ("partiallinktext=", "partial_text", 16),
        ("link=", "text", 5),
    )
    for pref, stype, skip in prefixes:
        if tl.startswith(pref):
            val = t[skip:].strip()
            if stype == "xpath" and val.lower().startswith("xpath="):
                val = val[6:].lstrip()
            return stype, val

    if t.startswith("//") or (t.startswith("/") and not t.startswith("//")):
        return "xpath", t

    return "css", t


def _resolve_open_url(base_url: str, target: str) -> str:
    target = (target or "").strip()
    if not target:
        return (base_url or "").strip()
    if target.startswith("http://") or target.startswith("https://"):
        return target
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return target
    if target.startswith("/"):
        return urljoin(base + "/", target[1:])
    return urljoin(base + "/", target)


def _normalize_select_value(val: str) -> str:
    """Selenium select 的 value 常为 label=xx / value=xx / index=n，平台下拉步骤多用可见文案。"""
    v = (val or "").strip()
    m = re.match(r"^(label|Label)\s*=\s*(.+)$", v)
    if m:
        return m.group(2).strip()
    m = re.match(r"^(value|Value)\s*=\s*(.+)$", v)
    if m:
        return m.group(2).strip()
    return v


def _commands_from_payload(data: Any) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """
    从粘贴的 JSON 取出命令列表、(项目 base) url、解析过程 warnings。
    """
    meta_warnings: List[str] = []
    base_url = ""

    if data is None:
        return [], "", ["JSON 为空"]

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            return [], "", [f"不是合法 JSON：{e}"]

    if isinstance(data, list):
        return data, "", meta_warnings

    if not isinstance(data, dict):
        return [], "", ["根节点必须是 JSON 对象或命令数组"]

    if "commands" in data and isinstance(data["commands"], list):
        base_url = str(data.get("url") or "")
        return data["commands"], base_url, meta_warnings

    tests = data.get("tests")
    if not isinstance(tests, list) or not tests:
        return [], str(data.get("url") or ""), ["未找到 .side 中的 tests 数组；请粘贴完整 Selenium IDE 项目 JSON"]

    base_url = str(data.get("url") or "")
    first = tests[0]
    if not isinstance(first, dict):
        return [], base_url, ["tests[0] 格式异常"]
    cmds = first.get("commands")
    if not isinstance(cmds, list):
        return [], base_url, ["第一个用例中没有 commands 数组"]

    if len(tests) > 1:
        meta_warnings.append(
            f"项目含 {len(tests)} 个测试用例，当前仅导入第一个「{first.get('name', '(未命名)')}」。"
            "可将其它用例另存为单独项目或合并到一个用例中。"
        )
    return cmds, base_url, meta_warnings


def parse_selenium_ide_to_steps(payload: Union[str, Dict[str, Any], List[Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    解析 Selenium IDE .side（或仅 commands 数组）为步骤列表。

    Returns:
        (steps, warnings)
    """
    warnings: List[str] = []
    commands, base_url, w0 = _commands_from_payload(payload)
    warnings.extend(w0)
    steps: List[Dict[str, Any]] = []

    for i, raw in enumerate(commands):
        if not isinstance(raw, dict):
            warnings.append(f"第 {i + 1} 条命令不是对象，已跳过")
            continue

        cmd = str(raw.get("command") or "").strip()
        target = str(raw.get("target") or "")
        value = str(raw.get("value") or "")
        cmd_l = cmd.lower()

        if not cmd:
            continue

        # 注释 / 空
        if cmd_l in ("#", "//", "break"):
            continue

        if cmd_l in ("open", "open url"):
            url = _resolve_open_url(base_url, target)
            if url:
                steps.append(
                    _base_step("navigate", "css", "", url, f"打开 {url[:80]}")
                )
            else:
                warnings.append(f"第 {i + 1} 行 open 缺少有效 URL")
            continue

        if cmd_l in ("click", "clickandwait"):
            chosen, swapped = _choose_best_target(raw)
            st, sv = _parse_selenium_target(chosen)
            if swapped:
                warnings.append(
                    f"第 {i + 1} 行 click：主 CSS 可能含易变 class，已改用备选定位（更稳）。"
                )
            steps.append(_base_step("click", st, sv, "", f"点击 {sv[:50] or chosen[:50]}"))
            continue

        if cmd_l in ("doubleclick", "double click"):
            chosen, swapped = _choose_best_target(raw)
            st, sv = _parse_selenium_target(chosen)
            if swapped:
                warnings.append(f"第 {i + 1} 行 doubleClick：已改用备选定位。")
            steps.append(_base_step("double_click", st, sv, "", "双击"))
            continue

        if cmd_l in ("rightclick", "context click", "contextmenu"):
            chosen, _ = _choose_best_target(raw)
            st, sv = _parse_selenium_target(chosen)
            steps.append(_base_step("right_click", st, sv, "", "右键点击"))
            continue

        if cmd_l in ("type", "sendkeys", "editcontent"):
            chosen, swapped = _choose_best_target(raw)
            st, sv = _parse_selenium_target(chosen)
            if swapped:
                warnings.append(
                    f"第 {i + 1} 行 type：主选择器可能随页面 class 变化失效，已改用 IDE 备选定位（如 xpath:attributes）。"
                )
            # sendKeys 里可能是 ${KEY_ENTER}，先做简单替换
            iv = value.replace("${KEY_ENTER}", "\n").replace("${KEY_TAB}", "\t")
            steps.append(_base_step("input", st, sv, iv, f"输入 {iv[:40]}"))
            continue

        if cmd_l == "select":
            chosen, _ = _choose_best_target(raw)
            st, sv = _parse_selenium_target(chosen)
            opt = _normalize_select_value(value)
            steps.append(_base_step("select", st, sv, opt, f"下拉选择 {opt[:40]}"))
            continue

        if cmd_l in ("mouseover", "mouse over"):
            chosen, swapped = _choose_best_target(raw)
            st, sv = _parse_selenium_target(chosen)
            if swapped:
                warnings.append(f"第 {i + 1} 行 mouseOver：已改用备选定位。")
            steps.append(_base_step("hover", st, sv, "", "悬停"))
            continue

        if cmd_l == "pause":
            try:
                ms = int(value.strip() or target.strip() or "1000")
            except ValueError:
                ms = 1000
            sec = max(1, ms // 1000)
            steps.append(_base_step("wait", "css", "", str(sec), f"等待约 {sec} 秒（来自 pause {ms}ms）"))
            continue

        if cmd_l == "setwindowsize":
            # 无头执行不关心窗口像素，跳过避免「未识别」噪音
            continue

        if cmd_l in ("mouseout", "mouse out"):
            continue

        if cmd_l in ("waitforelementvisible", "waitforelementpresent", "waitforvisible"):
            try:
                ms = int(value.strip() or "30000")
            except ValueError:
                ms = 30000
            sec = max(1, min(120, ms // 1000))
            steps.append(
                _base_step(
                    "wait",
                    "css",
                    "",
                    str(sec),
                    f"等待约 {sec} 秒（由 IDE「{cmd}」超时 {ms}ms 简化，非逐元素轮询）",
                )
            )
            warnings.append(
                f"第 {i + 1} 行「{cmd}」target=「{target[:50]}...」已转为固定 {sec}s 等待；"
                "平台当前无 IDE 式条件等待，请在失败时加大秒数或拆步骤。"
            )
            continue

        if cmd_l.startswith("assert") or cmd_l.startswith("verify"):
            warnings.append(f"已跳过断言类命令（请在于平台中单独添加断言步骤）：{cmd} {target[:40]}")
            continue

        if cmd_l in ("store", "storetext", "storeattribute", "storetitle", "storexpathcount", "echo", "print"):
            warnings.append(f"已跳过：{cmd}")
            continue

        if cmd_l in ("runscript", "executescript", "ajaxwait"):
            warnings.append(f"已跳过脚本类命令：{cmd}")
            continue

        if cmd_l in ("openbrowser", "close", "selectwindow", "selectframe", "switchtowindow"):
            warnings.append(
                f"已跳过窗口/框架类命令（平台请用步骤中的进入 iframe 等）：{cmd}"
            )
            continue

        warnings.append(f"未识别的命令，已跳过：{cmd} | target={target[:60]}")

    return steps, warnings
