# Browser desktop notifications

Twinteams uses the browser's desktop Notification API while a signed-in page
is open. In **Settings and more → Notifications and activity**, use **Desktop
notifications** to turn this browser's preference on or off. An initial **Turn
on** button requests browser permission only when clicked. Blocking or dismissing
the request does not prevent chat from working; a browser-level block must be
changed in the browser's site settings.

## Behavior and Teams reference

The design follows the familiar separation between chat activity and desktop
alerts. Microsoft Teams lets users select notification destinations and change
chat/channel notification preferences; its quick views do not change those
preferences. Twinteams currently offers a single desktop-notification control,
not the complete Teams notification/settings feature set. See Microsoft's
[Manage notifications in Microsoft Teams](https://support.microsoft.com/en-us/teams/notifications-settings/manage-notifications-in-microsoft-teams).

When enabled and browser permission is granted, a committed incoming message can
notify while the page is hidden or its window is unfocused. The alert includes
the author and chat, a bounded preview, and a brand icon. Clicking it focuses the
existing page and opens that chat; it does not automatically send a reply.

| Message or refresh | Expected result |
| --- | --- |
| Your Twin's normal answer | Notify when the page is in the background. |
| Someone else's human, Twin or Reply for me message | Notify when the page is in the background. |
| Your own human message or your own Reply for me rewrite | No notification; a local echo or acknowledgment is not an incoming message. |
| Focused page, initial history, reaction/read-state change | No desktop alert for the old message. |
| Reconnect | Do not replay the same last message. A newer reply committed during the gap can notify once after reauthorization. |
| New conversation | A new incoming first message can notify; newly authorized old history is not a new message. |
| Attachment without text | Show the attachment fallback rather than an empty preview. |
| Multiple same-origin tabs | A short Web Locks claim and bounded local metadata coordinate duplicate delivery. Without the browser capabilities needed for coordination, deduplication is best effort. |
| Logout or account switch | Close this account's alerts and stop its observer. Preferences and claims are scoped by owner. |

Alerts consume already-authorized chat bootstrap/detail/summary responses. No
message bodies are added to WebSocket hints, no extra message polling is added,
and no provider call is required to deliver an alert. The claim store contains
bounded chat/sequence/message identifiers, not notification preview text.

## Browser and operating-system limits

The Notifications API requires a secure context and browser permission. Requests
should follow a user gesture. See MDN's
[requestPermission documentation](https://developer.mozilla.org/en-US/docs/Web/API/Notification/requestPermission_static).
Use trusted HTTPS in deployment; loopback localhost is the isolated test origin.
A self-signed certificate warning must not be bypassed and then reported as a
successful secure-context notification test.

The browser and operating system control actual presentation. Browser/site
permissions, OS notification permissions, Focus/Do Not Disturb, browser policies
and platform support may suppress a banner or sound even when construction
succeeds. This implementation needs the page open and connected; it does not
install a service-worker push subscription, promise delivery after all tabs are
closed, or provide mobile background-push parity. MDN distinguishes page-bound
notifications from persistent service-worker notifications in
[Using the Notifications API](https://developer.mozilla.org/en-US/docs/Web/API/Notifications_API/Using_the_Notifications_API).

## Reproducible isolated browser checks

Install the normal frontend dependencies and a supported Playwright browser as
described in [browser-e2e.md](browser-e2e.md). Run the existing isolated launcher:

```sh
cd backend
TWINTEAMS_BROWSER_CHANNEL=chrome TWINTEAMS_E2E_CASE='^notifications ' \
  .venv/bin/python checks/browser_e2e.py --node "$(command -v node)" \
  --artifact-dir /tmp/twinteams-browser-notifications
```

The launcher provisions temporary PostgreSQL/Redis/files, two real loopback API
processes and the worker/SDK with the deterministic offline provider. It rejects
production origins. No real model calls or production messages are needed. The
notification cases are also registered in the default offline suite, before the
final Reset workflow. There are **eight notification workflows**; a filtered run
is not the full offline suite.

`frontend/e2e/notifications.cjs` controls only the browser Notification boundary
for seven cases: permission result, constructor observation, close and click.
These cases do not intercept business HTTP or WebSocket traffic. The own-Twin case also moves only browser `Date.now()` one day ahead; real
server timestamps and the monotonic performance clock remain unchanged. This
checks the server-provided initialization baseline without pretending that a day
has elapsed. The general focused/blurred inputs are explicitly controlled rather than passed off as
physical window-focus tests. All hidden/unfocused inputs, including the native smoke's background input,
are explicitly controlled. Actual physical window blur and hidden-tab behavior
are not verified in this environment.

| Named workflow | Evidence checked |
| --- | --- |
| Permission gesture | No automatic permission request, active user gesture on Turn on, grant, opt-out and reload. |
| Denied/unsupported | Ordinary real chat remains usable; no constructor or unsolicited permission request. |
| Own Twin and click | Real worker reply while blurred; human persistence/ACK and focused replies remain silent; click invokes focus and source-chat navigation. |
| Incoming kinds | Actual human, other Twin and other owner's rewrite notify; own rewrite does not. Hidden/unfocused inputs are controlled. |
| Initial/reaction/reconnect | Initial history and reactions stay silent; real browser offline/reconnect catches a persisted gap reply once. |
| New chat and attachment | Actual new group first message and uploaded attachment-only fallback. |
| Multi-tab/account lifecycle | Same-origin tabs share one claim, then real UI logout/account switch isolates later messages. |
| Native Chrome smoke | Real browser permission grant and one call to the original native Notification constructor under controlled background input, promptly closed. |

The separately named **native Chrome smoke** does not replace Notification with
a synthetic implementation: an observer forwards to the original constructor.
It creates at most one synthetic-content notification and closes it after 300
milliseconds. Its focus/visibility inputs are controlled too. This verifies
browser API creation without an exception, not real document blur, an OS toast
or sound. Its permission grant applies only to the isolated
loopback context. It does not change production browser permissions.

Source withdrawal/authorization-driven last-message rollback is covered by the
backend's real PostgreSQL authorization tests. The frontend retains a maximum
sequence high-water mark. No browser withdrawal workflow or write-capable fixture
command is invented for this check; that boundary must not be counted as a
browser-interaction pass.

## Recorded results

The first full-suite attempt stopped after **19 executed workflows: 18 passed,
one failed**, with 16 not run. Its preserved artifacts are
`/tmp/twinteams-desktop-notifications-full-20261023/`. The first notification
case failed because the test's unscoped Settings and more locator matched both
the header and navigation rail. The helper now scopes that existing action to
the header; no product selector or behavior was changed to satisfy the locator.
This partial run is not a 35-case pass, and no native notification was constructed.

The first seven-case filtered attempt then stopped at **3 passed, 1 failed**,
with three not run, in
`/tmp/twinteams-desktop-notifications-targeted-20261023/`. Its next case had not
sent the incoming message: the test incorrectly required both hidden and
unfocused after bringing another page forward. The product condition is hidden
or unfocused. A second filtered attempt, preserved at
`/tmp/twinteams-desktop-notifications-targeted-final-20261023/`, also stopped
at 3 passed/1 failed before sending that case's message: neither physical
background state was observed. Neither attempt constructed a native notification.

A separate, application-free Chrome 155.0.8059.85 capability probe confirmed the
limitation: on `about:blank`, the first page remained `visibility: visible` and
`hasFocus(): true` before and after another page was brought forward. The final
test inputs are therefore explicitly controlled for both stub and native cases.
The product's real hidden-or-unfocused rule is unchanged. These are test-boundary
adaptations, not product fixes, and both failed reports remain intact.

Review also corrected three product race boundaries before final verification:
use existing chat revisions to reject stale snapshots; revoke pending alerts on
an unauthorized/unavailable summary; and use bootstrap's server time for the
initial-message baseline rather than trusting a fast client clock. The maximum
sequence high-water mark still survives reconnects and source rollback. These
changes do not add polling or rebaseline every reconnect.

The controlled seven-case subset then passed **7/7** in **60.383 seconds**,
from `2026-10-22T17:49:44.556Z` to `2026-10-22T17:50:44.939Z`, with installed
Chrome **155.0.8059.85**. Its report explicitly has `full_suite: false`, with
35 registered cases, at
`/tmp/twinteams-desktop-notifications-stub-controlled-20261023/`. Its six owned
processes, schema/root and Redis namespace were cleaned. This was not a native
notification or full-suite pass.

The next full attempt, preserved at
`/tmp/twinteams-desktop-notifications-full-final-20261023/`, stopped at **21
passed, 1 failed**, with 13 not run. The notification case selected a chat by
substring title; an earlier history workflow had added CCC to an old similarly
named chat. The real rewrite was successfully committed to that selected old
chat while the test waited on the new pair's ID. Trace evidence included the
actual settings target and committed `reply_for_me` message. The case now
selects the incoming message's unique preview and verifies that source message's
article before sending. It still requires the rewrite to persist in the intended
chat. Its silence assertion uses that rewrite's unique label rather than
rejecting concurrent legitimate incoming notifications. Product permissions,
routing and timeouts were not changed for this correction; no native
notification had been constructed.

The sixth run, a fresh complete suite on **2026-10-23 (Asia/Shanghai)**,
**passed 35/35 workflows, 0 failed**, with `full_suite: true`, 35 registered and
executed cases, and no unrun cases. It lasted **347.785 seconds**, from
`2026-10-22T17:58:42.370Z` to `2026-10-22T18:04:30.155Z`, using installed Chrome
**155.0.8059.85**. Its original artifacts are
`/tmp/twinteams-desktop-notifications-full-verified-20261023/`.

All eight notification cases passed in that complete run. The corrected
incoming-message case persisted its own rewrite in the intended chat and
verified its suppression by unique message label. Real human/Twin/rewrite kinds,
attachment fallback, multi-tab claims, account isolation, reconnect-gap delivery
and the fast-client-clock case reached their assertions. The other 27 workflows
also passed, including ordinary sends, Workspace behavior and the final Reset.

The native case performed the only native construction in this round: a **real
Chrome permission grant** and a successful call to the **original Notification
constructor**, automatically closed after 300 milliseconds. The report explicitly
records `controlled_background: true`,
`actual_document_blur_verified: false`, `actual_OS_toast_verified: false` and
`sound_verified: false`. This is not OS display, sound or physical-focus
acceptance.

Independent review read the complete report and the native observer code,
checked the final permission-prompt/settings screenshots, and confirmed no
business HTTP or WebSocket interception. The report uses
`model_mode: deterministic-offline`, `synthetic_model: true` and
`actual_model: null`: **zero real provider calls** were made. The sensitive
credential workflow retained no screenshots/traces; the final text artifacts
contain zero raw fake-secret markers and there are no retained trace ZIPs.
`cleanup.json` records all seven owned process instances stopped, the schema
and temporary root removed, and zero namespaced Redis keys.

The report's `source_commit` is base HEAD
`43b25fc33abef4ab25563a6a48f486de2a47bf06`. This tested the uncommitted candidate
working tree, not that base commit alone or an already-created delivery commit.
The backend's separate metadata/performance/ACK checks passed **15/15 in 8.67
seconds**; they are not additional browser workflows. The earlier partial and
filtered reports remain unchanged: six executions comprise four failed partial
runs, one seven-case subset pass and this complete 35-case pass. No production
permission, message, model, account or workspace was used for these checks.
