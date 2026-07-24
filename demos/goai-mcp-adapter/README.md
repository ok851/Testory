# GOAI MCP 适配器样例（R12）

离线可跑的 **Desktop** 工具连接层样例：列出与生产一致的工具 Schema，并用假端口演示 `tools/call` 失败诚实语义。

**不**启动真实桌面应用；真机请走平台 MCP / Gateway。

## 复现

```bash
# 列出工具 Schema
python demos/goai-mcp-adapter/run_sample.py --list

# 假端口：未知工具 / 无环境调用
python demos/goai-mcp-adapter/run_sample.py --demo-call

# 写出 artifacts
python demos/goai-mcp-adapter/run_sample.py --out artifacts/goai-mcp-adapter
```

期望：退出码 0；产物含 `tools.json` 与 `demo_calls.json`；`demo_calls` 中无环境调用的 `ok=false`。
