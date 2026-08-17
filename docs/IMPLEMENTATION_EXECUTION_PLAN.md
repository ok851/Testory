# 合并版落地执行计划说明

## 1. 目标
本文件用于说明当前已落地的合并执行计划产物，目标是：

- 以当前代码为基座
- 先验证关键能力瓶颈
- 再逐步收敛执行核心、验证层、自愈层、DevOps 闭环

## 2. 已新增的核心产物

### 2.1 `execution_events.py`
统一执行事件标准，包含：

- `route_decided`
- `tool_registered`
- `tool_call_start`
- `tool_call_end`
- `observation_start`
- `observation_end`
- `assertion_start`
- `assertion_end`
- `heal_attempt`
- `risk_decision`
- `done`

用途：
- 时间线
- 执行回放
- 问题定位
- 后续前端事件标准化

## 3. 已新增的工具与能力骨架

### 3.1 `agent_tool_registry.py`
当前目标不是立即替代原有工具调度，而是把现有工具按执行通道明确分组：

- desktop
- observation
- execution_agent
- planning

其中 `execution_agent` 已纳入 `api_call`，用于支持 API 与 UI 并列的执行通道策略。

### 3.2 `assertion_service.py`
统一断言入口，当前已接入：

- 跨端一致性断言
- DB 只读断言骨架

### 3.3 `db_assertion.py`
第一阶段 DB 验证骨架，已强制：

- 只允许 SELECT
- 关键字黑名单
- 表名白名单
- readonly 连接策略
- 行数限制
- scalar 断言能力

## 4. 已新增的能力基线评测脚本

### 4.1 `scripts/visual_baseline_benchmark.py`
用于先验证：

- UIA 命中率
- OCR 命中率
- Vision 命中率

当前为骨架实现，建议后续逐步接入真实执行器。

## 5. 已落地的关键能力增量

### 5.1 双通道执行骨架
已新增 `api_call` 工具 schema，并在 `AGENT_API_EXECUTION_ENABLE=1` 时注入 Agent 工具集。

### 5.2 自愈审计链路
已在自愈提案链路中加入 `HEAL_ATTEMPT` 审计事件，并通过 `build_heal_proposals_from_run_audited` 统一调用。

## 6. 当前实施与合并计划的对应关系

### Phase 0：现状收口与能力基线
- 已新增工具注册骨架
- 已新增执行事件标准
- 已新增基准评测脚本

### Phase 1：执行核心收敛
- 当前先建立可审计结构
- 后续再逐步拆解 `ai_chat_tool_loop.py`

### Phase 2：验证层统一
- 已新增统一断言服务
- 已新增 DB 只读断言骨架

### Phase 3：自愈分层
- 当前代码库已有 self-heal 矩阵
- 已补充自愈审计链路

### Phase 4：DevOps 闭环
- 当前已有 webhook/impact/generate/heal proposals 骨架
- 后续重点补齐状态流与审核闭环

## 7. 后续建议
### 优先级
1. 接入真实基准评测结果
2. 逐步抽取工具注册表
3. 增强双通道执行选择
4. 扩展 DB 安全断言
5. 打通执行时间线

## 8. 结论
当前实施保留了：

- 方向正确性
- 工程现实性
- 关键能力验证点

不是重写，而是增量演进。
