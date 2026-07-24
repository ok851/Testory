# Bundled Hermes Skills

Testory 平台内置 Hermes Agent 技能位于 [`bundled/`](bundled/)。

启动时由 `hermes_skill_bootstrap.sync_bundled_skills_to_hermes()` 同步到 `%UAT_DATA_DIR%/hermes/skills/`。

| Skill ID | 用途 |
|----------|------|
| `testory-web-browser` | Web 画布 CDP attach + 人机协作 |
| `testory-android-mobile` | Android bridge + UC WebView |
| `testory-windows-desktop` | Desktop gateway UIA/视觉 |
| `testory-api-http` | HTTP / 接口用例与跨端 vars |
| `testory-cross-end` | 跨端编排 |
| `testory-risk-guard` | L0/L1/L2 审批门禁 |
| `testory-ui-design` | UI 审查与平台界面优化 |

## 附录 B 质量（R11）

标准：[docs/goai/SKILL_APPENDIX_B.md](../docs/goai/SKILL_APPENDIX_B.md)

```bash
python -m skills.skill_quality
python -m pytest tests/test_skill_appendix_b.py -q
```

手动强制同步：`POST /api/ai/skills/sync-bundled`
