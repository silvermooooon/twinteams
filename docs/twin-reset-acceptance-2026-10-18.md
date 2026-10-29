# 私聊重置、即时 Dream 与 SOUL.md 独立验收（2026-10-18）

> Historical verification record. This describes the tested revision on its stated date, not the current peer-message contract. Current guidance: [interaction rules](interaction-rules.md) and [architecture](architecture.md).

本轮所有写入验收使用随机 PostgreSQL schema、临时 Git/工作区和离线 fake provider。SDK、PG 事务、Git 发布与 HTTP/WS 均真实；没有调用官方模型；生产仅执行获授权的迁移与只读验收，未调用真实用户重置、手动 Dream 或改名。

## 实现边界

- “重置”只清空自己的私聊产品内容和 Memory。保留姓名、头像、SOUL.md、Skills、凭证与整个工作区文件；群消息、群任务和其他 owner 保留。
- 私聊持久 `reset_generation` 与请求 `private_generation` 相符才可执行。新私聊 generation 使用独立 SDK session；generation 0 保留原 session ID 兼容。旧 SDK 审计可保留，但产品旧 run detail 被拒绝，新上下文不读取旧 session、摘要或历史。
- 主任务/子任务在 factory、模型请求、工具调用、token/Activity 投影前检查代次；产品提交处再核对。Memory 按 generation 隔离，旧 Git/legacy JSON 物理清理；真正 Git 发布和 epoch 切换复用同一 private Chat 行事务，防止晚 push 重建旧记忆。不新增锁框架。
- 重置先持久 fence，再清理文件。失败保留 pending，原幂等 key 可恢复；重放已完成 key 不会再次清除后来新消息/新 Memory。后台子任务结果仅在确认为同一 owner、精确 parent session/run 且旧 epoch 时终结 SDK 投递；未知父或错 owner 不 ack，不新建私聊消息。
- 群任务不应用私聊 fence；Activity generation `None` 归 0，保持既有完成路径。合法私有 shell 已启动的工作通过现 abort 终止，但仍属可信本机 demo，不宣称能撤销所有晚文件副作用。
- 手动 Dream 立即进入同一 private FIFO；已有排队任务去重，未知发布待核实期间拒绝新 Dream。无新经历时可整理现有 Memory，使用真实 owner audit 与原 Memory provenance；完全为空则不调用模型。首次入队不能让 cursor 跨过尚未完成的人类任务。
- SOUL.md 是唯一人格内容来源，版本是内容 SHA-256。默认 Muse 正文按 Twin 名生成；改名仅更新托管标题，保留自定义及缺 marker 正文。删除后 GET 不重建。新 run 读取最新文件，父/子及恢复保留既有冻结 profile；协议要求自主修改 Soul 后告知 owner。旧数据库 Soul 经显式迁移进入文件后删除旧列。
- Worker 配置探测为纯配置布尔，不再为 heartbeat 创建 synthetic owner store。

## 已执行检查

- 最终 `pytest -q`：**111 passed，77.57 秒，无 warning**。
- 独立 `tests/test_reset_runtime.py` + `tests/test_twin_reset_acceptance.py`：共 11 项已包含于最终 suite。覆盖晚模型文本/工具被拒绝、下一 generation 无旧 journal、精确新 session、配置探测无 fake owner；真实 group SDK run 在 owner 私聊重置后照常完成，Activity 为 0；群 SQL hash、其他 owner Memory、资料/Soul/Skills/凭证/工作文件保留；重放旧 reset key 保留之后新消息与 Memory；旧 Dream draft 无法晚发布或复活旧 repo；未完成 human 经历不被手动 Dream cursor 跳过。
- 子任务使用真实 SDK 控制记录：完成 child 通知和未启动 child → 重置 → drain/recover 各两次；旧通知 ack，旧未启动命令 rejected，模型调用数不增加，无新聊天。错 owner 不 ack，历史读取设为直接失败以证明退休投递不加载输出正文。
- 真实 SDK `write` 工具编辑 SOUL.md，下一新 run 的 profile 读取新文件，旧 run profile 保留；不是以 API/Path.write_text 冒充 Agent 编辑。
- 无新增经历、仅已有 Memory 的手动 Dream：真实 SDK read/write/publish 产生新 Git commit，真实 manual audit 与原 provenance 均保留，没有私聊气泡；排队去重和 unknown 拒绝另有主体回归。
- 旧关键 checks 全通过：runtime 6、product 10、conversation 12、message bridge 5、distributed 10。distributed 包括独立 Worker 启动、双 API、断进程/outbox、Redis 提示丢失、取消和无 worker 接单；无真实 provider。
- 真实双 API/HTTP/WS：重置 **25** 项、个人页 **56** 项、follow-up **19** 项通过。包含 401、跨 owner 私聊 403、伪造 owner body 422、owner-only WS、代次 CAS、旧 key 重放保新数据和群 SQL hash；各 fixture/schema/进程已清理。
- standalone reset DDL 从旧列状态连续执行两次，原 actor 数据保留，严格 schema/index 检查通过，随机 schema 已清理。
- 真实 Chrome 无 mock：Reset generation 2→3，私聊/Memory 清空，群消息与配置精确不变；旧 key 重放保留新 Memory；Soul hash 409 显式合并、默认/托管改名保自定义、删除后两次读取 null 不重建；Dream 真实入队且不启动 Worker。桌面与 390 px 无溢出，`errors/5xx/chatWrites=[]`。报告 `/tmp/twinteams-reset-real-report.json`，截图 `/tmp/twinteams-reset-real-{desktop,mobile}.png`。前端 typecheck/build 与新旧离线 UI checks 通过。

此前网络 check 的“另一 owner Soul 必须为空”断言已更新为新 Muse 默认与 owner 内容隔离；失败来自旧测试期望，并非数据越权。新增 direct-action NULL ID warning 已整理并在最终 suite 消除。

## 发布前只读预检

发布前生产 queued/running/pending routing/Dream state/active follow-up 均为 0。三个 owner 旧 DB Soul 均空，当时无 SOUL.md，迁移预期生成三个默认文件。9 张业务表既有列 hash/count、工作区已有 3 文件 hash、Memory 66 文件 hash 曾保存在权限 600 临时快照（验后已删除）；旧 Soul 两列有意迁移并单独保存目标文件预期 SHA，不误算为数据丢失。

API/Worker 的 Memory 与工作区路径一致；两份 `.env` 为 600，代理仍为 127.0.0.1:7894。模型保持 gpt-6-luna。父 Agent 在最终回归和预检后明确授权本轮部署，发布结果如下。


## 已部署结果

再次空闲预检后依次执行 `checks/local_stack.py stop` → 以原 API environment 执行规范 `python -m app.init_db` → `checks/local_stack.py start`。业务/SDK schema 迁移成功，Soul 先写入并落稳三个默认 SOUL.md 与 marker，随后移除旧数据库 Soul 两列。未运行重置 API、手动 Dream 或 Memory 迁移/清理。

Worker PID 61726，API 8000/8001 PID 61727/61728。三个新 SOUL.md 的内容 SHA 与发布前计算的预期完全一致，文件及 marker 均为 600。原工作区 3 个文件和 Memory 66 个文件的每个 SHA 均完全不变。

9 张业务表既有列 count/hash 均保留；Chat 部署时的既有 seed_local 对 group-alex-maya 相同 title 赋值造成 revision +1；验后 private-maya 另有 revision +3，均有对应 outbox 记录。按这些实际 outbox 增量精确扣除 revision 后，整张 Chat 的全部原列 hash 与发布前精确相等；没有回滚或改写这些正常元数据。Actor 仅有意删除已迁到文件的 Soul 两列，其他原字段/资料未改。新 reset 索引存在；所有 Chat generation=0/not pending，TwinReset 和 MemoryDreamState 均 0 行。

两个 API 对三个账号的 reset status、Soul、Memory status、runtime、Memory list 均为 200。重置为 ready/generation 0，Soul API SHA 等于实际文件预期，Memory 为 Git、列表空、Dream idle/due null；Worker 在线 configured=true，openai/gpt-6-luna/openai-responses。实际 Worker 进程仍使用 127.0.0.1:7894 代理，原 `.env` 权限 600。

[公网入口](https://154.217.234.218/) 正常 TLS 校验、无全局代理、无 Basic Auth 凭据下：首页、health、实际静态资源均 200；匿名 reset status 仍为 401。没有更改 FRP、nginx 或系统代理。

部署后全新中文 Chrome 只读验收通过：红色重置 → 确认弹窗 → 取消，generation 始终 0；Dream 按钮只看不点，维持 idle；默认 SOUL.md 含当前 Twin 名且 SHA 匹配。六页签、桌面和 390 px 均通过，前后 Actor/Soul/Memory/reset 相同，无 JS/API 错误。HTTP 只有 GET 和必要登录/退出；WS 仅 3 条正常 presence，没有 send 或其他命令。报告 `/tmp/twinteams-reset-live-report.json`，截图 `/tmp/twinteams-reset-live-{desktop,mobile,soul,memory}.png`。

浏览器完成后再次复核：9 表原数据（扣除已解释的精确 Chat revision/outbox 增量）、Memory 66 文件、原工作区 3 文件与三个新 Soul 预期 SHA 均匹配；TwinReset/MemoryDreamState/MemoryEditAudit 仍全部 0 行。临时私密快照/helper 已删除。没有提交或推送代码，保留原有 dirty。
