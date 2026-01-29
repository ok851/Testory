import asyncio
from playwright.async_api import async_playwright
from typing import List, Dict, Any, Optional
import json
import time
from logger import uat_logger
import ctypes  # 用于调用Windows API获取真实屏幕尺寸

class PlaywrightAutomation:
    def __init__(self):
        self.browser = None
        self.page = None
        self.context = None
        self.recording = False
        self.recorded_steps = []
        self.current_url = ""
        self.page_events = []  # 存储页面事件以便后续处理
        self.sync_task = None  # 用于同步录制事件的后台任务
        self.playwright = None  # 初始化playwright实例变量
    
    async def start_browser(self, headless=False):
        """启动浏览器"""
        try:
            # 确保浏览器相关对象都已正确重置
            if self.browser is None or not self.browser.is_connected():
                uat_logger.info(f"启动浏览器,headless={headless}")
                
                # 1. 确保playwright实例已正确关闭和重置
                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass
                    self.playwright = None
                
                # 2. 使用Windows API直接获取真实的屏幕尺寸(不依赖浏览器)
                self.playwright = await async_playwright().start()
                
                # 调用Windows API获取真实屏幕尺寸
                user32 = ctypes.windll.user32
                # 获取主显示器的屏幕尺寸
                screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
                
                # 获取可用工作区尺寸(减去任务栏等)
                avail_width = user32.GetSystemMetrics(78)  # SM_CXAVAILABLE
                avail_height = user32.GetSystemMetrics(79)  # SM_CYAVAILABLE
                
                screen_size = {"width": screen_width, "height": screen_height}
                avail_screen_size = {"width": avail_width, "height": avail_height}
                
                uat_logger.info(f"Windows API获取的屏幕尺寸: {screen_size['width']}x{screen_size['height']}")
                uat_logger.info(f"Windows API获取的可用工作区尺寸: {avail_screen_size['width']}x{avail_screen_size['height']}")
                
                # 2. 使用获取到的可用工作区尺寸启动真正的浏览器实例
                # 使用可用工作区尺寸可以避免与任务栏等系统UI冲突
                args = [
                    '--start-maximized',  # 真正的浏览器最大化
                    '--no-default-browser-check',
                    '--no-first-run'
                ]
                
                self.browser = await self.playwright.chromium.launch(
                    headless=headless,
                    args=args
                )
                
                # 创建上下文时不强制设置viewport大小,让浏览器自动适应窗口尺寸
                # 这样可以确保页面渲染和滚动行为与普通浏览器一致
                self.context = await self.browser.new_context(
                    ignore_https_errors=True,
                    no_viewport=True  # 让浏览器自动管理视口大小
                )
                
                # 创建新页面
                self.page = await self.context.new_page()
                
                # 使用Windows API获取真实屏幕尺寸
                user32 = ctypes.windll.user32
                screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
                screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
                
                # 设置浏览器窗口大小为真实屏幕尺寸
                uat_logger.info(f"将浏览器窗口设置为真实屏幕尺寸: {screen_width}x{screen_height}")
                await self.page.evaluate(f"window.resizeTo({screen_width}, {screen_height})")
                await self.page.evaluate("window.moveTo(0, 0)")
                
                # 直接获取浏览器窗口的实际尺寸
                viewport_size = await self.page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
                outer_size = await self.page.evaluate("() => ({ width: window.outerWidth, height: window.outerHeight })")
                screen_size = await self.page.evaluate("() => ({ width: screen.width, height: screen.height })")
                avail_screen_size = await self.page.evaluate("() => ({ width: screen.availWidth, height: screen.availHeight })")
                
                uat_logger.info(f"屏幕总尺寸: {screen_size['width']}x{screen_size['height']}")
                uat_logger.info(f"屏幕可用尺寸: {avail_screen_size['width']}x{avail_screen_size['height']}")
                uat_logger.info(f"浏览器窗口内尺寸: {viewport_size['width']}x{viewport_size['height']}")
                uat_logger.info(f"浏览器窗口外尺寸: {outer_size['width']}x{outer_size['height']}")
                uat_logger.info(f"浏览器已设置为全屏模式,右上角最大化按钮应不可见")
                
                uat_logger.info("浏览器已启动并最大化,设置事件监听器")
                
                # 设置事件监听器用于录制用户操作
                await self._setup_event_listeners()
                
                # 监听页面跳转事件,确保在新页面上也设置事件监听器
                self.page.on('framenavigated', self._on_page_navigated)
            
            return self.page
        except Exception as e:
            uat_logger.log_exception("start_browser", e)
            raise Exception(f"启动浏览器失败: {str(e)}")
    
    async def _setup_event_listeners(self):
        """设置页面事件监听器用于录制操作"""
        if self.page:
            # 定义事件监听器JavaScript代码
            event_listeners_js = r"""
                // 检查是否已经添加了事件监听器,避免重复添加
                if (!window.eventListenersAdded) {
                    // 初始化事件数组
                    window.automationEvents = window.automationEvents || [];
                    window.automationConfig = {
                        scrollTimeout: null,
                        lastScrollPosition: { x: 0, y: 0 },
                        scrollThreshold: 50, // 只有滚动超过50px才记录
                        inputDebounce: {},
                        debounceDelay: 500
                    };
                
                // 生成更精确的CSS选择器
                // 递归辅助函数:生成元素的完整路径选择器
                function generateFullPath(element, maxDepth = 4, currentDepth = 0) {
                    if (!element || element.tagName === 'HTML' || currentDepth >= maxDepth) {
                        return [];
                    }
                    
                    let elementSelector = '';
                    const tagName = element.tagName.toLowerCase();
                    
                    // 优先使用ID
                    if (element.id) {
                        return [`#${element.id}`];
                    }
                    
                    // 优先使用稳定属性 - 扩展更多自动化测试常用属性
                    const stableAttrs = [
                        'data-testid', 'data-cy', 'data-test', 'data-qa', 
                        'data-automation', 'data-selector', 'data-key', 
                        'data-id', 'data-name', 'data-component', 
                        'data-module', 'data-section', 'data-field',
                        'data-action', 'data-target', 'data-type',
                        'name', 'title', 'role', 'aria-label', 
                        'aria-labelledby', 'aria-describedby', 'aria-controls'
                    ];
                    let hasStableAttr = false;
                        for (const attr of stableAttrs) {
                            const value = element.getAttribute(attr);
                            if (value && value.length > 0) {
                                // 支持包含空格的值,使用转义双引号
                                const safeValue = value.replace(/"/g, '&quot;');
                                elementSelector = `${tagName}[${attr}="${safeValue}"]`;
                                hasStableAttr = true;
                                break;
                            }
                        }
                    
                    if (!hasStableAttr) {
                        elementSelector = tagName;
                        // 处理类名,过滤掉动态类名
                        if (element.className) {
                            const allClasses = element.className.split(' ').filter(c => c.length > 2); // 过滤掉太短的类名
                            const dynamicClassPatterns = [
                                /^is-\w+$/, /^has-\w+$/, /^\w+-\w+-(leave|enter|active|done)$/, 
                                /^el-\w+(-\w+)*$/, /^ant-\w+(-\w+)*$/, /^t-[a-zA-Z0-9]{8}$/, 
                                /^weui-\w+(-\w+)*$/, /^layui-\w+(-\w+)*$/, /^v-\w+$/, 
                                /^ng-\w+$/, /^vue-\w+$/, /^react-\w+$/, /^svelte-\w+$/, 
                                /^css-\w+$/, /^scss-\w+$/, /^style-\w+$/, /^component-\w+$/, 
                                /^theme-\w+$/, /^mode-\w+$/, /^state-\w+$/, /^variant-\w+$/, 
                                /^hover-\w+$/, /^focus-\w+$/, /^active-\w+$/, /^disabled-\w+$/, 
                                /^selected-\w+$/, /^checked-\w+$/, /^expanded-\w+$/, /^collapsed-\w+$/, /^open-\w+$/, /^closed-\w+$/, 
                                /^loading-\w+$/, /^error-\w+$/, /^success-\w+$/, /^warning-\w+$/, /^info-\w+$/, 
                                /^\d+-\w+$/, /^\w+-\d+$/, /^[a-f0-9]{6,}$/, /^\w+-[a-f0-9]{6,16}$/, 
                                /^\w+-[0-9a-z]{8,}$/, /^[a-z]{3,6}-[0-9a-z]{5,10}$/, 
                                /^v?\d+\.\d+\.\d+$/, /^[0-9]+$/, 
                                /^flex$/, /^grid$/, /^block$/, /^inline$/, /^hidden$/, /^visible$/, 
                                /^absolute$/, /^relative$/, /^fixed$/, /^sticky$/, 
                                /^left-\d+$/, /^right-\d+$/, /^top-\d+$/, /^bottom-\d+$/, 
                                /^w-\d+$/, /^h-\d+$/, /^max-w-\d+$/, /^max-h-\d+$/, 
                                /^p-\d+$/, /^m-\d+$/, /^mt-\d+$/, /^mr-\d+$/, /^mb-\d+$/, /^ml-\d+$/, 
                                /^pt-\d+$/, /^pr-\d+$/, /^pb-\d+$/, /^pl-\d+$/, 
                                /^text-\w+$/, /^bg-\w+$/, /^border-\w+$/, /^rounded-\w+$/, 
                                /^lang-\w+$/, /^i18n-\w+$/, /^ltr$/, /^rtl$/, 
                                /^mobile-\w+$/, /^tablet-\w+$/, /^desktop-\w+$/, /^xl-\w+$/, /^sm-\w+$/, /^md-\w+$/, /^lg-\w+$/
                            ];
                            
                            const stableClasses = allClasses.filter(c => {
                                    // 过滤掉动态类名
                                    const isDynamic = dynamicClassPatterns.some(p => p.test(c));
                                    // 过滤掉只有数字或特殊字符的类名
                                    const isInvalid = /^[0-9_\-\.\s]+$/.test(c);
                                    // 过滤掉太短的类名(可能是动态生成的)
                                    const isTooShort = c.length < 3;
                                    return !isDynamic && !isInvalid && !isTooShort;
                                });
                            if (stableClasses.length) {
                                elementSelector += '.' + stableClasses.slice(0, 3).join('.');
                            }
                        }
                    }
                    
                    // 元素类型特定属性处理,增强对动态表单元素的支持
                        if (tagName === 'input') {
                            // 对于表单输入元素,添加更多识别属性
                            const type = element.type;
                            elementSelector += `[type="${type}"]`;
                            
                            // 优化表单元素识别顺序,优先使用更多稳定属性
                            if (element.name && element.name.length > 0) {
                                elementSelector += `[name="${element.name}"]`;
                            } else if (element.placeholder && element.placeholder.length > 0) {
                                elementSelector += `[placeholder="${element.placeholder}"]`;
                            } else if (element.value && element.value.length > 0 && !element.value.match(/^[0-9]+$/)) {
                                // 仅对非数字的静态值使用value属性
                                elementSelector += `[value="${element.value}"]`;
                            } else if (element.getAttribute('aria-label')) {
                                elementSelector += `[aria-label="${element.getAttribute('aria-label')}"]`;
                            }
                        } else if (tagName === 'textarea' || tagName === 'select') {
                            // 对于其他表单元素,增强识别能力
                            if (element.name && element.name.length > 0) {
                                elementSelector += `[name="${element.name}"]`;
                            } else if (element.placeholder && element.placeholder.length > 0) {
                                elementSelector += `[placeholder="${element.placeholder}"]`;
                            } else if (element.title && element.title.length > 0) {
                                elementSelector += `[title="${element.title}"]`;
                            } else if (element.getAttribute('aria-label')) {
                                elementSelector += `[aria-label="${element.getAttribute('aria-label')}"]`;
                            }
                        } else if (tagName === 'button') {
                            // 增强按钮元素的识别,优化动态按钮处理
                            if (element.textContent && element.textContent.trim().length > 0) {
                                const text = element.textContent.trim().substring(0, 25).replace(/"/g, '&quot;');
                                elementSelector += `:contains("${text}")`;
                            } else if (element.getAttribute('aria-label')) {
                                elementSelector += `[aria-label="${element.getAttribute('aria-label')}"]`;
                            } else if (element.type) {
                                elementSelector += `[type="${element.type}"]`;
                            }
                        } else if (tagName === 'img') {
                            // 对于图片,使用更精确的定位
                            if (element.alt && element.alt.length > 0) {
                                elementSelector += `[alt="${element.alt}"]`;
                            } else if (element.src && element.src.length > 0) {
                            // 对于图片,使用部分src路径
                            const srcParts = element.src.split('/');
                            const filename = srcParts[srcParts.length - 1];
                            elementSelector += `[src*="${filename}"]`;
                        }
                    } else if (tagName === 'a') {
                        // 对于链接,使用href属性
                        if (element.href && element.href.length > 0) {
                            const url = element.href;
                            // 只使用相对路径或域名后的路径
                            const path = url.replace(/^https?:\/\//, '').split('/').slice(1).join('/');
                            if (path.length > 0) {
                                elementSelector += `[href*="${path}"]`;
                            }
                        }
                    } else if (tagName === 'button') {
                        // 对于按钮,添加更多识别属性
                        if (element.textContent && element.textContent.trim().length > 0) {
                            // 只在没有其他更好的属性时使用文本内容
                            const text = element.textContent.trim();
                            if (text.length < 20 && !elementSelector.includes(':has-text(')) {
                                elementSelector += `:has-text("${text}")`;
                            }
                        } else if (element.title && element.title.length > 0 && !elementSelector.includes('[title=')) {
                            elementSelector += `[title="${element.title}"]`;
                        }
                    }
                    
                    // 添加更多稳定属性作为补充
                    const additionalAttrs = ['data-name', 'role', 'aria-label', 'aria-labelledby'];
                    for (const attr of additionalAttrs) {
                        const value = element.getAttribute(attr);
                        if (value && value.length > 0 && !value.includes(' ') && !elementSelector.includes(`[${attr}=`)) {
                            elementSelector += `[${attr}="${value}"]`;
                        }
                    }
                    
                    // 添加更多稳定属性
                    if (element.title && element.title.length > 0 && !elementSelector.includes('[title=')) {
                        elementSelector += `[title="${element.title}"]`;
                    }
                    
                    const parentPath = generateFullPath(element.parentElement, maxDepth, currentDepth + 1);
                    return [...parentPath, elementSelector];
                }
                
                // 生成完整的CSS选择器
                function generateSelector(element) {
                    if (!element) return '';
                    
                    // 生成完整路径
                    let path = generateFullPath(element);
                    
                    // 如果路径为空,直接返回标签名
                    if (path.length === 0) {
                        return element.tagName.toLowerCase();
                    }
                    
                    // 将路径数组转换为完整的CSS选择器
                    let fullSelector = path.join(' > ');
                    
                    // 检查选择器的唯一性
                    try {
                        const matches = document.querySelectorAll(fullSelector);
                        if (matches.length > 1) {
                            // 如果选择器不唯一,添加nth-of-type作为兜底
                            let uniquePath = [...path];
                            let index = 1;
                            let parent = element.parentElement;
                            
                            // 查找当前元素在父元素中的位置
                            if (parent) {
                                const siblings = Array.from(parent.children).filter(child => child.tagName === element.tagName);
                                index = siblings.indexOf(element) + 1;
                                
                                // 如果有多个同类型的兄弟元素,添加nth-of-type
                                if (siblings.length > 1) {
                                    const lastSelector = uniquePath.pop();
                                    const tagName = element.tagName.toLowerCase();
                                    // 确保我们只给基础标签添加nth-of-type
                                    if (lastSelector.startsWith(tagName + '[') || lastSelector === tagName) {
                                        uniquePath.push(`${lastSelector}:nth-of-type(${index})`);
                                    } else {
                                        uniquePath.push(lastSelector);
                                    }
                                    fullSelector = uniquePath.join(' > ');
                                }
                            }
                        }
                    } catch (e) {
                        // 如果查询失败,使用原始选择器
                        console.error('选择器验证失败:', e);
                    }
                    
                    return fullSelector;
                }
                
                // 点击事件监听 - 使用冒泡阶段避免重复事件
                if (document && document.addEventListener) {
                    document.addEventListener('click', function(e) {
                        const target = e.target;
                        let actualTarget = target;
                        
                        // 处理复合组件(如复选框/单选框):统一使用最外层可交互元素作为目标
                        function findCompositeComponentRoot(element, componentTypes) {
                            let current = element;
                            while (current && current.tagName !== 'BODY' && current.tagName !== 'HTML') {
                                // 检查当前元素是否包含组件类型关键字
                                const hasComponentType = componentTypes.some(type => 
                                    current.className && current.className.includes(type)
                                );
                                
                                // 检查当前元素是否是label标签
                                const isLabel = current.tagName === 'LABEL';
                                
                                // 检查当前元素是否包含目标input类型
                                const hasTargetInput = componentTypes.some(type => 
                                    current.querySelector(`input[type="${type}"]`)
                                );
                                
                                if (hasComponentType || isLabel || hasTargetInput) {
                                    return current;
                                }
                                
                                current = current.parentElement;
                            }
                            return null;
                        }
                        
                        // 处理input类型的复合组件
                        if (target.tagName === 'INPUT') {
                            if (target.type === 'checkbox' || target.type === 'radio') {
                                // 查找复合组件的根元素
                                const rootElement = findCompositeComponentRoot(target, [target.type]);
                                if (rootElement) {
                                    actualTarget = rootElement;
                                }
                            }
                        }
                        // 处理非input类型的复合组件点击
                        else {
                            // 检查是否点击了复选框或单选框的关联元素
                            const checkbox = target.querySelector('input[type="checkbox"]');
                            const radio = target.querySelector('input[type="radio"]');
                            
                            if (checkbox || radio) {
                                // 当前元素包含input,使用当前元素作为目标
                                actualTarget = target;
                            } else {
                                // 检查父元素是否包含checkbox或radio
                                const hasCheckbox = target.closest('[class*="checkbox"]');
                                const hasRadio = target.closest('[class*="radio"]');
                                
                                if (hasCheckbox || hasRadio) {
                                    // 使用closest方法找到最近的包含checkbox或radio类名的元素
                                    actualTarget = hasCheckbox || hasRadio;
                                } else {
                                    // 查找包含checkbox或radio input的父元素
                                    const parentCheckbox = target.closest(':has(input[type="checkbox"])');
                                    const parentRadio = target.closest(':has(input[type="radio"])');
                                    
                                    if (parentCheckbox || parentRadio) {
                                        actualTarget = parentCheckbox || parentRadio;
                                    }
                                }
                            }
                        }
                        
                        const selector = generateSelector(actualTarget);
                        
                        // 记录详细的元素信息
                        const elementInfo = {
                            tagName: actualTarget.tagName,
                            id: actualTarget.id || '',
                            className: actualTarget.className || '',
                            textContent: actualTarget.textContent ? actualTarget.textContent.substring(0, 50) : '',
                            attributes: {}
                        };
                        
                        // 收集重要属性
                        ['name', 'type', 'placeholder', 'value', 'href', 'title', 'alt', 'role', 'data-testid', 'data-cy'].forEach(attr => {
                            if (actualTarget[attr]) {
                                elementInfo.attributes[attr] = actualTarget[attr];
                            }
                        });
                        
                        if (window && window.automationEvents) {
                            window.automationEvents.push({
                                action: 'click',
                                selector: selector,
                                timestamp: Date.now(),
                                elementInfo: elementInfo
                            });
                            
                            // 检查是否点击了提交按钮,如果是则记录submit事件
                            const isSubmitButton = actualTarget.tagName === 'BUTTON' || 
                                                  (actualTarget.tagName === 'INPUT' && (actualTarget.type === 'submit' || actualTarget.type === 'button'));
                            const hasSubmitClass = actualTarget.className && 
                                                  (actualTarget.className.includes('submit') || 
                                                   actualTarget.className.includes('primary') || 
                                                   actualTarget.className.includes('login'));
                            
                            if (isSubmitButton || hasSubmitClass) {
                                // 查找关联的表单
                                const form = actualTarget.closest('form');
                                if (form) {
                                    const formSelector = generateSelector(form);
                                    // 记录submit事件,选择器是提交按钮的选择器,而不是表单的选择器
                                    // 这样在回放时可以直接点击提交按钮来触发表单提交
                                    window.automationEvents.push({
                                        action: 'submit',
                                        selector: selector,
                                        timestamp: Date.now(),
                                        elementInfo: {
                                            tagName: actualTarget.tagName,
                                            id: actualTarget.id || '',
                                            className: actualTarget.className || '',
                                            type: actualTarget.type || '',
                                            formSelector: formSelector,
                                            formAction: form.action || ''
                                        }
                                    });
                                }
                            }
                        }
                    }, false); // 使用冒泡阶段,避免重复捕获
                }
                
                // 输入事件监听 - 带防抖以避免过于频繁的事件
                if (document && document.addEventListener && window && window.automationConfig) {
                    document.addEventListener('input', function(e) {
                        const target = e.target;
                        
                        // 精确检查元素类型,只处理真正可输入的文本元素
                        const isTextInput = (
                            (target.tagName === 'INPUT' && 
                             ['text', 'email', 'password', 'number', 'search', 'url', 'tel'].includes(target.type)) ||
                            target.tagName === 'TEXTAREA' ||
                            (target.tagName === 'INPUT' && !target.type) || // 没有type属性默认为text
                            target.isContentEditable
                        );
                        
                        // 显式排除所有非文本输入类型
                        const isExcludedType = (
                            target.tagName === 'INPUT' && 
                            ['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'image', 'hidden'].includes(target.type)
                        );
                        
                        if (!isTextInput || isExcludedType) {
                            return; // 忽略非文本输入事件
                        }
                        
                        // 只处理文本输入类型
                        const selector = generateSelector(target);
                        const elementId = selector + '_' + target.tagName; // 创建唯一ID用于防抖
                        
                        // 清除之前的防抖定时器
                        if (window.automationConfig.inputDebounce[elementId]) {
                            clearTimeout(window.automationConfig.inputDebounce[elementId]);
                        }
                        
                        // 设置新的防抖定时器
                        window.automationConfig.inputDebounce[elementId] = setTimeout(() => {
                            if (window && window.automationEvents) {
                                window.automationEvents.push({
                                    action: 'fill',
                                    selector: selector,
                                    text: target.value,
                                    timestamp: Date.now(),
                                    elementInfo: {
                                        tagName: target.tagName,
                                        id: target.id || '',
                                        className: target.className || '',
                                        name: target.name || '',
                                        type: target.type || ''
                                    }
                                });
                            }
                            delete window.automationConfig.inputDebounce[elementId];
                        }, window.automationConfig.debounceDelay);
                    }, true);
                }
                
                // 表单提交事件
                if (document && document.addEventListener) {
                    document.addEventListener('submit', function(e) {
                        const target = e.target;
                        if (target.tagName === 'FORM') {
                            // 不要阻止默认的表单提交行为,让表单能够正常提交
                            // e.preventDefault();  // 移除此行,避免阻止表单提交
                            
                            // 找到触发表单提交的提交按钮
                            let submitButton = null;
                            if (e.submitter) {
                                // 如果浏览器支持e.submitter属性,直接使用
                                submitButton = e.submitter;
                            } else {
                                // 否则,查找表单内的第一个提交按钮
                                const buttons = target.querySelectorAll('button[type="submit"], input[type="submit"]');
                                if (buttons.length > 0) {
                                    submitButton = buttons[0];
                                }
                            }
                            
                            // 如果找到提交按钮,使用提交按钮的选择器;否则使用表单的选择器
                            const selector = submitButton ? generateSelector(submitButton) : generateSelector(target);
                            
                            if (window && window.automationEvents) {
                                window.automationEvents.push({
                                    action: 'submit',
                                    selector: selector,
                                    timestamp: Date.now(),
                                    elementInfo: {
                                        tagName: submitButton ? submitButton.tagName : target.tagName,
                                        id: submitButton ? (submitButton.id || '') : (target.id || ''),
                                        className: submitButton ? (submitButton.className || '') : (target.className || ''),
                                        action: target.action || ''
                                    }
                                });
                            }
                        }
                    }, true);
                }
                
                // 监听页面导航事件
                const originalPushState = history.pushState;
                const originalReplaceState = history.replaceState;
                
                history.pushState = function() {
                    const result = originalPushState.apply(history, arguments);
                    window.automationEvents.push({
                        action: 'navigate',
                        url: location.href,
                        timestamp: Date.now(),
                        navigationType: 'pushState'
                    });
                    return result;
                };
                
                history.replaceState = function() {
                    const result = originalReplaceState.apply(history, arguments);
                    window.automationEvents.push({
                        action: 'navigate',
                        url: location.href,
                        timestamp: Date.now(),
                        navigationType: 'replaceState'
                    });
                    return result;
                };
                
                // 监听hashchange事件
                if (window && window.addEventListener) {
                    window.addEventListener('hashchange', function(e) {
                        if (window && window.automationEvents) {
                            window.automationEvents.push({
                                action: 'navigate',
                                url: location.href,
                                timestamp: Date.now(),
                                navigationType: 'hashchange',
                                oldURL: e.oldURL,
                                newURL: e.newURL
                            });
                        }
                    });
                }
                
                // 监听popstate事件(浏览器前进/后退)
                if (window && window.addEventListener) {
                    window.addEventListener('popstate', function(e) {
                        if (window && window.automationEvents) {
                            window.automationEvents.push({
                                action: 'navigate',
                                url: location.href,
                                timestamp: Date.now(),
                                navigationType: 'popstate',
                                state: e.state
                            });
                        }
                    });
                }
                
                // 改进的滚动事件监听
                if (window && window.addEventListener && window.automationConfig) {
                    window.addEventListener('scroll', function() {
                        // 清除之前的定时器
                        if (window.automationConfig.scrollTimeout) {
                            clearTimeout(window.automationConfig.scrollTimeout);
                        }
                        
                        // 设置新的定时器
                        window.automationConfig.scrollTimeout = setTimeout(() => {
                            if (window && document && window.automationEvents) {
                                const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                                const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
                                
                                // 计算滚动距离
                                const deltaX = Math.abs(scrollLeft - window.automationConfig.lastScrollPosition.x);
                                const deltaY = Math.abs(scrollTop - window.automationConfig.lastScrollPosition.y);
                                
                                // 只有当滚动距离超过阈值时才记录
                                if (deltaX >= window.automationConfig.scrollThreshold || deltaY >= window.automationConfig.scrollThreshold) {
                                    window.automationEvents.push({
                                        action: 'scroll',
                                        scrollPosition: {
                                            x: scrollLeft,
                                            y: scrollTop
                                        },
                                        scrollDirection: {
                                            x: scrollLeft > window.automationConfig.lastScrollPosition.x ? 'right' : 
                                                scrollLeft < window.automationConfig.lastScrollPosition.x ? 'left' : 'none',
                                            y: scrollTop > window.automationConfig.lastScrollPosition.y ? 'down' : 
                                                scrollTop < window.automationConfig.lastScrollPosition.y ? 'up' : 'none'
                                        },
                                        scrollDistance: {
                                            x: deltaX,
                                            y: deltaY
                                        },
                                        timestamp: Date.now()
                                    });
                                    
                                    // 更新最后滚动位置
                                    window.automationConfig.lastScrollPosition = { x: scrollLeft, y: scrollTop };
                                }
                            }
                        }, 100);
                    });
                }
                
                // 监听键盘事件(可选,用于特殊交互)
                if (document && document.addEventListener) {
                    document.addEventListener('keydown', function(e) {
                        // 只记录特殊按键,如回车、ESC等
                        if (e.key === 'Enter' || e.key === 'Escape' || e.key === 'Tab') {
                            const target = e.target;
                            const selector = generateSelector(target);
                            
                            if (window && window.automationEvents) {
                                window.automationEvents.push({
                                    action: 'keypress',
                                    selector: selector,
                                    key: e.key,
                                    timestamp: Date.now(),
                                    elementInfo: {
                                        tagName: target.tagName,
                                        id: target.id || '',
                                        className: target.className || ''
                                    }
                                });
                            }
                        }
                    }, true);
                }
                
                // 监听悬停事件(可选)
                if (document && document.addEventListener) {
                    document.addEventListener('mouseover', function(e) {
                        const target = e.target;
                        
                        // 只对可交互元素记录悬停
                        const interactiveTags = ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'OPTION'];
                        const isInteractive = interactiveTags.includes(target.tagName) || 
                                            target.onclick !== null || 
                                            target.getAttribute('role') === 'button' || 
                                            target.getAttribute('role') === 'link';
                        
                        if (isInteractive) {
                            const selector = generateSelector(target);
                            if (window && window.automationEvents) {
                                window.automationEvents.push({
                                    action: 'hover',
                                    selector: selector,
                                    timestamp: Date.now(),
                                    elementInfo: {
                                        tagName: target.tagName,
                                        id: target.id || '',
                                        className: target.className || ''
                                    }
                                });
                            }
                        }
                    }, true);
                }
                
                console.log('自动化事件监听器已设置完成');
                
                // 设置标志,表示已添加事件监听器
                window.eventListenersAdded = true;
            }
            """;
            
            # 1. 添加初始化脚本,确保新页面加载时自动设置监听器
            await self.page.add_init_script(event_listeners_js);
            
            # 2. 直接在当前页面执行,确保已加载页面也能捕获事件
            await self.page.evaluate(event_listeners_js);
            
            uat_logger.info("事件监听器已成功设置")
        else:
            uat_logger.warning("页面对象为None,无法设置事件监听器")

    async def _on_page_navigated(self, frame):
        """页面导航事件处理函数"""
        # 在页面导航后重新设置事件监听器,确保在新页面上也能记录用户操作
        try:
            # 多层检查确保页面对象有效且可用
            if (self.page is not None and 
                not (hasattr(self.page, 'is_closed') and self.page.is_closed()) and 
                not (hasattr(self.page, 'closed') and self.page.closed)):
                
                try:
                    # 调用统一的事件监听器设置方法
                    # _setup_event_listeners 方法内部会检查 window.eventListenersAdded 标志
                    # 避免重复添加事件监听器
                    await self._setup_event_listeners()
                    uat_logger.info("页面导航完成,已重新设置事件监听器")
                except Exception as inner_e:
                    # 捕获页面操作相关的异常
                    uat_logger.error(f"页面操作失败,可能页面已关闭: {str(inner_e)}")
            else:
                uat_logger.warning("页面对象无效或已关闭,无法设置事件监听器")
        except Exception as e:
            uat_logger.error(f"重新设置页面事件监听器时出错: {str(e)}")

    async def get_recorded_events(self):
        """从浏览器获取记录的事件"""
        if self.page is None:
            uat_logger.warning("页面对象为None,无法获取事件")
            return []
        
        try:
            # 检查页面是否仍然可用
            if hasattr(self.page, 'is_closed') and self.page.is_closed():
                uat_logger.warning("页面已关闭,无法获取事件")
                return []
            
            # 尝试使用更简单的方式检查页面状态
            try:
                # 检查事件数组是否存在
                has_events = await self.page.evaluate("typeof window.automationEvents !== 'undefined'")
                if not has_events:
                    uat_logger.warning("window.automationEvents 未定义,可能是事件监听器未设置")
                    # 尝试重新设置事件监听器
                    await self._setup_event_listeners()
                    return []
                
                # 调试:检查事件数组中是否有内容
                events_count = await self.page.evaluate("window.automationEvents ? window.automationEvents.length : 0")
                
                if events_count == 0:
                    uat_logger.debug("没有获取到浏览器事件")
                    return []
                
                events = await self.page.evaluate("window.automationEvents || []")
                uat_logger.info(f"获取到 {len(events)} 个浏览器事件")
                
                # 清空浏览器端的事件数组
                await self.page.evaluate("window.automationEvents = []")
                return events
            except Exception as e:
                # 页面可能正在导航中,这是正常情况
                uat_logger.debug(f"获取事件时遇到临时错误: {str(e)}")
                return []
        except Exception as e:
            uat_logger.error(f"获取浏览器事件时出错: {str(e)}")
            # 尝试重新设置事件监听器
            try:
                await self._setup_event_listeners()
            except:
                pass
            return []

    async def sync_recorded_events(self):
        """同步浏览器记录的事件到本地"""
        if self.recording and self.page:
            # 检查页面是否仍然可用
            try:
                if hasattr(self.page, 'is_closed') and self.page.is_closed():
                    print("页面已关闭,无法同步事件")
                    return 0
                
                # 检查页面是否仍可访问
                try:
                    # 先尝试访问一个简单的属性来检查页面状态
                    await self.page.title()
                except:
                    print("页面不可访问,无法同步事件")
                    return 0
                
                events = await self.get_recorded_events()
                for event in events:
                    # 将浏览器中的事件转换为录制步骤格式
                    step = {
                        "action": event.get('action'),
                        "timestamp": event.get('timestamp')
                    }
                    
                    if event.get('action') == 'click':
                        step['selector'] = event.get('selector')
                    elif event.get('action') == 'fill':
                        step['selector'] = event.get('selector')
                        step['text'] = event.get('text', '')
                    elif event.get('action') == 'navigate':
                        step['url'] = event.get('url')
                    elif event.get('action') == 'scroll':
                        step['scrollPosition'] = event.get('scrollPosition')
                        step['scrollDirection'] = event.get('scrollDirection')
                        step['scrollDistance'] = event.get('scrollDistance')
                    elif event.get('action') == 'hover':
                        step['selector'] = event.get('selector')
                    elif event.get('action') == 'double_click':
                        step['selector'] = event.get('selector')
                    elif event.get('action') == 'right_click':
                        step['selector'] = event.get('selector')
                    elif event.get('action') == 'submit':
                        step['selector'] = event.get('selector')
                    elif event.get('action') == 'keypress':
                        step['selector'] = event.get('selector')
                        step['key'] = event.get('key')
                    
                    # 去重逻辑:避免添加重复的步骤
                    if self.recorded_steps:
                        last_step = self.recorded_steps[-1]
                        
                        # 重新获取上一步骤
                        if self.recorded_steps:
                            last_step = self.recorded_steps[-1]
                        
                        # 特殊处理:如果当前是navigate事件,且上一步是submit事件,则跳过这个navigate事件
                        # 因为submit操作可能导致页面导航,我们不需要重复记录导航
                        if step['action'] == 'navigate' and last_step['action'] == 'submit':
                            uat_logger.info(f"跳过submit后的navigate事件: {step.get('url')}")
                            continue
                        
                        # 检查是否与上一步骤完全相同
                        if last_step['action'] == step['action']:
                            # 计算时间差(毫秒)
                            time_diff = step.get('timestamp', 0) - last_step.get('timestamp', 0)
                            
                            # 对于导航步骤,检查URL是否相同且时间间隔小于2秒
                            if step['action'] == 'navigate' and last_step.get('url') == step.get('url') and time_diff < 2000:
                                continue  # 跳过短时间内重复的导航步骤
                            # 对于点击步骤,检查选择器是否相同且时间间隔小于1秒
                            elif step['action'] == 'click' and last_step.get('selector') == step.get('selector') and time_diff < 1000:
                                continue  # 跳过短时间内重复的点击步骤
                            # 对于悬停步骤,检查选择器是否相同且时间间隔小于1秒
                            elif step['action'] == 'hover' and last_step.get('selector') == step.get('selector') and time_diff < 1000:
                                continue  # 跳过短时间内重复的悬停步骤
                            # 对于填充步骤,检查选择器和文本是否相同且时间间隔小于2秒
                            # 填充可能需要更长时间,但短时间内相同内容的填充应跳过
                            elif step['action'] == 'fill' and last_step.get('selector') == step.get('selector') and last_step.get('text') == step.get('text') and time_diff < 2000:
                                continue  # 跳过短时间内重复的填充步骤
                            # 对于按键步骤,检查选择器和按键是否相同且时间间隔小于1秒
                            elif step['action'] == 'keypress' and last_step.get('selector') == step.get('selector') and last_step.get('key') == step.get('key') and time_diff < 1000:
                                continue  # 跳过短时间内重复的按键步骤
                            # 对于提交步骤,检查选择器是否相同且时间间隔小于1秒
                            elif step['action'] == 'submit' and last_step.get('selector') == step.get('selector') and time_diff < 1000:
                                continue  # 跳过短时间内重复的提交步骤
                            # 对于滚动步骤,检查滚动位置是否基本相同且时间间隔小于1秒
                            elif step['action'] == 'scroll' and last_step.get('scrollPosition') == step.get('scrollPosition') and time_diff < 1000:
                                continue  # 跳过短时间内重复的滚动步骤
                    
                    # 添加到录制步骤中
                    self.recorded_steps.append(step)
                
                return len(events)
            except Exception as e:
                print(f"同步事件时出错: {e}")
                return 0
        return 0
    
    async def navigate_to(self, url: str, iframe_selector: str = None):
        """导航到指定URL,支持iframe导航"""
        if self.page is None:
            await self.start_browser()
        
        # 再次检查确保page对象存在
        if self.page is not None:
            # 导航到URL,对于录制时使用domcontentloaded以提高响应速度
            # 但在回放时,我们需要确保页面完全加载
            if self.recording:
                await self.page.goto(url, wait_until='domcontentloaded')
            else:
                # 回放时等待更完整的页面加载状态
                await self.page.goto(url, wait_until='load')
                # 额外等待网络请求完成(对于复杂的单页应用)
                try:
                    await self.page.wait_for_load_state('networkidle', timeout=25000)
                except Exception as e:
                    uat_logger.debug(f"网络idle状态超时(可能是正常的长连接): {str(e)}")
                # 增加JavaScript渲染等待时间,确保动态内容完全显示
                await self.page.wait_for_timeout(1000)
                
                # 等待页面渲染稳定(无更多DOM变化)
                await self.page.evaluate("""
                    () => new Promise(resolve => {
                        let lastScrollHeight = document.body.scrollHeight;
                        let checkCount = 0;
                        const checkInterval = 100;
                        const maxChecks = 10;
                        
                        const checkStability = () => {
                            const currentScrollHeight = document.body.scrollHeight;
                            if (currentScrollHeight === lastScrollHeight) {
                                checkCount++;
                                if (checkCount >= maxChecks) {
                                    resolve();
                                } else {
                                    setTimeout(checkStability, checkInterval);
                                }
                            } else {
                                lastScrollHeight = currentScrollHeight;
                                checkCount = 0;
                                setTimeout(checkStability, checkInterval);
                            }
                        };
                        
                        setTimeout(checkStability, checkInterval);
                    })
                    """)
        else:
            uat_logger.error("页面对象为None,无法导航")
            raise Exception("无法创建页面对象")
        self.current_url = url
        
        # 如果正在录制,记录导航步骤,并应用去重逻辑
        if self.recording:
            step = {
                "action": "navigate",
                "url": url,
                "iframe_selector": iframe_selector,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            
            # 应用去重逻辑
            if self.recorded_steps:
                last_step = self.recorded_steps[-1]
                if last_step['action'] == 'navigate' and last_step.get('url') == step.get('url'):
                    # 计算时间差(毫秒)
                    time_diff = step.get('timestamp', 0) - last_step.get('timestamp', 0)
                    if time_diff < 2000:  # 使用与其他地方一致的2秒阈值
                        return  # 跳过短时间内重复的导航步骤
            
            self.recorded_steps.append(step)
            uat_logger.info(f"录制导航步骤: {url}")
        else:
            uat_logger.info(f"执行导航操作: {url}")
    
    async def click_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None):
        """点击元素"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        # 检查页面是否已经被关闭
        try:
            if hasattr(self.page, 'url'):
                _ = self.page.url  # 尝试访问URL判断页面是否有效
        except Exception as e:
            uat_logger.error(f"页面已关闭,无法执行点击操作: {str(e)}")
            raise Exception("页面已关闭")
        
        uat_logger.info(f"🔍 [CLICK_DEBUG] 开始点击元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        if target_context is None:
            uat_logger.error(f"❌ [CLICK_DEBUG] 操作上下文为None,无法执行点击操作")
            raise Exception(f"操作上下文为None,无法执行点击操作")
        
        element_clicked = False
        
        # 获取当前页面URL和状态
        try:
            current_url = self.page.url
            uat_logger.info(f"🔍 [CLICK_DEBUG] 当前页面URL: {current_url}")
        except Exception as e:
            uat_logger.warning(f"🔍 [CLICK_DEBUG] 获取当前URL失败: {str(e)}")
            current_url = ""
        
        # 尝试多种点击方式,增加成功概率
        # 方式1: 使用Playwright的click方法,等待元素可点击
        try:
            uat_logger.info(f"🔍 [CLICK_DEBUG] 尝试方式1: Playwright click方法")
            # 根据上下文类型执行不同的操作
            if hasattr(target_context, 'wait_for_selector'):
                # 等待元素可见且可交互
                await target_context.wait_for_selector(full_selector, state='visible', timeout=5000)
                # 等待元素可点击
                await target_context.wait_for_selector(full_selector, state='enabled', timeout=5000)
                # 使用更健壮的点击方式
                await target_context.click(full_selector, timeout=5000)
                uat_logger.info(f"✅ [CLICK_DEBUG] 方式1成功点击元素: {selector}, 选择器类型: {selector_type}")
                element_clicked = True
            else:
                # 如果是frame_locator对象,需要使用其locator方法
                element = target_context.locator(full_selector)
                await element.wait_for(state='visible', timeout=5000)
                await element.wait_for(state='enabled', timeout=5000)
                await element.click(timeout=5000)
                uat_logger.info(f"✅ [CLICK_DEBUG] 方式1成功点击元素: {selector}, 选择器类型: {selector_type}")
                element_clicked = True
        except Exception as e:
            uat_logger.warning(f"⚠️ [CLICK_DEBUG] 方式1失败: {str(e)}, 尝试方式2: force click")
            
            # 方式2: 使用force参数强制点击
            try:
                if hasattr(target_context, 'click'):
                    await target_context.click(full_selector, force=True, timeout=5000)
                    uat_logger.info(f"✅ [CLICK_DEBUG] 方式2成功点击元素: {selector}, 选择器类型: {selector_type}")
                    element_clicked = True
                else:
                    # 如果是frame_locator对象,需要使用其locator方法
                    element = target_context.locator(full_selector)
                    await element.click(timeout=5000, force=True)
                    uat_logger.info(f"✅ [CLICK_DEBUG] 方式2成功点击元素: {selector}, 选择器类型: {selector_type}")
                    element_clicked = True
            except Exception as e2:
                uat_logger.warning(f"⚠️ [CLICK_DEBUG] 方式2失败: {str(e2)}, 尝试方式3: JavaScript点击")
                
                # 方式3: 尝试使用JavaScript点击
                try:
                    uat_logger.info(f"🔍 [CLICK_DEBUG] 尝试方式3: JavaScript点击")
                    # 检查元素是否存在并点击
                    if selector_type == "css":
                        if hasattr(target_context, 'evaluate'):
                            element_exists = await target_context.evaluate("(selector) => document.querySelector(selector) !== null", selector)
                            if element_exists:
                                # 使用JavaScript点击,正常触发所有事件
                                await target_context.evaluate("""(selector) => {
                                    const element = document.querySelector(selector);
                                    if (element) {
                                        // 直接使用click(),触发所有相关事件
                                        element.click();
                                    }
                                }""", selector)
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击元素: {selector}, 选择器类型: {selector_type}")
                                element_clicked = True
                            else:
                                uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在,无法使用JavaScript点击: {selector}")
                        else:
                            # 如果是frame_locator对象,使用其locator方法
                            element = target_context.locator(selector)
                            count = await element.count()
                            if count > 0:
                                await element.click(timeout=5000, force=True)
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击元素: {selector}, 选择器类型: {selector_type}")
                                element_clicked = True
                            else:
                                uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在,无法点击: {selector}")
                    else:  # xpath
                        if hasattr(target_context, 'evaluate'):
                            element_exists = await target_context.evaluate("""(xpath) => {
                                const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                return result.singleNodeValue !== null;
                            }""", selector)
                            if element_exists:
                                # 使用JavaScript点击,正常触发所有事件
                                await target_context.evaluate("""(xpath) => {
                                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                    const element = result.singleNodeValue;
                                    if (element) {
                                        // 直接使用click(),触发所有相关事件
                                        element.click();
                                    }
                                }""", selector)
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击元素: {selector}, 选择器类型: {selector_type}")
                                element_clicked = True
                            else:
                                uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在,无法使用JavaScript点击: {selector}")
                        else:
                            # 如果是frame_locator对象,使用其locator方法
                            element = target_context.locator(f"xpath={selector}")
                            count = await element.count()
                            if count > 0:
                                await element.click(timeout=5000, force=True)
                                uat_logger.info(f"✅ [CLICK_DEBUG] 方式3成功点击元素: {selector}, 选择器类型: {selector_type}")
                                element_clicked = True
                            else:
                                uat_logger.error(f"❌ [CLICK_DEBUG] 元素不存在,无法点击: {selector}")
                except Exception as e3:
                    uat_logger.error(f"❌ [CLICK_DEBUG] 方式3失败: {str(e3)}")
                    
        if not element_clicked:
            # 如果所有点击方式都失败,抛出异常
            raise Exception(f"无法点击元素: {selector}, 选择器类型: {selector_type}, 所有点击方式均失败")
        
        # 检查点击后的页面状态
        try:
            new_url = self.page.url
            uat_logger.info(f"🔍 [CLICK_DEBUG] 点击后页面URL: {new_url}")
            if new_url != current_url:
                uat_logger.info(f"🔄 [CLICK_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
        except Exception as e:
            uat_logger.warning(f"🔍 [CLICK_DEBUG] 获取点击后URL失败: {str(e)}")
        
        # 单选框和复选框点击后状态验证
        try:
            # 检查是否是单选框或复选框相关选择器
            is_radio_selector = False
            is_checkbox_selector = False
            selector_lower = selector.lower()
            if 'radio' in selector_lower or 'type="radio"' in selector_lower:
                is_radio_selector = True
            elif 'checkbox' in selector_lower or 'type="checkbox"' in selector_lower:
                is_checkbox_selector = True
            
            # 如果是单选框或复选框选择器,验证点击后状态
            if is_radio_selector or is_checkbox_selector:
                # 等待元素状态更新
                await self.page.wait_for_timeout(200)
                
                # 检查单选框或复选框是否被选中
                evaluate_script = f'''() => {{
                    const element = document.querySelector('{selector}');
                    if (element && element.tagName === 'INPUT' && (element.type === 'radio' || element.type === 'checkbox')) {{
                        return element.checked;
                    }}
                    // 处理复合组件,找到内部的input元素
                    const inputElement = element?.querySelector('input[type="radio"], input[type="checkbox"]');
                    return inputElement ? inputElement.checked : false;
                }}'''
                is_checked = await self.page.evaluate(evaluate_script)
                
                if is_checked:
                    element_type = "单选框" if is_radio_selector else "复选框"
                    uat_logger.info(f"✅ {element_type}点击验证通过: {selector} 已选中")
                else:
                    element_type = "单选框" if is_radio_selector else "复选框"
                    uat_logger.warning(f"⚠️ {element_type}点击验证警告: {selector} 未选中")
        except Exception as e:
            uat_logger.warning(f"验证单选框/复选框状态时出错: {str(e)}")
        
        # 如果正在录制,记录点击步骤
        if self.recording:
            step = {
                "action": "click",
                "selector": selector,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            self.recorded_steps.append(step)
    
    async def fill_input(self, selector: str, text: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None):
        """填充输入框"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        # 尝试多种填充方式,增加成功概率
        fill_success = False
        
        # 方式1: 使用Playwright的fill方法
        try:
            # 等待元素可见
            if hasattr(target_context, 'wait_for_selector'):
                await target_context.wait_for_selector(full_selector, state='visible', timeout=5000)
                # 填充输入框
                await target_context.fill(full_selector, text, timeout=5000)
                uat_logger.info(f"成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                fill_success = True
            else:
                # 如果是frame_locator对象,需要使用其locator方法
                element = target_context.locator(full_selector)
                await element.wait_for(state='visible', timeout=5000)
                await element.fill(text, timeout=5000)
                uat_logger.info(f"成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                fill_success = True
        except Exception as e:
            uat_logger.warning(f"常规填充失败: {str(e)}, 尝试使用force fill方法")
            
            # 方式2: 使用force fill方法
            try:
                if hasattr(target_context, 'fill'):
                    await target_context.fill(full_selector, text, timeout=5000, force=True)
                    uat_logger.info(f"使用force fill方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                    fill_success = True
                else:
                    # 如果是frame_locator对象,需要使用其locator方法
                    element = target_context.locator(full_selector)
                    await element.fill(text, timeout=5000, force=True)
                    uat_logger.info(f"使用force fill方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                    fill_success = True
            except Exception as e2:
                uat_logger.warning(f"force fill方法失败: {str(e2)}, 尝试使用type方法")
                
                # 方式3: 使用type方法
                try:
                    if hasattr(target_context, 'type'):
                        await target_context.type(full_selector, text, timeout=5000)
                        uat_logger.info(f"使用type方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                        fill_success = True
                    else:
                        # 如果是frame_locator对象,需要使用其locator方法
                        element = target_context.locator(full_selector)
                        await element.type(text, timeout=5000)
                        uat_logger.info(f"使用type方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                        fill_success = True
                except Exception as e3:
                    uat_logger.warning(f"type方法失败: {str(e3)}, 尝试使用force type方法")
                    
                    # 方式4: 使用force type方法
                    try:
                        if hasattr(target_context, 'type'):
                            await target_context.type(full_selector, text, timeout=5000, force=True)
                            uat_logger.info(f"使用force type方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                            fill_success = True
                        else:
                            # 如果是frame_locator对象,需要使用其locator方法
                            element = target_context.locator(full_selector)
                            await element.type(text, timeout=5000, force=True)
                            uat_logger.info(f"使用force type方法成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                            fill_success = True
                    except Exception as e4:
                        uat_logger.warning(f"force type方法失败: {str(e4)}, 尝试使用JavaScript")
                        
                        # 方式5: 使用JavaScript直接设置值
                        try:
                            # 检查元素是否存在并设置值
                            if selector_type == "css":
                                if hasattr(target_context, 'evaluate'):
                                    element_exists = await target_context.evaluate("(selector) => document.querySelector(selector) !== null", selector)
                                    if element_exists:
                                        # 使用JavaScript设置值并触发输入相关事件
                                        await target_context.evaluate("""(selector, text) => {
                                            const element = document.querySelector(selector);
                                            if (element) {
                                                // 设置值
                                                element.value = text;
                                                
                                                // 触发输入相关事件
                                                element.dispatchEvent(new Event('input', {bubbles: true}));
                                                element.dispatchEvent(new Event('change', {bubbles: true}));
                                                element.dispatchEvent(new Event('blur', {bubbles: true}));
                                            }
                                        }""", selector, text)
                                        uat_logger.info(f"使用JavaScript成功填充元素: {selector}, 文本: {text}")
                                        fill_success = True
                                    else:
                                        uat_logger.error(f"元素不存在,无法使用JavaScript填充: {selector}")
                                else:
                                    # 如果是frame_locator对象,使用其locator方法
                                    element = target_context.locator(selector)
                                    count = await element.count()
                                    if count > 0:
                                        await element.fill(text, timeout=5000, force=True)
                                        uat_logger.info(f"使用frame_locator方法成功填充元素: {selector}, 文本: {text}")
                                        fill_success = True
                                    else:
                                        uat_logger.error(f"元素不存在,无法使用frame_locator方法填充: {selector}")
                            else:  # xpath
                                # 使用XPath查找元素
                                if hasattr(target_context, 'evaluate'):
                                    element_exists = await target_context.evaluate("""(xpath) => {
                                    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                    return result.singleNodeValue !== null;
                                }""", selector)
                                if element_exists:
                                    # 使用JavaScript设置值并触发输入相关事件
                                    await target_context.evaluate("""(xpath, text) => {
                                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                                        const element = result.singleNodeValue;
                                        if (element) {
                                            // 设置值
                                            element.value = text;
                                            
                                            // 触发输入相关事件
                                            element.dispatchEvent(new Event('input', {bubbles: true}));
                                            element.dispatchEvent(new Event('change', {bubbles: true}));
                                            element.dispatchEvent(new Event('blur', {bubbles: true}));
                                        }
                                    }""", selector, text)
                                    uat_logger.info(f"使用JavaScript成功填充元素: {selector}, 选择器类型: {selector_type}, 文本: {text}")
                                    fill_success = True
                                else:
                                    uat_logger.error(f"元素不存在,无法使用JavaScript填充: {selector}")
                        except Exception as e5:
                            uat_logger.error(f"JavaScript填充失败: {str(e5)}")
        
        if not fill_success:
            raise Exception(f"无法填充元素: {selector}, 选择器类型: {selector_type}, 所有填充方式均失败")
        
        # 如果正在录制,记录填充步骤
        if self.recording:
            step = {
                "action": "fill",
                "selector": selector,
                "selector_type": selector_type,
                "text": text,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            self.recorded_steps.append(step)
    
    async def scroll_page(self, direction: str = "down", pixels: int = 500, iframe_selector: str = None, iframe_context=None):
        """滚动页面或iframe"""
        if self.page is None:
            uat_logger.error("浏览器未启动,无法执行滚动操作")
            raise Exception("浏览器未启动")
        
        # 检查页面是否已经被关闭
        try:
            # 尝试获取页面URL来检查页面是否仍然有效
            if hasattr(self.page, 'url'):
                _ = self.page.url
        except Exception as e:
            uat_logger.error(f"页面已关闭,无法执行滚动操作: {str(e)}")
            raise Exception("页面已关闭")
        
        uat_logger.info(f"🔍 [SCROLL_DEBUG] 开始滚动,方向: {direction}, 像素: {pixels}, iframe选择器: {iframe_selector}")
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        # 执行滚动操作
        if hasattr(target_context, 'evaluate'):
            # 对于page对象
            if direction == "down":
                await target_context.evaluate(f"window.scrollBy(0, {pixels})")
            elif direction == "up":
                await target_context.evaluate(f"window.scrollBy(0, {-pixels})")
            elif direction == "to_top":
                await target_context.evaluate("window.scrollTo(0, 0)")
            elif direction == "to_bottom":
                await target_context.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            # 对于frame_locator对象,需要先获取iframe的contentFrame
            try:
                # 获取iframe的contentFrame
                iframe = await target_context.first.content_frame()
                if iframe:
                    if direction == "down":
                        await iframe.evaluate(f"window.scrollBy(0, {pixels})")
                    elif direction == "up":
                        await iframe.evaluate(f"window.scrollBy(0, {-pixels})")
                    elif direction == "to_top":
                        await iframe.evaluate("window.scrollTo(0, 0)")
                    elif direction == "to_bottom":
                        await iframe.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                else:
                    uat_logger.warning("无法获取iframe的contentFrame,无法执行滚动操作")
            except Exception as e:
                uat_logger.warning(f"执行iframe滚动时出错: {str(e)}")
        
        # 如果正在录制,记录滚动步骤
        if self.recording:
            step = {
                "action": "scroll",
                "direction": direction,
                "pixels": pixels,
                "iframe_selector": iframe_selector,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            self.recorded_steps.append(step)
    
    async def get_page_text(self) -> str:
        """获取页面文本内容"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        # 使用更高效的方法获取页面文本
        try:
            # 首先尝试使用JavaScript直接获取所有文本,这是最快的方法
            text_content = await self.page.evaluate(
                "() => document.body.innerText || document.body.textContent || document.documentElement.innerText || document.documentElement.textContent || ''"
            )
            
            if text_content and text_content.strip():
                return text_content.strip()
            
            # 如果JavaScript方法失败,使用Playwright的text_content方法
            body_element = self.page.locator('body')
            text_content = await body_element.text_content(timeout=5000)
            
            return text_content if text_content else ""
        except Exception as e:
            print(f"获取页面文本时出错: {e}")
            return ""
    
    async def extract_element_text(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None) -> str:
        """提取特定元素的文本，支持多种定位方式
        Parameters:
            selector: Locator string
            selector_type: Locator type, supports:
                - css: CSS selector
                - xpath: XPath selector
                - text: Text content
                - role: Semantic role (use role name directly, e.g. "button", "heading")
                - testid: Test ID (data-testid attribute value)
            iframe_selector: iframe selector (optional)
            iframe_context: iframe context (optional)
        """
        if self.page is None:
            raise Exception("Browser not started")
        
        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Start extracting text, selector: {selector}, selector_type: {selector_type}")
        
        try:
            element = None
            
            # Determine target context
            target_context = self.page
            if iframe_selector:
                target_context = self.page.frame_locator(iframe_selector)
            elif iframe_context:
                target_context = iframe_context
            
            # Get element based on context type and locator method
            if hasattr(target_context, 'locator'):
                # For page or frame_locator objects, use locator method
                if selector_type == "css":
                    # CSS selector
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using CSS selector: {selector}")
                    element = target_context.locator(selector)
                    element = element.first
                elif selector_type == "xpath":
                    # XPath selector
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using XPath selector: {selector}")
                    element = target_context.locator(f"xpath={selector}")
                    element = element.first
                elif selector_type == "text":
                    # Text content selector
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using text selector: {selector}")
                    element = target_context.locator(f"text={selector}")
                    element = element.first
            elif selector_type == "role":
                # Semantic role selector
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using role selector: {selector}")
                # Use Playwright's dedicated role locator
                if "," in selector:
                    # Handle role with parameters, only use role name part
                    role_name = selector.split(",")[0]
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Role selector contains parameters, only use role name: {role_name}")
                    element = self.page.get_by_role(role_name)
                else:
                    element = self.page.get_by_role(selector)
                element = element.first
            elif selector_type == "testid":
                # Test ID selector, use Playwright's dedicated testid locator
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Using testid selector: {selector}")
                element = self.page.get_by_test_id(selector)
                element = element.first
            elif selector.startswith("//") or selector.startswith("/"):
                # Auto-detect XPath
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Auto-detected as XPath selector: {selector}")
                element = self.page.locator(f"xpath={selector}")
                element = element.first
            else:
                # Default to CSS selector
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Default to CSS selector: {selector}")
                element = self.page.locator(selector)
                element = element.first
            
            # Ensure element is correctly obtained
            if element is None:
                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] Element not successfully obtained")
                return ""
            
            # Add relaxed waiting mechanism
            try:
                # Try to wait for element to exist (not required to be visible)
                await element.wait_for(state="attached", timeout=5000)
            except Exception:
                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] Waiting for element existence timed out, trying to continue extraction")
            
            # Check if element exists
            try:
                count = await element.count()
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Found {count} elements")
                if count == 0:
                    uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] Element not found")
                    return ""
            except Exception as e:
                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] Failed to check element count: {e}")
                # Continue trying to extract, not forcing element existence
            
            # Use appropriate extraction method based on element type
            extracted_text = ""
            try:
                # Try to get element's tag name to determine element type
                tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Element tag name: {tag_name}")
                
                if tag_name in ["input", "textarea"]:
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Input element, using input_value() extraction")
                    try:
                        extracted_text = await element.input_value()
                        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] input_value() extraction result: '{extracted_text}'")
                    except Exception as e:
                        uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] input_value() failed: {e}")
                        try:
                            extracted_text = await element.get_attribute("value")
                            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] get_attribute('value') extraction result: '{extracted_text}'")
                        except Exception as e2:
                            uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] get_attribute('value') failed: {e2}")
                else:
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Normal element, using inner_text() extraction")
                    try:
                        extracted_text = await element.inner_text()
                        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] inner_text() extraction result: '{extracted_text}'")
                    except Exception as e:
                        uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] inner_text() failed: {e}")
                        try:
                            extracted_text = await element.text_content()
                            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] text_content() extraction result: '{extracted_text}'")
                        except Exception as e2:
                            uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] text_content() failed: {e2}")
            except Exception as e:
                # If getting tag name fails, try using general methods to extract text
                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] Failed to get tag name: {e}, trying general methods to extract text")
                try:
                    extracted_text = await element.inner_text()
                    uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method inner_text() extraction result: '{extracted_text}'")
                except Exception as e:
                    uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] inner_text() failed: {e}")
                    try:
                        extracted_text = await element.text_content()
                        uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method text_content() extraction result: '{extracted_text}'")
                    except Exception as e2:
                        uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] text_content() failed: {e2}")
                        try:
                            extracted_text = await element.input_value()
                            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method input_value() extraction result: '{extracted_text}'")
                        except Exception as e3:
                            uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] input_value() failed: {e3}")
                            try:
                                extracted_text = await element.get_attribute("value")
                                uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] General method get_attribute('value') extraction result: '{extracted_text}'")
                            except Exception as e4:
                                uat_logger.warning(f"📝 [TEXT_EXTRACT_DEBUG] get_attribute('value') failed: {e4}")
            
            # Ensure returned text is not None
            result = extracted_text if extracted_text is not None else ""
            uat_logger.info(f"📝 [TEXT_EXTRACT_DEBUG] Final extraction result: '{result}'")
            return result
        except Exception as e:
            # Record detailed exception information
            uat_logger.error(f"📝 [TEXT_EXTRACT_DEBUG] Error extracting text: {str(e)}")
            print(f"Error extracting element text: {str(e)}")
            return ""
    async def extract_element_json(self, selector: str, selector_type: str = "css") -> dict:
        """从特定元素中提取JSON数据,支持多种定位方式
        参数:
            selector: 定位器字符串
            selector_type: 定位器类型,支持以下选项:
                - css: CSS选择器
                - xpath: XPath选择器
                - text: 文本内容
                - role: 语义角色 (直接使用角色名,如 "button", "heading")
                - testid: 测试ID (data-testid属性值)
        返回:
            提取到的JSON数据,解析失败则返回空字典
        """
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 开始提取JSON,选择器: {selector}, 选择器类型: {selector_type}")
        
        try:
            element = None
            
            # 根据不同定位方式获取元素
            if selector_type == "css":
                # CSS选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用CSS选择器: {selector}")
                element = self.page.locator(selector)
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "xpath":
                # XPath选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用XPath选择器: {selector}")
                element = self.page.locator(f"xpath={selector}")
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "text":
                # 文本内容选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用文本选择器: {selector}")
                element = self.page.locator(f"text={selector}")
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "role":
                # 语义角色选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用角色选择器: {selector}")
                # 使用Playwright的专用role定位器
                if "," in selector:
                    # 处理带参数的角色,只使用角色名部分
                    role_name = selector.split(",")[0]
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 角色选择器包含参数,只使用角色名: {role_name}")
                    element = self.page.get_by_role(role_name)
                else:
                    element = self.page.get_by_role(selector)
                await element.wait_for(state="visible", timeout=8000)
            elif selector_type == "testid":
                # 测试ID选择器,使用Playwright的专用testid定位器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 使用testid选择器: {selector}")
                element = self.page.get_by_test_id(selector)
                await element.wait_for(state="visible", timeout=8000)
            elif selector.startswith("//") or selector.startswith("/"):
                # 自动识别XPath
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 自动识别为XPath选择器: {selector}")
                element = self.page.locator(f"xpath={selector}")
                await element.wait_for(state="visible", timeout=8000)
            else:
                # 默认使用CSS选择器
                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 默认使用CSS选择器: {selector}")
                element = self.page.locator(selector)
                await element.wait_for(state="visible", timeout=8000)
            
            # 确保元素已正确获取
            if element is None:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 未成功获取元素")
                return {}
            
            # 检查元素是否存在
            count = await element.count()
            uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 找到元素数量: {count}")
            if count == 0:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 未找到元素")
                return {}
            
            # 获取第一个匹配元素
            element = element.first
            
            # 获取元素的标签名,判断元素类型
            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 元素标签名: {tag_name}")
            
            # 从多种来源提取JSON数据
            json_sources = []
            
            # 1. 从元素文本内容提取
            try:
                text_content = await element.text_content()
                if text_content and text_content.strip():
                    json_sources.append(text_content.strip())
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从text_content提取到潜在JSON: {text_content.strip()[:100]}...")
            except Exception as e:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从text_content提取失败: {e}")
            
            # 2. 从inner_text提取
            try:
                inner_text = await element.inner_text()
                if inner_text and inner_text.strip() and inner_text.strip() != text_content:
                    json_sources.append(inner_text.strip())
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从inner_text提取到潜在JSON: {inner_text.strip()[:100]}...")
            except Exception as e:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从inner_text提取失败: {e}")
            
            # 3. 从input/textarea的value属性提取
            if tag_name in ["input", "textarea"]:
                try:
                    input_value = await element.input_value()
                    if input_value and input_value.strip():
                        json_sources.append(input_value.strip())
                        uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从input_value提取到潜在JSON: {input_value.strip()[:100]}...")
                except Exception as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从input_value提取失败: {e}")
            
            # 4. 从innerHTML提取(寻找JSON结构)
            try:
                inner_html = await element.innerHTML()
                if inner_html and inner_html.strip():
                    # 尝试从innerHTML中提取JSON字符串
                    import re
                    # 匹配JSON对象或数组
                    json_pattern = r'\{\s*["\w].*?\}\s*' + r'|' + r'\[\s*["\w].*?\]\s*'
                    matches = re.findall(json_pattern, inner_html, re.DOTALL)
                    if matches:
                        for match in matches:
                            if match.strip():
                                json_sources.append(match.strip())
                                uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从innerHTML提取到潜在JSON: {match.strip()[:100]}...")
            except Exception as e:
                uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从innerHTML提取失败: {e}")
            
            # 5. 从元素的特定属性提取
            json_attributes = ["data-json", "data-content", "data-value", "value"]
            for attr in json_attributes:
                try:
                    attr_value = await element.get_attribute(attr)
                    if attr_value and attr_value.strip():
                        json_sources.append(attr_value.strip())
                        uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 从属性{attr}提取到潜在JSON: {attr_value.strip()[:100]}...")
                except Exception as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 从属性{attr}提取失败: {e}")
            
            # 尝试解析每个潜在的JSON源
            for json_source in json_sources:
                try:
                    import json
                    # 清理JSON字符串(移除可能的换行符、多余空格等)
                    cleaned_json = json_source.replace("\n", "").replace("\r", "").strip()
                    # 尝试解析JSON
                    json_data = json.loads(cleaned_json)
                    uat_logger.info(f"📝 [JSON_EXTRACT_DEBUG] 成功解析JSON,包含{len(json_data) if isinstance(json_data, dict) else len(json_data)}个元素")
                    return json_data
                except json.JSONDecodeError as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] JSON解析失败: {e},尝试下一个源")
                except Exception as e:
                    uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 处理JSON源时出错: {e},尝试下一个源")
            
            uat_logger.warning(f"📝 [JSON_EXTRACT_DEBUG] 所有JSON源解析失败")
            return {}
        except Exception as e:
            # 详细记录异常信息
            uat_logger.error(f"📝 [JSON_EXTRACT_DEBUG] 提取JSON时出错: {str(e)}")
            print(f"提取元素JSON时出错: {str(e)}")
            return {}

    async def _validate_selector(self, selector: str):
        """验证定位器的有效性和唯一性"""
        try:
            # 执行inspector验证
            elements = await self.page.evaluate(f'''
                (selector) => {{
                    const els = document.querySelectorAll(selector);
                    return {{
                        count: els.length,
                        sampleHtml: els.length > 0 ? els[0].innerHTML.substring(0, 200) : ''
                    }};
                }}
            ''', selector)
            
            print(f"定位器验证结果: 匹配 {elements['count']} 个元素")
            if elements['sampleHtml']:
                print(f"第一个匹配元素的HTML片段: {elements['sampleHtml']}")
        except Exception as e:
            print(f"定位器验证失败: {e}")
    
    async def _wait_for_text_non_empty(self, element, selector: str, timeout: int = 10000):
        """等待元素文本非空状态"""
        try:
            # 尝试等待文本非空
            await self.page.wait_for(f'''
                () => {{
                    const el = document.querySelector('{selector}');
                    if (!el) return false;
                    
                    // 检查是否为输入元素
                    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
                        return el.value && el.value.trim() !== '';
                    }}
                    
                    // 检查其他元素
                    return (el.innerText && el.innerText.trim() !== '') || 
                           (el.textContent && el.textContent.trim() !== '');
                }}
            ''', timeout=timeout)
        except Exception:
            # 超时后继续执行,不抛出异常
            pass
    
    async def _extract_from_shadow_dom(self, selector: str) -> str:
        """从Shadow DOM中提取文本"""
        try:
            # 尝试使用JavaScript穿透Shadow DOM
            text = await self.page.evaluate(f'''
                (selector) => {{
                    // 递归查找元素,支持Shadow DOM
                    function findElement(root, selector) {{
                        // 先在当前根节点查找
                        let el = root.querySelector(selector);
                        if (el) return el;
                        
                        // 查找所有Shadow DOM
                        const shadowHosts = root.querySelectorAll('*');
                        for (let host of shadowHosts) {{
                            if (host.shadowRoot) {{
                                el = findElement(host.shadowRoot, selector);
                                if (el) return el;
                            }}
                        }}
                        return null;
                    }}
                    
                    // 开始查找
                    const element = findElement(document, selector);
                    if (!element) return '';
                    
                    // 提取文本
                    if (element.tagName === 'INPUT' || element.tagName === 'TEXTAREA') {{
                        return element.value || element.getAttribute('value') || '';
                    }}
                    return element.innerText || element.textContent || '';
                }}
            ''', selector)
            print(f"Shadow DOM提取结果: '{text}'")
            return text if text else ""
        except Exception as e:
            print(f"Shadow DOM提取时出错: {e}")
            # 尝试使用更简单的方法
            try:
                # 使用更简单的JavaScript提取方法
                text = await self.page.evaluate(f'''
                    (selector) => {{
                        // 直接尝试使用querySelector穿透Shadow DOM
                        // 注意:这在某些浏览器中可能不支持
                        const el = document.querySelector(selector);
                        if (!el) return '';
                        
                        if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
                            return el.value || el.getAttribute('value') || '';
                        }}
                        return el.innerText || el.textContent || '';
                    }}
                ''', selector)
                print(f"降级Shadow DOM提取结果: '{text}'")
                return text if text else ""
            except Exception as e2:
                print(f"降级Shadow DOM提取时出错: {e2}")
                return ""
    
    async def _extract_from_iframe(self, selector: str) -> str:
        """从iframe中提取文本"""
        try:
            # 递归函数:从frame及其子frame中提取文本
            async def extract_from_frame(frame):
                try:
                    # 尝试在当前frame中查找元素
                    element = frame.locator(selector)
                    await element.wait_for(timeout=5000)
                    
                    # 提取文本
                    extraction_methods = []
                    
                    try:
                        # 尝试使用inner_text()提取可见文本
                        text = await element.inner_text()
                        extraction_methods.append(("inner_text", text))
                        print(f"iframe中inner_text提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    try:
                        # 尝试使用text_content()提取所有文本
                        text = await element.text_content()
                        extraction_methods.append(("text_content", text))
                        print(f"iframe中text_content提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    try:
                        # 尝试使用input_value()提取输入框值
                        text = await element.input_value()
                        extraction_methods.append(("input_value", text))
                        print(f"iframe中input_value提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    try:
                        # 尝试使用get_attribute("value")提取属性值
                        text = await element.get_attribute("value")
                        extraction_methods.append(("get_attribute('value')", text))
                        print(f"iframe中get_attribute('value')提取结果: '{text}'")
                        if text:
                            return text
                    except:
                        pass
                    
                    # 提取方法结果对比验证
                    if extraction_methods:
                        print("iframe中提取方法结果对比:")
                        for method, result in extraction_methods:
                            print(f"  {method}: '{result}'")
                        
                        # 选择非空结果
                        for method, result in extraction_methods:
                            if result:
                                print(f"选择iframe中最优提取方法: {method}")
                                return result
                                
                except:
                    pass
                
                # 递归处理子frame
                try:
                    child_frames = frame.child_frames()
                    for child_frame in child_frames:
                        try:
                            child_text = await extract_from_frame(child_frame)
                            if child_text:
                                return child_text
                        except Exception as e:
                            print(f"处理子frame时出错: {e}")
                            pass
                except Exception as e:
                    print(f"获取子frame时出错: {e}")
                    pass
                
                return ""
            
            # 从主页面开始递归提取
            main_frame_text = await extract_from_frame(self.page.main_frame())
            if main_frame_text:
                return main_frame_text
            
            # 额外尝试:使用frame_locator方法
            try:
                # 尝试通过CSS选择器定位iframe
                iframe_selector = "iframe"
                await self.page.wait_for_selector(iframe_selector, timeout=5000)
                iframe = self.page.frame_locator(iframe_selector)
                element = iframe.locator(selector)
                await element.wait_for(timeout=5000)
                
                # 尝试提取文本
                try:
                    text = await element.inner_text()
                    if text:
                        return text
                except:
                    pass
                
                try:
                    text = await element.text_content()
                    if text:
                        return text
                except:
                    pass
                
                try:
                    text = await element.input_value()
                    if text:
                        return text
                except:
                    pass
                
                try:
                    text = await element.get_attribute("value")
                    if text:
                        return text
                except:
                    pass
                    
            except:
                pass
            
            return ""
        except Exception as e:
            print(f"iframe提取时出错: {e}")
            return ""
    
    async def extract_all_texts(self, selector: str) -> List[str]:
        """批量提取多个元素的文本"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 使用locator()定位多个元素,内置自动等待
            elements = self.page.locator(selector)
            # 等待至少一个元素可见
            await elements.first.wait_for(state='visible', timeout=10000)
            # 使用all_inner_texts()批量提取文本
            texts = await elements.all_inner_texts()
            return texts
        except Exception as e:
            print(f"批量提取文本时出错: {e}")
            return []
    
    async def extract_text_from_iframe(self, iframe_selector: str, element_selector: str) -> str:
        """从iframe中提取文本"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 1. 增强等待机制:等待iframe加载完成
            await self.page.wait_for_selector(iframe_selector, timeout=15000)
            
            # 2. 使用frame_locator()定位iframe
            iframe = self.page.frame_locator(iframe_selector)
            
            # 3. 等待iframe中的元素可见
            await iframe.locator(element_selector).wait_for(state='visible', timeout=10000)
            
            # 4. 在iframe中定位元素
            element = iframe.locator(element_selector)
            
            # 5. 尝试获取元素标签名,判断元素类型
            try:
                tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
                
                # 对于输入框类型,使用多种方法获取值
                if tag_name in ["input", "textarea"]:
                    # 首先尝试input_value()
                    try:
                        text = await element.input_value()
                        if text:
                            return text
                    except:
                        pass
                    
                    # 然后尝试get_attribute("value")作为补充
                    try:
                        text = await element.get_attribute("value")
                        return text if text else ""
                    except:
                        pass
                    
                    return ""
            except:
                pass
            
            # 6. 对于非输入框元素,根据可见性选择提取方法
            try:
                # 尝试使用inner_text()提取可见文本
                text = await element.inner_text()
                if text:
                    return text
            except:
                pass
            
            try:
                # 尝试使用text_content()提取所有文本(包括隐藏文本)
                text = await element.text_content()
                return text if text else ""
            except:
                pass
            
            return ""
        except Exception as e:
            print(f"从iframe提取文本时出错: {e}")
            try:
                # 7. 降级方案:再次尝试
                await self.page.wait_for_selector(iframe_selector, timeout=10000)
                iframe = self.page.frame_locator(iframe_selector)
                await iframe.locator(element_selector).wait_for(timeout=8000)
                element = iframe.locator(element_selector)
                
                # 尝试text_content()
                try:
                    text = await element.text_content()
                    if text:
                        return text
                except:
                    pass
                
                # 尝试inner_text()
                try:
                    text = await element.inner_text()
                    if text:
                        return text
                except:
                    pass
                
                # 尝试input_value()
                try:
                    text = await element.input_value()
                    if text:
                        return text
                except:
                    pass
                
                # 尝试get_attribute("value")
                try:
                    text = await element.get_attribute("value")
                    if text:
                        return text
                except:
                    pass
                
                return ""
            except Exception as e2:
                print(f"iframe降级方案提取时出错: {e2}")
                return ""
    
    async def extract_text_from_image(self, selector: str) -> str:
        """从图片中提取文本(OCR)"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 定位图片元素
            element = self.page.locator(selector)
            # 等待元素可见
            await element.wait_for(state='visible', timeout=10000)
            
            # 截取图片
            screenshot_path = f"temp_image_{int(time.time())}.png"
            await element.screenshot(path=screenshot_path)
            
            # 这里可以集成OCR库,如Tesseract或第三方API
            # 暂时返回占位符,实际项目中需要实现OCR逻辑
            print(f"图片已保存到: {screenshot_path}")
            print("OCR功能需要安装Tesseract或集成第三方OCR API")
            
            # 清理临时文件
            import os
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)
            
            return "OCR功能已触发(需要安装Tesseract或集成第三方API)"
        except Exception as e:
            print(f"从图片提取文本时出错: {e}")
            return ""
    
    async def get_element_attributes(self, selector: str) -> Dict[str, str]:
        """获取元素属性"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        attributes = {}
        try:
            # 等待元素可见
            await self.page.wait_for_selector(selector, state='visible', timeout=10000)
            # 获取元素的所有属性
            attrs = await self.page.evaluate(f"""
                (selector) => {{
                    const element = document.querySelector(selector);
                    if (!element) return {{}};
                    const attrs = {{}};
                    for (let attr of element.attributes) {{
                        attrs[attr.name] = attr.value;
                    }}
                    return attrs;
                }}
            """, selector)
            return attrs
        except:
            return {}
    
    async def get_all_links(self) -> List[Dict[str, str]]:
        """获取页面上所有链接"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        links = await self.page.evaluate("""
            () => {
                const elements = document.querySelectorAll('a[href]');
                return Array.from(elements).map(el => ({
                    text: el.textContent.trim(),
                    href: el.href,
                    title: el.title
                }));
            }
        """)
        return links
    
    async def extract_element_data(self, selector: str) -> Dict[str, Any]:
        """提取元素的各种数据"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            # 使用 locator() 定位元素,内置自动等待
            element = self.page.locator(selector)
            
            # 等待元素可见
            await element.wait_for(state='visible', timeout=10000)
            
            # 提取文本内容
            text_content = await element.text_content()
            inner_text = await element.inner_text()
            
            # 提取属性
            attributes = {
                'id': await element.get_attribute('id') or '',
                'className': await element.get_attribute('class') or '',
                'tagName': await element.evaluate('el => el.tagName') or '',
                'href': await element.get_attribute('href') or '',
                'src': await element.get_attribute('src') or '',
                'alt': await element.get_attribute('alt') or '',
                'title': await element.get_attribute('title') or '',
                'value': await element.get_attribute('value') or '',
                'placeholder': await element.get_attribute('placeholder') or '',
                'type': await element.get_attribute('type') or '',
                'name': await element.get_attribute('name') or '',
            }
            
            # 提取样式
            styles = {
                'display': await element.evaluate('el => getComputedStyle(el).display'),
                'visibility': await element.evaluate('el => getComputedStyle(el).visibility'),
                'opacity': await element.evaluate('el => getComputedStyle(el).opacity'),
            }
            
            # 提取位置信息
            bounding_box = await element.bounding_box()
            
            # 提取状态信息
            is_visible = await element.is_visible()
            is_enabled = await element.is_enabled()
            # 仅对复选框或单选按钮调用 is_checked()
            try:
                is_selected = await element.is_checked()
            except:
                is_selected = False
            
            return {
                'textContent': text_content.strip() if text_content else '',
                'innerText': inner_text.strip() if inner_text else '',
                'innerHTML': await element.inner_html() or '',
                'attributes': attributes,
                'styles': styles,
                'rect': bounding_box,
                'isVisible': is_visible,
                'isEnabled': is_enabled,
                'isSelected': is_selected
            }
        except Exception as e:
            print(f"提取元素数据时出错: {e}")
            return {}

    
    async def get_page_data(self) -> Dict[str, Any]:
        """获取页面的全面数据"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        page_data = await self.page.evaluate("""
            () => {
                return {
                    url: window.location.href,
                    title: document.title,
                    textContent: document.body ? document.body.textContent.trim() : '',
                    html: document.documentElement.outerHTML,
                    metaTags: Array.from(document.querySelectorAll('meta')).map(meta => ({
                        name: meta.name,
                        property: meta.property,
                        content: meta.content
                    })),
                    links: Array.from(document.querySelectorAll('a[href]')).length,
                    images: Array.from(document.querySelectorAll('img')).length,
                    forms: Array.from(document.querySelectorAll('form')).length,
                    inputs: Array.from(document.querySelectorAll('input, textarea, select')).length,
                    headings: {
                        h1: Array.from(document.querySelectorAll('h1')).map(el => el.textContent.trim()),
                        h2: Array.from(document.querySelectorAll('h2')).map(el => el.textContent.trim()),
                        h3: Array.from(document.querySelectorAll('h3')).map(el => el.textContent.trim()),
                    },
                    scripts: Array.from(document.querySelectorAll('script')).length,
                    stylesheets: Array.from(document.querySelectorAll('link[rel="stylesheet"]')).length
                };
            }
        """)
        
        return page_data
    
    async def analyze_page_content(self, selector: str = 'body') -> Dict[str, Any]:
        """分析页面内容"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            if selector == 'body':
                text_content = await self.page.inner_text('body')
            else:
                text_content = await self.page.inner_text(selector)
            
            # 分析文本内容
            words = text_content.split()
            word_count = len(words)
            char_count = len(text_content)
            
            # 提取所有链接
            links = await self.get_all_links()
            
            # 提取所有图片
            images = await self.page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('img')).map(img => ({
                        src: img.src,
                        alt: img.alt,
                        title: img.title
                    }));
                }
            """)
            
            analysis = {
                'textContent': text_content,
                'wordCount': word_count,
                'charCount': char_count,
                'links': links,
                'images': images,
                'summary': f"页面包含 {word_count} 个词, {len(links)} 个链接, {len(images)} 个图片"
            }
            
            return analysis
        except Exception as e:
            print(f"分析页面内容时出错: {e}")
            return {'error': str(e)}
    
    async def wait_for_element_visible(self, selector: str, timeout: int = 30000, selector_type: str = "css"):
        """等待元素可见"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            if selector_type == "xpath":
                element = self.page.locator(f"xpath={selector}")
                await element.wait_for(state="visible", timeout=timeout)
            else:
                await self.page.wait_for_selector(selector, state="visible", timeout=timeout)
            return True
        except:
            return False
    
    async def hover_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None):
        """悬停在元素上"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [HOVER_DEBUG] 开始悬停元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        # 悬停步骤通常不是必要的,设置较短的超时时间
        try:
            # 等待元素可见(减少超时时间到2秒)
            if hasattr(target_context, 'wait_for_selector'):
                # 对于page对象
                await target_context.wait_for_selector(full_selector, state='visible', timeout=2000)
                # 使用更健壮的悬停方式
                await target_context.hover(full_selector, timeout=2000)
            else:
                # 对于frame_locator对象
                element = target_context.locator(full_selector)
                await element.wait_for(state='visible', timeout=2000)
                # 使用更健壮的悬停方式
                await element.hover(timeout=2000)
            uat_logger.info(f"成功悬停元素: {selector}")
        except Exception as e:
            uat_logger.warning(f"悬停失败,这通常不影响执行: {str(e)}")
            # 悬停失败不影响后续操作,不尝试JavaScript模拟
        
        # 如果正在录制,记录悬停步骤
        if self.recording:
            step = {
                "action": "hover",
                "selector": selector,
                "selector_type": selector_type,
                "iframe_selector": iframe_selector,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            self.recorded_steps.append(step)
    
    async def double_click_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None):
        """双击元素"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [DOUBLE_CLICK_DEBUG] 开始双击元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        # 等待元素可见且可交互
        if hasattr(target_context, 'wait_for_selector'):
            # 对于page对象
            await target_context.wait_for_selector(full_selector, state='visible', timeout=10000)
            await target_context.dblclick(full_selector, timeout=10000)
        else:
            # 对于frame_locator对象
            element = target_context.locator(full_selector)
            await element.wait_for(state='visible', timeout=10000)
            await element.dblclick(timeout=10000)
        
        # 如果正在录制,记录双击步骤
        if self.recording:
            step = {
                "action": "double_click",
                "selector": selector,
                "selector_type": selector_type,
                "iframe_selector": iframe_selector,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            self.recorded_steps.append(step)
    
    async def right_click_element(self, selector: str, selector_type: str = "css", iframe_selector: str = None, iframe_context=None):
        """右键点击元素"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"🔍 [RIGHT_CLICK_DEBUG] 开始右键点击元素,选择器: {selector}, 选择器类型: {selector_type}, iframe选择器: {iframe_selector}")
        
        # 构建完整的选择器
        full_selector = selector
        if selector_type == "xpath":
            full_selector = f"xpath={selector}"
        
        # 确定操作上下文
        target_context = self.page
        if iframe_context:
            target_context = iframe_context
        elif iframe_selector:
            uat_logger.info(f"🔄 [IFRAME_DEBUG] 使用iframe上下文,选择器: {iframe_selector}")
            target_context = self.page.frame_locator(iframe_selector)
        
        # 等待元素可见且可交互
        if hasattr(target_context, 'wait_for_selector'):
            # 对于page对象
            await target_context.wait_for_selector(full_selector, state='visible', timeout=10000)
            await target_context.click(full_selector, button="right", timeout=10000)
        else:
            # 对于frame_locator对象
            element = target_context.locator(full_selector)
            await element.wait_for(state='visible', timeout=10000)
            await element.click(button="right", timeout=10000)
        
        # 如果正在录制,记录右键步骤
        if self.recording:
            step = {
                "action": "right_click",
                "selector": selector,
                "selector_type": selector_type,
                "iframe_selector": iframe_selector,
                "timestamp": int(time.time() * 1000)  # 转换为毫秒,与浏览器事件保持一致
            }
            self.recorded_steps.append(step)
    
    async def get_element_screenshot(self, selector: str, path: str = None):
        """截取特定元素的截图"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        if path is None:
            path = f"element_screenshot_{int(time.time())}.png"
        
        # 等待元素可见
        await self.page.wait_for_selector(selector, state='visible', timeout=10000)
        await self.page.locator(selector).screenshot(path=path)
        return path
    
    async def get_page_elements(self) -> List[Dict[str, Any]]:
        """获取页面上所有可交互元素的信息"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        elements = await self.page.evaluate("""
            () => {
                const elements = [];
                
                // 获取所有可交互的元素
                const selector = 'button, input, textarea, select, a, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="menuitem"], [role="tab"], [role="option"], [role="listbox"], [role="combobox"], [role="gridcell"], [role="treeitem"], [role="slider"], [role="progressbar"], [role="img"], [role="tooltip"], [role="dialog"], [role="alert"], [role="alertdialog"], [role="application"], [role="banner"], [role="complementary"], [role="contentinfo"], [role="form"], [role="main"], [role="navigation"], [role="region"], [role="search"], [role="status"], [role="tabpanel"], [role="timer"], [role="toolbar"], [role="tooltip"], [role="tree"], [role="treegrid"], [role="grid"], [role="gridcell"], [role="row"], [role="rowgroup"], [role="columnheader"], [role="rowheader"], [role="separator"], [role="presentation"], [role="none"], [tabindex], [onclick], [ondblclick], [onmousedown], [onmouseup], [onmouseover], [onmousemove], [onmouseout], [onkeypress], [onkeydown], [onkeyup], [class*="btn"], [class*="button"], [class*="input"], [class*="select"], [class*="click"], [class*="toggle"], [class*="menu"], [class*="nav"], [class*="link"], [class*="item"], [class*="option"], [class*="control"], [class*="field"], [class*="action"], [class*="trigger"], [id*="btn"], [id*="button"], [id*="input"], [id*="select"], [id*="click"], [id*="toggle"], [id*="menu"], [id*="nav"], [id*="link"], [id*="item"], [id*="option"], [id*="control"], [id*="field"], [id*="action"], [id*="trigger"]';
                
                const elementList = document.querySelectorAll(selector);
                
                for (let i = 0; i < elementList.length; i++) {
                    const el = elementList[i];
                    
                    // 获取元素的可见性
                    const isVisible = el.offsetParent !== null;
                    
                    // 跳过不可见的元素
                    if (!isVisible) continue;
                    
                    // 获取元素的边界框
                    const rect = el.getBoundingClientRect();
                    
                    // 获取各种可能的标识符
                    const id = el.id;
                    const classes = el.className;
                    const tagName = el.tagName.toLowerCase();
                    const textContent = el.textContent ? el.textContent.trim().substring(0, 50) : '';
                    const title = el.title;
                    const ariaLabel = el.getAttribute('aria-label');
                    const placeholder = el.placeholder;
                    const alt = el.alt;
                    
                    // 构建选择器
                    let selector = tagName;
                    if (id) selector += `#${id}`;
                    if (classes) {
                        const classList = classes.split(' ').filter(c => c !== '');
                        for (const cls of classList) {
                            if (cls) selector += `.${cls}`;
                        }
                    }
                    
                    elements.push({
                        selector: selector,
                        tagName: tagName,
                        id: id,
                        classes: classes,
                        textContent: textContent,
                        title: title,
                        ariaLabel: ariaLabel,
                        placeholder: placeholder,
                        alt: alt,
                        rect: {
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            top: rect.top,
                            right: rect.right,
                            bottom: rect.bottom,
                            left: rect.left
                        },
                        isVisible: isVisible
                    });
                }
                
                return elements;
            }
        """)
        
        return elements
    
    async def get_page_title(self) -> str:
        """获取页面标题"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        return await self.page.title()
    
    async def get_current_url(self) -> str:
        """获取当前URL"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        return self.page.url
    
    async def wait_for_selector(self, selector: str, timeout: int = 30000):
        """等待元素出现"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        await self.page.wait_for_selector(selector, timeout=timeout)
    
    async def get_element_count(self, selector: str) -> int:
        """获取指定选择器的元素数量"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        count = await self.page.evaluate(f"""
            () => {{
                return document.querySelectorAll('{selector}').length;
            }}
        """)
        return count
    
    async def take_screenshot(self, path: str = None):
        """截取页面截图"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        if path is None:
            path = f"screenshot_{int(time.time())}.png"
        
        await self.page.screenshot(path=path)
        return path
    
    async def execute_script_steps(self, steps: List[Dict[str, Any]]):
        """执行脚本步骤"""
        if self.page is None:
            await self.start_browser(headless=False)
        else:
            # 确保窗口最大化 - 使用与start_browser相同的Windows API策略
            try:
                # 调用Windows API获取真实屏幕尺寸
                user32 = ctypes.windll.user32
                avail_width = user32.GetSystemMetrics(78)  # SM_CXAVAILABLE
                avail_height = user32.GetSystemMetrics(79)  # SM_CYAVAILABLE
                
                uat_logger.info(f"脚本执行时获取的可用工作区尺寸: {avail_width}x{avail_height}")
                
                # 获取当前窗口大小信息用于验证
                viewport_size = await self.page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
                uat_logger.info(f"脚本执行时窗口大小: {viewport_size['width']}x{viewport_size['height']}")
            except Exception as e:
                uat_logger.warning(f"获取窗口大小信息时出错: {str(e)}")
        
        # 步骤去重逻辑
        if not steps:
            return []
            
        # 第一阶段:合并所有相同选择器的填充步骤(无论是否连续)
        # 创建一个字典存储每个选择器的最新填充值
        fill_values = {}
        all_steps = []
        
        # 遍历所有步骤,收集填充值和非填充步骤
        for step in steps:
            if step['action'] in ['fill', 'input']:
                selector = step.get('selector')
                if selector:
                    # 更新该选择器的最新填充值
                    fill_values[selector] = step
                    all_steps.append(step)  # 保留原始填充步骤用于执行顺序
            else:
                all_steps.append(step)
        
        # 第二阶段:合并连续的重复步骤和处理填充步骤
        deduplicated_steps = []
        last_step = None
        
        # 跟踪已处理的填充选择器
        processed_fills = set()
        
        # 跟踪所有已处理的点击步骤(用于处理非连续的重复点击)
        processed_clicks = {}
        
        uat_logger.info(f"开始步骤去重,原始步骤数: {len(all_steps)}")
        
        for step in all_steps:
            action = step.get('action')
            uat_logger.info(f"处理步骤: {action}, 详情: {step}")
            
            # 过滤悬停动作,不记录和执行
            if step['action'] == 'hover':
                uat_logger.info(f"跳过悬停步骤: {step.get('selector')}")
                continue
            
            if step['action'] in ['fill', 'input']:
                selector = step.get('selector')
                if selector:
                    # 如果该选择器已经处理过,跳过
                    if selector in processed_fills:
                        continue
                    
                    # 获取最新的填充值
                    if selector in fill_values:
                        latest_fill = fill_values[selector]
                        uat_logger.info(f"使用最新填充值: {selector} -> {latest_fill.get('text')}")
                        deduplicated_steps.append(latest_fill)
                        processed_fills.add(selector)
                    continue
            
            # 处理点击步骤 - 特殊处理单选框/复选框的重复点击
            if step['action'] == 'click':
                selector = step.get('selector')
                if selector:
                    # 检测是否为单选框或复选框相关选择器
                    # 更准确的检测方式:基于选择器和元素信息
                    is_radio = False
                    is_checkbox = False
                    
                    # 首先检查选择器中是否包含明确的单选框/复选框标识
                    selector_lower = selector.lower()
                    if 'radio' in selector_lower:
                        is_radio = True
                    elif 'checkbox' in selector_lower:
                        # 注意:有些单选框可能使用checkbox的样式或类名
                        # 对于这种情况,我们也将其视为单选框处理
                        # 因为用户通常不希望单选框被取消选择
                        is_radio = True
                        # is_checkbox = True
                    
                    # 移除动态类名,生成稳定的选择器用于比较
                    import re
                    stable_selector = selector
                    # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                    stable_selector = re.sub(r'\.(is-\w+)', '', stable_selector)
                    # 移除所有以el-开头的动态类(Element UI临时类名)
                    stable_selector = re.sub(r'\.(el-\w+-\w+)', '', stable_selector)
                    # 移除所有以has-开头的动态类
                    stable_selector = re.sub(r'\.(has-\w+)', '', stable_selector)
                    # 移除连续的空格和重复的>符号
                    stable_selector = re.sub(r'\s+', ' ', stable_selector)
                    stable_selector = re.sub(r'\s*>\s*', ' > ', stable_selector)
                    stable_selector = stable_selector.strip()
                    
                    # 特殊处理:如果选择器只剩下基础元素类型(如span、div),则保留原始选择器的前两个类名
                    if '.' not in stable_selector and selector.count('.') >= 2:
                        # 保留原始选择器的基础元素和前两个类名
                        parts = selector.split(' ')
                        new_parts = []
                        for part in parts:
                            if '.' in part:
                                # 提取元素类型和前两个类名
                                element_class_parts = part.split('.')
                                if len(element_class_parts) > 2:
                                    new_parts.append('.'.join(element_class_parts[:3]))
                                else:
                                    new_parts.append(part)
                            else:
                                new_parts.append(part)
                        stable_selector = ' '.join(new_parts)
                    
                    # 对于单选框:同一选择器的非连续重复点击应该被过滤
                    # 因为单选框点击一次就足够,重复点击会导致状态切换
                    if is_radio:
                        if stable_selector in processed_clicks:
                            uat_logger.info(f"跳过非连续的重复点击步骤(单选框): {selector}")
                            continue
                        # 记录已处理的单选框点击
                        processed_clicks[stable_selector] = True
                    
                    # 对于复选框:可以多次点击切换状态,所以不应该过滤重复点击
                    # 对于普通元素:也不应该过滤重复点击,因为用户可能需要多次点击
                    elif not is_checkbox:
                        # 记录已处理的点击,但不用于过滤,仅作参考
                        processed_clicks[stable_selector] = True
            
            # 处理其他类型的步骤
            if not last_step:
                deduplicated_steps.append(step)
                last_step = step
                uat_logger.info(f"添加第一个步骤: {action}")
                continue
            
            # 移除跳过submit后navigate事件的逻辑,确保所有步骤都按顺序执行
            
            uat_logger.info(f"上一步骤: {last_step['action']}, 当前步骤: {action}")
            
            # 跳过连续的重复步骤
            if last_step['action'] == step['action']:
                if step['action'] == 'navigate':
                    if last_step.get('url') == step.get('url'):
                        uat_logger.info(f"跳过重复导航步骤: {step.get('url')}")
                        continue
                elif step['action'] == 'click' or step['action'] == 'hover':
                    # 特殊处理:如果当前步骤是click,且下一个步骤是submit,则不跳过这个click
                    # 因为这个click可能是提交按钮的点击,需要保留
                    next_step_index = all_steps.index(step) + 1
                    next_step = all_steps[next_step_index] if next_step_index < len(all_steps) else None
                    if next_step and next_step['action'] == 'submit':
                        uat_logger.info(f"保留submit前的click操作: {step.get('selector')}")
                    elif last_step.get('selector') == step.get('selector'):
                        uat_logger.info(f"跳过重复{step['action']}步骤: {step.get('selector')}")
                        continue
                elif step['action'] == 'scroll':
                    if last_step.get('scrollPosition') == step.get('scrollPosition'):
                        uat_logger.info(f"跳过重复滚动步骤")
                        continue
            
            deduplicated_steps.append(step)
            last_step = step
            uat_logger.info(f"添加步骤到去重列表: {action}, 当前去重列表长度: {len(deduplicated_steps)}")
        
        uat_logger.info(f"步骤去重完成,去重后步骤数: {len(deduplicated_steps)}")
        
        results = []
        step_index = 0
        
        # 跟踪操作状态,强制执行顺序
        has_clicked = False
        has_submitted = False
        
        for step in deduplicated_steps:
            step_index += 1
            action = step.get("action")
            uat_logger.info(f"🎯 [STEP_DEBUG] ========== 开始执行步骤 {step_index}/{len(deduplicated_steps)} ==========")
            uat_logger.info(f"🎯 [STEP_DEBUG] 步骤类型: {action}, 详情: {step}")
            uat_logger.info(f"🎯 [STEP_DEBUG] 当前操作状态: has_clicked={has_clicked}, has_submitted={has_submitted}")
            
            # 获取当前页面状态
            try:
                current_url = self.page.url
                uat_logger.info(f"🎯 [STEP_DEBUG] 当前页面URL: {current_url}")
            except Exception as e:
                uat_logger.warning(f"🎯 [STEP_DEBUG] 获取当前URL失败: {str(e)}")
            
            try:
                # 强制检查:submit操作前必须先click
                if action == "submit":
                    if not has_clicked:
                        uat_logger.warning(f"⚠️ [FORCE_CHECK] submit操作前未检测到click,但继续执行(多案例执行模式)")
                        # 不再强制抛出异常,允许继续执行
                        # raise Exception(f"违反强制规则:submit操作前必须先click,但当前未检测到click操作")
                    else:
                        uat_logger.info(f"✅ [FORCE_CHECK] submit操作检查通过:已检测到click操作")
                
                # 强制检查:navigate操作前必须先submit(除非是第一个navigate操作)
                if action == "navigate" and step_index > 1:
                    if not has_submitted:
                        uat_logger.warning(f"⚠️ [FORCE_CHECK] navigate操作前未检测到submit,但继续执行(多案例执行模式)")
                        # 不再强制抛出异常,允许继续执行
                        # raise Exception(f"违反强制规则:navigate操作前必须先submit,但当前未检测到submit操作")
                    else:
                        uat_logger.info(f"✅ [FORCE_CHECK] navigate操作检查通过:已检测到submit操作")
                
                if action == "navigate":
                    url = step.get("url")
                    # 检查当前页面是否已经在目标URL上,避免重复导航
                    if self.page and self.page.url != url:
                        await self.navigate_to(url)
                        # 确保页面完全加载完成
                        if self.page:
                            uat_logger.info("导航后等待页面完全加载")
                            await self.page.wait_for_load_state('domcontentloaded', timeout=30000)
                            await self.page.wait_for_load_state('load', timeout=30000)
                    else:
                        uat_logger.info(f"页面已在目标URL上,跳过导航: {url}")
                elif action == "click":
                    selector = step.get("selector")
                    
                    # 尝试点击元素,如果失败则尝试处理动态选择器
                    click_success = False
                    
                    # 首先尝试原始选择器
                    try:
                        await self.click_element(selector, step.get("selector_type", "css"), step.get("iframe_selector"))
                        click_success = True
                    except Exception as e:
                        uat_logger.warning(f"原始选择器点击失败: {str(e)}")
                        
                        # 尝试使用更宽松的选择器(移除动态class)
                        if '.' in selector:
                            # 对于CSS选择器,尝试移除动态类名(如is-loading、is-focus等)
                            import re
                            # 保留基础元素类型和非动态类
                            # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                            base_selector = re.sub(r'\.(is-\w+)', '', selector)
                            # 移除所有以el-开头的动态类(Element UI临时类名)
                            base_selector = re.sub(r'\.(el-\w+-\w+)', '', base_selector)
                            # 移除所有以has-开头的动态类
                            base_selector = re.sub(r'\.(has-\w+)', '', base_selector)
                            # 移除连续的空格和重复的>符号
                            base_selector = re.sub(r'\s+', ' ', base_selector)
                            base_selector = re.sub(r'\s*>\s*', ' > ', base_selector)
                            base_selector = base_selector.strip()
                            
                            if base_selector != selector and base_selector.strip():
                                uat_logger.info(f"尝试使用更宽松的选择器: {base_selector}")
                                try:
                                    # 等待基础选择器的元素可见
                                    await self.page.wait_for_selector(base_selector, state='visible', timeout=5000)
                                    await self.page.click(base_selector, force=True, timeout=5000)
                                    uat_logger.info(f"使用宽松选择器成功点击元素: {base_selector}")
                                    click_success = True
                                except Exception as e2:
                                    uat_logger.warning(f"宽松选择器点击失败: {str(e2)}")
                                    
                        # 如果前面的尝试都失败,尝试更基础的选择器
                        if not click_success:
                            # 尝试仅使用标签名和ID
                            try:
                                import re
                                # 提取ID部分
                                id_match = re.search(r'#([\w-]+)', selector)
                                class_matches = re.findall(r'\.([\w-]+)', selector)
                                tag_match = re.match(r'([a-zA-Z]+)', selector)
                                
                                if id_match:
                                    basic_selector = f"#{id_match.group(1)}"
                                    uat_logger.info(f"尝试使用ID选择器: {basic_selector}")
                                    await self.page.wait_for_selector(basic_selector, state='visible', timeout=5000)
                                    await self.page.click(basic_selector, force=True, timeout=5000)
                                    uat_logger.info(f"使用ID选择器成功点击元素: {basic_selector}")
                                    click_success = True
                            except:
                                pass
                        
                        if not click_success:
                            # 如果所有尝试都失败,抛出异常
                            raise Exception(f"无法点击元素,所有选择器尝试均失败: {selector}")
                    
                    # 对于点击操作,根据元素类型执行适当的等待策略
                    if self.page:
                        try:
                            # 根据选择器判断元素类型,执行不同的等待策略
                            if 'input' in selector or 'textarea' in selector or 'select' in selector:
                                # 对于表单元素,等待一段时间让数据保存,但不等待页面加载
                                uat_logger.info("表单元素点击,等待数据保存完成")
                                await self.page.wait_for_timeout(300)
                            elif 'button' in selector or 'submit' in selector.lower():
                                # 对于按钮,先不进行导航检测,因为可能只是UI变化
                                uat_logger.info("按钮点击,等待UI响应")
                                await self.page.wait_for_timeout(300)
                            else:
                                # 对于其他元素,使用较短的等待时间
                                await self.page.wait_for_timeout(200)
                        except Exception as e:
                            uat_logger.warning(f"点击后等待时出错: {str(e)}")
                            # 发生错误时也继续执行
                elif action in ["fill", "input"]:
                    selector = step.get("selector")
                    text = step.get("text")
                    
                    # 尝试填充元素,如果失败则尝试处理动态选择器
                    fill_success = False
                    
                    # 首先尝试原始选择器
                    try:
                        await self.fill_input(selector, text, step.get("selector_type", "css"), step.get("iframe_selector"))
                        fill_success = True
                    except Exception as e:
                        uat_logger.warning(f"原始选择器填充失败: {str(e)}")
                        
                        # 尝试使用更宽松的选择器(移除动态class)
                        if '.' in selector:
                            import re
                            # 保留基础元素类型和非动态类
                            # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                            base_selector = re.sub(r'\.(is-\w+)', '', selector)
                            # 移除所有以el-开头的动态类(Element UI临时类名)
                            base_selector = re.sub(r'\.(el-\w+-\w+)', '', base_selector)
                            # 移除所有以has-开头的动态类
                            base_selector = re.sub(r'\.(has-\w+)', '', base_selector)
                            # 移除连续的空格和重复的>符号
                            base_selector = re.sub(r'\s+', ' ', base_selector)
                            base_selector = re.sub(r'\s*>\s*', ' > ', base_selector)
                            base_selector = base_selector.strip()
                            
                            if base_selector != selector and base_selector.strip():
                                uat_logger.info(f"尝试使用更宽松的选择器: {base_selector}")
                                try:
                                    await self.fill_input(base_selector, text)
                                    fill_success = True
                                except Exception as e2:
                                    uat_logger.warning(f"宽松选择器填充失败: {str(e2)}")
                                    
                        # 如果前面的尝试都失败,尝试更基础的选择器
                        if not fill_success:
                            # 尝试仅使用标签名和ID
                            try:
                                import re
                                # 提取ID部分
                                id_match = re.search(r'#([\w-]+)', selector)
                                class_matches = re.findall(r'\.([\w-]+)', selector)
                                tag_match = re.match(r'([a-zA-Z]+)', selector)
                                
                                if id_match:
                                    basic_selector = f"#{id_match.group(1)}"
                                    uat_logger.info(f"尝试使用ID选择器: {basic_selector}")
                                    await self.fill_input(basic_selector, text)
                                    fill_success = True
                            except:
                                pass
                        
                        if not fill_success:
                            # 如果所有尝试都失败,抛出异常
                            raise Exception(f"无法填充元素,所有选择器尝试均失败: {selector}")
                    
                    # 填充后等待一小段时间以确保值已设置,但不等待页面加载
                    if self.page:
                        await self.page.wait_for_timeout(300)
                        uat_logger.info(f"填充操作完成,等待值生效: {selector}")
                elif action == "scroll":
                    # 处理新的滚动格式
                    if "scrollPosition" in step:
                        scroll_pos = step.get("scrollPosition", {})
                        # 计算滚动距离和方向
                        current_scroll = {"x": 0, "y": 0}  # 默认值
                        if self.page is not None:
                            current_scroll = await self.page.evaluate("""
                                () => ({
                                    x: window.pageXOffset || document.documentElement.scrollLeft,
                                    y: window.pageYOffset || document.documentElement.scrollTop
                                })
                            """)
                        else:
                            uat_logger.warning("页面对象为None,无法获取滚动位置")
                        
                        delta_x = scroll_pos.get("x", 0) - current_scroll["x"]
                        delta_y = scroll_pos.get("y", 0) - current_scroll["y"]
                        
                        # 执行滚动
                        if self.page is not None:
                            await self.page.evaluate(f"window.scrollBy({delta_x}, {delta_y})")
                    else:
                        # 处理旧的滚动格式
                        direction = step.get("direction", "down")
                        pixels = step.get("pixels", 500)
                        await self.scroll_page(direction, pixels)
                    
                    # 移除滚动后的固定等待
                elif action == "hover":
                    # 悬停步骤通常不是必要的,跳过以提高执行速度
                    uat_logger.info(f"跳过悬停步骤: {step.get('selector')}")
                    # selector = step.get("selector")
                    # await self.hover_element(selector)
                    # await asyncio.sleep(0.2)
                elif action == "double_click":
                    selector = step.get("selector")
                    await self.double_click_element(selector)
                    # 移除双击后的固定等待
                elif action == "right_click":
                    selector = step.get("selector")
                    await self.right_click_element(selector)
                    # 移除右键点击后的固定等待
                elif action == "submit":
                    selector = step.get("selector")
                    uat_logger.info(f"🔍 [SUBMIT_DEBUG] 开始执行submit操作,选择器: {selector}")
                    
                    # 获取当前页面URL和状态
                    try:
                        current_url = self.page.url
                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 当前页面URL: {current_url}")
                    except Exception as e:
                        uat_logger.warning(f"🔍 [SUBMIT_DEBUG] 获取当前URL失败: {str(e)}")
                    
                    # 尝试提交表单,如果失败则尝试处理动态选择器
                    submit_success = False
                    
                    # 首先尝试原始选择器,直接点击提交按钮来触发表单提交
                    try:
                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试方式1: 原始选择器提交")
                        # 检查元素是否存在
                        element_exists = await self.page.evaluate("(selector) => document.querySelector(selector) !== null", selector)
                        if element_exists:
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 提交按钮存在,准备点击")
                            # 使用JavaScript点击提交按钮,触发表单提交
                            await self.page.evaluate("""(selector) => {
                                const element = document.querySelector(selector);
                                if (element) {
                                    // 直接点击提交按钮,触发表单提交
                                    element.click();
                                }
                            }""", selector)
                            uat_logger.info(f"✅ [SUBMIT_DEBUG] 方式1成功点击提交按钮")
                            submit_success = True
                        else:
                            uat_logger.error(f"❌ [SUBMIT_DEBUG] 提交按钮不存在: {selector}")
                    except Exception as e:
                        uat_logger.error(f"❌ [SUBMIT_DEBUG] 原始选择器提交失败: {str(e)}")
                        
                        # 尝试使用更宽松的选择器(移除动态class)
                        if '.' in selector:
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试方式2: 更宽松的选择器")
                            import re
                            # 保留基础元素类型和非动态类
                            # 移除所有以is-开头的动态类(如is-loading、is-focus、is-active等)
                            base_selector = re.sub(r'\.(is-\w+)', '', selector)
                            # 移除所有以el-开头的动态类(Element UI临时类名)
                            base_selector = re.sub(r'\.(el-\w+-\w+)', '', base_selector)
                            # 移除所有以has-开头的动态类
                            base_selector = re.sub(r'\.(has-\w+)', '', base_selector)
                            # 移除连续的空格和重复的>符号
                            base_selector = re.sub(r'\s+', ' ', base_selector)
                            base_selector = re.sub(r'\s*>\s*', ' > ', base_selector)
                            base_selector = base_selector.strip()
                            
                            if base_selector != selector and base_selector.strip():
                                uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试使用更宽松的选择器: {base_selector}")
                                try:
                                    # 使用JavaScript点击提交按钮
                                    element_exists = await self.page.evaluate("(selector) => document.querySelector(selector) !== null", base_selector)
                                    if element_exists:
                                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 宽松选择器元素存在,准备点击")
                                        await self.page.evaluate("""(selector) => {
                                            const element = document.querySelector(selector);
                                            if (element) {
                                                // 直接点击提交按钮,触发表单提交
                                                element.click();
                                            }
                                        }""", base_selector)
                                        uat_logger.info(f"✅ [SUBMIT_DEBUG] 方式2成功点击提交按钮")
                                        submit_success = True
                                    else:
                                        uat_logger.warning(f"⚠️ [SUBMIT_DEBUG] 宽松选择器元素不存在: {base_selector}")
                                except Exception as e2:
                                    uat_logger.warning(f"❌ [SUBMIT_DEBUG] 宽松选择器提交失败: {str(e2)}")
                                    
                        # 如果前面的尝试都失败,尝试更基础的选择器
                        if not submit_success:
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试方式3: 基础选择器")
                            # 尝试仅使用标签名和ID
                            try:
                                import re
                                # 提取ID部分
                                id_match = re.search(r'#([\w-]+)', selector)
                                class_matches = re.findall(r'\.([\w-]+)', selector)
                                tag_match = re.match(r'([a-zA-Z]+)', selector)
                                
                                if id_match:
                                    basic_selector = f"#{id_match.group(1)}"
                                    uat_logger.info(f"🔍 [SUBMIT_DEBUG] 尝试使用ID选择器: {basic_selector}")
                                    # 使用JavaScript点击提交按钮
                                    element_exists = await self.page.evaluate("(selector) => document.querySelector(selector) !== null", basic_selector)
                                    if element_exists:
                                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] ID选择器元素存在,准备点击")
                                        await self.page.evaluate("""(selector) => {
                                            const element = document.querySelector(selector);
                                            if (element) {
                                                // 直接点击提交按钮,触发表单提交
                                                element.click();
                                            }
                                        }""", basic_selector)
                                        uat_logger.info(f"✅ [SUBMIT_DEBUG] 方式3成功点击提交按钮")
                                        submit_success = True
                                    else:
                                        uat_logger.warning(f"⚠️ [SUBMIT_DEBUG] ID选择器元素不存在: {basic_selector}")
                            except Exception as e3:
                                uat_logger.warning(f"❌ [SUBMIT_DEBUG] 基础选择器提交失败: {str(e3)}")
                        
                        if not submit_success:
                            # 如果所有尝试都失败,抛出异常
                            uat_logger.error(f"❌ [SUBMIT_DEBUG] 所有提交方式均失败: {selector}")
                            raise Exception(f"无法提交表单,所有选择器尝试均失败: {selector}")
                        
                        uat_logger.info(f"✅ [SUBMIT_DEBUG] submit操作执行成功: {selector}")
                    
                    # 提交后等待一小段时间,确保表单提交事件被触发
                    if self.page:
                        uat_logger.info(f"🔍 [SUBMIT_DEBUG] 表单提交,等待一小段时间确保提交事件触发")
                        await self.page.wait_for_timeout(300)
                        
                        # 检查提交后的页面状态
                        try:
                            new_url = self.page.url
                            uat_logger.info(f"🔍 [SUBMIT_DEBUG] 提交后页面URL: {new_url}")
                            if new_url != current_url:
                                uat_logger.info(f"🔄 [SUBMIT_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                        except Exception as e:
                            uat_logger.warning(f"🔍 [SUBMIT_DEBUG] 获取提交后URL失败: {str(e)}")
                        
                        uat_logger.info(f"✅ [SUBMIT_DEBUG] submit操作完成")
                elif action == "keypress":
                    selector = step.get("selector")
                    key = step.get("key")
                    if selector:
                        await self.page.click(selector)  # 先点击确保焦点
                    # 如果没有selector,直接发送按键
                    await self.page.keyboard.press(key)
                    # 移除按键后的固定等待
                elif action == "wait":
                    wait_time = step.get("time", 1000)
                    await asyncio.sleep(wait_time / 1000)  # 转换为秒
                elif action == "wait_for_selector":
                    selector = step.get("selector")
                    timeout = step.get("timeout", 30000)
                    if selector:
                        await self.wait_for_selector(selector, timeout, step.get("selector_type", "css"), step.get("iframe_selector"))
                elif action == "wait_for_element_visible":
                    selector = step.get("selector")
                    timeout = step.get("timeout", 30000)
                    if selector:
                        await self.wait_for_element_visible(selector, timeout, step.get("selector_type", "css"), step.get("iframe_selector"))
                elif action == "screenshot":
                    # 截取页面截图
                    await self.take_screenshot()
                elif action == "extract_text":
                    selector = step.get("selector")
                    uat_logger.info(f"🔍 [EXTRACT_TEXT_DEBUG] 开始执行提取文本操作,选择器: {selector}")
                    
                    try:
                        if selector:
                            # 提取元素文本
                            extracted_text = await self.extract_element_text(selector, step.get("selector_type", "css"), step.get("iframe_selector"))
                            uat_logger.info(f"✅ [EXTRACT_TEXT_DEBUG] 提取到文本: {extracted_text[:100]}...")
                            # 标记为成功
                            step_status = "success"
                            step_extracted_text = extracted_text
                        else:
                            # 提取整个页面文本
                            extracted_text = await self.get_page_text()
                            uat_logger.info(f"✅ [EXTRACT_TEXT_DEBUG] 提取到页面文本: {extracted_text[:100]}...")
                            # 标记为成功
                            step_status = "success"
                            step_extracted_text = extracted_text
                    except Exception as e:
                        uat_logger.error(f"❌ [EXTRACT_TEXT_DEBUG] 提取文本失败: {str(e)}")
                        step_status = "error"
                        step_error = str(e)
                        step_extracted_text = ""
                    
                    # 等待页面状态稳定
                    if self.page:
                        try:
                            # 等待页面稳定,确保上一步操作完成
                            uat_logger.info(f"等待步骤完成: {action}")
                            
                            # 检查页面是否正在加载
                            try:
                                # 等待页面加载状态稳定(最多等待2秒)
                                await self.page.wait_for_load_state('domcontentloaded', timeout=2000)
                            except:
                                pass  # 页面可能已经加载完成
                            
                            # 等待一小段时间,让页面状态稳定
                            await self.page.wait_for_timeout(500)
                            
                            # 检查是否有正在进行的网络请求
                            try:
                                # 等待网络空闲(最多等待3秒)
                                await self.page.wait_for_load_state('networkidle', timeout=3000)
                            except:
                                pass  # 网络可能一直有活动
                            
                            uat_logger.info(f"步骤完成: {action}")
                        except Exception as e:
                            uat_logger.warning(f"等待页面稳定时出错: {str(e)}")
                            # 即使等待失败,也继续执行后续步骤
                    
                    # 检查步骤执行后的页面状态
                    try:
                        new_url = self.page.url
                        uat_logger.info(f"🎯 [STEP_DEBUG] 步骤执行后页面URL: {new_url}")
                        if new_url != current_url:
                            uat_logger.info(f"🔄 [STEP_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                    except Exception as e:
                        uat_logger.warning(f"🎯 [STEP_DEBUG] 获取步骤执行后URL失败: {str(e)}")
                    
                    uat_logger.info(f"✅ [STEP_DEBUG] ========== 步骤 {step_index}/{len(deduplicated_steps)} 执行成功 ==========")
                    
                    # 添加到结果中
                    if step_status == "success":
                        result = {"status": "success", "step": step}
                        if step_extracted_text:
                            result["extracted_text"] = step_extracted_text
                        results.append(result)
                    else:
                        results.append({"status": "error", "step": step, "error": step_error})
                    
                    # 跳过后续的通用处理
                    continue
                if self.page:
                    try:
                        # 等待页面稳定,确保上一步操作完成
                        uat_logger.info(f"等待步骤完成: {action}")
                        
                        # 检查页面是否正在加载
                        try:
                            # 等待页面加载状态稳定(最多等待2秒)
                            await self.page.wait_for_load_state('domcontentloaded', timeout=2000)
                        except:
                            pass  # 页面可能已经加载完成
                        
                        # 等待一小段时间,让页面状态稳定
                        await self.page.wait_for_timeout(500)
                        
                        # 检查是否有正在进行的网络请求
                        try:
                            # 等待网络空闲(最多等待3秒)
                            await self.page.wait_for_load_state('networkidle', timeout=3000)
                        except:
                            pass  # 网络可能一直有活动
                        
                        uat_logger.info(f"步骤完成: {action}")
                    except Exception as e:
                        uat_logger.warning(f"等待页面稳定时出错: {str(e)}")
                        # 即使等待失败,也继续执行后续步骤
                
                # 检查步骤执行后的页面状态
                try:
                    new_url = self.page.url
                    uat_logger.info(f"🎯 [STEP_DEBUG] 步骤执行后页面URL: {new_url}")
                    if new_url != current_url:
                        uat_logger.info(f"🔄 [STEP_DEBUG] 检测到页面URL变化: {current_url} -> {new_url}")
                except Exception as e:
                    uat_logger.warning(f"🎯 [STEP_DEBUG] 获取步骤执行后URL失败: {str(e)}")
                
                uat_logger.info(f"✅ [STEP_DEBUG] ========== 步骤 {step_index}/{len(deduplicated_steps)} 执行成功 ==========")
                results.append({"status": "success", "step": step})
                
                # 更新操作状态
                if action == "click":
                    has_clicked = True
                    uat_logger.info(f"🔄 [STATE_UPDATE] 已执行click操作,更新状态: has_clicked=True")
                elif action == "submit":
                    has_submitted = True
                    uat_logger.info(f"🔄 [STATE_UPDATE] 已执行submit操作,更新状态: has_submitted=True")
            except Exception as e:
                uat_logger.error(f"❌ [STEP_DEBUG] ========== 步骤 {step_index}/{len(deduplicated_steps)} 执行失败 ==========")
                uat_logger.error(f"❌ [STEP_DEBUG] 错误详情: {str(e)}")
                results.append({"status": "error", "step": step, "error": str(e)})
        
        uat_logger.info(f"🎯 [STEP_DEBUG] ========== 所有步骤执行完成,共 {len(results)} 个步骤 ==========")
        return results
    
    async def execute_multiple_test_cases(self, case_ids: List[int], db) -> Dict[str, Any]:
        """执行多个测试用例
        
        Args:
            case_ids: 测试用例ID列表
            db: 数据库实例,用于获取测试用例步骤
            
        Returns:
            包含所有测试用例执行结果的字典
        """
        uat_logger.info(f"🚀 [MULTI_CASE] ========== 开始执行多个测试用例,共 {len(case_ids)} 个用例 ==========")
        
        all_results = {
            "total_cases": len(case_ids),
            "successful_cases": 0,
            "failed_cases": 0,
            "case_results": []
        }
        
        # 确保浏览器已启动
        if self.page is None:
            await self.start_browser(headless=False)
        
        for case_index, case_id in enumerate(case_ids):
            case_number = case_index + 1
            uat_logger.info(f"🎯 [MULTI_CASE] ========== 开始执行第 {case_number}/{len(case_ids)} 个测试用例,ID: {case_id} ==========")
            
            try:
                # 从数据库获取测试用例信息
                case_info = db.get_test_case_v2(case_id)
                if not case_info:
                    uat_logger.error(f"❌ [MULTI_CASE] 测试用例不存在,ID: {case_id}")
                    all_results["case_results"].append({
                        "case_id": case_id,
                        "case_name": "未知",
                        "status": "error",
                        "error": f"测试用例不存在,ID: {case_id}"
                    })
                    all_results["failed_cases"] += 1
                    continue
                
                case_name = case_info.get("name", "未命名用例")
                uat_logger.info(f"📋 [MULTI_CASE] 测试用例名称: {case_name}")
                
                # 从数据库获取测试用例的所有步骤
                steps = db.get_case_steps(case_id)
                uat_logger.info(f"📋 [MULTI_CASE] 获取到 {len(steps)} 个测试步骤")
                
                if not steps:
                    uat_logger.warning(f"⚠️ [MULTI_CASE] 测试用例没有步骤,ID: {case_id}")
                    all_results["case_results"].append({
                        "case_id": case_id,
                        "case_name": case_name,
                        "status": "warning",
                        "warning": "测试用例没有步骤"
                    })
                    continue
                
                # 将数据库步骤格式转换为执行脚本所需的格式
                execution_steps = []
                for step in steps:
                    exec_step = {
                        "action": step["action"]
                    }
                    
                    # 根据不同的操作类型添加相应的参数
                    if step["action"] == "click":
                        exec_step["selector"] = step["selector_value"]
                        exec_step["selector_type"] = step.get("selector_type", "css")
                        exec_step["iframe_selector"] = step.get("iframe_selector")
                    elif step["action"] in ["fill", "input"]:
                        exec_step["selector"] = step["selector_value"]
                        exec_step["text"] = step["input_value"]
                        exec_step["selector_type"] = step.get("selector_type", "css")
                        exec_step["iframe_selector"] = step.get("iframe_selector")
                    elif step["action"] == "submit":
                        exec_step["selector"] = step["selector_value"]
                        exec_step["selector_type"] = step.get("selector_type", "css")
                        exec_step["iframe_selector"] = step.get("iframe_selector")
                    elif step["action"] == "navigate":
                        exec_step["url"] = step["url"] or step["input_value"]
                    elif step["action"] == "keypress":
                        exec_step["key"] = step["input_value"]
                    elif step["action"] == "wait":
                        try:
                            exec_step["time"] = int(step["input_value"])
                        except:
                            exec_step["time"] = 1000
                    elif step["action"] in ["wait_for_selector", "wait_for_element_visible"]:
                        exec_step["selector"] = step["selector_value"]
                        exec_step["selector_type"] = step.get("selector_type", "css")
                        exec_step["iframe_selector"] = step.get("iframe_selector")
                        try:
                            exec_step["timeout"] = int(step["input_value"])
                        except:
                            exec_step["timeout"] = 30000
                    elif step["action"] == "extract_text":
                        exec_step["selector"] = step["selector_value"]
                        exec_step["selector_type"] = step.get("selector_type", "css")
                        exec_step["iframe_selector"] = step.get("iframe_selector")
                    
                    # 添加描述信息
                    if step["description"]:
                        exec_step["description"] = step["description"]
                    
                    execution_steps.append(exec_step)
                
                uat_logger.info(f"🔄 [MULTI_CASE] 转换后的执行步骤数: {len(execution_steps)}")
                
                # 执行测试用例的步骤
                case_results = await self.execute_script_steps(execution_steps)
                
                # 统计执行结果
                success_count = sum(1 for r in case_results if r.get("status") == "success")
                error_count = sum(1 for r in case_results if r.get("status") == "error")
                
                # 提取文本(从所有步骤中收集,使用最后一个提取的文本)
                extracted_text = ""
                for r in case_results:
                    if r.get("extracted_text"):
                        extracted_text = r.get("extracted_text")
                # 移除了 break,现在会使用最后一个提取的文本
                
                case_status = "success" if error_count == 0 else "error"
                
                uat_logger.info(f"✅ [MULTI_CASE] 测试用例执行完成: {case_name}")
                uat_logger.info(f"📊 [MULTI_CASE] 成功步骤: {success_count}, 失败步骤: {error_count}")
                if extracted_text:
                    uat_logger.info(f"📝 [MULTI_CASE] 提取的文本: {extracted_text[:100]}...")
                
                # 记录测试用例执行结果到数据库
                try:
                    db.create_run_history(
                        case_id,
                        case_status,
                        0,  # 暂时设置为0,后续可以计算实际执行时间
                        "" if case_status == "success" else str(case_results),
                        extracted_text
                    )
                    uat_logger.info(f"📋 [MULTI_CASE] 测试结果已保存到数据库")
                except Exception as db_error:
                    uat_logger.error(f"❌ [MULTI_CASE] 保存测试结果到数据库失败: {db_error}")
                
                # 记录测试用例执行结果
                all_results["case_results"].append({
                    "case_id": case_id,
                    "case_name": case_name,
                    "status": case_status,
                    "total_steps": len(case_results),
                    "successful_steps": success_count,
                    "failed_steps": error_count,
                    "extracted_text": extracted_text,
                    "step_results": case_results
                })
                
                if case_status == "success":
                    all_results["successful_cases"] += 1
                else:
                    all_results["failed_cases"] += 1
                
                # 在测试用例之间添加短暂的等待,确保页面状态稳定
                if case_index < len(case_ids) - 1:
                    uat_logger.info(f"⏳ [MULTI_CASE] 等待 2 秒后执行下一个测试用例")
                    await asyncio.sleep(2)
                
            except Exception as e:
                uat_logger.error(f"❌ [MULTI_CASE] 测试用例执行异常,ID: {case_id}, 错误: {str(e)}")
                all_results["case_results"].append({
                    "case_id": case_id,
                    "case_name": case_info.get("name", "未命名用例") if 'case_info' in locals() else "未知",
                    "status": "error",
                    "error": str(e)
                })
                all_results["failed_cases"] += 1
        
        uat_logger.info(f"🎉 [MULTI_CASE] ========== 所有测试用例执行完成 ==========")
        uat_logger.info(f"📊 [MULTI_CASE] 总用例数: {all_results['total_cases']}, 成功: {all_results['successful_cases']}, 失败: {all_results['failed_cases']}")
        
        return all_results
    
    async def start_recording(self):
        """开始录制"""
        self.recording = True
        self.recorded_steps = []
        
        # 确保页面上有事件监听器来捕获用户操作
        if self.page:
            await self._setup_event_listeners()
            uat_logger.info("录制已开始,事件监听器已设置")
        else:
            uat_logger.warning("页面对象为None,无法设置事件监听器")
        
        # 不启动后台任务,因为这会导致事件循环冲突
        # 我们将在stop_recording时一次性获取所有事件
        uat_logger.info("录制已开始,事件将在停止录制时获取")
    
    def _get_and_process_events(self):
        """获取并处理浏览器中的事件"""
        # 使用线程安全的方式来获取和处理事件
        import asyncio
        import threading
        from queue import Queue
        
        result_queue = Queue()
        
        def get_events():
            async def run_get_events():
                return await self.get_recorded_events()
            
            try:
                # 创建新的事件循环来运行异步操作
                return asyncio.run(run_get_events())
            except Exception as e:
                print(f"获取事件时出错: {e}")
                return []
        
        def run_in_thread():
            try:
                events = get_events()
                result_queue.put(('success', events))
            except Exception as e:
                result_queue.put(('error', e))
        
        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join(timeout=2)  # 2秒超时
        
        if result_queue.empty():
            print("获取事件超时")
            return 0
        
        status, events = result_queue.get()
        if status == 'error':
            print(f"获取事件时出错: {events}")
            return 0
        else:
            print(f"获取到 {len(events)} 个浏览器事件")
            # 将浏览器中的事件转换为录制步骤格式
            for event in events:
                        step = {
                            "action": event.get('action'),
                            "timestamp": event.get('timestamp')
                        }
                        
                        if event.get('action') == 'click':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'fill':
                            step['selector'] = event.get('selector')
                            step['text'] = event.get('text', '')
                        elif event.get('action') == 'navigate':
                            step['url'] = event.get('url')
                        elif event.get('action') == 'scroll':
                            step['scrollPosition'] = event.get('scrollPosition')
                        elif event.get('action') == 'hover':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'double_click':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'right_click':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'submit':
                            step['selector'] = event.get('selector')
                        
                        # 添加到录制步骤中
                        self.recorded_steps.append(step)
            
            return len(events)
    
    async def _sync_events_periodically(self):
        """定期同步浏览器事件的后台任务"""
        while self.recording:
            try:
                # 检查页面是否仍然可用
                if not self.page or (hasattr(self.page, 'is_closed') and self.page.is_closed()):
                    print("页面已关闭,停止同步事件")
                    break
                await self.sync_recorded_events()
                # 每秒同步一次
                await asyncio.sleep(1)
            except Exception as e:
                print(f"同步事件时出错: {e}")
                # 出错时也等待一秒再继续
                await asyncio.sleep(1)
    
    async def stop_recording(self) -> List[Dict[str, Any]]:
        """停止录制并返回录制的步骤"""
        self.recording = False
        
        # 在关闭浏览器前,先获取浏览器中记录的所有事件
        if self.page:
            try:
                # 检查页面是否仍然可用
                if not hasattr(self.page, 'is_closed') or not self.page.is_closed():
                    # 直接获取浏览器中剩余的所有事件
                    events = await self.get_recorded_events()
                    uat_logger.info(f"停止录制时获取到 {len(events)} 个浏览器事件")
                    
                    # 将浏览器中的事件转换为录制步骤格式
                    for event in events:
                        step = {
                            "action": event.get('action'),
                            "timestamp": event.get('timestamp')
                        }
                        
                        if event.get('action') == 'click':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'fill':
                            step['selector'] = event.get('selector')
                            step['text'] = event.get('text', '')
                        elif event.get('action') == 'navigate':
                            step['url'] = event.get('url')
                        elif event.get('action') == 'scroll':
                            step['scrollPosition'] = event.get('scrollPosition')
                        elif event.get('action') == 'hover':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'double_click':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'right_click':
                            step['selector'] = event.get('selector')
                        elif event.get('action') == 'submit':
                            step['selector'] = event.get('selector')
                        
                        # 记录事件
                        uat_logger.log_browser_event(event.get('action', 'unknown'), event)
                        
                        # 应用去重逻辑
                        if self.recorded_steps:
                            last_step = self.recorded_steps[-1]
                            
                            # 特殊处理:click和submit事件都需要保留,不要跳过
                            # 因为回放时需要先点击按钮,再提交表单
                            
                            # 重新获取上一步骤
                            if self.recorded_steps:
                                last_step = self.recorded_steps[-1]
                            
                            # 关键修复:过滤掉submit事件后的navigate事件
                            # 因为submit操作本身就会导致页面导航,不需要额外的navigate步骤
                            if step['action'] == 'navigate' and last_step['action'] == 'submit':
                                time_diff = step.get('timestamp', 0) - last_step.get('timestamp', 0)
                                if time_diff < 3000:  # 3秒内的navigate事件都认为是submit导致的
                                    uat_logger.info(f"🚫 [NAV_FILTER] 过滤掉submit后的navigate事件,时间差: {time_diff}ms")
                                    continue
                            
                            # 检查是否与上一步骤完全相同
                            if last_step['action'] == step['action']:
                                # 计算时间差(毫秒)
                                time_diff = step.get('timestamp', 0) - last_step.get('timestamp', 0)
                                
                                # 对于导航步骤,检查URL是否相同且时间间隔小于2秒
                                if step['action'] == 'navigate' and last_step.get('url') == step.get('url') and time_diff < 2000:
                                    continue  # 跳过短时间内重复的导航步骤
                                # 对于点击步骤,检查选择器是否相同且时间间隔小于1秒
                                elif step['action'] == 'click' and last_step.get('selector') == step.get('selector') and time_diff < 1000:
                                    continue  # 跳过短时间内重复的点击步骤
                                # 对于悬停步骤,检查选择器是否相同且时间间隔小于1秒
                                elif step['action'] == 'hover' and last_step.get('selector') == step.get('selector') and time_diff < 1000:
                                    continue  # 跳过短时间内重复的悬停步骤
                                # 对于填充步骤,检查选择器和文本是否相同且时间间隔小于2秒
                                elif step['action'] == 'fill' and last_step.get('selector') == step.get('selector') and last_step.get('text') == step.get('text') and time_diff < 2000:
                                    continue  # 跳过短时间内重复的填充步骤
                                # 对于按键步骤,检查选择器和按键是否相同且时间间隔小于1秒
                                elif step['action'] == 'keypress' and last_step.get('selector') == step.get('selector') and last_step.get('key') == step.get('key') and time_diff < 1000:
                                    continue  # 跳过短时间内重复的按键步骤
                                # 对于提交步骤,检查选择器是否相同且时间间隔小于1秒
                                elif step['action'] == 'submit' and last_step.get('selector') == step.get('selector') and time_diff < 1000:
                                    continue  # 跳过短时间内重复的提交步骤
                                # 对于滚动步骤,检查滚动位置是否基本相同且时间间隔小于1秒
                                elif step['action'] == 'scroll' and last_step.get('scrollPosition') == step.get('scrollPosition') and time_diff < 1000:
                                    continue  # 跳过短时间内重复的滚动步骤
                        
                        # 添加到录制步骤中
                        self.recorded_steps.append(step)
            except Exception as e:
                uat_logger.log_exception("stop_recording", e)
        
        return self.recorded_steps
    
    def _get_recorded_events_sync(self):
        """同步获取录制的事件"""
        # 为了避免事件循环冲突,直接返回空列表
        # 实际的事件同步已经在后台任务中完成
        return []
    
    async def wait_for_timeout(self, milliseconds: int):
        """等待指定的毫秒数"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        uat_logger.info(f"等待 {milliseconds} 毫秒")
        await self.page.wait_for_timeout(milliseconds)
    
    async def close_browser(self):
        """关闭浏览器"""
        # 设置recording为False以停止任何可能的循环
        self.recording = False
        
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass  # 忽略错误
            self.browser = None
            self.page = None
            self.context = None
        
        if hasattr(self, 'playwright') and self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass  # 忽略错误
            self.playwright = None
    
    async def enable_element_selection(self, url=''):
        """启用元素选择模式,显示悬浮窗让用户选择页面元素"""
        try:
            # 检查浏览器实例是否有效
            browser_valid = False
            
            # 1. 检查browser对象是否存在且已连接
            browser_connected = False
            if self.browser:
                try:
                    browser_connected = self.browser.is_connected()
                except:
                    browser_connected = False
            
            # 2. 如果浏览器已连接,检查page对象是否有效
            if browser_connected and self.page:
                try:
                    # 尝试执行一个简单的操作来检查页面是否仍然有效
                    await self.page.evaluate("1 + 1")
                    browser_valid = True
                except Exception as e:
                    uat_logger.warning(f"页面对象已失效: {str(e)}")
                    # 重置浏览器相关状态
                    self.page = None
                    self.context = None
            
            # 3. 如果浏览器未连接或页面无效,重置所有浏览器相关状态
            if not browser_valid:
                uat_logger.warning(f"浏览器实例无效,重置所有相关状态")
                # 尝试优雅关闭playwright
                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except:
                        pass
                # 重置所有浏览器相关状态
                self.browser = None
                self.page = None
                self.context = None
                self.playwright = None
            
            # 4. 启动或复用浏览器实例
            if not browser_valid:
                # 如果浏览器实例不存在或已失效,则启动新实例
                uat_logger.info("启动新的浏览器实例")
                await self.start_browser()
            else:
                # 复用已存在的浏览器实例,切换到当前页面
                uat_logger.info("复用已存在的浏览器实例")
                # 确保页面已加载
                await self.page.wait_for_load_state('networkidle')
            
            # 如果提供了URL,则导航到该URL
            if url:
                await self.page.goto(url)
                await self.page.wait_for_load_state('networkidle')
            
            uat_logger.info("元素选择模式已启用")
            return True
        except Exception as e:
            uat_logger.error(f"启用元素选择模式时出错: {str(e)}")
            raise Exception(f"启用元素选择模式失败: {str(e)}")

    async def disable_element_selection(self):
        """禁用元素选择模式"""
        if self.page is None:
            return False
        
        try:
            await self.page.evaluate("""
                (() => {
                    if (typeof disableElementSelection === 'function') {
                        disableElementSelection();
                    }
                })
            """)
            
            uat_logger.info("元素选择模式已禁用")
            return True
        except Exception as e:
            uat_logger.error(f"禁用元素选择模式时出错: {str(e)}")
            return False

    async def get_selected_element(self):
        """获取用户选择的元素信息"""
        if self.page is None:
            return None
        
        try:
            # 获取页面标题,用于填充页面名称
            page_name = await self.page.title()
            
            # 等待元素选择事件
            raw_element_info = await self.page.evaluate("""
                (() => {
                    return new Promise((resolve) => {
                        // 检查是否已经有选中的元素
                        if (window.automationSelection && window.automationSelection.selectedElement) {
                            const element = window.automationSelection.selectedElement;
                            const selector = generateSelector(element);
                            resolve({
                                selector: selector,
                                elementInfo: {
                                    tagName: element.tagName,
                                    id: element.id || '',
                                    className: element.className || '',
                                    textContent: element.textContent ? element.textContent.substring(0, 100) : '',
                                    attributes: {
                                        type: element.type || '',
                                        name: element.name || '',
                                        value: element.value || '',
                                        href: element.href || '',
                                        src: element.src || '',
                                        alt: element.alt || '',
                                        title: element.title || ''
                                    }
                                }
                            });
                        } else {
                            // 监听元素选择事件
                            window.addEventListener('elementSelected', function handler(e) {
                                window.removeEventListener('elementSelected', handler);
                                resolve(e.detail);
                            });
                        }
                    });
                })
            """)
            
            if raw_element_info:
                # 处理原始元素信息,转换为前端期望的格式
                element = raw_element_info.get('elementInfo', {})
                css_selector = raw_element_info.get('selector', '')
                text_content = element.get('textContent', '').strip()
                
                # 选择最合适的定位方式
                selector_type = 'css'
                selector_value = css_selector
                
                # 如果有ID,优先使用ID选择器
                element_id = element.get('id', '')
                if element_id:
                    selector_type = 'id'
                    selector_value = element_id
                # 如果有data-testid属性,优先使用testid
                elif element.get('attributes', {}).get('data-testid'):
                    selector_type = 'testid'
                    selector_value = element.get('attributes', {}).get('data-testid')
                # 如果是文本内容比较独特,使用文本选择器
                elif text_content and len(text_content) > 5:
                    selector_type = 'text'
                    selector_value = text_content
                
                # 构造前端期望的返回格式
                formatted_element_info = {
                    'selector_type': selector_type,
                    'selector_value': selector_value,
                    'text_content': text_content,
                    'page_name': page_name,
                    'tag_name': element.get('tagName', '').lower(),
                    'css_selector': css_selector,
                    'id': element_id,
                    'class_name': element.get('className', '')
                }
                
                uat_logger.info(f"获取到格式化的选中元素: {formatted_element_info}")
                return formatted_element_info
            return None
        except Exception as e:
            uat_logger.error(f"获取选中元素信息时出错: {str(e)}")
            raise Exception(f"获取选中元素信息失败: {str(e)}")

    async def extract_json_from_selected_element(self):
        """从用户选定的区域提取JSON数据"""
        if self.page is None:
            raise Exception("浏览器未启动")
        
        try:
            uat_logger.info("📝 [JSON_EXTRACT] 开始从选定元素提取JSON数据")
            
            # 获取用户选择的元素
            selected_element_info = await self.get_selected_element()
            if not selected_element_info:
                raise Exception("未选择任何元素")
            
            # 获取选中元素的选择器
            selector = selected_element_info.get('css_selector')
            if not selector:
                raise Exception("无法获取选中元素的选择器")
            
            uat_logger.info(f"📝 [JSON_EXTRACT] 从元素选择器提取JSON: {selector}")
            
            # 使用现有的extract_element_json方法从选定元素中提取JSON数据
            json_data = await self.extract_element_json(selector)
            
            if json_data:
                uat_logger.info(f"📝 [JSON_EXTRACT] 成功提取JSON数据: {json_data}")
                return json_data
            else:
                uat_logger.warning("📝 [JSON_EXTRACT] 未从选定元素中提取到JSON数据")
                return {}
                
        except Exception as e:
            uat_logger.error(f"📝 [JSON_EXTRACT] 提取JSON数据时出错: {str(e)}")
            raise Exception(f"提取JSON数据失败: {str(e)}")

# 全局实例
automation = PlaywrightAutomation()

# 添加一个函数来重置自动化实例,确保每次录制都是干净的开始
def reset_automation_instance():
    """重置自动化实例,确保干净的状态"""
    global automation
    global worker
    # 关闭当前实例的浏览器
    try:
        if automation.browser:
            automation.close_browser()
    except:
        pass  # 忽略错误
    
    # 停止并重新创建工作线程
    try:
        if worker:
            worker.stop()
    except:
        pass  # 忽略错误
    
    # 创建新的工作线程实例
    worker = PlaywrightWorker()
    
    # 创建新的自动化实例
    automation = PlaywrightAutomation()
    return automation

# 同步包装器函数
# 使用一个全局事件循环来避免重复创建
import threading
import queue

# 创建一个专门的线程池来处理Playwright操作
class PlaywrightWorker:
    def __init__(self):
        self.task_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.worker_thread = None
        self.loop = None
        self.running = False
        self._start_worker()
    
    def _start_worker(self):
        """启动工作线程"""
        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        # 等待线程完全启动
        time.sleep(0.1)
    
    def _worker_loop(self):
        """工作线程的主循环"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        while self.running:
            try:
                # 获取任务,超时1秒
                task = self.task_queue.get(timeout=1)
                task_id, func, args, kwargs = task
                
                try:
                    # 检查是否是协程函数
                    if asyncio.iscoroutinefunction(func):
                        # 在事件循环中执行异步函数
                        result = self.loop.run_until_complete(func(*args, **kwargs))
                    else:
                        # 执行同步函数
                        result = func(*args, **kwargs)
                    
                    self.result_queue.put((task_id, "success", result))
                except Exception as e:
                    # 获取完整的异常信息
                    import traceback
                    exc_info = traceback.format_exc()
                    self.result_queue.put((task_id, "error", {"message": str(e), "traceback": exc_info}))
                
                self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                import traceback
                exc_info = traceback.format_exc()
                print(f"工作线程错误: {e}\n{exc_info}")
    
    def execute(self, func, *args, **kwargs):
        """在工作线程中执行函数"""
        # 确保工作线程已启动
        if not self.running or self.worker_thread is None or not self.worker_thread.is_alive():
            self._start_worker()
        
        task_id = str(time.time()) + str(id(func))
        self.task_queue.put((task_id, func, args, kwargs))
        
        # 等待结果
        while True:
            try:
                # 增加超时时间到10分钟(600秒),以支持长脚本执行
                tid, status, result = self.result_queue.get(timeout=600)
                if tid == task_id:
                    if status == "success":
                        return result
                    else:
                        # 处理错误结果
                        if isinstance(result, dict) and "message" in result:
                            raise Exception(result["message"])
                        else:
                            raise Exception(result)
            except queue.Empty:
                raise Exception("执行超时")
    
    def stop(self):
        """停止工作线程"""
        self.running = False
        if hasattr(self, 'worker_thread') and self.worker_thread:
            self.worker_thread.join(timeout=2)
            
        # 清理事件循环
        if hasattr(self, 'loop') and self.loop and not self.loop.is_closed():
            self.loop.close()

# 创建全局工作线程实例
worker = PlaywrightWorker()

def sync_start_browser(headless=False):
    async def run():
        return await automation.start_browser(headless)
    return worker.execute(run)

def sync_navigate_to(url: str, iframe_selector: str = None):
    async def run():
        return await automation.navigate_to(url, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_click_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.click_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_fill_input(selector: str, text: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.fill_input(selector, text, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_scroll_page(direction: str = "down", pixels: int = 500, iframe_selector: str = None):
    async def run():
        return await automation.scroll_page(direction, pixels, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_get_page_text():
    async def run():
        return await automation.get_page_text()
    return worker.execute(run)

def sync_extract_element_text(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.extract_element_text(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_extract_element_json(selector: str, selector_type: str = "css"):
    async def run():
        return await automation.extract_element_json(selector, selector_type)
    return worker.execute(run)

def sync_execute_script_steps(steps: List[Dict[str, Any]]):
    async def run():
        return await automation.execute_script_steps(steps)
    return worker.execute(run)

def sync_close_browser():
    async def run():
        return await automation.close_browser()
    return worker.execute(run)

def sync_wait_for_timeout(milliseconds: int):
    async def run():
        return await automation.wait_for_timeout(milliseconds)
    return worker.execute(run)

def sync_get_all_links():
    async def run():
        return await automation.get_all_links()
    return worker.execute(run)

def sync_get_page_title():
    async def run():
        return await automation.get_page_title()
    return worker.execute(run)

def sync_get_current_url():
    async def run():
        return await automation.get_current_url()
    return worker.execute(run)

def sync_wait_for_selector(selector: str, timeout: int = 30000):
    async def run():
        if selector is None:
            raise ValueError("选择器不能为None")
        return await automation.wait_for_selector(selector, timeout)
    return worker.execute(run)

def sync_get_element_count(selector: str):
    async def run():
        return await automation.get_element_count(selector)
    return worker.execute(run)

def sync_take_screenshot(path: str = None):
    async def run():
        return await automation.take_screenshot(path)
    return worker.execute(run)

def sync_hover_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.hover_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_double_click_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.double_click_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_right_click_element(selector: str, selector_type: str = "css", iframe_selector: str = None):
    async def run():
        return await automation.right_click_element(selector, selector_type, iframe_selector=iframe_selector)
    return worker.execute(run)

def sync_get_page_elements():
    async def run():
        return await automation.get_page_elements()
    return worker.execute(run)

def sync_extract_element_data(selector: str):
    async def run():
        return await automation.extract_element_data(selector)
    return worker.execute(run)

def sync_extract_all_texts(selector: str):
    async def run():
        return await automation.extract_all_texts(selector)
    return worker.execute(run)

def sync_extract_text_from_iframe(iframe_selector: str, element_selector: str):
    async def run():
        return await automation.extract_text_from_iframe(iframe_selector, element_selector)
    return worker.execute(run)

def sync_get_page_data():
    async def run():
        return await automation.get_page_data()
    return worker.execute(run)

def sync_analyze_page_content(selector: str):
    async def run():
        return await automation.analyze_page_content(selector)
    return worker.execute(run)

def sync_wait_for_element_visible(selector: str, timeout: int = 30000, selector_type: str = "css"):
    async def run():
        if selector is None:
            raise ValueError("选择器不能为None")
        return await automation.wait_for_element_visible(selector, timeout, selector_type)
    return worker.execute(run)

def sync_start_recording():
    async def run():
        return await automation.start_recording()
    return worker.execute(run)

def sync_stop_recording():
    async def run():
        return await automation.stop_recording()
    return worker.execute(run)

def sync_enable_element_selection(url=''):
    async def run():
        return await automation.enable_element_selection(url)
    return worker.execute(run)

def sync_disable_element_selection():
    async def run():
        return await automation.disable_element_selection()
    return worker.execute(run)

def sync_get_selected_element():
    async def run():
        return await automation.get_selected_element()
    return worker.execute(run)

def sync_extract_json_from_selected_element():
    async def run():
        return await automation.extract_json_from_selected_element()
    return worker.execute(run)

def sync_execute_multiple_test_cases(case_ids: List[int], db):
    async def run():
        return await automation.execute_multiple_test_cases(case_ids, db)
    return worker.execute(run)


# 网络爬虫文本提取功能
try:
    from crawler_text_extractor_adapter import extract_text_from_page, extract_all_page_text, extract_multiple_elements
    
    async def crawl_extract_text(self, url: str, selector: str = None) -> str:
        """
        使用网络爬虫技术提取文本内容
        
        Args:
            url: 目标URL
            selector: CSS选择器,可选
            
        Returns:
            提取到的文本内容
        """
        if selector:
            return await extract_text_from_page(url, selector)
        else:
            return await extract_all_page_text(url)
    
    async def crawl_extract_multiple_elements(self, url: str, selectors: List[str]) -> Dict[str, str]:
        """
        使用网络爬虫技术提取多个元素的文本内容
        
        Args:
            url: 目标URL
            selectors: 选择器列表
            
        Returns:
            包含各选择器对应文本的字典
        """
        return await extract_multiple_elements(url, selectors)
    
    # 将方法绑定到Automation类
    from playwright.async_api import async_playwright
    import sys
    # 通过globals获取PlaywrightAutomation类
    if 'PlaywrightAutomation' in globals():
        PlaywrightAutomation.crawl_extract_text = crawl_extract_text
        PlaywrightAutomation.crawl_extract_multiple_elements = crawl_extract_multiple_elements
    else:
        # 如果类未定义,稍后绑定
        def bind_crawler_methods():
            if hasattr(sys.modules[__name__], 'PlaywrightAutomation'):
                cls = getattr(sys.modules[__name__], 'PlaywrightAutomation')
                cls.crawl_extract_text = crawl_extract_text
                cls.crawl_extract_multiple_elements = crawl_extract_multiple_elements
        bind_crawler_methods()
    
except ImportError:
    uat_logger.warning("未能导入网络爬虫文本提取模块,将使用原版方法")
    # 如果无法导入爬虫模块,保持原有功能不变
    pass


# 高性能文本提取功能
try:
    from high_performance_text_extractor import HighPerformanceTextExtractor
    
    def init_high_performance_extractor(self):
        """初始化高性能文本提取器"""
        if not hasattr(self, '_high_perf_extractor'):
            self._high_perf_extractor = HighPerformanceTextExtractor(self)
        return self._high_perf_extractor
    
    async def extract_element_text_fast(self, selector: str, use_cache: bool = True) -> str:
        """
        快速提取元素文本
        
        Args:
            selector: CSS选择器
            use_cache: 是否使用缓存
            
        Returns:
            提取到的文本内容
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_element_text_fast(selector, use_cache)
    
    async def extract_element_text_with_fallback(self, selector: str, timeout: int = 5000) -> str:
        """
        带降级策略的文本提取
        
        Args:
            selector: CSS选择器
            timeout: 超时时间(毫秒)
            
        Returns:
            提取到的文本内容
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_element_text_with_fallback(selector, timeout)
    
    async def extract_multiple_elements_batch(self, selectors: List[str]) -> Dict[str, str]:
        """
        批量提取多个元素的文本
        
        Args:
            selectors: 选择器列表
            
        Returns:
            包含各选择器对应文本的字典
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_multiple_elements_batch(selectors)
    
    async def extract_text_by_priority(self, selector: str, extraction_priority: List[str] = None) -> str:
        """
        按优先级提取文本
        
        Args:
            selector: CSS选择器
            extraction_priority: 提取方法优先级列表
            
        Returns:
            提取到的文本内容
        """
        extractor = self.init_high_performance_extractor()
        return await extractor.extract_text_by_priority(selector, extraction_priority)
    
    # 将方法绑定到PlaywrightAutomation类
    PlaywrightAutomation.init_high_performance_extractor = init_high_performance_extractor
    PlaywrightAutomation.extract_element_text_fast = extract_element_text_fast
    PlaywrightAutomation.extract_element_text_with_fallback = extract_element_text_with_fallback
    PlaywrightAutomation.extract_multiple_elements_batch = extract_multiple_elements_batch
    PlaywrightAutomation.extract_text_by_priority = extract_text_by_priority
    
except ImportError:
    uat_logger.warning("未能导入高性能文本提取模块,将使用优化后的基础方法")
    # 如果无法导入高性能提取模块,保持优化后的基础功能
    pass
