"""
智能步骤录制器 - Playwright 事件捕获引擎
支持实时捕获用户操作并自动生成测试步骤
"""
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
from typing import Dict, List, Optional, Callable
import asyncio
import json
from datetime import datetime
import re


class StepRecorder:
    """步骤录制引擎类"""
    
    def __init__(self, websocket_callback: Optional[Callable] = None):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_recording = False
        self.recorded_steps: List[Dict] = []
        self.websocket_callback = websocket_callback
        self.current_case_id = None
        self.project_id = None
        
    async def start(self, url: str, headless: bool = False):
        """启动浏览器并开始录制"""
        self.playwright = await async_playwright().start()
        
        # 启动浏览器，开启 CDP
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--allow-running-insecure-content'
            ]
        )
        
        # 创建上下文
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        # 创建页面
        self.page = await self.context.new_page()
        
        # 注入事件监听脚本
        await self._inject_event_listener()
        
        # 导航到目标页面
        await self.page.goto(url, wait_until='domcontentloaded')
        
        self.is_recording = True
        self.recorded_steps = []
        
        # 设置事件处理器
        await self._setup_event_handlers()
        
        # 监听浏览器关闭事件
        self.browser.on('disconnected', lambda: asyncio.create_task(self._on_browser_closed()))
        
    async def _inject_event_listener(self):
        """注入 JavaScript 事件监听器"""
        script = """
        () => {
            // 拦截所有点击事件
            document.addEventListener('click', async (event) => {
                event.preventDefault();
                event.stopPropagation();
                
                const target = event.target;
                const elementInfo = {
                    type: 'click',
                    tagName: target.tagName.toLowerCase(),
                    id: target.id || null,
                    className: target.className || null,
                    name: target.name || null,
                    text: target.innerText?.slice(0, 50) || null,
                    placeholder: target.placeholder || null,
                    href: target.href || null,
                    value: target.value || null,
                    xpath: getXPath(target),
                    cssSelector: getCssSelector(target),
                    timestamp: Date.now()
                };
                
                window.parent.postMessage({
                    type: 'recorder_event',
                    data: elementInfo
                }, '*');
                
                // 如果是链接，延迟跳转
                if (target.tagName === 'A' && target.href) {
                    setTimeout(() => {
                        window.location.href = target.href;
                    }, 500);
                }
            }, true);
            
            // 拦截输入事件
            document.addEventListener('input', async (event) => {
                const target = event.target;
                if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') {
                    const elementInfo = {
                        type: 'input',
                        tagName: target.tagName.toLowerCase(),
                        id: target.id || null,
                        className: target.className || null,
                        name: target.name || null,
                        value: target.value,
                        placeholder: target.placeholder || null,
                        xpath: getXPath(target),
                        cssSelector: getCssSelector(target),
                        timestamp: Date.now()
                    };
                    
                    window.parent.postMessage({
                        type: 'recorder_event',
                        data: elementInfo
                    }, '*');
                }
            }, true);
            
            // 拦截选择事件
            document.addEventListener('change', async (event) => {
                const target = event.target;
                if (target.tagName === 'SELECT') {
                    const selectedOption = target.options[target.selectedIndex];
                    const elementInfo = {
                        type: 'select',
                        tagName: target.tagName.toLowerCase(),
                        id: target.id || null,
                        className: target.className || null,
                        name: target.name || null,
                        value: target.value,
                        text: selectedOption?.text || null,
                        xpath: getXPath(target),
                        cssSelector: getCssSelector(target),
                        timestamp: Date.now()
                    };
                    
                    window.parent.postMessage({
                        type: 'recorder_event',
                        data: elementInfo
                    }, '*');
                }
            }, true);
            
            // 生成 XPath
            function getXPath(element) {
                if (!element) return null;
                if (element.id) return `//*[@id="${element.id}"]`;
                
                const parts = [];
                let current = element;
                
                while (current && current.nodeType === Node.ELEMENT_NODE) {
                    let index = 1;
                    let sibling = current.previousSibling;
                    
                    while (sibling) {
                        if (sibling.nodeType === Node.ELEMENT_NODE && 
                            sibling.nodeName === current.nodeName) {
                            index++;
                        }
                        sibling = sibling.previousSibling;
                    }
                    
                    const tagName = current.nodeName.toLowerCase();
                    const part = index > 1 ? `${tagName}[${index}]` : tagName;
                    parts.unshift(part);
                    
                    current = current.parentNode;
                }
                
                return '/' + parts.join('/');
            }
            
            // 生成 CSS 选择器
            function getCssSelector(element) {
                if (!element) return null;
                if (element.id) return `#${element.id}`;
                if (element.className && typeof element.className === 'string') {
                    const classes = element.className.split(' ').filter(c => c);
                    if (classes.length > 0) {
                        return `${element.tagName.toLowerCase()}.${classes.join('.')}`;
                    }
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
                        const classes = current.className.split(' ').filter(c => c);
                        if (classes.length > 0) {
                            tagName += `.${classes.join('.')}`;
                        }
                    }
                    
                    parts.unshift(tagName);
                    current = current.parentNode;
                }
                
                return parts.join(' > ');
            }
        }
        """
        await self.page.evaluate(script)
        
    async def _setup_event_handlers(self):
        """设置事件处理器"""
        # 处理页面消息
        self.page.on('console', lambda msg: print(f'Console: {msg.text}'))
    
    async def _on_browser_closed(self):
        """浏览器关闭时的处理"""
        if self.is_recording:
            print("浏览器已关闭，自动停止录制")
            self.is_recording = False
            # 清理资源
            await self.stop()
        
    def set_case_info(self, case_id: int, project_id: int):
        """设置用例和项目信息"""
        self.current_case_id = case_id
        self.project_id = project_id
        
    async def handle_event(self, event_data: Dict):
        """处理前端发送的事件"""
        if not self.is_recording:
            return
            
        step = await self._generate_step(event_data)
        if step:
            self.recorded_steps.append(step)
            
            # 通过 WebSocket 推送步骤
            if self.websocket_callback:
                await self.websocket_callback({
                    'type': 'step_added',
                    'step': step,
                    'total_steps': len(self.recorded_steps)
                })
    
    async def _generate_step(self, event_data: Dict) -> Optional[Dict]:
        """根据事件数据生成步骤"""
        event_type = event_data.get('type')
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if event_type == 'click':
            return self._generate_click_step(event_data, timestamp)
        elif event_type == 'input':
            return self._generate_input_step(event_data, timestamp)
        elif event_type == 'select':
            return self._generate_select_step(event_data, timestamp)
        
        return None
    
    def _generate_click_step(self, event_data: Dict, timestamp: str) -> Dict:
        """生成点击步骤"""
        tag_name = event_data.get('tagName', '')
        element_text = event_data.get('text', '')
        
        # 优先使用 ID，其次 CSS，最后 XPath
        locator = event_data.get('id') and f"#{event_data['id']}" or \
                  event_data.get('cssSelector') or \
                  event_data.get('xpath')
        
        # 判断操作类型
        if tag_name == 'a':
            action = 'click'
            description = f"点击链接：{element_text or locator}"
        elif tag_name == 'button' or tag_name == 'input':
            action = 'click'
            description = f"点击按钮：{element_text or locator}"
        else:
            action = 'click'
            description = f"点击元素：{locator}"
        
        return {
            'operation_type': action,
            'operation_locator': locator,
            'operation_value': '',
            'expected_result': '',
            'description': description,
            'sort_order': len(self.recorded_steps) + 1,
            'case_id': self.current_case_id,
            'project_id': self.project_id,
            'created_time': timestamp
        }
    
    def _generate_input_step(self, event_data: Dict, timestamp: str) -> Dict:
        """生成输入步骤"""
        locator = event_data.get('id') and f"#{event_data['id']}" or \
                  event_data.get('cssSelector') or \
                  event_data.get('xpath')
        
        input_value = event_data.get('value', '')
        placeholder = event_data.get('placeholder', '')
        field_name = event_data.get('name', '') or placeholder or locator
        
        return {
            'operation_type': 'input',
            'operation_locator': locator,
            'operation_value': input_value,
            'expected_result': '',
            'description': f"在 {field_name} 中输入：{input_value[:30]}{'...' if len(input_value) > 30 else ''}",
            'sort_order': len(self.recorded_steps) + 1,
            'case_id': self.current_case_id,
            'project_id': self.project_id,
            'created_time': timestamp
        }
    
    def _generate_select_step(self, event_data: Dict, timestamp: str) -> Dict:
        """生成选择步骤"""
        locator = event_data.get('id') and f"#{event_data['id']}" or \
                  event_data.get('cssSelector') or \
                  event_data.get('xpath')
        
        selected_text = event_data.get('text', '')
        selected_value = event_data.get('value', '')
        
        return {
            'operation_type': 'select',
            'operation_locator': locator,
            'operation_value': selected_value,
            'expected_result': '',
            'description': f"从下拉框选择：{selected_text}",
            'sort_order': len(self.recorded_steps) + 1,
            'case_id': self.current_case_id,
            'project_id': self.project_id,
            'created_time': timestamp
        }
    
    async def pause(self):
        """暂停录制"""
        self.is_recording = False
        
    async def resume(self):
        """恢复录制"""
        self.is_recording = True
        
    async def stop(self):
        """停止录制并关闭浏览器"""
        self.is_recording = False
        
        if self.browser:
            await self.browser.close()
            self.browser = None
            
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
            
        return self.recorded_steps
    
    def get_recorded_steps(self) -> List[Dict]:
        """获取已录制的步骤"""
        return self.recorded_steps.copy()
    
    async def take_screenshot(self) -> str:
        """截取当前屏幕"""
        if self.page:
            screenshot = await self.page.screenshot(encoding='base64')
            return screenshot
        return None
    
    async def navigate_to(self, url: str):
        """导航到新 URL"""
        if self.page:
            await self.page.goto(url, wait_until='domcontentloaded')


# 全局录制器实例
_recorders: Dict[str, StepRecorder] = {}


def get_recorder(session_id: str) -> Optional[StepRecorder]:
    """获取指定会话的录制器"""
    return _recorders.get(session_id)


def create_recorder(session_id: str, websocket_callback: Callable = None) -> StepRecorder:
    """创建新的录制器"""
    recorder = StepRecorder(websocket_callback)
    _recorders[session_id] = recorder
    return recorder


def remove_recorder(session_id: str):
    """移除录制器"""
    if session_id in _recorders:
        del _recorders[session_id]
