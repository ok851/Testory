# -*- coding: utf-8 -*-
"""Test script for verifying the multi-platform step recording fixes."""
import re as _re
from modules.ai.ai_action_recorder import ActionRecorder, sanitize_target, is_state_observation, contains_negative_snippet

# Test 1: ActionRecorder captures all platform tools
print("=" * 60)
print("Test 1: ActionRecorder captures all platform tools")
print("=" * 60)

# Browser tools
rec_web = ActionRecorder(platform='web')
tests_web = [
    ('browser_navigate', {'url': 'https://example.com'}, {'ok': True}, 'completed'),
    ('browser_click', {'ref': '@e5', 'text': 'Login'}, {'ok': True, 'matched': 'Login'}, 'completed'),
    ('browser_type', {'ref': '@e3', 'text': 'admin'}, {'ok': True}, 'completed'),
    ('browser_snapshot', {}, {'elements': 5}, 'completed'),  # observation → skipped
    ('browser_scroll', {'direction': 'down'}, {'ok': True}, 'completed'),
]
for name, args, result, status in tests_web:
    recs = rec_web.capture_from_tool_event(name=name, args=args, result=result, status=status)
    for r in recs:
        print(f"  [Web] {r.action_type}: target={r.target[:40]}, status={r.status}")
    if not recs:
        print(f"  [Web] skipped observation: {name}")

# Desktop tools
rec_desktop = ActionRecorder(platform='desktop')
tests_desktop = [
    ('windows_launch_app', {'app': 'notepad.exe'}, {'ok': True, 'app_name': '记事本'}, 'completed'),
    ('windows_click_text', {'text': '确定'}, {'ok': True, 'matched': '确定按钮'}, 'completed'),
    ('windows_type_text', {'text': 'Hello'}, {'ok': True}, 'completed'),
    ('windows_focus_app', {'title': '无标题'}, {'ok': True}, 'completed'),
    ('windows_press_key', {'key': 'Enter'}, {'ok': True}, 'completed'),
]
for name, args, result, status in tests_desktop:
    recs = rec_desktop.capture_from_tool_event(name=name, args=args, result=result, status=status)
    for r in recs:
        print(f"  [Desktop] {r.action_type}: target={r.target[:40]}, status={r.status}")

# Mobile tools
rec_mobile = ActionRecorder(platform='android')
tests_mobile = [
    ('mobile_open_app', {'app': 'com.example.app'}, {'ok': True, 'app_name': '示例应用'}, 'completed'),
    ('mobile_tap', {'text': '登录'}, {'ok': True}, 'completed'),
    ('mobile_input_text', {'text': '1234567890'}, {'ok': True}, 'completed'),
    ('mobile_swipe', {'direction': 'up'}, {'ok': True}, 'completed'),
    ('mobile_extract_otp', {'timeout_sec': 30}, {'ok': True, 'code': '123456'}, 'completed'),
]
for name, args, result, status in tests_mobile:
    recs = rec_mobile.capture_from_tool_event(name=name, args=args, result=result, status=status)
    for r in recs:
        print(f"  [Mobile] {r.action_type}: target={r.target[:40]}, status={r.status}")

# Test 2: Normalization
print("\n" + "=" * 60)
print("Test 2: Action type normalization")
print("=" * 60)
test_names = [
    'browser_navigate', 'browser_click', 'browser_type', 'browser_snapshot', 'browser_scroll',
    'windows_launch_app', 'windows_click_text', 'windows_type_text', 'windows_focus_app',
    'windows_press_key', 'windows_screenshot', 'windows_get_screen_text',
    'mobile_open_app', 'mobile_tap', 'mobile_input_text', 'mobile_swipe', 'mobile_extract_otp',
    'mobile_screenshot', 'mobile_press_key', 'mobile_back', 'mobile_home',
    'mobile_extract_text',
]
for name in test_names:
    normalized = ActionRecorder._normalize_action_type(name, {})
    print(f"  {name} -> {normalized}")

# Test 3: Fallback parser function patterns
print("\n" + "=" * 60)
print("Test 3: Fallback parser function patterns")
print("=" * 60)

fN_PATTERNS = [
    # Web 浏览器工具
    (r"browser_(?:navigate|goto)\s*\(\s*"
     r"(?:url\s*=\s*)?['\"]([^'\"]{2,240})['\"]",
     "navigate", 0),
    (r"browser_click\s*\(\s*"
     r"(?:ref\s*=\s*)?['\"]?(@?e?\d+|[^'\",\)]{1,40})['\"]?",
     "click", 0),
    (r"browser_(?:type|fill)\s*\(\s*(?:ref\s*=\s*)?['\"]([^'\"]{0,80})['\"]\s*,\s*(?:text|value)\s*=\s*['\"]([^'\"]{1,200})['\"]",
     "input", 2),
    (r"\b(browser_snapshot)\s*\(",
     "snapshot", 0),
    (r"browser_scroll\s*\(\s*direction\s*=\s*['\"]?([^'\",\)\s]{2,20})['\"]?",
     "scroll", 0),
    # 桌面工具
    (r"windows_launch_app\s*\(\s*(?:path|app)\s*=\s*['\"]?([^'\",\)\s]{2,100})['\"]?",
     "launch_app", 0),
    (r"windows_click_text\s*\(\s*(?:text|label)\s*=\s*['\"]?([^'\",\)\s]{2,100})['\"]?",
     "click", 0),
    (r"windows_type_text\s*\(\s*(?:text|value)\s*=\s*['\"]([^'\"]{1,200})['\"]",
     "input", 0),
    # 移动端工具
    (r"mobile_open_app\s*\(\s*(?:package|app)\s*=\s*['\"]?([^'\",\)\s]{2,100})['\"]?",
     "open_app", 0),
    (r"mobile_tap\s*\(\s*(?:text|selector)\s*=\s*['\"]?([^'\",\)\s]{2,100})['\"]?",
     "tap", 0),
    (r"mobile_extract_otp\s*\(",
     "extract_otp", 0),
]

test_text = """
正在执行以下操作：
1. browser_navigate(url='https://www.example.com/login')
2. browser_click(ref='@e5')
3. browser_type(ref='@e3', text='admin123')
4. 获取页面结构快照
5. windows_launch_app(app='notepad.exe')
6. windows_type_text(text='Hello World')
7. mobile_open_app(package='com.example.app')
8. mobile_tap(text='登录')
9. mobile_extract_otp()
10. 点击登录按钮
"""

fb_recs = []
_seen_sigs = set()

def _dedup_sig(action: str, target: str) -> bool:
    k = f"{action}|{str(target).strip()[:80]}"
    if k in _seen_sigs:
        return False
    _seen_sigs.add(k)
    return True

for _pat, _act, _g in fN_PATTERNS:
    try:
        for _m in _re.finditer(_pat, test_text, flags=_re.IGNORECASE):
            try:
                _tgt = _m.group(_g + 1) if _g == 0 else _m.group(_g)
            except Exception:
                _tgt = _m.group(1) if _m.groups() else _act
            if not _tgt:
                _tgt = _act
            _tgt = sanitize_target(str(_tgt).strip()[:120] or _act)
            if is_state_observation(_tgt) or contains_negative_snippet(_tgt):
                print(f"  Skipping (state/negative): {_act} -> {_tgt}")
                continue
            if not _dedup_sig(_act, _tgt):
                print(f"  Skipping (dup): {_act} -> {_tgt}")
                continue
            fb_recs.append({
                "action_type": _act,
                "target": _tgt,
                "status": "success",
                "result": _m.group(0)[:100],
            })
            print(f"  Matched: {_act} -> {_tgt}")
    except Exception as e:
        print(f"  Error: {e}")

print(f"\nTotal function-style matches: {len(fb_recs)}")
for r in fb_recs:
    print(f"  {r['action_type']}: {r['target']}")

# Test 4: Chinese patterns
print("\n" + "=" * 60)
print("Test 4: Fallback parser Chinese patterns")
print("=" * 60)

cN_PATTERNS = [
    # Web 浏览器
    (r"(?:导航到|访问|打开网页|进入网站)\s*[:：]\s*(https?://[^\s\"'，。、；]{4,240})",
     "navigate", 1),
    (r"获取页面结构|页面结构快照|DOM\s*清单|browser_snapshot\(\)",
     "snapshot", 0),
    (r"点击\s*([^「\"'\s，。；：\n]{1,30}?)\s*(?:按钮|链接|图标|tab|标签|菜单|选项卡)",
     "click", 1),
    # 桌面
    (r"(?:启动|打开)\s*([^，。；：\s]{1,30}?)\s*(?:应用|程序|软件)",
     "launch_app", 1),
    # 移动端
    (r"(?:提取|获取|读取)\s*(?:短信|验证码|OTP|otp)",
     "extract_otp", 0),
    (r"(?:在手机|移动端|手机)\s*上?\s*(?:点击|轻触)\s*([^，。；：\s]{1,30}?)\s*(?:按钮|图标|文字)",
     "tap", 1),
]

test_text_cn = """
我将执行以下操作：
1. 导航到：https://www.example.com/dashboard
2. 获取页面结构快照
3. 点击登录按钮
4. 启动记事本应用
5. 提取短信验证码
6. 在手机上点击登录按钮
"""

fb_recs_cn = []
for _pat, _act, _tg in cN_PATTERNS:
    try:
        for _m in _re.finditer(_pat, test_text_cn):
            try:
                _raw_tgt = _m.group(_tg) if _tg > 0 else _m.group(0)
            except Exception:
                _raw_tgt = _m.group(0)
            _tgt = sanitize_target(str(_raw_tgt))[:120] or _act
            if not _dedup_sig(_act, _tgt):
                continue
            fb_recs_cn.append({
                "action_type": _act,
                "target": _tgt,
                "status": "success",
                "result": str(_m.group(0) or _act)[:100],
            })
            print(f"  Matched: {_act} -> {_tgt}")
    except Exception as e:
        print(f"  Error: {e}")

print(f"\nTotal Chinese-style matches: {len(fb_recs_cn)}")
for r in fb_recs_cn:
    print(f"  {r['action_type']}: {r['target']}")

# Summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
total_web = len(rec_web.records)
total_desktop = len(rec_desktop.records)
total_mobile = len(rec_mobile.records)
total_fallback_fn = len(fb_recs)
total_fallback_cn = len(fb_recs_cn)

print(f"  Web records: {total_web}")
print(f"  Desktop records: {total_desktop}")
print(f"  Mobile records: {total_mobile}")
print(f"  Fallback function matches: {total_fallback_fn}")
print(f"  Fallback Chinese matches: {total_fallback_cn}")

assert total_web >= 4, f"Expected at least 4 web records (snapshot filtered), got {total_web}"
assert total_desktop >= 5, f"Expected at least 5 desktop records, got {total_desktop}"
assert total_mobile >= 5, f"Expected at least 5 mobile records, got {total_mobile}"
assert total_fallback_fn >= 7, f"Expected at least 7 fallback function matches, got {total_fallback_fn}"
assert total_fallback_cn >= 5, f"Expected at least 5 fallback Chinese matches, got {total_fallback_cn}"

# Test 5: observation tools filtered; console expression lifted
print("\n" + "=" * 60)
print("Test 5: Observation filter + console lift")
print("=" * 60)
from modules.ai.ai_action_recorder import lift_console_expression, is_observation_tool, is_replayable_action_type

assert is_observation_tool("browser_snapshot")
assert is_observation_tool("get_screen_text")
assert is_observation_tool("mobile_get_ui_tree")
assert not is_replayable_action_type("snapshot")
assert not is_replayable_action_type("console")

rec_obs = ActionRecorder(platform="web")
assert rec_obs.capture_from_tool_event(
    name="browser_snapshot", args={}, result={"ok": True}, status="completed"
) == []
assert rec_obs.capture_from_tool_event(
    name="browser_console",
    args={"expression": "console.log(document.title)"},
    result={"ok": True},
    status="completed",
) == []
lifted_click = rec_obs.capture_from_tool_event(
    name="browser_console",
    args={"expression": "document.querySelector('#login').click()"},
    result={"ok": True},
    status="completed",
)
assert len(lifted_click) == 1 and lifted_click[0].action_type == "click"
assert "#login" in lifted_click[0].target
lifted_input = rec_obs.capture_from_tool_event(
    name="browser_console",
    args={"expression": "document.querySelector('input[name=user]').value = 'admin'"},
    result={"ok": True},
    status="completed",
)
assert len(lifted_input) == 1 and lifted_input[0].action_type == "input"
assert lifted_input[0].input_data == "admin"
lifted_nav = lift_console_expression("location.href = 'https://example.com/home'")
assert lifted_nav and lifted_nav["action_type"] == "navigate"
print("  observation filtered + console lift OK")

print("\n✅ All tests passed!")
