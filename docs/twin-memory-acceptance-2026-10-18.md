# Soul、Git Memory 与 Dream 独立验收（2026-10-18）

本轮使用真实 PostgreSQL 随机 schema、真实 SDK 执行和本地 bare Git。模型全部为离线 fake provider；没有向官方模型发请求，没有替实际用户重试消息。本轮已按授权部署到现有本地双 API/Worker 栈，公网入口检查通过。

## 运行与提示词边界

- 隐藏的工具/权限协议从 `runtime_bridge._system` 提取到 `twin_prompts`；`platform_prompt(context)` 是默认空的服务端扩展，不经 owner API 返回。
- Soul 是 owner 的行为偏好，首次取得执行权时写入既有 execution snapshot；新请求读取最新值，同一请求恢复与对应子任务保留父快照。群可使用 Soul，不注入 owner 私有 Memory。
- Memory 始终标注为参考资料。普通私聊主 Agent 可使用 Memory；群及后台子任务没有私有 Memory 工具。
- Dream 使用原 private FIFO 和 `dream:<request-id>` 独立 SDK session，不占用自动对话 6 次额度，没有聊天工具、子任务工具、输出气泡或 token 投影。
- Dream 复用 SDK `run_memory_dream` 和 disposable Git draft。默认 180 秒（可配置，最大 900 秒）、最多 16 次模型调用，每次最多 4096 输出 tokens。单 run 正文证据最多 64 KiB，单条正文最多 48,000 字符，批次最多 128 KiB；每次模型请求前完整 context（含工具结果）还需小于 128 KiB 或模型窗口预留 8,192 后的更小字节上限。超限直接失败，不静默截断、不推进游标；大型单 run 需要后续人工处理/缩小任务，本轮没有引入自动压缩。取消会等待已启动的有界 Git I/O 结束，超时不是强制杀死正在提交的 Git 进程。

## 证据与恢复

Dream 只读取本批已授权、已完成、owner 私聊来源 run 中真实 `run_started` 的 user 正文和 `model_completed` 的 assistant text block；来源为真实 journal record ID。隐藏 reasoning、工具原始结果、原始 provider response 和 compaction summary 不作为经历注入。

`read_source` 支持当前批次真实文本来源，以及重新校验属于本 owner 的配置编辑 audit；不支持任意历史 run 回读。每次模型请求及工具执行前重验批次权限和人工编辑集合；排队后发生新人工修改会使旧 attempt 失败，游标不前移。同批人工修改过的 key 拒绝 Dream 写入/删除，模型可以跳过并发布其他 key；draft 期间同 key 并发修改由 Git 版本比较拒绝。

只有确认发布才能推进数据库游标。已提交工具返回提供 durable publication receipt；若发布回执缺失，则按精确 `Memory-Operation` trailer 查权威 Git 历史。查不到或读取失败保留 `recovery_required`，不重放旧 draft，也不自动新建可能重复发布的 attempt。业务请求失败后释放原 private lane，独立 SDK 会话留作审计；确认真实提交后可幂等推进游标。取消期间已发布也先确认，防止下一批盲目重复。

这里保证的是历史访问权限与工具边界；不能声称模型根据已授权正文推断出的每一条长期记忆均准确。

## 已执行

- `pytest -q tests/test_dream_runtime.py`：13 passed，7.29 秒。真实 SDK/PG/Git；覆盖发布幂等、丢回执已提交/未提交、同 key 并发 owner 修改、同批删除保护、超时、16-turn 上限、取消期间已提交/未提交，以及真实 assembly 的 Soul 快照/子任务继承、过大证据拒绝且无静默截断。
- 定向 Dream + follow-up + recovery：23 passed，14.87 秒（后续增加的取消/预算用例已在上项独立通过）。
- 最终 `pytest -q`：90 passed，50.91 秒。
- SDK `python -m unittest discover -s tests -p 'test_*.py'`：315 tests，37.579 秒，OK；1 项可选 S3 依赖跳过。未运行 `live_*`。
- 旧关键 checks：runtime 6、product 10、Twin conversation 12、message bridge 5、distributed 10 场景均通过；包含断进程恢复、双 API、取消与 FIFO、原因果链额度、子任务和空终态投影。
- `checks.personal_twin_acceptance`：56 项真实 HTTP/WS 断言。Soul 默认空、owner 隔离、CAS 409、清空；Memory Git commit SHA、真实 audit source、跨进程持久化、CAS 更新/删除；Soul 仅本人 WS 通知。API 进程、随机 schema 和文件均已清理。
- `checks.twin_followup_acceptance`：19 项真实 HTTP/WS/PG 断言通过并清理。
- 前端 `tsc`/Vite build、离线 UI 与真实 Chrome 均通过。真实浏览器无 mock：两 context 的 Soul/Memory stale-write 409、显式合并、旧版本删除 409、最终删除 404、跨 owner 隔离与伪造 owner 输入 422；390 px 无横向溢出。报告 `/tmp/twinteams-soul-memory-real-report.json` 的 errors、badResponses、chatWrites 均为空；桌面/移动截图同前缀 `-desktop.png` / `-mobile.png`。
- 本轮代码 lint 通过。实现与跨作者独立复核均未发现剩余部署阻断。

## 发布前只读预检

生产 queued/running requests 均 0，pending routing 0，新增 Dream state 0。API/Worker 使用同一 Memory 路径；当前根不存在，legacy JSON 文件和条目均 0（显式迁移仍须运行并记录）。Memory 路径已受 gitignore 保护；两份私有 `.env` 保持 600，Worker 项目代理仍为已修复的 127.0.0.1:7894。9 张原业务表的既有列 count/hash 仅保存于权限 600 临时快照，未记录明文内容。

## 已部署结果

父 Agent 在最终回归和空闲预检后明确授权发布；部署前再次确认 queued/running/pending routing 均为 0。依次执行：

1. `checks/local_stack.py stop`。
2. `checks/local_stack.py migrate`：业务/SDK schema 成功增量迁移。
3. 以同一 Worker environment 执行 `python -m app.migrate_git_memory`，全部 3 个实际 owner 各导入 0 条；没有创建演示 Memory。Memory root 显式设为 700，3 个 scope 均为 700，bare repo 与 migration marker 齐全，所有嵌套仓库均被应用仓库 gitignore 排除。
4. `checks/local_stack.py start`：Worker PID 52199，API 8000/8001 PID 52200/52201，全部正常。

9 张原业务表中 8 张既有列 count/hash 完全一致。`tt_chats` 仅既有 `seed_local` 对 `group-alex-maya` 的相同 title 赋值造成 `revision +1`；只读地将该单次 revision 增量从当前行扣除后，**整张 Chat 表的原列 hash 精确等于发布前快照**。没有回退或重写数据；其余 Chat 字段/行、用户资料、消息、已有请求、操作、额度与跟进记录都保留。临时私密 hash 快照验后已删除。

新增 `tt_memory_edit_audit` / `tt_memory_dream_state` 均 0 行。3 个 Twin Soul 默认空、version 0；Dream idle、due null，未回灌旧聊天或触发后台模型。新表/索引检查通过；独立随机 PG schema 还从旧列状态连续执行 standalone DDL 两次并通过原数据保留与严格 schema/index 检查。

8000 和 8001 均对三个账号完成只读 Soul/Memory/runtime API 200；跨 API 的已登录 WS 建连通过。Worker heartbeat 在线、configured=true，`openai / gpt-6-luna / openai-responses`，Memory `enabled=true,mode=git`。实际 Worker 进程代理仍为 `http://127.0.0.1:7894`；两份 `.env` 仍为 600，API/Worker Memory 路径一致。

[公网入口](https://154.217.234.218/) 在正常 TLS 验证、无代理、无 Basic Auth 凭据下首页、`/health`、实际静态资源均 200；未登录 Soul API 仍 401。没有修改 FRP、nginx、系统代理、模型或凭据。

部署后全新 Chrome 只读 6 页签（Soul、资料、运行、Skills、凭证、Memory）通过；桌面/390 px 无错误或溢出。Soul 和 Memory 前后读一致，`errors=[]`、`failures=[]`、`forbiddenWrites=[]`。报告 `/tmp/twinteams-soul-memory-live-report.json`；截图 `/tmp/twinteams-soul-memory-live-{soul,runtime,desktop,mobile}.png`。实际 Memory 为空，已有条目编辑 UI 由前面的隔离真实浏览器完成，生产没有为此造数据。

最终 queued/running、Dream state、人工 edit audit 仍均为 0。没有重试用户失败请求，没有新增生产聊天或调用真实 provider。所有本轮随机 schema、API fixture 和测试文件清理完成；报告与截图保留便于复核。未 commit/push，已有 dirty 保留。
