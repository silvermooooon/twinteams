/** Real business traffic; only the browser Notification boundary is controlled. */
const {
  assert,
  api,
  poll,
  sleep,
  login,
  logout,
  sendMessage,
  privateChat,
  chooseChat,
} = require("./helpers.cjs");
const { startChat, detail, waitMessage } = require("./conversations.cjs");
const path = require("node:path");

// This observes browser API calls, not OS banner display or sound. Context setup
// precedes the app's first script; no business API or WebSocket is intercepted.
async function probeSession(
  t,
  {
    code = "DEV-AAA",
    permission = "granted",
    native = false,
    unsupported = false,
    clockSkew = 0,
  } = {},
) {
  const events = [];
  const session = await t.session(code, {
    contextSetup: async (context) => {
      if (native)
        await context.grantPermissions(["notifications"], {
          origin: t.baseURL,
        });
      await context.exposeBinding(
        "__recordDesktopNotification",
        ({ page }, event) => {
          events.push({ ...event, page: page.url() });
        },
      );
      await context.addInitScript(
        ({ permission, native, unsupported, clockSkew }) => {
          if (clockSkew) {
            const originalNow = Date.now.bind(Date);
            Date.now = () => originalNow() + clockSkew;
          }
          const Original = window.Notification;
          const record = (event) => {
            void window.__recordDesktopNotification(event);
          };
          window.__notificationProbe = { permission, notices: [] };
          if (unsupported) {
            delete window.Notification;
            return;
          }
          function ObservedNotification(title, options = {}) {
            const id = crypto.randomUUID();
            const notice = native
              ? new Original(title, options)
              : {
                  onclick: null,
                  onerror: null,
                  onshow: null,
                  tag: options.tag,
                  close() {
                    record({ type: "close", id });
                    this.onclose?.(new Event("close"));
                  },
                  addEventListener(name, fn) {
                    this[`on${name}`] = fn;
                  },
                  removeEventListener(name) {
                    this[`on${name}`] = null;
                  },
                };
            record({
              type: "construct",
              id,
              title: String(title),
              options,
              native,
              focused: document.hasFocus(),
              visibility: document.visibilityState,
            });
            window.__notificationProbe.notices.push(notice);
            // The native smoke intentionally creates at most one and closes it
            // promptly; success does not assert that macOS displayed a toast.
            if (native) setTimeout(() => notice.close(), 300);
            return notice;
          }
          Object.defineProperty(ObservedNotification, "permission", {
            get: () =>
              native
                ? Original.permission
                : window.__notificationProbe.permission,
          });
          ObservedNotification.requestPermission = async () => {
            record({
              type: "request-permission",
              userActivation: navigator.userActivation.isActive,
            });
            if (native) return Original.requestPermission();
            return (window.__notificationProbe.permission =
              permission === "default" ? "granted" : permission);
          };
          window.Notification = ObservedNotification;
        },
        { permission, native, unsupported, clockSkew },
      );
    },
  });
  session.events = events;
  session.notices = () => events.filter((event) => event.type === "construct");
  return session;
}
async function mode(page, value) {
  // Focus/visibility inputs are controlled for deterministic platform-boundary
  // tests. The native case also declares this controlled background input.
  await page.evaluate((value) => {
    Object.defineProperty(document, "hasFocus", {
      configurable: true,
      value: () => value === "focused",
    });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (value === "hidden" ? "hidden" : "visible"),
    });
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => value === "hidden",
    });
    window.dispatchEvent(new Event(value === "focused" ? "focus" : "blur"));
    document.dispatchEvent(new Event("visibilitychange"));
  }, value);
}
async function preferences(page) {
  await page
    .getByRole("banner")
    .getByRole("button", { name: "Settings and more", exact: true })
    .click();
  await page
    .getByRole("menuitem", { name: "Notifications and activity", exact: true })
    .click();
  return page.getByRole("dialog", {
    name: "Notifications and activity",
    exact: true,
  });
}
async function enabled(page) {
  const dialog = await preferences(page);
  const toggle = dialog.getByRole("switch", {
    name: "Desktop notifications",
    exact: true,
  });
  if ((await toggle.getAttribute("aria-checked")) !== "true")
    await toggle.click();
  await dialog
    .getByRole("button", { name: "Close", exact: true })
    .last()
    .click();
}
async function settled() {
  await sleep(900);
}
async function notification(probe, fragment, count = 1) {
  return poll(
    () => {
      const values = probe
        .notices()
        .filter((entry) => JSON.stringify(entry).includes(fragment));
      return values.length >= count && values;
    },
    `Notification boundary received ${fragment}`,
    { timeout: 25000 },
  );
}
async function noNew(probe, before, label) {
  await settled();
  assert.equal(probe.notices().length, before, label);
}
async function toggleRewrite(page) {
  const button = page.getByRole("button", {
    name: "Reply for me",
    exact: true,
  });
  if ((await button.getAttribute("aria-pressed")) !== "true")
    await button.click();
}
async function sendRewrite(page, chat, label) {
  await toggleRewrite(page);
  const body = `E2E ECHO ${label}`;
  await page.locator(".composer textarea").fill(body);
  await page.locator(".composer textarea").press("Enter");
  return waitMessage(
    page,
    chat,
    (message) =>
      message.kind === "reply_for_me" && message.raw_owner_input === body,
  );
}
async function recipientPair(t) {
  const recipient = await probeSession(t);
  await enabled(recipient.page);
  const peer = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
  const chat = await startChat(peer.page, ["AAA"]);
  await poll(
    async () =>
      (await api(recipient.page, "/bootstrap")).chats.some(
        (item) => item.id === chat.id,
      ),
    "pair membership",
  );
  await settled();
  return { recipient, peer, chat };
}

const cases = [
  {
    name: "notifications platform stub / permission is requested only by a user gesture; grant and opt-out persist",
    run: async (t) => {
      const a = await probeSession(t, { permission: "default" });
      await settled();
      assert.equal(
        a.events.filter((event) => event.type === "request-permission").length,
        0,
      );
      const turnOn = a.page
        .locator(".desktop-notification-prompt")
        .getByRole("button", { name: "Turn on", exact: true });
      await turnOn.waitFor();
      await a.page.screenshot({
        path: path.join(t.artifactDir, "notifications-permission-prompt.png"),
        fullPage: true,
      });
      t.record(
        "permission_prompt_screenshot",
        "notifications-permission-prompt.png",
      );
      await turnOn.click();
      await poll(
        () => a.events.some((event) => event.type === "request-permission"),
        "permission request boundary",
      );
      assert.ok(
        a.events
          .filter((event) => event.type === "request-permission")
          .every((event) => event.userActivation),
      );
      const dialog = await preferences(a.page);
      const toggle = dialog.getByRole("switch", {
        name: "Desktop notifications",
        exact: true,
      });
      assert.equal(await toggle.getAttribute("aria-checked"), "true");
      await a.page.screenshot({
        path: path.join(t.artifactDir, "notifications-settings.png"),
        fullPage: true,
      });
      t.record("settings_screenshot", "notifications-settings.png");
      await toggle.click();
      assert.equal(await toggle.getAttribute("aria-checked"), "false");
      await dialog
        .getByRole("button", { name: "Close", exact: true })
        .last()
        .click();
      await a.page.reload();
      const after = await preferences(a.page);
      assert.equal(
        await after
          .getByRole("switch", { name: "Desktop notifications", exact: true })
          .getAttribute("aria-checked"),
        "false",
      );
      t.record("notification_boundary", "controlled Notification; not OS UI");
    },
  },
  {
    name: "notifications platform stub / denied and unsupported capabilities remain usable without notifications",
    run: async (t) => {
      for (const options of [{ permission: "denied" }, { unsupported: true }]) {
        const a = await probeSession(t, options);
        await privateChat(a.page);
        const label = t.unique("capability");
        await sendMessage(a.page, `E2E ECHO ${label}`);
        await waitMessage(
          a.page,
          await privateChat(a.page),
          (message) => message.kind === "twin" && message.body.includes(label),
        );
        assert.equal(a.notices().length, 0);
        assert.equal(
          a.events.filter((event) => event.type === "request-permission")
            .length,
          0,
        );
        const dialog = await preferences(a.page);
        assert.ok(await dialog.innerText());
        await dialog
          .getByRole("button", { name: "Close", exact: true })
          .last()
          .click();
        await a.context.close();
      }
      t.record(
        "notification_boundary",
        "controlled denied/unsupported Notification capability",
      );
    },
  },
  {
    name: "notifications platform stub / own Twin alerts while blurred; own human ACK and focused replies do not; click navigates",
    run: async (t) => {
      const a = await probeSession(t, { clockSkew: 86400000 });
      t.record("client_clock_boundary", {
        Date_now_offset_ms: 86400000,
        performance_clock_modified: false,
        server_message_times_modified: false,
      });
      await enabled(a.page);
      const chat = await privateChat(a.page);
      await settled();
      await mode(a.page, "focused");
      const focused = t.unique("focused");
      await sendMessage(a.page, `E2E ECHO ${focused}`);
      await waitMessage(
        a.page,
        chat,
        (message) => message.kind === "twin" && message.body.includes(focused),
      );
      await noNew(a, 0, "focused normal Twin reply is silent");
      const label = t.unique("own-twin");
      await sendMessage(a.page, `E2E SLOW ${label}`);
      await mode(a.page, "blurred");
      await noNew(a, 0, "local echo, human persistence and ACK do not notify");
      await waitMessage(
        a.page,
        chat,
        (message) => message.kind === "twin" && message.body.includes(label),
      );
      const notices = await notification(a, label);
      assert.equal(notices.length, 1);
      assert.equal(notices[0].focused, false);
      assert.equal(
        notices[0].visibility,
        "visible",
        "blurred-visible case differs from hidden tab",
      );
      const bootstrap = await api(a.page, "/bootstrap");
      const other = bootstrap.chats.find((row) => row.id !== chat.id);
      await chooseChat(a.page, other.title);
      await a.page.evaluate(() => {
        window.__focusCalls = 0;
        const originalFocus = window.focus.bind(window);
        window.focus = () => {
          window.__focusCalls++;
          originalFocus();
        };
        window.__notificationProbe.notices.at(-1).onclick(new Event("click"));
      });
      await poll(
        async () =>
          (await a.page.locator(".chat-header").innerText()).includes(
            chat.title,
          ),
        "notification click navigated to source chat",
      );
      assert.equal(await a.page.evaluate(() => window.__focusCalls), 1);
      await mode(a.page, "focused");
      await noNew(a, 1, "focus/read do not replay notification");
      t.record(
        "notification_boundary",
        "controlled focus/visibility and Notification click; not native OS click",
      );
    },
  },
  {
    name: "notifications platform stub / incoming human, other Twin and other Reply for me alert; own rewrite is silent",
    run: async (t) => {
      const { recipient: a, peer: b, chat } = await recipientPair(t);
      await mode(a.page, "hidden");
      const human = t.unique("human");
      await sendMessage(b.page, `Human notification ${human}`);
      const observed = await notification(a, human);
      assert.equal(observed[0].visibility, "hidden");
      assert.equal(observed[0].focused, false);
      t.record("controlled_browser_background", {
        visibility: observed[0].visibility,
        focused: observed[0].focused,
        actual_document_blur_verified: false,
      });
      const own = t.unique("own-rewrite");
      const humanSource = await waitMessage(
        b.page,
        chat,
        (message) => message.body === `Human notification ${human}`,
      );
      // Earlier workflows may have renamed or expanded another similarly named
      // chat. Select the real incoming message's unique preview, not chat.title.
      await a.page
        .locator(".conversation-list .chat-item")
        .filter({
          has: a.page.locator(".chat-preview").filter({ hasText: human }),
        })
        .click();
      await a.page
        .locator(`article[data-message-id="${humanSource.id}"]`)
        .waitFor();
      const rewritten = await sendRewrite(a.page, chat, own);
      assert.equal(rewritten.chat_id, chat.id);
      t.record("own_rewrite_message", {
        id: rewritten.id,
        chat_id: rewritten.chat_id,
        kind: rewritten.kind,
      });
      await mode(a.page, "hidden");
      await settled();
      assert.equal(
        a.notices().filter((notice) => JSON.stringify(notice).includes(own))
          .length,
        0,
        "this own reply_for_me is silent even if another legitimate incoming alert arrives",
      );
      const incoming = t.unique("peer-rewrite");
      await sendRewrite(b.page, chat, incoming);
      await notification(a, incoming);
      const button = b.page.getByRole("button", {
        name: "Reply for me",
        exact: true,
      });
      if ((await button.getAttribute("aria-pressed")) === "true")
        await button.click();
      // A human command causes B's real isolated SDK automatic response. Its
      // text is not injected into a mocked event or snapshot.
      const ownToggle = a.page.getByRole("button", {
        name: "Reply for me",
        exact: true,
      });
      if ((await ownToggle.getAttribute("aria-pressed")) === "true")
        await ownToggle.click();
      const otherTwin = t.unique("other-twin");
      await sendMessage(a.page, `E2E ECHO ${otherTwin}`);
      await mode(a.page, "hidden");
      await waitMessage(
        b.page,
        chat,
        (message) =>
          message.kind === "twin" && message.body.includes(otherTwin),
      );
      await notification(a, otherTwin);
      const kinds = (await detail(a.page, chat)).messages
        .filter((message) =>
          [human, incoming, otherTwin].some((label) =>
            message.body.includes(label),
          ),
        )
        .map((message) => message.kind);
      assert.ok(
        kinds.includes("human") &&
          kinds.includes("reply_for_me") &&
          kinds.includes("twin"),
      );
      t.record("persisted_kinds", kinds);
      t.record(
        "notification_boundary",
        "controlled Notification constructor; real incoming message kinds",
      );
    },
  },
  {
    name: "notifications platform stub / initial history and reactions stay silent; reconnect catches a gap reply exactly once",
    run: async (t) => {
      const { recipient: a, peer: b, chat } = await recipientPair(t);
      await mode(a.page, "hidden");
      const label = t.unique("dedupe");
      const row = await sendMessage(b.page, `Reaction target ${label}`);
      await notification(a, label);
      const before = a.notices().length;
      const react = row.getByRole("button", { name: "React 👍", exact: true });
      await react.focus();
      await react.press("Enter");
      await poll(
        async () =>
          (await detail(a.page, chat)).messages.some(
            (message) =>
              message.body.includes(label) && message.reactions?.length,
          ),
        "real reaction projection",
      );
      await noNew(
        a,
        before,
        "reaction and unread refresh cannot replay last message",
      );
      await a.context.setOffline(true);
      await sleep(300);
      await a.context.setOffline(false);
      await settled();
      await noNew(
        a,
        before,
        "reconnect with the same canonical message is silent",
      );
      const slow = t.unique("gap");
      await privateChat(a.page);
      await sendMessage(a.page, `E2E SLOW ${slow}`);
      await mode(a.page, "hidden");
      await a.context.setOffline(true);
      await sleep(9000);
      await a.context.setOffline(false);
      await notification(a, slow);
      const current = a.notices().length;
      await a.context.setOffline(true);
      await sleep(300);
      await a.context.setOffline(false);
      await noNew(
        a,
        current,
        "another reconnect does not repeat the gap notification",
      );
      const initial = await probeSession(t);
      await enabled(initial.page);
      await mode(initial.page, "hidden");
      await settled();
      assert.equal(
        initial.notices().length,
        0,
        "new browser initial bootstrap seeds old messages",
      );
      t.record(
        "notification_boundary",
        "controlled Notification; actual browser offline/reconnect and real persisted completion",
      );
    },
  },
  {
    name: "notifications platform stub / a new conversation first message and attachment-only fallback notify",
    run: async (t) => {
      const a = await probeSession(t);
      await enabled(a.page);
      await mode(a.page, "hidden");
      await settled();
      const b = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const title = t.unique("notification-new-group");
      const chat = await startChat(b.page, ["AAA", "CCC"], title);
      const label = t.unique("new-chat");
      await sendMessage(b.page, `New conversation ${label}`);
      await notification(a, label);
      const before = a.notices().length;
      await b.page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles({
          name: `${label}.txt`,
          mimeType: "text/plain",
          buffer: Buffer.from("Isolated notification attachment"),
        });
      await poll(
        async () =>
          (await b.page.locator(".draft-attachment.ready").count()) === 1,
        "notification attachment uploaded",
      );
      await b.page.locator(".composer textarea").press("Enter");
      const attachment = await waitMessage(
        b.page,
        chat,
        (message) =>
          !message.body &&
          message.attachments?.some((file) => file.name === `${label}.txt`),
      );
      await poll(
        () => a.notices().length === before + 1,
        "attachment-only Notification fallback",
      );
      const last = a.notices().at(-1);
      assert.ok(last.options.body && !last.options.body.includes("undefined"));
      t.record("attachment_message_id", attachment.id);
      t.record("attachment_notification_body", last.options.body);
      t.record(
        "notification_boundary",
        "controlled Notification; actual new chat and uploaded attachment",
      );
    },
  },
  {
    name: "notifications platform stub / tabs aggregate once and logout or owner switch cannot leak notifications",
    run: async (t) => {
      const { recipient: a, peer: b, chat } = await recipientPair(t);
      const second = await a.context.newPage();
      await second.goto(`${t.baseURL}/#/chat`);
      await second
        .getByRole("button", { name: "Account", exact: true })
        .waitFor();
      await mode(a.page, "hidden");
      await mode(second, "hidden");
      await settled();
      const label = t.unique("two-tabs");
      await sendMessage(b.page, `Two tabs ${label}`);
      await notification(a, label);
      await sleep(500);
      assert.equal(
        a.notices().filter((notice) => JSON.stringify(notice).includes(label))
          .length,
        1,
      );
      assert.equal(
        await a.page.evaluate(() => typeof navigator.locks?.request),
        "function",
        "this exact-once assertion uses available Web Locks",
      );
      await second.close();
      await mode(a.page, "focused");
      await logout(a.page);
      const before = a.notices().length;
      await sendMessage(b.page, `Logged out ${t.unique("logout")}`);
      await noNew(
        a,
        before,
        "logged-out owner has no live notification observer",
      );
      await login(a.page, "DEV-CCC");
      await mode(a.page, "hidden");
      await settled();
      await sendMessage(b.page, `Wrong owner ${t.unique("switch")}`);
      await noNew(a, before, "CCC cannot receive old AAA pair notifications");
      t.record(
        "notification_boundary",
        "controlled Notification; same-origin multi-tab claim and real UI account lifecycle",
      );
    },
  },
  {
    name: "notifications native Chrome smoke / real permission and original constructor under controlled background",
    run: async (t) => {
      const a = await probeSession(t, { native: true });
      await enabled(a.page);
      assert.equal(
        await a.page.evaluate(() => Notification.permission),
        "granted",
      );
      const chat = await privateChat(a.page);
      const label = t.unique("native");
      await sendMessage(a.page, `E2E SLOW ${label}`);
      await mode(a.page, "blurred");
      const seen = await notification(a, label);
      assert.equal(seen.length, 1);
      assert.equal(seen[0].native, true);
      assert.equal(seen[0].focused, false);
      assert.equal(seen[0].options.tag, `twinteams:owner-alex:${chat.id}`);
      await waitMessage(
        a.page,
        chat,
        (message) => message.kind === "twin" && message.body.includes(label),
      );
      t.record("native_smoke", {
        permission: "real Chrome grant",
        controlled_background: true,
        actual_document_blur_verified: false,
        native_constructor_called: true,
        actual_OS_toast_verified: false,
        sound_verified: false,
        auto_close_ms: 300,
      });
    },
  },
];
module.exports = { cases };
