"""
无头抓取目标页可交互元素摘要，供本地 LLM 结合真实 DOM 生成/优化定位符。
依赖已安装的 Playwright 浏览器（与平台执行用例相同）。

增强：主文档 + iframe、Shadow DOM 浅层遍历、可配置等待与 settle；
     返回控件注册表用于 probe_index 映射与生成后选择器校验。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from locator_tier_utils import clamp01

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)

# 在单帧内收集可见可交互元素（含 open Shadow DOM 内节点）
_COLLECT_INTERACTIVE_JS = """
(maxNodes) => {
  const sel = [
    'a[href]','button','input:not([type=hidden])','textarea','select',
    '[role=button]','[role=link]','[role=textbox]','[role=searchbox]',
    '[role=combobox]','[contenteditable=true]'
  ].join(',');
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    return true;
  }
  function rowFor(el) {
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
    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '';
    return { tag, id, name, typ, ph, al, rid, txt, href, css, testid };
  }
  const rows = [];
  function addFrom(root) {
    if (rows.length >= maxNodes) return;
    let nodes = Array.from(root.querySelectorAll(sel)).filter(visible);
    for (const el of nodes) {
      if (rows.length >= maxNodes) return;
      rows.push(rowFor(el));
    }
    const hosts = root.querySelectorAll('*');
    for (const h of hosts) {
      if (rows.length >= maxNodes) return;
      if (h.shadowRoot) addFrom(h.shadowRoot);
    }
  }
  addFrom(document);
  return rows;
}
"""

# 仅主文档、不穿透 Shadow（略快；复杂页可改用带 Shadow 版本）
_COLLECT_INTERACTIVE_JS_FLAT = """
(maxNodes) => {
  const sel = [
    'a[href]','button','input:not([type=hidden])','textarea','select',
    '[role=button]','[role=link]','[role=textbox]','[role=searchbox]',
    '[role=combobox]','[contenteditable=true]'
  ].join(',');
  function visible(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return false;
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    return true;
  }
  function rowFor(el) {
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
    const testid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '';
    return { tag, id, name, typ, ph, al, rid, txt, href, css, testid };
  }
  const rows = [];
  const nodes = Array.from(document.querySelectorAll(sel)).filter(visible);
  for (const el of nodes) {
    if (rows.length >= maxNodes) break;
    rows.push(rowFor(el));
  }
  return rows;
}
"""


def extract_http_urls(text: str) -> List[str]:
    s = str(text or "").strip()
    if not s:
        return []
    found = _URL_RE.findall(s)
    return list(dict.fromkeys(found))


def pick_probe_url(
    goal: str,
    case_url: str = "",
    plan: Optional[Dict[str, Any]] = None,
    extra_hints: Optional[List[str]] = None,
) -> Optional[str]:
    """从顶栏/显式 URL、用户描述、或 plan（case_url / caseUrl / navigate 步）中选探测地址。"""
    ordered: List[str] = []
    if extra_hints:
        for h in extra_hints:
            if h:
                ordered.append(str(h).strip())
    if case_url:
        ordered.append(str(case_url).strip())
    for h in ordered:
        if h.startswith("http://") or h.startswith("https://"):
            return h.split()[0]

    blob = "\n".join([str(goal or "")] + ([str(h) for h in (extra_hints or []) if h]))
    for candidate in extract_http_urls(blob):
        if candidate.startswith("http://") or candidate.startswith("https://"):
            return candidate.rstrip(").,]}>'\"")

    if plan and isinstance(plan, dict):
        u2 = str(plan.get("case_url") or plan.get("caseUrl") or "").strip()
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


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if raw.isdigit():
        return int(raw)
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def build_locator_candidates_from_probe_entry(entry: Dict[str, Any]) -> str:
    """
    根据单次页面探测的一行，生成 locator_candidates JSON（与 playwright_automation._normalize_locator_candidate_list 兼容）。
    主选择器失败后按 score 降级尝试，对齐 Testim 类「多候选 fallback」思路。
    可通过 LOCAL_AI_PROBE_LOCATOR_CANDIDATES=0 关闭。
    """
    if (os.environ.get("LOCAL_AI_PROBE_LOCATOR_CANDIDATES", "1").strip().lower() in ("0", "false", "no")):
        return ""
    if not isinstance(entry, dict):
        return ""

    cands: List[Dict[str, Any]] = []
    seen = set()

    def add(st: str, sv: str, score: int) -> None:
        st = (st or "").strip().lower()
        sv = (sv or "").strip()
        if not sv or not st:
            return
        key = (st, sv)
        if key in seen:
            return
        seen.add(key)
        cands.append({"selector_type": st, "selector_value": sv, "score": score})

    # 推荐主路径（与 recommended_selector 一致）
    rec = _norm_probe_str(entry.get("recommended_selector"))
    rty = _norm_probe_str(entry.get("recommended_selector_type")).lower()
    if rec:
        if rty == "text":
            add("partial_text", rec, 100)
        elif rty in ("css", "xpath", "text"):
            add(rty if rty != "text" else "partial_text", rec, 100)
        else:
            add("css", rec, 100)

    eid = _norm_probe_str(entry.get("id"))
    if eid and re.match(r"^[\w-]+$", eid):
        add("id", eid, 98)
        add("css", f"#{eid}", 97)

    tid = _norm_probe_str(entry.get("testid"))
    if tid:
        safe = tid.replace("\\", "\\\\").replace('"', '\\"')
        add("css", f'[data-testid="{safe}"]', 96)

    name = _norm_probe_str(entry.get("name"))
    tag = (_norm_probe_str(entry.get("tag")) or "input").lower()
    if name and re.match(r"^[\w.\-]+$", name):
        add("css", f'{tag}[name="{name}"]', 93)

    ph = _norm_probe_str(entry.get("ph"))
    if ph and len(ph) <= 80 and "\n" not in ph:
        add("partial_text", ph, 90)
        if "'" not in ph:
            add("xpath", f"//*[@placeholder='{ph}']", 84)

    al = _norm_probe_str(entry.get("al"))
    if al and len(al) <= 80 and "\n" not in al:
        add("partial_text", al, 88)

    txt = _norm_probe_str(entry.get("txt"))
    if txt and 2 <= len(txt) <= 48 and "\n" not in txt and '"' not in txt and "'" not in txt:
        add("partial_text", txt, 82)
        add("xpath", f'//*[contains(normalize-space(.),"{txt[:40]}")]', 74)

    # Tier3：探测项含 box + viewport 时写入视口比例坐标（与 playwright 三层降级一致）
    box = entry.get("box")
    vp = entry.get("viewport") or {}
    if isinstance(box, dict) and isinstance(vp, dict):
        try:
            vw = int(vp.get("width") or 0)
            vh = int(vp.get("height") or 0)
            x = float(box.get("x", 0))
            y = float(box.get("y", 0))
            w = float(box.get("w", 0) or 0)
            h = float(box.get("h", 0) or 0)
            if vw > 0 and vh > 0 and w > 0 and h > 0:
                fx = clamp01((x + w / 2.0) / float(vw))
                fy = clamp01((y + h / 2.0) / float(vh))
                add(
                    "viewport_coord",
                    json.dumps({"fx": round(fx, 6), "fy": round(fy, 6)}, ensure_ascii=False),
                    30,
                )
        except (TypeError, ValueError):
            pass

    if not cands:
        return ""

    cands.sort(key=lambda x: -int(x.get("score") or 0))
    return json.dumps(cands, ensure_ascii=False)


def _norm_probe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _recommended_selector(row: Dict[str, Any]) -> Tuple[str, str]:
    """从探测行生成优先推荐的选择器与类型（供 probe_index 映射）。"""
    css = (row.get("css") or "").strip()
    if css:
        return css, "css"
    testid = (row.get("testid") or "").strip()
    if testid:
        safe = testid.replace("\\", "\\\\").replace('"', '\\"')
        return f'[data-testid="{safe}"]', "css"
    tag = (row.get("tag") or "div").strip() or "div"
    name = (row.get("name") or "").strip()
    if name and re.match(r"^[\w.\-]+$", name):
        return f'{tag}[name="{name}"]', "css"
    ph = (row.get("ph") or "").strip()
    if ph:
        return ph, "text"
    al = (row.get("al") or "").strip()
    if al:
        return al, "text"
    return "", ""


def _format_summary_lines(
    title: str,
    url: str,
    registry: List[Dict[str, Any]],
    max_lines: int,
    max_chars: int,
) -> str:
    summary_lines: List[str] = []
    summary_lines.append(f"页面标题: {title or '(无)'}")
    summary_lines.append(f"探测 URL: {url}")
    summary_lines.append(
        "下列为页面内可见可交互元素（含 iframe / Shadow 内）。"
        "每行 [n] 为 probe_index；生成步骤时应优先填 probe_index=n，且 selector_value 必须与该行 recommended=() 内字符串完全一致；"
        "禁止编造未出现在本列表中的 class 或 xpath。"
    )
    for row in registry[:max_lines]:
        parts = [
            f"[{row.get('i')}] frame={row.get('frame')!s} <{row.get('tag')}>",
        ]
        rec = row.get("recommended_selector") or ""
        rty = row.get("recommended_selector_type") or ""
        if rec:
            parts.append(f"recommended=({rty}){rec}")
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
    return text


def dom_context_pack_enabled() -> bool:
    return (os.environ.get("LOCAL_AI_DOM_PACK", "0").strip().lower() in ("1", "true", "yes", "on"))


def _dom_env_pack_chars() -> int:
    return _env_int("LOCAL_AI_DOM_PACK_MAX_CHARS", 12000)


def _dom_band_gap_px() -> int:
    return _env_int("LOCAL_AI_DOM_PACK_BAND_GAP", 56)


def format_a11y_snapshot_lines(root: Any, max_lines: int = 48) -> str:
    """
    将 Playwright accessibility.snapshot() 根节点压成缩进文本行（非完整树，节流行）。
    root 为 dict 或 None。
    """
    if not isinstance(root, dict):
        return ""
    lines: List[str] = []
    interesting = {
        "button",
        "link",
        "textbox",
        "searchbox",
        "combobox",
        "listbox",
        "checkbox",
        "radio",
        "heading",
        "navigation",
        "main",
        "form",
        "menubar",
        "menu",
        "menuitem",
        "tab",
        "tablist",
        "dialog",
        "alert",
    }
    skip_roles = {"generic", "none", "invisible", "statictext", "InlineTextBox"}

    def walk(node: Any, depth: int) -> None:
        if len(lines) >= max_lines or not isinstance(node, dict):
            return
        role = (node.get("role") or "").strip()
        name = re.sub(r"\s+", " ", (node.get("name") or "").strip())[:140]
        rl = role.lower()
        if rl in skip_roles and not name:
            for ch in (node.get("children") or [])[:40]:
                if len(lines) >= max_lines:
                    return
                walk(ch, depth)
            return
        if name or rl in interesting or (role and rl not in skip_roles):
            indent = "  " * min(depth, 5)
            line = f"{indent}{role or '?'}: {name}".strip() if name else f"{indent}{role or '?'}"
            if line.strip():
                lines.append(line[:220])
        for ch in (node.get("children") or [])[:35]:
            if len(lines) >= max_lines:
                return
            walk(ch, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


def dom_context_pack(
    snap: Dict[str, Any],
    a11y_outline: str = "",
) -> str:
    """
    第二路上下文：视口内控件按垂直区域分组 + 可选无障碍大纲；不替代 probe 行表，只辅助主模型分块理解页面。
    """
    if not dom_context_pack_enabled() or not isinstance(snap, dict):
        return ""
    maxc = _dom_env_pack_chars()
    title = _norm_probe_str(snap.get("title"))
    url = _norm_probe_str(snap.get("url"))
    vp = snap.get("viewport") or {}
    vpt = ""
    if isinstance(vp, dict) and (vp.get("width") or vp.get("height")):
        vpt = f"{vp.get('width', '')}x{vp.get('height', '')}"
    items_raw = [x for x in (snap.get("items") or []) if isinstance(x, dict)]
    # 带 box 的项用于分带；无 box 则退化为单列列表
    scored: List[Tuple[int, int, int, Dict[str, Any]]] = []
    for it in items_raw:
        box = it.get("box")
        y = 10**9
        x = 0
        nprobe = 0
        if isinstance(box, dict):
            try:
                y = int(box.get("y", 0))
            except (TypeError, ValueError):
                y = 0
            try:
                x = int(box.get("x", 0))
            except (TypeError, ValueError):
                x = 0
        try:
            nprobe = int(it.get("n") or 0)
        except (TypeError, ValueError):
            nprobe = 0
        scored.append((y, x, nprobe, it))
    scored.sort(key=lambda t: (t[0], t[1]))
    gap = _dom_band_gap_px()
    bands: List[List[Dict[str, Any]]] = []
    for _y, _x, _n, it in scored:
        if not bands:
            bands.append([it])
            continue
        last = bands[-1]
        prev_box = last[-1].get("box") if last else None
        cur_box = it.get("box")
        y_prev = 0
        y_cur = 0
        if isinstance(prev_box, dict) and isinstance(cur_box, dict):
            try:
                y_prev = int(prev_box.get("y", 0))
                y_cur = int(cur_box.get("y", 0))
            except (TypeError, ValueError):
                y_prev, y_cur = 0, 0
        if y_cur - y_prev > gap and last:
            bands.append([it])
        else:
            last.append(it)

    lines: List[str] = [
        "--- DOM context pack (grouped; probe_index in [n] matches the LIVE list above) ---",
        f"Title: {title or '(无)'} | URL: {url}" + (f" | viewport {vpt}" if vpt else ""),
    ]
    if (a11y_outline or "").strip() and (os.environ.get("LOCAL_AI_DOM_A11Y", "1").strip().lower() not in ("0", "false", "no")):
        lines.append("Accessibility outline (trimmed):")
        for ln in a11y_outline.strip().splitlines()[:60]:
            if ln.strip():
                lines.append("  " + ln.strip()[:200])
    lines.append("Controls by vertical region (y-order):")
    for bi, group in enumerate(bands[:32], start=1):
        if not group:
            continue
        y0 = None
        b0 = group[0].get("box")
        if isinstance(b0, dict):
            try:
                y0 = int(b0.get("y", 0))
            except (TypeError, ValueError):
                y0 = None
        y_label = f"y≈{y0}" if y0 is not None else f"band{bi}"
        lines.append(f"  [Region {bi} | {y_label}]")
        for it in group[:24]:
            try:
                idx = int(it.get("n") or 0)
            except (TypeError, ValueError):
                idx = 0
            tag = (it.get("tag") or "?").lower()
            tx = re.sub(r"\s+", " ", (it.get("text") or ""))[:64]
            sug = re.sub(r"\s+", " ", (it.get("suggestedSelector") or ""))[:100]
            bit = f"    [n={idx}] <{tag}>"
            if tx:
                bit += f" text={tx!r}"
            if sug:
                bit += f" try={sug!r}"
            lines.append(bit[:300])
    out = "\n".join(lines)
    if len(out) > maxc:
        out = out[: maxc - 80] + "\n…(DOM pack truncated)…"
    return out


def probe_registry_from_interactive_snapshot(snap: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], str]:
    """
    将 get_interactive_page_snapshot / 网关 inspect 返回的 data 转为
    （page_snapshot 文本, probe 注册表, 页面 URL）。

    注册表字段与 collect_page_controls / _probe_pick_selector / build_locator_candidates_from_probe_entry 兼容。
    """
    title = _norm_probe_str(snap.get("title"))
    url = _norm_probe_str(snap.get("url"))
    items = snap.get("items") or []
    max_lines = _env_int("LOCAL_AI_PROBE_MAX_LINES", 90)
    max_chars = _env_int("LOCAL_AI_PROBE_MAX_CHARS", 18000)
    registry: List[Dict[str, Any]] = []
    for it in items[:max_lines]:
        if not isinstance(it, dict):
            continue
        try:
            ii = int(it.get("n") or len(registry) + 1)
        except (TypeError, ValueError):
            ii = len(registry) + 1
        tag = _norm_probe_str(it.get("tag")) or "div"
        sel = _norm_probe_str(it.get("suggestedSelector"))
        rty = "css"
        if sel.startswith("//") or sel.lower().startswith("xpath:"):
            rty = "xpath"
            if sel.lower().startswith("xpath:"):
                sel = sel[6:].strip()
        row: Dict[str, Any] = {
            "i": ii,
            "frame": "main",
            "tag": tag,
            "typ": _norm_probe_str(it.get("type")).lower(),
            "txt": _norm_probe_str(it.get("text")),
            "al": _norm_probe_str(it.get("ariaLabel")),
            "ph": _norm_probe_str(it.get("placeholder")),
            "rid": _norm_probe_str(it.get("role")),
            "href": _norm_probe_str(it.get("href")),
            "id": _norm_probe_str(it.get("id")),
            "name": _norm_probe_str(it.get("name")),
            "testid": _norm_probe_str(it.get("dataTestid")),
            "recommended_selector": sel,
            "recommended_selector_type": rty,
        }
        vp = snap.get("viewport") if isinstance(snap.get("viewport"), dict) else {}
        if vp:
            try:
                row["viewport"] = {
                    "width": int(vp.get("width") or 0),
                    "height": int(vp.get("height") or 0),
                }
            except (TypeError, ValueError):
                pass
        bx = it.get("box")
        if isinstance(bx, dict):
            row["box"] = bx
        eid = row["id"]
        if eid and re.match(r"^[\w-]+$", eid):
            row["css"] = f"#{eid}"
        registry.append(row)
    text = _format_summary_lines(title, url, registry, max_lines, max_chars)
    return text, registry, url


def collect_page_controls(url: str) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    """
    打开 url（无头），抽取可见可交互控件，返回 (摘要文本, 错误信息, 注册表)。
    注册表每项含全局 i（probe_index）、frame、推荐选择器等。
    """
    timeout_ms = _env_int("LOCAL_AI_PROBE_TIMEOUT_MS", 35000)
    settle_ms = _env_int("LOCAL_AI_PROBE_SETTLE_MS", 800)
    max_nodes_total = _env_int("LOCAL_AI_PROBE_MAX_NODES", 220)
    max_lines = _env_int("LOCAL_AI_PROBE_MAX_LINES", 90)
    max_chars = _env_int("LOCAL_AI_PROBE_MAX_CHARS", 18000)
    main_cap = _env_int("LOCAL_AI_PROBE_MAIN_CAP", 140)
    frame_cap = _env_int("LOCAL_AI_PROBE_FRAME_CAP", 40)
    scan_iframes = (os.environ.get("LOCAL_AI_PROBE_IFRAMES", "1").strip().lower() not in ("0", "false", "no"))
    scan_shadow = (os.environ.get("LOCAL_AI_PROBE_SHADOW", "1").strip().lower() not in ("0", "false", "no"))

    goto_wait = (os.environ.get("LOCAL_AI_PROBE_GOTO_WAIT", "load") or "load").strip().lower()
    if goto_wait not in ("commit", "domcontentloaded", "load", "networkidle"):
        goto_wait = "load"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "", "未安装 playwright Python 包，无法探测页面", []

    registry: List[Dict[str, Any]] = []
    err: Optional[str] = None
    title = ""

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
                page.goto(url, wait_until=goto_wait, timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(12000, timeout_ms))
                except Exception:
                    pass
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)

                title = page.title() or ""

                frames = list(page.frames)
                global_i = 0

                for fi, frame in enumerate(frames):
                    if global_i >= max_nodes_total:
                        break
                    try:
                        if frame.is_detached():
                            continue
                    except Exception:
                        continue

                    cap = main_cap if fi == 0 else frame_cap
                    cap = min(cap, max_nodes_total - global_i)
                    if cap <= 0:
                        break

                    fu = ""
                    try:
                        fu = (frame.url or "")[:96]
                    except Exception:
                        fu = ""

                    if fi == 0:
                        frame_label = "main"
                    else:
                        frame_label = f"iframe[{fi}]"
                        if fu:
                            frame_label = f"{frame_label} url≈{fu}"

                    js = _COLLECT_INTERACTIVE_JS if scan_shadow else _COLLECT_INTERACTIVE_JS_FLAT

                    if not scan_iframes and fi > 0:
                        break

                    try:
                        rows = frame.evaluate(js, cap)
                    except Exception:
                        continue

                    if not isinstance(rows, list):
                        continue

                    for raw in rows:
                        if global_i >= max_nodes_total:
                            break
                        if not isinstance(raw, dict):
                            continue
                        rec, rty = _recommended_selector(raw)
                        entry = {
                            "i": global_i,
                            "frame": frame_label,
                            "frame_index": fi,
                            "tag": raw.get("tag") or "",
                            "id": raw.get("id") or "",
                            "name": raw.get("name") or "",
                            "typ": raw.get("typ") or "",
                            "ph": raw.get("ph") or "",
                            "al": raw.get("al") or "",
                            "rid": raw.get("rid") or "",
                            "txt": raw.get("txt") or "",
                            "href": raw.get("href") or "",
                            "css": raw.get("css") or "",
                            "testid": raw.get("testid") or "",
                            "recommended_selector": rec,
                            "recommended_selector_type": rty,
                        }
                        registry.append(entry)
                        global_i += 1

            finally:
                browser.close()
    except Exception as e:
        return "", f"页面探测失败：{e}", []

    text = _format_summary_lines(title, url, registry, max_lines, max_chars)
    return text, err, registry


def fetch_page_controls_bundle(url: str) -> Tuple[str, Optional[str], List[Dict[str, Any]]]:
    """返回 (摘要文本, 错误, 控件注册表)。无错误时第二项为 None。"""
    text, err, registry = collect_page_controls(url)
    return text, err, registry


def fetch_page_controls_summary(url: str) -> Tuple[str, Optional[str]]:
    """兼容旧接口：仅返回摘要与错误。"""
    text, err, _ = fetch_page_controls_bundle(url)
    return text, err


def _frame_locator(frame: Any, selector_type: str, selector_value: str) -> Optional[Any]:
    from playwright.sync_api import Frame

    if not isinstance(frame, Frame):
        return None
    st = (selector_type or "css").strip().lower()
    sv = (selector_value or "").strip()
    if not sv:
        return None
    try:
        if st == "xpath":
            xs = sv
            if not xs.lower().startswith("xpath="):
                xs = f"xpath={sv}"
            return frame.locator(xs)
        if st == "text":
            return frame.get_by_text(sv, exact=False)
        return frame.locator(sv)
    except Exception:
        return None


def validate_plan_locators(url: str, steps: List[Dict[str, Any]]) -> Tuple[List[str], Optional[str]]:
    """
    在无头会话中校验步骤中的选择器在各 frame 中的匹配数。
    返回 (警告列表, 致命错误)。iframe 内控件在主 frame 可能 0 匹配属正常，会提示若多 frame 命中则不稳定。
    """
    timeout_ms = _env_int("LOCAL_AI_PROBE_TIMEOUT_MS", 35000)
    settle_ms = _env_int("LOCAL_AI_PROBE_SETTLE_MS", 800)
    goto_wait = (os.environ.get("LOCAL_AI_PROBE_GOTO_WAIT", "load") or "load").strip().lower()
    if goto_wait not in ("commit", "domcontentloaded", "load", "networkidle"):
        goto_wait = "load"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "未安装 playwright，无法校验选择器"

    warnings: List[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(locale="zh-CN", viewport={"width": 1365, "height": 900})
                page = ctx.new_page()
                page.set_default_timeout(timeout_ms)
                page.goto(url, wait_until=goto_wait, timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(12000, timeout_ms))
                except Exception:
                    pass
                if settle_ms > 0:
                    page.wait_for_timeout(settle_ms)

                for idx, step in enumerate(steps, start=1):
                    if not isinstance(step, dict):
                        continue
                    action = str(step.get("action") or "").strip().lower()
                    if action in ("navigate", "wait", ""):
                        continue
                    ct_assert = str(step.get("compare_type") or "").strip().lower()
                    if action == "assert" and ct_assert in ("url_equals", "url_contains"):
                        continue
                    stype = str(step.get("selector_type") or "css").strip().lower()
                    sval = str(step.get("selector_value") or "").strip()
                    if not sval:
                        warnings.append(f"第{idx}步({action})缺少 selector_value，无法校验")
                        continue

                    total = 0
                    frame_hits: List[str] = []
                    for fi, frame in enumerate(page.frames):
                        try:
                            if frame.is_detached():
                                continue
                        except Exception:
                            continue
                        loc = _frame_locator(frame, stype, sval)
                        if loc is None:
                            continue
                        try:
                            c = loc.count()
                        except Exception:
                            c = 0
                        if c > 0:
                            total += c
                            fu = ""
                            try:
                                fu = (frame.url or "")[:64]
                            except Exception:
                                fu = ""
                            frame_hits.append(f"frame[{fi}]×{c}({fu})")

                    if total == 0:
                        warnings.append(
                            f"第{idx}步({action})选择器无匹配: {stype}={sval[:100]}"
                            "（若在 iframe 内且主文档无匹配，可忽略；否则请改 selector）"
                        )
                    elif total > 1:
                        warnings.append(
                            f"第{idx}步({action})选择器共匹配 {total} 处，可能不稳定: {stype}={sval[:80]} "
                            f"详情: {', '.join(frame_hits[:4])}"
                        )
            finally:
                browser.close()
    except Exception as e:
        return [], f"校验过程异常：{e}"

    return warnings, None
