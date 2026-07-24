# ERP 桌面样例（Fake ERP / 客户别名）

无需安装真实 ERP：用 Tk 窗口模拟胖客户端，窗口标题即订单号。也可经 `DESKTOP_APP_ALIASES` 的 `@erp` 启动客户客户端。

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
```

## 平台入口

- 跨端页 →「ERP 桌面样例」（Fake）或「ERP @erp 别名」
- `GET /api/ai/cross-end/erp-desktop-plan?mode=fake`
- `GET /api/ai/cross-end/erp-desktop-plan?mode=alias&alias=erp`

别名未配置时 `ready_to_run=false`，返回 `DESKTOP_ALIAS_MISSING`（不假绿）。

## 真实客户 ERP（别名）

`.env`：

```bash
# 仅 path
DESKTOP_APP_ALIASES={"erp":"C:\\\\ERP\\\\client.exe"}

# 或 path + args + 标题正则（可用 {order_id}）
DESKTOP_APP_ALIASES={"erp":{"path":"C:\\\\ERP\\\\client.exe","args":["/order","{order_id}"],"window_title_re":".*{order_id}.*"}}

# 可选：覆盖窗口标题正则
DESKTOP_ERP_WINDOW_TITLE_RE=.*ORD-.*
```

用 Fake ERP 验证别名通路时，可将对象别名指到本机 Python + `fake_erp_client.py`（见计划 `meta.suggest_alias_entry`）。

断言契约不变：`api_order_id` ↔ `erp_order_id`。
