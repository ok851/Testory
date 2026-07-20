---
name: testory-api-http
description: Testory 接口自动化：通过平台 HTTP 执行内核跑临时请求或已有接口用例，支持将响应字段写入跨端 vars。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: api
    tags: [http, api, rest, cross-end]
    risk: medium
---

# Testory 接口自动化（API）

Hermes **只编排**，执行落在 Testory 平台内核（勿自造第二套 HTTP 客户端逻辑）。

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

## 风险

标注 `risk: medium`：可能触发生产写接口；默认对未知环境谨慎，优先用测试环境 URL。
