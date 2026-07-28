# 跨端 Agent 工具契约（大脑 / 双手）

> Testory 跨端 Agent：在 **PC 上思考**，通过工具驱动 **桌面 UIA / 浏览器 CDP / 手机本机 APK**，在一次任务中完成多端联动。

## 角色

| 角色 | 职责 |
|------|------|
| Agent（大脑） | 选工具、读返回、写共享变量、决定下一步 |
| 桌面执行器 | `desktop_*` / `windows_*` → 本机 UIA |
| 手机执行器 | `mobile_*` → sync enqueue → **手机 APK 本机执行** → await |
| 浏览器执行器 | Hermes / CDP（现有） |

正式路径：**禁止**用 adb 逐步遥控手机当执行引擎。

## 工具面

### 桌面（别名 → 现有 windows_*）

| 工具 | 说明 |
|------|------|
| `desktop_launch` | 启动应用 |
| `desktop_focus` | 聚焦窗口 |
| `desktop_click` | 短控件名点击 |
| `desktop_input` | 输入文本 |

### 手机（本机 await）

| 工具 | 说明 | 关键返回 |
|------|------|----------|
| `mobile_extract_otp` | 从通知/短信取验证码 | `variables.sms_otp` |
| `mobile_run_steps` | 下发步骤本机回放 | `job_id` + results |
| `mobile_run_case` | 按 case_id 本机跑 | 同上 |

环境变量 `MOBILE_OTP_MOCK=123456`：无真机时立即返回 mock 码（CI）。

### 共享 context

常用键：`phone_number`、`sms_otp`、`order_id`。跨端 stage 可用 `{{sms_otp}}` 引用。

---

## 验收剧本：桌面注册 + 手机取码 + 回填

文件：[demos/cross_end/desktop_mobile_otp_plan.json](../demos/cross_end/desktop_mobile_otp_plan.json)

```mermaid
sequenceDiagram
  participant Agent
  participant Desk as desktop_UIA
  participant Phone as phone_APK

  Agent->>Desk: desktop_input 手机号
  Agent->>Desk: desktop_click 发送验证码
  Agent->>Phone: mobile_extract_otp
  Phone-->>Agent: sms_otp
  Agent->>Desk: desktop_input sms_otp
  Agent->>Desk: desktop_click 提交
```

变量约定：

| 阶段 | 写入 | 读取 |
|------|------|------|
| desktop_fill_phone | `phone_number`（可选） | — |
| mobile_extract_otp | `sms_otp` | — |
| desktop_submit_otp | — | `{{sms_otp}}` |

---

## 实现入口

- Python 工具实现：[`mobile_cross_end_tools.py`](../mobile_cross_end_tools.py)
- Agent 挂载：`ai_chat_tool_loop.chat_tool_schemas` / `dispatch_cross_end_tool`
- Job 队列：`mobile_sync_store.enqueue_run_job(..., job_kind=extract_otp)`
- 手机拉取：`GET /api/mobile/sync/run/pending`（含 `job_kind`）
