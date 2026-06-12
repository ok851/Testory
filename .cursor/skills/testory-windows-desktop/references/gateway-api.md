# Desktop Automation Gateway API

Base URL: `http://127.0.0.1:8766`（`DESKTOP_AGENT_GATE_PORT`）

所有请求需 header：`X-Desktop-Agent-Secret: <DESKTOP_AGENT_GATEWAY_SECRET>`

## POST /internal/session

创建会话。

**Response:**
```json
{"success": true, "session_id": "uuid"}
```

## POST /internal/session/{session_id}/run-steps

**Body:**
```json
{
  "steps": [
    {
      "action": "launch_app",
      "input_value": "notepad.exe",
      "description": "打开记事本"
    },
    {
      "action": "wait",
      "input_value": "2"
    },
    {
      "action": "click",
      "selector_type": "visual_template",
      "selector_value": "templates/notepad_edit.png",
      "description": "点击编辑区"
    }
  ]
}
```

**Response:**
```json
{
  "success": true,
  "results": [
    {"status": "success", "action": "launch_app", ...}
  ]
}
```

## POST /internal/session/{session_id}/inspect

**Body:**
```json
{
  "max_depth": 4,
  "max_nodes": 120,
  "desktop_spec": {}
}
```

**Response:**
```json
{
  "success": true,
  "nodes": [
    {"name": "...", "control_type": "Button", "automation_id": "...", "bounds": {...}}
  ]
}
```

## 错误码

- `401` — secret 不匹配
- 步内异常 — `success: false` + `results` 末项含 `error`

## 与 Hermes computer_use 的关系

Hermes 可直接操控桌面时，仍应遵循 **UIA 优先 → 视觉降级** 策略，与 gateway 引擎一致。
