---
name: 登录流程
description: 自动化流程：登录流程
format: agentskills.io/v1
source: testory-ai-plan
---

# 登录流程

## 环境前提

（无）

## 用例 URL

https://example.com/login

## 预期结果

（见步骤 assert）

## 自动化步骤（平台 JSON）

```json
[
  {
    "action": "navigate",
    "url": "https://example.com/login"
  }
]
```

## 维护说明

UI 变更时可在 AI Heal / AI Test 对话中说明变更，由 Hermes 更新本 Skill 并同步回用例步骤。
