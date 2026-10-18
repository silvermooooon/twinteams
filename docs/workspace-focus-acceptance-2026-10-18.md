# 工作区刷新与焦点验收（2026-10-18）

本轮前端修复已构建并由现有 API 静态服务提供，无后端迁移或 Worker 重启。

## 原因与修复边界

- WebSocket `changed` 会重新加载聊天并增加侧栏版本。旧工作区用 `loading && !workspace.length` 判断加载态，因此已加载的空目录在每次后台请求期间也会从空状态切换为加载状态。现按 activity/workspace 分别记录是否完成首次加载；后台刷新保留已呈现的空目录。
- 聊天加载的暂时 HTTP 500/网络失败保留同一 owner、同一聊天已加载的详情，显示错误。401/403/404/409 仍清除旧详情。当前聊天及 owner 校验防止旧请求错误清除新界面。
- Modal 与抽屉现有 mount-only 焦点处理保持不变。未观察到最终版本正常后台刷新反复抢焦点；本轮未取得用户原浏览器的运行轨迹，不能将其所有焦点现象断言为同一原因。旧 `/api/events` SSE 客户端请求也不能归因于当前使用 WebSocket 的构建。

## 独立 Chrome 回归

脚本：`frontend/checks/workspace-focus-acceptance.cjs`。真实 Chrome、真实构建资源，所有 API/WS 使用合成响应；不写用户消息、不调用模型。

Reproduction requires Node.js >=22.12 on `PATH` and Playwright with Chrome; replace `<playwright-node-modules>` with the directory containing the installed Playwright package.

```sh
NODE_PATH="<playwright-node-modules>" \
node \
frontend/checks/workspace-focus-acceptance.cjs
```

私聊/群聊 × 空/非空目录四场景全部通过：

- 各场景连续四次 changed：后台 Loading 出现 0 次，空状态移除 0 次，侧栏/输入框 DOM identity 不变，输入焦点与未发送草稿保留。
- HTTP 500 和 fetch 失败后保持侧栏与输入焦点；恢复成功后仍保持。
- 非空树展开状态、滚动位置保持。打开文件预览后增加目录条目、重复 changed、暂时失败和恢复均不重挂 Modal、不抢当前焦点，预览仅请求一次；当前已打开预览保持快照，不声称自动刷新文件正文。
- 各场景 401/403/404/409 均清除原详情与输入框，避免权限回归。
- 四场景无页面 JavaScript 错误。报告 `/tmp/twinteams-workspace-focus-report.json`，空/非空私群截图 `/tmp/twinteams-workspace-{private,group}-{empty,populated}.png`。

实施者另在同一最终构建运行 `twin-reset-ui.cjs`、`my-twin-ui.cjs`，均通过；typecheck/build 通过。未重复与本轮无关的后端全量测试。

## 已提供的构建与真实只读烟测

- 8000 首页与 `frontend/dist/index.html` 逐字节相同。加载资源 `/assets/index-DSKxRNLZ.js`，SHA-256 `095ea7b2c18ae6eda64baf3f5c8f70c02e55dea9aa01dde0b4f77581f68fb677`；首页 SHA-256 `67722fedbc3ba59921bc451e80c94c6f36e8de3303a48ff72fbca6c4680fea77`。
- 新 Chrome context 真实 DEV-AAA 登录、只读运行配置、退出成功；Worker 在线，模型仍 `gpt-6-luna`。HTTP 仅 GET 与登录/退出；WS 仅 presence。无侧栏设置修改、消息发送或模型调用，页面 JS 错误 0。报告 `/tmp/twinteams-workspace-live-report.json`。
- 公网入口正常 TLS、无 Basic Auth，请求首页与上述 JS 均 HTTP 200，内容及 SHA-256 与本地 dist 完全一致。报告 `/tmp/twinteams-workspace-public-report.json`。

真实站点烟测仅验证登录、只读 API 和构建可达；刷新/焦点故障注入使用上述合成 API/WS，未在真实用户聊天注入事件。
