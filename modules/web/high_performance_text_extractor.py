#!/usr/bin/env python3
"""
高性能文本提取模块
专门优化文本提取的准确率和响应性能
"""

import asyncio
import time
from typing import Dict, List, Optional, Union
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HighPerformanceTextExtractor:
    """
    高性能文本提取器
    专注于快速准确地提取网页文本内容
    """
    
    def __init__(self, automation_instance):
        """
        初始化提取器
        
        Args:
            automation_instance: PlaywrightAutomation实例
        """
        self.automation = automation_instance
        self.extraction_cache = {}  # 简单缓存机制
        self.cache_ttl = 5  # 缓存有效期（秒）
    
    def _is_cache_valid(self, cache_key: str) -> bool:
        """检查缓存是否有效"""
        if cache_key not in self.extraction_cache:
            return False
        
        cached_time, _ = self.extraction_cache[cache_key]
        return (time.time() - cached_time) < self.cache_ttl
    
    def _get_from_cache(self, cache_key: str) -> Optional[str]:
        """从缓存获取文本"""
        if self._is_cache_valid(cache_key):
            _, text = self.extraction_cache[cache_key]
            return text
        return None
    
    def _set_cache(self, cache_key: str, text: str):
        """设置缓存"""
        self.extraction_cache[cache_key] = (time.time(), text)
    
    async def extract_element_text_fast(self, selector: str, use_cache: bool = True) -> str:
        """
        快速提取元素文本
        
        Args:
            selector: CSS选择器
            use_cache: 是否使用缓存
            
        Returns:
            提取到的文本内容
        """
        if use_cache:
            cache_key = f"element_{selector}"
            cached_result = self._get_from_cache(cache_key)
            if cached_result is not None:
                return cached_result
        
        if self.automation.page is None:
            raise Exception("浏览器未启动")
        
        start_time = time.time()
        
        try:
            # 方法1: 使用优化的JavaScript直接提取（最快）
            text = await self.automation.page.evaluate(f'''
                (selector) => {{
                    const element = document.querySelector(selector);
                    if (!element) return '';
                    
                    // 特殊处理表单元素
                    if (element.tagName === 'INPUT') {{
                        if (['text', 'password', 'email', 'number', 'search', 'tel', 'url'].includes(element.type)) {{
                            return element.value || element.getAttribute('value') || '';
                        }} else if (element.type === 'button' || element.type === 'submit' || element.type === 'reset') {{
                            return element.value || element.textContent || element.innerText || '';
                        }} else {{
                            return element.getAttribute('value') || '';
                        }}
                    }} else if (element.tagName === 'TEXTAREA') {{
                        return element.value || element.textContent || '';
                    }} else if (element.tagName === 'SELECT') {{
                        const selectedOption = element.options[element.selectedIndex];
                        return selectedOption ? selectedOption.text || selectedOption.textContent || '' : '';
                    }}
                    
                    // 处理普通元素，优先获取可见文本
                    if (element.textContent && element.textContent.trim()) {{
                        return element.textContent.trim();
                    }}
                    
                    if (element.innerText && element.innerText.trim()) {{
                        return element.innerText.trim();
                    }}
                    
                    // 尝试获取属性值
                    const attributes = ['title', 'alt', 'placeholder', 'label', 'data-label', 'aria-label'];
                    for (const attr of attributes) {{
                        const value = element.getAttribute(attr);
                        if (value && value.trim()) {{
                            return value.trim();
                        }}
                    }}
                    
                    // 最后尝试innerHTML中的文本
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = element.innerHTML || '';
                    return tempDiv.textContent || tempDiv.innerText || '';
                }}
            ''', selector)
            
            # 清理文本
            if text:
                text = text.strip()
            
            # 设置缓存
            if use_cache and text:
                self._set_cache(cache_key, text)
            
            extraction_time = (time.time() - start_time) * 1000  # 转换为毫秒
            logger.debug(f"快速提取文本耗时: {extraction_time:.2f}ms, 选择器: {selector}")
            
            return text if text else ""
            
        except Exception as e:
            logger.warning(f"快速提取失败: {e}, 选择器: {selector}")
            # 如果快速方法失败，返回空字符串而不是尝试其他方法，以保持性能
            return ""
    
    async def extract_element_text_with_fallback(self, selector: str, timeout: int = 5000) -> str:
        """
        带降级策略的文本提取
        
        Args:
            selector: CSS选择器
            timeout: 超时时间（毫秒）
            
        Returns:
            提取到的文本内容
        """
        if self.automation.page is None:
            raise Exception("浏览器未启动")
        
        start_time = time.time()
        
        # 策略1: 快速JavaScript提取
        try:
            text = await self.automation.page.evaluate(f'''
                (selector) => {{
                    const element = document.querySelector(selector);
                    if (!element) return '';
                    
                    // 组合多种文本获取方式
                    const strategies = [
                        () => element.value,
                        () => element.textContent,
                        () => element.innerText,
                        () => element.getAttribute('value'),
                        () => element.getAttribute('placeholder'),
                        () => element.getAttribute('title'),
                        () => element.getAttribute('alt'),
                        () => element.getAttribute('aria-label'),
                        () => {{
                            if (element.children.length === 0) {{
                                return element.innerHTML;
                            }}
                            return null;
                        }}
                    ];
                    
                    for (const strategy of strategies) {{
                        try {{
                            const result = strategy();
                            if (result && result.trim && result.trim()) {{
                                return result.trim();
                            }}
                        }} catch (e) {{
                            continue;
                        }}
                    }}
                    
                    return '';
                }}
            ''', selector)
            
            if text and text.strip():
                extraction_time = (time.time() - start_time) * 1000
                logger.debug(f"策略1提取成功，耗时: {extraction_time:.2f}ms, 选择器: {selector}")
                return text.strip()
                
        except Exception:
            pass
        
        # 策略2: Playwright locator方法
        try:
            element = self.automation.page.locator(selector)
            
            # 按效率排序的提取方法
            methods = [
                lambda: element.text_content(timeout=timeout),
                lambda: element.inner_text(timeout=timeout),
                lambda: element.input_value(timeout=timeout),
                lambda: element.get_attribute("value", timeout=timeout),
                lambda: element.get_attribute("placeholder", timeout=timeout),
                lambda: element.get_attribute("title", timeout=timeout)
            ]
            
            for i, method in enumerate(methods):
                try:
                    text = await method()
                    if text and text.strip():
                        extraction_time = (time.time() - start_time) * 1000
                        logger.debug(f"策略2方法{i+1}提取成功，耗时: {extraction_time:.2f}ms, 选择器: {selector}")
                        return text.strip()
                except Exception:
                    continue
                    
        except Exception as e:
            logger.debug(f"策略2失败: {e}")
        
        extraction_time = (time.time() - start_time) * 1000
        logger.debug(f"所有策略失败，总耗时: {extraction_time:.2f}ms, 选择器: {selector}")
        return ""
    
    async def extract_multiple_elements_batch(self, selectors: List[str]) -> Dict[str, str]:
        """
        批量提取多个元素的文本（并发处理以提高性能）
        
        Args:
            selectors: 选择器列表
            
        Returns:
            包含各选择器对应文本的字典
        """
        results = {}
        
        # 使用并发处理提高性能
        tasks = []
        for selector in selectors:
            task = self.extract_element_text_fast(selector, use_cache=False)
            tasks.append((selector, task))
        
        # 并发执行所有提取任务
        for selector, task in tasks:
            try:
                text = await task
                results[selector] = text
            except Exception as e:
                logger.warning(f"批量提取选择器 {selector} 失败: {e}")
                results[selector] = ""
        
        return results
    
    async def extract_text_by_priority(self, selector: str, extraction_priority: List[str] = None) -> str:
        """
        按优先级提取文本
        
        Args:
            selector: CSS选择器
            extraction_priority: 提取方法优先级列表
            
        Returns:
            提取到的文本内容
        """
        if extraction_priority is None:
            extraction_priority = [
                'js_value',      # JavaScript value
                'js_textContent', # JavaScript textContent
                'js_innerText',   # JavaScript innerText
                'js_attribute',   # JavaScript attribute
                'playwright_text_content', # Playwright textContent
                'playwright_inner_text',   # Playwright innerText
                'playwright_input_value',  # Playwright input value
                'playwright_attribute'     # Playwright attribute
            ]
        
        if self.automation.page is None:
            raise Exception("浏览器未启动")
        
        for method in extraction_priority:
            try:
                if method == 'js_value':
                    text = await self.automation.page.evaluate(f'''
                        (selector) => {{
                            const el = document.querySelector(selector);
                            return el ? (el.value || '') : '';
                        }}
                    ''', selector)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'js_textContent':
                    text = await self.automation.page.evaluate(f'''
                        (selector) => {{
                            const el = document.querySelector(selector);
                            return el ? (el.textContent || '') : '';
                        }}
                    ''', selector)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'js_innerText':
                    text = await self.automation.page.evaluate(f'''
                        (selector) => {{
                            const el = document.querySelector(selector);
                            return el ? (el.innerText || '') : '';
                        }}
                    ''', selector)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'js_attribute':
                    text = await self.automation.page.evaluate(f'''
                        (selector) => {{
                            const el = document.querySelector(selector);
                            if (!el) return '';
                            const attrs = ['value', 'placeholder', 'title', 'alt', 'aria-label', 'data-value'];
                            for (const attr of attrs) {{
                                const value = el.getAttribute(attr);
                                if (value) return value;
                            }}
                            return '';
                        }}
                    ''', selector)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'playwright_text_content':
                    element = self.automation.page.locator(selector)
                    text = await element.text_content(timeout=2000)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'playwright_inner_text':
                    element = self.automation.page.locator(selector)
                    text = await element.inner_text(timeout=2000)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'playwright_input_value':
                    element = self.automation.page.locator(selector)
                    text = await element.input_value(timeout=2000)
                    if text and text.strip():
                        return text.strip()
                
                elif method == 'playwright_attribute':
                    element = self.automation.page.locator(selector)
                    attrs = ['value', 'placeholder', 'title', 'alt', 'aria-label']
                    for attr in attrs:
                        try:
                            text = await element.get_attribute(attr, timeout=2000)
                            if text and text.strip():
                                return text.strip()
                        except:
                            continue
            
            except Exception:
                continue  # 继续尝试下一种方法
        
        return ""  # 所有方法都失败


# 全局实例（如果需要）
def get_high_performance_extractor(automation_instance):
    """
    获取高性能文本提取器实例
    
    Args:
        automation_instance: PlaywrightAutomation实例
        
    Returns:
        HighPerformanceTextExtractor实例
    """
    return HighPerformanceTextExtractor(automation_instance)


async def test_high_performance_extractor():
    """测试高性能文本提取器"""
    print("测试高性能文本提取器...")
    
    # 注意：这里只是演示接口，实际使用时需要传入automation实例
    print("高性能文本提取模块已准备就绪")


if __name__ == "__main__":
    asyncio.run(test_high_performance_extractor())