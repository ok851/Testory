# -*- coding: utf-8 -*-
"""Add _last_extract_detail side-channel to extract_element_text."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\playwright_automation.py"
with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

# Add _last_step_detail to __init__
old_init = '        self.current_iframe = []  # iframe \u6808\uff1a[{selector, selector_type, iframe}, ...] \u7531 enter_iframe push'
new_init = '        self.current_iframe = []  # iframe \u6808\uff1a[{selector, selector_type, iframe}, ...] \u7531 enter_iframe push\n        self._last_step_detail: dict = {}  # \u6b65\u9aa4\u6267\u884c\u8be6\u60c5\u4fa7\u901a\u9053\uff08\u7531\u5404 action \u65b9\u6cd5\u586b\u5145\uff0c\u4f9b app.py \u8bfb\u53d6\uff09'
assert old_init in content, "old_init not found"
content = content.replace(old_init, new_init, 1)

# Add detail collection in extract_element_text - after the locator fallback loop
old_extract_start = '''        try:
            element = None

            # Determine target context
            target_context = target_page
            if iframe_selector:'''

new_extract_start = '''        # \u6536\u96c6\u5b9a\u4f4d\u7b56\u7565\u8be6\u60c5
        _extract_detail = {"selector_strategy": selector_type, "selector_attempts": 1, "selector_resolve_ms": 0}
        _resolve_t0 = time.time()

        try:
            element = None

            # Determine target context
            target_context = target_page
            if iframe_selector:'''

assert old_extract_start in content, "old_extract_start not found"
content = content.replace(old_extract_start, new_extract_start, 1)

# After the element.wait_for, record resolve time
old_wait_done = '''            # \u7b49\u5f85\u6587\u672c\u5185\u5bb9\u6e32\u67d3\u5b8c\u6210\uff08\u5f02\u6b65\u52a0\u8f7d\u573a\u666f\uff1a\u5143\u7d20\u5df2 attached \u4f46\u6587\u672c\u5c1a\u672a\u586b\u5145\uff09'''
new_wait_done = '''            # \u8bb0\u5f55\u5b9a\u4f4d\u8017\u65f6
            _extract_detail["selector_resolve_ms"] = (time.time() - _resolve_t0) * 1000

            # \u7b49\u5f85\u6587\u672c\u5185\u5bb9\u6e32\u67d3\u5b8c\u6210\uff08\u5f02\u6b65\u52a0\u8f7d\u573a\u666f\uff1a\u5143\u7d20\u5df2 attached \u4f46\u6587\u672c\u5c1a\u672a\u586b\u5145\uff09'''

assert old_wait_done in content, "old_wait_done not found"
content = content.replace(old_wait_done, new_wait_done, 1)

# At the end of extract_element_text, save detail to side-channel
old_return_result = '''            uat_logger.info(f"\U0001f4dd [TEXT_EXTRACT_DEBUG] Final extraction result: '{result}'")
            return result
        except Exception as e_tag:'''

new_return_result = '''            uat_logger.info(f"\U0001f4dd [TEXT_EXTRACT_DEBUG] Final extraction result: '{result}'")
            _extract_detail["extracted_value"] = result
            self._last_step_detail = _extract_detail
            return result
        except Exception as e_tag:'''

assert old_return_result in content, "old_return_result not found"
content = content.replace(old_return_result, new_return_result, 1)

# Also save detail on empty text return
old_empty_return = '''                uat_logger.warning(f"\U0001f4dd [TEXT_EXTRACT_DEBUG] Element found but text is empty, returning empty string")
                return ""'''

new_empty_return = '''                uat_logger.warning(f"\U0001f4dd [TEXT_EXTRACT_DEBUG] Element found but text is empty, returning empty string")
                _extract_detail["extracted_value"] = ""
                self._last_step_detail = _extract_detail
                return ""'''

assert old_empty_return in content, "old_empty_return not found"
content = content.replace(old_empty_return, new_empty_return, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)
print("OK: playwright_automation.py extract detail side-channel added")
