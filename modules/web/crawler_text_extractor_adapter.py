#!/usr/bin/env python3
"""
网络爬虫文本提取适配器
将基于网络爬虫的文本提取功能集成到现有系统中
"""

from modules.web.web_crawler_text_extractor import WebCrawlerTextExtractor
import asyncio
from typing import Dict, List, Optional, Union
import logging

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CrawlerTextExtractorAdapter:
    """
    网络爬虫文本提取适配器
    提供与现有Playwright文本提取兼容的接口
    """
    
    def __init__(self):
        """初始化适配器"""
        self.crawler_extractor = WebCrawlerTextExtractor(timeout=15)
    
    async def extract_text_from_url(self, url: str, selector: Optional[str] = None) -> str:
        """
        从URL提取文本内容（异步接口，兼容现有系统）
        
        Args:
            url: 目标URL
            selector: CSS选择器，可选
            
        Returns:
            提取到的文本内容
        """
        loop = asyncio.get_event_loop()
        
        if selector:
            # 使用指定选择器提取文本
            html = await loop.run_in_executor(None, self.crawler_extractor.get_page_content, url)
            if html:
                texts = await loop.run_in_executor(None, self.crawler_extractor.extract_text_by_selector, html, selector)
                return ' '.join(texts) if texts else ""
            else:
                return ""
        else:
            # 提取页面所有文本
            html = await loop.run_in_executor(None, self.crawler_extractor.get_page_content, url)
            if html:
                text = await loop.run_in_executor(None, self.crawler_extractor.extract_all_text, html)
                return text
            else:
                return ""
    
    async def extract_multiple_selectors(self, url: str, selectors: List[str]) -> Dict[str, str]:
        """
        从URL提取多个选择器的文本内容
        
        Args:
            url: 目标URL
            selectors: 选择器列表
            
        Returns:
            包含各选择器对应文本的字典
        """
        loop = asyncio.get_event_loop()
        
        html = await loop.run_in_executor(None, self.crawler_extractor.get_page_content, url)
        if not html:
            return {}
        
        results = {}
        for selector in selectors:
            texts = await loop.run_in_executor(None, self.crawler_extractor.extract_text_by_selector, html, selector)
            results[selector] = ' '.join(texts) if texts else ""
        
        return results
    
    async def extract_structured_data(self, url: str) -> Dict[str, Union[str, List[str]]]:
        """
        提取结构化数据
        
        Args:
            url: 目标URL
            
        Returns:
            结构化数据字典
        """
        loop = asyncio.get_event_loop()
        
        result = await loop.run_in_executor(None, self.crawler_extractor.extract_with_fallback, url, ['h1', 'h2', 'h3', 'p', 'a'])
        return result


# 全局实例
crawler_text_extractor = CrawlerTextExtractorAdapter()


# 提供兼容现有系统的接口函数
async def extract_text_from_page(url: str, selector: str) -> str:
    """
    提取指定页面中特定元素的文本内容
    
    Args:
        url: 页面URL
        selector: CSS选择器
        
    Returns:
        提取到的文本内容
    """
    return await crawler_text_extractor.extract_text_from_url(url, selector)


async def extract_all_page_text(url: str) -> str:
    """
    提取页面所有文本内容
    
    Args:
        url: 页面URL
        
    Returns:
        提取到的文本内容
    """
    return await crawler_text_extractor.extract_text_from_url(url)


async def extract_multiple_elements(url: str, selectors: List[str]) -> Dict[str, str]:
    """
    提取页面中多个元素的文本内容
    
    Args:
        url: 页面URL
        selectors: 选择器列表
        
    Returns:
        包含各选择器对应文本的字典
    """
    return await crawler_text_extractor.extract_multiple_selectors(url, selectors)


# 用于与playwright_automation模块集成的辅助函数
def get_crawler_extractor():
    """
    获取爬虫提取器实例
    
    Returns:
        CrawlerTextExtractorAdapter实例
    """
    return crawler_text_extractor


async def test_crawler_extractor():
    """测试爬虫提取器功能"""
    print("测试网络爬虫文本提取功能...")
    
    # 测试提取所有文本
    text = await extract_all_page_text("https://example.com")
    print(f"提取到示例页面文本长度: {len(text)} 字符")
    
    # 测试提取特定元素
    title = await extract_text_from_page("https://example.com", "h1")
    print(f"提取到标题: {title}")
    
    # 测试提取多个元素
    elements = await extract_multiple_elements("https://example.com", ["h1", "p"])
    print(f"提取到的元素: {elements}")


if __name__ == "__main__":
    asyncio.run(test_crawler_extractor())