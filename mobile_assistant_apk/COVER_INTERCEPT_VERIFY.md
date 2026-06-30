# Cover 拦截手动验证清单

录制前：安装最新 `testory-assistant.apk`，开启无障碍，进入待测 App。

| # | 操作 | 期望 |
|---|------|------|
| 1 | 点「开始录制」，warmup 内（约 0.5s）快速点 App 按钮 | 不产生步骤 |
| 2 | warmup 结束后点 App 按钮 | 正常录制 tap |
| 3 | 点悬浮条「暂停」 | 不产生 tap；暂停后 App 点击不录 |
| 4 | 点「继续」后再点 App | 恢复录制 |
| 5 | 点悬浮条「结束」 | 不产生 tap；录制停止 |
| 6 | 点「暂停」后 200ms 内点 App（若已继续） | App 点击应被录制，不被全局 suppress |
| 7 | 回放用例（RunOverlay 显示） | 不写入录制步骤 |

自动化：`mobile_assistant_apk` 下运行 `./gradlew :app:testDebugUnitTest`（`OverlaySpatialFilterTest`）。
