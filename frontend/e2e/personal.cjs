/** Real browser + product API/PG/Redis/worker/SDK. Only the provider is scripted. */
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const { api, openTwinTab, sendMessage, poll } = require("./helpers.cjs");

const button = (page, name) => page.getByRole("button", { name, exact: true });
const dialog = (page) => page.locator('.modal[role="dialog"]');
function inspectFixture() {
  const data = JSON.parse(
    execFileSync(
      process.env.TWINTEAMS_E2E_PYTHON,
      [
        path.resolve(__dirname, "../../backend/checks/browser_e2e_fixture.py"),
        "inspect",
        "--state",
        process.env.TWINTEAMS_E2E_STATE,
      ],
      { encoding: "utf8" },
    ),
  );
  assert.equal(data.browser_e2e, true);
  assert.equal(data.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return data;
}
async function chat(page) {
  if (await button(page, "Back to chats").count())
    await button(page, "Back to chats").click();
  else await page.goto(page.url().split("#")[0] + "#chat");
  await page.locator(".composer textarea").waitFor();
}
async function privateChat(page) {
  await chat(page);
  const data = await api(page, "/bootstrap");
  const own = data.chats.find((c) => c.kind === "private");
  assert.ok(own);
  const twin = data.actors.find(
    (a) => a.kind === "twin" && a.owner_id === data.current_owner_id,
  );
  await page
    .locator(".conversation-list .chat-item")
    .filter({ hasText: twin.name })
    .click();
  await page.locator(".composer textarea").waitFor();
  return own.id;
}
async function saved(page, path, expected) {
  return poll(async () => {
    const value = await api(page, path);
    return expected(value) ? value : false;
  }, "persisted " + path);
}
async function saveSoul(page, content) {
  await page.getByLabel("Soul content", { exact: true }).fill(content);
  await button(page, "Save").click();
  return saved(page, "/me/twin/soul", (v) => v.content === content);
}
async function addMemory(page, key, content) {
  await button(page, "Add memory").click();
  await dialog(page).getByLabel("Memory key", { exact: true }).fill(key);
  await dialog(page)
    .getByLabel("Memory content", { exact: true })
    .fill(content);
  await dialog(page).getByRole("button", { name: "Save", exact: true }).click();
  await dialog(page).waitFor({ state: "hidden" });
  return saved(
    page,
    "/me/twin/memory/entry?key=" + encodeURIComponent(key),
    (v) => v.content === content,
  );
}
async function openMemory(page, key) {
  await page
    .locator(".twin-memory-row")
    .filter({ has: page.getByRole("heading", { name: key, exact: true }) })
    .click();
  await dialog(page).waitFor();
}
async function marker(page, text) {
  await page
    .locator(".message-row.twin-message .message-text")
    .filter({ hasText: text })
    .waitFor({ timeout: 30000 });
}
async function activityContains(page, chatId, needle) {
  const activity = await api(page, "/chats/" + chatId + "/activity");
  for (const run of activity.items.slice(0, 12)) {
    const data = await api(
      page,
      `/chats/${chatId}/activity/${encodeURIComponent(run.session_id)}/${encodeURIComponent(run.run_id)}`,
    );
    if (JSON.stringify(data).includes(needle)) return true;
  }
  return false;
}
async function removeCredential(page, name) {
  await button(page, "Delete credential " + name).click();
  await dialog(page)
    .getByRole("button", { name: "Delete", exact: true })
    .click();
  await dialog(page).waitFor({ state: "hidden" });
}
async function noFixtureValue(page, value) {
  assert.ok(!(await page.locator("body").innerText()).includes(value));
  assert.equal(
    await page.evaluate((v) => JSON.stringify(localStorage).includes(v), value),
    false,
  );
  assert.ok(
    !JSON.stringify(await api(page, "/me/twin/credentials")).includes(value),
  );
  const bootstrap = await api(page, "/bootstrap");
  for (const c of bootstrap.chats) {
    assert.ok(
      !JSON.stringify(await api(page, "/chats/" + c.id)).includes(value),
    );
  }
}

const cases = [
  {
    name: "personal / profile, avatar, runtime and cross-API identity",
    run: async (t) => {
      const { page } = await t.session(),
        other = await t.session("DEV-AAA", { baseURL: t.secondaryURL });
      const original = (await api(page, "/me/twin")).actor;
      await openTwinTab(page, "Profile");
      const name = "E2E Twin " + t.unique("profile");
      await page.getByLabel("Twin name", { exact: true }).fill(name);
      await button(page, "Save").click();
      await saved(page, "/me/twin", (v) => v.actor.name === name);
      await button(page, "Choose avatar").click();
      await button(page, "Default avatar 3").click();
      await dialog(page)
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      await saved(page, "/me/twin", (v) => v.actor.avatar_preset === "wave");
      await page.reload();
      await page.getByLabel("Twin name", { exact: true }).waitFor();
      assert.equal(
        await page.getByLabel("Twin name", { exact: true }).inputValue(),
        name,
      );
      await poll(
        async () =>
          (await other.page
            .locator(".conversation-list .chat-item")
            .filter({ hasText: name })
            .count()) === 1,
        "live private title on second API",
      );
      await openTwinTab(page, "Runtime");
      await page.locator(".twin-worker-state.online").waitFor();
      const runtime = await api(page, "/me/twin/runtime");
      assert.equal(runtime.worker.online, true);
      assert.equal(runtime.memory.mode, "git");
      assert.equal(runtime.configured, true);
      assert.ok(runtime.api);
      const peer = await t.session("DEV-BBB");
      assert.notEqual((await api(peer.page, "/me/twin")).actor.id, original.id);
      assert.notEqual((await api(peer.page, "/me/twin")).actor.name, name);
      await openTwinTab(page, "Profile");
      await page.getByLabel("Twin name", { exact: true }).fill(original.name);
      await button(page, "Save").click();
      await saved(page, "/me/twin", (v) => v.actor.name === original.name);
      t.record("runtime", {
        api: runtime.api,
        model: runtime.model,
        online: true,
      });
    },
  },
  {
    name: "personal / Soul optimistic conflict, draft recovery and deletion",
    run: async (t) => {
      const { page, context } = await t.session();
      const label = t.unique("soul");
      await openTwinTab(page, "Soul");
      await saveSoul(page, "E2E Soul base " + label);
      const second = await context.newPage();
      await second.goto(t.baseURL + "/#/my-twin/soul");
      await second.getByLabel("Soul content", { exact: true }).waitFor();
      await page
        .getByLabel("Soul content", { exact: true })
        .fill("E2E Soul first " + label);
      await second
        .getByLabel("Soul content", { exact: true })
        .fill("E2E Soul preserved draft " + label);
      await button(page, "Save").click();
      await saved(
        page,
        "/me/twin/soul",
        (v) => v.content === "E2E Soul first " + label,
      );
      await second.locator(".twin-content-conflict").waitFor();
      assert.equal(
        await second.getByLabel("Soul content", { exact: true }).inputValue(),
        "E2E Soul preserved draft " + label,
      );
      await button(second, "Keep draft and use current version").click();
      await button(second, "Save").click();
      await saved(
        second,
        "/me/twin/soul",
        (v) => v.content === "E2E Soul preserved draft " + label,
      );
      await button(second, "Delete SOUL.md").click();
      await dialog(second)
        .getByRole("button", { name: "Delete SOUL.md", exact: true })
        .click();
      await dialog(second).waitFor({ state: "hidden" });
      assert.equal((await api(second, "/me/twin/soul")).version, null);
      await second.close();
    },
  },
  {
    name: "personal / Git Memory create, search, conflict, edit and delete",
    run: async (t) => {
      const { page, context } = await t.session();
      const key = "e2e-ui-" + t.unique("memory");
      await openTwinTab(page, "Memory");
      const created = await addMemory(page, key, "E2E memory base " + key);
      assert.match(created.version, /^[0-9a-f]{40,64}$/);
      await page.getByLabel("Search saved memory", { exact: true }).fill(key);
      await button(page, "Search").click();
      await openMemory(page, key);
      await dialog(page)
        .getByRole("button", { name: "Edit memory", exact: true })
        .click();
      const second = await context.newPage();
      await second.goto(t.baseURL + "/#/my-twin/memory");
      await second.getByLabel("Search saved memory", { exact: true }).waitFor();
      await second.getByLabel("Search saved memory", { exact: true }).fill(key);
      await button(second, "Search").click();
      await openMemory(second, key);
      await dialog(second)
        .getByRole("button", { name: "Edit memory", exact: true })
        .click();
      await dialog(page)
        .getByLabel("Memory content", { exact: true })
        .fill("E2E first edit " + key);
      await dialog(second)
        .getByLabel("Memory content", { exact: true })
        .fill("E2E kept conflict draft " + key);
      await dialog(page)
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      await dialog(second).locator(".twin-content-conflict").waitFor();
      assert.equal(
        await dialog(second)
          .getByLabel("Memory content", { exact: true })
          .inputValue(),
        "E2E kept conflict draft " + key,
      );
      await dialog(second)
        .getByRole("button", {
          name: "Keep draft and use current version",
          exact: true,
        })
        .click();
      await dialog(second)
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await dialog(second).waitFor({ state: "hidden" });
      await saved(
        second,
        "/me/twin/memory/entry?key=" + key,
        (v) => v.content === "E2E kept conflict draft " + key,
      );
      await openMemory(second, key);
      await dialog(second)
        .getByRole("button", { name: "Delete memory", exact: true })
        .click();
      await dialog(second)
        .locator(".twin-delete-confirm")
        .getByRole("button", { name: "Delete memory", exact: true })
        .click();
      await dialog(second).waitFor({ state: "hidden" });
      await api(second, "/me/twin/memory/entry?key=" + key, { status: 404 });
      await second.close();
    },
  },
  {
    name: "personal / discovered Skills and owner Memory execute through the SDK",
    run: async (t) => {
      const { page } = await t.session(),
        peer = await t.session("DEV-BBB");
      await openTwinTab(page, "Skills");
      await page
        .getByLabel("Search Skills", { exact: true })
        .fill("e2e-verification");
      await button(page, "Details e2e-verification").click();
      await dialog(page).locator(".twin-text-content").waitFor();
      assert.ok((await dialog(page).innerText()).includes("e2e-verification"));
      await page.keyboard.press("Escape");
      const toggle = page.getByRole("switch", {
        name: "Enable e2e-verification",
        exact: true,
      });
      if ((await toggle.getAttribute("aria-checked")) === "true") {
        await toggle.click();
        await saved(page, "/me/twin/skills", (v) =>
          v.items.some((s) => s.name === "e2e-verification" && !s.enabled),
        );
      }
      await toggle.click();
      await saved(page, "/me/twin/skills", (v) =>
        v.items.some((s) => s.name === "e2e-verification" && s.enabled),
      );
      const label = t.unique("skill");
      const privateId = await privateChat(page);
      await sendMessage(page, "E2E SKILL " + label);
      await marker(page, "E2E skill loaded: " + label);
      const memoryLabel = t.unique("owner-memory");
      await sendMessage(page, "E2E MEMORY " + memoryLabel);
      await marker(page, "E2E memory saved: " + memoryLabel);
      const entry = await api(
        page,
        "/me/twin/memory/entry?key=e2e-" + memoryLabel,
      );
      assert.equal(entry.content, "E2E memory " + memoryLabel);
      await api(peer.page, "/me/twin/memory/entry?key=e2e-" + memoryLabel, {
        status: 404,
      });
      const activity = await api(page, "/chats/" + privateId + "/activity");
      assert.ok(activity.items.length);
      let evidence = false;
      for (const run of activity.items.slice(0, 8)) {
        const data = await api(
          page,
          `/chats/${privateId}/activity/${encodeURIComponent(run.session_id)}/${encodeURIComponent(run.run_id)}`,
        );
        if (JSON.stringify(data).includes("load_skill")) evidence = true;
      }
      assert.equal(
        evidence,
        true,
        "Activity must project an actual load_skill operation",
      );
      t.record("owner_memory", {
        key: entry.key,
        version: entry.version,
        peer_not_found: true,
      });
    },
  },
  {
    name: "personal / manual Dream uses canonical private message sources",
    run: async (t) => {
      const { page } = await t.session();
      const privateId = await privateChat(page),
        label = t.unique("dream");
      await sendMessage(page, "E2E MEMORY " + label);
      await marker(page, "E2E memory saved: " + label);
      const boot = await api(page, "/bootstrap"),
        group = boot.chats.find((c) => c.kind === "group");
      assert.ok(group);
      await page
        .locator(".conversation-list .chat-item")
        .filter({ hasText: group.title })
        .click();
      const raw = "E2E GROUP ONLY " + label;
      await sendMessage(page, raw);
      await openTwinTab(page, "Memory");
      const beforeTools = inspectFixture().tool_counts;
      const before = await api(page, "/me/twin/memory/status"),
        priorKeys = new Set(
          (await api(page, "/me/twin/memory")).items.map((m) => m.key),
        );
      await button(page, "Dream now").click();
      await poll(
        async () => {
          const s = await api(page, "/me/twin/memory/status");
          return (
            s.dream.status === "published" &&
            s.dream.last_success_at !== before.dream.last_success_at
          );
        },
        "manual Dream publishes",
        { timeout: 40000 },
      );
      const newEntry = (await api(page, "/me/twin/memory")).items.find(
        (m) => m.key.startsWith("e2e-dream-") && !priorKeys.has(m.key),
      );
      assert.ok(
        newEntry,
        "Dream creates a fresh key without overwriting manual entries",
      );
      const memory = await api(
        page,
        "/me/twin/memory/entry?key=" + encodeURIComponent(newEntry.key),
      );
      assert.ok(memory.sources.length);
      assert.ok(!memory.content.includes(raw));
      const current = await api(page, "/chats/" + privateId);
      const ids = new Set(current.messages.map((m) => m.id));
      const sdkSessions = new Set(
        inspectFixture().tools.map((tool) => tool.session_id),
      );
      const privateSources = memory.sources.filter((source) =>
        source.session_id.startsWith("twin-chat:"),
      );
      assert.ok(privateSources.length);
      for (const source of privateSources) {
        assert.ok(
          source.session_id.startsWith(
            "twin-chat:" + privateId + ":generation:",
          ),
        );
        assert.ok(
          ids.has(source.event_id),
          "Dream conversation reference must identify a visible private message",
        );
      }
      for (const source of memory.sources.filter(
        (source) => !source.session_id.startsWith("twin-chat:"),
      ))
        assert.ok(
          source.session_id === "twin-config:owner-alex" ||
            sdkSessions.has(source.session_id),
          "Other references must name actual SDK sessions or owner configuration",
        );
      const inspection = inspectFixture();
      assert.ok(
        inspection.tool_counts.read_source > (beforeTools.read_source || 0),
      );
      assert.ok(
        inspection.tool_counts.memory_publish >
          (beforeTools.memory_publish || 0),
      );
      const batch = inspection.dreams.find(
        (row) => newEntry.key === "e2e-dream-" + row.request_id.slice(0, 12),
      );
      assert.ok(batch && batch.scope === "private_messages");
      assert.ok(
        batch.messages.length &&
          batch.messages.every(
            (row) => row.chat_id === privateId && ids.has(row.id),
          ),
      );
      t.record("dream_sources", {
        count: memory.sources.length,
        private_namespace: true,
        raw_group_excluded: true,
      });
    },
  },
  {
    name: "personal / private credential CRUD, request form and continuation",
    sensitive: true,
    run: async (t) => {
      const { page } = await t.session(),
        peer = await t.session("DEV-BBB");
      const label = t.unique("credential"),
        manual = "e2e_manual_" + label,
        value = "E2E_ONLY_FAKE_" + label;
      await openTwinTab(page, "Credentials");
      await button(page, "Add credential").click();
      await dialog(page).locator("form>label input").first().fill(manual);
      await dialog(page)
        .locator(".twin-secret-row input:not([type=password])")
        .fill("E2E_TOKEN");
      await dialog(page).locator("input[type=password]").fill(value);
      await dialog(page)
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      await saved(page, "/me/twin/credentials", (v) =>
        v.credentials.some((c) => c.name === manual),
      );
      await noFixtureValue(page, value);
      assert.ok(
        !(await api(peer.page, "/me/twin/credentials")).credentials.some(
          (c) => c.name === manual,
        ),
      );
      await removeCredential(page, manual);
      assert.ok(
        !(await api(page, "/me/twin/credentials")).credentials.some(
          (c) => c.name === manual,
        ),
      );
      const privateId = await privateChat(page);
      await sendMessage(page, "E2E CREDENTIAL " + label);
      await marker(page, "E2E credential pending: " + label);
      await openTwinTab(page, "Credentials");
      const card = page
        .locator(".twin-request-card")
        .filter({ hasText: "e2e-" + label });
      await card
        .getByRole("button", { name: "Provide credential", exact: true })
        .click();
      await dialog(page).locator("input[type=password]").fill(value);
      await dialog(page)
        .getByRole("button", { name: "Save and provide", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      await privateChat(page);
      await marker(page, "E2E credential resumed: " + label);
      await noFixtureValue(page, value);
      assert.equal(
        await activityContains(page, privateId, "E2E_CREDENTIAL_AVAILABLE"),
        true,
        "real bound shell sees the credential without printing its value",
      );
      assert.ok(
        inspectFixture().tools.some(
          (tool) =>
            tool.name === "bash" && tool.success && tool.credential_available,
        ),
      );
      const requests = await api(page, "/me/twin/credential-requests");
      const fulfilled = requests.requests.find(
        (r) => r.name === "e2e-" + label,
      );
      assert.equal(fulfilled.status, "fulfilled");
      assert.ok(fulfilled.continuation_request_id);
      await openTwinTab(page, "Credentials");
      await removeCredential(page, "e2e-" + label);
      t.record("credential", {
        form_to_continuation: true,
        owner_only: true,
        value_absent_from_metadata_chat_storage: true,
      });
    },
  },
  {
    name: "personal / exact approval approve once and reject without action",
    run: async (t) => {
      const { page } = await t.session();
      await privateChat(page);
      const approved = t.unique("approve");
      await sendMessage(page, "E2E APPROVAL " + approved);
      await page
        .locator('.approval-tabs button[title="E2E approval ' + approved + '"]')
        .click();
      await page
        .locator(".approval-card")
        .filter({ hasText: approved })
        .waitFor();
      await page
        .locator(".approval-card")
        .getByRole("button", { name: "View details", exact: true })
        .click();
      await page
        .locator(".approval-arguments")
        .filter({ hasText: "E2E approved " + approved })
        .waitFor();
      await page
        .locator(".approval-card")
        .getByRole("button", { name: "Approve once", exact: true })
        .click();
      await marker(page, "E2E approval executed: " + approved);
      const reminders = (await api(page, "/bootstrap")).reminders;
      assert.equal(
        reminders.filter((r) => r.title === "E2E approved " + approved).length,
        1,
      );
      await page.reload();
      await page.locator(".composer textarea").waitFor();
      assert.equal(
        (await api(page, "/bootstrap")).reminders.filter(
          (r) => r.title === "E2E approved " + approved,
        ).length,
        1,
      );
      const rejected = t.unique("reject");
      await sendMessage(page, "E2E APPROVAL " + rejected);
      await page
        .locator('.approval-tabs button[title="E2E approval ' + rejected + '"]')
        .click();
      await page
        .locator(".approval-card")
        .filter({ hasText: rejected })
        .waitFor();
      await page
        .locator(".approval-card")
        .getByRole("button", { name: "Reject once", exact: true })
        .click();
      await poll(
        async () =>
          !(await api(page, "/chats/private-alex")).approvals.some((a) =>
            a.title.includes(rejected),
          ),
        "rejected approval leaves inbox",
      );
      assert.equal(
        (await api(page, "/bootstrap")).reminders.filter(
          (r) => r.title === "E2E approved " + rejected,
        ).length,
        0,
      );
      t.record("approval", {
        approved_effect_count: 1,
        rejected_effect_count: 0,
      });
    },
  },
  {
    name: "personal / sourced Tracking CRUD and reminders persist across reload",
    run: async (t) => {
      const { page } = await t.session();
      const privateId = await privateChat(page),
        label = t.unique("tracking");
      const source = await sendMessage(page, "E2E tracking source " + label);
      const sourceId = await source.getAttribute("data-message-id");
      assert.ok(sourceId);
      await source.hover();
      await source.getByTitle("Track this message", { exact: true }).click();
      await dialog(page)
        .getByLabel("Topic", { exact: true })
        .fill("E2E topic " + label);
      await dialog(page)
        .getByLabel("Description", { exact: true })
        .fill("E2E tracked source " + label);
      await dialog(page)
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      let detail = await api(page, "/chats/" + privateId);
      let topic = detail.tracking.find((r) => r.title === "E2E topic " + label);
      assert.ok(topic);
      const drawer = page.getByRole("dialog", {
        name: "E2E topic " + label,
        exact: true,
      });
      await drawer
        .getByLabel("Add an update", { exact: true })
        .fill("E2E progress " + label);
      await drawer.getByRole("button", { name: "Add", exact: true }).click();
      await drawer
        .getByText("E2E progress " + label, { exact: true })
        .waitFor();
      await drawer.getByRole("button", { name: "Edit", exact: true }).click();
      await dialog(page)
        .getByLabel("Topic", { exact: true })
        .fill("E2E edited " + label);
      await dialog(page).getByRole("combobox").selectOption("completed");
      await dialog(page)
        .getByRole("button", { name: "Save", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      topic = await api(page, `/chats/${privateId}/tracking/${topic.id}`);
      assert.equal(topic.status, "completed");
      assert.ok(topic.updates.some((u) => u.body === "E2E progress " + label));
      assert.ok(
        inspectFixture()
          .tracking.find((row) => row.id === topic.id)
          .source_message_ids.includes(sourceId),
        "Persisted Tracking retains its actual source ID",
      );
      await page
        .getByRole("dialog", { name: "E2E edited " + label, exact: true })
        .locator(".tracking-detail-actions")
        .getByRole("button", { name: "Delete", exact: true })
        .click();
      await dialog(page)
        .getByRole("button", { name: "Delete", exact: true })
        .click();
      await page
        .getByRole("dialog", { name: "E2E edited " + label, exact: true })
        .waitFor({ state: "hidden" });
      assert.ok(
        !(await api(page, "/chats/" + privateId)).tracking.some(
          (r) => r.id === topic.id,
        ),
      );
      assert.equal(
        inspectFixture().tracking.find((row) => row.id === topic.id).deleted,
        true,
      );
      await page
        .getByRole("button", { name: "Reminders", exact: true })
        .click();
      await page
        .locator(".reminders-main")
        .getByRole("button", { name: "New reminder", exact: true })
        .click();
      await dialog(page)
        .getByLabel("Reminder", { exact: true })
        .fill("E2E reminder " + label);
      await dialog(page)
        .getByLabel("Details (optional)", { exact: true })
        .fill("E2E reminder detail");
      await dialog(page)
        .getByRole("button", { name: "Add reminder", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      const reminder = page
        .locator(".reminder-card")
        .filter({ hasText: "E2E reminder " + label });
      await reminder
        .getByRole("button", { name: "Mark complete", exact: true })
        .click();
      await page
        .locator(".reminder-nav")
        .getByRole("button", { name: /Completed/ })
        .click();
      await reminder.waitFor();
      await page.reload();
      await page
        .locator(".reminder-nav")
        .getByRole("button", { name: /Completed/ })
        .click();
      await reminder.waitFor();
      assert.equal(
        (await api(page, "/bootstrap")).reminders.find(
          (r) => r.title === "E2E reminder " + label,
        ).done,
        true,
      );
      await reminder
        .getByRole("button", { name: "Reopen reminder", exact: true })
        .click();
      await page
        .locator(".reminder-nav")
        .getByRole("button", { name: /To do/ })
        .click();
      await reminder.waitFor();
      t.record("tracking", {
        source_id: sourceId,
        created_edited_updated_deleted: true,
        reminder_completed_reopened: true,
      });
    },
  },
  {
    name: "personal / Reset cancel then confirm preserves groups, Soul and identity",
    last: true,
    run: async (t) => {
      const { page } = await t.session();
      const label = t.unique("reset");
      const privateId = await privateChat(page);
      await sendMessage(page, "E2E MEMORY " + label);
      await marker(page, "E2E memory saved: " + label);
      await openTwinTab(page, "Soul");
      await saveSoul(page, "E2E Soul survives reset " + label);
      const soul = await api(page, "/me/twin/soul"),
        profile = await api(page, "/me/twin"),
        skills = await api(page, "/me/twin/skills"),
        credentials = await api(page, "/me/twin/credentials");
      const groups = (await api(page, "/bootstrap")).chats.filter(
        (c) => c.kind === "group",
      );
      const groupMessages = {};
      for (const c of groups)
        groupMessages[c.id] = (await api(page, "/chats/" + c.id)).messages.map(
          (m) => m.id,
        );
      const before = await api(page, "/me/twin/reset");
      await openTwinTab(page, "Profile");
      await button(page, "Reset").click();
      await dialog(page)
        .getByRole("button", { name: "Cancel", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden" });
      assert.equal(
        (await api(page, "/me/twin/reset")).generation,
        before.generation,
      );
      assert.ok((await api(page, "/chats/" + privateId)).messages.length);
      await button(page, "Reset").click();
      await dialog(page)
        .getByRole("button", { name: "Confirm reset", exact: true })
        .click();
      await dialog(page).waitFor({ state: "hidden", timeout: 30000 });
      await saved(
        page,
        "/me/twin/reset",
        (v) =>
          v.generation === before.generation + 1 && v.status === "completed",
      );
      assert.deepEqual((await api(page, "/chats/" + privateId)).messages, []);
      assert.deepEqual((await api(page, "/me/twin/memory")).items, []);
      assert.deepEqual(await api(page, "/me/twin/soul"), soul);
      assert.equal((await api(page, "/me/twin")).actor.id, profile.actor.id);
      assert.equal(
        (await api(page, "/me/twin")).actor.avatar_preset,
        profile.actor.avatar_preset,
      );
      assert.deepEqual(await api(page, "/me/twin/skills"), skills);
      assert.deepEqual(await api(page, "/me/twin/credentials"), credentials);
      for (const c of groups)
        assert.deepEqual(
          (await api(page, "/chats/" + c.id)).messages.map((m) => m.id),
          groupMessages[c.id],
        );
      t.record("reset", {
        generation_increment: 1,
        private_and_memory_cleared: true,
        groups_soul_identity_skills_credentials_kept: true,
      });
    },
  },
];
module.exports = { cases };
