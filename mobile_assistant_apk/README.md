# Testory Assistant APK

Android 无障碍助手，通过 `adb reverse` 连接平台 WebSocket（`127.0.0.1:19223`），**无需用户配置网络**。

## 用户侧（零配置）

1. 在 Testory 插件市场安装「Testory 移动端助手」（平台自动 `adb install` + `adb reverse`）。
2. 在设备上打开 **Testory Assistant**，点击「开启无障碍服务」，在系统设置中启用（仅需一次）。
3. 在平台移动端测试页连接设备并开始录制/捕获。

## 维护者构建

```bash
python scripts/build_testory_assistant_apk.py
```

产物：`config/plugin_bundles/testory-assistant.apk`（插件市场直接分发）。

需要 JDK 17+；若无 `ANDROID_HOME`，脚本会自动下载 Android SDK 到 `plugin_bundles/android-sdk`。

## 协议

- 设备 → 平台：`hello`、`event`/`capture`（含 `payload`）
- 平台 → 设备：`arm`（`mode`: `record` | `capture_element`）、`disarm`、`pong`
