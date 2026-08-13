# -*- coding: utf-8 -*-
"""Improve cross-end strategy prompt to prevent swipe-on-launcher issue."""

FILE = r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\ai_chat_tool_loop.py"

with open(FILE, "r", encoding="utf-8") as f:
    content = f.read()

old = '''    return [
        "",
        "## 跨端工具原则（能力面，非固定剧本）",
        "- 桌面 GUI：用 desktop_* / windows_*；每步根据工具返回再决定下一步，禁止臆造成功。",
        "- 需要短信/通知验证码：调用 mobile_extract_otp，只用工具返回值；禁止编造验证码。",
        "- 需要手机本机跑步骤或用例：mobile_run_steps / mobile_run_case。",
        "- mobile_run_steps 的 steps 须用手机 IR action："
        "open_app（推荐，带 package_name 如 com.tencent.mobileqq）、"
        "tap/input/wait/home/back；禁止 invent launch_app/start_app/shell/find_and_tap。",
        "- 打开应用示例："
        \'{"action":"open_app","description":"打开QQ","package_name":"com.tencent.mobileqq"}\',
        "- 应用内点击必须带可见文案定位（勿只写 description）："
        \'{"action":"tap","description":"点击登录","selector_type":"text","selector_value":"登录"}\',
        "- 勾选协议/复选框：description 含「勾选」且 prefer_checkable（平台会自动补）；"
        "禁止用协议链接文案当唯一目标，应定位勾选框旁短文案。",
        "- 应用内输入：input_value=内容，selector_value=输入框提示文案："
        \'{"action":"input","description":"输入手机号","selector_type":"text",\'
        \'"selector_value":"手机号","input_value":"13800000000"}\',
        "- mobile_* 工具返回的 success 只表示手势层结果；必须阅读 steps_digest / error。"
        "禁止在工具未全部 OK、或存在未勾选/未推进错误时向用户宣称「已完成」。",
        "- 跨工具共享变量在平台侧累积；参数中可写 {{var}}（如 {{sms_otp}} / {{phone_number}}），平台会替换。",
        "- 用户目标可能是登录、注册、换绑或其他：按当前界面与目标自行选择控件描述与顺序，勿套死模板。",
        "- 任一步失败：最多再调整尝试 1 次（共 2 轮）；仍失败则停止并向用户如实说明，禁止长时间循环猜测。",
    ]'''

new = '''    return [
        "",
        "## 跨端工具原则（能力面，非固定剧本）",
        "- 桌面 GUI：用 desktop_* / windows_*；每步根据工具返回再决定下一步，禁止臆造成功。",
        "- 需要短信/通知验证码：调用 mobile_extract_otp，只用工具返回值；禁止编造验证码。",
        "- 需要手机本机跑步骤或用例：mobile_run_steps / mobile_run_case。",
        "- mobile_run_steps 的 steps 须用手机 IR action："
        "open_app（推荐，带 package_name 如 com.tencent.mobileqq）、"
        "tap/input/wait/home/back；禁止 invent launch_app/start_app/shell/find_and_tap。",
        "- 【关键】steps 第一步必须是 open_app 且必须带 package_name，手机会先回到桌面再打开应用；"
        "缺少 open_app 或 package_name 会导致后续操作在桌面上执行（乱滑/乱点）。",
        "- 打开应用示例："
        \'{"action":"open_app","description":"打开目标App","package_name":"com.example.app"}\',
        "- 应用内点击必须带可见文案定位（勿只写 description）："
        \'{"action":"tap","description":"点击登录","selector_type":"text","selector_value":"登录"}\',
        "- 勾选协议/复选框：description 含「勾选」且 prefer_checkable（平台会自动补）；"
        "禁止用协议链接文案当唯一目标，应定位勾选框旁短文案。",
        "- 应用内输入：input_value=内容，selector_value=输入框提示文案："
        \'{"action":"input","description":"输入手机号","selector_type":"text",\'
        \'"selector_value":"手机号","input_value":"13800000000"}\',
        "- 【禁止】用 swipe/scroll 来「寻找」登录元素或「探索页面」。"
        "如果找不到目标元素，应直接向用户报告当前页面状态，而非用滑动猜测。"
        "swipe/scroll 仅用于明确的翻页需求（如列表滚动到指定项）。",
        "- 【步骤数量限制】单次 mobile_run_steps 不超过 10 步。"
        "如果任务复杂，拆分为多次调用，每次都以 open_app 开头。",
        "- mobile_* 工具返回的 success 只表示手势层结果；必须阅读 steps_digest / error。"
        "禁止在工具未全部 OK、或存在未勾选/未推进错误时向用户宣称「已完成」。",
        "- 跨工具共享变量在平台侧累积；参数中可写 {{var}}（如 {{sms_otp}} / {{phone_number}}），平台会替换。",
        "- 用户目标可能是登录、注册、换绑或其他：按当前界面与目标自行选择控件描述与顺序，勿套死模板。",
        "- 任一步失败：最多再调整尝试 1 次（共 2 轮）；仍失败则停止并向用户如实说明，禁止长时间循环猜测。",
    ]'''

assert old in content, "old not found"
content = content.replace(old, new, 1)

with open(FILE, "w", encoding="utf-8") as f:
    f.write(content)

print("OK: ai_chat_tool_loop.py prompt updated")
