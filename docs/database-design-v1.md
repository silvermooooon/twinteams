# Twinteams 数据库设计 v1

评审稿 · 2026-10-17 · 仅设计，未执行迁移、修改应用或运行测试

## 1. 先看结论

**数据库选型已确认**：来源 Sentinel_668b128c9bd481918ea969b8c14f4763。本地开发使用 SQLite，生产部署使用 PostgreSQL，替代此前仅建议 PostgreSQL、选型待确认的表述。Twinteams 数据库设计的主范围是身份、聊天、历史权限、消息、业务回复队列与产品资料，以及到现有 SDK Session/Run/Event 的映射；复用 SDK 原有七张表的逻辑模型、标识类型与事件日志，不假定原 PostgreSQL DDL 能直接用于 SQLite。

**职责澄清（已确认）**：来源 Sentinel_8bdb5bcb16c08191869a24887683f37b。Agent 运行行为由 Agent Runtime 项目重点实现，Twinteams 不重建运行时。Session/Run/Event、Agent loop、模型/工具调用、subagent 通信、运行持久化、checkpoint、恢复和执行并发控制均归 SDK。本文第 2 节及第 9–13 节涉及的 Runtime 表与机制，是已有能力说明或 SDK 侧集成要求；新增 RT 表名只是供 Runtime 项目评审的建议，不属于 Twinteams 建表清单，更不代表已实现。

**复用 DatabaseSession，不重做运行日志。** SDK 已经把模型最终响应、工具开始/原始返回/最终结果、队列安全边界快照、checkpoint 等语义事实写入 PostgreSQL，并具备历史状态回放、分叉和续跑。Twinteams 将业务请求接受、FIFO 调度、消息投递与权限关联接到 SDK 接口；父子回传、执行恢复等缺口在 Runtime 项目补齐，不由产品数据库另建实现。

**已确认的持久化粒度：语义事件与完整 output，不要求逐 token 精确回放。** 来源：Sentinel_163f756d0bc0819183270b91e8d32935。用户说明当前使用 response API，逐字 token 最终由对应 detail 级别事件的完整 output 汇总；token delta 是正常连接期间用于打字机效果的临时数据，可以丢弃。恢复、重连及未来跨 pod 接管时，从数据库读取已经提交的完整 output 展示，不补回丢失的 token。这里的完整 output 属于每个已完成的模型调用、工具调用或其他产出结果的语义操作/事件，不是只保存整个 Run 结束时的一条总结。

当前方案直接复用既有 journal、Run、checkpoint 及完整模型/工具结果，并补齐产品编排；第 9 节说明正常流式展示与恢复契约。不新增 token/chunk 持久表或 token 回放游标。已被业务接受的输入、语义执行事实和完整结果仍须可靠提交，不能因为允许丢 token 而退回进程内存。

“任意点恢复”需要拆成三个能力：查看某个已提交事件点的历史状态；恢复未完成运行；从历史状态建立新分支。历史回放不执行工具；续跑需要检查副作用和权限；分叉不意味着撤销已经发生的现实动作。对不支持幂等或查询的外部工具，无法承诺 exactly-once 或任意回滚。

本文新增表名、字段、状态编码、索引、事务和部署方式仍是设计提案；但主动 Twin 对话与默认 6 次全链自动接受上限等明确标注确认的产品语义已经确认，不因字段待落定而变为未决。业务表提案归 Twinteams；RT/agent_* 运行机制提案归 SDK，二者不能合并成产品实施清单。已确认的产品行为来自需求文档；SDK 当前事实单独列明。未决行为不会通过数据库默认值悄悄定案。

## 2. 现有事实与边界

### 2.1 DatabaseSession 已有的写入与恢复

DatabaseSession(Session), durable=True → Session.commit → DatabaseSession._persist → PostgresStore.append：写 journal、CAS next_seq 与相关投影同事务，持久写入完成后才更新内存。DatabaseSession.open 读取日志并 replay；asnapshot_at(event_id) 可重建历史状态；Agent.resume 延续原 Run 并进入按阶段恢复的循环。模型最终回复在 message_end 之前持久提交；工具 start、raw return、final result 分别持久化。sync_queues 会写持久队列快照，缺口仅是 enqueue 返回到下一个安全边界之间。

依据：sessions/database.py:8–9,19–24,41–55；sessions/base.py:502–561；storage/postgres.py:57–81；agent_loop.py:350–359,603,627,632；agent.py:250–253,467–470。下文新增机制是围绕既有能力的整合与增强，不替换 DatabaseSession。

### 2.2 原样复用的 Runtime 表

| 现有表 | 已有核心字段 / 约束 | 继续负责 |
|---|---|---|
| agent_sessions | PK (tenant_id text, session_id text)；next_seq bigint；metadata jsonb；user_id text 可空 | 执行会话、事件顺序与归属投影 |
| agent_session_events | PK (tenant_id, session_id, seq bigint)；UQ (tenant_id, session_id, event_id text)；record json；metadata jsonb；session FK | 可重放 journal，保留原格式 |
| agent_session_archives | PK (tenant_id, session_id, start_seq bigint)；end_seq、object_uri、version_id、sha256、format_version | 归档分段与完整性 |
| agent_runs | PK (tenant_id, session_id, run_id text)；user_id、root_run_id、source、started_at、finished_at、status | 现有运行投影；当前没有 session FK |
| agent_audit_events | PK (tenant_id, audit_id text)；actor_id/type、user_id、session_id、run_id、action/resource/outcome | 安全与操作审计；不能当作完整执行日志 |
| agent_model_usage | PK (tenant_id, attempt_id text)；session/run/root、provider/model、usage/status | 模型调用尝试及用量 |
| agent_session_checkpoints | PK (tenant_id, session_id, seq)；UQ event_id；session FK | checkpoint 位置索引；完整状态仍在 journal |

依据：Runtime 的 src/agent_runtime/storage/sql/001_sessions.sql、002_checkpoints.sql、003_session_identity.sql。现有 ID 均为 text，不能迁成 uuid 后假装兼容。agent_runs / agent_model_usage / agent_audit_events 并没有本文新增模型所需的全部外键。

现有 PostgreSQL append 可在一个事务内写 journal、CAS 更新 next_seq，并更新部分运行/用量投影。它不等于已经实现跨业务表事务、主回复租约或工具副作用事务。现有 audit 表也不是自动记录全部工具与输入的通用日志。

### 2.3 当前边界与需集成的部分

- message_update/token delta、tool_execution_update 走内存 Session.live，尚未逐条持久化；最终模型文本与工具结果已经持久化。按本次确认，逐 token/纯展示进度的精确回放不在当前需求范围；涉及执行状态变化的语义事实仍必须进入 journal。
- enqueue / steer / follow_up 先改内存；之后循环到安全边界才同步记录，不是“收到即可靠接受”。
- 缺少持久输入/父子回传 inbox、outbox、租约 fencing，以及跨进程恢复调度闭环。
- checkpoint 表只存位置；完整 checkpoint v2 在 compaction journal 内。不是每个 token 都有独立可执行快照。
- 未确认结果且非安全工具恢复会要求 ToolRecoveryRequired；这应进入人工核对流程，不能一律重试。
- 内嵌 run_tool_call 可能使用无 session 配置，需补齐嵌套工具可观测性；只包裹最外层工具不足以满足全部事件要求。
- 子任务 factory 与数据库 session 异步创建需要适配；已有 spawn/send/wait 原语不表示数据库父子执行链已完整落地。

依据详见第 19 节；这些是现有实现检查结果，不是已修复说明。属于运行机制的差距交由 Agent Runtime 解决；Twinteams 只核对接口是否满足产品需要，不以这些差距为理由复制一套运行时。

### 2.4 Schema 与所有权

本地使用 SQLite，生产使用 PostgreSQL；两种后端保持一致的业务含义、身份关联、语义结果保存和权限边界。SDK 的 SQLite 适配如有缺口由 Runtime 项目解决，不以产品另写运行日志绕过。SQLite 开发运行不承诺生产跨 pod 调度行为。

生产可考虑同一 PostgreSQL 实例、同一数据库的部署提案；SDK 表保持原 schema/名称不变（当前脚本未写 schema，取决于部署 search_path），不移动或复制。新增 Runtime 表由 SDK 管理；业务表放 twinteams schema 的提案仅适用于 PostgreSQL。SQLite 使用适合其后端的逻辑命名与隔离，不机械复制 PostgreSQL schema 语法。文中 RT 表示 SDK 所属数据，不要求立即迁入名为 runtime 的 schema。

下文字典中的 uuid/text、jsonb、timestamptz、部分索引、行锁/CAS 和跨表事务以逻辑需求及 PostgreSQL 示例表达；实际类型编码、约束、迁移与并发适配需分别支持 SQLite/PostgreSQL。不能把 PostgreSQL 专用 DDL 直接视为双后端 migration，也不能为兼容 SQLite 取消租户隔离、去重、可靠接受或 SDK 的执行安全要求。

- Runtime：session、语义事件 journal、run、模型与工具尝试及完整结果、输入/回传、审批执行状态、操作账本、checkpoint、归档；token live 流不属于持久恢复数据。
- Twinteams：身份配对、成员与历史边界、消息及读回执、触发判断与 FIFO、Chat→Session 映射、Tracking、摘要授权来源、工作空间和 UI 设置。
- 不新增第二套 runs/events 复制 Runtime 事实。产品只保存引用及业务投影，投影可以从持久事实重建。
- 业务通过 SDK 接口提交命令、读取运行状态和完整 output；即使同库，也不由 Twinteams 直接维护 SDK journal/checkpoint/执行租约。跨表原子性需要双方明确事务适配契约；若无法共用事务，使用各自拥有的 outbox/inbox 桥接，不宣称跨连接或跨数据库天然原子。

## 3. 统一约定与关系图

新增业务 ID 建议 uuid；引用 Runtime 的 tenant/session/run/event ID 保持 text。所有表含 tenant_id text NOT NULL，PK/FK 必须包含 tenant_id。下文为压缩字典，id 指表自身 uuid 主键；外键全部含租户维度，timestamptz 用数据库 UTC，顺序用 bigint，不用时钟单独判定先后。jsonb 仅用于版本化载荷，不把关键关系、授权或状态藏在 JSON。

简写字段类型规则：所有 *_at / *_until 为 timestamptz，*_seq / revision / fence 为 bigint，*_no / generation / epoch 为 int，active/enabled 等为 boolean，hash/digest/status/kind/key/ref/name/title 为 text，正文为 text 或受控 blob 引用。除标记 ? 或明确可空外均 NOT NULL；表中字段为核心逻辑字典，最终 DDL 仍需逐列列出默认值、约束名称与迁移顺序。业务 owner actor uuid 通过明确映射/字符串化传给 SDK 的 user_id text，不改变 SDK 类型；不把 user_id 当 Twin ID。

状态采用 text + CHECK 与状态迁移校验，避免早期 PostgreSQL enum 难改；新增状态均是提案。所有可变投影带 revision bigint，采用乐观并发。不可变事件只追加，纠错通过新事件。硬删除、保留期和法律删除政策待定；不把“持久”误解为允许无限保留秘密。

```text
Owner ──1:1── Twin                     真人与 Twin 各是独立 Actor
   └──── paired membership ──── Chat (private / group)
                                  ├─ Messages ── Reply request ── Attempt
                                  │       └─ Causal chain / Auto budget / Human Continue grants
                                  ├─ (Chat,Twin) Session binding ── RT Session
                                  │                                  ├─ Runs ── parent/child edges
                                  │                                  └─ Journal + Full outputs + Checkpoints
                                  ├─ History grants ── Sources ── Summary / Tracking
                                  ├─ Workspace ── File versions
                                  └─ Per-owner settings / Read receipts
RT Run ── Commands / Tool operations / Approvals / Outbox / Inbox
```

图中 RT 及其 Commands / Tool operations / Approvals / Outbox / Inbox 均归 Agent Runtime，表示已有能力或待 SDK 评审的接口依赖；Twinteams 仅拥有图中的产品实体、来源映射与业务投递状态。审批 UI/产品路由归 Twinteams，审批执行状态与受控工具动作归 SDK。

## 4. 身份、聊天、成员与历史权限

| 新增业务表 | 字段重点 | 约束与索引 |
|---|---|---|
| actors | id、kind text(human/twin)、created_at、disabled_at、display_name | PK tenant/id；身份 ID 不变，显示名可变；禁用不重写历史作者 |
| owners | actor_id uuid、auth_subject text、created_at | PK tenant/actor_id；FK actors；UQ tenant/auth_subject；必须 kind=human |
| twins | actor_id uuid、owner_actor_id uuid、created_at | PK tenant/actor_id；FK actors / owners；UQ tenant/owner_actor_id；必须 kind=twin |
| chats | id、kind(private/group)、private_owner_id uuid?、title、next_message_seq bigint、next_membership_seq bigint、created_at | private 必有 owner，group 必无；UQ tenant/private_owner_id WHERE private；title 不作为标识 |
| chat_memberships | id、chat_id、owner_actor_id、joined_at、left_at?、join_message_seq bigint、membership_seq bigint、revision | 每次加入一条记录，UQ tenant/chat/owner WHERE left_at IS NULL；INDEX chat/current 与 owner/current |
| history_grants | id、membership_id、policy(all/fixed_7d/no_prior)、lower_seq bigint、upper_seq bigint?、effective_at、revoked_at?、policy_version | 不覆盖旧版本；INDEX membership/effective_at；授权修订要留事件 |

Owner/Twin 配对加入通过一条 membership 表达，成员视图派生两位 actor，而不是分别插入两条可失配成员记录。事务中创建 owner 与 twin，跨表 kind/一对一完整性用约束触发器或受控写入函数校验；不能仅靠两条独立 INSERT。首次创建私人 Chat 也在同一事务完成。帐号停用、继承与永久删除仍需产品决定，历史身份不重用。

群聊两位 Owner 就是 group；不增设第三种 direct 类型。扩大群组只是新增 paired membership。当前成员计数显示真人或所有身份未定，不在 schema 固化显示口径。

**加入边界必须固定。** 在锁定 Chat 顺序分配器的事务内记录 join_message_seq：all 从首条开始；no_prior 从加入后第一条开始；fixed_7d 使用 joined_at−7 天在本聊天已提交消息中求首个获准 seq，并永久保存 lower_seq。消息服务端 created_at 与 seq 在同一 Chat 锁下生成，避免时钟倒退/客户端伪时间改变边界。最终授权检查使用明确 seq 集合/区间，不能每天滚动计算 seven days。

历史授权属于 membership incarnation；退出重入不能不经判断沿用旧 grant。退出后是否保留阅读权、修改历史范围如何影响已有内容是未决项：首版安全提案是失去当前成员资格即停止新查询/工具操作，旧数据保留审计，任何扩大访问需明确授权版本。此项不是已确认产品需求。

## 5. 消息、来源、引用与真人已读

| 新增业务表 | 字段重点 | 约束与索引 |
|---|---|---|
| messages | id、chat_id、seq bigint、author_actor_id、kind(human/twin/reply_for_me/system)、state(streaming/published/withdrawn)、reply_to_message_id?、source_request_id?、created_at、published_at?、current_revision bigint | UQ tenant/chat/seq；UQ tenant/chat/id 支持同聊复合 FK；INDEX chat/seq；author FK actors |
| message_revisions | message_id、revision bigint、body text 或 encrypted_blob_ref、raw_owner_input_ref?、owner_actor_id?、source_event_id text?、created_at、reason | PK tenant/message/revision；不可变；raw 与 processed 是同一消息同一 ACL；记录内容 hash |
| message_origins | message_id、request_id?、attempt_id?、session_id text?、run_id text?、output_key text、source_owner_id?、acting_for_owner_id?、origin_kind、chain_id?、parent_message_id?、parent_request_id?、source_manifest_id?、prompt_version? | UQ tenant/output_key；Twin 来源必须有 source Owner/chain，非对话系统消息可空；Twin 作者与 source Owner 分离；完整 session/run、chain 与父来源键；来源原输入另有持久 command |
| human_read_receipts | message_id、owner_actor_id、first_seen_at、last_seen_at、evidence_kind、client_event_key | PK tenant/message/owner；仅 human owner；INDEX owner/message；重复上报幂等 |
| view_events | id、chat_id、owner_actor_id、device_id text、view_epoch bigint、kind(open/focus/close)、observed_at、received_at | 可持久记录影响触发的查看证据；客户端时间不直接决定先后 |

messages.state=streaming 仅表示回复正在生成，可以保存消息占位及关联，不能解释成逐 token 写 message_revisions 的要求。正常连接的临时文本可以直接来自 live 流；对应完整 output 持久提交后，再形成可恢复的正文 revision/引用。恢复时按已提交 output 重建正文，不从旧客户端前缀拼接；尚无完整 output 的当前操作只展示已知 Run 状态，已有其他完成操作的 output 仍保留。消息是否发布/撤回与 Run 是否结束分别判断。

主动 Twin 消息与回复均保留实际 author_actor_id=Twin、source_owner_id、来源 Run、父消息/请求、chain_id 和披露来源 manifest，不能因为按来源 Owner 路由而改成真人作者。跨聊天父来源是因果边，不受 reply_to 必须同聊的限制，但不向无权读来源的用户展示私密引用。目标聊天发送权及受众披露权在发布前检查；既有聊天发送工具不能自行加成员/新建聊天绕过授权。只知道 Twin 所属 Owner 不代表拿到了该 Owner 的工具副作用批准。

Reply for me：先持久保存 Owner 原始输入 command，再生成 Twin 输出；消息 author 为 Twin，origin 标明 acting_for owner。发布时同一消息 revision 保存 raw 引用与加工文本，二者共享授权。处理中原输入可以只在 Owner/Runtime 的受控待处理域可见；一旦发布，所有获准读该消息的人都可见两部分。不另发一条真人消息。全局/单聊提示词的实际版本与 hash 随请求冻结，不能重放时偷偷换用新提示词。

reply_to 必须属于同 Chat。引用展示时再次检查被引用来源 ACL：读得了新回复不表示读得了旧引用，越界引用仅显示不可用占位，不复制旧文泄漏。生成时的引用片段与来源 revision 可记入审计，但对用户投影继续受来源权限约束。

Stop replying 不删日志：将本次请求已经持久化的所有 message/output segments 标记 withdrawn，追加撤回事件，UI 隐藏正文并清除该请求的临时 token 文本；已持久化的原始内容保留在严格受控审计存储，不要求补存临时 token。撤回后引用、摘要、Tracking 派生投影需失效/重建，防止绕过 UI 再读到撤回内容。历史审计查看权限与一般聊天阅读分开。

真人已读必须来自本人前端显示/确认信号，不能由 Twin 处理生成。不能只用 max_seen_seq 推断中间所有消息读过；滚动跳读会漏看。首版使用逐消息 receipt；优化可用明确连续可见区间压缩。打开会话可抑制该时刻符合规则的待触发项，和某条消息实际已读是两类证据。

## 6. 登录、在线、查看：短期状态与永久决定分离

登录会话以认证系统为权威，不把 Actor 表的布尔位当在线。建议 presence_leases(tenant, owner, device, auth_session_ref, last_heartbeat_at, expires_at, focused_chat_id, view_epoch) 作为 TTL 投影，可在 PostgreSQL 或缓存中保存；缓存丢失时不能凭空恢复为“正在查看”。TTL、后台标签页、多设备合并与“登录即在线”的精确定义尚待确认。

TTL 投影不需要保存每个无意义心跳，但每次触发判断必须持久保存当时读取到的在线/查看/已读证据、版本与判断时间。所有进入 Agent 的事件必须入日志；presence 网络心跳本身不是模型事件。若其改变触发结果，产生持久 decision/view 事件。恢复时不根据当下 presence 重新改写过去的已接受决定。

## 7. 目标识别、10 秒预触发与接受去重

| 新增业务表 | 字段重点 | 约束与索引 |
|---|---|---|
| routing_decisions | id、message_id、decision_version、strategy(pair/llm)、source_owner_id、model_attempt_id text?、context_manifest_id?、needs_response bool、content_guard_result(first/duplicate/not_applicable)、content_guard_ref?、inferred_owner_ids jsonb、mentioned_owner_ids jsonb、potential_owner_ids jsonb、final_owner_ids jsonb、status、reason、policy_version、created_at | UQ tenant/message/decision_version；潜在与最终目标分离；false 时 final 为空；群路由排除来源 Owner；私人 Owner/Twin 直达交互不套用此排除；候选明细可拆 routing_targets；目标必须为当前合法成员 |
| trigger_candidates | id、message_id、target_twin_id、decision_id、chain_id、state(waiting/suppressed/paused_budget/accepted/expired/error)、reason_code?、due_at?、observed_presence jsonb、read_evidence?、accepted_request_id?、revision | UQ tenant/message/target_twin/decision_id；INDEX due_at WHERE waiting；paused_budget 尚未接受，不进入 FIFO |
| reply_intents | id、chat_id、source_message_id、target_twin_id、generation int、request_id? | UQ tenant/chat/source_message/target_twin/generation；统一自动与手动逻辑去重 |
| reply_requests | id、intent_id、chat_id、target_twin_id、requester_actor_id?、source(auto/manual/retry/reply_for_me)、chain_id、parent_request_id?、trigger_authority(auto/human)、human_authorization_id?、budget_reservation_id?、accepted_at、accepted_seq bigint、state、client_request_key text、retry_of_request_id?、cancelled_by?、revision | UQ tenant/client_request_key；UQ tenant/intent_id；INDEX chat/twin/accepted_seq WHERE queued；自动接受有唯一预算记录，人工请求有可验证真人操作证据，retry 不隐含新授权 |

当前已确认路由（Sentinel_fca0adb1ac888191835278e37f931b47、Sentinel_5297a0cb1f3c8191b3e0795320e73836）：A Twins 发言按来源 Owner A 路由，保留实际 Twin 作者并排除 A Twins 自触发。两 Owner 群默认对方为候选，无需额外目标识别；独立回复必要性判断仍适用。多人群由独立 LLM 判断 needs_response 与候选目标，合并显式 @ 真人成为 potential 集合；needs_response=false 时 final_owner_ids 为空，包括被 @ 者。为 true 后才逐目标判断触发。感谢、确认、完成、无新信息可以正常不回复，空最终集合本身不是路由错误。保存模型尝试、授权 manifest、potential/final 集合、原因与版本。低置信度/路由失败反馈、@Twins 解析仍需细化；不能再把 Twin 互答、空目标或已确认默认 6 次防护列成未决。

每个目标分别判断：离线可立即尝试接受；在线未查看建立 due_at；正在查看抑制。所有实际自动接受都先通过权限、精确重复与同链预算检查，再原子提交。10 秒起点待确认，提案用服务端消息提交时间；LLM 路由超过窗口时立即依据保存的消息期查看/回复证据和最新状态裁决，不能丢失等待期打开过的事实。Owner 在等待期间查看/已读/明确回复时，持久标记 suppressed，离开后不补旧消息。接受后状态机不再订阅 presence 自动取消。

定时器 worker 与查看/手动请求事务锁同一个 candidate/intent，再在事务内复查持久证据。真正的竞态按数据库线性化提交次序定义；客户端早已打开但信号尚未到服务器无法凭数据库保证，需产品接受网络边界并评估短暂容错。

自动和手动争同一 intent，仅一个接受请求胜出；失败自动、手动不各排一个。generation=0 初次，明确 Retry 新增 generation/request，在 lane 内获得新的 accepted_seq；同一个 Retry 操作重发靠 client_request_key 去重。用户有意要求同消息再次回答是否可建新 generation 仍待定，不通过随机 key 绕过去重。

### 7.1 已确认的业务因果链与预算语义

来源：Sentinel_fca0adb1ac888191835278e37f931b47（主动消息及 A Twins 等同 A 的触发语义要求）、Sentinel_5297a0cb1f3c8191b3e0795320e73836（同意完整方案并更新设计）。以下均为已确认设计，不代表已实现。

单链初始默认可配置 6 次自动 Twin 回复接受，真人 Continue 可追加新段额度，跨全部 Twins、聊天与 SDK Run 共享。首次与多目标每个新接受均计一次；不是每个聊天/Session 重置。独立新真人消息创建新 chain；显式 Continue 与原请求的人工 Retry/手动请求保留原链。真正独立的主动来源可建立自己的根，但不能把同 Run 主动发送、子任务结果、Run 切换、恢复或跨聊投递伪装成新根。

Runtime 的 root_run_id、parent_run_id、task_id 不能替代业务 chain_id；它们分别表达运行关系，可能横跨多个 Run/Session，不承载用户对自动对话的业务授权。产品维护来源到 chain 的稳定映射，SDK 执行机制保持原职责。

### 7.2 业务表与约束提案

以下字段服务已经确认的行为，具体命名/DDL 仍待实现；均是 Twinteams 业务表，不是 SDK loop/运行状态表。

| 新增业务表 | 核心字段与约束 |
|---|---|
| causal_chains | tenant_id、id、root_source_kind、root_message_id?、root_run_ref?、root_authorization_id?、created_at、auto_accepted_total bigint、allowance_total bigint、budget_state(open/paused)、pause_epoch int、policy_version、revision；PK tenant/id；所有聊天与 Twins 引用同一链；累计接受数单调，不因失败/取消/恢复归零 |
| chain_edges | tenant_id、chain_id、edge_id、parent_message_id?、parent_request_id?、source_session/run/task_ref?、child_message_id?、child_request_id?、edge_kind、created_at；稳定 source key 唯一，保留跨聊天父来源，访问受来源 ACL 约束 |
| chain_allowance_grants | tenant_id、id、chain_id、grant_kind(initial/human_continue)、amount int、actor_owner_id?、human_action_id?、pause_epoch?、authorization_context_ref?、policy_version、created_at；默认每段 amount=6，可配置且冻结版本；human_continue 必有经认证真人动作及所续接的 pause_epoch，UQ tenant/human_action_id 与 tenant/chain/pause_epoch（仅 continue），重复操作/同次暂停双击不重复加额 |
| chain_budget_reservations | tenant_id、id、chain_id、request_id、acceptance_key、grant_id、ordinal bigint、created_at；UQ tenant/request_id；UQ tenant/chain_id/acceptance_key；UQ tenant/chain_id/ordinal；每个新自动接受恰有一次记录，不与 SDK 模型尝试混淆 |
| chain_content_guards | tenant_id、chain_id、normalization_version、normalized_content_hash、normalized_content_ref、first_message_id、created_at；INDEX tenant/chain/normalization_version/hash；受控事务下核对规范化内容精确相等；hash 碰撞不同正文可独立保存，不能拿 hash 碰撞当重复 |
| human_trigger_authorizations | tenant_id、id、owner_actor_id、human_action_id、chat_id、source_message_id?、target_twin_id?、kind(manual_request/retry/continue)、chain_id、created_at；UQ tenant/human_action_id；由受认证真人入口生成，Twins/工具不能自行声明真人动作 |

配置中的 6 表示新段自动接受额度。初始 grant 与链根一起持久提交；人工 Continue 是追加 grant、增加 allowance_total，不改写先前 grant 或 auto_accepted_total。运行中更改默认配置不追溯停止已接受项；新段记录实际额度与策略版本。自动接受表中 grant/计数与 request 必须一致，不能只依赖客户端计数或内存缓存。

### 7.3 原子接受、去重、恢复与暂停

1. 在同一业务事务内校验来源 chain、needs_response、来源 Owner 排除、目标成员/权限、presence/已读证据与确定性精确重复；锁 chain 与目标 intent/lane，采用统一顺序避免跨目标死锁。
2. 先按 intent、client_request_key、retry 操作键返回已有接受/预留结果；不能先扣预算再发现重复。新自动请求仅在 auto_accepted_total < allowance_total 时预留一次、增加累计计数、分配 lane FIFO、写 request 与业务 outbox，并一起提交；达到本段累计上限时记录 paused/pause_epoch，但已接受请求保持原状态。PostgreSQL 行锁/条件更新与 SQLite 串行写入/条件更新分别适配同一原子不变量。
3. 多目标共享 chain 行或等价 CAS，在余额只剩一次时最多一个新自动接受成功。预算不足的尚未接受候选持久 paused_budget，保存原因；不创建 queued request，不占 FIFO，不向 SDK 发新启动命令。
4. queued/running 请求已经消费其名额，预算耗尽只影响新接受，不取消、暂停或 Stop 已接受请求。失败/取消/完成不退还已经发生的自动接受；Owner 查看也不触发自动撤销。
5. 同一请求的 outbox 重发、内部模型/工具重试、SDK resume、重连或接管不新增 reservation；新的自动 Retry request 沿原 chain 另预留一次，同一 Retry 操作重发只一次。真实人工 Retry/Ask 请求附 human authorization 并遵守 FIFO，不重置链、不自动加额。source=retry 或 manual 标签本身均不是免预算证据。
6. Run 切换、subagent 完成通知与后续结果发送、跨聊天投递、同一 Run 多次主动发送保留业务 chain 与父来源；如果需要新自动回复接受仍走同一预算事务。既有被接受任务的继续/结果不因预算耗尽被强制停止，具体 SDK 执行与主回复发布调度仍由既定集成规则处理。
7. 进程崩溃后从 chain/grant/reservation/edge 与 accepted request 恢复累计状态；不能按当前聊天/活跃 Run 重新数最近消息。缺少来源关联时不能猜一个新 chain 来放行，需补全可信关联或报告阻塞。

精确重复保护在同链中检查不同自动 Twin 消息的确定性规范化结果，只阻止重复内容引发尚未接受的新自动接话；不撤回已发消息，不修改已接受请求。内容 guard 原子绑定 first_message_id，同一首次 message 的多个目标可以分别受理；同 message_id 重投沿用原 verdict，不把逐目标接受或重试误作新重复消息。规范化算法需固定版本并可重现，字段与编码实现时明确；不能增加 embedding/模糊语义匹配丢弃。语义是否需要回应由独立路由 LLM 的 needs_response 决策记录解释。

### 7.4 Continue 与披露边界

预算暂停是业务候选状态，不是 SDK Run 的 paused/failed/stopped，也不能覆盖原有 Failed/Retry 或 Stop replying 事实。UI 显示授权范围内的暂停原因和 Continue；跨链聊天明细不默认暴露。

真人明确 Continue 的事务验证当前用户对目标聊天及续接请求的权限，锁定并核对当前额度耗尽的 pause_epoch，记录 human_trigger_authorizations 与唯一 action key，向原链追加默认 6 次的新段 grant，保留原根来源和累计已用量。同次暂停即使收到多个双击动作也只追加一次 grant，已不在该暂停状态的旧 Continue 不再加额；重复授权返回已有结果。继续不是自动放行所有候选：重新应用 needs_response、在线/查看/10 秒、来源 ACL 与精确去重；预算暂停本身不赋予忽略真人查看的权利。

业务工具/后台任务不能创建 human_continue grant 或伪造 manual 权限。手动入口始终可用是指真实用户请求仍可依既定权限与 FIFO 受理，不是赋予 Agent 自己追加额度的工具。Twin 消息、预算 grant、手动请求均不能替代工具副作用所需的 Owner 授权。

跨聊天消息发布同时核对目标发送权、当前受众及每个原始/派生来源的披露范围；保存 source manifest，避免通过消息复制、摘要、Tracking 或工具结果搬运私域信息。chain/edge 可记录敏感来源关联，但对 UI 只返回当前读者有权看的投影，不把全链共享计数变成跨聊天读取权限。

## 8. FIFO、主回复互斥、取消与尝试

| 业务表提案 / SDK 集成依赖（分别归属） | 字段重点 | 约束与索引 |
|---|---|---|
| twinteams.reply_lanes | chat_id、twin_id、next_accept_seq bigint、active_request_id?、revision bigint | PK tenant/chat/twin；单行锁保证业务接受顺序与调度占用，不承载 SDK 执行租约 |
| twinteams.reply_attempts | id、request_id、attempt_no int、session_id text、run_id text?、runtime_execution_ref?、state(starting/running/recovering/completed/failed/stopped)、started_at、ended_at、error_code? | 仅保存请求到 SDK 执行的关联与状态投影；UQ tenant/request/attempt_no；每 request 同时最多一个非终态业务 attempt；不复制 SDK 模型/工具尝试日志 |
| twinteams.chat_twin_sessions | id、chat_id、twin_id、session_id text、epoch int、active bool、created_at | UQ tenant/chat/twin WHERE active；UQ tenant/session_id；FK RT.agent_sessions |
| RT.agent_session_leases（SDK 侧增强建议，非业务建表项） | tenant_id、session_id、fence bigint、holder text、lease_until、revision | PK tenant/session；FK 原 session；由 SDK 对 append 与操作派发验证 fence |

业务 request 建议状态 queued→dispatching→running→completed/failed/stopped；queued→cancelled；运行中 stopping 为短暂过渡，不是立即释放锁。接受事务按固定锁序关联 chain/intent/lane；自动项依第 7 节原子预留额度，分配 accepted_seq、插 request 与 dispatch outbox，再提交。accepted_at 是展示时间，accepted_seq 是 FIFO 权威；同 lane 下不使用全局 sequence 的分配先后冒充提交先后。

Twinteams 调度器锁业务 lane，选最小 accepted_seq，设置 active_request、建业务 attempt，再通过可靠投递调用 SDK。SDK 负责执行租约、fence、Session 单 writer 及实际启动/恢复。不同业务 lane 可独立调度，同 lane 无论手动自动同优先级。收到 SDK 主 Run 真正结束的事实后才清 active；发“已收到”这句话不释放占用，subagent 继续不必占用主 lane。子任务回传如何参与产品主回复队列尚未确认，SDK inbox 可先持久接收，不暗中设为插队。

SDK 执行契约：租约过期只允许接管，不证明旧进程停止；SDK 的新 fence 应阻止旧 worker 提交/派发，不能阻止已发出的远端调用，未知副作用仍须对账。Twinteams 依据 SDK 有效运行标识及业务请求的发布/撤回状态接收输出，不自行建设第二套执行租约或工具账本。

取消权限：请求者可取消本人请求；目标 Twin Owner 可取消发给自己 Twin 的任何排队请求。自动项原消息发送者可按已确认 UI 权限取消该消息的对应目标请求，request 需明确保存 attribution/source_sender，不能 requester=NULL 导致权限丢失。原消息下按钮仅给有权的发送者；目标 Owner 在自己 composer 操作。请求者不是原作者时的入口位置仍待定，但后端取消权保留。

Stop replying 的产品权限仅给目标 Twin Owner；Twinteams 锁 lane/request、可靠提交停止请求、标记本次输出 withdrawn 并阻止迟到内容重新发布。SDK 负责停止/隔离旧执行与 fence；Twinteams 收到主 Run 终结事实后结束业务 attempt/request，并调度下一 FIFO 项，不等子任务全部结束，也不声称回滚工具。同一 request 对应一个逻辑主 Run：下一 FIFO request 等当前主 Run 终结，再请求新的 prompt，不能通过 enqueue/steer/follow_up 偷塞进旧 Run。SDK 恢复可建立新 execution attempt，但用 resume 保留原 run_id；产品维持同一 request/run 关联。用户 Retry 则是新的 request 排尾并产生新 Run，不能与模型内部重试混用。

## 9. 临时 token 流与完整 output 恢复（已确认）

来源：Sentinel_163f756d0bc0819183270b91e8d32935。此决定取代旧版第 9 节“可选精确事件流”及其待确认项。当前不建立 RT.agent_stream_events、token/chunk 持久表或 stream_seq / last_stream_seq / fork_stream_seq 等 token 回放字段；旧方案不进入关系图、恢复契约、Activity 查询或 v1 验收。未来若要精确回放每个 token，属于另行提出的新需求。

### 9.1 两种数据与各自的保证

- 临时展示数据：逐字 token delta，以及不改变执行事实的纯展示 progress，走内存 live 流。只在正常连接期间尽力提供打字机效果；不要求逐条写库、跨 pod 复制、重连补齐或历史动画回放。允许已经显示过但尚无完整结果的局部文字丢失。
- 持久恢复数据：已接受输入、Run 与语义 Event、每次模型/工具调用的请求和结果、checkpoint 及命令/审批/父子关系。每个产出完整结果的操作，在其语义完成边界持久保存完整 output 或现有结果字段/受控引用；不等待整个 Run 完成才保存。没有输出的生命周期事件不需要虚构 output。
- 数据库中的完整结果是恢复和历史展示的权威来源。模型完整响应、工具 raw return 与 final result 保留各自的已有提交边界，不能用 Run 的最终摘要替代，也不能为了节省 token 存储而省掉工具审计与恢复事实。
- 本文的 output 是产品层对完整结果的称呼，不要求把 SDK 所有记录改成同名字段。继续使用原 journal 记录与结果引用，避免复制第二套权威结果表；与 tenant、session、run、event、模型 attempt 或 tool operation 的关联必须保留。

### 9.2 正常展示、重连与跨 pod 接管

1. 正常连接：live token 可先于完整结果提交显示，但属于临时文本；完整 output 成功提交后，以完整结果替换临时文本并形成持久消息/Activity 投影。不能按 token 已展示推断结果已经保存或 Run 已完成。
2. 重连或恢复：重新鉴权，读取持久 Run 状态、语义事件及已经提交的完整 output，直接展示完整结果，不复演打字机动画，不要求旧 pod 或客户端交回 token 前缀。通过稳定事件/输出标识幂等覆盖，不能把完整 output 追加到旧前缀后造成重复文本。
3. 仍在执行但没有已提交 output：清除失效的临时前缀，展示最后已知 Run 状态；可继续展示此前其他操作已经提交的完整结果。等待当前操作完整结果提交后再展示，不编造丢失文字或假称从断点 token 无缝续写。
4. 跨 pod 接管：按相同契约从共享持久存储恢复；不为保住打字机效果增加 token 复制、持久 chunk 或 token 游标。恢复后的已有流片段无需补齐；后续独立操作若建立正常实时流，可重新按正常连接规则展示。
5. 展示恢复与执行续跑分别处理。客户端掉线本身不触发新请求、新 Run 或重复模型/工具调用；执行接管仍走 lease/fence、journal、checkpoint、inbox/outbox 与工具副作用对账。完整结果未落库时按实际执行阶段恢复，不能拿临时文本当作工具成功凭据。

完整 output、语义状态与消息发布必须在对应持久提交后作为已保存结果对外确认；临时 token 不适用“每个 delta 提交后才展示”的旧约束。完成结果与 live 更新发生竞态时，以已提交的完整结果和当前发布/撤回状态为准，迟到的 delta 不得覆盖最终正文或复活已撤回内容。

### 9.3 语义事件覆盖与安全投影

保留输入接受/修订、Run 开始/结束/停止/失败、模型调用请求与尝试/完整响应/usage、工具意图/开始/原始返回/最终结果、嵌套工具、审批请求/决定/失效、父子任务 spawn/send/result、队列接受/出队/取消、checkpoint/恢复/分叉、消息发布/撤回、投递确认与人工核对结果。纯业务事件以业务日志/outbox 为权威，Runtime 保存关联引用；不将业务历史复制成第二个真相。纯展示 progress 可丢弃，但其中涉及执行状态改变的事实必须单独持久提交。

事件身份包含 tenant/session/event，排序沿用 journal seq；SDK fork 会保留历史 event ID，不能仅凭 event_id 全局去重。尚无 Run 的会话事件可不挂 Run，Run 开始后的执行事件必须明确关联；异步结果的 originating run 与 receiving run 分别记录。跨 Session 的父子顺序用明确因果关联，不把各自 journal seq 当作全局顺序。

正常工具原始载荷可加密保存，凭证只保留秘密引用。blob 先持久写入并校验 hash，数据库提交引用后才确认完整结果可用；不能提交指向不存在内容的引用。原始敏感记录只有受控后端审计角色可解密；Activity 只接收经来源 ACL 与字段白名单处理的 projection。错误堆栈、提示词、工具参数也可能有秘密，必须分类。隐藏内部推理不进入公开卡片；提供安全的结构化进度摘要。

## 10. SDK 集成要求：可靠命令、父子通信与 Outbox / Inbox

本节 agent_* 表均为 Agent Runtime 侧增强建议，具体 schema 与实现由 SDK 项目决定，不是 Twinteams 待建表。产品只拥有业务 outbox 及请求/消息关联，并依赖 SDK 的可靠接受与结果回调契约。

| Runtime 增强表 | 关键字段与约束 |
|---|---|
| agent_commands | PK tenant/command_id text；session_id、command_seq bigint、kind、payload_ref/hash、idempotency_key text、accepted_at、state(pending/applied/rejected)、applied_journal_seq?；UQ tenant/session/idempotency_key 与 tenant/session/command_seq |
| agent_outbox | PK tenant/outbox_id text；event_id、destination、message_key、payload_ref、available_at、attempts、lease/fence、delivered_at?；UQ tenant/destination/message_key；待发索引 available_at |
| agent_inbox | PK tenant/receiver_session_id/message_key；sender_session_id?、sender_run_id?、task_id?、payload_ref/hash、received_at、consumed_command_id?、state；接收与唤醒 command 同事务 |
| agent_run_edges | PK tenant/child_session_id/child_run_id/edge_kind；parent_session_id、parent_run_id、task_id text?、spawn_event_id、origin_context_id；双方引用完整 run 键，允许同 child session 多 runs |

业务也可有 twinteams.outbox，采用相同可靠模式但由业务拥有；不同域共享一个物理消息基础设施也可，不需要为了命名再造一套消费协议。产品事务接受 reply_request 后写 outbox；Runtime inbox/command 幂等接收；确认回传建立 attempt↔run 关联。掉线可重发同键，不能创建新请求。

command 必须在回应“已接受”前提交。持久 inbox 由输入处理器写入，journal 仍由 Runtime 单执行协程消费写入；不要让 HTTP handler 与执行循环并发调用 Session.commit。Reducer 在 journal 里记录 command_applied(command_id)，与 applied 状态原子提交，崩溃后未应用命令可重新消费；已应用命令不再次注入。start/prompt 命令的新 run ID 必须在持久意图中预分配或与 command 原子关联，避免“run 已开始但回执丢失”造成第二个 run。这需要 SDK 增强，不是当前 prompt 已有幂等 API。

子任务结果先写 child journal + outbox；parent inbox 幂等收取、生成 wake command；通知与执行分离。通信至少一次传输、幂等消费，消费方状态与效果必须在同一个本地事务落定。parent_run_id 不可用 root_run_id 猜；task_id、child session、run 三者分别保留。不存在的父引用可在异步映射暂存后补齐，错误链不得进入 Activity。

## 11. 独立审批与通知

职责：Twinteams 定义谁能处理、审批入口/抽屉、路由与通知；Agent Runtime 维护授权决定、动作绑定、执行检查与后续恢复。下述 agent_approvals/approval_events 属于 SDK 侧集成建议，产品不能另存一套相冲突的批准执行真相。

Runtime 增强 agent_approvals：PK tenant/approval_id text，requesting_session/run、approver_owner_id text、operation_id text、action_digest、scope_digest、requested_at、expires_at?、state(pending/approved/rejected/expired/cancelled)、decision_actor_id、decided_at、revision。approval_events 记录每次请求/决定/过期；不可变决定用唯一 decision key 和 CAS pending→终态，重复点击不重复执行。

审批绑具体 operation 与规范化参数 hash。批准后更改金额/目标/数据/动作，原批准不再适用。执行前重新校验批准状态、期限、当前权限和 digest，并将批准与 operation 派发关联；Approve once 不等于允许无限工具重试扩大效果。长期授权独立 grant/version/revocation，管理 UI 后置，默认只读原则不能被手动触发或 Tracking 入列绕过。

Twinteams.approval_contexts(tenant, approval_id text PK, chat_id, twin_id, owner_id, source_message_id, arrival_seq bigint) 保存产品路由，审批决定与执行真相仍属 Runtime。SDK 保存 pending action 与 continuation/command 引用；主回复可以结束，由产品据终态释放业务 lane，审批通过后由 SDK 持久接收 callback，不挂着主 Run 锁等待。已经 completed 的主 Run 不再作为待续跑 continuation。批准后若需要模型继续，SDK 创建新的运行并保证同一 Session 单 writer；产品协调其与业务 FIFO 的关系。回调继续哪种业务主回复调度仍需定稿，不能把 SDK 执行锁与产品 lane 混成一个机制。

通知使用 notifications(id, recipient_owner_id, chat_id, kind, source_event_key, approval_id?, created_at, read_at?, dismissed_at?)，UQ recipient/source_event_key；notification_deliveries(notification_id, channel, attempt_no, state, provider_id?, error, sent_at)，不把“已通知”和“已批准”混为一谈。单聊审批按 arrival_seq 查询本人 pending；当前 tab/展开偏好放 UI state，新到达不覆盖选择，处理当前后选下一项、清零收起。通知、聊天已读、审批决定三个独立状态。

## 12. SDK 集成要求：工具副作用账本与恢复边界

以下是供 SDK 项目评审的执行安全要求。工具调用、账本、幂等、对账与恢复均由 Agent Runtime 实现，Twinteams 仅展示获准状态并提交用户明确决定，不直接派发或重试工具。

新增 RT.agent_tool_operations：PK tenant/operation_id text；session_id、run_id、step_id text、tool_call_id、parent_operation_id?、command_id、tool_name/version、args_ref/hash、effect_class(read_only/idempotent/reconcilable/non_idempotent)、external_idempotency_key、approval_id?、status(planned/dispatched/succeeded/failed/unknown/reconciled)、provider_operation_id?、raw_result_ref/hash?、final_result_ref/hash?、tool_returned_event_ref?、tool_completed_event_ref?、fence、created_at/updated_at。UQ tenant/session/run/step_id/tool_call_id。每次网络尝试记录 operation_attempts(operation_id, attempt_no, dispatched_at, outcome, provider_request_id, result_ref)。

协议：

1. 事务内写 tool intent、参数摘要、授权依赖、operation_id，提交后才能发外部请求。
2. 派发前记录 dispatched 与 attempt；把稳定 external_idempotency_key 传给支持它的 provider。
3. 外部响应收到后，在本地同事务写 result blob 引用、operation 终态、journal 结果与后续 outbox。
4. 崩溃在 1 之前：没有接受效果；在 1 后未派发：可安全继续；在派发后响应落库前：状态 unknown，不知道现实是否发生，优先按 provider id/key 查询。
5. 有幂等键可重试同 operation，不能换键；只读是否可重试也要考虑时效和额度。不可幂等且不可查询：人工确认真实结果，再记 reconciled；未确认前不重复副作用。

现有 tool_started / tool_returned / tool_completed 已提供工具边界事实；operation 表是幂等键、授权、外部对账与派发控制的补充索引/控制状态，应由这些 journal 事件投影或同事务关联，不能独立记录一份互相冲突的“工具是否成功”。raw_result 与 final_result 的独立持久边界不可合并；已保存 raw return 时恢复不能再次执行工具，只重做尚未完成的 after-tool hook，hook 需纯函数/幂等，或另建受控 operation。

数据库和第三方 HTTP 无共同事务，必须公开承认第 4 步的窗口。fencing 防止旧 worker 的本地提交，不保证撤销已送达供应商的请求。也不能把 timeout 当失败未执行。撤回消息或 Stop replying 不自动补偿转账/发信等动作。补偿是新的授权操作、有自己的 operation 与审计。

## 13. SDK 集成要求：恢复、快照、版本与分叉

执行状态恢复、checkpoint、版本兼容与分叉由 SDK 实现。Twinteams 的职责是保存来源映射、调用相应接口，并按第 9 节展示完整 output 与状态；下述机制不转化为产品运行时实现任务。

| 能力 | 输入与流程 | 不能承诺的事 |
|---|---|---|
| 历史状态回放 | 指定 session + journal seq，选不晚于边界的 checkpoint，校验 hash/version，重放到边界；展示该边界已有的完整 output 与状态 | 不触发工具、不把旧状态覆盖生产，不回放丢失 token，也不要求模型逐 token 可继续 |
| 续跑 pending run | 取得新 lease/fence，恢复 journal 状态、命令 inbox、工具账本、审批与 outbox；对账未知效果后继续原 run/关联 attempt | 不能无条件自动重跑非幂等工具；不能用新权限“恢复”越权上下文 |
| 从历史分叉 | 新 session_id，记录 source_session、fork_journal_seq、checkpoint hash、原因/发起人；建立新映射 epoch 或独立实验分支 | 不删除原分支、不回滚已发生效果、不沿用一次性批准，不携带 token 回放游标 |

继续复用 agent_session_checkpoints 位置表与 journal 中的完整 checkpoint，不再新建第二套同义快照主表。SDK 侧 checkpoint envelope 增强建议包含 reducer_version、event_schema_versions、last_journal_seq、state_hash、source_history_digest、pending_command_refs、tool/approval refs、created_at，不加入 token/stream 游标。完整执行状态及已提交结果必须可重建，不要求重建临时 token 前缀；引用外部 blob 也在备份和保留范围内。hash 用于检测损坏，若需要防管理员篡改，另用签名/外部不可变锚，hash 本身不是防篡改承诺。

现有运行状态 running/completed/stopped/failed 保持兼容。recovery_pending、awaiting_reconciliation 等可先用新增恢复控制投影表达，不能直接往旧 Run status 填新值而不评估消费者。事件采用版本化解码/upcaster；未知版本停止恢复并报警，不能跳过。迁移前保留原记录，重建投影可重复、可比较摘要/hash；不能声称已通过兼容测试。

生产 PostgreSQL 的灾备建议：WAL/PITR、对象存储版本与校验、密钥可恢复、归档清单一致性、定期恢复演练；实际 RPO/RTO 未定。SQLite 本地备份需按其存储方式设计，不假称具备相同的集群恢复保证。单次数据库提交的持久性受部署同步复制和磁盘策略影响，不能把应用日志设计等同于零数据损失 SLA。

## 14. 授权摘要、精确覆盖与上下文溯源

| 新增业务表 | 字段重点与约束 |
|---|---|
| context_manifests | id、chat_id、consumer_twin_id、membership/grant_version、created_at、sources_digest、policy_version、build_state；记录每次模型输入实际来源，不只最终 prompt |
| context_sources | manifest_id、ordinal int、source_kind、source_id、source_revision、range_start/end?、content_hash、grant_id；PK tenant/manifest/ordinal；多态来源由受控 resolver 校验 |
| summaries | id、chat_id、visibility_scope_digest、coverage_start_seq/end_seq、coverage_manifest_id、content_ref/hash、model_attempt_id、version、state(valid/stale/revoked)、created_at | 
| summary_segments | summary_id、ordinal、source_start/end、source_revision_digest、included_summary_id?；PK tenant/summary/ordinal；表达非连续来源及递归依赖 |

summary 的 begin/end 只是加速查找，**精确来源 manifest 才是授权与覆盖证据**。摘要复用必须检查实际包含的所有底层消息 revision/附件/Tracking 来源均在当前读者授权集合内，并且仍有效；权限 scope digest 相同只可作候选缓存键，不能替代最终权限检查。

7 天前产出的累计摘要可能包含几个月前内容，绝不能给新成员再要求模型忽略。对授权一致的有效片段复用，否则从授权原文重建。摘要变体按真实授权集合共享，不按“老成员五个人”标签共享。

上下文构建固定一个 high_water_seq，选覆盖范围一致的摘要，补全其后至 high_water 的所有获准原始消息；有洞则显式补洞。超长时分段摘要或检索，但必须记录缺口/选择策略，不可声称完整却仅取最近几条。Periodic 周期、token 预算未定。

编辑/撤回/权限变更使依赖它的摘要、Tracking 目录/详情及缓存失效；任何旧 cumulative summary 都不能跨越新的 ACL。正在生成期间权限变更：使用版本校验在输入装配与发布前复核，过期上下文不继续输出敏感内容。私人 Runtime memory 默认只在私域使用，群回复不自动注入；需要跨域披露时另设明确来源授权。

## 15. Tracking：目录、动态、删除与来源权限

| 新增业务表 | 字段重点与约束 |
|---|---|
| tracking_topics | id、chat_id、created_by_actor、current_revision、status text、deleted_at?、revision、created_at；INDEX chat/deleted_at；状态 active/paused/done 仅提案 |
| tracking_topic_revisions | topic_id、revision、title、short_description、source_manifest_id、created_by、created_at；PK tenant/topic/revision；目录内容也有来源 ACL |
| tracking_entries | id、topic_id、entry_seq bigint、revision、kind、body_ref、source_manifest_id、created_at、created_by；UQ tenant/topic/entry_seq |
| tracking_tombstones | topic_id、deleted_by、deleted_at、source_cutoff_seq、dedup_subject_key、reopen_command_id?；保留删除理由及明确再提出证据 |

私人范围直接绑定私人 Chat，群 Tracking 绑定本群 Chat，不多一套全局 topic 共享。主题自动入列，无需确认草稿；所有当前群真人/Twin 可修改删除获准访问的目标，revision CAS 防止覆盖别人更新。动态修订和删除事件持久，不硬抹历史。

目录“始终可见”表示始终提供**当前 Twin 有权看到**的目录。若标题来自不可见旧消息，隐藏该标题或从允许来源生成独立安全视图，不能因为目录短就免检。后续动态可见而旧主题名不可见时使用授权范围内新建的安全标签，或暂不展示整个主题，策略待确认。服务端不得返回越权 topic 的标题供前端隐藏。

删除后提取器检查 tombstone 与来源 cutoff，不允许旧聊天再次建立同目标。去重 subject_key 只能辅助，不能靠 embedding 相似度保证语义绝不复活；记录来源 lineage、同一主题标识与 reintroduction command。只有 Owner 新的明确提及/重启证据才能解除删除保护，不能把定期重跑 extractor 当作新意图。

## 16. Workspace、个人设置与通知展示

Workspace 核心建议：workspaces(id, chat_id UQ, kind private/group)；workspace_nodes(id, workspace_id, parent_id?, name, node_kind, current_version_id?, deleted_at?, revision)；file_versions(id, node_id, version_no, blob_ref, sha256, size_bytes bigint, mime_type, created_by, source_manifest_id?, created_at)；workspace_grants(id, workspace_id/node_id, principal_id, permissions, effective/revoked_at, grant_version)。文件版本 UQ tenant/node/version；同父目录有效同名唯一；parent 必须同 workspace，禁止循环。

每个 Chat 对应空间；对象存储为文件字节，数据库为不可变版本/授权。当前共享群空间不自动意味着允许新成员读全部旧版本，消息历史策略也不自动决定文件权限。首版安全提案是显式 workspace/file grant 与来源 ACL 双重检查，未明确共享的私人文件不带入群。上传、复制、下载、预览与版本 URL 都在签发前查权限，短期签名链接不作为永久权限。

owner_settings(tenant, owner_id PK, reply_prompt_default, prompt_version)；owner_chat_settings(tenant, owner_id, chat_id PK, reply_for_me_enabled bool, reply_prompt_override text?, override_enabled bool, last_sidebar_tab, sidebar_open bool, revision)。NULL 与空提示词通过 override_enabled 区分。private 默认 Activity，group 默认 Workspace，初次展开；group 不能保存 Activity tab。每 Owner/Chat 独立模式，改全局提示词不改变开关。

颜色、斜线硬分界、折叠块默认、头像独立图标是 UI 表现，不给每个色块加业务表。raw/processed、作者来源及 mode 设置足够支撑最新图 3；排队/typing 来源是 request/run 状态，不额外持久一张 UI typing 表。

## 17. 查询、权限与 Activity 投影

所有查询在身份认证后从 tenant/actor/membership 推导可读范围，不接受前端传来的 owner_id 直接作为授权。可用 PostgreSQL RLS 作为防线，但服务端鉴权、来源 ACL 与对象存储也必须执行；RLS 当前并非 SDK 已有能力。后台服务跨租户权限需最小化。跨域 FK 可同库校验；先验证 SDK 旧数据再加 NOT VALID→VALIDATE 约束，不能无视历史孤儿数据。

常用查询与索引：

- 聊天列表：memberships(owner,left_at,chat)→chats；消息使用 (tenant,chat,seq) keyset，并裁剪 history grant。
- 排队：reply_requests(tenant,chat,twin,accepted_seq) WHERE state=queued；typing 来源为 lane.active_request 的运行态。成功行从 UI 活动查询排除，但保留数据库历史；Failed/Retry 单目标保留。
- 审批：approval_contexts(tenant,owner,chat,arrival_seq) + Runtime pending，不能把别人待审批卡片传给前端。
- Summary：(tenant,chat,scope_digest,coverage_end_seq DESC) WHERE valid，再核对所有来源。
- Tracking：(tenant,chat,deleted_at) + 授权目录投影，动态按 (topic,entry_seq)。
- Activity：从当前私人 chat 的 session binding 和来源 request 找根 runs，再通过 SDK 父子关系取后代；校验 origin_context 指向该私人 chat。业务索引覆盖 chat/session/run 来源映射；SDK 查询应支持 runs 的时间分页与 session/run 内按 journal seq 读取语义事件，不依赖 token stream 索引。同 Twin 的其他群 session 不入集合。

Activity 可增加可重建的 run_origins 投影(tenant,session_id,run_id PK, origin_chat_id, root_request_id, parent refs)以避免每次递归，但不可凭同 user_id 全局查询 daily_runs。一个子任务 session 多次 run 各一张卡，按明确 run 边关联；没有直接父边的历史数据标记来源未知，不猜进私人列表。

详情通过 SDK 按 journal seq 读取语义事件并补归档，直接展示已提交的完整 output；工具 result 引用经鉴权解引用。重连不依赖 token cursor；当前操作没有完整结果时展示已知运行状态，不伪造中间文字。列表 summary 是显式完成消息/安全摘要投影，run_completed 不保证文字总结，也不能替代每次模型/工具调用的结果。默认建议 started_at 倒序并用 run key 稳定分页，具体 UI 排序键仍待确认；stopped/failed 不能标 completed。只读历史页面也禁止隐藏推理与秘密透出。

## 18. 必须成立的不变量与故障推演

以下是产品与 SDK 的联调目标，不表示 Twinteams 要实现每项运行机制，也不是已完成验证的结果。业务队列/消息/权限由产品落实；执行租约、工具对账和 checkpoint 恢复由 SDK 落实。

1. 同租户同 Owner 恰有一 Twin；任意历史作者 ID 不随改名/停用改变。
2. 每个 active Chat/Twin 只映射一个执行 Session epoch，私聊/群聊隔离。
3. 同 lane 至多一个被授权的主回复 writer；接受顺序按 lane 序号 FIFO，manual/auto 不插队。
4. 一个 logical intent 的同 generation 只能接受一次；Retry 是新 generation 新 FIFO 项。
5. presence/已读变化只终止 waiting candidate，不取消已接受 request。
6. 执行事实、已接受输入及每次操作的完整 output 必须持久；token delta 仅正常连接尽力展示，允许丢失，不要求先提交。副作用派发前有持久 operation intent；unknown 不当作未执行。
7. 所有消息派生内容、引用、摘要、Tracking 与文件均受来源 ACL，不能通过投影扩大权限。
8. Stop 只撤本次输出，保留审计；没有授权补偿不声称撤销现实副作用。
9. 审批决定仅作用指定动作摘要，独立等待不持有主回复锁。
10. 历史回放不产生外部效果，恢复分支不会重用旧一次性批准。
11. 同 chain 的全部 Twins/聊天/Run 共用自动接受预算，默认每段 6 次；每次新自动接受恰有一次原子预留。自动重发/恢复不重扣，新的自动 Retry 不重建链。
12. 预算耗尽只暂停未接受自动候选，queued/running 照常；真人 Continue 追加唯一授权和新段额度，累计总数与 lineage 不清零。
13. needs_response=false 时最终目标可为空，即使有显式 @；来源 Owner 的 Twins 不自触发。真实 Twin 作者/来源与工具授权边界不改变。
14. 精确重复只依据固定版本规范化后完全相同的内容，不引入未经确认的模糊语义丢弃；跨聊天来源及其派生内容继续遵从 ACL。

| 场景 | 预期持久状态 / 恢复结果 |
|---|---|
| 自动 timer 与手动按钮同时到达 | 锁相同 intent，仅一个 request；另一操作返回已有结果 |
| 第 9 秒打开群后离开 | view/suppressed 已持久；第 11 秒不追补旧消息，新消息独立判断 |
| 接受后用户打开群 | request 保持 queued/running，只有明确取消/Stop 改变 |
| 三目标中一个失败 | 各自 lane/request 隔离；失败目标显示 Retry，重试排尾，其余不受影响；重试保留 chain，按真实自动/真人授权分别处理 |
| 链余额仅一次，B/C 同时自动接受 | 共享 chain 的原子事务最多接受一个；另一个保持未接受预算暂停，不能两边超支 |
| 第六次已接受，队列中还未运行 | 已接受项照常 FIFO；仅后续新自动机会 paused_budget，不发 Stop、不撤队列 |
| 第六次以后同 Run 主动发送或子任务结果到达 | 保留同一 chain，已接受任务继续/投递结果；新的自动接话不得换 Run/聊天重获额度 |
| 人工 Continue 重放或并发重复点击 | 同 human_action_id 仅一个 grant；追加新段默认 6 次，保留累计消费和原始 lineage |
| 工具伪造 source=manual | 无可验证 human authorization，不能免除自动预算或新建 Continue grant |
| @A 的“谢谢，已完成” | 可 needs_response=false、final targets 为空；不因 @ 强制继续对话 |
| 同链自动消息规范化后完全重复 | guard 记录精确命中，不新接受自动回复；不删除消息或停止已接受 Run |
| Twin 把私人来源转发到另一聊天 | 发布前受众/来源披露检查拒绝越权，不通过 chain 关联扩展读取权 |
| 提交 request 后进程崩溃 | outbox 重发；Runtime inbox 幂等；不会丢请求或生成双 run |
| 正常连接已展示部分 token | 临时文本不要求落库；完整 output 提交后覆盖临时文本，迟到 delta 不得覆盖完整结果 |
| 已有完整 output 后断线或跨 pod 重连 | 从持久语义事件读取完整结果，直接展示且幂等去重；不回放逐字动画，不重复触发 Run |
| 只展示过部分 token，当前操作尚无完整 output 时重启 | 允许局部文字丢失；保留此前已提交结果，当前操作只显示已知 Run 状态；SDK 按执行边界恢复，不编造缺失内容 |
| 一个 Run 的前序模型/工具操作已完成，后续操作仍运行 | 前序完整 output 可独立恢复；不必等待 Run 最终总结，也不把前序完成误报成整个 Run 完成 |
| 工具成功但结果写库前断电 | operation=unknown；查询 provider 或人工核对，不盲重做 |
| 旧 worker 租约过期仍运行 | 新 fence 拒绝旧提交与新派发；已发远端效果走对账 |
| 审批挂起时来新问题 | 原主回复可结束；下一请求执行；审批独立，后续回调入 inbox |
| Stop 时刚收到输出/工具结果 | 同 fence 与事务检查决定发布；停止后旧输出不发布，结果仍记审计，对外效果不假称回滚 |
| 新成员只看固定 7 天 | lower_seq 不随日期变；拒用含更旧来源的 cumulative summary |
| 删除 Tracking 后重建索引 | tombstone+lineage 阻止旧主题复活；新明确 Owner 指令可重新开启 |
| 归档或快照缺块/版本不兼容 | 校验失败停止恢复并报警，不能略过后声称完整恢复 |

## 19. 实施拆分与评审决策

### Twinteams v1 业务实现范围

身份/配对成员与历史边界；消息/来源/真人 receipt；needs_response/潜在及最终目标路由、触发 candidate+intent 与业务 FIFO；持久跨聊 causal chain、共享自动预算原子预留、精确重复防护、暂停及真人 Continue 授权；Chat/Twin 到 SDK Session/Run/Event 的映射；可靠业务 outbox 与结果幂等投影；审批入口/路由/通知及用户决定提交；源权限约束的 Summary/Tracking；工作空间资料与权限；Activity 来源链、安全投影及完整 output 展示。文件版本可先采用简单存储实现，但权限不能后补。业务 accepted request 不能只放内存。

### Agent Runtime 侧集成依赖（不列入产品自建范围）

复用 DatabaseSession 原表、语义 journal、模型/工具完整结果和既有 checkpoint/replay。可靠 command/inbox/outbox、父子回传/唤醒、执行租约/fencing、工具幂等/对账与审批执行等，按已核对的现状区分已有能力和缺口，由 SDK 项目落实。Twinteams 验证调用契约和结果，不实现第二套 runs/events、运行恢复或执行并发控制；SDK 缺口需作为联调依赖明确报告，不能写成已经完成。

### 可以后置的优化

事件分区、冷存储自动分层、高吞吐批量写、物化 Activity feed、摘要细粒度片段缓存、read receipt 区间压缩、RLS 策略进一步自动化、长期授权管理 UI 与更多统计，按数据归属分别在业务层或 SDK 中评估。后置不影响语义事实持久化、不丢 accepted command、来源 ACL、fencing 与未知副作用停机对账。逐 token/progress 的精确回放已明确不在当前范围，不是等待默认启用的 v1 项；当前无需对应流表、chunk 表或 token 游标。

### 进入 DDL 前需要决定

持久化粒度已确认，不再列为待定：语义执行事实和每次操作的完整 output 持久保存，逐 token 展示数据可以丢失。Twin 互答、needs_response 可产生空最终目标、全链默认 6 次预算及 Continue 同样已确认，具体字段/双后端事务待落地，不再属于待定安全策略。后续需决定：

1. 已确认 SQLite 本地/PostgreSQL 生产后的双后端类型、迁移与并发适配；生产同库跨 schema 和对象存储方案；业务调用 SDK 的事务/投递接口。
2. presence/查看定义、10 秒起点、网络延迟规则；LLM 低置信度/路由失败反馈与 @Twins 解析。
3. 退出与重入、历史权限变更、共享文件旧版本策略。
4. 子任务/审批回调结果参与业务主回复队列的规则；SDK 中 Stop 对后台任务的影响。
5. 原始敏感事件保留期、加密密钥治理、审计读取权限、删除合规，以及 RPO/RTO，由相应数据所有方落实。
6. Run 展示排序、Tracking 状态集合等产品细节。

建议下一步先评审职责边界与恢复保证，再将字段落为可审查 DDL 与 SDK 兼容迁移计划。本文不是可执行 migration；本轮没有执行任何数据库变更、应用修改或测试。

### 依据

- 产品：product-requirements.md、interaction-rules.md、design-review-2026-10-17.md（含最新三图反馈与“用户关注时掌握回复主动权”原则）。
- 最新确认：Sentinel_fca0adb1ac888191835278e37f931b47、Sentinel_5297a0cb1f3c8191b3e0795320e73836（主动 Twin 消息、来源 Owner 触发语义、needs_response、全链默认 6 次自动接受上限及 Continue）；Sentinel_163f756d0bc0819183270b91e8d32935（token 可丢弃、完整 output 恢复）；Sentinel_8bdb5bcb16c08191869a24887683f37b（项目职责）；Sentinel_668b128c9bd481918ea969b8c14f4763（SQLite 本地、PostgreSQL 生产）。
- SDK：src/agent_runtime/storage/sql/001_sessions.sql、002_checkpoints.sql、003_session_identity.sql：七张表、text ID、checkpoint 位置与归属字段。
- SDK：src/agent_runtime/storage/postgres.py:57–68：append 事务与序号 CAS。
- SDK：src/agent_runtime/sessions/base.py:707–720：live 的 token/progress 非持久边界。
- SDK：src/agent_runtime/agent.py:176–188、428–438、542–543：输入队列与持久同步边界。
- SDK：src/agent_runtime/agent_loop.py:452–484：嵌套工具调用观察缺口。
- Runtime 独立只读审计：runtime-db-audit-v1.md，核对 commit 1873697c8c599bb1f7c4889f120b627babc0aea5；包含完整原 DDL、语义日志类型、恢复矩阵与 DatabaseSession 调用链。现有状态依据本次本地源码快照，不对将来版本作永久断言。
