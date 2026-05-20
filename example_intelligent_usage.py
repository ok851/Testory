# -*- coding: utf-8 -*-
"""
智能API使用示例

展示如何使用新的桌面自动化智能API实现OpenClaw式体验。
"""

from __future__ import annotations

from desktop_intelligent_api import DesktopAgent, get_agent, run


def example_1_basic_usage():
    """示例1: 基本使用流程。"""
    print("=" * 60)
    print("示例1: 基本使用流程")
    print("=" * 60)

    # 创建代理
    agent = DesktopAgent()

    # 设置进度回调
    def on_progress(msg):
        print(f"  [进度] {msg}")

    agent.set_progress_callback(on_progress)

    # 初始化（自动完成首次配置）
    print("\n1. 初始化代理...")
    result = agent.initialize()
    print(f"   初始化成功: {result['success']}")

    # 自然语言执行
    print("\n2. 执行自然语言指令...")

    # 启动应用
    result = agent.execute("打开记事本")
    print(f"   '打开记事本' -> {result.status.name}: {result.message}")

    # 等待应用启动
    agent.wait(2)

    # 点击操作
    result = agent.execute("点击 格式")
    print(f"   '点击 格式' -> {result.status.name}")

    # 输入文本
    result = agent.type_text("Hello from Intelligent API!")
    print(f"   输入文本 -> {result.status.name}")

    print("\n3. 查看状态...")
    status = agent.get_status()
    print(f"   窗口已附着: {status['window_attached']}")
    print(f"   历史动作: {status['action_history_count']}")


def example_2_fuzzy_app_search():
    """示例2: 模糊应用搜索。"""
    print("\n" + "=" * 60)
    print("示例2: 模糊应用搜索")
    print("=" * 60)

    agent = get_agent()

    # 自然语言查询
    queries = ["记事本", "浏览器", "编辑器", "计算器"]

    for query in queries:
        print(f"\n查询: '{query}'")
        apps = agent.find_app(query, top_k=3)
        for app in apps:
            if "error" in app:
                print(f"  错误: {app['error']}")
            else:
                print(f"  - {app['name']} ({app['exe']})")
                print(f"    路径: {app['path']}")
                print(f"    匹配度: {app['confidence']:.2f}")


def example_3_skills():
    """示例3: 使用技能。"""
    print("\n" + "=" * 60)
    print("示例3: 使用技能")
    print("=" * 60)

    agent = get_agent()

    # 列出所有可用技能
    print("\n可用技能列表:")
    for skill in agent.list_available_skills():
        print(f"  - {skill['skill_name']} ({skill['skill_id']})")
        print(f"    描述: {skill['skill_description'][:50]}...")
        print(f"    意图: {', '.join(skill['intent_patterns'][:2])}")

    # 使用文件整理技能
    print("\n文件整理技能示例（试运行）:")
    result = agent.organize_files(
        folder="C:/Users/Public/Downloads",
        dry_run=True,
        organize_by="date",
    )
    print(f"   状态: {result.status.name}")
    print(f"   消息: {result.message}")
    if result.data.get("stats"):
        stats = result.data["stats"]
        print(f"   扫描文件: {stats.get('scanned', 0)}")
        print(f"   计划移动: {stats.get('to_move', 0)}")


def example_4_quick_commands():
    """示例4: 快速命令。"""
    print("\n" + "=" * 60)
    print("示例4: 快速命令")
    print("=" * 60)

    # 使用全局run函数快速执行
    print("\n使用 run() 函数:")

    # 这些命令会自动路由到正确的技能
    commands = [
        "打开计算器",
        "启动 chrome",
        "等待 2 秒",
    ]

    for cmd in commands:
        print(f"\n  执行: '{cmd}'")
        result = run(cmd)
        print(f"  结果: {result.status.name} - {result.message}")


def example_5_window_management():
    """示例5: 窗口管理。"""
    print("\n" + "=" * 60)
    print("示例5: 窗口管理")
    print("=" * 60)

    agent = get_agent()

    # 最大化当前窗口
    print("\n最大化窗口...")
    result = agent.execute("最大化窗口")
    print(f"   结果: {result.status.name}")

    # 捕获当前状态
    print("\n捕获窗口状态...")
    state = agent.capture_state()
    print(f"   窗口标题: {state.get('title', 'N/A')}")
    print(f"   进程: {state.get('process', 'N/A')}")
    print(f"   控件数量: {state.get('control_count', 0)}")


def example_6_suggestions():
    """示例6: 输入建议。"""
    print("\n" + "=" * 60)
    print("示例6: 输入建议")
    print("=" * 60)

    agent = get_agent()

    # 获取输入建议
    partials = ["记", "chrome", "编辑"]

    for partial in partials:
        suggestions = agent.get_suggestions(partial, top_k=5)
        print(f"\n输入 '{partial}' 的建议:")
        for suggestion in suggestions:
            print(f"  - {suggestion}")


if __name__ == "__main__":
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  桌面自动化智能API - 使用示例".center(54) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "═" * 58 + "╝")

    try:
        # 运行示例
        example_1_basic_usage()
        # example_2_fuzzy_app_search()
        # example_3_skills()
        # example_4_quick_commands()
        # example_5_window_management()
        # example_6_suggestions()

        print("\n" + "=" * 60)
        print("所有示例运行完成！")
        print("=" * 60)

    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n\n运行出错: {e}")
        import traceback
        traceback.print_exc()
