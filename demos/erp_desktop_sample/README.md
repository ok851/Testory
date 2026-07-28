# ERP 桌面样例（Fake ERP / 客户别名）

无需安装真实 ERP：用 Tk 窗口模拟胖客户端，窗口标题即订单号。也可经 `@erp` 启动**真实客户客户端**。

## 验收

```bash
# 通过：API 种子订单号 == 窗口标题（Fake ERP）
python demos/erp_desktop_sample/run_accept.py

# 经 @erp 别名（未配置时进程内临时注入 Fake ERP，不改 .env）
python demos/erp_desktop_sample/run_accept.py --mode alias

# 诚实：别名缺失
python demos/erp_desktop_sample/run_accept.py --mode alias --expect-missing

# 诚实失败：故意不一致不得假绿
python demos/erp_desktop_sample/run_accept.py --mismatch

# 真实客户 ERP：持久化别名到 data/desktop_aliases.json 并验收
python demos/erp_desktop_sample/run_accept.py --customer-exe "C:\ERP\client.exe" --window-title-re ".*{order_id}.*" --order-id ORD-DEMO-404
```

## 平台入口

- 跨端页 →「ERP 桌面样例」（Fake）/「ERP @erp 别名」/「配置客户 ERP」
- `GET /api/ai/cross-end/erp-desktop-plan?mode=fake`
- `GET /api/ai/cross-end/erp-desktop-plan?mode=alias&alias=erp`
- `PUT /api/desktop/aliases/erp` — 持久化客户 path / args / window_title_re
- `GET /api/desktop/aliases/erp/probe` — 探测 path 是否存在（不启动）

别名未配置时 `ready_to_run=false`，返回 `DESKTOP_ALIAS_MISSING`（不假绿）。

## 真实客户 ERP

优先级：`.env DESKTOP_APP_ALIASES` > `data/desktop_aliases.json` > 本机目录 catalog。

```bash
# .env（可选）
DESKTOP_APP_ALIASES={"erp":{"path":"C:\\\\ERP\\\\client.exe","args":["/order","{order_id}"],"window_title_re":".*{order_id}.*"}}
DESKTOP_ERP_WINDOW_TITLE_RE=.*ORD-.*
```

或在跨端页点「配置客户 ERP」写入 `data/desktop_aliases.json`（推荐，无需手改 .env）。

断言契约不变：`api_order_id` ↔ `erp_order_id`。
