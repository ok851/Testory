# Demo：前端 UI Agent（离线启发式）

```bash
python demos/frontend_ui_agent/run_sample.py
```

输出写入 `artifacts/frontend-ui-agent/`：

- `analysis.json` — 组件清单 / 稳定度分桶
- `drafts.json` — 待审用例草案（含 testid 定位步骤）

平台页：`/ai-design`（上传 `.tsx/.vue` 等，后台自动识别组件并生成）  
CI 打包：`docs/examples/pack_code_change_payload.py`
