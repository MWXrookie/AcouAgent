# 技能扩展：数据、接口与任务恢复合同

日期：2026-10-23。状态：**阶段1格式/草稿与阶段2内部trial任务/结果已实现，尚未部署**。阶段3内部独立验证/接纳/复用已实现候选，HTTP/UI与自动事件仍待实施，实施边界见 [阶段1说明](STAGE1_IMPLEMENTATION.md)。生产库未迁移，技能认证未改变。

依据：[NEXT_STAGE_PLAN](../NEXT_STAGE_PLAN.md)阶段0→1→2→3，阶段4/5后接阶段6。保持现有FastMCP、SQLite、local编排与executor，不增加Redis、Celery、向量数据库或独立服务。实际实施须同步架构JSON/HTML；本准备文档不把拟建组件画成当前运行事实。

## 1. 兼容与身份边界

- schema17的skills/versions/events、17项精确固定配方、原运行回执、撤回和使用前复核保持原含义；新草稿不伪装成已有run_id的Q2。
- 旧17项继续原`normalize_parameters`与`skill_fingerprint`，不重算、不重写旧身份。新格式已实现`normalizer_v2`和`case_fingerprint_v2`，旧入口仍拒绝新增字段，不能暗中丢字段。
- `case_fingerprint`仍为场景＋规范参数身份；`code_sha256`为实际执行代码字节身份；`content_sha256`为整份版本内容身份。三者不互代：同代码不同介质不是同题，同题不同代码须新版本/冲突提示。
- 同一草稿内容重存返回原版本；代码或结果定义改动必须新版本。不同草稿同题可并存供编辑；接纳时归入既有技能的新版本，不静默覆盖active版本。
- 新受控写入/检索适配器须识别v2规范与带版本域的v2摘要，再接入现有唯一fingerprint身份；不能删掉地图或源字段后调用旧写入函数来规避拒绝。旧行仍按v1验证，新版本元数据记录normalizer/fingerprint版本，禁止无版本混算。
- 格式、边界、证据、接纳与复用授权分开。普通Q2、用户确认、模型评语或stdout中的PASS都不构成active授权。

## 2. 内容与结果合同

SkillSpec v1内容：`schema_version`、`name`、`description`、`scenario_type`、`parameters`、`code_template`、`validation_request`、`result_spec`（必填），以及可选`author_notes`。标准模板、表单、JSON导入及服务端由同一严格模型导出Schema。

以同目录`skill-spec-v1.schema.json`与`SKILL_FORMAT.md`为唯一字段定义；不另造适用域/来源顶层。`validation_request.policy`固定`server_required`，可选reference_case_id只是待核对声明。来源与适用限制说明只放`author_notes`且无信任效力。首版`result_spec`字段为`kind=steady_complex_pressure`、`units=numerical_source_normalization`、`axis_order=["x","y"]`、`grid_shape`、`full_complex=true`、`downsample=1`；其他结果类别需后续版本，不用本文概念名扩张Schema。未知字段、重复JSON键、非有限数、隐式布尔数值、单位不明或冲突均拒绝。首版冻结HTTP请求正文上限64000个UTF-8字节（不是65536）、代码20000字节、parameters序列化4096字节、正文嵌套深度16；入口按字节计而非Schema字符数计，不改变旧路由。

`normalizer_v2`新增受限`material_regions`（rectangle/half_space，零起始半开start/stop，区域声速/密度），`helmholtz_source_coefficient`及允许零的`background_attenuation_db_mhz2_cm`。B02/B03的材料图和源RHS=1不能交给旧白名单；新指纹的规范身份必须完整包含schema版本、normalizer版本、场景、全部材料区域/空间位置、背景材料、源系数/源位置及result_spec。不重叠区域按规范JSON排序，使等价地图身份一致；拒绝越界、歧义重叠/覆盖，不猜默认物理值。同基础参数而材料图、源或结果合同不同，必须得到不同v2身份，不能折叠为同一技能。服务端适配器才能显式转换到旧执行入口；未验证新版本前不让v2落入旧指纹唯一索引碰撞。

用户来源描述只是线索；用户不能提供可信质量、激活状态、执行nonce、受信run_id、回执、验证器代码或可执行参考脚本。`source_run_id`由自动入口从服务器历史读取；手动入口由实际验证执行生成。管理员导入的来源文件也必须完成服务端来源审核，不能因上传者是管理员就自动变成独立真值。

完整计算结果、显示抽样和标准答案分离：完整数组用于校验，抽样仅用于图示。参考答案不进入模型提示词、被测代码或executor可读路径。既有stdout大小、30秒墙钟及2CPU/4GiB边界不变；阶段0须证明完整产物能在现边界中传出，不能以“后台任务”为由绕过限制。

## 3. 追加schema18建议DDL（不执行）

以下表拟放在现有`skill_store.db`，不创建第二技能库。DDL是实施依据，不是已部署结构。外部JSON由严格模型先验后写；SQL CHECK提供基础保护，不能代替服务端有限值、预算和语义验证。连接须开启外键。所有时间为服务器UTC毫秒，ID为服务器生成UUID；摘要为小写SHA-256。

```sql
-- 原schema17迁移链完整且无未来版本时，显式备份后执行。
CREATE TABLE skill_drafts (
 draft_id TEXT PRIMARY KEY,
 origin TEXT NOT NULL CHECK(origin IN ('manual','automatic')),
 actor_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('editing','submitted','archived')),
 current_version INTEGER NOT NULL CHECK(current_version>0),
 revision INTEGER NOT NULL CHECK(revision>0),
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL
);
CREATE TABLE skill_draft_versions (
 draft_id TEXT NOT NULL REFERENCES skill_drafts(draft_id),
 version INTEGER NOT NULL CHECK(version>0),
 spec_version TEXT NOT NULL, spec_json TEXT NOT NULL CHECK(json_valid(spec_json)),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 normalizer_version TEXT NOT NULL,
 fingerprint_version TEXT NOT NULL,
 case_fingerprint TEXT NOT NULL CHECK(length(case_fingerprint)=64),
 code_sha256 TEXT NOT NULL CHECK(length(code_sha256)=64),
 source_run_id TEXT, created_ms INTEGER NOT NULL,
 PRIMARY KEY(draft_id,version), UNIQUE(draft_id,content_sha256)
);
CREATE INDEX idx_drafts_updated ON skill_drafts(updated_ms,draft_id);
CREATE INDEX idx_draft_case ON skill_draft_versions(case_fingerprint);

CREATE TABLE skill_validation_profiles (
 profile_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
 validator_id TEXT NOT NULL, validator_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('enabled','retired')),
 created_ms INTEGER NOT NULL,
 PRIMARY KEY(profile_id,version), UNIQUE(profile_id,policy_sha256)
);
CREATE TABLE skill_validation_assets (
 asset_sha256 TEXT PRIMARY KEY CHECK(length(asset_sha256)=64),
 storage_key TEXT NOT NULL UNIQUE,
 byte_count INTEGER NOT NULL CHECK(byte_count>=0),
 media_type TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 role TEXT NOT NULL CHECK(role IN ('reference','result','evidence')),
 provenance TEXT NOT NULL CHECK(provenance IN ('unreviewed','server_reviewed','execution_bound')),
 created_ms INTEGER NOT NULL
);

CREATE TABLE skill_validation_jobs (
 job_id TEXT PRIMARY KEY,
 draft_id TEXT NOT NULL, draft_version INTEGER NOT NULL,
 profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
 intent TEXT NOT NULL CHECK(intent IN ('validate','trial')),
 idempotency_scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','needs_reconciliation','blocked_credentials','cancel_requested',
  'cancelled','failed','rejected','validated','admitted')),
 revision INTEGER NOT NULL CHECK(revision>0),
 attempt_no INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until_ms INTEGER, fence INTEGER NOT NULL DEFAULT 0,
 cancel_ms INTEGER, reason_code TEXT,
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
 FOREIGN KEY(draft_id,draft_version) REFERENCES skill_draft_versions(draft_id,version),
 FOREIGN KEY(profile_id,profile_version) REFERENCES skill_validation_profiles(profile_id,version),
 UNIQUE(idempotency_scope,idempotency_key)
);
CREATE INDEX idx_jobs_claim ON skill_validation_jobs(state,created_ms,job_id);
CREATE INDEX idx_jobs_lease ON skill_validation_jobs(state,lease_until_ms);
CREATE TABLE skill_validation_attempts (
 job_id TEXT NOT NULL REFERENCES skill_validation_jobs(job_id),
 attempt_no INTEGER NOT NULL, fence INTEGER NOT NULL,
 run_id TEXT NOT NULL UNIQUE, request_nonce TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
 runtime_sha256 TEXT NOT NULL,
 dispatch_state TEXT NOT NULL CHECK(dispatch_state IN
 ('prepared','dispatching','receipt_bound','unknown','terminal')),
 receipt_sha256 TEXT, output_manifest_sha256 TEXT,
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
 PRIMARY KEY(job_id,attempt_no)
);
CREATE TABLE skill_validation_evidence (
 evidence_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
 binding_sha256 TEXT NOT NULL UNIQUE,
 manifest_sha256 TEXT NOT NULL REFERENCES skill_validation_assets(asset_sha256),
 verdict TEXT NOT NULL CHECK(verdict IN ('passed','failed','unsupported')),
 created_ms INTEGER NOT NULL,
 FOREIGN KEY(job_id,attempt_no) REFERENCES skill_validation_attempts(job_id,attempt_no),
 UNIQUE(job_id,attempt_no)
);
CREATE TABLE skill_validation_admissions (
 job_id TEXT PRIMARY KEY REFERENCES skill_validation_jobs(job_id),
 evidence_id TEXT NOT NULL UNIQUE REFERENCES skill_validation_evidence(evidence_id),
 skill_id TEXT NOT NULL, skill_version INTEGER NOT NULL,
 admission_binding_sha256 TEXT NOT NULL UNIQUE,
 created_ms INTEGER NOT NULL,
 FOREIGN KEY(skill_id,skill_version) REFERENCES skill_versions(skill_id,version)
);
CREATE TABLE skill_validation_outbox (
 event_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL REFERENCES skill_validation_jobs(job_id),
 event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 state TEXT NOT NULL CHECK(state IN ('pending','processing','done','blocked')),
 revision INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until_ms INTEGER, next_ms INTEGER NOT NULL,
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL
);
CREATE INDEX idx_outbox_pending ON skill_validation_outbox(state,next_ms,event_id);
-- 实施时全部建表/索引验证成功后，在同一迁移事务最后登记18。
-- INSERT INTO schema_migrations(version,applied_at) VALUES(18,<server UTC>);
```

profile的policy快照包括输入适用域、独立参考资产摘要、结果量/坐标/归一化、验证器版本、不可变容差及允许运行时；profile行不存或执行用户Python。参考资产由policy摘要绑定；SQLite只存受控相对storage_key，拒绝绝对路径、路径穿越、任意URL抓取。JSON中所有资产摘要引用在写事务内检查存在且role/provenance正确。不同role的相同字节共享原文件时，可由元数据描述用途，不能因结果字节撞中参考摘要就取得参考权威。

不支持独立验证类别的草稿仍可显式试运行：服务端选择不可授级的trial_only政策快照，使用相同任务/执行/资产预算；它没有参考授权且禁止进入admission。客户端不能指定或伪装该政策来绕过validate请求的必需检查。

资产采用内容寻址、先写临时文件→校验摘要→同卷原子重命名→登记索引；文件系统与SQLite不是同一事务，崩溃后按索引/文件双向对账。未引用暂存可延迟清理，已绑定证据、任务或profile的资产不得删除。冻结单资产4MiB、本部署本功能资产总量256MiB（含暂存/参考/结果/证据，不按压缩后体积逃避解析后预算）、未引用孤儿宽限24小时；超限拒绝新写入而不删除已绑定证据。受限解析须同时检查展开大小，磁盘满不得登记半成品成功。

## 4. CAS、领取与恢复

草稿更新与所有状态变更必须携带expected_revision；SQL以`WHERE id=? AND revision=?`更新并递增revision，影响0行返回409。领取任务使用短`BEGIN IMMEDIATE`，仅领取queued且未取消任务，写owner/lease并递增fence/attempt；每次心跳和完成写入同时核对owner、fence、state、revision。首版部署验证并发=1、最多排队100、lease=120秒、heartbeat=5秒、单job总预算180秒、每次executor仍≤30秒；未知dispatch绝不自动重跑，自动付费模型重试为0。不在数据库写事务中做模型/求解/文件解析或网络调用。写锁竞争最多3次等待50/150/450ms，仅在事务body尚未开始时重试BEGIN；body开始后不重放事务。

lease只证明worker当前所有权，不证明executor已停止。running租约到期进入needs_reconciliation，不能直接回queued。外部调用前先持久化attempt与run_id/nonce/request摘要；进入dispatching后网络断开视为unknown，先读取唯一历史记录和匹配回执。找到可信终态则补记；没有证据或摘要冲突保持待核对，不虚构失败/成功、不盲重放。确定未派发的prepared可恢复；重跑须新attempt/run/nonce且保存上次终态，不复用旧证明。

取消queued可直接cancelled；取消running先cancel_requested，不提前释放执行并发名额。只能终止本任务可验证归属的进程；无法证明终止则对账到终态，最终不再接纳。取消与接纳用同一写事务竞争，先提交者确定结果；已admitted不能倒改为cancelled，只能走原撤回/隔离动作。

任务重启、凭据过期、验证完成接纳前崩溃、磁盘满及SQLITE_BUSY都有稳定reason_code和有界重试。BYOK只保存现有服务端profile引用，不在任务、outbox或草稿存Key；临时profile重启失效时blocked_credentials，重新提供凭据后显式恢复，不改用其他模型或预算。

## 5. 跨库run绑定与接纳

execution_history.db与skill_store.db的写入不天然原子；error_cache.db不参与科学授权。采用以下可恢复步骤，不跨库长事务：

1. 在任务库提交attempt预派发意图，绑定内容/参数/实际代码/runtime/profile/参考/validator摘要。
2. 调用现有受控executor；沿原执行路径持久化run历史与回执。只有服务器nonce、代码/输出摘要匹配且唯一run记录才可关联。
3. outbox/reconciler读取该历史，校验完整结果资产和独立参考，追加不可变验证manifest；stdout里的shape、PASS、误差只是非可信声明，比较器必须重新计算。
4. 同一skill_store事务内重核未取消、未撤回、profile仍有效、版本未变、证据未消费，写既有技能版本/事件与admission映射、置admitted。唯一job/evidence约束防止重复激活。
5. “执行已成功而技能登记失败”只补接纳，不重新求解；反方向缺少执行证据则不得接纳。outbox完成回写失败可重试同一dedupe_key。

验证binding manifest至少含job/attempt/run/nonce、source content、case fingerprint、实际代码、参数、完整输出、运行时、profile政策、参考与validator摘要，以及比较结果/单位/轴/阈值。所有摘要由服务端实际字节计算，不信调用者上传的摘要。即使用户代码打印出正确答案，也必须检查受控执行、独立输出、参考隔离及类别的反作弊/适用条件；单题数字相等不支持任意参数泛化。

新通用接纳路径由服务端profile适配到既有检索→生成决定→参考交付→重执行核验，每次使用前重查生命周期与绑定；不能只更新数据库status/quality跳过权限。17项旧scope仍走原pins；新路径不能恢复已归档/撤回技能。质量映射以SKILL_LIBRARY_ACCEPTANCE冻结口径：精确冻结参考满足受控输入、server-selected reference、真实run绑定与独立求解证明的完整链后，才可接纳为Q4；单纯数值误差E通过不授级，首版本阶段不启用其他Q3路径。API从不允许用户选择质量等级。B01/B02/B03的PDE数值探针通过不等于实际接纳/复用链已实现。

## 6. 拟新增HTTP接口

所有路径均为拟新增`/skills/api/v1/...`，不改变已有`/skills/search`及`/skills/api/{skill_id}`的字段、权限与动作。新路由在旧动态路由前注册或确保静态匹配不冲突，实施必须有路由回归。

首版部署仍为管理员控制的单站点，不暗示已有多用户登录。全部草稿/任务写入及敏感读取沿用服务端`X-Admin-Token`校验，actor_id由服务端赋值；自动学习入口只由内部受控流程调用，不暴露绕过鉴权的自动创建HTTP接口。公开技能只读接口继续现有脱敏规则。令牌不持久化到浏览器。

| 方法与路径 | 请求重点 | 响应/语义 |
|---|---|---|
| GET `/skills/api/v1/spec-schema` | 无 | 200：spec_version、schema、模板；可公开，不含代码/资产权限 |
| POST `/skills/api/v1/drafts` | `{spec,origin:"manual"}`；Idempotency-Key | 201：draft_id/version/revision/content_sha256；重复同请求200 |
| GET `/skills/api/v1/drafts/{id}` | 管理员权限 | 200：内容和revision，不含内部可信回执/Key |
| PUT `/skills/api/v1/drafts/{id}` | `{expected_revision,spec}` | 200新版本；内容未变返回原版本；冲突409 |
| POST `/skills/api/v1/drafts/{id}/validations` | `{expected_revision,draft_version}（服务端从不可变SkillSpec的validation_request选择profile）`；Idempotency-Key | 202：job_id/state/revision/status_url；立即固定全部版本 |
| POST `/skills/api/v1/drafts/{id}/trials` | 同上，intent固定trial | 202；原沙箱试运行，永不自动接纳，不能继承验证证据 |
| GET `/skills/api/v1/jobs/{job_id}` | 管理员权限 | 200：state/revision/step/reason、脱敏run引用和结果摘要；结果不是成功即可信 |
| POST `/skills/api/v1/jobs/{job_id}/cancel` | `{expected_revision}` | 202取消请求或200已取消；已接纳409 |
| POST `/skills/api/v1/jobs/{job_id}/resume` | `{expected_revision,profile_ref?}` | 202；仅blocked_credentials/确定可恢复状态；unknown先对账，不能强制跳过 |
| GET `/skills/api/v1/jobs/{job_id}/result` | 管理员权限 | 200展示结果/校验摘要及受控下载引用；未有终态409 |

幂等作用域包含部署内actor＋操作＋资源；同key同request摘要返回原资源，同key不同请求409。自动入口幂等键取实际source_run＋内容＋profile版本，模型改写后的内容不能继承原run证据；改写必须重新求解。

错误结构统一`{error:{code,message,fields?,retryable},request_id}`；message不回显Key、代码、原始模型响应、SQL或本机路径。400=畸形JSON/重复键，403=管理员权限不足，404=未发现或不可见资源，409=CAS/幂等/生命周期冲突，413=字节超限，422=严格字段/单位/适用域/不支持验证类别，429=任务或模型预算不足，503=存储/队列暂不可用。字段错误给JSON Pointer，如`/parameters/domain_dx`。现有接口保持旧错误结构，不做不兼容替换。

## 7. 迁移与出口测试

显式在线备份三库并检查schema17完整链和quick_check；18只追加表/索引，既有技能/运行不重写。新功能默认关闭，读旧库可继续旧路径；部署schema18后用旧代码可能被其未来版本检查拒绝，回退必须按验证备份恢复，不能承诺盲目降版本可用。回滚窗口内新执行记录需按清晰恢复策略保留，禁止直接删版本行伪装17。

出口覆盖：旧17项库存/实际复用不变；严格格式负例；同题不同代码；重复提交/并发CAS；崩溃点逐一注入；租约过期旧worker写回被fence拒绝；未知派发不重放；跨库部分失败只补记；取消竞态；凭据失效；被撤回profile/参考/技能立即不交付；参考隔离；完整数组比较与抽样图一致。

### 已冻结预算与部署阶段验证

B01/B02/B03已提供预算内的数值探针证据，首版128²完整复数结果stdout实测不足515KB；这支持完整结果在原约1MiB输出边界内传出，不授权提高该边界。上述请求、参数、资产、并发、排队、租约、心跳、任务时长、重试和孤儿预算是保守设计上限，不是吞吐或任意输入耗时保证。

部署阶段验证这些冻结值的真实执行、跨库恢复、来源隔离和受控接纳，不再把预算列成未决产品选择。数值PDE比较通过与渲染通过只证明相应探针，不证明持久任务、导入API、Q4接纳或实际复用已实现。所有新增结构和接口仍待按阶段实施，不改当前默认学习策略。


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

## 实施开关（新增，默认关闭）

计划新增 SKILL_AUTHORING_ENABLED、SKILL_VALIDATION_ENABLED、SKILL_GENERAL_ADMISSION_ENABLED，分别控制编辑入口、验证任务和新类别接纳；实施时校验依赖关系。
它们不复用现有 SKILL_MANUAL_INTAKE_ENABLED（固定配方接纳）的语义，也不替换既有学习/检索开关。
自动来源还必须满足原 SKILL_LEARNING_ENABLED；关闭学习不撤销既有已验证技能，关闭新接纳不影响旧17项路径。
AUTHORING与VALIDATION已加入配置且默认false；GENERAL_ADMISSION已实现且默认false。生产环境未变。
## 阶段1实施补充

实际schema18以 `app/skill_authoring_schema.py` 为准：在上述DDL基础上增加 `skill_draft_requests`（按actor/operation/idempotency_key唯一，绑定初始版本）、作者分页索引和版本/请求防更新、防删除、防替换触发器。完整性检查核对表、索引和触发器的真实SQL定义，只有版本号或同名空触发器不算已迁移。

当前版本相同内容更新为无操作；提交历史旧内容返回 `historical_content_conflict`，不回退head。幂等创建重放返回原始版本内容与真实最新head状态。HTTP错误码、验证worker和新接纳仍待后续阶段。

## 阶段2实施补充

内部trial任务与资产实现见 [阶段2说明](STAGE2_IMPLEMENTATION.md)。成功trial使用rejected/trial_completed_ungraded，既无validated也无admitted；这与未来validate的科学失败不同。当前不需要模型凭据，不实现BYOK恢复或接纳outbox。任何任务重建、跨库补记必须先核对真实执行终态，未知派发继续占slot；不借迁移或试运行暗中实现授级。

## 阶段3实施补充

受控派生、独立比较、原目录Q4接纳、generation decision/consumption及新运行重验已贯通，见 [阶段3说明](STAGE3_IMPLEMENTATION.md)。沿用18表，执行manifest与比较proof分开，evidence绑定proof资产；版本保留完整spec和授权job/proof指针。跨库断点由reconcile核对，admission在同skill库事务；未新增后台outbox调度或HTTP接口。数值解析在写锁外，事务内重哈希实际文件/历史，预检缺失拒绝。

## 阶段4实施补充

沿用18的trial job/attempt与append-only draft_requests保存受控source；源draft、trial job、controlled-source-job标记同事务。自动学习draft另绑定原run，outbox记录推进/稳定失败，不能替代lease或独立证明。FastMCP lifespan中的后台pump按5秒有界推进；默认四开关关闭、启动无迁移。自动origin强制提交源守卫，完整链路见STAGE4_IMPLEMENTATION。

## 阶段5实施补充

`/skills/authoring` 已实现管理员标准格式工作台，新增v1路由在旧动态路由之前注册。JSON schema、三模板可公开读取，敏感读取/写入均使用临时X-Admin-Token、服务端actor `portal-admin`；单站点管理员语义不变。

在第6节拟定接口基础上，实际新增 GET `/drafts`（20条、after_id分页）、POST `/drafts/{id}/archive`、POST `/drafts/{id}/controlled`（expected_revision/apply，预览真实diff或显式新版本）、GET `/drafts/{id}/jobs`（最近20条）、POST `/jobs/{id}/admit`（expected_revision，独立验证后显式接纳）。路径均带 `/skills/api/v1` 前缀。

`resume` 和 `reconcile` 只接受needs_reconciliation，核对原历史且不重新派发；当前不需模型凭据的受控执行没有虚构blocked_credentials恢复。GET `/jobs/{id}/result` 返回受保护结果/热图/比较摘要；GET `/jobs/{id}/field` 重核同源绑定后下载完整复数NPY，allow_pickle=false，固定附件名，不接受任意资产路径。

当前HTTP错误结构保留稳定code、JSON Pointer、retryable与request_id；不回显输入代码、SQL、Key或文件路径。详情见STAGE5_IMPLEMENTATION。功能仍默认关闭，所有profile/运行时/扩展schema须先由已有操作CLI显式准备。
