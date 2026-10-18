# 我的 Twin 前端验收

本轮扩展现有 Teams 风格，复用 Avatar、Icon、Modal、按钮及表单。入口位于本人账户菜单，个人页有六页签，URL 为 `#/my-twin/{profile|soul|runtime|skills|memory|credentials}`。

## 已覆盖

- 本人账户和 Twin 独立显示；修改 Twin 名称后更新各处 Actor 和私聊标题，较旧 HTTP 响应不会覆盖较新的资料 revision。
- 六款默认头像统一选择保存；上传支持拖动、缩放和键盘位置滑块，前端裁剪为 256px PNG；可更换、移除，输入限制 5 MiB。
- 运行配置只读，显示 Worker 实际 provider/model/API、连接、记忆及报告时效；注明新会话默认与已有会话不同。到期只使用一次本地计时器更新状态，不新增轮询。
- Skills 按名称和描述本地过滤；开关、缺失但已启用条目的关闭、详情及引用文件列表。
- Soul 编辑固定的 `SOUL.md`，版本为内容哈希；读取后展示默认内容，Agent 可修改同一文件，改名同步托管的名称部分。确认删除会删除文件，后续读取不自动重建；Twin 身份保留。不提供平台 system 提示词字段或编辑入口。
- 记忆未启用、空目录、错误分别处理；列表、搜索、分页、全文及原始 session/event 引用保留。包括 `MEMORY` 在内的真实条目可新增、编辑、确认删除，版本显示 Git commit；不推测引用内容。
- Soul 与记忆使用 `expected_version`。409 展示服务端当前内容并保留本地草稿，禁用保存；用户明确选择重读或采用当前版本并合并后才能再次保存。无自动重试或覆盖。
- 草稿仅保留于当前账户的组件状态，切换页签、关闭记忆弹窗和收到元数据通知均保留；不存浏览器持久存储。Git/Dream 显示下次维护、最近成功及错误；“立即 Dream”提交后台任务并复用已有任务，提交后刷新一次状态，无轮询、额外开关或任务参数。
- 资料页底部红色“重置 / Reset”，一次取消／确认弹窗说明只清本人私聊与 Memory，保留群聊、工作区文件、身份和个人配置。提交中禁重复和关闭；失败保持同一次幂等请求，刷新后能从 canonical metadata 继续 pending 操作；409 要求关闭弹窗检查后重新确认。
- 重置完成通过 owner 通知／重连读取 canonical generation，仅清本人私聊输入、附件输入、历史分页、Activity 与 Memory 草稿及缓存；Soul 草稿与当前群聊保留。旧私聊、summary、分页和 Memory 异步响应不得恢复重置前内容。
- 凭证多字段新增、替换、删除；待办请求可填写新值、选择 ref 与 keynames 匹配的已有凭证或忽略；私聊中显示非阻塞卡片。
- 凭证值仅保留于密码输入框，提交前读取后立即清空；失败、关闭、切换表单、卸载和 pagehide 均清空；不进入 React 状态、localStorage 或聊天消息。
- 登录即建立现有 WebSocket；`actor_changed` 定向读取 Actor，`personal_changed` 刷新对应元数据；重连同步当前资料及当前聊天成员，无新增轮询或订阅协议。
- 中英文、方向键/Home/End 页签导航、Escape/焦点恢复、reduced-motion；390px 窄屏头像弹窗不越界。

## 离线浏览器验证

`checks/my-twin-ui.cjs` 使用 Playwright 和本机 Chrome，拦截全部 API/WS，仅使用合成资料和合成凭证。它不连接数据库、真实凭证存储或模型。先启动 Vite（默认端口 5199），再执行此脚本。

Reproduction requires Node.js >=22.12 on `PATH` and Playwright with Chrome; replace `<playwright-node-modules>` with the directory containing the installed Playwright package.

```sh
NODE_PATH="<playwright-node-modules>" node checks/my-twin-ui.cjs
```

可通过 `TWINTEAMS_UI_URL` 修改前端地址，通过 `TWINTEAMS_BROWSER_CHANNEL` 修改浏览器 channel。

截图写入 `/tmp/twinteams-my-twin-desktop.png` 和 `/tmp/twinteams-my-twin-mobile.png`。已目视确认布局，并修复默认头像网格触发的窄屏弹窗溢出。

TypeScript 检查使用仓库 `node_modules/typescript/bin/tsc --noEmit`；构建使用 `node_modules/vite/bin/vite.js build`，均由上述 Node runtime 执行。

`checks/soul-memory-ui.cjs` 在同一离线环境验证 Soul 保存/清空、切页及通知时的草稿保留、明确的冲突合并、Git Memory 新增/编辑/删除及删除冲突、根 `MEMORY` 编辑、Dream 只读状态、中英文及 390px 移动弹窗。两套脚本均已通过；Soul 上限 50,000 字符、Memory 上限 100,000 字符与接口一致。新增截图为 `/tmp/twinteams-soul-memory-desktop.png` 和 `/tmp/twinteams-soul-memory-mobile.png`。

## 真实隔离服务验证

以下是此前五页签版本的真实隔离验证；新增 Soul/Git Memory CRUD 的真实隔离证据另行追加。整体验收提供随机 PostgreSQL schema 和两个同源 API 实例后，使用全新 Chrome context、正常 DEV-AAA 登录及实际构建产物完成五页签 smoke，未设置任何 mock 路由。

- 修改名称和默认头像后，第二 API 实例读取同一 profile revision，确认实际持久化。
- Worker 未启动，运行页准确显示离线／未知；未发送聊天或触发模型。
- Skills 开关后恢复原状态，详情和客户端搜索可用。
- 三条合成记忆的列表、全文、搜索及准确引用可读。
- 合成凭证值经真实服务新增、替换、删除；更新表单不回填值，浏览器存储不含值。
- 桌面和 390px 移动端均检查布局；无页面脚本错误、无个人 API 异常。预登录 `/auth/me` 401，以及隔离环境禁用旧 Provider 配置页的 403 属预期鉴权结果。

证据为 `/tmp/twinteams-my-twin-real-report.json`、`/tmp/twinteams-my-twin-real-desktop.png` 和 `/tmp/twinteams-my-twin-real-mobile.png`。临时 fixture marker 已按约定删除，由验收进程清理随机 schema。

真实 fixture 的凭证请求已完成，待办填写／选择已有凭证的交互由上面的离线浏览器测试覆盖；本项 smoke 不代替后端权限、Worker 执行及续接验收。

## Soul / Git Memory 真实隔离服务验证

`checks/soul-memory-real-ui.cjs` 必须通过 `TWINTEAMS_ISOLATED_FIXTURE` 读取验收方的随机 PostgreSQL schema fixture marker，拒绝生产常用端口。使用全新 Chrome context、DEV-AAA 正常登录，未拦截 API 或 WebSocket，运行真实构建产物与临时 Git store：

- Soul 真实保存、递增版本、清空；过期版本 PUT 返回 409。另一设备更新经真实 WS 展示当前内容，保留输入，明确采用新版本并合并后保存。
- Memory 真实创建、编辑、删除；每次保存返回不同的 40 位 Git commit 与本人配置审计来源。过期删除返回 409 且当前条目保留；最终删除后读取 404。
- DEV-BBB 不能读取 AAA 的记忆；Soul 仅返回本人内容，伪造 body owner 字段 422，AAA 内容未改变。
- Profile/Runtime/Skills/Credentials/Memory 原入口与对应实际 API 回归通过。隔离 fixture 无 Worker，未将 Git runtime fixture 结果声称为生产 Worker 状态。
- Dream 基本状态由真实 API 显示；桌面与 390px 移动弹窗已截图及目视检查；无脚本错误、个人接口 5xx 或聊天请求写入。

结果 `/tmp/twinteams-soul-memory-real-report.json`，截图 `/tmp/twinteams-soul-memory-real-desktop.png` 与 `/tmp/twinteams-soul-memory-real-mobile.png`。测试后已注销并关闭 context，marker 已删除交由验收进程清理。没有修改生产用户、启动 Worker 或调用模型。

## 部署后的真实本地栈只读验证

收到验收方确认 8000/8001 与 Worker 更新且健康后，执行 `TWINTEAMS_LIVE_READONLY=1` 的 `checks/soul-memory-live-readonly.cjs`。使用全新 Chrome context 在 `http://127.0.0.1:8000` 正常登录 DEV-AAA，没有 API/WS mock。

- 六页签全部可用；Soul 版本 0、空内容及 50,000 字符编辑框正常，无平台提示词入口。
- 真实 Worker 在线且 configured，实际显示 `openai / gpt-6-luna / openai-responses`，记忆模式为 Git。
- Memory 实际启用，当前无条目；新增表单可打开并取消，未输入或保存。已有条目的编辑/删除由此前随机 schema 的真实 CRUD 验收覆盖，未为本次 smoke 增加用户数据。
- Dream 为 idle，下次运行为空、游标 0、无成功/错误记录；桌面页面及 390px 移动弹窗截图已目视确认，无页面横向溢出。
- 无 JavaScript 错误、个人 API 错误、个人配置写入或聊天消息请求；Soul 和 Memory 前后 GET 内容完全一致。已注销并关闭浏览器，没有模型调用。

报告 `/tmp/twinteams-soul-memory-live-report.json`；截图 `/tmp/twinteams-soul-memory-live-soul.png`、`/tmp/twinteams-soul-memory-live-runtime.png`、`/tmp/twinteams-soul-memory-live-desktop.png`、`/tmp/twinteams-soul-memory-live-mobile.png`。

## 重置 / 立即 Dream / 文件 Soul 离线验证

新增 `checks/twin-reset-ui.cjs`，与原两套浏览器脚本一起使用纯合成 API/WS。已覆盖取消零写入、标准确认无额外输入、提交禁关闭／二次提交、失败取消再打开仍使用同 key、pending 刷新恢复使用原 key 与 generation、重置清私聊／Memory 草稿而保留 Soul 草稿、当前群聊输入保留且不重新拉群、重置前晚到的私聊响应与 Memory 409 无法恢复旧内容。“立即 Dream”排队后禁重复，未加轮询。

`SOUL.md` 的哈希版本及删除语义已更新到原 Soul/Memory fake fixture；原头像、资料、Skills、凭证、键盘／中英文回归继续通过。重置桌面与 390px 截图 `/tmp/twinteams-reset-desktop.png`、`/tmp/twinteams-reset-mobile.png` 已目视检查，确认按钮和说明未溢出。最终 TypeScript 与 Vite build 通过。

真实写入验证使用 `checks/twin-reset-real-ui.cjs` 且必须提供随机 schema fixture marker；脚本拒绝正常应用端口，生产只允许用 `soul-memory-live-readonly.cjs` 查看重置弹窗后取消，禁止点击确认或 Dream。

## 重置 / 文件 Soul / 立即 Dream 真实隔离验证

验收方先运行真实双 API HTTP/WS／owner 隔离脚本，再提供随机 PG schema、临时 Git store 和同源构建 UI。全新 Chrome context 正常 DEV-AAA 登录，不设置 API/WS mock，不启动 Worker：

- 浏览器取消确认不发重置请求；确认后 canonical generation 递增，真实本人私聊与 Memory 为空。群消息、Actor 资料、SOUL.md 的内容哈希、Skills 和凭证元数据前后相同。
- 重置后新增合成 Memory，再通过旧 idempotency key 重放原请求，新 Memory 保留。
- SOUL.md 保存返回 64 位 SHA-256。真实托管名称更新保留用户追加段；另一设备修改显示冲突并经明确合并保存。确认删除后连续读取均为 `version:null`，没有自动重建，随后可重新创建。
- “立即 Dream”实际成功排队；已有 queued 状态禁重复。此 fixture 的 Memory 配置改动也构成待处理内容，因此实际结果是 queued。只排任务，没有 Worker 或模型执行。
- 桌面及 390px 重置弹窗截图已目视检查。无 JavaScript 错误、个人 API 5xx 或聊天消息写入；最终已注销关闭，删除 marker，由验收进程清理随机 schema。

报告 `/tmp/twinteams-reset-real-report.json`，截图 `/tmp/twinteams-reset-real-desktop.png` 与 `/tmp/twinteams-reset-real-mobile.png`。本轮没有对真实用户执行重置、Soul 写入、Dream 或聊天请求。

## 本轮部署后的中文只读验收

验收方确认 API 8000/8001 与 Worker 部署健康后，用 `checks/twin-reset-live-readonly.cjs` 在 8000 创建全新中文 Chrome context。HTTP 传输保护仅放行 GET 与登录／退出，不模拟 API 数据；同时记录 WebSocket 命令，实际只有自动 presence 控制帧，没有 send 或其他内容命令。

- 六页签均正常。中文红色“重置”按钮打开标准取消／确认弹窗，桌面与 390px 两次均只取消；未提交重置，canonical generation 始终为 0。
- “立即 Dream”可见但未点击；实际状态 idle，游标 0、下次运行为空。
- SOUL.md 默认模板包含当前 Twin 名称，内容计算的 SHA-256 与 API 哈希完全一致（`7b1bfb600f7c87d0c964750b44425e2035ea3cac478d34f733be8e4de664cbf0`）。未编辑、删除、保存或改名。
- 真实 Worker 在线、configured，`openai / gpt-6-luna / openai-responses`，记忆模式 Git。
- 桌面与 390px 截图已目视检查，无页面横向溢出。Actor、Soul、Memory、reset metadata 前后相同；个人写入／聊天命令、JavaScript 错误及个人 API 错误均为零。已退出登录并关闭浏览器，没有模型调用。

报告 `/tmp/twinteams-reset-live-report.json`；截图 `/tmp/twinteams-reset-live-desktop.png`、`/tmp/twinteams-reset-live-mobile.png`、`/tmp/twinteams-reset-live-soul.png`、`/tmp/twinteams-reset-live-memory.png`。

## 工作区刷新修复后的既有回归

本轮仅调整 `DetailSidebar.tsx` 的 activity/workspace 首次加载标记，以及 `App.tsx` 同聊天暂态网络错误的处理：已加载空列表在后台刷新时保留空态；500／网络失败保留同 owner、同聊天的现有内容，401／403／404／409 仍清除。没有更改弹窗焦点 effect 或文件预览依赖，也不将尚无独立证据的焦点问题归因于 callback。

最终 TypeScript 与 Vite build 通过。使用 8000 实际最终静态构建、全部 API/WS 合成拦截运行原 `twin-reset-ui.cjs` 与 `my-twin-ui.cjs`，均通过：晚到私聊／Memory 响应隔离、重置与群／Soul 保留、幂等恢复，以及头像裁剪、Skills、凭证值清理、中英文、键盘和移动布局。没有写入真实业务、发送消息或调用模型。本段记录既有回归；工作区四场景动态复现与修复验证由独立验收脚本单独记录。

回归前后产物未变：`index-DSKxRNLZ.js` SHA-256 为 `095ea7b2c18ae6eda64baf3f5c8f70c02e55dea9aa01dde0b4f77581f68fb677`，`index.html` 为 `67722fedbc3ba59921bc451e80c94c6f36e8de3303a48ff72fbca6c4680fea77`。回归后未再次构建。

## 聊天人数、名称及替我回复

本人 `kind=private` 聊天隐藏“替我回复”开关，前端统一有效模式为 false（提示、输入框、附件校验及发送 payload 均一致），不回写旧设置。保留 `kind=group` 语义：两位 owner 的标题与头像在导航、头部、详情中使用对方真人；三位及以上 owner 使用群名与无 Twin 标记的群头像，“群聊”筛选排除两人聊天。群名编辑复用现有 Modal，支持 trim、256 字符、保存／取消、提交禁用及错误展示。

新增 `checks/chat-creation-ui.cjs` 在 8000 的最终 `BjCg8P3T` 构建上使用全部合成 API/WS，通过：PeoplePicker 选择人数 1→2→1 时群名输入隐藏→显示→隐藏，降回一人提交不携带残留 title；选两人创建多人群提交 trim 后 title；两人聊天的对方真人头像／姓名、多人群无 Twin 标记，以及群聊筛选分类。所有 fixture 使用真实两／三 owner 形状，未为非法单 owner 群增加业务 fallback。没有真实写入或模型调用。

TypeScript 与构建通过。独立 `chat-naming-acceptance.cjs` 负责本人旧 enabled 的 false payload、AAA/BBB viewer 显示、群改名保存／取消／错误及工作区稳定性验证；本段补充其未覆盖的创建人数变化、头像与筛选。源码和最终构建冻结，回归后未重建。

一对一 get-or-create 补充：沿用现有 PeoplePicker `POST /chats` 与返回 Chat 的导航路径，不新增前端聊天 registry。`chat-creation-ui.cjs` 已在同一已部署构建上验证从另一聊天开始、以及已选中此人聊天时重复 Start，服务器均返回同一 pair ID，最终只有同一导航行被选中、标题正确、创建页关闭且加载正常。后端负责唯一性与并发，前端不猜测或合并历史重复聊天；本次仅补合成 API 浏览器断言，没有修改产品源码或重建产物。
