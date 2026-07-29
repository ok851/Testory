# Unified Step IR（手机 APK ↔ PC sync）

跨端共用同一套步骤字段。手机推送时顶层 IR 字段会并入 `mobile_spec`；拉取时从 `mobile_spec` + 顶层还原。

## Action（`action` 小写）

| action | 说明 | 主要参数 |
|--------|------|----------|
| `tap` / `long_press` / `swipe` / `input` | 交互 | locator / `input_value` / swipe |
| `assert` | 断言 | `assert_text`, `assert_type` |
| `wait` / `wait_until` | 等待 / 等到文本出现 | `wait_duration_ms`, `until_assert_text` |
| `screenshot` | 截图 | `save_as`（可选路径变量） |
| `extract_text` | 提取文本 | `save_as` |
| `scroll` / `scroll_until` | 滚动 / 滚到文本 | `swipe_direction`, `until_assert_text` |
| `press_key` / `close_app` / `open_app` / `back` / `home` | 系统 | `key_code` / package |
| `scan_qr` | 扫码 | `roi`, `save_as` |
| `solve_captcha` | 自动解验证码（VLM） | `roi`, `captcha_hint`, `captcha_fallback` |
| `human_gate` | 验证码操作（暂停等人） | — |
| `repeat` / `while` | 轻量循环（等到 until） | `repeat_max`, `until_assert_text` |

## 透传字段

顶层或 `mobile_spec` 均可：

- `assert_text`, `assert_type` (`contains`\|`equals`\|`visible`\|`not_visible`)
- `wait_duration_ms`, `pre_wait_ms`, `max_retries`, `optional`
- `save_as`, `key_code`, `repeat_max`, `until_assert_text`
- `captcha_hint`, `captcha_fallback` (`human_gate`\|`fail`), `roi` `[l,t,r,b]`
- `scroll_amount`, `swipe_direction`

## 变量

步骤字符串支持 `{{name}}`。`extract_text` / `scan_qr` 写入 `variables`；数据驱动用例可附 `dataRows`。

## 回传

```json
{
  "success": true,
  "variables": {"order_id": "A123"},
  "evidence": "/data/.../shot.png"
}
```

## PC 辅助 API

- `POST /api/mobile/sync/captcha/solve` — `{ image_base64, captcha_hint }` → `{ solution }`

## 编创（手机）

录制 → 用例详情点步骤编辑（改/删/插/重排）→ enrichment（标断言/提取）→ 屏上拾取 → AI 意图。
