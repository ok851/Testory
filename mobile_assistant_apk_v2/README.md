# Testory Assistant v2 (智测工坊 移动端助手)

> **可视化、生产级、零代码** Android 自动化测试助手。说你想测什么就行。

## 架构

```
mobile_assistant_apk_v2/
├── app/           # 主应用 (Application, MainActivity, Navigation, Theme)
├── core/          # 核心层 (Model, Room数据库, Repository, Communication, DI, Utils)
├── feature/       # 功能层 (Home, Recorder, Replay, Cases, AI-Bridge, Mirror, Settings, Onboarding)
├── service/       # 服务层 (AccessibilityService, EventPipeline, NodeAnalyzer, ForegroundService)
└── proto/         # gRPC 协议定义
```

| 层 | 职责 |
|----|------|
| **app** | Application 入口、Compose Navigation 路由、Material 3 主题（含暗色模式 + Dynamic Color） |
| **core** | 领域模型 + Room 数据库 + Repository + OkHttp 通信 + Hilt DI + 工具类 |
| **feature** | 所有 UI 页面的 Screen + ViewModel（MVVM 模式） |
| **service** | Android 系统服务：无障碍服务（事件管线）、前台录制服务 |

## 技术栈

| 技术 | 选型 |
|------|------|
| 语言 | Kotlin 100% |
| UI | Jetpack Compose + Material 3 |
| 架构 | MVVM + Clean Architecture |
| DI | Hilt (Dagger) |
| 数据库 | Room + Flow |
| 通信 | OkHttp (HTTP/WebSocket) + gRPC (proto 已定义) |
| 测试 | JUnit5 + MockK + Compose Testing |
| 最低 SDK | Android 8.0 (API 26) |
| 目标 SDK | Android 14 (API 34) |

## 快速开始

### 环境要求

- Android Studio Hedgehog (2023.1.1) 或更高
- JDK 17
- Kotlin 1.9.22
- Gradle 8.4

### 编译

```bash
# 编译 Debug APK
./gradlew assembleDebug

# 编译 Release APK (带混淆)
./gradlew assembleRelease

# 运行所有测试
./gradlew test

# 运行集成测试
./gradlew connectedAndroidTest
```

### 与 PC 端联调

1. 在 PC 端启动 Testory 平台
2. 打开移动端 Testory Assistant
3. 在设置中输入 PC IP 地址和端口 (默认 `192.168.1.100:8777`)
4. 开启无障碍服务和悬浮窗权限
5. 返回首页即可开始使用

## 核心功能

### 零代码录制
- 对话式创建："帮我测试登录功能" → AI 自动生成步骤
- 手动录制：悬浮控制条 + 实时步骤预览 + 元素高亮

### 智能回放
- 多级定位策略回退：坐标 → 文本 → ID → XPath
- 响应式等待 + 执行可视化（步骤指示器 + 高亮）
- 失败时截图对比 + 问题标注

### 投屏（内置）
- MediaProjection + MediaCodec H.264 硬编码 → TCP 直连推流
- 不依赖 scrcpy，不经过 ADB 隧道
- 自适应分辨率/帧率（WiFi/USB/蜂窝网络 自动切换）

### AI 桥接
- 移动端收集上下文（截图 + 控件树）→ 发送到 PC 端 Ollama
- PC 端推理 → 返回步骤列表 → 移动端保存并执行
- 移动端不部署 LLM，资源占用极小

## 权限说明

| 权限 | 用途 |
|------|------|
| 无障碍服务 | 录制用户操作、回放时查找和操作元素 |
| 悬浮窗 | 录制时显示控制条和步骤预览 |
| 媒体投影 (MediaProjection) | 投屏功能（可选） |
| 网络 | 与 PC 端通信、投屏推流 |

## 测试

```bash
# 单元测试（core + feature）
./gradlew :core:test
./gradlew :feature:test

# Compose UI 测试
./gradlew :app:connectedAndroidTest

# 带覆盖率报告
./gradlew test jacocoTestReport
```

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.0.0 | 2026-07 | Kotlin + Compose 全量重写，MVVM架构，Room数据库，内置投屏，AI桥接 |
| v1.x   | 2025   | Java + XML (已归档为 `mobile_assistant_apk_v1_archived/`) |

## CI/CD

详见 `.github/workflows/build.yml` (GitHub Actions) 或本地执行 `./gradlew assembleRelease test`。
