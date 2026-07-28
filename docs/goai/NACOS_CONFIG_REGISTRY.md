# 本地配置注册中心（R19）

> Nacos 叙事的轻量替代：不引入中间件堆料。

## 结论

Testory 默认用 `data/config_registry/` 托管 **AgentTeams Spec** 与 Skill 索引。  
企业若已有 Nacos，可将同一目录内容同步为 Nacos 配置项；**本地离线演示不依赖 Nacos 进程**。

## API

- `GET /api/config-registry/info` — 查看根目录与已发布 Spec 列表  
- `GET /api/config-registry/info?seed=1` — 将内置 Team Spec 写入注册中心  

Trace 运营页（`/trace-hub`）提供一键种子发布。

## 非目标

- 不为复赛 Demo 强制部署 Nacos / Consul / etcd  
- 不把配置中心可用性与用例判绿绑定
