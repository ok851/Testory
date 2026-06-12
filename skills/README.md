# Bundled Hermes Skills

Testory 平台内置 Hermes Agent 技能位于 [`bundled/`](bundled/)。

启动时由 `hermes_skill_bootstrap.sync_bundled_skills_to_hermes()` 同步到 `%UAT_DATA_DIR%/hermes/skills/`。

| Skill ID | 用途 |
|----------|------|
| `testory-web-browser` | Web 画布 CDP attach + 人机协作 |
| `testory-android-mobile` | Android bridge + UC WebView |
| `testory-windows-desktop` | Desktop gateway UIA/视觉 |
| `testory-ui-design` | UI 审查与平台界面优化 |

手动强制同步：`POST /api/ai/skills/sync-bundled`

源 skill 包（已迁移，可删除）：根目录 `tencent-novnc-*`、`appium-android-adb-*`、`windows-gui-automation-cn-*`、`ui-new-*`。
