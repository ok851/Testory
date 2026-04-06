"""
Codegen 导入与执行环境快速验证
用于检查 Playwright / Flask / 导入 API / 步骤页模板是否正常
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
        print("[OK] Python 版本满足要求 (≥3.7)")
        return True
    else:
        print("[FAIL] Python 版本不满足要求，需要 ≥3.7")
        return False


def check_playwright():
    """检查 Playwright 安装"""
    print("\n" + "=" * 60)
    print("2. 检查 Playwright 安装")
    print("=" * 60)
    
    try:
        import asyncio
        from playwright.async_api import async_playwright
        
        print("[OK] Playwright 已安装")
        
        async def check_browser():
            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.launch(headless=True)
                await browser.close()
                print("[OK] Chromium 浏览器可用")
                return True
            except Exception as e:
                print(f"[FAIL] Chromium 浏览器未安装或不可用：{e}")
                print("-> 请运行：playwright install chromium")
                return False
            finally:
                await pw.stop()
        
        return asyncio.run(check_browser())
        
    except ImportError:
        print("[FAIL] Playwright 未安装")
        print("-> 请运行：pip install playwright")
        return False


def check_flask():
    """检查 Flask 安装"""
    print("\n" + "=" * 60)
    print("3. 检查 Flask 安装")
    print("=" * 60)
    
    try:
        from flask import Flask
        print("[OK] Flask 已安装")
        return True
    except ImportError:
        print("[FAIL] Flask 未安装")
        print("-> 请运行：pip install flask flask-cors flask-login")
        return False


def check_selenium_ide_import():
    """检查 Selenium IDE 解析模块"""
    print("\n" + "=" * 60)
    print("4b. 检查 selenium_ide_import 模块")
    print("=" * 60)
    try:
        from selenium_ide_import import parse_selenium_ide_to_steps
        sample = '{"url":"https://x.com","tests":[{"commands":[{"command":"open","target":"/","value":""}]}]}'
        steps, _ = parse_selenium_ide_to_steps(sample)
        if steps and steps[0].get("action") == "navigate":
            print("[OK] Selenium .side 样例解析正常")
            return True
        print("[FAIL] Selenium 样例未得到导航步骤")
        return False
    except Exception as e:
        print(f"[FAIL] {e}")
        return False


def check_codegen_import():
    """检查 Codegen 解析模块"""
    print("\n" + "=" * 60)
    print("4. 检查 playwright_codegen_import 模块")
    print("=" * 60)
    
    try:
        from playwright_codegen_import import parse_playwright_codegen_to_steps
        sample = "await page.goto('https://example.com')\nawait page.click('text=Submit')"
        steps, warnings = parse_playwright_codegen_to_steps(sample)
        if steps:
            print(f"[OK] 解析样例成功，得到 {len(steps)} 步")
            if warnings:
                print(f"   （样例附带 {len(warnings)} 条提示，可忽略）")
            return True
        print("[FAIL] 解析样例未得到步骤")
        return False
    except ImportError as e:
        print(f"[FAIL] 无法导入 playwright_codegen_import：{e}")
        return False
    except Exception as e:
        print(f"[FAIL] 检查过程出错：{e}")
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
            print("[OK] batch_insert_steps 方法存在")
            return True
        else:
            print("[FAIL] batch_insert_steps 方法不存在")
            return False
            
    except Exception as e:
        print(f"[FAIL] 检查过程出错：{e}")
        return False


def check_app_routes():
    """检查 Flask 应用路由"""
    print("\n" + "=" * 60)
    print("6. 检查录制导入相关路由")
    print("=" * 60)
    
    try:
        from app import app
        routes = [rule.rule for rule in app.url_map.iter_rules()]
        
        required = [
            '/recording-tutorial',
            '/api/cases/<int:case_id>/import-playwright-codegen',
            '/api/cases/<int:case_id>/import-selenium-ide',
        ]
        
        all_present = True
        for route in required:
            if route in routes:
                print(f"[OK] 路由 {route} 已注册")
            else:
                print(f"[FAIL] 路由 {route} 未注册")
                all_present = False
        
        return all_present
        
    except Exception as e:
        print(f"[FAIL] 检查过程出错：{e}")
        return False


def check_template():
    """检查前端模板"""
    print("\n" + "=" * 60)
    print("7. 检查前端模板组件（list_steps）")
    print("=" * 60)
    
    import os
    
    template_path = 'templates/list_steps.html'
    
    if not os.path.exists(template_path):
        print(f"[FAIL] 模板文件不存在：{template_path}")
        return False
    
    print(f"[OK] 模板文件存在：{template_path}")
    
    with open(template_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    checks = [
        ('codegenImportModal', 'Codegen 导入模态框'),
        ('submitCodegenImport', 'Codegen 导入提交函数'),
        ('从 Codegen 导入', 'Codegen 导入按钮'),
        ('seleniumIdeImportModal', 'Selenium IDE 导入模态框'),
        ('submitSeleniumIdeImport', 'Selenium IDE 导入提交函数'),
        ('从 Selenium IDE 导入', 'Selenium 导入按钮'),
    ]
    
    all_present = True
    for keyword, description in checks:
        if keyword in content:
            print(f"   [OK] {description} 存在")
        else:
            print(f"   [FAIL] {description} 缺失")
            all_present = False
    
    # 不应再依赖站内 Socket.IO 录制
    if 'cdn.socket.io' in content or '/api/recordings/' in content:
        print("   [FAIL] 模板仍含旧版 Socket.IO / recordings API")
        all_present = False
    else:
        print("   [OK] 未发现旧版 recordings / socket.io 引用")
    
    return all_present


def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 8 + "Codegen / Selenium 导入与环境验证" + " " * 8 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    
    results = []
    
    results.append(("Python 版本", check_python_version()))
    results.append(("Playwright", check_playwright()))
    results.append(("Flask", check_flask()))
    results.append(("Codegen 解析模块", check_codegen_import()))
    results.append(("Selenium IDE 解析模块", check_selenium_ide_import()))
    results.append(("数据库模块", check_database()))
    results.append(("API 路由", check_app_routes()))
    results.append(("前端模板", check_template()))
    
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    for name, ok in results:
        status = "[OK] 通过" if ok else "[FAIL] 失败"
        print(f"{name}: {status}")
    
    all_ok = all(r[1] for r in results)
    print("\n" + ("全部通过 [OK]" if all_ok else "存在失败项，请根据上文修复 [FAIL]"))
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
