---
name: testory-windows-desktop
description: Testory Windows 桌面自动化：通过 desktop automation gateway（UIA 优先 + ORB 视觉降级）执行 launch/click/input/inspect。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: desktop
    risk_default: L1
    tags: [windows, gui, uia, rpa, desktop-gateway]
---

# Testory Windows 桌面自动化

## 输入 / 输出 Schema

### 步骤输入（`run-steps` / 跨端 `layer=desktop`）

| 字段 | 说明 |
|------|------|
| `action` | `launch_app` / `click` / `input` / `get_text` / `hotkey` / `wait` / … |
| `desktop_spec` / 定位 | UIA name、automation_id 或视觉描述 |
| `store_as` | 抽取文本写入变量 |

### 步骤输出（须经 `validate_desktop_step_result`）

| 字段 | 说明 |
|------|------|
| `status` | 仅 `success`/`ok`/`passed` 可在跨端当绿；**`warning` 不得绿** |
| `verified` / `pointer_executed` | 指针类动作必填校验 |
| `error` / `warning` | 失败原因 |
| `extracted_text` | 可选 |

离线闸门 Demo：`python demos/goai-agentteams/run_demo.py --suite guards --variant desktop_softfail`

### 企业主路径（真机）

跨端页一键「桌面主路径（记事本）」或：

```http
GET /api/ai/desktop/preflight
GET /api/ai/cross-end/desktop-mainpath-plan
```

编排在 desktop 阶段前预检；Gateway/非 Windows 不可用 → `DESKTOP_NO_SESSION`（不假绿）。单测可设 `DESKTOP_PREFLIGHT=0`。

真机验收：

```bash
python demos/desktop_notepad_mainpath_accept.py
```

退出码 0=通过；1=诚实失败；2=预检未通过。

## 失败处理（诚实）

| 情况 | 结果 |
|------|------|
| 返回非 dict / status 失败 | 校验/阶段失败 |
| 指针步骤缺 `verified` 或 `pointer_executed` | 不得报成功 |
| `status=warning`（跨端） | 编排显式失败 |
| Gateway 401 / 空流 | **不要** tight-loop 重试同一请求 |
| 无桌面会话 | 诚实失败，禁止假装点过 |

## 安全边界

- 默认 **L1**；卸载软件、改系统配置、清用户数据 → **L2** + RiskGuard。  
- 不抢焦点：`DESKTOP_NO_FOCUS_STEAL` / `DESKTOP_PHYSICAL_MOUSE`。  
- 截图/OCR 可能含隐私；证据包按平台策略保存。  
- 禁止在核心代码写死某 App 热键宏；差异留在本 Skill 配方。

## 与官方 Hermes computer_use 的关系

| 路径 | 何时用 |
|------|--------|
| **Windows 默认** 本 Skill + MCP `testory-desktop`（`:9820/mcp`）+ Desktop Gateway `:8766` | 用户点「启动智能体」后由平台自动注册/拉起 |
| **macOS 可选** Hermes `computer_use` + skill `computer-use` + cua-driver | Darwin 且 doctor 通过 |

平台外层 **不** 再 FC 编排桌面点击；只做入口、超时、SSE 展示与用例 JSON。

## 架构

| 组件 | 端口 | 说明 |
|------|------|------|
| Hermes Agent | `8642` | NL→单脑工具循环；桌面优先 computer_use |
| Desktop Automation Gateway | `8766` | FastAPI，cua 不可用时的 UIA/SendInput 后备 |
| 执行引擎 | — | `desktop_automation.py`：UIA + ORB 视觉 + SendInput |
| Bootstrap | — | `desktop_service_bootstrap.py` 随 Flask 启动 |

**不依赖** ClawHub 外部技能；全部通过 Testory 内置 gateway 或 Hermes 官方 computer_use。

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

1. **观察→动作→核验**：`windows_type_text` / `windows_press_key` 先捕获目标窗（frame），再操作，最后用 OCR/画面证据确认；无证据不得报成功
2. **UIA 优先**：inspect 获取控件树，按 name/automation_id 定位；点击优先 UIA Invoke，再 PostMessage
3. **后台模式**：`DESKTOP_NO_FOCUS_STEAL=1` 时不抢前台；`DESKTOP_PHYSICAL_MOUSE=0`（默认）不移动光标。Qt/弱 UIA 应用仍可能需要前台
4. **视觉降级**：UIA 失败时用 ORB / OCR；微信搜索栏可用布局估算（仅目标确认为微信时）
5. **搜索输入**：仅当任务需要搜索时再点搜索控件并 `windows_type_text`；中文优先剪贴板 Ctrl+V
6. **核验等待**：`windows_wait(condition='desktop_change')` 或 `window:标题`；一步失败则 flow_halt，勿连环盲点
7. **合理等待**：应用启动后 wait 1-3s；gateway 返回逐步 results 便于排查

## 应用专属配方（可选，非核心代码）

应用差异写在本 Skill，由 Agent 用通用工具编排。示例（IM 发消息一类任务）：

1. `windows_focus_app(应用名)`
2. 仅需要时再点搜索 / `Ctrl+F` → `get_screen_text` 确认
3. `windows_type_text(关键词)` → 观察
4. `windows_press_key("Enter")` 打开条目
5. `windows_type_text(正文)` → 观察
6. `windows_press_key("Enter")` 提交 → 观察

禁止在平台 Python 核心路径写死某一 App 的热键宏。

## Hermes 使用方式

1. **用户点「启动智能体」**：平台写入 `HERMES_HOME/config.yaml` 的 `mcp_servers.testory-desktop`，并拉起 `:8766` + MCP `:9820`
2. 桌面任务优先调用 MCP `windows_*` / `get_screen_*`（observe→act→observe）
3. 若需流程知识：`skill_view(name='testory-windows-desktop')`
4. macOS 且已装 cua：可选 `skill_view(name='computer-use')` + `computer_use`
5. 收到 401 / 空流时**不要反复重试**同一请求
6. **混用**：Web 流程中出现系统弹窗时，切 MCP 桌面工具或本 skill
7. 弱 UIA 时：先 inspect，失败则视觉降级；勿假装成功

## 触发词

- Windows 桌面自动化 / GUI 自动化 / RPA
- 打开记事本 / 点击窗口按钮 / 任意桌面应用
- 桌面应用测试
- OS 弹窗 / UAC / 系统确认框

## 平台入口

- AI 测试左栏：桌面自动化层
- `/api/ai/agent/gateway-stream` + `platform: desktop` 意图
- 统一 Hermes 会话（platform=auto）内与 Web/API 同会话切换
- Skills 单一来源：`HERMES_HOME/skills` + `skill_view`
