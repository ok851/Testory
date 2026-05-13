"""
断言引擎 - 支持多种断言类型
"""
import json
import re
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass
from enum import Enum
from api_http_helper import execute_api_spec_sync, get_json_path_value


def _url_assert_match_variants(actual_url: str, expected: str, equals: bool) -> bool:
    """与 playwright_automation._url_assert_matches_pa 语义一致（编码/解码交叉比对）。"""
    from urllib.parse import unquote

    def variants(s: str):
        s = (s or "").strip()
        if not s:
            return ("",)
        u = unquote(s)
        out = []
        for x in (s, u):
            if x not in out:
                out.append(x)
        return tuple(out)

    av = variants(actual_url)
    ev = variants(expected)
    if equals:
        return any(a == e for a in av for e in ev)
    return any(e and e in a for a in av for e in ev)


class AssertionType(Enum):
    """断言类型"""
    TEXT_EQUALS = "text_equals"           # 文本相等
    TEXT_CONTAINS = "text_contains"       # 文本包含
    TEXT_REGEX = "text_regex"             # 文本正则匹配
    ELEMENT_EXISTS = "element_exists"     # 元素存在
    ELEMENT_VISIBLE = "element_visible"   # 元素可见
    ELEMENT_ATTRIBUTE = "element_attr"    # 元素属性
    ELEMENT_CSS = "element_css"           # 元素CSS样式
    ELEMENT_COUNT = "element_count"       # 元素数量
    URL_EQUALS = "url_equals"             # URL相等
    URL_CONTAINS = "url_contains"         # URL包含
    API_STATUS = "api_status"             # API状态码
    API_JSON = "api_json"                 # API JSON响应
    DATABASE = "database"                 # 数据库查询
    JAVASCRIPT = "javascript"             # JavaScript执行
    CUSTOM = "custom"                     # 自定义断言


@dataclass
class AssertionResult:
    """断言结果"""
    success: bool
    message: str
    actual_value: Any = None
    expected_value: Any = None
    assertion_type: str = ""
    duration_ms: float = 0


class AssertionEngine:
    """断言引擎"""

    def __init__(self, page=None):
        self.page = page

    def execute_assertion(self, assertion_type: str, params: Dict[str, Any]) -> AssertionResult:
        """执行断言"""
        start_time = time.time()

        try:
            handler = getattr(self, f"_assert_{assertion_type}", None)
            if handler:
                result = handler(params)
            else:
                result = AssertionResult(
                    success=False,
                    message=f"未知的断言类型: {assertion_type}",
                    assertion_type=assertion_type
                )
        except Exception as e:
            result = AssertionResult(
                success=False,
                message=f"断言执行异常: {str(e)}",
                assertion_type=assertion_type
            )

        result.duration_ms = (time.time() - start_time) * 1000
        return result

    def _assert_text_equals(self, params: Dict[str, Any]) -> AssertionResult:
        """文本相等断言"""
        actual = params.get('actual', '')
        expected = params.get('expected', '')
        ignore_case = params.get('ignore_case', False)

        if ignore_case:
            actual = actual.lower()
            expected = expected.lower()

        success = actual == expected
        return AssertionResult(
            success=success,
            message=f"文本{'相等' if success else '不相等'}",
            actual_value=actual,
            expected_value=expected,
            assertion_type="text_equals"
        )

    def _assert_text_contains(self, params: Dict[str, Any]) -> AssertionResult:
        """文本包含断言"""
        actual = params.get('actual', '')
        expected = params.get('expected', '')
        ignore_case = params.get('ignore_case', False)

        if ignore_case:
            success = expected.lower() in actual.lower()
        else:
            success = expected in actual

        return AssertionResult(
            success=success,
            message=f"文本{'包含' if success else '不包含'}预期内容",
            actual_value=actual,
            expected_value=expected,
            assertion_type="text_contains"
        )

    def _assert_text_regex(self, params: Dict[str, Any]) -> AssertionResult:
        """文本正则匹配断言"""
        actual = params.get('actual', '')
        pattern = params.get('pattern', '')

        try:
            match = re.search(pattern, actual)
            success = match is not None
            return AssertionResult(
                success=success,
                message=f"正则{'匹配成功' if success else '匹配失败'}",
                actual_value=actual,
                expected_value=pattern,
                assertion_type="text_regex"
            )
        except re.error as e:
            return AssertionResult(
                success=False,
                message=f"正则表达式错误: {e}",
                actual_value=actual,
                expected_value=pattern,
                assertion_type="text_regex"
            )

    def _assert_element_exists(self, params: Dict[str, Any]) -> AssertionResult:
        """元素存在断言"""
        selector = params.get('selector', '')
        selector_type = params.get('selector_type', 'css')

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="element_exists"
            )

        try:
            if selector_type == 'css':
                element = self.page.query_selector(selector)
            else:
                element = self.page.query_selector(f"xpath={selector}")

            success = element is not None
            return AssertionResult(
                success=success,
                message=f"元素{'存在' if success else '不存在'}",
                actual_value=success,
                expected_value=True,
                assertion_type="element_exists"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"元素查询异常: {e}",
                assertion_type="element_exists"
            )

    def _assert_element_visible(self, params: Dict[str, Any]) -> AssertionResult:
        """元素可见断言"""
        selector = params.get('selector', '')

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="element_visible"
            )

        try:
            element = self.page.query_selector(selector)
            if element:
                visible = element.is_visible()
                return AssertionResult(
                    success=visible,
                    message=f"元素{'可见' if visible else '不可见'}",
                    actual_value=visible,
                    expected_value=True,
                    assertion_type="element_visible"
                )
            else:
                return AssertionResult(
                    success=False,
                    message="元素不存在",
                    assertion_type="element_visible"
                )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"元素可见性检查异常: {e}",
                assertion_type="element_visible"
            )

    def _assert_element_attr(self, params: Dict[str, Any]) -> AssertionResult:
        """元素属性断言"""
        selector = params.get('selector', '')
        attr_name = params.get('attr_name', '')
        expected_value = params.get('expected_value', '')
        operator = params.get('operator', 'equals')  # equals, contains, regex

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="element_attr"
            )

        try:
            element = self.page.query_selector(selector)
            if not element:
                return AssertionResult(
                    success=False,
                    message="元素不存在",
                    assertion_type="element_attr"
                )

            actual_value = element.get_attribute(attr_name) or ''

            if operator == 'equals':
                success = actual_value == expected_value
            elif operator == 'contains':
                success = expected_value in actual_value
            elif operator == 'regex':
                success = re.search(expected_value, actual_value) is not None
            else:
                success = False

            return AssertionResult(
                success=success,
                message=f"属性 {attr_name} {'符合预期' if success else '不符合预期'}",
                actual_value=actual_value,
                expected_value=expected_value,
                assertion_type="element_attr"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"属性检查异常: {e}",
                assertion_type="element_attr"
            )

    def _assert_element_css(self, params: Dict[str, Any]) -> AssertionResult:
        """元素CSS样式断言"""
        selector = params.get('selector', '')
        css_property = params.get('css_property', '')
        expected_value = params.get('expected_value', '')

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="element_css"
            )

        try:
            element = self.page.query_selector(selector)
            if not element:
                return AssertionResult(
                    success=False,
                    message="元素不存在",
                    assertion_type="element_css"
                )

            actual_value = element.evaluate(f"el => getComputedStyle(el).{css_property}")
            success = actual_value == expected_value

            return AssertionResult(
                success=success,
                message=f"CSS属性 {css_property} {'符合预期' if success else '不符合预期'}",
                actual_value=actual_value,
                expected_value=expected_value,
                assertion_type="element_css"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"CSS检查异常: {e}",
                assertion_type="element_css"
            )

    def _assert_element_count(self, params: Dict[str, Any]) -> AssertionResult:
        """元素数量断言"""
        selector = params.get('selector', '')
        expected_count = params.get('expected_count', 0)
        operator = params.get('operator', 'equals')  # equals, gt, lt, gte, lte

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="element_count"
            )

        try:
            elements = self.page.query_selector_all(selector)
            actual_count = len(elements)

            if operator == 'equals':
                success = actual_count == expected_count
            elif operator == 'gt':
                success = actual_count > expected_count
            elif operator == 'lt':
                success = actual_count < expected_count
            elif operator == 'gte':
                success = actual_count >= expected_count
            elif operator == 'lte':
                success = actual_count <= expected_count
            else:
                success = False

            return AssertionResult(
                success=success,
                message=f"元素数量 {actual_count} {'符合预期' if success else '不符合预期'}",
                actual_value=actual_count,
                expected_value=expected_count,
                assertion_type="element_count"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"元素数量检查异常: {e}",
                assertion_type="element_count"
            )

    def _assert_url_equals(self, params: Dict[str, Any]) -> AssertionResult:
        """URL相等断言"""
        expected_url = params.get('expected_url', '')

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="url_equals"
            )

        try:
            actual_url = self.page.url
            success = _url_assert_match_variants(actual_url, expected_url, True)

            return AssertionResult(
                success=success,
                message=f"URL {'相等' if success else '不相等'}",
                actual_value=actual_url,
                expected_value=expected_url,
                assertion_type="url_equals"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"URL检查异常: {e}",
                assertion_type="url_equals"
            )

    def _assert_url_contains(self, params: Dict[str, Any]) -> AssertionResult:
        """URL包含断言"""
        expected_text = params.get('expected_text', '')

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="url_contains"
            )

        try:
            actual_url = self.page.url
            success = _url_assert_match_variants(actual_url, expected_text, False)

            return AssertionResult(
                success=success,
                message=f"URL {'包含' if success else '不包含'}预期内容",
                actual_value=actual_url,
                expected_value=expected_text,
                assertion_type="url_contains"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"URL检查异常: {e}",
                assertion_type="url_contains"
            )

    def _assert_api_status(self, params: Dict[str, Any]) -> AssertionResult:
        """API状态码断言"""
        body = params.get('body')
        spec = {
            "method": params.get('method', 'GET'),
            "url": params.get('url', ''),
            "headers": params.get('headers') or {},
            "body_type": "json" if body is not None else "none",
            "body_json": body,
            "expected_status": params.get('expected_status', 200),
            "timeout": params.get('timeout', 30),
        }
        try:
            out = execute_api_spec_sync(spec, resolve_text=None, browser_cookie_jar=None)
            if out.get("error") and out.get("status_code") is None:
                return AssertionResult(
                    success=False,
                    message=out.get("assert_message") or out.get("error") or "API请求失败",
                    assertion_type="api_status",
                )
            code = out.get("status_code")
            exp = spec["expected_status"]
            success = bool(out.get("ok_assert"))
            return AssertionResult(
                success=success,
                message=out.get("assert_message") or f"HTTP {code}（期望 {exp}）",
                actual_value=code,
                expected_value=exp,
                assertion_type="api_status",
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"API请求异常: {e}",
                assertion_type="api_status"
            )

    def _assert_api_json(self, params: Dict[str, Any]) -> AssertionResult:
        """API JSON响应断言"""
        body = params.get('body')
        json_path = params.get('json_path', '')  # 如: data.user.name
        expected_value = params.get('expected_value', '')
        spec = {
            "method": params.get('method', 'GET'),
            "url": params.get('url', ''),
            "headers": params.get('headers') or {},
            "body_type": "json" if body is not None else "none",
            "body_json": body,
            "timeout": params.get('timeout', 30),
            "expected_status": params.get('expected_status', 200),
            "json_path": json_path,
            "expected_json_value": expected_value,
        }
        try:
            out = execute_api_spec_sync(spec, resolve_text=None, browser_cookie_jar=None)
            if out.get("error") and out.get("status_code") is None:
                return AssertionResult(
                    success=False,
                    message=out.get("assert_message") or out.get("error") or "API请求失败",
                    assertion_type="api_json",
                )
            pj = out.get("response_json")
            actual_value = None
            if pj is not None:
                actual_value = get_json_path_value(pj, json_path)
            success = bool(out.get("ok_assert"))
            return AssertionResult(
                success=success,
                message=out.get("assert_message")
                or f"JSON路径 {json_path} 的值 {'符合预期' if success else '不符合预期'}",
                actual_value=actual_value,
                expected_value=expected_value,
                assertion_type="api_json",
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"API JSON断言异常: {e}",
                assertion_type="api_json"
            )

    def _assert_javascript(self, params: Dict[str, Any]) -> AssertionResult:
        """JavaScript执行断言"""
        script = params.get('script', '')
        expected_result = params.get('expected_result', True)

        if not self.page:
            return AssertionResult(
                success=False,
                message="页面未初始化",
                assertion_type="javascript"
            )

        try:
            actual_result = self.page.evaluate(script)
            success = actual_result == expected_result

            return AssertionResult(
                success=success,
                message=f"JavaScript执行结果 {'符合预期' if success else '不符合预期'}",
                actual_value=actual_result,
                expected_value=expected_result,
                assertion_type="javascript"
            )
        except Exception as e:
            return AssertionResult(
                success=False,
                message=f"JavaScript执行异常: {e}",
                assertion_type="javascript"
            )


import time


if __name__ == '__main__':
    # 测试代码
    engine = AssertionEngine()

    # 测试文本断言
    result = engine.execute_assertion("text_equals", {
        "actual": "Hello World",
        "expected": "Hello World"
    })
    print(f"文本相等测试: {result}")

    # 测试包含断言
    result = engine.execute_assertion("text_contains", {
        "actual": "Hello World",
        "expected": "World"
    })
    print(f"文本包含测试: {result}")
