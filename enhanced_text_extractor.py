#!/usr/bin/env python3
"""
增强型文本提取器
结合Playwright和网络爬虫技术的文本提取解决方案
"""

import asyncio
from typing import Dict, List, Optional, Union
from playwright.async_api import async_playwright
import requests
from bs4 import BeautifulSoup
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EnhancedTextExtractor:
    """
    增强型文本提取器
    结合Playwright和网络爬虫技术，提供快速可靠的文本提取
    """
    
    def __init__(self, timeout: int = 15):
        """
        初始化提取器
        
        Args:
            timeout: 请求超时时间
        """
        self.timeout = timeout
        self.playwright_instance = None
        self.browser = None
        self.page = None
        
        # 网络爬虫相关配置
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        })
    
    async def initialize_playwright(self):
        """初始化Playwright实例"""
        if self.playwright_instance is None:
            self.playwright_instance = await async_playwright().start()
            self.browser = await self.playwright_instance.chromium.launch(headless=True)
            self.page = await self.browser.new_page()
    
    async def close_playwright(self):
        """关闭Playwright实例"""
        if self.browser:
            await self.browser.close()
        if self.playwright_instance:
            await self.playwright_instance.stop()
    
    def extract_with_crawler(self, url: str, selector: Optional[str] = None) -> str:
        """
        使用网络爬虫提取文本
        
        Args:
            url: 目标URL
            selector: CSS选择器，可选
            
        Returns:
            提取到的文本内容
        """
        try:
            logger.info(f"使用网络爬虫提取文本: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            html_text = response.text
            soup = BeautifulSoup(html_text, 'html.parser')
            
            # SPA 检测：如果页面是 JS 框架渲染的空壳，直接返回空让调用方走 Playwright
            body = soup.find('body')
            if body:
                visible = body.get_text(strip=True)
                if len(visible) < 50:
                    scripts = soup.find_all('script', src=True)
                    spa_hints = ('react', 'vue', 'angular', 'next', 'nuxt', 'webpack', 'vite', 'chunk')
                    for s in scripts:
                        src = (s.get('src') or '').lower()
                        if any(h in src for h in spa_hints):
                            logger.info(f"检测到 SPA 页面，爬虫无法提取，将由 Playwright 接管")
                            return ""
                    noscript = soup.find('noscript')
                    if noscript and ('javascript' in noscript.get_text().lower() or '启用' in noscript.get_text()):
                        logger.info(f"检测到 noscript JavaScript 提示，将由 Playwright 接管")
                        return ""
            
            if selector:
                # 使用指定选择器提取
                elements = soup.select(selector)
                texts = [elem.get_text(strip=True) for elem in elements if elem.get_text(strip=True)]
                return ' '.join(texts)
            else:
                # 提取全部文本
                # 移除script和style标签
                for script in soup(["script", "style", "noscript", "header", "footer", "nav"]):
                    script.decompose()
                
                text = soup.get_text()
                # 清理文本
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)
                
                return text
                
        except Exception as e:
            logger.error(f"网络爬虫提取失败: {e}")
            return ""
    
    async def extract_with_playwright(self, url: str, selector: Optional[str] = None) -> str:
        """
        使用Playwright提取文本
        
        Args:
            url: 目标URL
            selector: CSS选择器，可选
            
        Returns:
            提取到的文本内容
        """
        try:
            logger.info(f"使用Playwright提取文本: {url}")
            await self.initialize_playwright()
            
            # 导航到页面
            await self.page.goto(url, wait_until='domcontentloaded', timeout=self.timeout*1000)
            
            if selector:
                # 等待元素出现并提取文本
                try:
                    await self.page.wait_for_selector(selector, timeout=10000)
                    element = self.page.locator(selector)
                    
                    # 尝试多种方法提取文本
                    try:
                        text = await element.inner_text(timeout=5000)
                        if text.strip():
                            return text.strip()
                    except:
                        pass
                    
                    try:
                        text = await element.text_content(timeout=5000)
                        if text.strip():
                            return text.strip()
                    except:
                        pass
                    
                    try:
                        text = await element.input_value(timeout=5000)
                        if text.strip():
                            return text.strip()
                    except:
                        pass
                    
                    try:
                        text = await element.get_attribute("value", timeout=5000)
                        if text:
                            return text
                    except:
                        pass
                        
                except Exception as e:
                    logger.warning(f"使用选择器 '{selector}' 提取文本失败: {e}")
                    # 如果选择器失败，提取整个页面文本
                    text = await self.page.inner_text('body')
                    return text if text else ""
            else:
                # 提取整个页面文本
                text = await self.page.inner_text('body')
                return text if text else ""
                
        except Exception as e:
            logger.error(f"Playwright提取失败: {e}")
            return ""
    
    async def extract_text(self, url: str, selector: Optional[str] = None, 
                          use_crawler_first: bool = True) -> Dict[str, Union[str, bool]]:
        """
        提取文本内容，使用最佳策略
        
        Args:
            url: 目标URL
            selector: CSS选择器，可选
            use_crawler_first: 是否优先使用网络爬虫
            
        Returns:
            包含提取结果的字典
        """
        result = {
            'success': False,
            'text': '',
            'method_used': '',
            'error': ''
        }
        
        try:
            if use_crawler_first:
                # 优先使用网络爬虫
                text = self.extract_with_crawler(url, selector)
                if text.strip():
                    result.update({
                        'success': True,
                        'text': text,
                        'method_used': 'crawler'
                    })
                    return result
                else:
                    # 爬虫失败，尝试Playwright
                    text = await self.extract_with_playwright(url, selector)
                    if text.strip():
                        result.update({
                            'success': True,
                            'text': text,
                            'method_used': 'playwright'
                        })
                        return result
            else:
                # 优先使用Playwright
                text = await self.extract_with_playwright(url, selector)
                if text.strip():
                    result.update({
                        'success': True,
                        'text': text,
                        'method_used': 'playwright'
                    })
                    return result
                else:
                    # Playwright失败，尝试网络爬虫
                    text = self.extract_with_crawler(url, selector)
                    if text.strip():
                        result.update({
                            'success': True,
                            'text': text,
                            'method_used': 'crawler'
                        })
                        return result
            
            # 两种方法都失败
            result['error'] = 'Both crawler and playwright failed to extract text'
            
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"文本提取过程中发生错误: {e}")
        
        return result
    
    async def extract_multiple_selectors(self, url: str, selectors: List[str], 
                                       use_crawler_first: bool = True) -> Dict[str, Dict[str, Union[str, bool]]]:
        """
        提取多个选择器的文本内容
        
        Args:
            url: 目标URL
            selectors: 选择器列表
            use_crawler_first: 是否优先使用网络爬虫
            
        Returns:
            包含各选择器提取结果的字典
        """
        results = {}
        
        for selector in selectors:
            result = await self.extract_text(url, selector, use_crawler_first)
            results[selector] = result
        
        return results


# 全局实例
extractor_instance = EnhancedTextExtractor()


async def extract_text_content(url: str, selector: Optional[str] = None) -> Dict[str, Union[str, bool]]:
    """
    提取文本内容的便捷函数
    
    Args:
        url: 目标URL
        selector: CSS选择器，可选
        
    Returns:
        包含提取结果的字典
    """
    return await extractor_instance.extract_text(url, selector)


async def extract_multiple_elements(url: str, selectors: List[str]) -> Dict[str, Dict[str, Union[str, bool]]]:
    """
    提取多个元素的文本内容
    
    Args:
        url: 目标URL
        selectors: 选择器列表
        
    Returns:
        包含各选择器提取结果的字典
    """
    return await extractor_instance.extract_multiple_selectors(url, selectors)


async def cleanup_extractor():
    """清理提取器资源"""
    await extractor_instance.close_playwright()


# 测试函数
async def test_enhanced_extractor():
    """测试增强型文本提取器"""
    print("测试增强型文本提取器...")
    
    # 测试提取示例网站
    result = await extract_text_content("https://example.com", "h1")
    print(f"提取标题结果: {result}")
    
    # 测试提取全文
    result = await extract_text_content("https://example.com")
    print(f"提取全文长度: {len(result.get('text', ''))} 字符")
    
    # 测试多个选择器
    results = await extract_multiple_elements("https://example.com", ["h1", "p"])
    print(f"多个选择器提取结果: {results}")


if __name__ == "__main__":
    asyncio.run(test_enhanced_extractor())