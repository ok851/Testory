#!/usr/bin/env python3
"""
基于网络爬虫的文本提取模块
用于快速高效地从网页中提取文本内容，不依赖Playwright浏览器自动化
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import logging
from typing import Dict, List, Optional, Union
import re

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WebCrawlerTextExtractor:
    """
    基于网络爬虫的文本提取器
    使用requests和BeautifulSoup进行快速文本提取
    """
    
    def __init__(self, timeout: int = 10, headers: Optional[Dict] = None):
        """
        初始化文本提取器
        
        Args:
            timeout: 请求超时时间（秒）
            headers: 自定义请求头
        """
        self.timeout = timeout
        
        # 默认请求头，模拟真实浏览器
        self.default_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        if headers:
            self.default_headers.update(headers)
            
        self.session = requests.Session()
        self.session.headers.update(self.default_headers)
    
    def get_page_content(self, url: str) -> Optional[str]:
        """
        获取页面HTML内容
        
        Args:
            url: 目标URL
            
        Returns:
            页面HTML内容或None
        """
        try:
            logger.info(f"正在获取页面内容: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            # 尝试检测编码
            response.encoding = response.apparent_encoding
            logger.info(f"页面获取成功，状态码: {response.status_code}")
            return response.text
            
        except requests.exceptions.RequestException as e:
            logger.error(f"获取页面内容失败: {e}")
            return None
    
    def extract_text_by_selector(self, html: str, selector: str) -> List[str]:
        """
        使用CSS选择器提取文本
        
        Args:
            html: HTML内容
            selector: CSS选择器
            
        Returns:
            提取到的文本列表
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            elements = soup.select(selector)
            
            texts = []
            for element in elements:
                text = element.get_text(strip=True)
                if text:
                    texts.append(text)
                    
            logger.info(f"使用选择器 '{selector}' 提取到 {len(texts)} 个文本元素")
            return texts
            
        except Exception as e:
            logger.error(f"使用选择器提取文本失败: {e}")
            return []
    
    def detect_spa_shell(self, html: str) -> bool:
        """检测是否为 SPA 空壳页面（JS 框架渲染，静态 HTML 无实质内容）。"""
        soup = BeautifulSoup(html, 'html.parser')
        body = soup.find('body')
        if not body:
            return False
        # SPA 典型特征：body 内只有 <div id="app"> 或 <div id="root">，无可见文本
        visible_text = body.get_text(strip=True)
        if len(visible_text) < 50:
            # 检查是否有 SPA 框架标记
            scripts = soup.find_all('script', src=True)
            spa_hints = ('react', 'vue', 'angular', 'next', 'nuxt', 'webpack', 'vite', 'chunk')
            for s in scripts:
                src = (s.get('src') or '').lower()
                if any(h in src for h in spa_hints):
                    return True
            # 检查 noscript 提示
            noscript = soup.find('noscript')
            if noscript and ('javascript' in noscript.get_text().lower() or '启用' in noscript.get_text()):
                return True
        return False

    def extract_all_text(self, html: str) -> str:
        """
        提取页面所有文本内容
        
        Args:
            html: HTML内容
            
        Returns:
            页面所有文本内容
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 移除script和style标签及其内容
            for script in soup(["script", "style", "noscript", "header", "footer", "nav"]):
                script.decompose()
                
            # 获取文本
            text = soup.get_text()
            
            # 清理文本：去除多余空白字符
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            logger.info(f"提取到页面总文本长度: {len(text)} 字符")
            return text
            
        except Exception as e:
            logger.error(f"提取所有文本失败: {e}")
            return ""
    
    def extract_text_by_xpath_alternative(self, html: str, xpath_like: str) -> List[str]:
        """
        使用类似XPath的路径提取文本（实际上使用CSS选择器模拟）
        
        Args:
            html: HTML内容
            xpath_like: 类似XPath的选择器
            
        Returns:
            提取到的文本列表
        """
        # 将简单的XPath转换为CSS选择器
        css_selector = self._convert_xpath_to_css(xpath_like)
        return self.extract_text_by_selector(html, css_selector)
    
    def _convert_xpath_to_css(self, xpath: str) -> str:
        """
        简单的XPath到CSS选择器转换
        
        Args:
            xpath: XPath表达式
            
        Returns:
            CSS选择器
        """
        # 处理简单的XPath转换
        xpath = xpath.strip('/')
        
        # 替换一些常见的XPath模式
        css_parts = []
        parts = xpath.split('/')
        
        for part in parts:
            if part.startswith('@'):
                # 属性选择器
                attr = part[1:]
                if '=' in attr:
                    attr_name, attr_val = attr.split('=')
                    attr_val = attr_val.strip('"\'')
                    css_parts.append(f"[{attr_name}='{attr_val}']")
                else:
                    css_parts.append(f"[{attr}]")
            elif '[' in part and ']' in part:
                # 处理索引和属性
                tag = part.split('[')[0]
                condition = part.split('[')[1].rstrip(']')
                
                if condition.isdigit():  # 索引
                    css_parts.append(tag)
                elif condition.startswith('@'):  # 属性
                    attr = condition[1:]
                    if '=' in attr:
                        attr_name, attr_val = attr.split('=')
                        attr_val = attr_val.strip('"\'')
                        css_parts.append(f"{tag}[{attr_name}='{attr_val}']")
                    else:
                        css_parts.append(f"{tag}[{attr}]")
                else:
                    css_parts.append(tag)
            else:
                css_parts.append(part)
        
        return ' > '.join(filter(None, css_parts))
    
    def extract_structured_data(self, html: str) -> Dict[str, Union[str, List[str]]]:
        """
        提取结构化数据
        
        Args:
            html: HTML内容
            
        Returns:
            结构化的数据字典
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        data = {}
        
        # 提取标题
        title_tag = soup.find('title')
        data['title'] = title_tag.get_text().strip() if title_tag else ''
        
        # 提取meta描述
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        data['description'] = meta_desc.get('content', '') if meta_desc else ''
        
        # 提取所有标题
        headings = []
        for i in range(1, 7):
            heading_tags = soup.find_all(f'h{i}')
            for tag in heading_tags:
                headings.append({
                    'level': i,
                    'text': tag.get_text(strip=True)
                })
        data['headings'] = headings
        
        # 提取段落
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if p.get_text(strip=True)]
        data['paragraphs'] = paragraphs
        
        # 提取链接
        links = []
        for link in soup.find_all('a', href=True):
            links.append({
                'text': link.get_text(strip=True),
                'href': link['href']
            })
        data['links'] = links
        
        # 提取图片
        images = []
        for img in soup.find_all('img'):
            images.append({
                'alt': img.get('alt', ''),
                'src': img.get('src', '')
            })
        data['images'] = images
        
        return data
    
    def extract_with_fallback(self, url: str, selectors: Optional[List[str]] = None) -> Dict[str, Union[str, List[str]]]:
        """
        使用多种策略提取文本，包含备用方案
        
        Args:
            url: 目标URL
            selectors: 可选的选择器列表
            
        Returns:
            提取到的数据字典
        """
        result = {
            'url': url,
            'success': False,
            'extracted_text': '',
            'specific_elements': {},
            'structured_data': {}
        }
        
        # 获取页面内容
        html = self.get_page_content(url)
        if not html:
            return result
        
        # SPA 检测：如果页面是 JS 框架渲染的空壳，标记 success=False 让调用方走 Playwright
        if self.detect_spa_shell(html):
            logger.info(f"SPA 页面检测到，爬虫无法提取: {url}")
            result['success'] = False
            result['error'] = 'SPA page detected, requires JavaScript rendering'
            return result

        result['success'] = True
        
        # 提取所有文本
        result['extracted_text'] = self.extract_all_text(html)
        
        # 提取结构化数据
        result['structured_data'] = self.extract_structured_data(html)
        
        # 如果提供了特定选择器，则提取特定元素
        if selectors:
            for selector in selectors:
                texts = self.extract_text_by_selector(html, selector)
                result['specific_elements'][selector] = texts
        
        return result


def main():
    """主函数，用于测试文本提取功能"""
    extractor = WebCrawlerTextExtractor(timeout=15)
    
    # 测试URL
    test_urls = [
        "https://example.com",
        "https://httpbin.org/html",
    ]
    
    for url in test_urls:
        print(f"\n=== 测试URL: {url} ===")
        
        # 基础文本提取
        result = extractor.extract_with_fallback(
            url=url,
            selectors=['h1', 'p', 'a', 'title']
        )
        
        if result['success']:
            print(f"✓ 成功提取文本，长度: {len(result['extracted_text'])} 字符")
            print(f"标题: {result['structured_data'].get('title', '')}")
            print(f"描述: {result['structured_data'].get('description', '')[:100]}...")
            
            # 显示特定元素提取结果
            for selector, texts in result['specific_elements'].items():
                if texts:
                    print(f"{selector} 元素: {texts[:3]}...")  # 只显示前3个结果
        else:
            print("✗ 提取失败")
    
    # 测试直接使用选择器
    print(f"\n=== 测试选择器提取 ===")
    html_sample = """
    <html>
        <body>
            <h1>测试标题</h1>
            <p>这是第一段文字。</p>
            <p>这是第二段文字。</p>
            <div class="content">这是内容区域</div>
        </body>
    </html>
    """
    
    print("提取h1:", extractor.extract_text_by_selector(html_sample, 'h1'))
    print("提取p:", extractor.extract_text_by_selector(html_sample, 'p'))
    print("提取.class:", extractor.extract_text_by_selector(html_sample, '.content'))


if __name__ == "__main__":
    main()