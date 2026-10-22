/** Independent live verification of a human-seeded group topic in the owner's first private run. */
const { assert, api, poll, privateChat } = require("./helpers.cjs");
const {
  inspect,
  state,
  send,
  waitTwin,
  openSidebar,
} = require("./continuous-workflow.cjs");
const { startChat } = require("./conversations.cjs");
const { randomInt, randomUUID } = require("node:crypto");
const path = require("node:path");

function hasGroupTracking(manifest, group, topic) {
  return (manifest.tracking_refs || []).some(
    (ref) =>
      ref.via_chat_context &&
      ref.chat_id === group &&
      ref.tracking_id === topic,
  );
}

async function runFirstPrivateGroupTracking(t, returned) {
  returned ||= await t.session("DEV-BBB", { baseURL: t.secondaryURL });
  const group = await startChat(returned.page, ["AAA"]),
    groupProject = `Review-${randomUUID().slice(0, 8)}`,
    marker = `GROUP-${randomUUID().slice(0, 8)}`,
    fee = randomInt(31, 69);
  const publicSource = await send(
    returned.page,
    group.id,
    `项目 ${groupProject} 有一项负责人待定的包装事项。这是项目记录，现在无需回复。`,
  );
  const sourceRow = returned.page.locator(
      `article[data-message-id="${publicSource.id}"]`,
    ),
    track = sourceRow.getByTitle("Track this message", { exact: true });
  await track.focus();
  await track.press("Enter");
  let modal = returned.page.locator('.modal[role="dialog"]');
  const title = `Decision for ${groupProject}`;
  await modal.getByLabel("Topic", { exact: true }).fill(title);
  await modal
    .getByLabel("Description", { exact: true })
    .fill("项目包装方案需要负责人决定。请查看最新进展后继续推进，尚未批准。");
  await modal.getByRole("button", { name: "Save", exact: true }).click();
  await modal.waitFor({ state: "hidden" });
  const seeded = await poll(
    async () =>
      (await state(returned.page, group.id)).tracking.find(
        (row) => row.title === title,
      ),
    "manual group topic saved",
  );
  // Saving Track this message already opens this topic drawer.
  const drawer = returned.page.getByRole("dialog", {
    name: title,
    exact: true,
  });
  await drawer.waitFor({ state: "visible" });
  const update = `最新未决变更 ${marker}：防潮包装会增加人民币 ${fee} 元，需要AAA决定是否接受，尚未批准。`;
  await drawer.getByLabel("Add an update", { exact: true }).fill(update);
  await drawer.getByRole("button", { name: "Add", exact: true }).click();
  const groupDetails = await poll(async () => {
    const value = await api(
      returned.page,
      `/chats/${group.id}/tracking/${seeded.id}`,
    );
    return value.updates.some((row) => row.body === update) && value;
  }, "manual group update persisted");
  assert.ok(
    !JSON.stringify((await state(returned.page, group.id)).messages).includes(
      marker,
    ),
    "new group fact is absent from all chat bodies",
  );
  assert.ok(
    !JSON.stringify((await state(returned.page, group.id)).tracking).includes(
      marker,
    ),
    "fact is absent from the lightweight Tracking index",
  );
  t.record("manual_group_seed", {
    topic_id: seeded.id,
    source_chat_id: group.id,
    source_message_id: publicSource.id,
    update_id: groupDetails.updates.find((row) => row.body === update).id,
    marker,
    fee,
    created_by_human_UI: true,
    agent_created_claimed: false,
    marker_only_in_tracking_update: true,
  });
  const a = await t.session();
  const aChat = await privateChat(a.page);
  const aBefore = await state(a.page, aChat.id),
    beforeEvidence = await inspect();
  assert.equal(
    aBefore.messages.length,
    0,
    "AAA has no prior private chat history",
  );
  assert.equal(
    beforeEvidence.requests.filter((row) => row.chat_id === aChat.id).length,
    0,
    "AAA has no previous private model run",
  );
  const beforeAskGroup = await state(a.page, group.id);
  const alternate = beforeAskGroup.messages
    .filter((message) => message.body.includes(marker))
    .map((message) => message.id);
  t.record("first_private_preconditions", {
    private_messages: aBefore.messages.length,
    previous_private_requests: beforeEvidence.requests.filter(
      (row) => row.chat_id === aChat.id,
    ).length,
    marker_in_prior_group_chat_messages: alternate,
  });
  assert.equal(
    alternate.length,
    0,
    "the update-only fact has not leaked into a chat-body alternative channel before the first private question",
  );
  const ask = await send(
    a.page,
    aChat.id,
    "现在有哪些需要我决定的事？请按最新进展说。",
  );
  const answer = await waitTwin(a.page, aChat.id, ask.id);
  const final = await inspect(),
    request = final.requests.find((row) => row.id === answer.request.id);
  const calls = final.tools.filter(
    (tool) => tool.request_id === request.id && tool.owner_id === "owner-alex",
  );
  assert.ok(
    calls.some(
      (tool) =>
        tool.name === "get_tracking" &&
        tool.success &&
        tool.tracking_id === seeded.id &&
        tool.arguments?.chat_id === group.id &&
        tool.result_tracking_markers?.includes(marker),
    ),
    "AAA reads the actual source group Tracking details",
  );
  const manifests = final.context_manifests.filter(
    (row) => row.request_id === request.id,
  );
  assert.ok(
    manifests.some((row) => hasGroupTracking(row, group.id, seeded.id)),
    "actual private runtime manifest contains the group topic in a chat_context envelope",
  );
  const answerText = answer.snapshot.messages
    .filter((message) => message.kind === "twin" && message.seq > ask.seq)
    .map((message) => message.body)
    .join("\n");
  assert.match(
    answerText.replace(/[,， ]/g, ""),
    new RegExp(`(^|[^0-9])${fee}([^0-9]|$)`),
    "generic reply reports the actual extra fee",
  );
  assert.match(
    answerText,
    /防潮|moisture|waterproof/i,
    "generic reply identifies the actual packaging decision",
  );
  assert.match(answerText, /批准|获批|未批|决定|决策|拍板|approval|decid/i);
  assert.ok(
    !calls.some(
      (tool) =>
        tool.success &&
        tool.name !== "get_tracking" &&
        tool.result_tracking_markers?.includes(marker),
    ),
    "no successful non-Tracking tool returns the same update-only fact",
  );
  t.record("first_private_group_aggregation", {
    status: "passed",
    source_group_id: group.id,
    topic_id: seeded.id,
    manual_seed: true,
    private_request: request.id,
    private_sdk_session: request.session_id,
    first_private_run: true,
    actual_get_tracking: true,
    group_manifest_envelope: true,
    marker_only_from_tracking_update: true,
    group_sdk_session_migration_claimed: false,
  });
  await openSidebar(a.page, "Tracking");
  await a.page.locator(".tracking-card").filter({ hasText: title }).waitFor();
  await a.page.screenshot({
    path: path.join(t.artifactDir, "tracking-first-private-aggregate.png"),
    fullPage: true,
  });
}

const cases = [
  {
    name: "live first-private group Tracking / manually seeded update, source-scoped detail read and actual context envelope",
    timeout: 300000,
    run: async (t) => {
      t.record("scenario", {
        independent_of_peer_collaboration: true,
        autonomous_tracking_creation_claimed: false,
        prior_resumption_phases_repeated: false,
        user_prompt_has_tool_names: false,
      });
      await runFirstPrivateGroupTracking(t);
    },
  },
];
module.exports = { cases, runFirstPrivateGroupTracking };
