"""
无头抓取目标页可交互元素摘要，供本地 LLM 结合真实 DOM 生成/优化定位符。
依赖已安装的 Playwright 浏览器（与平台执行用例相同）。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)


def extract_http_urls(text: str) -> List[str]:
    s = str(text or "").strip()
    if not s:
        return []
    found = _URL_RE.findall(s)
    # 去重且保持顺序
    return list(dict.fromkeys(found))


def pick_probe_url(
    goal: str,
    case_url: str = "",
    plan: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """从用户描述、用例 URL、或已有 plan（case_url / navigate 步）中选探测地址。"""
    u = str(case_url or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u.split()[0]
    for candidate in extract_http_urls(str(goal or "")):
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate.rstrip(").,]}>'\"")
    if plan and isinstance(plan, dict):
        u2 = str(plan.get("case_url") or "").strip()
        if u2.startswith("http://") or u2.startswith("https://"):
            return u2.split()[0]
        for st in plan.get("steps") or []:
            if not isinstance(st, dict):
                continue
            if str(st.get("action") or "").strip().lower() != "navigate":
                continue
            url = str(st.get("input_value") or st.get("selector_value") or "").strip()
            if url.startswith("http://") or url.startswith("https://"):
                return url.split()[0]
    return None


def fetch_page_controls_summary(url: str) -> Tuple[str, Optional[str]]:
    """
    打开 url（无头），抽取可见可交互控件的精简列表。
    返回 (供 LLM 阅读的文本, 错误信息)。
    """
    timeout_ms = int(os.environ.get("LOCAL_AI_PROBE_TIMEOUT_MS", "35000"))
    max_lines = int(os.environ.get("LOCAL_AI_PROBE_MAX_LINES", "90"))
    max_chars = int(os.environ.get("LOCAL_AI_PROBE_MAX_CHARS", "18000"))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "", "未安装 playwright Python 包，无法探测页面"

    summary_lines: List[str] = []
    err: Optional[str] = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1365, "height": 900},
                )
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(12000, timeout_ms))
                except Exception:
                    pass

                title = page.title()
                snapshot = page.evaluate(
                    """() => {
                      const sel = [
                        'a[href]','button','input:not([type=hidden])','textarea','select',
                        '[role=button]','[role=link]','[role=textbox]','[role=searchbox]',
                        '[role=combobox]','[contenteditable=true]'
                      ].join(',');
                      const nodes = Array.from(document.querySelectorAll(sel)).filter(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width < 2 && r.height < 2) return false;
                        const st = window.getComputedStyle(el);
                        if (st.visibility === 'hidden' || st.display === 'none') return false;
                        return true;
                      }).slice(0, 160);
                      return nodes.map((el, i) => {
                        const tag = el.tagName.toLowerCase();
                        const id = el.id || '';
                        const name = el.getAttribute('name') || '';
                        const typ = (el.getAttribute('type') || '').toLowerCase();
                        const ph = el.getAttribute('placeholder') || '';
                        const al = el.getAttribute('aria-label') || '';
                        const rid = el.getAttribute('role') || '';
                        const txt = (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 72);
                        const href = (el.getAttribute('href') || '').slice(0, 120);
                        let css = '';
                        if (id && /^[\\w-]+$/.test(id)) css = '#' + id;
                        return { i, tag, id, name, typ, ph, al, rid, txt, href, css };
                      });
                    }"""
                )
            finally:
                browser.close()
    except Exception as e:
        return "", f"页面探测失败：{e}"

    if not isinstance(snapshot, list):
        return "", "页面探测未返回有效结构"

    summary_lines.append(f"页面标题: {title or '(无)'}")
    summary_lines.append(f"探测 URL: {url}")
    summary_lines.append("下列为页面内可见可交互元素（请优先使用 id/css 提示；无 id 时可结合 tag+placeholder+aria-label 构造稳定选择器）：")
    for row in snapshot[:max_lines]:
        if not isinstance(row, dict):
            continue
        parts = [
            f"[{row.get('i')}] <{row.get('tag')}>",
        ]
        if row.get("css"):
            parts.append(f"css={row.get('css')}")
        if row.get("id"):
            parts.append(f"id={row.get('id')}")
        if row.get("name"):
            parts.append(f"name={row.get('name')}")
        if row.get("typ"):
            parts.append(f"type={row.get('typ')}")
        if row.get("ph"):
            parts.append(f"placeholder={row.get('ph')}")
        if row.get("al"):
            parts.append(f"aria-label={row.get('al')}")
        if row.get("rid"):
            parts.append(f"role={row.get('rid')}")
        if row.get("txt"):
            parts.append(f"text={row.get('txt')}")
        if row.get("href"):
            parts.append(f"href={row.get('href')}")
        summary_lines.append(" | ".join(parts))

    text = "\n".join(summary_lines)
    if len(text) > max_chars:
        text = text[: max_chars - 80] + "\n…(摘要已截断，可在描述中指定更具体的页面区域)…"
    return text, err
