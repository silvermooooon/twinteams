# Twinteams Heartbeat 调研 v1

日期：2026-10-17。状态：调研与待确认建议，未实施、未运行测试，也未修改现有产品规则或数据库设计。

适用背景：React + FastAPI / Python 3.13；现有 Agent Runtime、DatabaseSession 与 PostgreSQL。本文引用公开的一手资料；“来源事实”不等于 Twinteams 已实现能力。“建议”均待确认。

## 1. 结论

建议分开建模三件事：

1. **真人 presence / 当前查看 / 已读**：决定一条新消息是否形成自动回复请求。其中连接存活、打开群、消息已展示也必须分开。
2. **Worker heartbeat / 执行 lease / fencing**：决定哪个 worker 有权续跑和提交某个执行，解决宕机恢复、重复执行与过期执行者写入。
3. **主动唤醒或定期巡查**：属于业务调度，不是连接保活或执行存活证明。若未来需要，应使用独立的持久 schedule / trigger，并遵守触发权限与去重规则。

对当前架构，优先研究“PostgreSQL 持久决策与执行状态 + 可重建 presence 投影”，借鉴 Temporal 的持久计时、checkpoint 和幂等语义；暂无必要仅为 heartbeat 引入 Temporal、Firebase 或 Kubernetes Lease 依赖。**10 秒是已给定的产品等待窗口，不是 heartbeat 周期或 lease TTL。**

## 2. 一手资料：成熟方案实际保证什么

### 2.1 多设备 presence：Firebase、Matrix

- Firebase 官方示例为每个设备/标签页分别记录 connection，全部 connection 消失才判 offline；断开处理预先注册在服务端，由关闭或超时触发，并使用服务端时间。应先登记断开处理，再宣布 online，以避免连接中途消失的竞态。[Firebase Realtime Database](https://firebase.google.com/docs/database/web/offline-capabilities#sample-presence)
- Firebase 的跨数据库 presence 方案特别指出：测得的是客户端到 Realtime Database 的连通性，不能保证到另一个数据库也连通。异步状态转发还需避免较旧事件覆盖新状态。[Firebase presence 限制](https://firebase.google.com/docs/firestore/solutions/presence#limitations)
- Matrix 把设备级信息聚合为用户 presence；读回执是独立的 read-up-to 标记。规范明确：仅收到消息不足以认定已读，应有消息已展示等依据。[Matrix presence](https://spec.matrix.org/latest/client-server-api/#presence)、[Matrix receipts](https://spec.matrix.org/latest/client-server-api/#receipts)

**推论：**登录凭证有效不等于当前在线；某个标签页关闭不等于用户离线；某条 socket 收到消息不等于 owner 看过；第三方服务连通也不能替代 Twinteams 自身连通。

### 2.2 浏览器：可见、聚焦、运行状态各不相同

- Page Visibility 与 focus/blur 描述不同状态；后台标签页的计时器会受到节流。[MDN Page Visibility](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API)
- Chrome 页面 frozen 时 JavaScript timers / fetch callbacks 等任务暂停；discarded 可能没有可供页面执行的退出回调。[Chrome Page Lifecycle](https://developer.chrome.com/docs/web-platform/page-lifecycle-api)

**推论：**不要依赖浏览器 setTimeout 精确执行 10 秒接管，也不要依靠 unload、最后一次“我离开了”消息或固定客户端心跳证明人还在看。visibility/focus 只能提供“正在查看”的产品代理信号，不能证明人的注意力。

### 2.3 Redis：TTL 可作投影，过期通知不能当可靠任务队列

Redis 官方明确：keyspace notification 使用 Pub/Sub，订阅方断线会丢事件；expired 在实际删除 key 时产生，不保证恰好在 TTL 归零时发出。[Redis keyspace notifications](https://redis.io/docs/latest/develop/pubsub/keyspace-notifications/)

**推论：**Redis 可选作 presence 缓存；不能只靠 expired 事件触发 Twinteams 的 10 秒接管或恢复工作。正确性应来自持久 due_at 与可重扫的待处理记录；通知最多用于降低唤醒延迟。

### 2.4 Worker lease：Kubernetes、Temporal

- Kubernetes 用 Lease / renewTime 表达节点心跳及 leader 协调；client-go 选主库另明确声明：不保证只有一个客户端仍在作为 leader 行动，即不提供 fencing。[Kubernetes Leases](https://kubernetes.io/docs/concepts/architecture/leases/)、[client-go leader election](https://pkg.go.dev/k8s.io/client-go/tools/leaderelection#hdr-Overview)
- Temporal Activity heartbeat 可携带进度；超过 heartbeat timeout 可按 retry policy 重试，并将已送达的 heartbeat details 交给后续 attempt。heartbeat 发送可能被 SDK 节流；它不是每次调用都已持久化的承诺。[Temporal failure detection](https://docs.temporal.io/encyclopedia/detecting-activity-failures#activity-heartbeat)
- Temporal 明确要求 Activity 考虑幂等：外部动作已成功、worker 来不及报告就崩溃，仍可能被重试。幂等键需要被调用服务实际执行去重。[Temporal Activity idempotency](https://docs.temporal.io/activity-definition#idempotency)
- Redis 分布式锁文档同样建议 fencing，并警告 TTL 使用非单调的墙上时钟，时钟跳变可能影响互斥。[Redis distributed locks](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/#disclaimer-about-consistency)

**推论：**“租约已过期”只说明允许考虑接管，不证明旧进程已停止；“有 heartbeat”只证明一次活性/进度报告，不保证任务有进展，更不保证工具副作用恰好一次。

## 3. 真人 presence 与自动接管：待确认建议

### 3.1 最小信号与聚合

- 每个连接实例独立持有 connection_id / generation，另关联 owner、device、认证会话；避免同设备多标签页互相覆盖。
- 服务端记录最近收到续租的时间与 expires_at；客户端时间只作诊断，不能决定在线有效期。
- viewing 单独包含 chat_id、visibility、可选 focus、view_epoch；重连重新建 generation。迟到的旧连接更新/close 不得覆盖新连接，也不得清掉另一个设备。
- 用户在线 = 至少一个有效连接；正在查看某群 = 至少一个有效连接报告符合产品定义的查看状态。认证已过期/撤销的连接不能续租。
- 消息已读 = owner 的持久 receipt。当前 v1 已建议逐消息 receipt，应保留；Matrix 的 read-up-to 只供比较，不能直接复制为 max_seen_seq，否则跳读会误标中间消息。打开群不能顺手把未加载或未展示的消息都标为已读。
- “打开群即抑制候选”可单独记录持久 view/suppression 事实；这样既符合现有产品规则，也不伪造已读回执。瞬时查看证据若会影响稍后完成的路由判断，必须保留。

### 3.2 10 秒裁决保持现有产品边界

对每条新群消息、每个识别到的目标 Twin 独立判断：

- owner 已确定离线：立即接受自动请求。
- owner 在线但未查看：建立 waiting candidate，等待 10 秒。
- owner 正在查看：抑制本条自动触发。
- waiting 期间 owner 打开该群：持久标记 suppressed；随后离开也不重启该消息候选。
- owner 以前已读的消息：不会因为离开、断线或重连再次成为新候选。
- candidate 一旦 accepted，产生 queued/running 工作后，就不再因 owner 在线、查看、已读等变化自动取消。

**计时建议，尚未定案：**t0 采用已持久化消息的服务端接收时间，而非客户端时钟或 LLM 路由完成时间。due_at = t0 + 10 秒。路由较慢时，需要结合消息到达以来保存的打开/阅读证据裁决，不能只看路由结束那一刻的 presence。

**竞态建议，尚未定案：**打开/抑制与到期接受使用同一候选版本及事务串行化；明确唯一的接受提交边界。客户端 9.9 秒打开但请求 10.1 秒才到服务端时，无法同时无条件满足“准点接受”与“按客户端实际打开时间撤销”。建议以服务端已接纳证据为准，向 UI 返回确认；若产品要网络宽限，需明确宽限值及迟到事件规则，不偷偷增加。

### 3.3 故障语义要显式说明

- “离线立即”应指**服务端判定离线后的立即处理**。突发断网到服务端察觉之间必然有检测窗口，heartbeat 不能消除该窗口。
- 正常关闭可加速结束该连接，异常失联依靠服务端超时；不能据一次客户端 offline 事件清空全用户。
- presence 存储不可用或缓存全丢是 unknown，不等同于“已证实所有设备离线”。建议 unknown 时暂停新增自动接受并恢复证据，避免基础设施故障导致群内批量自动回复；此降级策略需产品确认。
- 仅 presence 抖动不应扫描历史未读并补跑；新的消息事件是候选创建入口。

## 4. Runtime lease 与恢复：待确认建议

沿用已有设计方向：业务 reply lane 与 Runtime session 的执行所有权各有明确边界；worker 进程健康指标另存，不能代替每次执行的 lease。

1. **持久接受：**candidate 接受、reply request 与 dispatch outbox 在同一业务事务内确定，避免接受成功却丢任务。重复调度以稳定 request/command ID 去重。
2. **领取与续租：**以 session/lane 的权威记录进行原子领取；每次新持有者递增 fence。续租检查 holder、fence、有效期，旧持有者不能仅凭迟到 heartbeat 复活过期 ownership。
3. **提交隔离：**journal append、状态提交、消息发布意图、工具派发资格都验证有效 lease/fence。仅在入口做一次检查不足以防止长运行期间失去所有权。
4. **恢复：**过期后领取新 attempt，读取既有 journal、inbox/outbox、审批与工具操作账本，再按现有 Runtime resume 语义继续原逻辑 run；不是无条件重启整个 prompt。
5. **未知副作用：**工具执行成功但结果落库前宕机，标记 unknown/reconciling。可安全幂等重试的沿用同一个业务 operation key；可查询的先对账；不可确认且不可幂等的暂停，不直接重跑。
6. **限制：**本地 fence 无法撤销已送往第三方的 HTTP 请求，也无法让不支持 fence/idempotency 的供应商自动拒绝旧调用。外部效果需要供应商去重/查询能力与操作账本共同保障。
7. **健康与进度：**记录 lease renewal 与 progress/checkpoint 的不同时间。后台线程持续续租但主工作卡死，应由独立进度/总时长策略发现，不能永久视为正常。
8. **Owner 活动隔离：**人重新上线不是 runtime failure，也不是 cancellation。只有已经定义的明确停止/取消命令改变已接受工作的执行状态。

PostgreSQL 的 SKIP LOCKED 适合多个消费者领取队列表记录，但官方也指出其视图不适合一般一致性读取；它是领取手段，不提供外部副作用恰好一次。[PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE)

## 5. 周期与存储选择：不把示例数值冒充行业标准

本次资料没有给出适用于 Twinteams 的统一 heartbeat 周期。应分别确定：

- **Presence H / TTL P：**以可以接受的离线检测延迟、网络抖动、后台冻结行为及连接数为约束。P 应覆盖允许的漏报与延迟；若为了容忍后台冻结把 P 无限制拉长，就会留下“假在线”。更好的做法是定义后台状态及服务端连接检测，而非假设浏览器定时器准时运行。
- **Worker H / TTL L：**L 应覆盖续租间隔、可容忍网络/数据库停顿及调度暂停，同时符合故障接管目标；过短造成无谓接管，过长延迟恢复。它与 owner 的 10 秒等待完全无关。
- **调度扫描：**持久 due_at 是权威。轮询间隔及队列拥塞决定额外迟延，因此产品的“10 秒”还需说明是最早可接受时间，还是有延迟服务目标。
- **时钟：**持久 deadline / lease 统一由服务端数据库时钟判断，进程本地间隔用单调时钟。数据库墙上时钟也可能调整，应监测异常；不能声称改用服务端时间就消除了时钟风险。PostgreSQL now()/CURRENT_TIMESTAMP 固定为事务开始时间，长事务续租应避免误用；clock_timestamp() 表示调用时当前时间。[PostgreSQL 时间函数](https://www.postgresql.org/docs/current/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT)

**推荐取舍：**当前先用 PostgreSQL 持久化 candidate、due_at、decision、执行 lease/fence 与恢复账本；presence 可先落 PostgreSQL TTL 投影，实际写入量有依据后再考虑 Redis。缓存读取可以加速，但接受决定应保存所用证据和版本，不能被异步旧事件覆盖。Temporal 值得借鉴或未来评估，但现有 DatabaseSession 持久日志不能直接等同于完整持久工作流平台。Temporal 的 timer 可跨停机恢复，反映的是必须保留的语义，而非当前项目已经拥有的保证。[Temporal durable timers](https://docs.temporal.io/develop/typescript/workflows/timers)

## 6. 对现有 v1 的最小结构建议（未定案）

不新增一套通用 heartbeat 历史库，优先复用已有逻辑实体：

- presence_leases：补清 connection_id / generation 和连接级唯一键；保留 device 作为关联信息。认证、短期在线、当前群查看仍是不同语义。
- human_read_receipts、view_events、trigger_candidates：分别保存逐消息已读、影响裁决的打开/查看证据、due_at 与最终接受/抑制决定；正常续租不逐次写 Agent journal。
- reply_requests、reply_lanes、agent_session_leases：继续承载接受后的排队、业务互斥与 Runtime 所有权；不在多个表重复定义互相矛盾的执行租约。
- 已有规划的 command/inbox/outbox 和 agent_tool_operations：复用稳定 ID、fence、checkpoint 引用及未知副作用状态。heartbeat 不另造第二套任务状态机。

是否把 presence 放入 PostgreSQL 或 Redis、具体索引和 TTL 均留到容量与产品语义确认后。本文未新增 DDL，也未把上述方向改写进原数据库设计。

## 7. 设计前需要确认的最小问题

1. online 是“有有效 Twinteams 连接”，还是“至少一个前台活跃设备”？建议前者；后台不等于 viewing。
2. viewing 是否要求 focus，还是“目标群打开且页面可见”即可？分屏失焦仍能读，focus 更严格但会漏判。
3. 10 秒起点及边界以服务端接纳事件为准吗？对迟到打开事件是否有网络宽限？
4. presence unknown 时是否允许暂缓自动接受？离线检测与 worker 恢复各自可接受的延迟是多少？

这些问题没有统一的第三方标准答案；确认后才能选择具体周期、TTL、字段和故障策略。本报告未将它们写成已确认需求。
