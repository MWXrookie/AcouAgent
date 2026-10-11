# 技能来源与消费边界（2026-10-08）

## 本轮可核查链路
内部固定VAL-1调度读取代码与validator冻结哈希；先在execution_history数据库写controlled_dispatches（run_id、nonce、固定参数JSON及哈希、30秒截止），成功后才派发。HTTP执行器响应须重新核对代码、输出与请求上下文；消费方在同一只读SQLite快照重新读取派发上下文与执行记录。缺失旧行不补写。固定参数上下文匹配仅为dispatch_context_matched，不等于parameters_bound。

controlled_dispatches拒绝更新、删除及REPLACE；写失败阻断受控执行。HTTP/MCP不开放写入接口，generated code无Docker权限。

## 已做真实隔离验证
现成executor镜像中通过loopback启动仓库executor Handler，先以urllib验证基础链路，再使用现成gateway-dev镜像的临时httpx依赖执行真实controlled_validation→execution._execute_code→HTTP→SQLite→独立validator。没有安装依赖、复制开发项目、修改生产服务或生产DB；临时依赖和脚本已清理。两次真实运行原始stdout哈希一致，波形误差约0.5055%低于1%。全部可信字段false。

## 尚未满足的认证条件
1. 当前网关进程的实际目标容器/镜像和本次nonce/run_id关联必须由受信宿主机制证明。当前host-before/after只是操作者收集的隔离容器生命周期证据，不能认证生产网关目标，更不能由调用者传JSON授信。
2. 当前运行批准必须由固定路径、scope及revision核查，并在执行结束和消费时再次验证；历史完成回执不是永久批准。生产批准包/PORTAL_ADMIN_TOKEN未配置，不能自行开启或改凭据。
3. 已冻结代码及完整参数必须绑定本次受信运行，不得将stdout自报配置当真实性证明。当前仅固定平面波，不能推广任意场景。
4. 通用Q3四项自洽和其他Q4场景的受信参考链路仍未实现。自洽不等于模型物理正确。
5. 生产接通涉及部署/运行批准与宿主接入，超出当前无部署授权边界。先交付隔离实现与审查证据，不擅自修改在用服务。

## 技能存储与生命周期的独立准备
schema7包含边界、派生关系、只追加审计、归档位和run绑定负反馈。消费检查只读，不创建缺失DB。新Q2代码版本使已有active转questioned，不能继承旧版本等级；归档/退役不被新候选恢复。归档/恢复内部API采用乐观并发和事务审计，恢复旧active转questioned。最近3个不同来源run中2次显式负反馈触发质疑，重复反馈幂等；派生项在消费时因被质疑来源而拒绝。尚未接门户、HTTP鉴权或检索服务，actor只是内部角色标签，不认证真人。当前success_count沿用候选观察历史，未认证前不得作为可信成功率或Wilson统计依据。

## 下一切片
先实现固定服务路径与鉴权约束下的只读技能视图/内部管理接线，默认关闭入口；来源缺口继续保持false，不把存储就绪当成功技能库完成。生产接通待明确部署/批准授权与宿主机制证据。Archify HTML/视觉验收按用户要求暂缓。

## 本轮追加的默认关闭API准备
- GET /skills/api：只读有界元数据，limit1–50、32KB输出预算、默认隐藏归档；不返回代码或原始物理日志，数据库缺失不会创建。
- POST /skills/api/{skill_id}：仅archive/restore/negative_feedback；4KB请求预算；管理员令牌必须配置且>=32字符。固定SkillStore路径，不接受调用者数据库路径、actor或激活/学习操作。
- 管理访问尝试记admin_events，拒绝、已授权但非法请求也记账；审计库不可用拒绝写操作。成功生命周期变化及反馈与skill_events同事务，审计失败回滚。
- SKILL_LIBRARY_ENABLED、SKILL_RETRIEVAL_ENABLED、SKILL_INJECTION_ENABLED、SKILL_LEARNING_ENABLED均默认false。后3个是预留开关，尚未接检索/注入/学习，不声称开关true能自动开启这些能力。
- 这仅是后端API，未部署，无/skills页面及反馈按钮。新版本候选会使旧active转questioned，不继承旧等级。success_count仍是历史候选观察计数，不能当可信成功率。
- 最终完整隔离回归368passed，隐藏auxiliary证据目录仍通过；运行身份fixture已移至tests/fixtures，未删除历史证据。正常、异常、边界、失败回滚、多进程写入、并发归档、最近3次反馈窗口均覆盖。

## 只读详情与门户准备（v3.4）
详情版本和审计分页均只读、有大小和SQLite执行预算；历史敏感记录隐藏。门户默认关闭，所有写入仍经管理员鉴权及事务审计。桌面临时DB演练通过不构成部署/物理/真人验收；移动端尚未验证。检索消费前仍须独立认证宿主、参数、批准与运行身份，当前全部false。

## K3 最小认证切片验收矩阵（v3.5；尚未认证）
目标：先证明“执行了哪一个固定请求”，再讨论数值基准，不用 JSON 自报字段授信。范围仅固定平面波，其他场景不能继承。
| 条件 | 当前证据 | 允许结论 | 解除缺口所需机制/反例 |
|---|---|---|---|
| 派发前 run_id/nonce/代码/参数 | 服务端只追加 controlled_dispatches | 输入上下文匹配 | 缺失、替换、重复nonce均拒绝 |
| executor 回执及输出 | 内部响应哈希重核 | 响应相关性 | stdout伪回执、nonce/代码/输出篡改拒绝 |
| 批准执行窗口 | 固定包/注册revision及完成回执 | 注册前置匹配 | 退役、revision变化、跨run消费拒绝；包未配置保持关闭 |
| 真实宿主和目标镜像 | 隔离操作者观察，未认证生产 | 无宿主认证结论 | 受信宿主观察进程从实际目标解析container/image/start identity，绑定同一请求nonce和时间窗口；模型/网关用户不可提供观察结果替代 |
| 宿主观察真实性 | 无已批准的认证传输/信任根 | host_binding_authenticated=false | 由操作者审查并授权固定宿主观察机制及信任根；不得自动创建共享密钥、挂Docker socket给网关/技能或把哈希当认证 |
| 实际参数执行 | 固定源码/上下文/数值输出相符 | dispatch_context_matched | 在已认证运行基础上绑定冻结recipe及完整配置哈希；仅stdout自报配置仍拒绝 |
| 消费时有效性 | 完成回执nonce+可选期望run重核 | 审批前置仍当前 | 技能消费必须显式传服务端期望run，重读当前批准及依赖；不能只凭历史eligible |
| 物理质量 | 平面波隔离误差<1%历史证据 | 指定隔离基准数值通过 | 完整宿主/参数/批准链通过后才讨论该场景等级；VAL-1五类仍各<1%，不推广通用Q3/Q4 |

本轮实现修复完成回执拒REPLACE、mode=ro避免缺失数据库创建竞态、expected_run_id跨运行核验，以及受控路径登记完成前置。以上不提供宿主认证。生产宿主机制部署、凭据/信任根配置及批准操作均超出当前无部署授权；不尝试绕过。下一独立开发可做宿主观察证据的严格离线格式/回放检查，但必须保留authenticated=false，不能仅凭上传JSON开放复用。

## v3.6 离线宿主观察格式
新增host_observation_contract.inspect_observation：仅8KB严格JSON与独立期望上下文相关性，60秒窗口/300秒最大年龄；完全匹配仍authenticated=false。拒绝陈旧和跨请求回放，不声称同请求一次性消费。未接受信采集/传输/信任根，不授信、不部署。

v3.6同轮继续：最小宿主快照离线适配已补，不实际调用Docker采集；保留RFC3339九位纳秒精度，拒绝前后容器/镜像/启动时间/重启数漂移、未运行、额外Env字段。request_observed恒false，不能把生命周期稳定当请求路由认证。完整隔离411passed/15warnings/10subtests/6.18s，证据unit-adapter.log及最终source.sha256；git diff --check通过。当前独立准备受生产认证信任根/实际请求观察机制缺失限制，未自行创建凭据或改变网络。下一唯一动作改为审查固定受控路径的检索消费前置接口设计，列出当前必须拒绝的来源/批准/派生/参数条件，并实现默认关闭、无可信命中时原生成路径回退的独立接线准备；不得用离线自报证据开放复用。

### 2026-10-08 定时轮 v3.7：检索拒绝边界与原生成回退准备
- 切片K5-FAIL-CLOSED-BOUNDARY（准备，不是K5完成），依赖已有存储/边界与K3未认证事实；新增app/skill_retrieval.py、search_simulation_skill MCP注册及测试。目标为最小公开只读合同、无可信命中回退，不部署或开放复用。
- 请求仅scenario_type/parameters_json/limit；limit1–3，参数4KB严格JSON/重复键/非有限值/规范化检查，不接受路径、actor、信任旗标。默认检索false不访问DB；启用也仅读active/Q3Q4/未归档场景前50条边界元数据，匹配不会读取代码，未做召回率验收。
- 固定来源认证消费者未实现，所以即使synthetic active/Q4且边界匹配，skills仍空，reason=provenance_authentication_unavailable；缺库/故障/不适用均原生成回退。无公开注入或学习写入，无可信布尔自报接口。
- generation_context为内部参数提取后、代码生成前适配器；默认注入false返回原context逐字符不变，开启也因来源关闭而原样回退，错误输入不制造额外模型调用。没有连接到外部Dify参数节点/生成节点，不能称生成前产品接线完成。
- 正常/异常/边界：完整隔离429passed/15warnings/10subtests/6.24s；default-off无DB、缺库不创建、active伪标签拒绝、candidate/questioned/retired/Q2/archived过滤、limit/重复键/NaN/超限/非法信任字段与MCP参数面测试通过；git diff --check通过。资源90s/1GB/CPU2/pids128/tmpfs128MB/非root/无网络/只读/降权；零付费、无部署/生产DB/凭据修改。
- 证据auxiliary/reference/skill-retrieval-boundary-2026-10-08/unit-first.log、unit-final.log、source.sha256、summary.json。架构JSON同步，Archify HTML/视觉仍按用户要求待补，项目记忆以仓库降级。
- 计时观察起21:40:07 UTC，收尾按summary实际checkpoint；前累计67.5分钟估算加未知继续保留，不计调度等待，不伪造精确余额，300分钟包不重置。
- 未完成：真实宿主/参数/批准认证阻挡正向复用；来源、批准退役、派生撤回、适用参数与物理门控必须在同消费决策重核，当前未实现正向消费，也未写检索审计（只读合同不允许该接口隐式写）。正式注入需要服务端内部只追加使用审计与Dify节点审查。
- 复盘：公开工具存在、开关存在和空结果回退并不意味着检索质量/正向复用已验收。无认证的预筛只能解释拒绝，不能把预筛候选作为代码注入。
- 下一唯一动作：梳理并实现服务端内部skill_retrievals的有界只追加使用审计及run/派发关联准备，明确检索只读与内部审计写入分工；不接受用户自报命中授信、不自动学习、不操作生产Dify，之后再准备可审查的工作流节点变更。

v3.7同轮继续：schema8为skill_retrievals增加UPDATE/DELETE/REPLACE拒绝触发器；内部record_fallback_usage固定dashboard历史路径重读派发run/nonce后，写参数哈希/场景/拒绝原因及空命中，幂等且事务化。不保存原始查询/参数值/代码，不接受命中列表或授信输入，公开search仍纯只读。无上下文不创建技能DB；关联派发不等于已认证执行。完整隔离430passed/15warnings/10subtests/6.31s，unit-audit.log；schema旧v2迁移fixture同步移除新trigger后回归通过。最终source.sha256/summary已更新。下一唯一动作覆盖上节：准备仓库内可审查的Dify“参数提取→只读search→无可信命中原生成回退”节点接口规格与离线工作流契约测试，不修改生产Dify；先保证默认关闭/错误回退/不重复付费调用，再等待来源认证与部署批准，不擅自开放正向复用。


### 2026-10-08 K5-DIFY-OFFLINE-CONTRACT
补充状态 v3.8｜UTC2026-10-07T22:24:58.470Z真实唤醒，实际观察22:25:20至22:28:42，约3.4分钟墙钟估算，收尾另计未知；前累计72.5分钟估算加未知保留，当前约75.9分钟估算加未知，300分钟不重置，不计调度等待。
K5-DIFY-OFFLINE-CONTRACT完成独立准备：docs/SKILL_DIFY_NODE_CONTRACT.md规定参数提取→只读search(limit<=3)→守卫→原生成；scripts/workflow/skill_generation_guard.py为可重复使用纯函数节点草案，无网络/模型/存储/执行调用。开关必须严格true；默认关闭逐字符保留原context；8KB严格JSON、重复键/NaN/未知字段拒绝，空结果/工具错误回退，任何正向命中或可信自报仍拒绝。没有生产接线，也不是可导入DSL。
历史备份app_0592503e_workflows.json SHA256=ed96107ccc613331933b1c0359937ed435375445162e9fe2c05588d9df0925c1仅用于定位历史提取1783991048409→生成1783991079665边；当前生产图未读取，旧备份未改。离线替身一次生成测试不能证明真实Dify付费调用次数；建议检索2s预算未在Dify执行验收。
完整隔离452passed/15warnings/10subtests/6.30s，auxiliary隐藏，git diff --check通过；原90s/1GB/CPU2/pids128/tmpfs128MB/非root/无网络/只读/降权保持。覆盖开关类型、原文不变、空结果、恶意正向/超限/重复键/非有限值回退及真实默认关闭search合同。无新增物理证据、无部署/生产DB/凭据/付费/安装/提交推送。
证据auxiliary/reference/skill-dify-contract-2026-10-08/unit-final.log/source.sha256/summary.json；仓库进度、设计与架构JSON同步，HTML/视觉继续暂缓。所有trusted_run/runtime_approved/run_bound/parameters_bound仍false。K3认证根/请求宿主观察未解，正向消费、真实Dify接线、自动学习及整体验收仍未完成。
复盘：历史图只能定位提案，守卫单测只能证明纯函数回退。外部接通需当前工作流导出与部署批准；不把草案当生产事实，不继续堆叠格式检查冒充来源认证。
下一唯一动作：审查现有技能质量门控与生命周期消费者，形成内部候选积累的最小事务接线切片；仅记录Q2候选，默认学习关闭且不扩大可信字段，不触碰生产。若无独立可行动代码缺口，整理逐项终验缺口及解除条件，不虚构进展。
本轮恰好一次完整提示词回写，保持ACTIVE/45分钟/原本机target，结果以保存回读为准。


### 2026-10-08 K4-CONTROLLED-CANDIDATE
补充状态 v3.9｜真实heartbeat UTC2026-10-07T23:09:59.246Z，实际观察23:10:13至23:12:23约2.2分钟墙钟估算，收尾另计未知；前累计75.9分钟估算加未知保留，当前约78.1分钟估算加未知，300分钟不重置，调度等待不计。
K4-CONTROLLED-CANDIDATE实现：app/skill_accumulation.py内部固定run入口，双服务端开关严格true才读取；默认false不访问历史或创建技能DB。只接受run_id，重读固定binding验证器，代码/数值/执行回执/隔离运行环境/派发前置均通过才记录Q2候选。参数为固定配方单位明确摘要，完整EXPECTED_CONFIGURATION另存SHA256；不接收调用方代码、等级、参数、证据或路径。
controlled_validation在既有历史、完成登记、验证后接候选路径；候选存储失败返回明确拒绝，不重放executor、不隐瞒原执行结果。SkillStore增加内部可选idempotent_source_run，同事务核对版本/观察事件，重复source_run不重复计数；新run可追加观察，退役/归档不复活。当前success_count仍历史候选观察数，不是可信成功率。
验证完整隔离462passed/15warnings/10subtests/6.35s，auxiliary隐藏，git diff --check通过。正常候选、重复run、不同run、退役保持、真实SQLite审计触发器失败整组回滚、缺失/篡改历史、开关类型及存储失败不重复执行覆盖。首轮1failed/455passed因直接把完整配方送入规范化器导致未知字段拒绝；更正参数摘要而非放宽规范化后456passed，再补生命周期/回滚检查至462。失败日志保留。资源90s/1GB/CPU2/pids128/tmpfs128MB/非root/无网络/只读/降权保持，未新增真实物理运行。
证据auxiliary/reference/skill-accumulation-2026-10-08/unit-first.log/unit-second.log/unit-final.log/source.sha256/summary.json；仓库进度/设计/架构JSON同步，HTML/视觉仍暂缓。未部署/修改生产DB/凭据/付费/安装/提交推送；学习开关未开启，trusted_run/runtime_approved/run_bound/parameters_bound全部false。
限制：仅固定受控配方候选准备，不是通用自动成功学习；来源认证、物理场景通用门控、用户正反馈/撤回、正向消费/当前Dify接线未完成。候选幂等不替代来源认证；历史DB与已安装验证器仍是信任假设。
复盘：候选积累也要在事务内去重并保留生命周期限制；存储故障不得触发额外仿真或模型调用。后续不能持续增加离线格式检查冒充宿主认证。
下一唯一动作：按主合同审计用户正反馈及撤回的缺口，设计并实现有审计、幂等、撤回不授信的最小内部记录切片；保留管理员鉴权边界，不声称真人已认证，不开启active或学习。真实宿主认证与生产接线继续等待独立证据和明确批准。
本轮恰好一次完整提示词回写，保留ACTIVE/45分钟/原本机target，保存回读核验另记仓库。


### 2026-10-08 K4-FEEDBACK-STATEMENT
补充状态 v3.10｜真实heartbeat UTC2026-10-07T23:54:59.962Z；checkpoint23:56:06Z。当前约1.1分钟连续墙钟估算加收尾未知，前78.1分钟估算加未知保留，累计约79.2分钟估算加未知，300分钟不重置，调度等待不计。
K4-FEEDBACK-STATEMENT内部切片完成：SkillStore.record_feedback_statement仅接受已持久化来源run关联，确认/撤回写已有append-only skill_events，无新schema。request_id按skill范围幂等，冲突重试拒绝；没有当前确认时不能撤回。撤回active降questioned，确认/重新确认不授信、不改质量/成功或失败计数；归档与退役不恢复。BEGIN IMMEDIATE内审计和降级同事务，审计失败回滚。
skill_library_service增加管理员令牌鉴权封装；未新增公开HTTP操作或门户按钮，未声称管理员角色认证真人。identity_authenticated/trusted_run恒false，所有既有可信字段仍false，自动学习未开启。
完整隔离466passed/15warnings/10subtests/6.46s，auxiliary隐藏，git diff --check通过。确认/重复/冲突/撤回/重确认、未知run、布尔类型、真实审计触发器故障回滚、管理员拒绝覆盖。原90s/1GB/CPU2/pids128/tmpfs128MB/非root/无网络/只读/降权保持；无新失败日志，无真实物理运行。
证据auxiliary/reference/skill-feedback-statement-2026-10-08/unit-final.log/source.sha256/summary.json；进度/设计/架构JSON同步，HTML/视觉暂缓。无部署/生产DB/凭据/付费/安装/提交推送。
限制：内部来源run关联仍不是用户实际使用关联；缺少真人反馈身份/权限、当前Dify图与来源认证根，整体技能库未终验。角色标签不能当真实性证据。
复盘：确认是声明，撤回是生命周期降级信号；必须保存历史、幂等且不能通过重新确认恢复可信状态。
下一唯一动作：在现有默认关闭管理API接入确认/撤回操作，保持访问尝试审计、严格输入与权限校验，并准备门户反馈状态读接口；不开放active/学习，不把管理员测试当真人验收。
本轮恰好一次完整提示词回写，保留ACTIVE/45分钟/本机target，以回读核验为准。


### 2026-10-08 K4-FEEDBACK-API
补充状态 v3.11｜真实heartbeat UTC2026-10-08T00:40:00.716Z，checkpoint00:41:10Z，约1.2分钟连续墙钟估算加收尾未知；前79.2分钟估算加未知继续保留，累计约80.4分钟估算加未知，300分钟不重置，调度等待不计。
K4-FEEDBACK-API完成：现有POST /skills/api/{skill_id}新增confirm_feedback/withdraw_feedback，严格action/run_id/request_id/reason，沿用管理员令牌、访问尝试审计与审计不可用拒写。禁止actor/等级/学习等额外字段；既有4KB请求预算保持，JSON新增严格UTF8/重复键/NaN拒绝及深度错误回退。
新增GET /skills/api/{skill_id}/feedback?run_id=只读当前声明状态，仅available/confirmed/event_id/identity_authenticated=false/trust_verified=false；禁止重复/额外查询字段，缺库不创建，使用既有只读SQLite执行预算，no-store/nosniff。读取不返回理由/令牌/身份。
完整隔离468passed/15warnings/10subtests/6.77s，auxiliary隐藏，git diff --check通过。HTTP未授权/确认/幂等/撤回/伪actor/重复查询/严格JSON/缺库只读覆盖；此前审计失败回滚与生命周期测试继续通过。原90s/1GB/CPU2/pids128/tmpfs128MB/非root/无网络/只读/降权保持，无新失败或真实物理运行。
证据auxiliary/reference/skill-feedback-api-2026-10-08/unit-final.log/source.sha256/summary.json；进度/设计/架构JSON同步，HTML/视觉继续暂缓。未部署/生产DB/凭据/付费/安装/提交推送；所有可信字段false，学习默认关闭。
限制：门户确认/撤回按钮和真实浏览器交互尚未接，角色不认证真人，来源run关联不等于用户实际使用关联；宿主认证根、当前Dify接线、正向复用、整体验收未完成。
复盘：新增状态读取须保留只读与敏感输出边界；声明接口必须和旧管理员审计链共享拒写规则，不能新增绕过权限的独立入口。
下一唯一动作：在已有/skills页面接确认/撤回与当前声明读取，保留令牌清空、异步代次防串技能、显式未认证说明；完成临时SQLite浏览器交互检查后再评估合同剩余缺口，不开放active/学习。
本轮恰好一次完整提示词回写，保持ACTIVE/45分钟/本机target，以保存回读为准。


### 2026-10-08 K6-FEEDBACK-PORTAL
补充状态 v3.12｜真实heartbeat UTC2026-10-08T01:25:01.411Z，checkpoint01:27:19Z，约2.3分钟连续墙钟估算加收尾未知；前80.4分钟估算加未知保留，累计约82.7分钟估算加未知，300分钟不重置，调度等待不计。
K6-FEEDBACK-PORTAL完成：/skills新增确认结果可用/撤回确认/读取声明状态，固定来源run、每次新request_id；令牌请求后及切换技能时清空。管理按钮在请求中禁用；声明异步回读核对技能代次与run，输入run变化提示重读；显式声明身份未认证且不证明物理正确。沿用管理员API，不提供激活或学习入口。
真实隔离浏览器路径：现成dev镜像Starlette skill_api→临时SQLite，经无网络容器Unix socket及Windows loopbackSSH；确认后页面显示已确认，撤回后无有效确认，审计两条confirmed true/false。临时DB回读candidate/Q2/success_count1/failure_count0、statements[1,0]，不授信。容器外截止300s，非root/无网络/只读/降权/CPU2/1GB/pids128/tmpfs128MB；测试容器、隧道及单次脚本已清理。仅桌面交互，移动端/全面视觉/真人UAT未验。CUA首次误用getState方法已改getAXState，无项目影响。
完整隔离468passed/15warnings/10subtests/6.66s，auxiliary隐藏，git diff --check通过；原90s隔离约束保持，无新物理运行。
证据auxiliary/reference/skill-feedback-portal-2026-10-08/unit-final.log/browser-db-receipt.json/source.sha256/summary.json；进度/设计/架构JSON同步，Archify HTML/视觉继续暂缓。ui-ux-pro-max局部异步按钮建议已核对：禁用防重复、状态可见。未部署/生产DB/凭据/付费/安装/提交推送，学习默认关闭，所有可信字段false。
限制：当前管理员声明及来源run不证明真人身份或实际技能使用；当前Dify图、宿主认证根/请求路由、通用物理门控和正向复用仍缺，技能库未终验。
复盘：UI成功提示应和数据库只追加事件交叉核对；确认与撤回不应改变质量或成功失败计数。浏览器测试不能代替物理或真人验收。
下一唯一动作：按K1–K6主合同制作基于代码/证据的逐项终验矩阵，分清工程准备、尚缺独立实现、外部认证/批准阻塞；优先修复矩阵暴露的独立代码缺口，不堆叠格式测试或声称整体完成。
本轮恰好一次完整提示词回写，保持ACTIVE/45分钟/本机target，以回读为准。


### 2026-10-08 K3-TEST-ENVELOPE v3.14
补充状态 v3.14｜真实heartbeat UTC2026-10-08T02:55:02.670Z，checkpoint02:56:16Z，约1.2分钟墙钟估算加收尾未知；前83.7分钟估算加未知保留，累计约84.9分钟估算加未知，300分钟不重置，调度等待不计。
纠错：v3.13合并误用indexOf("可变状态")，匹配开头第一句，实际删除固定五章；当时prefix相等校验也匹配同一错误短前缀，不能证明五章保留，撤销该通过声明。本轮从已保存v3.12完整原文恢复2839字符固定章节，用精确换行章节标记提取，实际核对五个标题及完整固定正文再保存；不弱化任何原边界。
K3-TEST-ENVELOPE原型：scripts/validation/host_envelope_prototype.py仅内存测试HMAC封套，16KB严格JSON/8KBpayload、域分离、恒定时间MAC比较、独立期望上下文与根撤回拒绝；篡改/错误测试根/跨nonce拒绝。共享HMAC验证者也能伪造，不满足独立宿主签名，所有authenticated/request_observed/trusted_run/runtime_approved/run_bound/parameters_bound恒false。同请求重复诊断允许，无一次性消费日志。没有生产根、签名服务、实际宿主采集或技能消费接线。
docs/HOST_AUTHENTICATION_PROTOTYPE.md明确真正生产主体须独立观察实际请求路由与最小容器身份、独立重算代码/参数/输出哈希/窗口；私钥只在宿主代理，消费者公钥与撤回批准，实际参数协议核对与原子消费仍未实现。无新依赖/凭据/网络/生产权限，不挂Docker socket给网关或技能。
完整隔离472passed/15warnings/10subtests/6.52s，auxiliary隐藏，git diff --check通过；90s/1GB/CPU2/pids128/tmpfs128MB/非root/无网络/只读/降权保持，无新物理运行。证据auxiliary/reference/host-envelope-prototype-2026-10-08/unit-final.log/source.sha256/summary.json；进度/设计/架构JSON同步，HTML/视觉暂缓。未部署/生产DB/付费/安装/提交推送，学习关闭，K3及整库仍未通过。
复盘：合并提示词必须在实际保存内容中验证五章全文，不能用可能匹配首句的模糊标记。封套完整性不等于签名主体或请求真实性。
下一唯一动作：核对现成dev环境是否已有可用非对称密码库（仅版本/模块探测、不安装），为独立宿主公钥验证与撤回准备临时根离线原型；同时限定实际路由/参数观察仍未实现，不以原型通过开放信任。若库不可用记录明确限制，优先已有独立代码缺口而非重复HMAC格式测试。
本轮恰好一次完整提示词回写，恢复固定五章，保留ACTIVE/45分钟/本机target，以实际保存回读核验为准。
