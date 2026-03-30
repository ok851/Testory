"""
步骤录制功能快速验证脚本
用于检查所有组件是否正确安装和配置
"""
import sys


def check_python_version():
    """检查 Python 版本"""
    print("=" * 60)
    print("1. 检查 Python 版本")
    print("=" * 60)
    version = sys.version_info
    print(f"Python 版本：{version.major}.{version.minor}.{version.micro}")
    
    if version.major >= 3 and version.minor >= 7:
        print("✅ Python 版本满足要求 (≥3.7)")
        return True
    else:
        print("❌ Python 版本不满足要求，需要 ≥3.7")
        return False


def check_playwright():
    """检查 Playwright 安装"""
    print("\n" + "=" * 60)
    print("2. 检查 Playwright 安装")
    print("=" * 60)
    
    try:
        import playwright
        print(f"Playwright 已安装")
        print("✅ Playwright 已安装")
        
        # 检查浏览器
        import asyncio
        from playwright.async_api import async_playwright
        
        async def check_browser():
            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.launch(headless=True)
                browser.close()
                print("✅ Chromium 浏览器可用")
                return True
            except Exception as e:
                print(f"❌ Chromium 浏览器未安装或不可用：{e}")
                print("💡 请运行：playwright install chromium")
                return False
            finally:
                await pw.stop()
        
        result = asyncio.run(check_browser())
        return result
        
    except ImportError:
        print("❌ Playwright 未安装")
        print("💡 请运行：pip install playwright")
        return False


def check_flask():
    """检查 Flask 安装"""
    print("\n" + "=" * 60)
    print("3. 检查 Flask 安装")
    print("=" * 60)
    
    try:
        from flask import Flask
        print("✅ Flask 已安装")
        return True
    except ImportError:
        print("❌ Flask 未安装")
        print("💡 请运行：pip install flask flask-cors flask-login")
        return False


def check_step_recorder():
    """检查录制器模块"""
    print("\n" + "=" * 60)
    print("4. 检查步骤录制器模块")
    print("=" * 60)
    
    try:
        from step_recorder import StepRecorder, create_recorder, get_recorder, remove_recorder
        print("✅ step_recorder.py 模块可导入")
        
        # 检查类和方法
        recorder = StepRecorder()
        required_methods = ['start', 'stop', 'handle_event', 'get_recorded_steps']
        
        for method in required_methods:
            if hasattr(recorder, method):
                print(f"   ✅ 方法 {method} 存在")
            else:
                print(f"   ❌ 方法 {method} 缺失")
                return False
        
        return True
        
    except ImportError as e:
        print(f"❌ 无法导入 step_recorder 模块：{e}")
        return False
    except Exception as e:
        print(f"❌ 检查过程出错：{e}")
        return False


def check_database():
    """检查数据库模块"""
    print("\n" + "=" * 60)
    print("5. 检查数据库批量插入方法")
    print("=" * 60)
    
    try:
        from database import Database
        db = Database()
        
        if hasattr(db, 'batch_insert_steps'):
            print("✅ batch_insert_steps 方法存在")
            return True
        else:
            print("❌ batch_insert_steps 方法不存在")
            return False
            
    except Exception as e:
        print(f"❌ 检查过程出错：{e}")
        return False


def check_app_routes():
    """检查 Flask 应用路由"""
    print("\n" + "=" * 60)
    print("6. 检查 API 路由注册")
    print("=" * 60)
    
    try:
        from app import app
        routes = [rule.rule for rule in app.url_map.iter_rules()]
        
        required_routes = [
            '/api/steps/recording/start',
            '/api/steps/recording/stop',
            '/api/steps/recording/steps',
            '/api/steps/recording/save'
        ]
        
        all_present = True
        for route in required_routes:
            if route in routes:
                print(f"✅ 路由 {route} 已注册")
            else:
                print(f"❌ 路由 {route} 未注册")
                all_present = False
        
        return all_present
        
    except Exception as e:
        print(f"❌ 检查过程出错：{e}")
        return False


def check_template():
    """检查前端模板"""
    print("\n" + "=" * 60)
    print("7. 检查前端模板组件")
    print("=" * 60)
    
    import os
    
    template_path = 'templates/list_steps.html'
    
    if not os.path.exists(template_path):
        print(f"❌ 模板文件不存在：{template_path}")
        return False
    
    print(f"✅ 模板文件存在：{template_path}")
    
    # 检查关键组件
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    checks = [
        ('recordingModal', '录制模态框'),
        ('startRecording', '开始录制函数'),
        ('stopRecording', '停止录制函数'),
        ('saveRecording', '保存录制函数'),
        ('recordedStepsList', '步骤预览区'),
        ('🎬 开始录制', '录制按钮')
    ]
    
    all_present = True
    for keyword, description in checks:
        if keyword in content:
            print(f"   ✅ {description} 存在")
        else:
            print(f"   ❌ {description} 缺失")
            all_present = False
    
    return all_present


def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "步骤录制功能验证" + " " * 15 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    
    results = []
    
    # 执行所有检查
    results.append(("Python 版本", check_python_version()))
    results.append(("Playwright", check_playwright()))
    results.append(("Flask", check_flask()))
    results.append(("录制器模块", check_step_recorder()))
    results.append(("数据库模块", check_database()))
    results.append(("API 路由", check_app_routes()))
    results.append(("前端模板", check_template()))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"{status} {name}: {'通过' if result else '失败'}")
    
    print()
    print(f"总计：{passed}/{total} 项通过")
    
    if passed == total:
        print("\n🎉 恭喜！所有组件都已正确安装和配置！")
        print("\n下一步:")
        print("1. 启动平台：python app.py")
        print("2. 访问：http://localhost:5000")
        print("3. 点击 🎬 开始录制 按钮体验功能")
        return 0
    else:
        print("\n⚠️ 部分组件未通过验证，请根据上述提示进行修复")
        return 1


if __name__ == "__main__":
    sys.exit(main())
