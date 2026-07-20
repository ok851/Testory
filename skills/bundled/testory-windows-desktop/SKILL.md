---
name: testory-windows-desktop
description: Testory Windows 桌面自动化：通过 desktop automation gateway（UIA 优先 + ORB 视觉降级）执行 launch/click/input/inspect。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: desktop
    tags: [windows, gui, uia, rpa, desktop-gateway]
---

# Testory Windows 桌面自动化

## 架构

| 组件 | 端口 | 说明 |
|------|------|------|
| Desktop Automation Gateway | `8766` | FastAPI，`desktop_automation_gateway/main.py` |
| 执行引擎 | — | `desktop_automation.py`：UIA + ORB 视觉 + SendInput |
| Bootstrap | — | `desktop_service_bootstrap.py` 随 Flask 启动 |

**不依赖** ClawHub 外部技能；全部通过 Testory 内置 gateway。

## 环境变量

```env
DESKTOP_AGENT_GATEWAY_SECRET=<与 Flask 共用>
DESKTOP_AGENT_GATE_PORT=8766
```

请求头：`X-Desktop-Agent-Secret: <secret>`

## API 速查

详见 `references/gateway-api.md`。

```http
POST /internal/session
POST /internal/session/{id}/run-steps   {"steps": [...]}
POST /internal/session/{id}/inspect     {"max_depth": 4, "max_nodes": 120}
```

## 步骤动作（run-steps）

与平台 `desktop_automation.py` 一致：

- `launch_app` — 启动应用（input_value 或 desktop_spec）
- `attach_window` — 附着窗口
- `click` / `double_click` / `right_click` — 视觉或 UIA 定位
- `input` — 键盘输入
- `hotkey` — 组合键
- `wait` / `screenshot` / `assert` / `verify`

## 最佳实践

1. **UIA 优先**：inspect 获取控件树，按 name/automation_id 定位
2. **视觉降级**：UIA 失败时用 ORB 模板匹配（desktop_spec.image）
3. **合理等待**：应用启动后 wait 1-3s
4. **每步截图**：失败时平台自动保存 artifact PNG
5. **日志**：gateway 返回逐步 results 便于排查

## Hermes 使用方式

1. 确认 desktop gateway 运行：`GET /health` 或平台桌面测试页
2. 加载本 skill：`skill_view(name='testory-windows-desktop')`
3. 通过 terminal 调用 gateway 时**必须**带鉴权头（平台会把密钥写入环境变量）：
   - `X-Desktop-Agent-Secret: $env:DESKTOP_AGENT_GATEWAY_SECRET`
   - 或 `Authorization: Bearer $env:DESKTOP_AGENT_GATEWAY_SECRET`
4. 收到 401 时**不要反复重试**同一请求；检查密钥后换步骤或回报失败
5. **混用**：Web 流程中出现 Windows/macOS 系统弹窗时，从 `testory-web-browser` 切到本 skill
6. 弱 UIA（如微信自定义绘制）时：先 inspect，失败则视觉/computer_use 降级；勿假装成功

## 触发词

- Windows 桌面自动化 / GUI 自动化 / RPA
- 打开记事本 / 点击窗口按钮
- 桌面应用测试
- OS 弹窗 / UAC / 系统确认框

## 平台入口

- AI 测试左栏：桌面自动化层
- `/api/ai/agent/gateway-stream` + `platform: desktop` 意图
- 统一 Hermes 会话（platform=auto）内与 Web/API 同会话切换
