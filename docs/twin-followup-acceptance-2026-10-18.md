# Twin 主动代发与事件跟进独立验收

日期：2026-10-18。最终结论：通过独立验收，已迁移并启动现有本地服务，公网入口可用。68 个后端测试使用真实 PostgreSQL 和 offline/mock LLM，没有验证或调用真实供应商模型。

## 用户批准的行为

Twin 可按用户要求向其已有、获授权的其他聊天主动代发，不再因 originating chat 为 private 一律 403。公开的是实际发送正文，原私聊历史和记忆仍不对收件人公开。

对发出的提问可建立一次最长 30 分钟的跟进。明确回复与超时只能产生一个终态，并以事件通知原主 Twin；原主 Twin 可静默结束、告知 owner 或继续工作。跟进不占用运行中的模型、常驻子任务或等待循环；由外层事件调度和现有 FIFO 执行。重启保留原 deadline，自动动作仍共用原始因果链的 6 次额度。

调度边界经集成负责人确认：复用既有 60 秒 compensation 中按 status/due_at 索引、limit 批次读取已到期或待通知记录，作为 Redis 丢提示和恢复兜底；不新增逐分钟扫描聊天消息/判断回复的任务，不逐个读取未到期等待项的聊天历史。明确回复在消息提交事务中结算，正常到期使用原 deadline 唤醒。

## 现有实现中须覆盖的风险

1. 原跨群消息继承完整输入 provenance。只删除 private 403 会使目标成员无法读取消息，或迫使开放原私聊。验收须直接用另一账号读取目标消息，并确认其仍不能读取私聊和原私聊消息/记忆。
2. 原 `accept_request` 和 legacy `request_chain` 只对若干固定 source 预留额度。新 follow-up continuation 必须显式继承原 chain 并走相同额度入口；retry、child result 和恢复不能建立新 chain 或新 allowance。
3. 原 router 在候选动作计费前会调用模型。耗尽额度必须在进入 router 前检查；只拒绝后续 candidate 不满足零模型消耗。
4. 普通新 human 消息按既有语义建立新 chain。被跟进捕获的明确 reply 必须通知原 chain，不能借新的回复链重置跟进额度。
5. 原 SDK observation 可投影 assistant 的工具前文字。静默跟进必须检查实际 Message 表与 WS/HTTP 内容，不能只看最终 response body 为空。
6. 既有 ChangeOutbox 是补偿通知来源，SDK command/journal 是执行来源。必须验证丢失 Redis hint 后可恢复，而且恢复只读原 deadline，重复事件不重复入队。

## 独立测试矩阵

| 场景 | 可观察的通过标准 |
| --- | --- |
| BBB 私聊要求向 AAA 打招呼 | `send_chat_message` 成功；AAA 能在目标群看到 Twin 正文；其他账号不能读取 BBB 私聊、私有源消息和记忆 |
| 未授权目标、失去成员资格 | 发送拒绝且无目标消息、无成功 ProductOperation |
| 发送幂等 | 同一 operation 重放只得到同一 message/follow-up，不重设 deadline，不重复扣费 |
| 明确回复 | 只有引用确切 outbound 的有效对方回复才结束跟进；普通群消息、发件 owner 自己消息、回复另一问题不结束 |
| 回复与超时并发 | 两个独立 PG 事务竞争后仅一个终态、一个 continuation/dispatch；另一事件幂等忽略 |
| 超时 | 使用已持久化的 deadline；fake clock 到期仅产生一次通知；不等待真实 30 分钟 |
| Busy 与 FIFO | 主 Twin 已在运行时事件仍持久入队、后续请求可接受；不占用等待 Worker 或子任务 |
| 恢复 | 新 service/Worker 读取同一 pending deadline；丢失 hint 后已有 durable outbox/队列补偿；重复恢复不重复 continuation |
| 预算 | origin→send→对方自动回复→follow-up→child 全在原链；spent 不超过 allowance，默认 6；耗尽时 router fake-provider 调用数为 0 |
| 额度耗尽的终态 | 跟进仍可结束并保留事件；不会靠新链、重启或新子任务偷增额度；是否 pause/需手工继续按既有业务表示 |
| 取消、撤回、权限变化 | 无已取消工作续跑；被撤回发送不继续等回复；来源或目标权限被撤销时不向错误人群发布内容 |
| 静默结算 | fake provider 可无可见文字完成 follow-up；数据库无空白、占位或工具前垃圾气泡，WS 不出现伪完成消息 |
| 正常告知/继续 | 同一来源事实可让 Twin 发布正常回复或执行下一工具；不回放前一轮已完成操作 |
| 重复 SDK/业务投影 | 派发、模型输出投影和 follow-up 结算的重试均有稳定键，最终可见副作用一次 |

## 测试隔离与交付纪律

- 独立新增 `backend/tests/test_twin_followup_acceptance.py`；不与实施 Agent 的 tests/service/models/runtime 文件并发编辑。
- 使用 `checks.postgres.TestDatabase` 的随机 schema、临时 workspace/memory/secrets；只通过 `checks.local_stack.read_env` 内部读取本地数据库连接给测试子进程，不输出配置秘密。
- 确认 fake-provider 已注册，禁止真实模型 HTTP；短命 shell 如确需使用，只采用伪造凭证。
- 先跑功能定向用例，再做完整后端回归；SDK 无改动不重复其既有全量测试。
- 仅在实施作者交接且集成负责人允许后迁移/重启真实本地栈；迁移前后只读 hash/count 检查，生产不创建新聊天或模型请求。
- 真实 HTTP/WS 验收使用额外随机 schema 和临时服务，生产最后仅只读健康/页面检查。不改 FRP/nginx，不提交或推送。

## 实际结果

独立新增 `backend/tests/test_twin_followup_acceptance.py` 和 `backend/checks/twin_followup_acceptance.py`，未改实施 Agent 的主体源码。旧 `checks/twin_conversation_acceptance.py` 按新行为更新：private owner-voice 发送可见；预算耗尽暂停 RoutingDecision，Continue 后仍验证原有 view/read/reply 抑制，不再假设耗尽后已经付费路由并产生 Candidate。

### 独立覆盖

- private 发往已加入群的 Twin 正文对另一个账号可见，原 private chat 仍拒绝该账号；群消息不挂载 private source IDs/provenance；重放保持同一 message、follow-up 和绝对 deadline。
- 未引用、引用其他问题、同一 owner 自己的回复不结算；明确对方 reply 进入原私聊 FIFO、复用原 chain，重复处理不重复派发。
- 三个真实 PG 事务并发竞争 reply/timeout 后仅一个终态、一个续接、一个 dispatch。
- 新建 service 恢复同一 deadline，Busy 私聊的跟进排在原有请求之后，不启动等待任务；已有 ChangeOutbox 留有恢复提示。
- 耗尽跟进保留 timed_out/paused，不新增 chain/额度；显式 Continue 才对原链 +6，续接只消费一次。
- 取消 origin、目标发送记录被撤回、目标访问权撤销、私有源撤回，均不派发新通知。取消不伪装为撤回已提交的外发正文。
- 耗尽链在 router 前停止，provider 调用数为零；唯一候选 owner 已被 follow-up 消费时也不重复调用 router。
- 普通群任务中间模型文字不结束跟进，只有完成后的 final 消息结算。
- 连续 generation 1/2 跟进重试保留 followup source、payload、原 chain/reservation，目标权限撤销后重新取上下文会拒绝。真实 SDK 配 offline provider 执行第二次重试，工具前文字不投影、空 final 无气泡/实时 token，原链 spent 仍为 2。

### 发现与修复复验

独立验收实际复现并阻止发布的三项问题：

1. 已耗尽链仍调用 router，断言实际调用数从 1 修正为 0。
2. `publish_runtime_output` 的模型中间叙述提前把跟进标为 replied；修复后等普通 completed 的最终投影才结算，human 和显式工具发送仍立即结算。
3. follow-up 失败重试变为普通 `retry` source，丢失通知权限重检和静默规则；修复保留 `followup_result`，由 generation 表达重试，没有新增语义类型框架。连续两代权限与真实 SDK 静默回归通过。

### 已完成命令

| 命令/范围 | 结果 |
| --- | --- |
| 最终 `python -m pytest -q` | **68 passed，31.32 秒**；含最新重试来源/manifest 继承修复和运行中撤权测试 |
| 独立 follow-up 验收测试 | 15 项最终全部纳入全量通过；修复重试前 12 项曾单独通过，5.78 秒；后补重试权限与 SDK 静默 3 项定向通过，2.23 秒 |
| `python -m checks.twin_followup_acceptance` | 19 项真实 HTTP/WS/PG 断言通过，双 API、无 Worker、`agent_model_usage=0` |
| `checks/runtime_acceptance.py` | 6 个既有 runtime/子任务恢复场景通过 |
| `checks/product_acceptance.py` | 10 个既有产品、权限、队列和取消场景通过 |
| `checks/twin_conversation_acceptance.py` | 12 个因果额度、Continue、成员注意力、旧结构迁移和并发场景通过 |
| `checks/twin_message_bridge_acceptance.py` | 5 个场景通过；覆盖普通 final 选择、模型路由、实际 SDK 中间投影/空 final 与重放，旧工具替身只采用显式测试契约 |
| `checks/distributed_acceptance.py` | 10 条 PASS；真实 PG/Redis、双 API、独立 offline Worker、100 WS 空闲 SQL 测量、SIGKILL 后 outbox 恢复及 Worker Redis 断连补偿通过 |
| `ruff check` 新模块/测试/网络检查器与 `git diff --check` | 通过 |

所有测试数据库使用随机 schema。网络检查用真实 HTTP 向目标群提交明确 reply，由 API 2 的事务结算，API 1 的 BBB private WS 收到 revision invalidation；重新读取看到同一 queued followup_result，重复 HTTP key 只保留一个回复和一个 dispatch。测试进程、文件和 schema 均清理。

### 现有服务部署与只读验收

所有实施作者完成、第二独立 review 无新增阻断、最终回归通过后，按授权更新现有本地栈。部署前再次只读确认 `queued/running ReplyRequest=0`、`pending RoutingDecision=0`，没有中断正在运行的用户任务。

执行现有 `backend/checks/local_stack.py stop` → `migrate` → `start`，三个命令成功。启动后 Worker、8000 API、8001 API 均运行。新 `tt_twin_followups` 表为空，没有为既有 BBB 请求创建跟进，也没有替用户重发消息；status/due_at、notification_state/chat_id 索引及 sent_message_id 唯一索引均存在。

迁移前后的 Actor、Message、ReplyRequest、CausalChain、AutomaticReservation、ProductOperation 原有列 hash/count 一致，Chat 数量一致；只保存过列名/count/hash 的权限 600 临时快照，验后删除。未修改用户资料、消息、凭证或因果链额度，未提交或推送。

更新后 19 项生产只读 HTTP/WS 检查通过：双 API 健康、个人页和既有 BBB 私聊/目标群可读，WS 可连接；实际 Worker 仍报告 `gpt-6-luna` / `openai` / `openai-responses`，online/configured 为真。登录只用于读取，随后 logout，没有发送聊天或执行真实模型请求。

公网 **https://154.217.234.218/** 在正常 TLS 验证、禁用环境代理、无网关凭据的情况下，首页、`/health` 和首页静态资源均 200，无 Basic Auth challenge；未登录应用个人 API 仍 401。未修改 FRP、nginx、证书或系统设置。

最终相关模块/测试/检查器 `ruff check` 与 `git diff --check` 均通过。隔离网络验收的 API 子进程、schema、临时文件均已清理，保留现有本地栈运行。

## 权限与模型边界

“原私聊历史不公开”指未扩大原消息、原历史和记忆记录的读取权限。代发正文由模型生成并公开，模型可能把私有内容写进正文；此项依赖用户意图和 prompt 约束，不宣称内容层面绝不泄漏。验收使用真实 PostgreSQL 和 offline/mock provider，不代表真实供应商模型已通过本轮测试。
