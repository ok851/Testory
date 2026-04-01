"""
步骤录制器 V2
交互事件优先经 BrowserContext.expose_binding 回传（比 console 更可靠），console 作兜底。
导航由主帧 framenavigated 记录。
"""
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Frame
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote
import asyncio
import json
import time


# 页面内监听脚本：每次注入先 AbortController 卸载上一版监听，再绑定到当前 document/window；
# 避免「window.__step_recorder_attached__ 只设一次」导致换文档后无监听、只剩 navigate。
# 优先调用 window.uatRecordingEmit(JSON字符串)，失败再 console。
_STEP_RECORDER_JS = r"""
(() => {
  /* 不能用 window 级永久 flag：同一 tab 内文档替换后监听器会失效，再次 init 若直接 return 则新文档无任何监听，只剩 framenavigated。 */
  const prevAbort = window.__uatRecAttachAbort;
  if (prevAbort) {
    try { prevAbort.abort(); } catch (e) {}
  }
  const ac = new AbortController();
  window.__uatRecAttachAbort = ac;
  const sig = ac.signal;
  const cap = { capture: true, signal: sig };

  let lastInputTs = 0;
  let lastInputVal = '';
  let lastClickTs = 0;
  let lastClickKey = '';
  let hoverTimer = null;
  let scrollTimer = null;
  let lastScrollY = window.pageYOffset || document.documentElement.scrollTop || 0;
  let lastScrollX = window.pageXOffset || document.documentElement.scrollLeft || 0;

  sig.addEventListener('abort', () => {
    if (hoverTimer) clearTimeout(hoverTimer);
    if (scrollTimer) clearTimeout(scrollTimer);
  });

  function eventTarget(raw) {
    if (!raw) return null;
    if (raw.nodeType === 3) return raw.parentElement;
    return raw;
  }

  function deepestElement(event) {
    if (event && typeof event.composedPath === 'function') {
      const path = event.composedPath();
      for (let i = 0; i < path.length; i++) {
        const n = path[i];
        if (n && n.nodeType === 1 && n.tagName) return n;
      }
    }
    return eventTarget(event && event.target);
  }

  function clickKey(el) {
    if (!el || !el.tagName) return '';
    return (el.id ? '#' + el.id : '') || getCssSelector(el) || getXPath(el) || '';
  }

  function getXPath(element) {
    if (!element) return '';
    if (element.id) return '//*[@id="' + element.id + '"]';
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      let index = 1;
      let sibling = current.previousSibling;
      while (sibling) {
        if (sibling.nodeType === Node.ELEMENT_NODE && sibling.nodeName === current.nodeName) index++;
        sibling = sibling.previousSibling;
      }
      const tagName = current.nodeName.toLowerCase();
      parts.unshift(index > 1 ? tagName + '[' + index + ']' : tagName);
      current = current.parentNode;
    }
    return '/' + parts.join('/');
  }

  function getCssSelector(element) {
    if (!element) return '';
    if (element.id) return '#' + element.id;
    const testId = element.getAttribute && element.getAttribute('data-testid');
    if (testId) return '[data-testid="' + testId.replace(/"/g, '\\"') + '"]';
    if (element.className && typeof element.className === 'string') {
      const classes = element.className.split(' ').filter(Boolean);
      if (classes.length) return element.tagName.toLowerCase() + '.' + classes.join('.');
    }
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE) {
      if (current.id) {
        parts.unshift('#' + current.id);
        break;
      }
      let tagName = current.tagName.toLowerCase();
      if (current.className && typeof current.className === 'string') {
        const classes = current.className.split(' ').filter(Boolean);
        if (classes.length) tagName += '.' + classes.join('.');
      }
      parts.unshift(tagName);
      current = current.parentNode;
    }
    return parts.join(' > ');
  }

  function common(target) {
    if (!target || !target.tagName) {
      return {
        tagName: '',
        id: null,
        className: null,
        name: null,
        text: null,
        placeholder: null,
        href: null,
        value: null,
        xpath: '',
        cssSelector: '',
        timestamp: Date.now()
      };
    }
    return {
      tagName: (target.tagName || '').toLowerCase(),
      id: target.id || null,
      className: target.className || null,
      name: target.name || null,
      text: target.innerText ? target.innerText.slice(0, 50) : null,
      placeholder: target.placeholder || null,
      href: target.href || null,
      value: target.value || null,
      xpath: getXPath(target),
      cssSelector: getCssSelector(target),
      timestamp: Date.now()
    };
  }

  var __UAT_REC_PREFIX__ = '__UAT_REC_V1__:';

  function emit(payload) {
    var s;
    try {
      s = JSON.stringify(payload);
    } catch (e) {
      return;
    }
    function fallbackConsole() {
      try {
        console.log(__UAT_REC_PREFIX__ + s);
      } catch (e2) {}
    }
    var fn = window.uatRecordingEmit;
    if (typeof fn === 'function') {
      try {
        var ret = fn(s);
        if (ret != null && typeof ret.then === 'function') {
          ret.then(function () {}).catch(fallbackConsole);
        }
        return;
      } catch (err) {
        fallbackConsole();
        return;
      }
    }
    fallbackConsole();
  }

  /* pointerdown 略早于 click，表单跳转前更易送达后端 */
  window.addEventListener('pointerdown', (event) => {
    if (!event.isPrimary) return;
    const t = deepestElement(event);
    if (!t || !t.tagName) return;
    const tag = t.tagName.toUpperCase();
    const role = t.getAttribute && t.getAttribute('role');
    const interactive =
      tag === 'BUTTON' || tag === 'A' || tag === 'INPUT' || tag === 'TEXTAREA' ||
      tag === 'SELECT' || tag === 'LABEL' || t.isContentEditable ||
      role === 'button' || role === 'link' || role === 'searchbox' ||
      role === 'combobox' || role === 'textbox';
    if (!interactive) return;
    const now = Date.now();
    const key = clickKey(t);
    if (key && key === lastClickKey && (now - lastClickTs) < 400) return;
    lastClickTs = now;
    lastClickKey = key;
    emit({ type: 'click', ...common(t), _via: 'pointerdown' });
  }, cap);

  window.addEventListener('click', (event) => {
    const t = deepestElement(event);
    if (!t) return;
    const now = Date.now();
    const key = clickKey(t);
    if (key && key === lastClickKey && (now - lastClickTs) < 400) return;
    lastClickTs = now;
    lastClickKey = key;
    emit({ type: 'click', ...common(t) });
  }, cap);

  window.addEventListener('dblclick', (event) => {
    const t = deepestElement(event);
    if (!t) return;
    emit({ type: 'double_click', ...common(t) });
  }, cap);

  window.addEventListener('contextmenu', (event) => {
    const t = deepestElement(event);
    if (!t) return;
    emit({ type: 'right_click', ...common(t) });
  }, cap);

  function emitInputFromTarget(target, rawVal) {
    if (!target) return;
    const now = Date.now();
    const v = rawVal != null ? String(rawVal) : '';
    if ((now - lastInputTs > 120 || v !== lastInputVal) && v.length >= 0) {
      lastInputTs = now;
      lastInputVal = v;
      const merged = common(target);
      merged.value = v;
      emit({ type: 'input', ...merged });
    }
  }

  window.addEventListener('input', (event) => {
    const target = deepestElement(event);
    if (!target) return;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
      const rawVal = target.value != null ? String(target.value) : (target.isContentEditable ? (target.innerText || '') : '');
      const now = Date.now();
      if (now - lastInputTs > 120 || rawVal !== lastInputVal) {
        lastInputTs = now;
        lastInputVal = rawVal;
        var merged = common(target);
        merged.value = rawVal;
        emit({ type: 'input', ...merged });
      }
    }
  }, cap);

  window.addEventListener('compositionend', (event) => {
    const target = deepestElement(event);
    if (!target) return;
    if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
      const rawVal = target.value != null ? String(target.value) : (target.isContentEditable ? (target.innerText || '') : '');
      if (rawVal) emitInputFromTarget(target, rawVal);
    }
  }, cap);

  window.addEventListener('change', (event) => {
    const target = deepestElement(event);
    if (!target) return;
    if (target.tagName === 'SELECT') {
      const selectedOption = target.options[target.selectedIndex];
      emit({ type: 'select', ...common(target), text: selectedOption ? selectedOption.text : null });
    } else if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
      const rawVal = target.value != null ? String(target.value) : '';
      if (rawVal) emitInputFromTarget(target, rawVal);
    }
  }, cap);

  window.addEventListener('keydown', (event) => {
    const specialKeys = ['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
    if (specialKeys.includes(event.key)) {
      const t = deepestElement(event);
      emit({ type: 'keypress', key: event.key, ...common(t || event.target) });
    }
  }, cap);

  window.addEventListener('submit', (event) => {
    const form = event.target;
    if (!form || form.tagName !== 'FORM') return;
    try {
      const sel = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="reset"]), textarea';
      form.querySelectorAll(sel).forEach((el) => {
        const v = el.value != null ? String(el.value) : '';
        if (v) {
          const merged = common(el);
          merged.value = v;
          emit(Object.assign({ type: 'input', _source: 'submit_flush' }, merged));
        }
      });
    } catch (e) {}
    let sub = event.submitter || null;
    if (!sub) {
      const buttons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
      if (buttons.length) sub = buttons[0];
    }
    const t = sub && sub.tagName ? sub : form;
    emit({ type: 'submit', ...common(t) });
  }, cap);

  window.addEventListener('mouseover', (event) => {
    const raw = deepestElement(event);
    if (!raw || !raw.tagName) return;
    const interactiveTags = ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'OPTION', 'LABEL'];
    const role = raw.getAttribute && raw.getAttribute('role');
    const interactive = interactiveTags.includes(raw.tagName) ||
      role === 'button' || role === 'link' || role === 'option';
    if (!interactive) return;
    if (hoverTimer) clearTimeout(hoverTimer);
    const snap = raw;
    hoverTimer = setTimeout(() => {
      hoverTimer = null;
      emit({ type: 'hover', ...common(snap) });
    }, 450);
  }, cap);

  /* 导航步骤由 Playwright main_frame framenavigated 统一记录，避免与 JS 双报、URL 过长 */

  window.addEventListener('scroll', () => {
    if (scrollTimer) clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      const y = window.pageYOffset || document.documentElement.scrollTop || 0;
      const x = window.pageXOffset || document.documentElement.scrollLeft || 0;
      const dy = Math.abs(y - lastScrollY);
      const dx = Math.abs(x - lastScrollX);
      if (dx < 40 && dy < 40) return;
      lastScrollY = y;
      lastScrollX = x;
      emit({
        type: 'scroll',
        scrollX: x,
        scrollY: y,
        timestamp: Date.now()
      });
    }, 200);
  }, cap);
})();
"""


class StepRecorder:
    """步骤录制引擎类（V2）"""

    def __init__(self, websocket_callback: Optional[Callable] = None):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_recording = False
        self.recorded_steps: List[Dict[str, Any]] = []
        self.websocket_callback = websocket_callback
        self.current_case_id = None
        self.project_id = None
        self.saved_to_case = False
        self._binding_ready = False
        self._last_nav_url: str = ""
        self._last_nav_mono: float = 0.0
        self._listening_pages: set = set()

    def set_case_info(self, case_id: int, project_id: int):
        self.current_case_id = case_id
        self.project_id = project_id

    def _schedule_ws_step(self, step: Dict[str, Any]):
        if not self.websocket_callback:
            return

        async def _notify():
            await self.websocket_callback({
                "type": "step_added",
                "step": step,
                "total_steps": len(self.recorded_steps),
            })

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_notify())

    def _append_step(self, step: Optional[Dict[str, Any]]):
        if not step:
            return
        if (
            self.recorded_steps
            and step.get("action") == "navigate"
            and self.recorded_steps[-1].get("action") == "navigate"
            and (self.recorded_steps[-1].get("url") or self.recorded_steps[-1].get("input_value")) == (step.get("url") or step.get("input_value"))
        ):
            return
        if (
            self.recorded_steps
            and step.get("action") == "input"
            and self.recorded_steps[-1].get("action") == "input"
            and (self.recorded_steps[-1].get("selector_value") or "") == (step.get("selector_value") or "")
            and (self.recorded_steps[-1].get("input_value") or "") == (step.get("input_value") or "")
        ):
            return
        self.recorded_steps.append(step)
        self._schedule_ws_step(step)

    def _should_suppress_redundant_search_nav(self, url: str) -> bool:
        """已由「点击 + 输入」触发的搜索跳转，不再单独记 navigate，与平台操作语义一致。"""
        try:
            qs = parse_qs(urlparse(url).query)
            qval = None
            for k in ("wd", "word", "q", "query", "text", "keyword"):
                if k in qs and qs[k] and str(qs[k][0]).strip():
                    qval = unquote(str(qs[k][0]).strip())
                    break
            if not qval:
                return False
        except Exception:
            return False
        for step in reversed(self.recorded_steps[-15:]):
            if step.get("action") != "input":
                continue
            iv = (step.get("input_value") or "").strip()
            if not iv:
                continue
            if iv == qval:
                return True
        return False

    def _binding_dispatch(self, source, arg: Any):
        """Playwright expose_binding 回调：页面传入 uatRecordingEmit(JSON 字符串)。"""
        if not self.is_recording:
            return
        payload = arg
        if isinstance(arg, str):
            try:
                payload = json.loads(arg)
            except Exception:
                return
        if not isinstance(payload, dict):
            return
        step = self._generate_step_sync(payload)
        self._append_step(step)

    def _on_console_recording_message(self, msg):
        if not self.is_recording:
            return
        try:
            text = msg.text if hasattr(msg, "text") else str(msg)
        except Exception:
            return
        prefix = "__UAT_REC_V1__:"
        idx = text.find(prefix)
        if idx < 0:
            return
        raw = text[idx + len(prefix) :]
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        step = self._generate_step_sync(payload)
        self._append_step(step)

    async def start(self, url: str, headless: bool = False):
        self._binding_ready = False
        self._listening_pages = set()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--allow-running-insecure-content",
            ],
        )
        self.context = await self.browser.new_context(no_viewport=True)

        self.is_recording = True
        self.recorded_steps = []
        self.saved_to_case = False

        await self._install_context_hooks()
        self.page = await self.context.new_page()
        await self._prepare_page(self.page)
        await self.page.goto(url, wait_until="domcontentloaded")
        self.browser.on("disconnected", lambda: asyncio.create_task(self._on_browser_closed()))

    async def _on_browser_closed(self):
        self.is_recording = False

    async def _install_context_hooks(self):
        if not self.context:
            return

        if not self._binding_ready:
            try:

                def _on_uat_rec_bind(source, arg):
                    self._binding_dispatch(source, arg)

                await self.context.expose_binding("uatRecordingEmit", _on_uat_rec_bind)
            except Exception:
                pass
            await self.context.add_init_script(_STEP_RECORDER_JS)
            self._binding_ready = True

        self.context.on("page", lambda p: asyncio.create_task(self._prepare_page(p)))

    async def _prepare_page(self, page: Page):
        self.page = page
        pid = id(page)
        if pid not in self._listening_pages:
            self._listening_pages.add(pid)
            page.on(
                "framenavigated",
                lambda frame: asyncio.create_task(self._on_frame_navigated(page, frame)),
            )
            page.on("console", self._on_console_recording_message)
            page.on("domcontentloaded", lambda: asyncio.create_task(self._inject_listener(page)))
        await self._inject_listener(page)

    async def _on_frame_navigated(self, page: Page, frame: Frame):
        if not self.is_recording or frame != page.main_frame:
            return
        url = frame.url or ""
        if not url.startswith(("http://", "https://")):
            return
        now = time.monotonic()
        if url == self._last_nav_url and (now - self._last_nav_mono) < 1.2:
            return
        self._last_nav_url = url
        self._last_nav_mono = now
        if self._should_suppress_redundant_search_nav(url):
            return
        payload = {"type": "navigate", "url": url, "nav": "load", "timestamp": datetime.now().timestamp() * 1000}
        step = self._generate_step_sync(payload)
        self._append_step(step)

    async def _inject_listener(self, page: Page):
        if page.is_closed():
            return
        try:
            await page.add_init_script(_STEP_RECORDER_JS)
        except Exception:
            pass
        try:
            await page.evaluate(_STEP_RECORDER_JS)
        except Exception:
            pass

    async def stop(self):
        self.is_recording = False

        try:
            if self.context:
                await asyncio.wait_for(self.context.close(), timeout=1.5)
        except Exception:
            pass
        finally:
            self.context = None

        try:
            if self.browser:
                await asyncio.wait_for(self.browser.close(), timeout=1.5)
        except Exception:
            pass
        finally:
            self.browser = None

        try:
            if self.playwright:
                await asyncio.wait_for(self.playwright.stop(), timeout=1.5)
        except Exception:
            pass
        finally:
            self.playwright = None

        return self.recorded_steps

    def get_recorded_steps(self) -> List[Dict]:
        return self.recorded_steps.copy()

    async def navigate_to(self, url: str):
        if self.page:
            await self.page.goto(url, wait_until="domcontentloaded")

    def _generate_step_sync(self, event_data: Dict) -> Optional[Dict]:
        event_type = event_data.get("type")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if event_type == "click":
            return self._generate_click_step(event_data, timestamp)
        if event_type == "input":
            return self._generate_input_step(event_data, timestamp)
        if event_type == "select":
            return self._generate_select_step(event_data, timestamp)
        if event_type == "keypress":
            return self._generate_keypress_step(event_data, timestamp)
        if event_type == "navigate":
            return self._generate_navigate_step(event_data, timestamp)
        if event_type == "hover":
            return self._generate_hover_step(event_data, timestamp)
        if event_type == "double_click":
            return self._generate_double_click_step(event_data, timestamp)
        if event_type == "right_click":
            return self._generate_right_click_step(event_data, timestamp)
        if event_type == "scroll":
            return self._generate_scroll_step(event_data, timestamp)
        if event_type == "submit":
            return self._generate_submit_step(event_data, timestamp)
        return None

    def _pick_locator(self, event_data: Dict) -> str:
        return (event_data.get("id") and f"#{event_data['id']}") or event_data.get("cssSelector") or event_data.get("xpath") or ""

    def _base_step(self, action: str, locator: str, input_value: str, description: str, timestamp: str, url: str = "") -> Dict:
        step_order = len(self.recorded_steps) + 1
        loc = locator or ""
        selector_type = "xpath" if str(loc).startswith("/") else "css"
        return {
            "action": action,
            "selector_type": selector_type,
            "selector_value": loc,
            "input_value": input_value if input_value is not None else "",
            "description": description,
            "step_order": step_order,
            "page_name": "",
            "swipe_x": "",
            "swipe_y": "",
            "url": url or "",
            "enter_iframe": False,
            "iframe_selector": "",
            "compare_type": "equals",
            "operation_type": action,
            "operation_locator": loc,
            "operation_value": input_value if input_value is not None else "",
            "expected_result": "",
            "sort_order": step_order,
            "case_id": self.current_case_id,
            "project_id": self.project_id,
            "created_time": timestamp,
        }

    def _generate_click_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        text = event_data.get("text", "")
        return self._base_step("click", locator, "", f"点击元素：{text or locator}", timestamp)

    def _generate_input_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        value = event_data.get("value", "") or ""
        field = event_data.get("name", "") or event_data.get("placeholder", "") or locator
        brief = (value[:30] + "...") if len(value) > 30 else value
        return self._base_step("input", locator, value, f"在 {field} 中输入：{brief}", timestamp)

    def _generate_select_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        value = event_data.get("value", "") or ""
        text = event_data.get("text", "") or value
        return self._base_step("select", locator, value, f"从下拉框选择：{text}", timestamp)

    def _generate_keypress_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        key = event_data.get("key", "") or ""
        return self._base_step("keypress", locator, key, f"按下按键：{key}", timestamp)

    def _short_nav_label(self, raw_url: str) -> str:
        if not raw_url:
            return "打开页面"
        try:
            p = urlparse(raw_url)
            host = (p.netloc or "").replace("www.", "", 1)
            path = p.path or "/"
            qs = parse_qs(p.query)
            extra = ""
            if "wd" in qs and qs.get("wd"):
                q = unquote((qs["wd"][0] or "")[:200])
                if q:
                    extra = f" · 搜索「{q[:22]}{'…' if len(q) > 22 else ''}」"
            elif "word" in qs and qs.get("word"):
                q = unquote((qs["word"][0] or "")[:200])
                if q:
                    extra = f" · 「{q[:22]}{'…' if len(q) > 22 else ''}」"
            path_disp = path
            if len(path_disp) > 40:
                path_disp = path_disp[:16] + "…" + path_disp[-18:]
            core = f"{host}{path_disp}" if host else raw_url[:48]
            return f"打开 {core}{extra}".strip()
        except Exception:
            u = raw_url.replace("https://", "", 1).replace("http://", "", 1)
            return f"打开 {u[:56]}{'…' if len(u) > 56 else ''}"

    def _generate_navigate_step(self, event_data: Dict, timestamp: str) -> Dict:
        url = (event_data.get("url") or "").strip()
        nav = (event_data.get("nav") or "").strip()
        desc = self._short_nav_label(url)
        if nav and nav != "load":
            desc = f"{desc} ({nav})"
        return self._base_step("navigate", "", url, desc, timestamp, url=url)

    def _generate_hover_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        text = event_data.get("text", "") or ""
        return self._base_step("hover", locator, "", f"悬停：{text or locator}", timestamp)

    def _generate_double_click_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        text = event_data.get("text", "") or ""
        return self._base_step("double_click", locator, "", f"双击：{text or locator}", timestamp)

    def _generate_right_click_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        text = event_data.get("text", "") or ""
        return self._base_step("right_click", locator, "", f"右键点击：{text or locator}", timestamp)

    def _generate_scroll_step(self, event_data: Dict, timestamp: str) -> Dict:
        sy = int(event_data.get("scrollY") or 0)
        sx = int(event_data.get("scrollX") or 0)
        return self._base_step(
            "scroll",
            "",
            "500",
            f"页面滚动至约 ({sx}, {sy})（回放为向下滚动一次）",
            timestamp,
        )

    def _generate_submit_step(self, event_data: Dict, timestamp: str) -> Dict:
        locator = self._pick_locator(event_data)
        text = event_data.get("text", "") or ""
        return self._base_step("click", locator, "", f"提交表单（关联控件：{text or locator}）", timestamp)


_recorders: Dict[str, StepRecorder] = {}


def get_recorder(session_id: str) -> Optional[StepRecorder]:
    return _recorders.get(session_id)


def create_recorder(session_id: str, websocket_callback: Callable = None) -> StepRecorder:
    recorder = StepRecorder(websocket_callback)
    _recorders[session_id] = recorder
    return recorder


def remove_recorder(session_id: str):
    if session_id in _recorders:
        del _recorders[session_id]
