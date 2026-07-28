# 云 Skills 生态对齐说明（R18）

> 可选交付：不引入云厂商运行时依赖；对齐「用云 Skills」叙事。

## 结论

Testory 主路径仍是**本机 / 私有化执行节点**上的 Web·Desktop·Mobile·API。  
阿里云等「用云 Skills」适合作为**非关键**旁路演示：把平台已沉淀的 Skill（`data/skills_promoted/` 或 Hermes skills）导出为云侧可引用的说明包，而不是把质量执行迁到公有云沙箱。

## 推荐做法

1. 在平台跑通跨端 / AgentTeams（诚实门禁）。  
2. Trace 页或 API `POST /api/ai/skills/promote-from-run` 沉淀草稿。  
3. 人工审阅后纳入 `skills/bundled/`，走 `python -m skills.skill_quality`。  
4. 若需向云控制台展示：导出 `SKILL.md` + Trace ZIP 作为附件，注明执行节点在客户内网。

## 非目标

- 不在 CI 容器内直接点 Windows ERP。  
- 不把云厂商密钥写进开源仓库。  
- 不宣称「已对接某某云 Skills 市场」除非商务合同落地。
