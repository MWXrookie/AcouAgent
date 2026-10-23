# 阶段3：独立验证、接纳与实际复用

日期：2026-10-23。内部候选实现，默认关闭、未部署生产。沿用schema18及原sandbox，新增内容进入原simulation_skills/skill_versions目录；原17项指纹、旧归一化与信任门保持。

## 已完成链路

1. **可见派生**：动态Helmholtz适配器从完整物理参数生成规范代码，展示diff；显式应用后保存新草稿版本和源/派生内容、构建摘要，保留原提交。任意Python即使打印完整正确数组，也不能直接取得接纳资格。
2. **独立验证**：固定参考原字节SHA及原误差规则；原executor回执、完整输出、材料地图、源、代码、参数、runtime、政策、参考和验证器联合绑定。未知范围拒绝，不能自报PASS或选择宽松验证器。
3. **原子接纳**：job先validated；接纳事务再核对取消、草稿版本、政策、证明及生命周期。通过原入口写Q2后，在同一真实证明事务内登记admission、版本/事件和Q4。中断重试不重复求解，失败不留半激活。
4. **原路径复用**：完整v2查询经过既有检索、generation decision、一次消费和参考交付，再以新nonce/run实际求解并重新独立比较。幅值图、指标、误差及正确单位进入聊天/看板，完整二进制数据留在受控结果中，不堆进聊天报告。
5. **撤回保护**：检索、消费与派发前后核对当前版本、归档/质疑、负反馈及profile状态。撤回不能因重复接纳恢复，未知派发的同一generation intent不得重跑。

数值解析/比较在写锁外完成；事务内重核实际文件字节、原历史和同一预检摘要。64项有界缓存不提供独立权威，缺失即拒绝并重新预检。重启后从持久完整产物重新比较。

## 认证范围

动态适配器按参数构建，不给每个新技能新增固定代码白名单。独立参考授权当前仍**只限B01/B02/B03三项精确配置**，均不在原17项目录内；材料、源、网格或结果合同变化不能继承认证。其他场景/时域/任意参数泛化尚未支持。

原误差为内部96×96幅值差最大值 / 完整128×128参考峰值，严格小于1%/1%/3%。只认证该幅值对照，不认证相位；源归一化数值不得冒称Pa。输入、来源及参考SHA见 [验证政策](VALIDATION_PROFILES.md)。profile_id在原参考ID后附完整runtime SHA，version=1，运行时升级不覆盖旧政策。

参考数组仅挂入网关/比较存储，不进入executor挂载、被测代码或模型提示词。相同字节的结果角色不能取得参考授权。

## 操作与恢复

先完成阶段1备份及显式18迁移。操作员提供参考目录和已审核runtime，显式开启AUTHORING、VALIDATION、GENERAL_ADMISSION，均默认false。检索交付还需原LIBRARY、RETRIEVAL、INJECTION开关。

```bash
python -m scripts.skill_library.general --help
```

各命令要求database/assets/references/runtime-sha256/case。install-profile核验B01.npy/B02.npy/B03.npy实际字节；preview-draft先查看差异，apply-draft按草稿ID和expected-revision保存新版；enqueue使用新revision和幂等键，run-once一次处理一个job；status/reconcile/admit使用job-id，admit还需job expected-revision。

服务端检索使用SKILL_GENERAL_ASSET_ROOT、SKILL_GENERAL_REFERENCE_ROOT、SKILL_GENERAL_RUNTIME_SHA256指向同一存储。v2查询envelope含normalizer_version=normalizer_v2、完整parameters和result_spec，不能从历史技能猜缺项。编排检索声明必须与simulation_params一致；未命中已验证技能时明确停止，不套旧生成路径。

模型提取需返回完整标准参数及result_spec，新路径才可使用。现有一般自然语言提示词未承诺覆盖所有v2请求；模型解析覆盖率与用户导入界面仍需后续验收，本次精确查询实跑不能替代它们。

执行已留历史但任务中断：recover/reconcile只补验证；validated后接纳中断：重试admit。参考/结果/历史变化拒绝，不得手改等级或删除索引强行恢复。新版本名称/说明变化也产生新版，物理身份保持；旧内容保留。

## 本次真实验收

可重复探针scripts/validation/skill_general_smoke.py要求显式开关、已审核runtime和参考目录，使用全新临时技能/历史/结果目录。三题均完成派生新版→独立验证→原目录接纳→检索交付→新运行复用→归档拒绝；共六次真实求解，无生产三库写入及付费模型。

|题|验证耗时ms|复用耗时ms|幅值误差|严格上限|
|---|---:|---:|---:|---:|
|B01|4011.0|4135.1|0.0338672%|1%|
|B02|5694.7|5766.6|0.0323398%|1%|
|B03|5545.2|5740.0|1.938952%|3%|

原executor维持2CPU/4GiB/30秒、只读、10001、cap-drop ALL、no-new-privileges及隔离网络。测试临时库/数组随容器清除，摘要日志本地保留，不能作为生产接纳来源。合成数组只用于回归故障注入，不充当论文参考。

## 后续

尚无用户添加表单/导入HTTP、自动学习事件接入或生产部署。阶段4完成自动提取/调度闭环，阶段5完成手动入口；本阶段不宣称两者已完成。v1.0发布门以CURRENT_STATUS为准。

看板单位由服务端唯一gateway执行记录判定：general_reference_execute显示数值源归一化，旧唯一网关路径保留Pa；重复、缺失或非网关记录显示单位未知，不从summary/stdout猜测。列表、详情及比较均按各自单位显示，后端无需新增schema。
