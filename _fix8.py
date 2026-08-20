import pathlib

p = pathlib.Path(r"D:\mkst_baixiang\Python_Code\NewUITestPlatform\NewUITestPlatform\mobile_scrcpy_bridge.py")
text = p.read_text(encoding="utf-8")

old = '''def _try_wake_screen(serial: str) -> bool:
    """尝试唤醒屏幕并解锁（多种方法组合）。"""
    try:
        # 1. 使用 WAKEUP keyevent (比 POWER 更可靠)
        _run_adb(serial, "shell", "input keyevent 224", timeout=5)  # KEYCODE_WAKEUP
        time.sleep(0.3)
        # 2. 也发 POWER 以防万一
        _run_adb(serial, "shell", "input keyevent 26", timeout=5)   # KEYCODE_POWER
        time.sleep(0.5)
        # 3. 上滑解锁（大部分 Android 设备）
        _run_adb(serial, "shell", "input swipe 540 1800 540 600 300", timeout=5)
        time.sleep(0.3)
        # 4. MENU key 解锁
        _run_adb(serial, "shell", "input keyevent 82", timeout=5)   # KEYCODE_MENU
        time.sleep(0.3)
        # 5. 尝试 dismiss keyguard (Android 8+)
        _run_adb(serial, "shell", "wm dismiss-keyguard", timeout=5)
        time.sleep(0.5)
        # 6. 保持屏幕常亮（调试期间）
        _run_adb(serial, "shell", "svc power stayon true", timeout=5)
        time.sleep(0.3)
        ok, msg = _check_device_screen_on(serial)
        if ok:
            uat_logger.info("scrcpy 唤醒屏幕成功 serial=%s", serial)
        else:
            uat_logger.warning("scrcpy 唤醒屏幕后状态: %s", msg)
        return ok
    except Exception as exc:
        uat_logger.debug("scrcpy 唤醒屏幕异常 serial=%s: %s", serial, exc)
        return False'''

new = '''def _try_wake_screen(serial: str) -> bool:
    """尝试唤醒屏幕并解锁（多种方法组合）。仅在屏幕未点亮时按电源键。"""
    try:
        # 先检查当前屏幕状态
        screen_on, _ = _check_device_screen_on(serial)
        if screen_on:
            uat_logger.info("scrcpy 屏幕已点亮 serial=%s，跳过唤醒", serial)
            return True

        # 屏幕灭了，才按电源键唤醒
        _run_adb(serial, "shell", "input keyevent 224", timeout=5)  # KEYCODE_WAKEUP
        time.sleep(0.5)
        # 如果 WAKEUP 不够，再试 POWER
        screen_on2, _ = _check_device_screen_on(serial)
        if not screen_on2:
            _run_adb(serial, "shell", "input keyevent 26", timeout=5)  # KEYCODE_POWER
            time.sleep(0.8)

        # 上滑解锁（大部分 Android 设备）
        _run_adb(serial, "shell", "input swipe 540 1800 540 600 300", timeout=5)
        time.sleep(0.3)
        # MENU key 解锁
        _run_adb(serial, "shell", "input keyevent 82", timeout=5)   # KEYCODE_MENU
        time.sleep(0.3)
        # dismiss keyguard (Android 8+)
        _run_adb(serial, "shell", "wm dismiss-keyguard", timeout=5)
        time.sleep(0.5)
        # 保持屏幕常亮
        _run_adb(serial, "shell", "svc power stayon true", timeout=5)
        time.sleep(0.3)
        ok, msg = _check_device_screen_on(serial)
        if ok:
            uat_logger.info("scrcpy 唤醒屏幕成功 serial=%s", serial)
        else:
            uat_logger.warning("scrcpy 唤醒屏幕后状态: %s", msg)
        return ok
    except Exception as exc:
        uat_logger.debug("scrcpy 唤醒屏幕异常 serial=%s: %s", serial, exc)
        return False'''

if old in text:
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")
    print("OK: fixed _try_wake_screen")
else:
    old_c = old.replace("\n", "\r\n")
    if old_c in text:
        new_c = new.replace("\n", "\r\n")
        text = text.replace(old_c, new_c, 1)
        p.write_text(text, encoding="utf-8")
        print("OK (CRLF): fixed _try_wake_screen")
    else:
        print("NOT FOUND")
