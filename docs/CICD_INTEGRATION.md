# CI/CD 对接说明（Jenkins / GitLab CI 等）

> 版本：2026-07-24  
> 上级：[PRODUCT_NORTH_STAR.md](./PRODUCT_NORTH_STAR.md)  
> 依赖：[EXECUTION_RELIABILITY_STANDARD.md](./EXECUTION_RELIABILITY_STANDARD.md)（执行必须先可信）

---

## 1. 结论：要对接，但是分工而不是互相替代

| 角色 | 职责 | 不负责 |
|------|------|--------|
| **Jenkins / GitLab CI / GitHub Actions** | 编排：拉代码、构建、单测、**触发** Testory、根据结果门禁、部署 | 在无桌面/无真机的 CI 容器里直接点 Windows ERP、本机浏览器、扫码登录 |
| **Testory** | **质量执行引擎**：在具备交互桌面 / 本机浏览器 / 设备的节点上执行 Web·Desktop·Mobile·API，产出诚实历史与报告 | 替代整条 DevOps 流水线 |

企业标准闭环：

```text
开发提交 → CI 构建/单测 → CI 调用 Testory 触发回归
         → Testory 在执行节点跑完 → 回传状态/JUnit
         → CI 红灯阻断合并或部署 / 绿灯继续
```

**不推荐作为主路径：** 把平台用例导出成脚本，交给 Jenkins「自己跑 UI」。会丢失桌面会话、HITL、多端网关与统一报告，也与北极星冲突。  
**可选补充：** 纯 Web 团队可额外导出 Playwright 供 CI 容器跑；**主路径仍是 CI 触发平台。**  
**存量 Jenkins Job：** 可用本平台 **反向触发** Jenkins（见 §2.5）；触发成功 ≠ Job 通过。

---

## 2. 推荐集成契约（Phase 0c MVP）

### 2.1 触发执行

`POST /api/ci/runs`（**已落地**：默认同步；`async=true` 异步）

认证：`Authorization: Bearer <API Token>` 或登录 Session（`token_or_login_required`）。  
能力门禁：需要 `ci_integration`（企业档；本机 `standalone` 默认可试用；商业部署设 `LICENSE_ENFORCE_FEATURES=1` 后按证书拦截）。详见 [PRODUCT_TIERS.md](./PRODUCT_TIERS.md)。

请求建议字段：

| 字段 | 说明 |
|------|------|
| `project_id` / `case_ids` | 跑什么（`suite_id` 暂未接入，请用二者之一） |
| `trigger_source` | `jenkins` / `gitlab` / `github` / `manual` |
| `build_id` / `pipeline_id` | CI 构建号（写入 run 记录） |
| `git_sha` / `branch` | 可选 |
| `fail_on` | 默认行为：仅 `status==success` 绿灯（与执行标准一致） |
| `async` / `sync:false` | 异步入队，HTTP **202**，`status=queued`，需轮询 |
| `callback_url` / `webhook_url` | 终态后 POST 标准回调（可选） |

响应（节选）：

| 字段 | 说明 |
|------|------|
| `run_id` / `job_id` | 查询句柄 |
| `status` | `queued` / `running` / `success` / `failed` |
| `terminal` | GET 时：是否终态 |
| `success` / `gate_passed` | **仅全部用例门禁通过为 true**（进行中时为 false） |
| `poll_url` | `GET /api/ci/runs/<id>` |
| `junit_url` | `GET /api/ci/runs/<id>/junit.xml`（终态后才有完整 XML） |

**平台内查看：** 顶栏 **「CI/CD」**（`/cicd`）可手动触发、查看近期运行与 curl 示例；`/run-history` 列表可显示 `build …` 徽章；详情含 CI run 链接与「导出证据包」。CI 触发写入的 `build_id` / `ci_run_id` 进入该条历史的 `expected_text` 信封。

异步轮询伪代码：

```bash
# 触发
curl -X POST "$URL/api/ci/runs" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id":1,"async":true,"build_id":"42","callback_url":"https://ci.example/hook"}'
# 轮询直至 terminal
curl "$URL/api/ci/runs/$RUN_ID" -H "Authorization: Bearer $TOKEN"
```

结束回调 body（节选）：`{ run_id, status, success, gate_passed, build_id, junit_url, poll_url, passed, failed, total }`。

兼容旧接口：`POST /api/trigger/<project_id>`、`POST /api/trigger/cases` 现已返回同样的 `run_id`/`gate_passed`/`junit_url`，且 **`success` 不再恒为 true**。

### 2.2 查询与门禁

- `GET /api/ci/runs/<id>` → 终态 + 计数（passed/failed）  
- **CI 门禁规则：** 仅 `status == success` / `gate_passed == true` 绿灯  
- 下载 JUnit：`failures`+`errors` 与未通过门禁的用例数一致（`warning`/`skipped` 记为 `<failure>`，硬错误为 `<error>`）

样例流水线：

- Jenkins：[examples/Jenkinsfile.testory](./examples/Jenkinsfile.testory)
- GitLab：[examples/gitlab-ci.testory.yml](./examples/gitlab-ci.testory.yml)

### 2.3 回调（可选）

运行结束 `POST` 客户 webhook：`{ build_id, status, report_url, junit_url }`。  
可复用现有通知 webhook 能力，但 payload 需标准化。

### 2.4 执行节点

| 模式 | 说明 |
|------|------|
| 本机桌面版 | 开发机/测试机常开 Testory，CI 经内网/隧道调 API（注意安全） |
| server + client | 团队服务器收触发，client 在有桌面的机器执行（与架构文档对齐） |
| 标签机 | 专用 Windows runner 注册为执行节点（远期） |

CI Agent 所在机器 **不等于** UI 执行机器——除非明确同机部署。

### 2.5 本平台 → Jenkins（反向触发，已落地）

适用：企业大量**存量用例/Job 仍在 Jenkins**，需要从 Testory 页面或 API 发起构建。

| 项 | 说明 |
|----|------|
| 环境变量 | `JENKINS_URL`、`JENKINS_USER`、`JENKINS_API_TOKEN` |
| 状态 | `GET /api/ci/jenkins/status` |
| 触发 | `POST /api/ci/jenkins/trigger` body: `{ "job_name": "folder/job", "parameters": {...} }` |
| UI | `/cicd` →「从本平台触发 Jenkins Job」 |

**诚实边界：** HTTP 201/队列 URL 仅表示 Jenkins **已受理构建**；`jenkins_build_claimed_pass` / `case_pass_claimed` 恒为 false。  
统一门禁与两侧结果同步见 **§2.6**。

推荐组合：

```text
# 存量 API/脚本类 Job + 统一门禁
Testory → POST /api/ci/sync（jenkins_job + 可选 testory_run_id）
       → Jenkins 受理 → 平台轮询 result / Job 回写
       → unified_gate_passed

# 新增 Web/Desktop/跨端回归（主路径）
Jenkins 流水线 → POST /api/ci/runs → Testory 执行 → JUnit 回 Jenkins
              → 可选 POST /api/ci/sync 绑定两侧
```

### 2.6 统一门禁同步（Testory ↔ Jenkins，已落地）

| 项 | 说明 |
|----|------|
| 创建 | `POST /api/ci/sync`：`{ testory_run_id?, jenkins_job?, parameters?, policy }` |
| 查询 | `GET /api/ci/sync/<sync_id>`（默认 `refresh=1` 刷新两侧） |
| 列表 | `GET /api/ci/sync` |
| Jenkins 回写 | `POST /api/ci/sync/<id>/jenkins`：`{ result, build_url?, build_number? }` |
| 触发时纳入 | `POST /api/ci/jenkins/trigger` 带 `create_sync: true` |
| UI | `/cicd` →「统一门禁」 |
| 策略 | 默认 `both_must_pass`；可选 `either_pass` |
| 持久化 | `data/ci_sync/<sync_id>.json` |

**同步行为：**

1. **Jenkins 受理**：`jenkins_job` 存在时调用 Remote API 触发；成功仅表示入队（`queue_url`）。
2. **Jenkins → Testory**：后台轮询 queue→build→`result`；或 Job 主动 `POST .../jenkins`。
3. **Testory → Jenkins**：CI run 终态后钩子刷新 sync；可选向 build `submitDescription` 写入摘要（**不改写** Jenkins `result`）。
4. **`unified_gate_passed`**：仅在策略要求的两侧均终态后才可能为 true；任一侧失败为 false；进行中绝不假绿。

```bash
# 绑定已有 run 并让 Jenkins 受理构建
curl -X POST "$URL/api/ci/sync" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"testory_run_id":"run-xxx","jenkins_job":"folder/job","policy":"both_must_pass"}'

# 轮询统一门禁
curl "$URL/api/ci/sync/$SYNC_ID" -H "Authorization: Bearer $TOKEN"

# Jenkinsfile 末尾回写（可选，与轮询二选一或互补）
curl -X POST "$URL/api/ci/sync/$SYNC_ID/jenkins" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"result\":\"${currentBuild.result}\",\"build_url\":\"${env.BUILD_URL}\",\"build_number\":${env.BUILD_NUMBER}}"
```

### 2.7 代码变更感知（影响分析 → 推荐回归）

> CI **已 checkout** 后把 `changed_files` / `diff` / **`file_snippets`**（前端源码内容）传给平台；**不要求**平台再克隆私有仓。有 `file_snippets` 时会跑前端组件识别并增强匹配信号。自动生成用例默认 **待审核**，不进绿灯门禁。

| 项 | 说明 |
|----|------|
| 分析入队 | `POST /api/ci/code-change`（默认 202 异步） |
| **file_snippets** | `{ "src/Login.tsx": "<源码>" }`；推荐用 `docs/examples/pack_code_change_payload.py`（可拷到业务仓 `ci/`）或平台侧 `scripts/build_code_change_payload.py` |
| 查询 | `GET /api/ci/code-change/<task_id>`；列表 `GET /api/ci/code-change`；结果可含 `impact.frontend_components` |
| 触发推荐回归 | `POST /api/ci/code-change/<task_id>/trigger-run` → 内部走 `/api/ci/runs` |
| 生成草稿 | `POST /api/ci/code-change/<task_id>/generate-drafts`（描述含 `[review_status:pending]`） |
| 手工分析 | Hub：`/ai-design` 上传 `.tsx/.vue` 等源码（后台自动识别组件并生成）；API：`POST /api/ai/hub/design/preview`（`input_kind=frontend_source`）或 `POST /api/ai/hub/analyze/frontend-code` |
| 自愈提案 | 推荐跑失败后自动钩子回写；亦可 `GET ...?refresh_heal=1`；`POST .../heal-proposals/<id>/ack` 仅确认人工处理，**不自动改步骤假绿** |
| 仓库映射 | `GET/POST/DELETE /api/ci/repo-map`：`{repo, project_id, default_branch?}`；webhook/分析可按 repo 自动解析 project |
| 待审核用例 | `GET /api/ci/cases/pending?project_id=`；`POST /api/ci/cases/<id>/review` `{status:active\|rejected}`；**CI 默认排除 pending/rejected**（`include_pending_review=true` 可覆盖） |
| 指标/清理 | `GET /api/ci/code-change/metrics`；`POST /api/ci/code-change/cleanup`（TTL，默认 `CODE_INTEL_TASK_TTL_DAYS=30`） |
| SCM Webhook（可选） | `POST /api/ci/webhooks/github`（`X-Hub-Signature-256` + `TESTORY_GITHUB_WEBHOOK_SECRET`）；`POST /api/ci/webhooks/gitlab`（`X-Gitlab-Token` + `TESTORY_GITLAB_WEBHOOK_SECRET`）；可选 `CODE_INTEL_WEBHOOK_IP_ALLOWLIST` |
| UI | `/cicd` →「代码变更感知」；`/ai-design` → 需求/源码生成（多平台） |
| 持久化 | `data/ci_code_change/<task_id>.json`；同 `git_sha`/`mr_key` 时间窗幂等（`CODE_INTEL_DEDUP_WINDOW_MIN`） |

环境变量（节选）：`CODE_INTEL_USE_LLM`、`CODE_INTEL_LLM_TIMEOUT_S`、`CODE_INTEL_MAX_DIFF_CHARS`、`CODE_INTEL_RATE_LIMIT_PER_MIN`、`CODE_INTEL_TASK_TTL_DAYS`、`LOCAL_MEMORY_ENABLE`（embedding 匹配加权）。

**诚实边界：** 影响分析是风险建议而非精确破坏证明；草稿用例须人工审核激活后才进门禁；自愈提案不得宣称「已因代码变更自动修复并通过」。

**GitLab/Jenkins 样例：** `docs/examples/gitlab-ci.testory.yml`、`docs/examples/Jenkinsfile.testory`（analyze 阶段先跑 pack 脚本再 `POST`）。

---

## 3. Jenkins 示例（目标形态）

```groovy
pipeline {
  agent any
  stages {
    stage('Build') { steps { sh 'echo build' } }
    stage('Testory Regression') {
      steps {
        script {
          def r = httpRequest httpMode: 'POST',
            url: "${TESTORY_URL}/api/ci/runs",
            customHeaders: [[name: 'Authorization', value: "Bearer ${TESTORY_TOKEN}"]],
            contentType: 'APPLICATION_JSON',
            requestBody: """{"suite_id":"${SUITE}","build_id":"${env.BUILD_ID}","git_sha":"${env.GIT_COMMIT}","trigger_source":"jenkins"}"""
          def job = readJSON text: r.content
          // poll until terminal...
          httpRequest url: "${TESTORY_URL}${job.junit_url}", outputFile: 'testory-junit.xml'
          junit 'testory-junit.xml'
        }
      }
    }
  }
}
```

GitLab CI 同理：`script` 里 curl 触发 + `artifacts:reports:junit`。

---

## 4. 与现有能力的关系

| 已有 | 用途 |
|------|------|
| 用例/批量/调度执行 | 执行后端；CI 应复用而非旁路 |
| `run_history` / 报告导出 | 人类审计；须与 CI 结论同源 |
| 通知 webhook（钉钉等） | 可扩展为 CI 回调，勿与「测试通过」假消息混用 |
| `execution_lock` | 执行节点防并发；CI 密集触发时需队列或多节点 |

### 4.1 落地前过渡方案（可接受）

`/api/ci/runs` **已可用**。旧脚本若仍调 `/api/trigger/...`，请改读 `gate_passed`/`status`，勿再假设 `success` 恒 true。

---

## 5. 安全与合规

| 项 | 要求 |
|----|------|
| 认证 | CI Token / 服务账号；最小权限（仅触发指定项目） |
| 网络 | 优先内网；公网需 TLS + IP 允许列表 |
| 密钥 | 不进 JUnit 与公开 artifacts；遵循脱敏策略 |
| 审计 | `trigger_source` + `build_id` 写入历史，可追责 |

---

## 6. Phase 0c 完成门禁

| # | 项 | 状态 |
|---|----|------|
| 1 | 文档 + Jenkins/GitLab 样例 | **已提供** `docs/examples/*`（含异步轮询） |
| 2 | 故意失败套件 → CI 红灯 | 依赖执行节点；JUnit failures 单测覆盖 |
| 3 | 全绿套件 → 绿灯 + build_id 可查 | run 记录含 `build_id` |
| 4 | JUnit failure 数与门禁失败一致 | `tests/test_ci_adapter.py` |
| 5 | 状态映射单测 | 与 `is_execution_gate_success` 对齐 |
| 6 | 异步 queued→running→终态 + 回调 | **0c-2 已落地** |

后续可选：`suite_id` 标签过滤、多执行节点调度。

---

## 7. 常见误区

| 误区 | 纠正 |
|------|------|
| 「有 Jenkins 就不用 Testory」 | Jenkins 管编排；UI/桌面/真机仍要执行引擎 |
| 「有 Testory 就不用 CI」 | 企业仍需构建门禁与发布编排 |
| 「平台触发了 Jenkins 就等于用例过了」 | 仅发出构建请求；结果仍在 Jenkins，除非再回调本平台 |
| 「导出脚本进 CI 更标准」 | 对纯 Web 单测可以；混合流/桌面主路径不成立 |
| 「CI 绿了就代表断言都过」 | 若平台假绿，CI 只是放大假绿——故 0c 依赖 0a |

---

## 8. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-24 | 首版：分工模型、契约、门禁、误区 |
| 2026-07-24 | **0c-1**：落地 `POST/GET /api/ci/runs` + JUnit；旧 trigger 诚实 `success`；样例 Jenkins/GitLab |
| 2026-07-24 | **0c-2**：`async` 入队（202/queued/running）+ `callback_url` 终态回调；样例改为轮询 |
| 2026-07-24 | **Phase 0c MVP Done**：触发 API + `build_id`/`git_sha` 关联 + 状态查询 + JUnit + webhook；Jenkins/GitLab 样例可复现；门禁与执行标准一致（仅 `success` 绿灯） |
| 2026-07-27 | **代码变更感知**：`POST/GET /api/ci/code-change` 影响分析+推荐回归；可选草稿生成/SCM webhook；自愈提案不假绿；见 §2.7 |
| 2026-07-27 | **前端 UI Agent**：`POST /api/ai/hub/analyze/frontend-code` 组件识别；`POST /api/ai/hub/generate/from-frontend-code` 按稳定定位知识生成待审用例 |
| 2026-07-27 | **file_snippets 闭环**：CI 样例用 `pack_code_change_payload.py` 上传源码；pipeline 融合组件清单；源码生成并入 `/ai-design`（按文件类型分流，无独立分析页） |
