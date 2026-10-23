# 技能扩展准备完成报告

日期：2026-10-23。基线：662391c（0.1.0）。准备分支：prep/skill-learning-contracts。
结论：**可以进入阶段1实现；准备工作已形成可执行合同并完成基础探针。**
这不表示新增导入、任务系统、自动接纳或新技能复用已经实现。

## 1. 冻结的实施依据

| 内容 | 文件 | 准备状态 |
| --- | --- | --- |
| 阶段与依赖 | [下一阶段计划](../NEXT_STAGE_PLAN.md) | 已确定，阶段0至6 |
| 统一格式与样例 | [SKILL_FORMAT](SKILL_FORMAT.md)、[Schema](skill-spec-v1.schema.json)、[样例](examples/manual-skill.json) | 3份结构样例及27项负例通过 |
| 数据/接口/恢复 | [DATA_API_CONTRACT](DATA_API_CONTRACT.md) | 字段、schema18追加DDL、鉴权、错误码、CAS/租约、跨库恢复及预算已冻结 |
| 首批物理类别 | [VALIDATION_PROFILES](VALIDATION_PROFILES.md) | B01/B02/B03真实求解及独立参考比较通过 |
| 产品验收 | [ACCEPTANCE_MATRIX](ACCEPTANCE_MATRIX.md) | 44个唯一用例已列出；新功能用例待实现后执行 |
| 准备探针工具 | scripts/validation/skill_learning_probe.py | 17项单元检查通过；3份真实响应的回执、版本及数值复核通过 |

## 2. 原沙箱真实求解

jwave 0.2.1、jax/jaxlib 0.4.38、jaxdf 0.2.8、numpy 1.26.4，Python 3.11.17，x86_64。
未调用付费模型，未向生产三库登记技能或运行记录；本次直接经原内部executor执行，记录的是响应/nonce，不伪造产品run_id。

| 算例 | 实际幅值误差 | 原严格上限 | 墙钟 | stdout字节 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| B01 均匀介质 | 0.0338671671% | <1% | 5.9758秒 | 490181 | 通过 |
| B02 高声速矩形 | 0.0323398272% | <1% | 5.2421秒 | 514364 | 通过 |
| B03 密度界面 | 1.9389519468% | <3% | 5.1895秒 | 510716 | 通过 |

比较完整128×128复场的幅值；分子去掉四周16格PML，分母仍是全域参考最大模。
原始参考NPY摘要已重核。没有插值、移动、归一化缩放或把参考数组传给求解端。
B03保留一条jaxdf既有弃用警告，没有改写为无警告；退出码0、未超时。

实际执行代码、stdout/stderr、nonce与executor-receipt.v1逐项匹配，运行时摘要通过当前核验函数。
这属于受控内部传输相关性，不是独立宿主认证，也不授予Q4。
源数值系数1不是经标定的1Pa边界；现产品通用图标签仍可能写Pressure(Pa)，正式结果类型/单位展示在阶段2修正，本报告不借该标签授予物理单位。

原分析模块对三份实际响应均能读取128²幅值矩阵；矩阵逐点等于完整复场取模，峰值/空间RMS和实际PNG已生成。
这不是自然语言链或浏览器全流程通过；完整复相位保留供后续使用，原题评分本身只比较幅值。

## 3. 边界和恢复准备验证

- Docker实查：2CPU、4GiB、readonly、10001:10001、cap-drop ALL、no-new-privileges、仅内部网络均保持。
- 受限探针：参考路径不可读、/app不可写、对外TCP无法连接；未扩大网络或资源权限。
- 结果传输：压缩NPY保留完整复场，解码有解压大小限制，原1MiB stdout限制内可传输；图像仅用于展示。
- 17项准备工具单测涵盖PML分子/全域分母、严格阈值、相位不属于幅值评分、NaN/错形状、重复/过大输出及nonce/代码/输出篡改。
- JSON Schema Draft2020-12合法，3份样例通过结构与当前代码存储预检，27项伪等级/错结果/未知字段等变体拒绝。
- 新参数有明确normalizer_v2及case_fingerprint_v2合同；实际旧normalizer拒绝新字段，不能把格式样例当现有接口直接可用。
- 在一次性临时库的完整schema17上执行14条追加DDL、事务回滚及提交，quick_check/foreign_key_check均通过，旧表定义逐项不变。测试库已关闭销毁，没有生产迁移。

## 4. 已解决的开工前决策

1. 手动与自动共用SkillSpec和接纳路径；物理输入完整声明，不从profile猜网格、地图、源或边界。
2. 首批可信代码由服务器维护的Helmholtz适配器生成；原始任意Python仅留来源/试运行。不同代码必须可见差异、新版本和真实重验。
3. solver政策固定gmres/tol=1e-5/complex64/FourierSeries；独立参考、完整来源绑定和受控求解均通过后才按既有标准授Q4；不新增宽松Q3路径。
4. 草稿不占用有run证据的Q2；新表追加，不重算旧17指纹；新物理地图/源字段完整入v2身份。
5. 最大请求64000字节、参数4096字节、代码20000字节；资产单个4MiB/总256MiB；任务并发1、排队100、lease120秒/heartbeat5秒/总任务180秒。
6. 单次executor≤30秒；未知派发先对账；无自动付费模型重试；写锁开始前最多3次有界重试，不能重放已执行事务体或求解。
7. 新API和学习扩展默认关闭；实现时同步当前架构事实、迁移备份与恢复验证，不把准备文件接到生产。

## 5. 开始开发后的第一批交付

- 先实现SkillSpec解析/语义及normalizer_v2、指纹v2，验证完整地图/源身份与旧17隔离。
- 再实现草稿存储和schema18迁移，不先开放自动激活或可信导入按钮。
- 按阶段2/3接通任务、受控适配器、独立验证、接纳与复用后，才接自动学习与手动添加界面。
- 所有尚未存在的新接口、hardcoded-answer拒绝、任务崩溃恢复、撤回与复用测试保持待实施，不能用准备探针的PASS替代。

## 6. 重复执行方式与留证

从仓库根目录用 `python -m unittest tests.unit.test_skill_learning_probe -v` 重跑准备工具检查。
用 `python -m scripts.validation.skill_learning_probe --case B01 --emit-request request.json` 生成不含参考的30秒请求。
操作者仅通过已鉴权的内部executor派发，不把请求送给模型，不打印令牌。
保存原响应后，用以下命令在执行器外比较（示例路径按本机替换）：

```bash
python -m scripts.validation.skill_learning_probe --case B01 \
  --request request.json --response response.json \
  --reference B01.npy --reference-sha256 <冻结参考SHA256> --output score.json
```

完整私有响应、参考摘要、实际图和契约检查回执保留在操作者证据目录；不提交生成数据、凭据或生产库。
原作者来源和三个参考摘要见VALIDATION_PROFILES。工具不会创建技能、授权接纳、读取生产数据库或自动调用模型。
