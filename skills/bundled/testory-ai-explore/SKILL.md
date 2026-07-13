---
name: testory-ai-explore
description: Testory AI 探索测试引擎：自动发现 Web/Desktop 应用的交互元素，遍历潜在路径，检测白屏、崩溃、低对比度等异常，生成结构化探索报告。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: web, desktop
    tags: [exploration, anomaly-detection, coverage, monkey-testing, web, desktop]
---

# Testory AI 探索测试

## 核心铁律

1. 探索引擎不替换已有测试用例，而是作为补充：发现未覆盖的页面路径和潜在缺陷。
2. Web 探索使用 embedded browser CDP attach，与用户画布共享同一 Chromium 实例。
3. Desktop 探索使用屏幕网格点击策略，OCR 辅助识别文本区域。

## 探索策略

| 策略 | 说明 | 适用 |
|------|------|------|
| `greedy` | 贪心遍历所有可交互元素 | Web 表单/列表页 |
| `random` | 随机点击屏幕网格区域 | Desktop 应用 |
| `model_driven` | LLM 驱动理解页面结构 | 复杂工作流 |

## 异常检测

| 检测器 | 触发条件 |
|--------|----------|
| 白屏检测 | 页面像素方差 < 阈值 |
| 崩溃检测 | 元素不可访问、连接断开 |
| 低对比度检测 | 可见文本与背景对比度 < WCAG AA |
| JS 错误检测 | console.error 监听 |

## 使用方式

```bash
# Web 探索
curl -X POST /api/ai/explore/start \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "platform": "web", "strategies": ["greedy"], "max_depth": 3}'

# 获取状态
curl /api/ai/explore/status

# 获取报告
curl /api/ai/explore/report
```

## 报告结构

```json
{
  "summary": {"total_steps": 45, "anomalies": 2},
  "anomalies": [
    {"type": "white_screen", "url": "...", "severity": "high"}
  ],
  "screenshots": ["step_001.png", "step_anomaly_007.png"],
  "coverage": {"visited_urls": 12, "unique_pages": 8}
}
```

## 预算控制

- `max_depth`: 最大探索深度 (默认 5)
- `max_steps`: 最大步骤数 (默认 30)
- `scope_urls`: 限定域名范围，避免爬出目标站点

## 不适用场景

- 需要登录的页面（请使用 ai-test 画布手动登录后探索）
- 纯 API 测试
- 需要精确断言的场景（用已编写的用例测试）
