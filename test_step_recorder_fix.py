"""
测试步骤录制器修复
验证事件捕获和步骤生成是否正常工作
"""
import asyncio
from step_recorder import StepRecorder


async def test_event_injection():
    """测试事件注入是否正常"""
    recorder = StepRecorder()
    
    # 启动浏览器
    await recorder.start('https://www.baidu.com', headless=True)
    
    print("✅ 浏览器启动成功")
    print(f"录制状态：{recorder.is_recording}")
    print(f"当前步骤数：{len(recorder.recorded_steps)}")
    
    # 等待一段时间，让 JavaScript 注入完成
    await asyncio.sleep(2)
    
    # 模拟点击事件
    try:
        await recorder.page.click('#kw')
        print("✅ 点击事件执行成功")
    except Exception as e:
        print(f"❌ 点击失败：{e}")
    
    # 等待事件处理
    await asyncio.sleep(1)
    
    # 模拟输入事件
    try:
        await recorder.page.fill('#kw', '测试内容')
        print("✅ 输入事件执行成功")
    except Exception as e:
        print(f"❌ 输入失败：{e}")
    
    # 等待事件处理
    await asyncio.sleep(1)
    
    # 检查录制的步骤
    print(f"\n📊 录制结果:")
    print(f"总步骤数：{len(recorder.recorded_steps)}")
    for i, step in enumerate(recorder.recorded_steps, 1):
        print(f"步骤 {i}: {step['operation_type']} - {step['description']}")
    
    # 停止录制
    await recorder.stop()
    print("\n✅ 测试完成")


if __name__ == '__main__':
    asyncio.run(test_event_injection())
