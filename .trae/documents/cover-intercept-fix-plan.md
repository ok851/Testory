# Cover 录制触摸拦截修复方案（已修正版）

## 摘要

当前项目 Testory Assistant APK 在录制模式下存在三个并行触摸捕获源互相冲突的问题。上一版方案参考 SoloPi 引入 `getevent -lt` 旁路监听作为主录制源，但**实践证明 getevent 在非 root 设备上完全不可用**（Permission denied），TouchEventOverlay 的 `FLAG_NOT_TOUCH_MODAL` 导致应用内点击重复录制。

修正方案：**彻底移除 getevent 和 TouchEventOverlay，以 AccessibilityService 作为唯一的设备端录制源**，同时修复 TYPE_VIEW_SCROLLED 坐标推算和 open_app 步骤坐标缺失问题。

## 已实施变更

### 1. 录制架构简化为纯 AccessibilityService 模式

**移除的三套并行机制**：
- **getevent 旁路监听**（`TouchEventCapture` 启动逻辑）— 非root设备不可用，已移除
- **TouchEventOverlay 降级方案**（`FLAG_NOT_TOUCH_MODAL` 覆盖层）— 导致重复录制，已移除
- **Cover 拦截注入流水线**（`RecordCoverView` 拦截模式）— 已在第一轮移除

**保留的唯一录制源**：
- `TYPE_TOUCH_INTERACTION`（API 31+）：高精度触摸坐标，应用内可靠
- `TYPE_VIEW_CLICKED` / `TYPE_VIEW_LONG_CLICKED`：click 兜底
- `TYPE_VIEW_SCROLLED`：滑动兜底（主流厂商Launcher支持）
- `TYPE_WINDOW_STATE_CHANGED`：应用切换（open_app）
- `TYPE_VIEW_TEXT_CHANGED`：文本输入

### 2. 修改文件清单

| 文件 | 变更 |
|------|------|
| `RecordingSession.java` | 移除 `startGeteventCapture()`、Overlay 降级逻辑、`touchCapture` 字段 |
| `AssistantSession.java` | 移除 `geteventCaptureActive` 状态 |
| `AssistantAccessibilityService.java` | 移除 Overlay 过滤分支、修复 SCROLLED 坐标推算、增强 open_app 坐标携带 |
| `RecordCoverView.java` | 移除 Cover 拦截注入流水线（第一轮完成） |
| `PerformingActionGuard.java` | 简化为回放护盾（第一轮完成） |
| `RecordEventFilter.java` | 去重窗口 180ms → 300ms（第一轮完成） |
| `TouchEventCapture.java` | 移除 `setGeteventCaptureActive` 调用，保留供 PC Agent 模式 |

### 3. 关键修复细节

#### 3.1 TYPE_VIEW_SCROLLED 坐标推算
- **原缺陷**：`x2 = cx - dx * 4`，当 scroll_delta=1-3px 时仅产生 4-12px 位移
- **修复**：使用屏幕宽度/高度的 30% 作为滑动距离，方向由 delta 符号决定

#### 3.2 open_app 步骤坐标增强
- **原缺陷**：桌面点击图标后 `open_app` 步骤没有坐标，导致回放失败
- **修复**：`handleAppSwitchRecord()` 中携带最近触摸坐标（`lastTouchX/Y`），并记录 `screen_width/height`

#### 3.3 VIEW_CLICKED 去重
- 保留 `RecordEventFilter.wasRecentTouchGesture()` 300ms 去重窗口
- 仅在 TOUCH_INTERACTION 已产出步骤时跳过 VIEW_CLICKED

### 4. 已知限制

- **桌面滑动**：不报告 `TYPE_VIEW_SCROLLED` 的 Launcher（如 AOSP 原生 Launcher3）桌面滑动无法录制。建议使用 PC Agent 模式（通过 ADB getevent）
- **API < 31 设备**：无 `TYPE_TOUCH_INTERACTION`，仅依赖 `TYPE_VIEW_CLICKED`（坐标精度较低）和 `TYPE_VIEW_SCROLLED`

### 5. 验证结果

- 编译：BUILD SUCCESSFUL
- 单元测试：全部通过
