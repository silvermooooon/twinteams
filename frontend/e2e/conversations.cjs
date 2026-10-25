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
  openTwinTab,
} = require("./helpers.cjs");
const fs = require("node:fs/promises");
const { promisify } = require("node:util");
const { execFile, execFileSync } = require("node:child_process");
const path = require("node:path");
const execute = promisify(execFile);
function inspectFixture(operation = "inspect") {
  const result = JSON.parse(
    execFileSync(
      process.env.TWINTEAMS_E2E_PYTHON,
      [
        path.resolve(__dirname, "../../backend/checks/browser_e2e_fixture.py"),
        operation,
        "--state",
        process.env.TWINTEAMS_E2E_STATE,
      ],
      { encoding: "utf8" },
    ),
  );
  assert.equal(result.browser_e2e, true);
  assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return result;
}

async function detail(page, chat) {
  return api(page, `/chats/${chat.id}?limit=100`);
}
async function waitMessage(page, chat, match, label = "persisted message") {
  return poll(
    async () => (await detail(page, chat)).messages.find(match),
    label,
  );
}
async function startChat(page, names, title) {
  const owner = (await api(page, "/auth/me")).current_owner_id;
  const peers = [];
  for (const name of names) {
    const person = (
      await api(page, `/people?q=${encodeURIComponent(name)}`)
    ).find((actor) => actor.kind === "human" && actor.name === name);
    assert.ok(person, "recipient exists");
    peers.push(person.id);
  }
  const members = [owner, ...peers].sort().join("|");
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  for (const name of names) {
    const input = page
      .locator(".people-picker-inline")
      .getByRole("combobox", { name: "Search people", exact: true });
    await input.fill(name);
    await page.getByRole("option", { name, exact: true }).click();
  }
  if (title)
    await page.getByLabel("Group name (optional)", { exact: true }).fill(title);
  await page.getByRole("button", { name: "Start chat", exact: true }).click();
  await page.locator(".new-chat-main").waitFor({ state: "hidden" });
  await page.locator(".composer textarea").waitFor();
  return poll(
    async () =>
      (await api(page, "/bootstrap")).chats.find(
        (chat) =>
          chat.kind === "group" &&
          (title
            ? chat.title === title
            : [...chat.member_owner_ids].sort().join("|") === members),
      ),
    "created chat",
  );
}
async function workspace(page) {
  if (!(await page.locator(".detail-sidebar").isVisible()))
    await page
      .getByRole("button", { name: "Toggle sidebar", exact: true })
      .click();
  await page.getByRole("tab", { name: "Workspace", exact: true }).click();
  await page
    .getByRole("button", { name: "Refresh workspace", exact: true })
    .waitFor();
}
const cases = [
  {
    name: "auth / anonymous and invalid login are rejected; UI login/logout changes the real session",
    run: async (t) => {
      const { page } = await t.session(null);
      await api(page, "/bootstrap", { status: 401 });
      await page.getByPlaceholder("DEV-AAA").fill("E2E-NOT-A-USER");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await page.locator(".login-error[role=alert]").waitFor();
      await api(page, "/auth/me", { status: 401 });
      const auth = await login(page);
      assert.equal(auth.current_owner_id, "owner-alex");
      await logout(page);
      await api(page, "/auth/me", { status: 401 });
    },
  },
  {
    name: "private / local echo converges exactly once through the worker and survives reload",
    run: async (t) => {
      const { page } = await t.session();
      const chat = await privateChat(page);
      const label = t.unique("echo"),
        body = `E2E ECHO ${label}`;
      await page.evaluate(() => {
        window.__e2eEcho = false;
        const observer = new MutationObserver((records) => {
          for (const record of records)
            for (const node of record.addedNodes)
              if (
                node.nodeType === 1 &&
                (node.matches("article[data-local-send]") ||
                  node.querySelector("article[data-local-send]"))
              )
                window.__e2eEcho = true;
        });
        observer.observe(document.querySelector(".message-area"), {
          childList: true,
          subtree: true,
        });
      });
      await sendMessage(page, body, { waitCanonical: false });
      const human = await waitMessage(
        page,
        chat,
        (m) => m.kind === "human" && m.body === body,
      );
      await page.locator(`article[data-message-id="${human.id}"]`).waitFor();
      assert.equal(
        await page.evaluate(() => window.__e2eEcho),
        true,
        "a sender-local echo was actually inserted",
      );
      const reply = await waitMessage(
        page,
        chat,
        (m) => m.kind === "twin" && m.body.includes(`E2E echo: ${label}`),
        "SDK worker response",
      );
      await page.locator(`article[data-message-id="${reply.id}"]`).waitFor();
      assert.equal(
        (await detail(page, chat)).messages.filter((m) => m.body === body)
          .length,
        1,
      );
      assert.equal(
        await page.locator(`article[data-message-id="${human.id}"]`).count(),
        1,
      );
      await page.reload();
      await page.locator(`article[data-message-id="${human.id}"]`).waitFor();
      assert.equal(
        (await detail(page, chat)).messages.filter((m) => m.body === body)
          .length,
        1,
      );
      await page.screenshot({
        path: path.join(t.artifactDir, "private-success.png"),
        fullPage: true,
      });
      t.record("success_screenshot", "private-success.png");
      t.record("chat_id", chat.id);
      t.record("human_message_id", human.id);
      t.record("worker_reply_id", reply.id);
    },
  },
  {
    name: "composer / Shift+Enter and real clipboard paste preserve text; Unicode HTTP payload is not truncated",
    run: async (t) => {
      const { page } = await t.session();
      const chat = await privateChat(page),
        label = t.unique("paste");
      const input = page.locator(".composer textarea");
      await input.fill(`E2E ECHO ${label}`);
      await input.press("Shift+Enter");
      await page.evaluate(
        (text) => navigator.clipboard.writeText(text),
        "Pasted Unicode 正文",
      );
      await input.press(process.platform === "darwin" ? "Meta+V" : "Control+V");
      await poll(
        async () => (await input.inputValue()).includes("Pasted Unicode 正文"),
        "actual clipboard paste",
      );
      const pasted = await input.inputValue();
      assert.ok(pasted.includes("\n"));
      await input.press("Enter");
      await waitMessage(page, chat, (m) => m.body === pasted);
      const longBody = `E2E ECHO ${t.unique("long")}\n${'长文🙂 "quoted"\n'.repeat(3000)}end`;
      assert.ok(longBody.length > 30000);
      const [transport] = await Promise.all([
        page.waitForResponse(
          (response) =>
            response.request().method() === "POST" &&
            /\/api\/chats\/[^/]+\/messages$/.test(
              new URL(response.url()).pathname,
            ) &&
            response.request().postDataJSON()?.body === longBody,
        ),
        sendMessage(page, longBody),
      ]);
      assert.equal(transport.status(), 200);
      t.record("long_message_transport", "real HTTP POST");
      assert.equal(
        (
          await waitMessage(
            page,
            chat,
            (m) => m.kind === "human" && m.body === longBody,
          )
        ).body,
        longBody,
      );
      t.record("unicode_characters", longBody.length);
    },
  },
  {
    name: "markdown / human and worker rich text render while HTML, unsafe links and image fetches stay inert",
    run: async (t) => {
      const { page } = await t.session();
      const chat = await privateChat(page),
        label = t.unique("markdown");
      const externalRequests = [];
      page.on("request", (request) => {
        if (new URL(request.url()).hostname === "e2e-untrusted.invalid")
          externalRequests.push(request.url());
      });
      const body = `E2E MARKDOWN ${label}\n\n### Human heading\n\n**Bold human** and ~~struck~~\n\n<script>window.__e2eXss=true</script>\n\n[unsafe](javascript:alert(1))\n\n![No implicit fetch](https://e2e-untrusted.invalid/pixel.png)`;
      const row = await sendMessage(page, body);
      await row
        .getByRole("heading", { name: "Human heading", exact: true })
        .waitFor();
      assert.equal(
        await row.locator("strong").filter({ hasText: "Bold human" }).count(),
        1,
      );
      assert.equal(
        await row.locator('script, a[href^="javascript:"]').count(),
        0,
      );
      assert.equal(await page.evaluate(() => window.__e2eXss), undefined);
      assert.deepEqual(externalRequests, []);
      const reply = await waitMessage(
        page,
        chat,
        (m) => m.kind === "twin" && m.body.includes(label),
      );
      const twin = page.locator(`article[data-message-id="${reply.id}"]`);
      await twin.locator("strong").first().waitFor();
      assert.ok(
        (await twin.locator("pre code, table, ul").count()) > 0,
        "worker Markdown semantic elements",
      );
    },
  },
  {
    name: "people / UI direct-chat deduplication and real second-owner delivery, quotes and reactions",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const pair = await startChat(alex.page, ["BBB"]);
      const again = await startChat(alex.page, ["BBB"]);
      assert.equal(
        again.id,
        pair.id,
        "direct chat is replay-safe by participant pair",
      );
      assert.equal(
        (await api(alex.page, "/bootstrap")).chats.filter(
          (c) =>
            c.kind === "group" &&
            c.member_owner_ids.length === 2 &&
            c.member_owner_ids.includes("owner-maya"),
        ).length,
        1,
      );
      assert.equal(
        (await startChat(maya.page, ["AAA"])).id,
        pair.id,
        "the other owner reuses the same direct chat on API two",
      );
      const body = `Human delivery ${t.unique("pair")}`;
      const row = await sendMessage(alex.page, body);
      const human = await waitMessage(maya.page, pair, (m) => m.body === body);
      await maya.page
        .locator(`article[data-message-id="${human.id}"]`)
        .waitFor();
      const peerRow = maya.page.locator(
        `article[data-message-id="${human.id}"]`,
      );
      await peerRow.hover();
      const replyAction = peerRow.getByRole("button", {
        name: "Reply",
        exact: true,
      });
      await replyAction.focus();
      await replyAction.press("Enter");
      await sendMessage(maya.page, `Quoted ${body}`);
      const quoted = await waitMessage(
        alex.page,
        pair,
        (m) => m.body === `Quoted ${body}`,
      );
      assert.equal(quoted.reply_to?.id, human.id);
      const reactAction = row.getByRole("button", {
        name: "React 👍",
        exact: true,
      });
      await reactAction.focus();
      await reactAction.press("Enter");
      await poll(
        async () =>
          (await detail(maya.page, pair)).messages
            .find((m) => m.id === human.id)
            ?.reactions?.some((r) => r.emoji === "👍" && r.count === 1),
        "persisted reaction on the other API",
      );
      await row.locator(".reaction-chip > button").first().click();
      await poll(
        async () =>
          !(await detail(maya.page, pair)).messages.find(
            (m) => m.id === human.id,
          )?.reactions?.length,
        "reaction removal",
      );
      const config = await api(alex.page, "/config");
      assert.equal(
        config.automatic_response_delay_seconds,
        10,
        "use the actual product timeout",
      );
      await maya.page.bringToFront();
      // Reading the source now suppresses its automatic reply. Stay genuinely
      // online elsewhere so this workflow tests the unread ten-second fallback.
      await openTwinTab(maya.page, 'Profile');
      await sleep(4300);
      const presence = await poll(() => {
        const value = inspectFixture("presence");
        return value.owners["owner-maya"]?.online && value;
      }, "actual live Redis presence before online send");
      t.record("online_presence_before_send", presence);
      const online = t.unique("online"),
        began = Date.now();
      await sendMessage(alex.page, `E2E ECHO ${online}`);
      await maya.page.bringToFront();
      await sleep(7800);
      const unreadSource = (await detail(alex.page, pair)).messages.find(
        (m) => m.kind === 'human' && m.body === `E2E ECHO ${online}`,
      );
      assert.ok(unreadSource && !unreadSource.read_by.includes('owner-maya'),
        'the online recipient has not read this new source message');
      assert.ok(
        !(await detail(alex.page, pair)).messages.some(
          (m) => m.kind === "twin" && m.body.includes(`E2E echo: ${online}`),
        ),
        "an online human gets the real response window",
      );
      await waitMessage(
        alex.page,
        pair,
        (m) => m.kind === "twin" && m.body.includes(`E2E echo: ${online}`),
      );
      const onlineMs = Date.now() - began;
      assert.ok(onlineMs >= 9500);
      await logout(maya.page);
      // Closed/logged-out devices expire naturally; do not simulate offline by rewriting presence.
      await sleep((config.presence_ttl_seconds + 2) * 1000);
      const offlinePresence = inspectFixture("presence");
      assert.equal(
        offlinePresence.owners["owner-maya"]?.online || false,
        false,
        "Maya devices really expired before offline send",
      );
      t.record("offline_presence_before_send", offlinePresence);
      const offline = t.unique("offline-peer"),
        offlineAt = Date.now();
      await sendMessage(alex.page, `E2E ECHO ${offline}`);
      await poll(
        async () =>
          (await detail(alex.page, pair)).messages.some(
            (m) => m.kind === "twin" && m.body.includes(`E2E echo: ${offline}`),
          ),
        "offline target replies without the ten-second gate",
        { timeout: 9000 },
      );
      t.record("automatic_fallback", {
        online_window_ms: onlineMs,
        offline_ms: Date.now() - offlineAt,
        product_delay_seconds: 10,
      });
    },
  },
  {
    name: "group / create and rename a multi-person chat, then send an owner-initiated Reply for me with its public disclosure",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const title = t.unique("group");
      const group = await startChat(alex.page, ["BBB", "CCC"], title);
      const renamed = `${title}-renamed`;
      await alex.page
        .getByRole("button", { name: "Rename group", exact: true })
        .click();
      await alex.page
        .getByRole("textbox", { name: "Group name", exact: true })
        .fill(renamed);
      await alex.page
        .getByRole("dialog")
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await poll(
        async () =>
          (await api(maya.page, "/bootstrap")).chats.some(
            (c) => c.id === group.id && c.title === renamed,
          ),
        "group rename visible on second API",
      );
      await maya.page.reload();
      await chooseChat(maya.page, renamed);
      const toggle = alex.page.getByRole("button", {
        name: "Reply for me",
        exact: true,
      });
      await toggle.click();
      assert.equal(await toggle.getAttribute("aria-pressed"), "true");
      const raw = `E2E ECHO ${t.unique("rewrite")}`;
      await alex.page.locator(".composer textarea").fill(raw);
      await alex.page.locator(".composer textarea").press("Enter");
      const published = await waitMessage(
        alex.page,
        group,
        (m) => m.kind === "reply_for_me" && m.raw_owner_input === raw,
        "generated Reply for me",
      );
      await maya.page
        .locator(`article[data-message-id="${published.id}"]`)
        .waitFor();
      const other = (await detail(maya.page, group)).messages.find(
        (m) => m.id === published.id,
      );
      assert.equal(
        other.raw_owner_input,
        raw,
        "published rewrite retains the existing explicit raw-input disclosure",
      );
      assert.equal(
        (await detail(alex.page, group)).messages.filter(
          (m) => m.kind === "human" && m.body === raw,
        ).length,
        0,
      );
      t.record("group_id", group.id);
    },
  },
  {
    name: "files / all-type and image-only uploads persist, preview/download, and deny another owner",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const chat = await privateChat(alex.page),
        label = t.unique("files");
      const png = Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGO0CdjCwMDAxAAGAA6yAUTVYc/JAAAAAElFTkSuQmCC",
        "base64",
      );
      const files = [
        { name: `${label}.png`, mimeType: "image/png", buffer: png },
        {
          name: `${label}.pdf`,
          mimeType: "application/pdf",
          buffer: Buffer.from("%PDF-1.7 synthetic E2E document"),
        },
        {
          name: `${label}.csv`,
          mimeType: "text/csv",
          buffer: Buffer.from("name,value\nE2E,1\n"),
        },
        {
          name: `${label}.zip`,
          mimeType: "application/zip",
          buffer: Buffer.from("Synthetic archive bytes"),
        },
      ];
      await alex.page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles(files);
      await poll(
        async () =>
          (await alex.page.locator(".draft-attachment.ready").count()) === 4,
        "four real HTTP uploads ready",
      );
      assert.equal(
        await alex.page
          .getByText("Not supported by the model", { exact: true })
          .count(),
        0,
      );
      await alex.page
        .getByRole("button", { name: "Send message", exact: true })
        .click();
      const message = await waitMessage(alex.page, chat, (m) =>
        m.attachments?.some((a) => a.name === files[0].name),
      );
      assert.equal(message.body, "");
      assert.equal(message.attachments.length, 4);
      assert.equal(
        message.attachments.find((item) => item.name === files[0].name).kind,
        "image",
      );
      const row = alex.page.locator(`article[data-message-id="${message.id}"]`);
      await row.waitFor();
      const [download] = await Promise.all([
        alex.page.waitForEvent("download"),
        row.getByRole("link", { name: files[3].name, exact: true }).click(),
      ]);
      assert.equal(download.suggestedFilename(), files[3].name);
      assert.deepEqual(
        await fs.readFile(await download.path()),
        files[3].buffer,
      );
      await row.getByTitle(`Preview ${files[0].name}`, { exact: true }).click();
      await poll(
        () =>
          alex.page
            .getByRole("dialog")
            .locator("img")
            .evaluate((img) => img.complete && img.naturalWidth > 0),
        "authorized image preview bytes",
      );
      await alex.page
        .getByRole("dialog")
        .getByRole("button", { name: "Close", exact: true })
        .click();
      for (const attachment of message.attachments) {
        const denied = await maya.page.request.get(
          new URL(attachment.url, t.secondaryURL).href,
        );
        assert.ok(
          [403, 404].includes(denied.status()),
          "another owner cannot fetch private attachments",
        );
      }
      const deniedChat = await maya.page.request.get(
        `${t.secondaryURL}/api/chats/${chat.id}`,
      );
      assert.ok([403, 404].includes(deniedChat.status()));
      await alex.page.reload();
      await alex.page
        .locator(`article[data-message-id="${message.id}"]`)
        .waitFor();
      const imageOnly = {
        name: `${label}-only.png`,
        mimeType: "image/png",
        buffer: png,
      };
      await alex.page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles(imageOnly);
      await poll(
        async () =>
          (await alex.page.locator(".draft-attachment.ready").count()) === 1,
        "image-only upload ready",
      );
      await alex.page
        .getByRole("button", { name: "Send message", exact: true })
        .click();
      await waitMessage(
        alex.page,
        chat,
        (m) =>
          m.body === "" &&
          m.attachments?.some((a) => a.name === imageOnly.name),
      );
      const shortcut = await alex.page.evaluate(() =>
        navigator.platform.includes("Mac") ? "Alt+Meta+O" : "Alt+Shift+O",
      );
      await alex.page.locator(".composer textarea").focus();
      await alex.page.keyboard.press(shortcut);
      await alex.page
        .getByRole("menuitem", { name: "Upload from this device", exact: true })
        .waitFor();
      await alex.page.keyboard.press("Escape");
      const dropped = `${label}-dropped.bin`,
        pasted = `${label}-pasted.txt`;
      const transfer = await alex.page.evaluateHandle((name) => {
        const data = new DataTransfer();
        data.items.add(
          new File(["E2E dropped file"], name, {
            type: "application/octet-stream",
          }),
        );
        return data;
      }, dropped);
      await alex.page
        .locator(".chat-main")
        .dispatchEvent("drop", { dataTransfer: transfer });
      await transfer.dispose();
      await alex.page.locator(".composer textarea").evaluate((node, name) => {
        const data = new DataTransfer();
        data.items.add(
          new File(["E2E pasted file"], name, { type: "text/plain" }),
        );
        node.dispatchEvent(
          new ClipboardEvent("paste", {
            bubbles: true,
            cancelable: true,
            clipboardData: data,
          }),
        );
      }, pasted);
      await poll(
        async () =>
          (await alex.page.locator(".draft-attachment.ready").count()) === 2,
        "DOM drag/drop and file paste hit actual upload endpoints",
      );
      await alex.page
        .getByRole("button", { name: "Send message", exact: true })
        .click();
      const handlers = await waitMessage(alex.page, chat, (m) =>
        m.attachments?.some((a) => a.name === dropped),
      );
      assert.ok(handlers.attachments.some((a) => a.name === pasted));
      t.record(
        "types",
        files.map((file) => file.mimeType),
      );
      t.record("private_attachment_acl", true);
      t.record("upload_handlers", {
        picker: true,
        shortcut: true,
        drag_drop: "DOM DataTransfer event",
        file_paste: "DOM ClipboardEvent; not an OS file clipboard",
      });
    },
  },
  {
    name: "workspace / real SDK file output and uploaded directories are listed lazily and downloadable",
    run: async (t) => {
      const { page } = await t.session();
      const chat = await privateChat(page),
        label = t.unique("output"),
        name = `e2e-${label}.txt`;
      const uploadName = `${label}-input.txt`;
      await page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles({
          name: uploadName,
          mimeType: "text/plain",
          buffer: Buffer.from("E2E workspace setup " + label),
        });
      await poll(
        async () =>
          (await page.locator(".draft-attachment.ready").count()) === 1,
        "workspace case own upload",
      );
      await page
        .getByRole("button", { name: "Send message", exact: true })
        .click();
      const uploaded = await waitMessage(page, chat, (m) =>
        m.attachments?.some((a) => a.name === uploadName),
      );
      await sendMessage(page, `E2E FILE ${label}`);
      await waitMessage(
        page,
        chat,
        (m) => m.kind === "twin" && m.body.includes(`E2E file ready: ${name}`),
        "SDK write tool and final output",
      );
      await workspace(page);
      await page
        .getByRole("button", { name: "Refresh workspace", exact: true })
        .click();
      await page.getByRole("button", { name, exact: true }).click();
      const preview = page.getByRole("dialog").locator(".text-file-preview");
      await poll(
        async () => (await preview.innerText()).includes(label),
        "workspace file preview content loaded",
      );
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        page
          .getByRole("dialog")
          .getByRole("link", { name: "Download", exact: true })
          .click(),
      ]);
      assert.equal(download.suggestedFilename(), name);
      assert.ok(
        (await fs.readFile(await download.path(), "utf8")).includes(label),
      );
      await page
        .getByRole("dialog")
        .getByRole("button", { name: "Close", exact: true })
        .click();
      const uploads = page.getByRole("button", {
        name: "uploads",
        exact: true,
      });
      await uploads.click();
      const upload = uploaded.attachments.find(
        (item) => item.name === uploadName,
      );
      const folder = upload.workspace_path.split("/uploads/")[1].split("/")[0];
      await page.getByRole("button", { name: folder, exact: true }).click();
      await page
        .getByRole("button", { name: uploadName, exact: true })
        .waitFor();
      t.record("generated_file", name);
    },
  },
  {
    name: "requests / queued cancellation and real runtime Stop preserve messages; failure dismissal and Retry recover",
    run: async (t) => {
      const { page } = await t.session();
      const chat = await privateChat(page),
        slow = t.unique("slow"),
        queued = t.unique("queued");
      await sendMessage(page, `E2E SLOW ${slow}`);
      await page.locator(".typing-row[data-reply-phase=running]").waitFor();
      await sendMessage(page, `E2E SLOW ${queued}`);
      await page
        .locator(".owner-queue")
        .getByRole("button", { name: "Cancel this request", exact: true })
        .click();
      await poll(async () => {
        const state = await detail(page, chat);
        return !state.requests.some(
          (r) =>
            r.source_message_id ===
            state.messages.find((m) => m.body === `E2E SLOW ${queued}`)?.id,
        );
      }, "queued cancellation");
      await page
        .locator(".typing-row")
        .getByRole("button", { name: "Stop replying", exact: true })
        .click();
      await page.locator(".typing-row").waitFor({ state: "hidden" });
      const label = t.unique("fail");
      await sendMessage(page, `E2E FAIL_ONCE ${label}`);
      const failed = await poll(async () => {
        const state = await detail(page, chat);
        return state.requests.find(
          (r) =>
            r.status === "failed" &&
            r.source_message_id ===
              state.messages.find((m) => m.body === `E2E FAIL_ONCE ${label}`)
                ?.id,
        );
      }, "actual SDK failure");
      await page
        .locator(`[data-failed-request="${failed.id}"]`)
        .getByRole("button", { name: "Dismiss", exact: true })
        .click();
      await page.reload();
      await page.locator(".composer textarea").waitFor();
      assert.equal(
        await page.locator(`[data-failed-request="${failed.id}"]`).count(),
        0,
      );
      const next = t.unique("retry");
      await sendMessage(page, `E2E FAIL_ONCE ${next}`);
      const retry = await poll(async () => {
        const state = await detail(page, chat);
        return state.requests.find(
          (r) =>
            r.status === "failed" &&
            r.source_message_id ===
              state.messages.find((m) => m.body === `E2E FAIL_ONCE ${next}`)
                ?.id,
        );
      }, "new failure remains visible");
      await page
        .locator(`[data-failed-request="${retry.id}"]`)
        .getByRole("button", { name: "Retry", exact: true })
        .click();
      await waitMessage(
        page,
        chat,
        (m) =>
          m.kind === "twin" && m.body.includes(`E2E retry succeeded: ${next}`),
        "retry SDK success",
      );
      await page.reload();
      await page.locator(".composer textarea").waitFor();
      assert.equal(
        await page.locator(`[data-failed-request="${retry.id}"]`).count(),
        0,
        "superseded failure does not reappear after recovery",
      );
      assert.ok(
        (await detail(page, chat)).messages.some(
          (m) => m.body === `E2E SLOW ${slow}`,
        ),
        "Stop did not remove the human message",
      );
      t.record("dismissed_request", failed.id);
      t.record("recovered_request", retry.id);
    },
  },
  {
    name: "recovery / browser offline reconnection and in-flight reload converge through the second API",
    run: async (t) => {
      const { context, page } = await t.session();
      const chat = await privateChat(page),
        label = t.unique("offline");
      await context.setOffline(true);
      assert.equal(
        await page.evaluate(async () => {
          try {
            await fetch("/api/bootstrap");
            return false;
          } catch {
            return true;
          }
        }),
        true,
        "the browser network is actually offline",
      );
      await sendMessage(page, `E2E ECHO ${label}`, { waitCanonical: false });
      await context.setOffline(false);
      await waitMessage(
        page,
        chat,
        (m) => m.kind === "human" && m.body === `E2E ECHO ${label}`,
      );
      await waitMessage(
        page,
        chat,
        (m) => m.kind === "twin" && m.body.includes(`E2E echo: ${label}`),
      );
      assert.equal(
        (await detail(page, chat)).messages.filter(
          (m) => m.body === `E2E ECHO ${label}`,
        ).length,
        1,
      );
      const inflight = t.unique("reload");
      await sendMessage(page, `E2E SLOW ${inflight}`);
      await page.locator(".typing-row[data-reply-phase=running]").waitFor();
      const current = await detail(page, chat),
        source = current.messages.find(
          (m) => m.body === `E2E SLOW ${inflight}`,
        ),
        request = current.requests.find(
          (r) => r.source_message_id === source.id,
        );
      assert.ok(
        request?.run_id,
        "request is bound to a real SDK run before reload",
      );
      await page.reload();
      await page.locator(".composer textarea").waitFor();
      const second = await t.session("DEV-AAA", { baseURL: t.secondaryURL });
      await privateChat(second.page);
      await poll(
        async () =>
          (await detail(second.page, chat)).messages.some(
            (m) => m.kind === "twin" && m.request_id === request.id,
          ),
        "one persisted SDK completion on secondary API",
        { timeout: 25000 },
      );
      const final = await detail(second.page, chat);
      assert.equal(
        final.messages.filter((m) => m.body === `E2E SLOW ${inflight}`).length,
        1,
      );
      assert.equal(
        final.messages.filter(
          (m) => m.kind === "twin" && m.request_id === request.id,
        ).length,
        1,
      );
      t.record("reloaded_request", request.id);
      t.record("second_api_recovered", true);
    },
  },

  {
    name: "agents / asynchronous child completion resumes the finished parent and projects real Activity",
    run: async (t) => {
      const { page } = await t.session();
      const chat = await privateChat(page),
        label = t.unique("delegate");
      await sendMessage(page, `E2E DELEGATE ${label}`);
      const first = await waitMessage(
        page,
        chat,
        (m) => m.kind === "twin" && m.body.includes(`E2E delegated: ${label}`),
        "parent finished without waiting for child",
      );
      const resumed = await waitMessage(
        page,
        chat,
        (m) =>
          m.kind === "twin" &&
          m.body.includes(`E2E delegation reviewed: ${label}`),
        "child result resumed parent",
      );
      assert.ok(first.seq < resumed.seq);
      const rows = inspectFixture();
      assert.ok(
        rows.tools.some((tool) => tool.name === "spawn_agent" && tool.success),
      );
      assert.ok(
        rows.requests.some(
          (request) =>
            request.id === resumed.request_id &&
            request.source === "subagent_result",
        ),
      );
      if (!(await page.locator(".detail-sidebar").isVisible()))
        await page
          .getByRole("button", { name: "Toggle sidebar", exact: true })
          .click();
      await page.getByRole("tab", { name: "Activity", exact: true }).click();
      const runs = await api(page, `/chats/${chat.id}/activity`);
      const child = runs.items.find(
        (run) =>
          run.parent_run_id && run.finished_at && run.title.includes(label),
      );
      assert.ok(child, "completed child is a real projected Activity run");
      assert.ok(
        Date.parse(first.created_at) < Date.parse(child.finished_at),
        "parent message committed before the child finished on the same server clock",
      );
      t.record("asynchronous_server_timestamps", {
        parent_message_created_at: first.created_at,
        child_finished_at: child.finished_at,
      });
      await page.locator(".run-card").first().waitFor();
      t.record("parent_and_resume", { first: first.id, resumed: resumed.id });
    },
  },
  {
    name: "group resources / another participant invokes the Duet owner workspace and private Memory",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const own = await privateChat(alex.page),
        label = t.unique("identity");
      await sendMessage(alex.page, `E2E MEMORY ${label}`);
      await waitMessage(
        alex.page,
        own,
        (m) =>
          m.kind === "twin" && m.body.includes(`E2E memory saved: ${label}`),
      );
      const pair = await startChat(maya.page, ["AAA"]);
      await sendMessage(maya.page, `E2E IDENTITY ${label}`);
      const answer = await waitMessage(
        maya.page,
        pair,
        (m) =>
          m.kind === "twin" &&
          m.body.includes("E2E owner identity: owner-alex") &&
          m.body.includes("actual-memory-present=true"),
        "owner resource tools run in the group",
      );
      assert.ok(answer.body.includes("/workspaces/private-alex"));
      assert.ok(
        !answer.body.includes("E2E memory " + label),
        "private memory content is not copied into the group",
      );
      await api(maya.page, `/me/twin/memory/entry?key=e2e-${label}`, {
        status: 404,
      });
      const evidence = inspectFixture();
      const request = evidence.requests.find(
        (row) => row.id === answer.request_id,
      );
      assert.equal(request.target_twin_id, "twin-alex");
      assert.ok(
        evidence.tools.some(
          (tool) =>
            tool.name === "bash" && tool.success && tool.owner_cwd_verified,
        ),
      );
      assert.ok(
        evidence.tools.some(
          (tool) => tool.name === "memory_read" && tool.success,
        ),
      );
      t.record("owner_capabilities", {
        actual_owner: "owner-alex",
        invoker: "owner-maya",
        cwd: true,
        private_memory_present: true,
      });
    },
  },
  {
    name: "group delivery / Duet privately reports to its owner without copying private conversation into the group",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const own = await privateChat(maya.page),
        label = t.unique("owner-dm"),
        sentinel = `PRIVATE-ONLY-${label}`;
      await sendMessage(maya.page, sentinel);
      const pair = await startChat(alex.page, ["BBB"]);
      await sendMessage(alex.page, `E2E OWNER_DM ${label}`);
      const shared = await waitMessage(
        alex.page,
        pair,
        (m) =>
          m.kind === "twin" &&
          m.body.includes(`E2E owner report sent: ${label}`),
      );
      const report = await waitMessage(
        maya.page,
        own,
        (m) => m.kind === "twin" && m.body === `E2E owner report: ${label}`,
        "real authorized cross-chat tool delivery",
      );
      const state = await detail(alex.page, pair);
      assert.equal(
        state.messages.filter(
          (m) => m.kind === "twin" && m.request_id === shared.request_id,
        ).length,
        1,
        "one current-chat output",
      );
      assert.ok(!JSON.stringify(state.messages).includes(sentinel));
      assert.equal(
        (await detail(maya.page, own)).messages.filter(
          (m) => m.id === report.id,
        ).length,
        1,
      );
      const denied = await alex.page.request.get(
        `${t.baseURL}/api/chats/${own.id}`,
      );
      assert.ok([403, 404].includes(denied.status()));
      t.record("delivery", {
        group: pair.id,
        private_destination: own.id,
        exact_once: true,
        private_sentinel_absent_from_group: true,
      });
    },
  },
  {
    name: "automatic replies / six real acceptances pause and explicit owner Continue grants the same chain",
    timeout: 150000,
    run: async (t) => {
      const { page } = await t.session();
      const label = t.unique("chain"),
        group = await startChat(page, ["BBB", "CCC"], label);
      await sendMessage(page, `E2E CHAIN ${label}`);
      const paused = await poll(
        async () =>
          (await detail(page, group)).automatic_conversations?.find(
            (chain) => chain.reason === "budget_exhausted" && chain.spent === 6,
          ),
        "six-reply product budget pause",
        { timeout: 45000 },
      );
      const steps = (state) =>
        state.messages
          .filter(
            (m) =>
              m.kind === "twin" && m.body.startsWith(`E2E CHAIN ${label} `),
          )
          .map((m) => Number(m.body.split(" ").at(-1)))
          .sort((a, b) => a - b);
      assert.deepEqual(steps(await detail(page, group)), [1, 2, 3, 4, 5, 6]);
      await page
        .locator(".automatic-paused")
        .getByRole("button", {
          name: "Continue automatically · 6 more replies",
          exact: true,
        })
        .click();
      const continued = await poll(
        async () =>
          (await detail(page, group)).automatic_conversations?.find(
            (chain) =>
              chain.id === paused.id &&
              chain.spent === 12 &&
              chain.allowance === 12,
          ),
        "same chain extended only by explicit consent",
        { timeout: 45000 },
      );
      assert.equal(continued.id, paused.id);
      assert.deepEqual(
        steps(await detail(page, group)),
        Array.from({ length: 12 }, (_, i) => i + 1),
      );
      t.record("budget", {
        original: 6,
        additional: 6,
        accepted: 12,
        chain: paused.id,
      });
    },
  },
  {
    name: "history / UI no-prior grant hides old messages and attachments from a newly added member",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL }),
        jordan = await t.session("DEV-CCC");
      const pair = await startChat(alex.page, ["BBB"]),
        label = t.unique("history"),
        filename = `${label}.txt`;
      await alex.page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles({
          name: filename,
          mimeType: "text/plain",
          buffer: Buffer.from("Private old history " + label),
        });
      await poll(
        async () =>
          (await alex.page.locator(".draft-attachment.ready").count()) === 1,
        "historical attachment upload",
      );
      const row = await sendMessage(alex.page, `Old grant source ${label}`);
      const source = await waitMessage(
        alex.page,
        pair,
        (m) => m.body === `Old grant source ${label}`,
      );
      await alex.page.getByTitle("Add members", { exact: true }).click();
      const modal = alex.page.getByRole("dialog");
      await modal.getByRole("option", { name: "CCC", exact: true }).click();
      await modal
        .getByRole("combobox", { name: /^New member history/ })
        .selectOption("no_prior");
      await modal.getByRole("button", { name: "Add", exact: true }).click();
      await modal.waitFor({ state: "hidden" });
      const visible = await poll(
        async () =>
          (await api(jordan.page, "/bootstrap")).chats.find(
            (chat) => chat.id === pair.id,
          ),
        "new member can see the group metadata",
      );
      await jordan.page.reload();
      await chooseChat(jordan.page, visible.title);
      assert.ok(
        !(await detail(jordan.page, pair)).messages.some(
          (m) => m.id === source.id,
        ),
      );
      assert.equal(
        await jordan.page
          .locator(`article[data-message-id="${source.id}"]`)
          .count(),
        0,
      );
      const attachment = source.attachments.find(
        (item) => item.name === filename,
      );
      const denied = await jordan.page.request.get(
        new URL(attachment.url, t.baseURL).href,
      );
      assert.ok([403, 404].includes(denied.status()));
      assert.ok(
        (await detail(alex.page, pair)).messages.some(
          (m) => m.id === source.id,
        ),
      );
      assert.ok(
        (await detail(maya.page, pair)).messages.some(
          (m) => m.id === source.id,
        ),
      );
      await sendMessage(jordan.page, `New member visible ${label}`);
      await waitMessage(
        alex.page,
        pair,
        (m) => m.body === `New member visible ${label}`,
      );
      t.record("history_grant", {
        policy: "no_prior",
        old_member_access: true,
        new_member_old_attachment_denied: true,
      });
    },
  },

  {
    name: "recovery / graceful worker process restart resumes the checkpoint while the browser stays connected",
    timeout: 150000,
    run: async (t) => {
      const primary = await t.session(),
        second = await t.session("DEV-AAA", { baseURL: t.secondaryURL });
      const chat = await privateChat(primary.page),
        label = t.unique("worker-restart");
      await sendMessage(primary.page, `E2E SLOW ${label}`);
      await primary.page
        .locator(".typing-row[data-reply-phase=running]")
        .waitFor();
      const before = await detail(primary.page, chat),
        human = before.messages.find((m) => m.body === `E2E SLOW ${label}`),
        request = before.requests.find((r) => r.source_message_id === human.id);
      assert.ok(request?.run_id);
      const { stdout } = await execute(
        process.env.TWINTEAMS_E2E_PYTHON,
        [
          path.resolve(
            __dirname,
            "../../backend/checks/browser_e2e_fixture.py",
          ),
          "restart-worker",
          "--state",
          process.env.TWINTEAMS_E2E_STATE,
        ],
        { timeout: 25000 },
      );
      const restarted = JSON.parse(stdout);
      assert.equal(restarted.restarted, true);
      assert.ok(restarted.pid > 0);
      const completed = await poll(
        async () =>
          (await detail(second.page, chat)).messages.find(
            (m) => m.kind === "twin" && m.request_id === request.id,
          ),
        "same durable request completes after owned worker restart",
        { timeout: 110000 },
      );
      await primary.page
        .locator(`article[data-message-id="${completed.id}"]`)
        .waitFor();
      const after = await detail(second.page, chat);
      assert.equal(
        after.messages.filter((m) => m.body === human.body).length,
        1,
      );
      assert.equal(
        after.messages.filter(
          (m) => m.kind === "twin" && m.request_id === request.id,
        ).length,
        1,
      );
      t.record("worker_restart", {
        request_id: request.id,
        restart: "SIGTERM graceful process restart with checkpoint resumption",
        human_count: 1,
        committed_output_count: 1,
        provider_execution_exact_once_claimed: false,
      });
    },
  },
  {
    name: "mobile / 390px keyboard Escape, focus, empty-send protection and a canonical send",
    run: async (t) => {
      const { page } = await t.session("DEV-AAA", { width: 390 });
      const chat = await privateChat(page),
        label = t.unique("mobile"),
        input = page.locator(".composer textarea"),
        send = page.getByRole("button", { name: "Send message", exact: true });
      if (await page.locator(".detail-sidebar").isVisible())
        await page
          .locator(".detail-sidebar")
          .getByRole("button", { name: "Close", exact: true })
          .click();
      await input.fill("");
      assert.equal(await send.isDisabled(), true);
      await input.fill(`Mobile ${label}`);
      assert.equal(await send.isDisabled(), false);
      const menu = page.getByRole("button", {
        name: "More tools",
        exact: true,
      });
      await menu.click();
      await page.locator(".composer-popover").waitFor();
      await page.keyboard.press("Escape");
      await page.locator(".composer-popover").waitFor({ state: "hidden" });
      assert.equal(
        await menu.evaluate((node) => node === document.activeElement),
        true,
      );
      await input.focus();
      await input.press("Shift+Enter");
      await page.keyboard.insertText("Keyboard second line");
      const body = await input.inputValue();
      assert.ok(body.includes("\n"));
      await input.press("Enter");
      const stored = await waitMessage(page, chat, (m) => m.body === body);
      await page.locator(`article[data-message-id="${stored.id}"]`).waitFor();
      assert.equal(await input.inputValue(), "");
      assert.equal(
        await input.evaluate((node) => node === document.activeElement),
        true,
      );
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      await page.screenshot({
        path: path.join(t.artifactDir, "mobile-success.png"),
        fullPage: true,
      });
      t.record("success_screenshot", "mobile-success.png");
      t.record("viewport", {
        width: 390,
        keyboard_escape: true,
        empty_send_disabled: true,
      });
    },
  },
  {
    name: "tracking scopes / private union labels sources and writes to the originating group while groups stay isolated",
    run: async (t) => {
      const alex = await t.session(),
        maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL }),
        jordan = await t.session("DEV-CCC");
      const label = t.unique("scope");
      async function panel(page) {
        if (!(await page.locator(".detail-sidebar").isVisible()))
          await page
            .getByRole("button", { name: "Toggle sidebar", exact: true })
            .click();
        await page.getByRole("tab", { name: "Tracking", exact: true }).click();
      }
      async function create(page, chat, title) {
        const source = await sendMessage(page, `Scope source ${title}`);
        const action = source.getByTitle("Track this message", { exact: true });
        await action.focus();
        await action.press("Enter");
        const modal = page.locator('.modal[role="dialog"]');
        await modal.getByLabel("Topic", { exact: true }).fill(title);
        await modal
          .getByLabel("Description", { exact: true })
          .fill(`Ongoing scope work ${label}`);
        await modal.getByRole("button", { name: "Save", exact: true }).click();
        await modal.waitFor({ state: "hidden" });
        return poll(
          async () =>
            (await detail(page, chat)).tracking.find(
              (topic) => topic.title === title,
            ),
          "UI-created sourced topic",
        );
      }
      const ab = await startChat(alex.page, ["BBB"]);
      const first = await create(alex.page, ab, `AB ${label}`);
      const ac = await startChat(alex.page, ["CCC"]);
      const second = await create(alex.page, ac, `AC ${label}`);
      const own = await privateChat(alex.page);
      await panel(alex.page);
      const card = (title) =>
        alex.page.locator(".tracking-card").filter({
          has: alex.page.getByRole("heading", { name: title, exact: true }),
        });
      await card(first.title).waitFor();
      await card(second.title).waitFor();
      assert.equal(
        await card(first.title).getAttribute("data-tracking-chat-id"),
        ab.id,
      );
      assert.match(
        await card(first.title).locator(".tracking-source").innerText(),
        /^Group · /,
      );
      assert.equal(
        await card(second.title).getAttribute("data-tracking-chat-id"),
        ac.id,
      );
      await alex.page.screenshot({
        path: path.join(t.artifactDir, "tracking-private-union.png"),
        fullPage: true,
      });
      await startChat(maya.page, ["AAA"]);
      const watched = [];
      alex.page.on("request", (request) => {
        if (request.method() === "GET")
          watched.push(new URL(request.url()).pathname);
      });
      const input = alex.page.locator(".composer textarea");
      await input.fill(`Keep this draft ${label}`);
      await input.focus();
      const incoming = await create(maya.page, ab, `Live AB ${label}`);
      await card(incoming.title).waitFor();
      assert.equal(await input.inputValue(), `Keep this draft ${label}`);
      assert.equal(
        await input.evaluate((node) => node === document.activeElement),
        true,
      );
      assert.ok(
        watched.includes(`/api/chats/${own.id}/tracking`),
        "owner Tracking hint fetches the small union index",
      );
      assert.ok(
        !watched.includes("/api/bootstrap") &&
          !watched.includes(`/api/chats/${own.id}`),
        "external Tracking mutation does not reload the whole private chat or bootstrap",
      );
      await card(first.title).click();
      let drawer = alex.page.getByRole("dialog", {
        name: first.title,
        exact: true,
      });
      await drawer.locator(".tracking-source").waitFor();
      await drawer.getByRole("button", { name: "Edit", exact: true }).click();
      const modal = alex.page.locator('.modal[role="dialog"]'),
        renamed = `Edited AB ${label}`;
      await modal.getByLabel("Topic", { exact: true }).fill(renamed);
      await modal.getByRole("button", { name: "Save", exact: true }).click();
      await modal.waitFor({ state: "hidden" });
      await poll(
        async () =>
          (await api(alex.page, `/chats/${ab.id}/tracking/${first.id}`))
            .title === renamed,
        "private union edit reached original group",
      );
      drawer = alex.page.getByRole("dialog", { name: renamed, exact: true });
      await drawer
        .getByLabel("Add an update", { exact: true })
        .fill(`Actual group progress ${label}`);
      await drawer.getByRole("button", { name: "Add", exact: true }).click();
      await poll(
        async () =>
          (
            await api(maya.page, `/chats/${ab.id}/tracking/${first.id}`)
          ).updates.some(
            (update) => update.body === `Actual group progress ${label}`,
          ),
        "group update visible to other member",
      );
      await drawer.getByRole("button", { name: "Delete", exact: true }).click();
      await alex.page
        .locator('.modal[role="dialog"]')
        .getByRole("button", { name: "Delete", exact: true })
        .click();
      await poll(
        async () =>
          !(await detail(alex.page, ab)).tracking.some(
            (topic) => topic.id === first.id,
          ),
        "union delete tombstones the source group topic",
      );
      await card(renamed).waitFor({ state: "hidden" });
      await card(second.title).waitFor();
      await privateChat(maya.page);
      await panel(maya.page);
      await maya.page
        .locator(".tracking-card")
        .filter({ hasText: incoming.title })
        .waitFor();
      assert.equal(
        await maya.page
          .locator(".tracking-card")
          .filter({ hasText: second.title })
          .count(),
        0,
        "another owner's group is absent from private union",
      );
      await privateChat(jordan.page);
      await panel(jordan.page);
      await jordan.page
        .locator(".tracking-card")
        .filter({ hasText: second.title })
        .waitFor();
      assert.equal(
        await jordan.page
          .locator(".tracking-card")
          .filter({ hasText: incoming.title })
          .count(),
        0,
      );
      await input.fill("");
      const privateTopic = await create(alex.page, own, `Private ${label}`);
      assert.equal(
        privateTopic.chat_id,
        own.id,
        "New in private keeps the original private write scope",
      );
      await card(privateTopic.title).waitFor();
      assert.equal(
        await card(privateTopic.title).locator(".tracking-source").innerText(),
        "Private conversation",
      );
      await startChat(alex.page, ["CCC"]);
      await panel(alex.page);
      await card(second.title).waitFor();
      assert.equal(await card(incoming.title).count(), 0);
      assert.equal(
        await card(privateTopic.title).count(),
        0,
        "group panel only lists its own group",
      );
      t.record("scope", {
        private_chat: own.id,
        source_groups: [ab.id, ac.id],
        cross_owner_isolation: true,
        source_edit_update_delete: true,
        local_tracking_refresh_only: true,
      });
      await alex.page.screenshot({
        path: path.join(t.artifactDir, "tracking-group-scope.png"),
        fullPage: true,
      });
    },
  },
];
module.exports = { cases, startChat, workspace, detail, waitMessage };
