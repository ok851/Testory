#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强型元素定位器模块
提供多种定位策略以应对动态元素和复杂Web应用
"""

import re
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum, auto
from logger import uat_logger


class LocatorType(Enum):
    """定位器类型枚举"""
    ID = auto()
    CSS_SELECTOR = auto()
    XPATH = auto()
    DATA_ATTRIBUTE = auto()
    ARIA_ATTRIBUTE = auto()
    TEXT_CONTENT = auto()
    PARTIAL_TEXT = auto()
    RELATIVE_PARENT = auto()
    CSS_PSEUDO_CLASS = auto()
    REGEX_CLASS = auto()
    ATTRIBUTE_CONTAINS = auto()
    NTH_CHILD = auto()
    SIBLING = auto()
    ANCESTOR = auto()
    DESCENDANT = auto()
    # Element UI 专用定位类型
    EL_SELECT_PLACEHOLDER = auto()  # 通过placeholder查找el-select
    EL_SELECT_ARIA = auto()  # 通过ARIA属性查找el-select
    EL_SELECT_INPUT = auto()  # 查找el-select内部的input
    EL_FORM_ITEM = auto()  # 通过el-form-item结构定位


@dataclass
class LocatorStrategy:
    """定位策略数据类"""
    type: LocatorType
    selector: str
    priority: int  # 优先级，数字越小优先级越高
    stability_score: float = 1.0  # 稳定性评分 0-1
    description: str = ""


@dataclass
class ElementInfo:
    """元素信息数据类"""
    tag_name: str
    attributes: Dict[str, str] = field(default_factory=dict)
    text_content: str = ""
    class_list: List[str] = field(default_factory=list)
    parent_tag: str = ""
    sibling_index: int = 0
    child_count: int = 0
    depth: int = 0
    # Element UI 专用字段
    is_el_select: bool = False  # 是否是el-select组件
    is_el_input: bool = False  # 是否是el-input组件
    placeholder: str = ""  # placeholder文本
    aria_label: str = ""  # aria-label文本
    parent_label: str = ""  # 父级label文本


class DynamicClassNameFilter:
    """动态类名过滤器"""
    
    # 动态类名模式 - 扩展自原有模式
    DYNAMIC_PATTERNS = [
        # CSS-in-JS / Styled Components 生成的类名
        r'^css-[a-zA-Z0-9]{5,}$',
        r'^styled__[a-zA-Z0-9]+$', 
        r'^sc-[a-zA-Z]+$',  # Styled Components
        r'^emotion-\d+$',
        r'^jss\d+$',
        r'^makeStyles-[a-zA-Z0-9-]+$',
        
        # 时间戳相关
        r'^\d{10,}$',  # Unix时间戳
        r'^\d{4}-\d{2}-\d{2}.*$',  # 日期格式
        r'^time-\d+$',
        r'^timestamp-\d+$',
        
        # 随机哈希/UUID
        r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$',  # UUID
        r'^[a-f0-9]{32}$',  # MD5
        r'^[a-zA-Z0-9]{20,}$',  # 长随机字符串
        
        # React/Vue/Angular 动态类名
        r'^v-\w+-[a-zA-Z0-9]{8}$',  # Vue scoped styles
        r'^ng-[a-z]+-\d+$',  # Angular
        r'^react-\w+-[a-zA-Z0-9]+$',  # React
        r'^data-v-[a-f0-9]{8}$',  # Vue scoped CSS
        
        # 动画相关
        r'^\w+-(enter|leave|active|done|start|end)$',
        r'^anim-\w+-\d+$',
        r'^transition-\w+-\d+$',
        
        # 状态相关（动态）
        r'^is-\w+-\d+$',
        r'^has-\w+-\d+$',
        r'^state-\w+-[a-z0-9]+$',
        
        # 框架特定
        r'^el-[a-z]+__[a-z]+$',  # Element UI
        r'^ant-[a-z]+(-\w+)*$',  # Ant Design
        r'^ivu-[a-z]+(-\w+)*$',  # iView
        r'^t-[a-zA-Z0-9]{8}$',  # TDesign
        r'^weui-[a-z]+$',  # WeUI
        r'^layui-[a-z]+$',  # LayUI
        r'^van-[a-z]+$',  # Vant
        r'^uni-[a-z]+$',  # UniApp
        
        # 工具类框架 (Tailwind等)
        r'^tw-[a-z]+(-\w+)*$',
        r'^tailwind-[a-z]+(-\w+)*$',
        
        # 版本号
        r'^v?\d+\.\d+\.\d+(-[a-z]+\.?\d*)?$',
        
        # 纯数字
        r'^\d+$',
    ]
    
    # 稳定的语义化类名模式
    STABLE_PATTERNS = [
        r'^(btn|button|input|form|nav|header|footer|sidebar|main|content|wrapper|container)$',
        r'^(icon|logo|title|subtitle|text|label|badge|tag|pill)$',
        r'^(primary|secondary|success|danger|warning|info|light|dark)$',
        r'^(large|small|medium|mini|tiny|big|huge)$',
        r'^(active|disabled|selected|checked|focused|hovered|hidden|visible)$',
        r'^(left|right|center|top|bottom|middle)$',
        r'^(first|last|odd|even|nth)$',
        r'^(open|close|expanded|collapsed|show|hide)$',
    ]
    
    @classmethod
    def is_dynamic(cls, class_name: str) -> bool:
        """检查类名是否是动态的"""
        if not class_name or len(class_name) < 2:
            return True
        
        # 检查是否是动态模式
        for pattern in cls.DYNAMIC_PATTERNS:
            if re.match(pattern, class_name, re.IGNORECASE):
                return True
        
        return False
    
    @classmethod
    def is_stable(cls, class_name: str) -> bool:
        """检查类名是否是稳定的"""
        if not class_name or len(class_name) < 3:
            return False
        
        # 如果是动态类名，直接返回False
        if cls.is_dynamic(class_name):
            return False
        
        # 检查是否是稳定模式
        for pattern in cls.STABLE_PATTERNS:
            if re.match(pattern, class_name, re.IGNORECASE):
                return True
        
        # 如果包含语义化词汇，认为是稳定的
        semantic_words = ['button', 'btn', 'input', 'form', 'nav', 'menu', 'card', 'modal', 
                         'dialog', 'tab', 'panel', 'list', 'item', 'link', 'text', 'icon',
                         'header', 'footer', 'sidebar', 'content', 'wrapper', 'container']
        
        for word in semantic_words:
            if word in class_name.lower():
                return True
        
        return True  # 默认认为是稳定的
    
    @classmethod
    def filter_classes(cls, class_list: List[str]) -> List[str]:
        """过滤类名列表，返回稳定的类名"""
        return [c for c in class_list if cls.is_stable(c)]


class EnhancedLocatorGenerator:
    """增强型定位器生成器"""
    
    def __init__(self):
        self.class_filter = DynamicClassNameFilter()
        
    def generate_all_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成所有可能的定位策略"""
        strategies = []
        
        # 1. ID定位 (最高优先级)
        if element_info.attributes.get('id'):
            strategies.append(LocatorStrategy(
                type=LocatorType.ID,
                selector=f"#{element_info.attributes['id']}",
                priority=1,
                stability_score=1.0,
                description="通过ID定位"
            ))
        
        # 2. 数据属性定位
        strategies.extend(self._generate_data_attribute_strategies(element_info))
        
        # 3. ARIA属性定位
        strategies.extend(self._generate_aria_strategies(element_info))
        
        # 4. Element UI 专用定位策略（高优先级）
        strategies.extend(self._generate_element_ui_strategies(element_info))
        
        # 5. 文本内容定位
        strategies.extend(self._generate_text_strategies(element_info))
        
        # 6. CSS选择器定位
        strategies.extend(self._generate_css_strategies(element_info))
        
        # 7. XPath定位
        strategies.extend(self._generate_xpath_strategies(element_info))
        
        # 8. 相对定位
        strategies.extend(self._generate_relative_strategies(element_info))
        
        # 按优先级排序
        strategies.sort(key=lambda x: (x.priority, -x.stability_score))
        
        return strategies
    
    def _generate_element_ui_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成Element UI组件专用定位策略"""
        strategies = []
        
        # 检测是否是Element UI组件
        classes = ' '.join(element_info.class_list)
        is_el_select = 'el-select' in classes
        is_el_input = 'el-input' in classes or 'el-textarea' in classes
        is_el_form_item = 'el-form-item' in classes
        
        # 1. 通过placeholder定位el-select（高优先级）
        if element_info.placeholder:
            placeholder = element_info.placeholder
            # 策略1: 通过placeholder查找el-select内部的input
            strategies.append(LocatorStrategy(
                type=LocatorType.EL_SELECT_PLACEHOLDER,
                selector=f'.el-select:has(.el-input__inner[placeholder="{self._escape_value(placeholder)}"])',
                priority=2,
                stability_score=0.9,
                description=f"el-select通过placeholder定位: {placeholder}"
            ))
            # 策略2: 直接查找带placeholder的input
            strategies.append(LocatorStrategy(
                type=LocatorType.EL_SELECT_INPUT,
                selector=f'.el-select .el-input__inner[placeholder="{self._escape_value(placeholder)}"]',
                priority=3,
                stability_score=0.85,
                description=f"el-select内部input通过placeholder定位"
            ))
            # 策略3: XPath方式
            strategies.append(LocatorStrategy(
                type=LocatorType.XPATH,
                selector=f"//div[contains(@class,'el-select')]//input[@placeholder='{self._escape_xpath(placeholder)}']",
                priority=4,
                stability_score=0.85,
                description=f"el-select通过XPath和placeholder定位"
            ))
        
        # 2. 通过aria-label定位（可搜索下拉框）
        if element_info.aria_label:
            aria_label = element_info.aria_label
            strategies.append(LocatorStrategy(
                type=LocatorType.EL_SELECT_ARIA,
                selector=f'.el-select [aria-label="{self._escape_value(aria_label)}"]',
                priority=3,
                stability_score=0.88,
                description=f"el-select通过aria-label定位"
            ))
        
        # 3. 通过父级label定位（表单场景）
        if element_info.parent_label:
            label = element_info.parent_label
            # 通过label文本找到关联的el-select - 使用XPath（兼容性更好）
            strategies.append(LocatorStrategy(
                type=LocatorType.EL_FORM_ITEM,
                selector=f"//label[contains(text(),'{self._escape_xpath(label)}')]/following::div[contains(@class,'el-select')][1]",
                priority=5,
                stability_score=0.8,
                description=f"通过父级label定位el-select: {label}"
            ))
            # 另一种XPath方式
            strategies.append(LocatorStrategy(
                type=LocatorType.XPATH,
                selector=f"//*[contains(@class,'el-form-item__label') and contains(text(),'{self._escape_xpath(label)}')]/following::div[contains(@class,'el-select')][1]",
                priority=6,
                stability_score=0.75,
                description=f"通过el-form-item__label定位el-select"
            ))
        
        # 4. 针对可搜索下拉框的特殊策略
        if is_el_select:
            # 通过role=combobox定位
            strategies.append(LocatorStrategy(
                type=LocatorType.EL_SELECT_INPUT,
                selector='.el-select .el-input__inner[role="combobox"]',
                priority=10,
                stability_score=0.7,
                description="el-select通过role=combobox定位"
            ))
            # 通过is-filterable类定位可搜索下拉框
            strategies.append(LocatorStrategy(
                type=LocatorType.CSS_SELECTOR,
                selector='.el-select.is-filterable .el-input__inner',
                priority=11,
                stability_score=0.7,
                description="el-select可搜索下拉框定位"
            ))
        
        # 5. el-input组件的特殊策略
        if is_el_input:
            # 通过el-input__inner类定位
            if element_info.attributes.get('id'):
                id_val = element_info.attributes['id']
                strategies.append(LocatorStrategy(
                    type=LocatorType.CSS_SELECTOR,
                    selector=f'.el-input__inner#{id_val}',
                    priority=2,
                    stability_score=0.9,
                    description="el-input内部input通过ID定位"
                ))
        
        return strategies
    
    def _generate_data_attribute_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成数据属性定位策略"""
        strategies = []
        
        # 优先的数据属性
        priority_data_attrs = [
            'data-testid', 'data-cy', 'data-test', 'data-qa',
            'data-automation', 'data-selector', 'data-key',
            'data-id', 'data-name', 'data-component',
            'data-module', 'data-section', 'data-field',
            'data-action', 'data-target', 'data-type',
            'data-role', 'data-value', 'data-index'
        ]
        
        for attr in priority_data_attrs:
            if attr in element_info.attributes:
                value = element_info.attributes[attr]
                if value and len(value) > 0:
                    strategies.append(LocatorStrategy(
                        type=LocatorType.DATA_ATTRIBUTE,
                        selector=f'[{attr}="{self._escape_value(value)}"]',
                        priority=2,
                        stability_score=0.95,
                        description=f"通过{attr}定位"
                    ))
        
        # 其他data-*属性
        for attr, value in element_info.attributes.items():
            if attr.startswith('data-') and attr not in priority_data_attrs:
                if value and len(value) > 0 and len(value) < 50:
                    strategies.append(LocatorStrategy(
                        type=LocatorType.DATA_ATTRIBUTE,
                        selector=f'[{attr}="{self._escape_value(value)}"]',
                        priority=5,
                        stability_score=0.8,
                        description=f"通过{attr}定位"
                    ))
        
        return strategies
    
    def _generate_aria_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成ARIA属性定位策略"""
        strategies = []
        
        aria_attrs = [
            'aria-label', 'aria-labelledby', 'aria-describedby',
            'aria-controls', 'aria-expanded', 'aria-selected',
            'aria-checked', 'aria-pressed', 'aria-hidden',
            'aria-disabled', 'aria-required', 'aria-invalid',
            'aria-valuenow', 'aria-valuemin', 'aria-valuemax',
            'aria-level', 'aria-posinset', 'aria-setsize'
        ]
        
        for attr in aria_attrs:
            if attr in element_info.attributes:
                value = element_info.attributes[attr]
                if value and len(value) > 0:
                    # aria-label 优先级更高
                    priority = 3 if attr == 'aria-label' else 6
                    stability = 0.9 if attr == 'aria-label' else 0.75
                    
                    strategies.append(LocatorStrategy(
                        type=LocatorType.ARIA_ATTRIBUTE,
                        selector=f'[{attr}="{self._escape_value(value)}"]',
                        priority=priority,
                        stability_score=stability,
                        description=f"通过{attr}定位"
                    ))
        
        # role属性
        if 'role' in element_info.attributes:
            role = element_info.attributes['role']
            strategies.append(LocatorStrategy(
                type=LocatorType.ARIA_ATTRIBUTE,
                selector=f'[role="{role}"]',
                priority=7,
                stability_score=0.7,
                description=f"通过role={role}定位"
            ))
        
        return strategies
    
    def _generate_text_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成文本内容定位策略"""
        strategies = []
        text = element_info.text_content.strip()
        
        if not text or len(text) < 1:
            return strategies
        
        # 完整文本匹配（适用于短文本）
        if len(text) <= 50 and '\n' not in text:
            strategies.append(LocatorStrategy(
                type=LocatorType.TEXT_CONTENT,
                selector=f':has-text("{self._escape_value(text)}")',
                priority=8,
                stability_score=0.6 if len(text) > 20 else 0.75,
                description="完整文本匹配"
            ))
        
        # 部分文本匹配
        if len(text) > 10:
            # 取前20个字符作为部分匹配
            partial = text[:20].strip()
            strategies.append(LocatorStrategy(
                type=LocatorType.PARTIAL_TEXT,
                selector=f':has-text("{self._escape_value(partial)}")',
                priority=9,
                stability_score=0.5,
                description="部分文本匹配"
            ))
        
        # 关键词匹配（适用于按钮、链接等）
        keywords = ['提交', '保存', '取消', '确定', '删除', '编辑', '新增', '添加',
                   '查询', '搜索', '登录', '注册', '下一步', '上一步', '完成',
                   'Submit', 'Save', 'Cancel', 'OK', 'Delete', 'Edit', 'Add',
                   'Search', 'Login', 'Register', 'Next', 'Back', 'Finish']
        
        for keyword in keywords:
            if keyword in text:
                strategies.append(LocatorStrategy(
                    type=LocatorType.PARTIAL_TEXT,
                    selector=f':has-text("{keyword}")',
                    priority=10,
                    stability_score=0.55,
                    description=f"关键词'{keyword}'匹配"
                ))
                break  # 只添加第一个匹配的关键词
        
        return strategies
    
    def _generate_css_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成CSS选择器定位策略"""
        strategies = []
        tag = element_info.tag_name.lower()
        
        # 过滤稳定的类名
        stable_classes = self.class_filter.filter_classes(element_info.class_list)
        
        if stable_classes:
            # 使用类名组合
            class_selector = f"{tag}.{'.'.join(stable_classes[:3])}"
            strategies.append(LocatorStrategy(
                type=LocatorType.CSS_SELECTOR,
                selector=class_selector,
                priority=15,
                stability_score=0.65,
                description="CSS类名组合"
            ))
        
        # 属性选择器
        stable_attrs = ['name', 'type', 'placeholder', 'title', 'href', 'src', 'alt']
        for attr in stable_attrs:
            if attr in element_info.attributes:
                value = element_info.attributes[attr]
                if value and len(value) > 0 and len(value) < 100:
                    strategies.append(LocatorStrategy(
                        type=LocatorType.CSS_SELECTOR,
                        selector=f'{tag}[{attr}="{self._escape_value(value)}"]',
                        priority=20,
                        stability_score=0.6,
                        description=f"通过{attr}属性定位"
                    ))
        
        # CSS伪类
        if element_info.sibling_index > 0:
            strategies.append(LocatorStrategy(
                type=LocatorType.CSS_PSEUDO_CLASS,
                selector=f'{tag}:nth-of-type({element_info.sibling_index})',
                priority=25,
                stability_score=0.4,
                description=f"第{element_info.sibling_index}个子元素"
            ))
        
        # first-child / last-child
        if element_info.sibling_index == 1:
            strategies.append(LocatorStrategy(
                type=LocatorType.CSS_PSEUDO_CLASS,
                selector=f'{tag}:first-child',
                priority=25,
                stability_score=0.45,
                description="第一个子元素"
            ))
        
        return strategies
    
    def _generate_xpath_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成XPath定位策略"""
        strategies = []
        tag = element_info.tag_name.lower()
        
        # 基于属性的XPath
        if element_info.attributes.get('id'):
            strategies.append(LocatorStrategy(
                type=LocatorType.XPATH,
                selector=f"//{tag}[@id='{element_info.attributes['id']}']",
                priority=30,
                stability_score=0.9,
                description="XPath通过ID"
            ))
        
        # 基于文本的XPath
        if element_info.text_content.strip():
            text = element_info.text_content.strip()[:30]
            strategies.append(LocatorStrategy(
                type=LocatorType.XPATH,
                selector=f"//{tag}[contains(text(), '{self._escape_xpath(text)}')]",
                priority=35,
                stability_score=0.5,
                description="XPath包含文本"
            ))
        
        # 基于类的XPath
        stable_classes = self.class_filter.filter_classes(element_info.class_list)
        if stable_classes:
            class_condition = " and ".join([f"contains(@class, '{c}')" for c in stable_classes[:2]])
            strategies.append(LocatorStrategy(
                type=LocatorType.XPATH,
                selector=f"//{tag}[{class_condition}]",
                priority=40,
                stability_score=0.55,
                description="XPath通过类名"
            ))
        
        # 层级XPath
        if element_info.parent_tag:
            parent_tag = element_info.parent_tag.lower()
            if element_info.sibling_index > 0:
                strategies.append(LocatorStrategy(
                    type=LocatorType.XPATH,
                    selector=f"//{parent_tag}/{tag}[{element_info.sibling_index}]",
                    priority=45,
                    stability_score=0.35,
                    description="XPath层级定位"
                ))
        
        return strategies
    
    def _generate_relative_strategies(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成相对定位策略"""
        strategies = []
        
        # 父元素定位
        if element_info.parent_tag:
            parent_tag = element_info.parent_tag.lower()
            tag = element_info.tag_name.lower()
            
            strategies.append(LocatorStrategy(
                type=LocatorType.ANCESTOR,
                selector=f"{parent_tag} > {tag}",
                priority=50,
                stability_score=0.3,
                description="父子关系定位"
            ))
        
        # 基于深度的定位
        if element_info.depth > 0:
            tag = element_info.tag_name.lower()
            strategies.append(LocatorStrategy(
                type=LocatorType.DESCENDANT,
                selector=f"{tag}",
                priority=55,
                stability_score=0.2,
                description=f"深度{element_info.depth}定位"
            ))
        
        return strategies
    
    def _escape_value(self, value: str) -> str:
        """转义属性值中的特殊字符"""
        return value.replace('"', '\\"').replace('\n', ' ')
    
    def _escape_xpath(self, value: str) -> str:
        """转义XPath中的特殊字符"""
        return value.replace("'", "&apos;").replace('"', '&quot;')


class SmartLocatorResolver:
    """智能定位器解析器 - 实现降级机制"""
    
    def __init__(self, page):
        self.page = page
        self.generator = EnhancedLocatorGenerator()
    
    async def find_element(self, element_info: ElementInfo, 
                          max_attempts: int = 5) -> Optional[Any]:
        """
        智能查找元素，自动尝试多种定位策略
        
        Args:
            element_info: 元素信息
            max_attempts: 最大尝试次数
            
        Returns:
            找到的元素或None
        """
        strategies = self.generator.generate_all_strategies(element_info)
        
        uat_logger.info(f"🔍 开始智能定位元素，共{len(strategies)}种策略")
        
        for i, strategy in enumerate(strategies[:max_attempts]):
            try:
                locator = self._create_locator(strategy)
                if locator:
                    # 检查元素是否存在
                    count = await locator.count()
                    if count == 1:
                        uat_logger.info(f"✅ 定位成功 [{i+1}/{max_attempts}]: {strategy.description}")
                        return locator
                    elif count > 1:
                        uat_logger.warning(f"⚠️ 定位到多个元素 [{i+1}/{max_attempts}]: {strategy.selector}")
                        # 尝试获取第一个
                        return locator.first
                    
            except Exception as e:
                uat_logger.debug(f"❌ 定位失败 [{i+1}/{max_attempts}]: {strategy.description} - {e}")
                continue
        
        uat_logger.error(f"❌ 所有定位策略均失败，共尝试{min(len(strategies), max_attempts)}种")
        return None
    
    def _create_locator(self, strategy: LocatorStrategy):
        """根据策略创建Playwright定位器"""
        selector = strategy.selector
        
        if strategy.type == LocatorType.ID:
            return self.page.locator(selector)
        elif strategy.type in [LocatorType.CSS_SELECTOR, LocatorType.DATA_ATTRIBUTE,
                              LocatorType.ARIA_ATTRIBUTE, LocatorType.CSS_PSEUDO_CLASS]:
            return self.page.locator(selector)
        elif strategy.type == LocatorType.XPATH:
            return self.page.locator(selector)
        elif strategy.type == LocatorType.TEXT_CONTENT:
            # Playwright的has-text
            return self.page.locator(selector)
        elif strategy.type == LocatorType.PARTIAL_TEXT:
            return self.page.locator(selector)
        else:
            return self.page.locator(selector)
    
    async def find_with_fallback(self, primary_selector: str, 
                                 element_info: ElementInfo) -> Optional[Any]:
        """
        先尝试主选择器，失败时使用智能降级
        
        Args:
            primary_selector: 主选择器
            element_info: 元素信息（用于降级）
            
        Returns:
            找到的元素或None
        """
        try:
            locator = self.page.locator(primary_selector)
            count = await locator.count()
            if count > 0:
                uat_logger.info(f"✅ 主选择器定位成功: {primary_selector}")
                return locator
        except Exception as e:
            uat_logger.warning(f"⚠️ 主选择器失败: {primary_selector} - {e}")
        
        uat_logger.info("🔄 启动智能降级机制...")
        return await self.find_element(element_info)


# JavaScript代码注入 - 用于在页面中提取元素信息
EXTRACT_ELEMENT_INFO_JS = """
(selector) => {
    function extractElementInfo(element) {
        if (!element) return null;
        
        const tagName = element.tagName.toLowerCase();
        const attributes = {};
        const classList = [];
        
        // 提取所有属性
        for (const attr of element.attributes) {
            attributes[attr.name] = attr.value;
        }
        
        // 提取类名
        if (element.className && typeof element.className === 'string') {
            classList.push(...element.className.split(' ').filter(c => c.trim()));
        }
        
        // 计算兄弟索引
        let siblingIndex = 0;
        if (element.parentElement) {
            const siblings = Array.from(element.parentElement.children)
                .filter(child => child.tagName === element.tagName);
            siblingIndex = siblings.indexOf(element) + 1;
        }
        
        // 计算深度
        let depth = 0;
        let parent = element.parentElement;
        while (parent) {
            depth++;
            parent = parent.parentElement;
        }
        
        // Element UI 专用信息提取
        const classStr = element.className || '';
        const isElSelect = classStr.includes('el-select');
        const isElInput = classStr.includes('el-input') || classStr.includes('el-textarea');
        
        // 提取placeholder
        let placeholder = element.placeholder || '';
        // 如果是el-select包装层，尝试查找内部input的placeholder
        if (isElSelect && !placeholder) {
            const innerInput = element.querySelector('.el-input__inner, input');
            if (innerInput) {
                placeholder = innerInput.placeholder || '';
            }
        }
        
        // 提取aria-label
        const ariaLabel = element.getAttribute('aria-label') || '';
        
        // 查找父级label文本
        let parentLabel = '';
        let currentEl = element;
        for (let i = 0; i < 5 && currentEl; i++) {  // 向上查找5层
            // 检查是否有兄弟label
            const parent = currentEl.parentElement;
            if (parent) {
                const label = parent.querySelector('label');
                if (label && label.textContent) {
                    parentLabel = label.textContent.trim();
                    break;
                }
                // 检查el-form-item结构
                if (parent.className && parent.className.includes('el-form-item')) {
                    const labelEl = parent.querySelector('.el-form-item__label');
                    if (labelEl && labelEl.textContent) {
                        parentLabel = labelEl.textContent.trim();
                        break;
                    }
                }
            }
            currentEl = parent;
        }
        
        return {
            tag_name: tagName,
            attributes: attributes,
            text_content: element.textContent ? element.textContent.substring(0, 100) : '',
            class_list: classList,
            parent_tag: element.parentElement ? element.parentElement.tagName.toLowerCase() : '',
            sibling_index: siblingIndex,
            child_count: element.children.length,
            depth: depth,
            // Element UI 专用字段
            is_el_select: isElSelect,
            is_el_input: isElInput,
            placeholder: placeholder,
            aria_label: ariaLabel,
            parent_label: parentLabel
        };
    }
    
    // 如果提供了selector，提取该元素信息；否则提取最后点击的元素
    let element = null;
    if (selector) {
        element = document.querySelector(selector);
    } else if (window.__lastClickedElement) {
        element = window.__lastClickedElement;
    }
    return element ? extractElementInfo(element) : null;
}
"""


# 用于页面注入的事件监听代码
SETUP_ELEMENT_TRACKING_JS = """
(function() {
    if (window.__elementTrackingSetup) return;
    window.__elementTrackingSetup = true;
    
    // 记录最后点击的元素
    document.addEventListener('click', function(e) {
        window.__lastClickedElement = e.target;
    }, true);
    
    // 记录最后输入的元素
    document.addEventListener('input', function(e) {
        window.__lastInputElement = e.target;
    }, true);
    
    // 记录最后聚焦的元素
    document.addEventListener('focus', function(e) {
        window.__lastFocusedElement = e.target;
    }, true);
})();
"""


class ElementLocatorManager:
    """元素定位管理器 - 整合所有功能"""
    
    def __init__(self, page):
        self.page = page
        self.resolver = SmartLocatorResolver(page)
        self.generator = EnhancedLocatorGenerator()
    
    async def setup_tracking(self):
        """设置页面元素追踪"""
        await self.page.evaluate(SETUP_ELEMENT_TRACKING_JS)
        uat_logger.info("✅ 页面元素追踪已设置")
    
    async def extract_element_info(self, selector: str = None) -> Optional[ElementInfo]:
        """提取元素信息"""
        result = await self.page.evaluate(EXTRACT_ELEMENT_INFO_JS, selector)
        if result:
            return ElementInfo(**result)
        return None
    
    async def generate_locators(self, element_info: ElementInfo) -> List[LocatorStrategy]:
        """生成所有定位策略"""
        return self.generator.generate_all_strategies(element_info)
    
    async def find_element(self, element_info: ElementInfo, 
                          max_attempts: int = 5) -> Optional[Any]:
        """智能查找元素"""
        return await self.resolver.find_element(element_info, max_attempts)
    
    async def click_with_fallback(self, selector: str, element_info: ElementInfo = None):
        """带降级的点击操作"""
        locator = await self.resolver.find_with_fallback(selector, element_info)
        if locator:
            await locator.click()
            return True
        return False
    
    async def fill_with_fallback(self, selector: str, text: str, 
                                 element_info: ElementInfo = None):
        """带降级的填充操作"""
        locator = await self.resolver.find_with_fallback(selector, element_info)
        if locator:
            await locator.fill(text)
            return True
        return False
    
    async def fill_el_select(self, selector: str, value: str, 
                             placeholder: str = None, 
                             label_text: str = None) -> bool:
        """
        专门用于填充Element UI下拉框(el-select)
        
        Args:
            selector: CSS选择器（可选，如果提供了placeholder或label_text）
            value: 要选择的值
            placeholder: 下拉框的placeholder文本（用于定位）
            label_text: 关联的label文本（用于定位）
            
        Returns:
            bool: 是否成功
        """
        try:
            uat_logger.info(f"🔍 [EL_SELECT] 开始填充el-select，值: {value}")
            
            # 构建定位策略
            target_selector = None
            
            if selector:
                # 如果提供了选择器，先尝试直接使用
                target_selector = selector
            elif placeholder:
                # 通过placeholder定位
                target_selector = f'.el-select:has(.el-input__inner[placeholder="{placeholder}"])'
                uat_logger.info(f"🔍 [EL_SELECT] 通过placeholder定位: {placeholder}")
            elif label_text:
                # 通过label文本定位
                target_selector = f'.el-form-item:has(.el-form-item__label:has-text("{label_text}")) .el-select'
                uat_logger.info(f"🔍 [EL_SELECT] 通过label定位: {label_text}")
            
            if not target_selector:
                uat_logger.error("❌ [EL_SELECT] 未提供有效的定位信息")
                return False
            
            # 1. 点击下拉框打开选项列表
            select_locator = self.page.locator(target_selector).first
            await select_locator.click()
            uat_logger.info(f"✅ [EL_SELECT] 已点击下拉框")
            
            # 2. 等待下拉列表出现（使用JavaScript检测）
            await asyncio.sleep(0.3)  # 给下拉框动画时间
            
            # 3. 查找并点击目标选项 - 使用JavaScript方式更可靠
            js_result = await self.page.evaluate("""(params) => {
                const { selector, value, isXPath } = params;
                
                // 找到下拉框元素
                let selectEl = null;
                if (isXPath) {
                    const result = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                    selectEl = result.singleNodeValue;
                } else {
                    selectEl = document.querySelector(selector);
                }
                if (!selectEl) return { success: false, error: 'Select element not found' };
                
                // 🔥 修复：通过位置找到与当前下拉框关联的下拉面板（支持向上/向下展开）
                const selectRect = selectEl.getBoundingClientRect();
                const allDropdowns = document.querySelectorAll('.el-select-dropdown');
                let dropdown = null;
                
                for (const d of allDropdowns) {
                    const style = window.getComputedStyle(d);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {
                        const dropdownRect = d.getBoundingClientRect();
                        // 支持向上展开和向下展开
                        const isDownward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                           dropdownRect.top >= selectRect.bottom - 10;
                        const isUpward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                         dropdownRect.bottom <= selectRect.top + 10;
                        if (isDownward || isUpward) {
                            dropdown = d;
                            break;
                        }
                    }
                }
                
                // 如果通过位置没找到，尝试在select内部找
                if (!dropdown) {
                    dropdown = selectEl.querySelector('.el-select-dropdown');
                }
                
                // 如果还是没找到，使用最后一个可见的
                if (!dropdown && allDropdowns.length > 0) {
                    for (let i = allDropdowns.length - 1; i >= 0; i--) {
                        const style = window.getComputedStyle(allDropdowns[i]);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            dropdown = allDropdowns[i];
                            break;
                        }
                    }
                }
                
                if (!dropdown) {
                    // 尝试强制显示下拉列表
                    const input = selectEl.querySelector('.el-input__inner');
                    if (input) {
                        input.click();
                        // 再次通过位置查找
                        const allDropdowns2 = document.querySelectorAll('.el-select-dropdown');
                        for (const d of allDropdowns2) {
                            const style = window.getComputedStyle(d);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                const dropdownRect = d.getBoundingClientRect();
                                const isDownward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                                   dropdownRect.top >= selectRect.bottom - 10;
                                const isUpward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                                 dropdownRect.bottom <= selectRect.top + 10;
                                if (isDownward || isUpward) {
                                    dropdown = d;
                                    break;
                                }
                            }
                        }
                    }
                }
                
                if (!dropdown) return { success: false, error: 'Dropdown not found' };
                
                // 查找选项
                const options = dropdown.querySelectorAll('.el-select-dropdown__item');
                
                for (const option of options) {
                    const text = option.textContent.trim();
                    if (text === value || text.includes(value)) {
                        option.click();
                        return { success: true, matchedText: text };
                    }
                }
                
                return { success: false, error: 'Option not found', availableOptions: Array.from(options).map(o => o.textContent.trim()) };
            }""", {'selector': target_selector, 'value': value, 'isXPath': self._is_xpath_selector(target_selector)})
            
            if js_result and js_result.get('success'):
                uat_logger.info(f"✅ [EL_SELECT] 已选择选项: {js_result.get('matchedText')}")
                return True
            else:
                uat_logger.error(f"❌ [EL_SELECT] 选择失败: {js_result}")
                return False
            
        except Exception as e:
            uat_logger.error(f"❌ [EL_SELECT] 填充失败: {str(e)}")
            return False
    
    def _is_xpath_selector(self, selector: str) -> bool:
        """判断选择器是否是XPath"""
        return selector.startswith('//') or selector.startswith('(//') or selector.startswith('./')
    
    async def fill_el_select_searchable(self, selector: str, value: str,
                                        placeholder: str = None) -> bool:
        """
        专门用于填充Element UI可搜索下拉框(is-filterable)
        
        Args:
            selector: CSS选择器
            value: 要选择的值
            placeholder: 输入框的placeholder
            
        Returns:
            bool: 是否成功
        """
        try:
            uat_logger.info(f"🔍 [EL_SELECT_SEARCH] 开始填充可搜索el-select，值: {value}")
            
            # 构建输入框选择器
            target_selector = None
            if selector:
                target_selector = selector
            elif placeholder:
                target_selector = f'.el-select:has(.el-input__inner[placeholder="{placeholder}"])'
            
            if not target_selector:
                uat_logger.error("❌ [EL_SELECT_SEARCH] 未提供有效的定位信息")
                return False
            
            # 使用JavaScript处理可搜索下拉框
            js_result = await self.page.evaluate("""(params) => {
                const { selector, value } = params;
                
                // 找到下拉框元素
                const selectEl = document.querySelector(selector);
                if (!selectEl) return { success: false, error: 'Select element not found' };
                
                // 找到输入框
                const input = selectEl.querySelector('.el-input__inner');
                if (!input) return { success: false, error: 'Input not found' };
                
                // 点击输入框
                input.click();
                input.focus();
                
                // 清除现有内容并输入新值
                input.value = value;
                
                // 触发输入事件
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                
                // 等待过滤完成
                return new Promise((resolve) => {
                    setTimeout(() => {
                        // 🔥 修复：通过位置找到与当前下拉框关联的下拉面板（支持向上/向下展开）
                        const selectRect = selectEl.getBoundingClientRect();
                        const allDropdowns = document.querySelectorAll('.el-select-dropdown');
                        let dropdown = null;
                        
                        for (const d of allDropdowns) {
                            const style = window.getComputedStyle(d);
                            if (style.display !== 'none' && style.visibility !== 'hidden') {
                                const dropdownRect = d.getBoundingClientRect();
                                // 支持向上展开和向下展开
                                const isDownward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                                   dropdownRect.top >= selectRect.bottom - 10;
                                const isUpward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                                 dropdownRect.bottom <= selectRect.top + 10;
                                if (isDownward || isUpward) {
                                    dropdown = d;
                                    break;
                                }
                            }
                        }
                        
                        if (!dropdown) {
                            // 再次点击触发下拉
                            input.click();
                            // 再次通过位置查找
                            const allDropdowns2 = document.querySelectorAll('.el-select-dropdown');
                            for (const d of allDropdowns2) {
                                const style = window.getComputedStyle(d);
                                if (style.display !== 'none' && style.visibility !== 'hidden') {
                                    const dropdownRect = d.getBoundingClientRect();
                                    const isDownward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                                       dropdownRect.top >= selectRect.bottom - 10;
                                    const isUpward = Math.abs(dropdownRect.left - selectRect.left) < 50 && 
                                                     dropdownRect.bottom <= selectRect.top + 10;
                                    if (isDownward || isUpward) {
                                        dropdown = d;
                                        break;
                                    }
                                }
                            }
                        }
                        
                        if (!dropdown) {
                            resolve({ success: false, error: 'Dropdown not found after input' });
                            return;
                        }
                        
                        // 查找选项
                        const options = dropdown.querySelectorAll('.el-select-dropdown__item');
                        
                        for (const option of options) {
                            const text = option.textContent.trim();
                            if (text === value || text.includes(value)) {
                                option.click();
                                resolve({ success: true, matchedText: text });
                                return;
                            }
                        }
                        
                        // 如果没找到精确匹配，选择第一个
                        if (options.length > 0) {
                            options[0].click();
                            resolve({ success: true, matchedText: options[0].textContent.trim(), method: 'first_available' });
                            return;
                        }
                        
                        resolve({ success: false, error: 'No options available', availableOptions: Array.from(options).map(o => o.textContent.trim()) });
                    }, 300);
                });
            }""", {'selector': target_selector, 'value': value})
            
            if js_result and js_result.get('success'):
                uat_logger.info(f"✅ [EL_SELECT_SEARCH] 已选择选项: {js_result.get('matchedText')}")
                return True
            else:
                uat_logger.error(f"❌ [EL_SELECT_SEARCH] 选择失败: {js_result}")
                return False
            
        except Exception as e:
            uat_logger.error(f"❌ [EL_SELECT_SEARCH] 填充失败: {str(e)}")
            return False


# 便捷函数
def create_locator_manager(page) -> ElementLocatorManager:
    """创建定位管理器实例"""
    return ElementLocatorManager(page)


# 动态类名检测函数
def analyze_class_stability(class_name: str) -> Dict[str, Any]:
    """分析类名的稳定性"""
    return {
        'class_name': class_name,
        'is_dynamic': DynamicClassNameFilter.is_dynamic(class_name),
        'is_stable': DynamicClassNameFilter.is_stable(class_name),
        'recommendation': 'use' if DynamicClassNameFilter.is_stable(class_name) else 'avoid'
    }


# 批量分析类名
def analyze_classes(class_list: List[str]) -> List[Dict[str, Any]]:
    """批量分析类名稳定性"""
    return [analyze_class_stability(c) for c in class_list]
