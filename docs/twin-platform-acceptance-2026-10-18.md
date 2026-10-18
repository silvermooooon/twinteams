# Twin 平台参与者验收（2026-10-18）

已部署。Twin 使用其 owner 当前 membership/history 权限操作平台，不获得 tenant 管理员或其他 owner 私聊权限。本轮无 schema 变更、无迁移，未调用真实模型。

## 实现边界

- 群聊也可发现 owner 全部现有聊天；跨聊天读取 history、Tracking、有限文本工作区及平台状态。消息读取有分页上限，工作区文本最多 64 KiB UTF-8，平台状态不返回凭证、执行配置或私有 Memory。
- 主动发送是 Twin 新撰写、保留 Twin attribution 与原 causal chain 的消息；源消息 ID 按实际聊天重新检查 owner 权限并保留内部 manifest/operation 审计。接收新报告不扩大原历史 ACL。
- 群聊读取 owner 私聊后，普通群内自动输出仍受完整来源权限限制，可能仅 owner 可见；应使用明确的主动私聊汇报。新撰写消息正文是否适合受众仍依赖任务与模型判断，记录权限保护不等于能保证正文不转述敏感信息。
- 新增显式 typed tools 复用平台事务内业务规则，没有通用 HTTP/管理员工具。群聊仍不装配 shell、credentials 或 Memory 工具，子任务不改变此边界。
- 审批保留原 chat/session/action/chain，仅向 owner 私聊投影 pending 卡和可靠通知。冻结动作可查看；pending 不阻塞，续接仍回原群，重复执行只产生一次 effect。
- 自动重试消耗触发它的 controller chain，轮换多个旧任务也不能绕过六次额度。正式 exact owner approval 在额度满时仅原子增加并消费一次额度，不重置 spent；group 重试旧 private 任务必须正式审批，保留原冻结配置。旧配置为空则使用不新增执行权限的 legacy 配置；private reset 后旧任务仍不可重跑。自动重试不是新的 human Memory 经历。

## 验证

| 检查 | 结果与范围 |
| --- | --- |
| 最终后端全量（实施者） | **142 passed / 105.64s，无 warning**；真实隔离 PG、离线 provider。 |
| 独立 `tests/test_platform_acceptance.py` | **18 项通过**；真实 SDK/PG。最后 source ID、冻结配置增量 **8 passed / 7.45s**，已纳入最终全量。 |
| 独立 `checks/platform_tools_acceptance.py` | **21 项真实 PG/双 API/WS 检查通过**；无 Worker/provider，临时 schema/文件已清理。 |
| 前端（实施者） | typecheck/build；工作区四场景、旧 Reset/MyTwin、聊天发现两场景、13 种冻结审批动作及 legacy/转义/390px 展示回归通过。使用合成 API/WS，不写业务数据。 |

独立回归实际覆盖：跨聊天发现→history 返回真实 ID→发送 owner 私聊；其他 owner 私聊和 source ID 拒绝；source 撤回/离群/reset/cancel 后晚发失败；新报告不授予原群历史；私聊汇报与审批 notice 不自触发 Agent；审批私聊投影但群续接，真实 SDK 执行冻结 rename 单次；注入/未安装 action 拒绝；跨七个旧 failed target 的 controller 额度耗尽后零 enqueue；批准 private retry 的一次 grant、配置冻结与 reset 拒绝；自动成功不产生 human Dream 经历。

恢复使用真实 PG DatabaseSession 与 SDK Agent：旧 14 项工具的完整 name/schema/description/replay/version 在 model-request 中断及 tool 已提交但结果未入 SDK 日志的中断后均恢复成功；后者不重复发送。新 run 获得新能力；未知 saved tool name 不安装执行器。`accepted_command=None` 已作为真实工具执行路径保留回归。

网络回归另验证：新增成员收到 owner 定向 `personal_changed(section=chats)`，非成员不收，复用 pair 不发送新会员提示；跨 API 可发现新会话；owner 可从私聊审批原群 action，其他参与者不能审批；SDK model usage 为 0。前端相应 hint/重连同步保留当前聊天、草稿及焦点，不因同集合排序重连。

运行独立网络检查：

```sh
cd backend
.venv/bin/python -m checks.platform_tools_acceptance
```

pytest 必须通过 `checks.local_stack.read_env` 仅把私密配置的 DATABASE_URL 传入 `TWINTEAMS_TEST_DB_URL`，沿 `tests/conftest.py` 随机 schema 运行；不得把生产 schema 或真实 provider 配置用于测试。

## 部署与只读烟测

最终预检及停止后 queued/running=0、Dream pending=0；`checks/local_stack.py stop` → `start --port 8000 --second-port 8001`，没有运行 init_db、迁移或生产平台动作。Worker PID **72203**，API PID **72204/72205**。

停止后基线与重启、只读烟测后对照：9 张既有业务表 count/完整行 hash 全部一致。代理保持 7894，模型保持 `gpt-6-luna`。真实 DEV-AAA/DEV-BBB 登录读取各自 private inbox 成功，当前 pending 均为 0，未造演示审批；Worker 在线。纯工具声明检查为 **27 项 group platform tools，全部 `twin-teams-platform-v2`**，含 status 工具，无 private execution 工具。

8000、8001、公网正常 TLS 的 health、首页、最终 JS/CSS 均 200，三处资源逐字节等于 dist：

- `index-s3FTSbjB.js` SHA-256：`9baf848501b5ece6b2c2f3fc96655dfb165adf104a898c791eecf1bfc30c1bc2`。
- `index-BMWbdcWb.css` SHA-256：`4f0e5216bbe7fc7b0945a57fc948309d3269a4048da15957f8ef72cce41d147f`。

只读报告 `/tmp/twinteams-platform-live-report.json`。没有在生产发送、审批、改名或重试任务，没有真实 provider 调用；实际 provider 对新增声明的请求未在本轮测试。临时部署 runner/hash 快照已删除，未提交或推送。
