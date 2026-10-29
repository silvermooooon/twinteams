# 聊天命名与替我回复验收（2026-10-18）

> Historical verification record. This describes the tested revision on its stated date, not the current peer-message contract. Current guidance: [interaction rules](interaction-rules.md) and [architecture](architecture.md).

已部署并通过独立验收。命名规则首轮无需迁移；追加一对一去重仅应用 pair 专用迁移，未运行 `init_db`，业务 `kind` 不变。

## 行为边界

- 自己与 Twin 的 private 聊天隐藏“替我回复”，旧设置 `enabled=true` 也不影响输入提示、模式样式或发送参数：前端发送 `reply_for_me:false`。本轮不改后端既有设置/发送契约，不迁移旧开关。
- 两个 owner 的聊天仍为 `group`，各查看者看到对方真人姓名，无改名入口；后端拒绝改名。创建此类聊天时忽略自定义标题。
- 三个及以上 owner 的群支持成员改名；去掉首尾空白后须为 1–256 字符。非成员/已退出成员拒绝，修改持久化并通过既有 changed 通知其他端。群聊“替我回复”保留。

## 验证结果

- 后端实施者最终全量：**113 passed / 75.53s**。独立执行 naming/recovery/health：**13 passed / 10.79s**，真实隔离 PG，无真实模型。
- `backend/checks/chat_naming_acceptance.py`：**36** 项真实隔离 PG、双 API、WS 断言通过。包括两查看者的 bootstrap/detail/summary、成员鉴权、空/256/257 边界、trim、跨 API 通知与持久化、两人创建忽略标题、加入第三人后可改、退出后拒绝、群聊 reply_for_me 保留。无 Worker，SDK model usage 为 0；临时 schema/文件清理。
- `frontend/checks/chat-naming-acceptance.cjs`：真实 Chrome、合成 API/WS，AAA/BBB 两视角通过。旧 true 私聊发送 false、普通 placeholder、开关隐藏；两人标题/无铅笔；群 true、多人改名错误保草稿、trim、保存与远端 changed 更新 header/列表、256 输入限制。无 JS 错误。
- `frontend/checks/workspace-focus-acceptance.cjs` 群 fixture 修正为三个真实 owner；私/群 × 空/非空四场景再次通过，无加载闪烁、焦点/DOM/展开/滚动/预览回归。报告 `/tmp/twinteams-workspace-focus-report.json`。
- 前端实施者 `chat-creation-ui.cjs` 通过（1→2→1 收件人时群名显隐、单人提交无标题、多人 trim、头像/群筛选）；`typecheck` / `build` 通过，最终构建冻结为 `index-BjCg8P3T.js`。

可运行独立检查：

```sh
cd backend
.venv/bin/python -m checks.chat_naming_acceptance
```

Reproduction requires Node.js >=22.12 on `PATH` and Playwright with Chrome; replace `<playwright-node-modules>` with the directory containing the installed Playwright package.

```sh
NODE_PATH="<playwright-node-modules>" \
node \
frontend/checks/chat-naming-acceptance.cjs
```

## 部署和真实只读烟测

最后预检 queued/running 请求 0、Dream pending 指针 0；使用现有 `checks/local_stack.py stop`、`start --port 8000 --second-port 8001` 更新 API/Worker，不初始化数据库。Worker PID 65128，双 API PID 65129/65130。既有 9 张业务表部署前后 count 与完整行 hash 全部一致。真实只读浏览器访问后，8 张表仍原始 hash 一致；Chat 仅既有 presence 通知引起 revision 增加，按同期 change outbox 次数规范化 revision 后完整行 hash 一致，标题及其他内容未改。真实代理仍为 7894，模型仍 `gpt-6-luna`。

真实 Chrome 新 context 分别登录 DEV-AAA/DEV-BBB，只读确认 private 开关隐藏、两人标题为对方姓名且无改名入口、多人群铅笔可见、Worker 在线。随后退出。未实际改名或发送消息；HTTP 仅 GET 和登录/退出通过，自动 read-receipt 请求在网络前拦截，WS 仅 presence。报告 `/tmp/twinteams-chat-naming-live-report.json`，无 JS 错误。未调用真实 provider。

8000、8001 和公网入口的首页、health、JS 均 200；公网正常 TLS、无 Basic Auth。三处首页及 JS 与 dist 逐字节相同：

- 首页 SHA-256：`e8a653f6bbfccaacae04c3b1beb0c07d16f8bb4d464bdceb45fd0c3dfa86bcc6`。
- JS SHA-256：`e622e00dea4fea58e8d2c737352d1083c5c6dac081c3de9a83ec56093af09f8c`。

报告 `/tmp/twinteams-chat-naming-report.json`、`/tmp/twinteams-chat-naming-deploy-report.json`；合成 Chrome 截图 `/tmp/twinteams-chat-naming-{aaa,bbb}.png`。部署校验只保存 hash/count 的临时快照与临时 runner 已删除；未提交或推送。

## 追加：一对一聊天去重已部署

- 使用 nullable `Chat.direct_pair_key` 与 PG 唯一索引；同向、反向及并发创建复用同一会话，不改变既有 history grant、lower_seq 或个人设置。两人会话加入第三人时释放 pair key，多人群保留，再次一对一开聊得到独立两人会话。无新锁框架，通用迁移发现重复会明确拒绝，不自动合并历史。
- 后端最终 **117 passed / 81.74s**；最终通知微调后 direct+naming **6 passed / 3.60s**。独立双 API/真实 PG/WS 检查增至 **48 项全过**，含全新 BBB↔CCC 的 12 次双向并发、升级与创建竞争、历史 `no_prior/lower_seq=7` 和自定义设置保留。Chrome 重开同一人复用已有列表项通过；本轮前端构建未变。
- 最后 idle 为 0 后停止现栈，在同一事务内重新检查指定重复空壳所有 Chat FK、当前两 owner 和默认设置，仅删除该空壳及 2 membership、1 默认 settings、11 通知 outbox、5 查看区间；没有消息、请求、文件或自定义设置。随后同事务应用专用 pair DDL/backfill，未调用 `init_db` 或 seed。
- canonical 会话在等待期间自然新增到 **12 条消息、7 个请求**，全部保留。**38 张 tt 表**的旧列完整行 hash/count 在排除已批准空壳元数据后，事务内及提交后完全一致。
- 最终 Worker **67396**、API **67397/67398** 健康。真实 AAA/BBB 只读 bootstrap 各只有同一 canonical pair，标题均为对方真人名，空壳不再返回；唯一键与唯一索引有效，Worker 在线、模型 `gpt-6-luna`、代理 7894，公网正常 TLS health 200。报告 `/tmp/twinteams-direct-pair-live-report.json`。没有在生产调用创建聊天、改名或发送消息；未调用真实模型。
- 一次性清理 runner、只含 hash/count 的临时快照已删除；生产 ID 未加入常驻应用代码。
