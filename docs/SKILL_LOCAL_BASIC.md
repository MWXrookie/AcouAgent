# 本机基础技能试用
当前提供一项固定二维平面初压技能；无需配置owner签名。搜索最多3条；每次交付前重新核对参数、代码、来源、物理证据和撤回。外部导入及其他普通运行仍保守作为候选，不自动成为可复用技能。

## 已验证范围
技能32f096b3-4a04-48a4-a5c5-3fe2cb3d4708，版本1，scope managed-local-plane-wave.v1。精确参数由scripts/validation/recipes/val1_plane_wave_v2.py及服务端profile确定，不支持任意参数外推。实际运行最大波形相对误差0.50549%，阈值严格小于1%。
受控网关认证内部executor调用并保存原始数值及摘要；旧executor无nonce回执，因此不宣称独立宿主、运行镜像认证或真实用户采用。内部来源与物理正确分别核验。

## 当前使用
页面/skills可查看已接纳技能。search_simulation_skill只读检索；build_simulation_skill_context和read_simulation_skill_context在服务端完成一次消费及最新重核，参考只在匹配精确参数时交付，失败回原上下文。
Dify草稿已连接两条生成分支，共52节点/56边；尚未发布，所以门户聊天的发布版不能据此宣称已采用参考。自动学习关闭，不产生付费模型调用。

## 运维命令
在已配置的网关容器中运行python -m scripts.skill_library.local_basic status查看当前状态；init只创建缺失的库，拒覆盖或迁移既有库；seed只执行固定配方，已有合格技能时不重跑，撤回后拒自动恢复。不要将init用于现存用户数据库迁移。
部署配置deploy/compose.local-basic.yaml；回滚镜像local/dify-mcp:before-local-basic-20261011保留。回滚只更换网关并关闭技能开关，保留生产SQLite及全部历史；Dify回滚需对照证据draft-before.json进行CAS，不能覆盖他人新改动。禁止重建executor。
生产只读快照备份/app/data/backups/local-basic-20261011.sqlite已创建。

## 验证与限制
证据在auxiliary/reference/local-profile-20261011。真实受限jwave执行、隔离库接纳/检索/参考/撤回、生产认证MCP交付、草稿结构校验已验证；真实模型采用、真人使用、一般场景、派生正向物理及30独立配置召回未验。
