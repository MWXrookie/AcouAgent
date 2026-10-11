# 第一阶段技能库接线（2026-10-10）

现有 app/skill_store.py、app/tools.py 的 search_simulation_skill 和 candidate_intake 已复用，不新增平行实现。普通执行必须服务器重读结果：normal、无超时、非零有限场、声明参数与导出网格/实际时间窗、基础物理门通过才写Q2候选；不合格拒入库。候选不是已认证可复用技能，退出0或normal单独不够。

scripts/workflow/skill_stage1_graph.py 统一组合参数声明、参数适配、执行上下文、只读检索和生成守卫。显式节点绑定，未知布局拒绝，不按标题猜测。图检索开关与注入开关独立；本阶段注入恒关闭，检索结果不直接变成可执行代码，失败/空结果保留原参数。服务端主/检索开关仍独立严格True控制，生产四开关未改变。

输入为JSON graph，不是完整YAML DSL；输出也为graph，需使用Dify图更新入口或与导出DSL的workflow.graph合并，不能宣称直接导入整个应用。只创建新输出文件，不覆盖原文件；重复生成从原导出开始，已接线图拒绝。工具模板须来自已配置MCP provider；上线需刷新服务端工具清单/核Dify对应schema和认证传输。

远端重复使用命令：
python3 -m scripts.workflow.skill_stage1_graph --graph auxiliary/reference/skill-stage1-20261010/current-draft-before.json --bindings auxiliary/reference/skill-stage1-20261010/bindings.json --output /tmp/acou-stage1-review.json
需要只读检索图时额外加 --enable-retrieval；不改变服务端配置，不发布。

用户授权自行导出后，仅对指定app当前draft做BEGIN READ ONLY/3秒statement_timeout查询，唯一行导出。导出id641c4530-b8f5-4365-8da7-17208c9e701a，updated_at2026-10-08T12:53:09.381416。副本34节点/36边，默认关闭及只读检索版分别保留。首次/重试参数分别绑定各自适配器；重试原来误引用首次提取器，显式修正。生成system提示词、无关节点及原执行code绑定逐项核对不变；PR6时长/稳态改动未重做。

验证86+160=246项相关检查分两批通过；14个相同库warning每批独立报告。首次命令指定不存在的测试文件，未执行测试，日志保留。不是全库重跑、物理实验或线上Dify验收。uid1000/无网/只读/cap-drop/no-new-privileges/CPU2/1GB-swap1GB/pids128/tmpfs128MB/90秒外截止保持。

交付与限制：代码及当前草稿接线副本可审查，尚未部署网关或回写Dify草稿/发布；不得把副本说成线上已调用。生产数据/凭据/服务/镜像未改。后续需要具体部署/草稿变更授权及认证请求验收。时间仅本轮工具证据与收尾，未统一有效累计；原历史约296.5分钟+未知保留，预算上限已解除。


## 2026-10-10 授权部署与草稿更新

用户明确授权网关部署与 Dify 草稿更新，不发布、不自动学习。仅切换网关镜像到 local/dify-mcp:skill-stage1-20261010，使用 deploy/compose.skills-stage1.yaml 开启主/只读检索开关，注入/学习为 false。executor 容器 ID、镜像和启动时间前后完全一致。回滚镜像 local/dify-mcp:before-skill-stage1-20261010 已保留；回滚仅网关使用旧镜像及原 compose 两层配置，不带 skills override。

Dify 当前草稿 641c4530-b8f5-4365-8da7-17208c9e701a 更新为36节点/38边；首次/重试各自参数→适配→检索→JSON输出归一→生成守卫。实际 Dify MCPTool 将 JSON TextContent 放入 json 数组，归一节点修复原 text selector 不匹配。provider 仅刷新 tools 元数据至9工具，未改认证、超时或连接设置。草稿及 tools 在同一 CAS 事务更新；已发布 graph/updated_at/app.workflow_id 前后逐项一致。

实际 Dify 环境通过现有认证向网关调用 search，耗时0.183秒，空结果 fallback，四可信字段 false。没有调用模型或运行完整工作流。生产 skill_store.db 尚未创建，reason=skill_store_unavailable；当前部署证明接线/安全回退，不证明实际技能入库或正向复用。保留 jwave 沙箱，不修改生产运行历史。

1142核心测试通过（1项重夹具排除，14 warnings、10 subtests）；传输/接线20项通过（15 warnings，含只读 pytest cache 警告），不相加冒充新全库结果。证据 auxiliary/reference/skill-deploy-20261010 包括更新前后草稿/发布快照、网关/容器验证、Dify真实MCP调用、测试及清单。未提交推送、未发布、未开启学习或代码注入。时间本轮工具分段未统一有效累计，不伪造总计；预算已解除。下一动作：按用户决定建立空生产技能库及后续有来源运行接纳；当前认证调用仅检索回退，不代表真实使用或正向物理授信。
