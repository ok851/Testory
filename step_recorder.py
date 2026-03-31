"""
步骤录制器 V2
独立、稳定的录制实现：基于 Playwright context binding 跨页面捕获事件。
"""
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
import asyncio


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

    def set_case_info(self, case_id: int, project_id: int):
        self.current_case_id = case_id
        self.project_id = project_id

    async def start(self, url: str, headless: bool = False):
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
            async def recorder_emit(payload):
                if not self.is_recording:
                    return
                if not isinstance(payload, dict):
                    return
                step = self._generate_step_sync(payload)
                if not step:
                    return
                self.recorded_steps.append(step)
                if self.websocket_callback:
                    await self.websocket_callback({
                        "type": "step_added",
                        "step": step,
                        "total_steps": len(self.recorded_steps)
                    })

            await self.context.expose_function("__stepRecorderEmit", recorder_emit)
            await self.context.add_init_script("""
            () => {
              if (window.__step_recorder_attached__) return;
              window.__step_recorder_attached__ = true;

              let lastInputTs = 0;
              let lastInputVal = '';

              function getXPath(element) {
                if (!element) return '';
                if (element.id) return `//*[@id="${element.id}"]`;
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
                  parts.unshift(index > 1 ? `${tagName}[${index}]` : tagName);
                  current = current.parentNode;
                }
                return '/' + parts.join('/');
              }

              function getCssSelector(element) {
                if (!element) return '';
                if (element.id) return `#${element.id}`;
                if (element.className && typeof element.className === 'string') {
                  const classes = element.className.split(' ').filter(Boolean);
                  if (classes.length) return `${element.tagName.toLowerCase()}.${classes.join('.')}`;
                }
                const parts = [];
                let current = element;
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                  if (current.id) {
                    parts.unshift(`#${current.id}`);
                    break;
                  }
                  let tagName = current.tagName.toLowerCase();
                  if (current.className && typeof current.className === 'string') {
                    const classes = current.className.split(' ').filter(Boolean);
                    if (classes.length) tagName += `.${classes.join('.')}`;
                  }
                  parts.unshift(tagName);
                  current = current.parentNode;
                }
                return parts.join(' > ');
              }

              function common(target) {
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

              function emit(payload) {
                if (typeof window.__stepRecorderEmit === 'function') {
                  window.__stepRecorderEmit(payload).catch(() => {});
                }
              }

              document.addEventListener('click', (event) => {
                emit({ type: 'click', ...common(event.target) });
              }, true);

              document.addEventListener('input', (event) => {
                const target = event.target;
                if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
                  const now = Date.now();
                  if (now - lastInputTs > 250 || target.value !== lastInputVal) {
                    lastInputTs = now;
                    lastInputVal = target.value;
                    emit({ type: 'input', ...common(target) });
                  }
                }
              }, true);

              document.addEventListener('change', (event) => {
                const target = event.target;
                if (target.tagName === 'SELECT') {
                  const selectedOption = target.options[target.selectedIndex];
                  emit({ type: 'select', ...common(target), text: selectedOption ? selectedOption.text : null });
                }
              }, true);

              document.addEventListener('keydown', (event) => {
                const specialKeys = ['Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
                if (specialKeys.includes(event.key)) {
                  emit({ type: 'keypress', key: event.key, ...common(event.target) });
                }
              }, true);
            }
            """)
            self._binding_ready = True

        self.context.on("page", lambda p: asyncio.create_task(self._prepare_page(p)))

    async def _prepare_page(self, page: Page):
        self.page = page
        page.on("domcontentloaded", lambda: asyncio.create_task(self._inject_listener(page)))
        await self._inject_listener(page)

    async def _inject_listener(self, page: Page):
        # 监听脚本已通过 add_init_script 注入；此处保持兼容空实现
        return

    async def stop(self):
        self.is_recording = False

        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        finally:
            self.context = None

        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        finally:
            self.browser = None

        try:
            if self.playwright:
                await self.playwright.stop()
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
        return None

    def _pick_locator(self, event_data: Dict) -> str:
        return (event_data.get("id") and f"#{event_data['id']}") or event_data.get("cssSelector") or event_data.get("xpath") or ""

    def _base_step(self, action: str, locator: str, input_value: str, description: str, timestamp: str) -> Dict:
        step_order = len(self.recorded_steps) + 1
        selector_type = "xpath" if str(locator).startswith("/") else "css"
        return {
            "action": action,
            "selector_type": selector_type,
            "selector_value": locator,
            "input_value": input_value if input_value is not None else "",
            "description": description,
            "step_order": step_order,
            "page_name": "",
            "swipe_x": "",
            "swipe_y": "",
            "url": "",
            "enter_iframe": False,
            "iframe_selector": "",
            "compare_type": "equals",
            "operation_type": action,
            "operation_locator": locator,
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
