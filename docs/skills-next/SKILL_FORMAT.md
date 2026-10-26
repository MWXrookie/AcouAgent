# SkillSpec v1：统一技能提交格式（格式契约）

状态：**阶段1已实现内部严格解析与草稿存储，尚未实现导入接口、自动学习接入或新验证器。** 详见 [实施范围](STAGE1_IMPLEMENTATION.md)。JSON Schema覆盖结构声明；当前语义支持范围限定为二维、点源、频域、PML及稳态复压力，其他类别明确拒绝。 JSON 文件是作者声明，不是已验证技能或现有数据库导出格式。此目录不能作为上线开关。

## 文件

- `skill-spec-v1.schema.json`：JSON Schema Draft 2020-12，约束允许字段、类型及部分范围。
- `examples/manual-skill.json`：B01 均匀介质 Helmholtz 的**未验证草稿**。代码主动抛出 `draft_template_not_implemented`，不会生成或内嵌标准答案。
- 本说明：解析、兼容、鉴权与物理验证要求。运行边界不能用 JSON Schema 替代。

## 作者可提交的字段

| 字段 | 含义 | 权限边界 |
|---|---|---|
| schema_version | 固定为 skill-spec.v1 | 格式版本，不是数据库 schema17 |
| name / description | 显示名称、说明 | 不可信文本，显示时转义 |
| scenario_type | 当前7种场景之一 | 声明不证明该配置受支持 |
| parameters | 单位明确的规范参数 | 不能从标题/代码/技能目录补齐缺项 |
| code_template | 非空Python源码 | 先检查再送原隔离executor；绝不在网关exec |
| result_spec | 完整复压力输出合同 | 数值源归一化，不自行声明Pa或科学正确 |
| validation_request.policy | 固定server_required | 不能关闭、挑选或降低服务端检查 |
| validation_request.reference_case_id | 可选参考编号，例如B01 | 只是待核对的来源声明；由服务端注册表解析 |
| author_notes | 可选说明 | 不作为运行证据、认证或执行指令 |

所有对象禁额外字段。**不接收** `quality_level`、`status`、`active`、`trusted_run`、`runtime_approved`、`run_id`、`source_run_id`、`validator_version`、`validator_code`、签名、回执或客户端给出的通过结论。参数里也不能藏这些字段。

禁止用户选择校验脚本、任意本地路径或要自动访问的参考URL。B01必须解析为受服务端管理的参考资产，不能仅凭字符串授权、伪造证明或读取任意文件。若编号不存在、参数不一致、引用撤回或无适配验证器，必须返回不支持/待验证；不能套近似题获得认证。

## 参数与当前实现的关系

既有字段使用 `normalize_parameters` 支持的规范命名；下述计划扩展需normalizer_v2，不能把整份新对象直接传给当前归一化器。**首版不接收同义别名**，避免同一个提交出现Hz/MHz或m/mm冲突：

| SkillSpec规范字段 | 当前部分运行入口使用的字段 |
|---|---|
| sound_speed_m_s | sound_speed |
| density_kg_m3 | density |
| grid_shape | domain_N |
| dx_m | domain_dx |
| frequency_hz | source_frequency |
| t_end_s | t_end |
| source_type | source_kind |

未来适配器必须显式转换到各入口需要的合同，并在运行、质量门和来源摘要之间保持一致；本次没有实现该适配器。**不能把规范对象直接送进要求旧字段的候选路径，并宣称已兼容。**

其余受约束字段包括PML、CFL、源/探头索引、初压/模态和显式边界。允许字段范围比现有归一化器可接受的任意单位后缀字段更窄，属于首版白名单；未列字段必须拒绝并走版本评审，不能偷偷保留。

当前 `SCENARIO_TYPES` 为：
`point_source_2d`、`point_source_3d`、`plane_wave_2d`、`initial_pressure_2d`、
`interface_2d`、`attenuation_helmholtz_2d`、`heterogeneous_2d`。
`initial_pressure_3d`、任意角谱场图等未加入本格式，不能用近似标签绕过支持范围。
模式、维度和源型是否适配场景仍由服务端语义校验确定。例如归一化器接受rigid边界字符串，不证明求解链已经实现声硬边界。

现有 `candidate_intake.prepare` 另有严格ALLOWED字段和源型限制；它不是本格式的现成导入器。尤其模态初压不能因本schema能表达就自动进入普通候选或获得固定配方认证。当前所有接纳/检索质量规则保持原样。

## 必须分开的校验层

| 层 | 检查内容 | 不能推导出的结论 |
|---|---|---|
| 原始解析 | 有界UTF-8、严格JSON、拒重复键/NaN/Infinity及过深结构 | 不证明参数合理 |
| Schema | 白名单、类型、枚举、范围、必填字段 | 不证明代码正确或可安全执行 |
| 语义/存储预检 | 规范化、尺寸/单位、AST、路径/凭据扫描、预算 | 不证明任意代码安全或物理正确 |
| 共享物理预检 | 频率分辨率、CFL、PML/几何、初始条件合同 | 不证明实际代码遵守参数 |
| 沙箱执行 | 服务端生成run_id及独立executor回执、真实输出、超时 | exit_code=0不证明物理正确 |
| 独立验证与接纳 | 精确绑定参数/源码/参考版本/本次运行并计算误差；由服务端定质量和生命周期 | 不外推到别的网格、材料或源型 |

**结构验证与科学验证是两回事。** 手工添加和自动生成最终应进入同一服务端验证/接纳通道，但不同来源的操作者鉴权和审计须独立保留。没有独立参考/适配物理门的结果不得自动升为可检索技能；普通Q2 candidate不因导入格式合格而变成active。

### 解析与预算（需在Schema之外实施）

1. 拟定提交文件原始UTF-8上限64,000字节；读取时限、解压限制（首版不接收压缩包）、深度上限16均由解析层执行，不能先无界读取再验证。传输层更严格限制取优先。
2. 原始JSON拒绝重复键；`object_pairs_hook`检测应早于字典生成。使用`parse_constant`拒NaN、Infinity和-Infinity，还需递归检查有限数值，拒绝`1e999`解析出的Inf；保存时`allow_nan=False`。
3. `code_template.encode("utf-8")`最多20,000字节；Schema的maxLength计算字符数，不能代替字节限制。当前`validate_candidate_content`还要求AST可解析、无生成包装、无本机绝对路径、无凭据模式，并限制代码加元数据至64,000字节；这些检查全部保留。
4. 规范参数及适配后的运行参数各自按实际紧凑JSON序列化后最多4,096 UTF-8字节（明确序列化方式：键排序、无多余空格、ensure_ascii=False、allow_nan=False）。当前物理门与候选路径已有4KB合同；不能仅凭maxProperties通过。
5. 网格、索引、维度、PML必须是实际整数，拒bool及1.0等浮点token。JSON Schema的integer可能接受数学上为整数的浮点值，故仍需运行时类型检查。
6. 再核对网格维数、步长向量长度、探头每项维度、索引位于有效域、2×PML小于每轴网格、模态列表等长、周期匹配、初压频率冲突、零初速度和宽度分辨率。Schema并未表达所有跨字段条件。
7. 科学代码/元数据扫描是已知模式预检，不是可靠的任意代码安全证明。只读根、降权、cap-drop、无外网及资源墙钟仍由原executor提供，不能为导入成功而放宽。
8. 草稿中的说明、数值和代码没有任何服务端授权效力；先以未验证提交保存，再由真实执行生成证据。不能把文档里的“draft”当作客户端可控制的数据库状态。

## 管理鉴权和审计

现有技能管理入口 `POST /skills/api/{skill_id}` 使用请求头 `X-Admin-Token`，
服务端 `authorize` 要求配置的 `PORTAL_ADMIN_TOKEN` 至少32字符并恒时比较；缺失或错误返回403，关闭技能库时返回404，管理审计失败时返回503。
这些事实不能解释成已经存在导入接口。

未来导入/更新/重新验证操作必须沿用等价的管理授权与失败闭锁规则，并记录服务端操作者、原提交摘要、请求标识、规范化摘要、验证结果及生命周期事件。认证凭据仅留传输层，不进入SkillSpec、SQLite技能正文、日志或浏览器持久存储。没有有效管理员配置时禁止管理写入；文件内部的“管理员”声明没有作用。

## B01示例的准确含义

原物理配置为均匀介质：128²、dx=1mm、c=1500m/s、ρ=1000kg/m³、
ω=1.5×10⁶rad/s、点源零起始(32,32)、PML16。格式使用Hz：
`frequency_hz=ω/(2π)≈238732.414637843`。不是1.5MHz。
完整B01参考绑定还需核对无损参数、原源系数1、求解器容差和幅值误差规则；这些不由占位代码完成。

示例仅应通过结构验证；既有参数子集可通过当前归一化，新增字段必须等normalizer_v2适配。代码仍须通过存储预检，并**必须在真实执行/物理验证阶段失败或保持未实现**。它没有run_id、没有质量等级、没有运行成功声明，不应进入可复用检索。开发首个真实技术探针后才可替换占位模板，不能直接复制标准答案数组充当求解结果。

## 源码核对入口

本合同依据现有仓库代码复核：`app/skill_store.py` 的
`SCENARIO_TYPES`、`normalize_parameters`、`validate_candidate_content`；
`app/candidate_intake.py` 的`prepare`白名单/4KB预算；
`app/physics_gate.py` 的参数预算；
`app/skill_api.py` 的`manage_skill`及`app/skill_library_service.py` 的`authorize`。
本轮只新增本目录格式及示例准备文件，没有修改这些模块或声明它们已经支持SkillSpec导入。


## 冻结扩展：B01/B02/B03与物理身份

所有结构示例均要求result_spec：kind=steady_complex_pressure、units=numerical_source_normalization、axis_order=[x,y]、grid_shape=[128,128]、full_complex=true、downsample=1。结果shape必须与parameters.grid_shape一致，由语义层交叉检查；输出完整复数组用于幅值验证，抽样仅在另外的显示层进行。源系数是原数值定义，不等于1Pa。首个准备切片只冻结二维稳态输出；场景枚举保留旧集合不等于所有场景已有验证器，时域等组合必须经独立输出合同评审，不得套稳态合同上线。

频域参数新增三个**计划normalizer_v2扩展**：

- helmholtz_source_coefficient：有限实数源系数；B01/B02/B03均为1，不能额外乘omega。系数为零、位置越界或与源几何冲突须语义拒绝。
- background_attenuation_db_mhz2_cm：显式背景y=2吸收系数，三例均为0。不能由Medium默认值猜测。
- material_regions：最多8个二维区域。geometry为rectangle或half_space；start含端点、stop不含端点，均为零起始整数向量。区域只允许覆盖sound_speed_m_s和/或density_kg_m3；未覆盖属性继承显式背景。首版语义拒绝重叠、空区、越界和维度冲突；half_space须在另一轴覆盖全域并在分割轴接触一侧边界，不支持任意曲面/图像。区域转换为完整材料图后再核对声明及执行。

B01为均匀背景、空区域；B02矩形[50:90,32:100]声速2300m/s、背景1500；B03行64:128密度2000kg/m³、背景1000，点源(32,64)、omega=1e6rad/s。B02/B03另见examples/b02-manual-skill.json与examples/b03-manual-skill.json。占位代码保持主动raise，即使已有外部技术探针成功，也不为这些草稿伪造运行证据。

当前normalize_parameters不接受这些新键。未来先将既有字段交给原归一化规则，再由独立、严格白名单的normalizer_v2校验扩展并生成统一配置；未知字段仍拒绝，不能靠单位后缀或代码解析隐式放行。

新增case_fingerprint_v2必须绑定SkillSpec格式版本、normalizer_v2版本、场景、全部物理参数、背景与完整区域/材料图、源系数/位置/几何、边界/网格/时间或频率，以及result_spec。所有数值有限、顺序语义确定；不允许仅散列背景标量而漏掉地图，也不允许仅用code_sha256替代物理身份。代码摘要另外保存和绑定执行。显示名和说明不授信。旧17个固定技能保留原fingerprint及原版本，不批量重算、不伪造新版本身份；跨版本只能走显式重验与迁移评审。

顶层字段以本schema为唯一格式源：schema_version/name/description/scenario_type/parameters/code_template/validation_request/result_spec，author_notes可选。来源与适用范围说明只放author_notes且不可信；不添加applicability、source_description或validation_class平行字段，实际验证策略由服务端选择。


## 首版受控求解适配器决策（已冻结）

可信验证路径由服务器维护的 Helmholtz 适配器读取完整 SkillSpec，生成规范执行代码；
原始用户/模型代码保留为来源和试运行内容，不凭数字吻合直接授予可信性。
若规范代码与提交代码不同，必须显示差异、记录派生关系并形成新版本，重新实际执行验证，不能静默替换。
没有对应适配器的任意 Python 仍可按权限试运行，但保持草稿/Q2，不进入默认检索注入。

gmres、tol=1e-5、complex64、FourierSeries属于版本化适配器/profile求解政策；
网格、源位置/系数、材料地图、边界、频率属于必须显式声明的物理输入，不能从profile猜测缺项。
首批频域样例显式声明absorbing_pml。修改源码中的tol不能改变服务端政策，
不同算法代码只能试运行；若生成规范代码，适用前述可见差异与新版本规则。

该机制验证的是本项目支持的受控声学技能，而不是证明任意上传程序科学正确。
提交模板的Schema合格不保证其能直接进入可信复用；验证与规范化结果必须在界面明确显示。
