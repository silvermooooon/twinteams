# 我的 Twin 整体验收 — 2026-10-18

## 范围与隔离

结论：本轮实现通过独立整体验收，本地服务已更新并可用，所有已发现问题已修复并复验。没有调用真实模型、修改用户个人资料或新增聊天。

验收覆盖资料、运行、Skills、记忆、凭证五页签，以及主任务/子任务工具权限、配置快照、旧任务恢复、凭证申请和 FIFO 续接。审查与执行独立于实现 Agent；保留仓库原有未提交修改，未提交或推送。

测试从 `checks.local_stack.read_env` 读取 ignored `backend/.env`，仅把本地数据库连接交给测试子进程，未打印数据库连接、模型密钥或凭证值。后端和 SDK 数据库测试均创建随机 PostgreSQL schema，退出后删除；网络验收额外使用临时文件目录、独立 Redis channel 和两个 API 进程。网络环境不启动 Worker，没有真实模型调用。真正 shell 测试仅使用合成凭证。

## 已执行验证

| 检查 | 命令/入口 | 结果 |
| --- | --- | --- |
| 后端完整离线回归 | `backend/.venv/bin/python -m pytest -q`；子进程注入 `TWINTEAMS_TEST_DB_URL`，移除继承的模型/Secrets key | 最终 40 passed，20.84 秒；修复前基线 38 passed，16.36 秒 |
| SDK 完整离线回归 | 在 `agent-runtime` 使用后端虚拟环境执行 `python -m unittest discover -s tests -p 'test_*.py'`；注入 `AGENT_TEST_DB_URL` | Ran 314 tests，37.872 秒，OK；1 项可选 S3/boto3 测试跳过 |
| 真实 HTTP/WS | `backend/.venv/bin/python -m checks.personal_twin_acceptance` | 39 项实际网络断言通过；schema、API 进程、临时文件清理完成 |
| 静态差异 | `git diff --check` | 通过 |
| 相关模块静态检查 | 对 personal、personal_api、personal_schema、credential_api、credential_models、credentials、twin_execution 与新增检查器执行 `ruff check` | 通过；仅修复个人接口两文件的 import 排序和一个未使用 import |
| API/Worker 目录与配置 | `checks.local_stack.environment('api'/'worker')`，仅输出相等布尔值 | Memory 路径一致、Secrets 路径一致、持久加密 key 一致、两侧模型均为 gpt-6-luna |

SDK 命令只发现 `test_*.py`，未运行 `tests/live*`。后端使用真实 PostgreSQL 和 fake provider；shell 操作是短命本地进程。测试通过不作为权限正确的唯一证据，下面列出独立审查结果与网络验证内容。

## 独立审查

- `DurableExecutor` 先获得 SDK PostgreSQL execution lease，再调用工厂和 `assemble`。业务 `execution_config` 仅包含 workspace、已启用 Skills、credential ref/description/env_names；快照不含凭证值。已有 SDK 恢复继续使用冻结的工具契约，旧未完成任务没有快照时保留旧工具。
- 只在本人 private conversation 及合法 child origin 安装 shell/credential 工具；群任务只能读取已启用 Skills。child 工厂明确禁用记忆工具，主私聊使用与个人页同一 tenant/user 的 MemoryStore。
- Skills 的目录绑定现有 private-chat workspace，专用 SkillStore 同时约束 `load_skill` 和 `load_skill_reference`。缺失、无效或越界 Skill 不进入可用目录；关闭开关影响后续任务，不改正在运行任务的目录快照。
- 凭证值采用独立 HTTP 输入和 `SecretStr`，表单错误统一脱敏，异常不经过通用 traceback logger。持久化文件加密；请求、快照和续接正文只存元数据。SDK 每次 shell 调用重新 resolve 值，环境变量不写入全局进程环境，删除、rotation 和 env-name 变化由执行工具重新检查。
- `request_credential` 持久化后立即返回 pending。complete 使用既有 Owner/lane/origin PostgreSQL 行序列化必要竞争；文件写成功而 PG 提交失败时保留 pending，不推断完成。用户可重试或选择已存在且 ref/keynames 匹配的凭证。取消原任务会取消 pending 或已排队续接。
- 资料 avatar 二进制为 deferred 列；常规 bootstrap/actor JSON 不读取或返回二进制。上传按字节和像素限制验证，重编码为 256×256 WebP，专用读取接口带私有缓存和 ETag。

## 真实网络验证内容

独立检查器 `backend/checks/personal_twin_acceptance.py` 创建双 API 服务，mutation 发往 API 1，读取和两个账号的 WS 发往 API 2。通过内容包括：

- 六类个人接口未登录均 401；本人资料在第二进程读取到更新，另一个账号资料隔离。
- `actor_changed` 向账号发送公开资料失效信息；`personal_changed` 仅发给对应 owner，另一个账号收不到个人事件。事件经过实际 Redis 跨进程投递。
- 上传图片裁剪/重编码为 256×256；ETag 返回 304；无效图片拒绝；删除头像成功。
- Skills 发现、启用、详情、账号隔离；记忆两页分页、搜索、账号隔离、写接口 405。
- 凭证创建、跨 API 读取、删除、账号隔离；创建响应和校验错误不含提交值；API 日志不含测试值；敏感接口成功响应 `Cache-Control: no-store`。
- 凭证申请跨账号完成为 404；本人完成一次排入 continuation，重复提交返回同一 continuation；没有 Worker 时仍正常完成表单请求，不占用模型执行。
- 无 Worker 的隔离服务正确显示 offline，不伪造配置状态。

## 审查发现与修复复验

交叉审查实际复现：原任务已成功 shell 后申请凭证，填表产生的新 `credential_result` 轮次没有原任务已提交工具结果。只有“不要重复”提示无法让模型知道哪些步骤已经完成。因此暂停迁移，完成修复并独立复验后才更新本地服务。

修复新增三个 nullable 非秘密 host 来源指针：`runtime_session_id`、`runtime_run_id`、`runtime_command_id`，不对个人接口暴露。新 continuation 首次工厂调用先复查 owner/private origin、当前 source 授权及 context manifest，再按 root run 和申请凭证的 child run 读取已提交事实，作为 system 的历史数据区；不扫描整个 session，不重放工具，不保存第二份永久工具历史。resumable 工厂保留既有 SDK 恢复边界。

新增实际 shell 回归确认：准备文件只写一次，填表后第 4 次 fake provider 调用能看见先前成功命令与结果，原 run 仅读取一次，模型输入不含填写值。另一个定向回归确认 root/child 两个来源各读一次；child 正常 settling 时返回 RuntimeBusy 并保持原 FIFO queued，由既有完成通知唤醒；waiting_recovery、缺失或未决记录明确拒绝续接，不当作空历史。

独立验收 Agent 补充了旧表迁移测试：创建含旧 pending credential 的旧结构，连续执行两次升级，ref/status 与 Actor 名称保留，三个来源指针为 null。个人测试 5 passed（1.39 秒）；修复后完整后端 40 passed，双 API 网络 39 项再次通过。

## 浏览器与构建

前端 Agent 已用指定 Node runtime 完成 TypeScript 检查和 Vite build。离线 Chrome 脚本 `frontend/checks/my-twin-ui.cjs` 覆盖五页签、头像 canvas 裁剪输出 256px PNG、5 MiB 上传限制、移除头像、中英文、窄屏、密码值清空和 pending 卡片流程。

真实浏览器另用全新 Chrome context 连接检查器的两个隔离 API，同源加载真实构建产物，没有拦截或 mock API/WS。执行入口：`python -m checks.personal_twin_acceptance --browser-marker /tmp/tt-personal-browser-fixture.json`；浏览器完成后删除 marker，检查器终止子进程并清理随机 schema/临时目录。

真实五页签结果：资料名和默认头像跨 API 持久化；运行页正确显示无 Worker/未知配置；Skills 开关、详情和名称搜索；三条 Memory 的列表、搜索、全文和原始 session/event 引用；合成凭证新增、替换、删除、不回填、无 localStorage 残留。浏览器报告无页面异常或非预期 API 失败，模型调用为零。独立验收 Agent 目视复核了桌面及 390px 窄屏截图。

临时证据：`/tmp/twinteams-my-twin-real-report.json`、`/tmp/twinteams-my-twin-real-desktop.png`、`/tmp/twinteams-my-twin-real-mobile.png`。离线截图为 `/tmp/twinteams-my-twin-desktop.png`、`/tmp/twinteams-my-twin-mobile.png`。真实 fixture 的凭证申请已 fulfilled，待办卡片的操作路径由离线 Chrome 与后端/真实 HTTP 分别覆盖，不宣称该次真实浏览器执行了 pending 填表。

## 现有本地栈更新与保留性

所有作者完成且最终复验通过后，使用现有 `backend/checks/local_stack.py` 顺序执行 `stop`、`migrate`、`start`，三个命令均成功。未改动 FRP、nginx 或系统设置。更新后 Worker 与 8000/8001 API 均正常运行。

迁移前确认现有 ReplyRequest 无 queued/running，保存仅包含列名/count/hash 的私密临时快照。迁移后 Actor、Message、ReplyRequest 全部原有列的 hash/count 相同，Chat 数量相同，服务保持无待执行任务。快照随后删除，没有把用户内容写入验收文档。没有改用户 Twin 资料，没有创建新聊天或执行模型请求；只读 smoke 的登录 session 正常退出。

最终本地只读 smoke 19 项通过：8000/8001 的健康、资料、运行、Skills、记忆列表/搜索、凭证目录/请求均 200；两个 API 均报告实际 Worker online/configured、gpt-6-luna/openai/openai-responses；8001 用 8000 的登录会话完成实际 WS 握手。API/Worker 的 Memory/Secrets 目录和加密 key 一致。

生产真实 Chrome 最后另用新 context 登录 8000，只读遍历五页签，确认 runtime 显示 gpt-6-luna/openai/openai-responses、在线且已配置，记忆 Enabled·Local，没有个人配置写入或聊天发送。检查后正常 logout 并关闭 Chrome。报告 `/tmp/twinteams-my-twin-live-runtime-report.json` 的 pageErrors、apiFailures、personalMutations 均为空、chatMessagesSent 为 0；独立目视截图 `/tmp/twinteams-my-twin-live-runtime.png`。退出后的已有异步请求返回 401 属于失效会话的预期行为。

公网检查使用现有私密 access JSON 内存读取 HTTP basic 认证、禁用环境 proxy、保持正常 TLS 验证。更新前健康、首页和全部首页静态资源均可达。更新后有限复查曾遇到 TLS 握手 EOF；后续 socket 正常 TLS 握手成功，stdlib urllib（`ProxyHandler({})`）的最终健康、首页和首页静态资源全部 200。只读 SSH 确认 `frps-twinteams`、nginx 均 active，443/7000/127.0.0.1:18000 正常监听，本地 frpc 存活。未重启或修改 FRP、nginx、证书及系统设置。

最终验收的主要运行计数为：40 个后端测试、314 个 SDK 测试（1 个可选依赖跳过）、39 项隔离真实 HTTP/WS 断言、19 项本地现有栈只读 HTTP/WS 断言、5 个真实浏览器页签，另有公网健康/首页/静态资源检查。SDK 在本轮补丁后无新改动，按首次全量结果计，未为凑次数重复运行。

## 边界

本次不验证真实供应商响应，不调用真实 LLM，不把真实凭证用于 shell。执行后端仍是同一 OS 用户的可信本地演示，权限控制不是操作系统沙箱。凭证续接必须提供成功工具步骤作为事实数据，但不会宣称模型绝对不会提出语义重复操作。
