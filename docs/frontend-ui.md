# First frontend UI

Teams-inspired shell with top people-search field, two-item rail (提醒 / 对话),
conversation sidebar, and chat pane/composer. Default conversation: 我的数字分身.

Run `npm run dev` in `frontend/`, then open http://127.0.0.1:5173 on the same
machine. `#chat` and `#reminders` select the initial view. No deployment or tunnel.

Local-only interactions: select chats; type/send messages; suggestion chips fill
the composer; insert a smile; add, complete and reopen reminders. All edits reset
on refresh. No messages or reminders are transmitted. Contacts/content are mock data.

`src/api/people.ts` reserves a typed GET `/people?query=...` boundary with optional
cursor/limit. It has no implementation. Top search performs no filtering or
fetching. Chat-history search and backend routes are out of scope.

The Python 3.13 FastAPI foundation and adjacent agent-runtime remain unchanged.
No tests, regressions, typecheck, lint or production build were run per user direction.

## Runtime observation

Vite started and served the transformed React entry component and CSS successfully
(HTTP 200). The cloud browser could not reach the isolated command network.
Host-visible shell startup was initially blocked by an execution-environment
mount error. The existing dist was then served from the cloud desktop terminal
and opened in the cloud browser at http://127.0.0.1:5173/. The UI rendered fully;
no application JavaScript errors were observed. This was a visual/runtime
inspection, not an automated test suite. The loopback URL is not an external
preview link.
