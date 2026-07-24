---
name: testory-api-http
description: Testory 接口自动化：通过平台 HTTP 执行内核跑临时请求或已有接口用例，支持将响应字段写入跨端 vars。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: api
    risk_default: L1
    tags: [http, api, rest, cross-end]
---

# Testory 接口自动化（API）

Hermes **只编排**，执行落在 Testory 平台内核（勿自造第二套 HTTP 客户端逻辑）。

## 输入 / 输出 Schema

### 临时请求输入

| 字段 | 类型 | 说明 |
|------|------|------|
| `method` | string | GET/POST/PUT/PATCH/DELETE… |
| `url` | string | 完整 URL（优先测试环境） |
| `headers` | object | 可选 |
| `body` | any | JSON / 表单 |
| `vars_to_store` | list | 从响应抽取写入跨端 vars |

### 成功输出（摘要）

| 字段 | 说明 |
|------|------|
| `status` | HTTP 状态码 |
| `body` / extract | 响应体或 JSONPath 抽取 |
| `ok_assert` | 断言通过才为 true |

跨端阶段另含 `error_code`（如断言失败、抽取缺失 `VAR_EXTRACT_MISSING`）。

## 失败处理（诚实）

| 情况 | 结果 |
|------|------|
| 网络/DNS/TLS 失败 | 阶段失败，保留错误信息 |
| status 不在 `assert.status_in` | 断言失败，不得绿 |
| 必选 `vars_to_store` 抽不到 | `VAR_EXTRACT_MISSING` |
| L2 写操作无审批 | 编排层 `RISK_APPROVAL_REQUIRED`（见 RiskGuard） |

禁止：仅凭「返回了 JSON」当作业务成功。

## 安全边界

- 默认 **L1**；GET/HEAD 只读可视为 **L0**。  
- 清库、删生产、批量写 → **L2** + RiskGuard 令牌。  
- 不把 token/密码写入未脱敏日志；vars 中敏感键走平台脱敏。  
- 未知主机默认谨慎，优先测试域名。

## 能力

1. **临时请求**：method / url / headers / body → status + body 摘要  
2. **跑已有接口用例**：`case_id`（case_type=api）  
3. **跨端**：将 `token`、订单号等写入任务上下文 `vars`，供 Web/Mobile/Desktop 后续步骤使用  

## 平台入口（推荐）

Python（与 Flask 同进程或 skill 脚本）：

```python
from agent_api_runner import run_temp_http, run_api_case, summarize_for_agent

r = run_temp_http(method="POST", url="https://example.com/login", body={"user":"a","pass":"b"})
print(summarize_for_agent(r))
```

HTTP（需登录 Cookie / 内部密钥，由平台暴露时使用）：

- `POST /api/ai/agent/api-http` — body: `{method,url,headers,body}`
- `POST /api/cases/{id}/run` — 已有接口用例

## 铁律

1. 先只读探测，再写操作；批量写需说明风险。  
2. 成功后把关键字段写入 vars（如 `vars.auth_token`）。  
3. 断言写清：status / JSONPath / 包含文案。  
4. 与 UI 混用时：API 段不依赖屏幕；UI 段用结构化或视觉眼睛。

## 触发词

- 调用接口 / 断言 200 / 拿 token 再登录网页  
- 跑接口用例 / HTTP 探测  
