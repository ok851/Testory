"""
步骤录制功能测试脚本
用于测试智能步骤录制器的完整流程
"""
import asyncio
from step_recorder import StepRecorder


async def test_recorder():
    """测试录制器基本功能"""
    print("=" * 60)
    print("步骤录制器功能测试")
    print("=" * 60)
    
    # 创建录制器
    recorder = StepRecorder()
    
    try:
        # 测试启动浏览器
        print("\n[1/3] 启动浏览器...")
        await recorder.start("https://www.baidu.com", headless=False)
        print("✓ 浏览器启动成功")
        
        # 等待 2 秒
        await asyncio.sleep(2)
        
        # 测试截图
        print("\n[2/3] 截取屏幕...")
        screenshot = await recorder.take_screenshot()
        if screenshot:
            print(f"✓ 截图成功，大小：{len(screenshot)} bytes")
        else:
            print("✗ 截图失败")
        
        # 测试停止
        print("\n[3/3] 停止录制...")
        steps = await recorder.stop()
        print(f"✓ 录制停止成功，共录制 {len(steps)} 个步骤")
        
        # 显示录制的步骤
        if steps:
            print("\n录制的步骤:")
            for i, step in enumerate(steps, 1):
                print(f"  {i}. {step.get('description', 'N/A')}")
        
    except Exception as e:
        print(f"\n✗ 测试失败：{e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理资源
        try:
            await recorder.stop()
        except:
            pass


def test_api_endpoints():
    """测试 API 端点"""
    import requests
    
    print("\n" + "=" * 60)
    print("API 端点测试")
    print("=" * 60)
    
    base_url = "http://localhost:5000"
    
    try:
        # 测试开始录制 API
        print("\n[1/4] 测试开始录制 API...")
        response = requests.post(
            f"{base_url}/api/steps/recording/start",
            json={
                "url": "https://www.baidu.com",
                "case_id": 1,
                "project_id": 1
            },
            cookies={"session": "test_session"}
        )
        print(f"状态码：{response.status_code}")
        print(f"响应：{response.json()}")
        
        if response.status_code == 200:
            session_id = response.json().get('session_id')
            
            # 测试获取步骤 API
            print("\n[2/4] 测试获取录制步骤 API...")
            response = requests.get(
                f"{base_url}/api/steps/recording/steps",
                params={"session_id": session_id},
                cookies={"session": "test_session"}
            )
            print(f"状态码：{response.status_code}")
            print(f"响应：{response.json()}")
            
            # 测试停止录制 API
            print("\n[3/4] 测试停止录制 API...")
            response = requests.post(
                f"{base_url}/api/steps/recording/stop",
                json={"session_id": session_id},
                cookies={"session": "test_session"}
            )
            print(f"状态码：{response.status_code}")
            print(f"响应：{response.json()}")
            
        else:
            print("✗ 开始录制失败，跳过后续测试")
            
    except Exception as e:
        print(f"\n✗ API 测试失败：{e}")


if __name__ == "__main__":
    print("\n请选择测试模式:")
    print("1. 直接测试录制器 (需要 Playwright)")
    print("2. 测试 API 端点 (需要运行 Flask 应用)")
    print("3. 退出")
    
    choice = input("\n请输入选择 (1/2/3): ").strip()
    
    if choice == "1":
        print("\n开始直接测试录制器...")
        asyncio.run(test_recorder())
    elif choice == "2":
        print("\n开始测试 API 端点...")
        test_api_endpoints()
    elif choice == "3":
        print("退出测试")
    else:
        print("无效的选择")
