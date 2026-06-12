---
name: testory-ui-design
description: Testory UI 设计审查与界面优化：Material 3 / Apple HIG 规范，用于被测页面可见性诊断及平台自身 Web UI 迭代。
version: 1.0.0
source: testory-bundled
format: agentskills.io/v1
metadata:
  testory:
    platform: ui
    tags: [ui-design, accessibility, material, hig, audit]
---

# Testory UI 设计审查

## 双重用途

1. **Hermes 测试 Agent**：审查被测 Web/App 页面 — 判断「元素不可见/不可点」是否由 UI 设计问题导致
2. **平台开发**：优化 Testory 自身 `static/`、`templates/` 界面

## 触发场景

- 自动化失败且 selector 正确但元素不可交互
- 用户请求「审查 UI」「优化界面」「设计系统」
- Heal 时怀疑对比度/触控区域/层级问题

## UI Audit 检查清单（6 维度）

### 1. 视觉层级
- 信息优先级是否清晰？
- 重要 CTA 是否足够突出？

### 2. 色彩系统
- 文字对比度 WCAG AA ≥ 4.5:1
- 色彩使用是否一致？

### 3. 排版
- 字体层级 ≤ 3 级
- 行高/字间距舒适

### 4. 间距与对齐
- 8pt 网格
- 组件内外间距统一

### 5. 组件一致性
- Hover/Active/Disabled/Focus 状态完整
- 圆角、阴影系统化

### 6. 触控与无障碍
- 最小点击区域 **44×44px**（Apple HIG）
- Focus 环可见

## 与自动化的关联

| 症状 | 可能 UI 原因 | 建议 |
|------|-------------|------|
| 按钮 click 无响应 | 透明 overlay、z-index | 检查 stacking context |
| 文字 assert 失败 | 对比色过低、字体过小 | 审查 on-surface 对比度 |
| 移动端 tap 偏移 | 触控区 < 44px | 扩大 hit area |
| 元素「存在但不可见」 | opacity:0 / visibility | 审查 CSS 状态 |

## 参考文件

- `references/color-systems.md` — Material 3 / Apple 色板
- `references/component-patterns.md` — 按钮/卡片/输入框
- `references/layout-templates.md` — Dashboard/Landing 布局

## 输出格式（审查时）

```markdown
## UI 审查报告

### 核心问题（3-5 个）
1. [问题] → [改进建议]

### 自动化影响
[哪些失败可能由 UI 引起]

### 建议修复
[具体 CSS/结构改动]
```

## 平台 UI 优化

修改 Testory 前端时遵循：
- CSS Variables 统一 token
- 响应式用 `clamp()`
- 动效仅用 transform/opacity
