当前补充（2026-10-10）：下文为历史空回退契约。当前离线正向提案使用build→不可信编号→服务端read重核，不靠公开search JSON注入；公开search只返回源重核后的元数据且无代码。生成服务端共享2秒截止、仅已有权威DB，网络/当前Dify待验。详见SKILL_FIXED_REFERENCE_CONSUMER.md和reuse-mainline-20261010/workflow-context-review.json。

# Dify 技能检索节点接口方案（待审查，未部署）

本方案不是 Dify DSL 导出，不可直接导入。仓库历史备份 SHA256
ed96107ccc613331933b1c0359937ed435375445162e9fe2c05588d9df0925c1
中参数提取1783991048409直接连接代码生成1783991079665；生产最新图没有读取或验证，部署前必须重新核对节点、变量选择器、工具schema和Dify错误分支支持。旧备份不改写。

## 请求与顺序
1. 参数提取完成后，由经过审查的确定性映射获得受控scenario_type及规范化parameters_json。未知场景或缺参直接回退，不让模型输出trusted/grade/runtime/批准字段控制行为。
2. 从服务端固定配置读取SKILL_RETRIEVAL_ENABLED和SKILL_INJECTION_ENABLED，均默认false，不能由聊天变量覆盖。任一关闭则绕过检索节点，进入守卫；不增加LLM调用。
3. 开启准备态才可调用search_simulation_skill(scenario_type,parameters_json,limit=3)，参数预算4KB。公开工具只读；超时/错误走同一守卫，禁止自动重试或重放整个付费工作流。正式网络检索截止建议2秒，待版本与环境验证，当前未接网络节点。
4. 守卫输入original_context必须取自原模板/知识上下文变量，不能取检索输出中的同名字段。检索JSON预算8KB；纯函数scripts/workflow/skill_generation_guard.py只接受当前空命中结构，字段与布尔类型严格核对。即使工具自报trusted=true或返回代码也拒绝。
5. 守卫输出generation_context逐字符等于原context、skill_injected=false、referenced_skill_ids=[]。代码生成节点只执行一次，沿原executor执行/纠错/分析路径继续；不得在守卫内执行代码或请求模型。现阶段没有任何正向注入路径。
6. 正向消费以后必须由固定服务端机制重核来源/宿主/参数/批准/物理/派生/归档状态并写内部只追加使用审计；不能由Dify调用方传JSON授信。当前尚未实现，不可仅改开关开放复用。

## 字段约束
- 工具返回七个固定字段：skills、fallback、trusted_run、runtime_approved、parameters_bound、run_bound、reason。
- 当前只接受skills=[]、fallback=true、四个可信字段false及固定拒绝原因枚举。
- 错误、超限、重复键、非有限JSON、未知字段、自报代码/可信旗标均回退；返回不带原始错误文本、代码或凭据。
- 两个开关必须是真布尔true才进入解析；"true"/"false"/1不作为启用授权。产品部署时仍只读服务端配置。

## 验收范围
离线测试可验证字节不变、错误回退、伪证据拒绝和每次测试生成替身仅一次调用。它不证明真实Dify节点已运行，不证明网络截止或真实模型计费行为，也不是物理质量/真人UAT。生产修改、部署与批准仍未授权。
