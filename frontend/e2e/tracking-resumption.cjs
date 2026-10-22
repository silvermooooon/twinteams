/** Independent live Tracking verification. This does not retry the peer-collaboration story. */
const {
  assert,
  api,
  poll,
  sleep,
  privateChat,
  logout,
} = require("./helpers.cjs");
const {
  inspect,
  state,
  send,
  waitTwin,
  openSidebar,
  isOngoing,
} = require("./continuous-workflow.cjs");
const {
  runFirstPrivateGroupTracking,
} = require("./first-private-group-tracking.cjs");
const { randomInt, randomUUID } = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs/promises");

const cases = [
  {
    name: "live tracking resumption / autonomous sourced goal and file, real 60-second return, and first-private aggregation of a manual group topic",
    timeout: 900000,
    run: async (t) => {
      const code = randomUUID().slice(0, 8),
        project = `Harbor-${code}`,
        quantity = randomInt(21, 49),
        decision = `DECISION-${randomUUID().slice(0, 8)}`,
        filename = `${project}-preparation.md`;
      const brief = `# ${project}

准备 ${quantity} 件试点样品。
未决事项 ${decision}：蓝色包装或绿色包装尚未由BBB批准。
只做准备，不下单、不联系其他人，不把未决选择写成已经完成。
`;
      const goal = `请根据附件整理一份可下载的准备建议，保存为 ${filename}，说明数量和仍需我决定的选择。这件事不是一次性问答，请持续保留待办和阻碍，我还没批准时不要当成完成。无需联系其他人。`;
      t.record("scenario", {
        independent_of_peer_collaboration: true,
        max_live_attempts: 20,
        project,
        quantity,
        decision,
        filename,
        user_goal_has_tool_names: false,
      });
      await fs.writeFile(
        path.join(t.artifactDir, "tracking-resumption-inputs.json"),
        JSON.stringify({ project, quantity, decision, brief, goal }, null, 2),
      );
      const b = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      const bChat = await privateChat(b.page);
      await b.page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles({
          name: `${project}-brief.md`,
          mimeType: "text/markdown",
          buffer: Buffer.from(brief),
        });
      await poll(
        async () =>
          (await b.page.locator(".draft-attachment.ready").count()) === 1,
        "brief upload ready",
      );
      const source = await send(b.page, bChat.id, goal);
      const first = await poll(
        async () => {
          const snapshot = await state(b.page, bChat.id),
            evidence = await inspect();
          const request = evidence.requests.find(
            (row) =>
              row.source_message_id === source.id &&
              row.target_twin_id === "twin-maya",
          );
          if (request?.status === "failed")
            throw new Error(`Independent goal failed: ${request.id}`);
          const topic = snapshot.tracking.find(
            (row) =>
              row.chat_id === bChat.id &&
              isOngoing(row.status) &&
              JSON.stringify(row).includes(project),
          );
          const file = (await api(b.page, `/chats/${bChat.id}/workspace`)).find(
            (row) => row.kind === "file" && row.name === filename,
          );
          const calls = evidence.tools.filter(
            (tool) => tool.owner_id === "owner-maya" && tool.success,
          );
          const creation =
            topic &&
            calls.find(
              (tool) =>
                tool.name === "create_tracking" &&
                tool.result_ids?.id === topic.id,
            );
          const activity = await api(b.page, `/chats/${bChat.id}/activity`);
          t.record("preparation_observation", {
            request_status: request?.status,
            topic_id: topic?.id,
            file_id: file?.id,
            actual_create_tracking: !!creation,
          });
          if (
            !request ||
            request.status !== "completed" ||
            !topic ||
            !file ||
            !creation ||
            activity.items.some((run) =>
              ["queued", "running"].includes(run.status),
            )
          )
            return false;
          return { request, topic, file, snapshot, evidence, creation };
        },
        "actual autonomous Tracking and downloadable preparation exist",
        { timeout: 240000, interval: 1200 },
      );
      const audit = first.evidence.tracking.find(
        (row) => row.id === first.topic.id,
      );
      assert.ok(
        audit.source_message_ids.length > 0 &&
          audit.source_message_ids.every((id) =>
            first.snapshot.messages.some((message) => message.id === id),
          ),
        "autonomous topic has actual authorized message sources",
      );
      await openSidebar(b.page, "Workspace");
      await b.page
        .getByRole("button", { name: "Refresh workspace", exact: true })
        .click();
      await b.page.getByRole("button", { name: filename, exact: true }).click();
      const [download] = await Promise.all([
        b.page.waitForEvent("download"),
        b.page
          .getByRole("dialog")
          .getByRole("link", { name: "Download", exact: true })
          .click(),
      ]);
      const output = await fs.readFile(await download.path(), "utf8");
      assert.ok(
        output.includes(project) &&
          (output.includes(decision) ||
            (output.includes("蓝") && output.includes("绿"))),
      );
      assert.match(
        output.replace(/[,， ]/g, ""),
        new RegExp(`(^|[^0-9])${quantity}([^0-9]|$)`),
      );
      await fs.writeFile(path.join(t.artifactDir, filename), output);
      await b.page
        .getByRole("dialog")
        .getByRole("button", { name: "Close", exact: true })
        .click();
      await openSidebar(b.page, "Tracking");
      await b.page
        .locator(".tracking-card")
        .filter({ hasText: first.topic.title })
        .waitFor();
      await b.page.screenshot({
        path: path.join(t.artifactDir, "tracking-autonomous-goal.png"),
        fullPage: true,
      });
      t.record("autonomous_preparation", {
        status: "passed",
        topic_id: first.topic.id,
        source_message_ids: audit.source_message_ids,
        actual_tracking_tool_event: first.creation.event_id,
        artifact: filename,
        screenshot: "tracking-autonomous-goal.png",
      });
      await logout(b.page);
      await b.context.tracing.stop({
        path: path.join(t.artifactDir, "tracking-preparation.zip"),
      });
      await b.context.close();
      const left = new Date().toISOString(),
        monotonic = performance.now();
      await sleep(60000);
      const restarted = await inspect("restart-worker");
      assert.equal(restarted.restarted, true);
      const returned = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      await privateChat(returned.page);
      const elapsed = performance.now() - monotonic;
      assert.ok(elapsed >= 60000);
      t.record("actual_time_gap", {
        left_wall: left,
        returned_wall: new Date().toISOString(),
        monotonic_elapsed_ms: elapsed,
        clock_advanced: false,
        worker_restart: "graceful owned worker restart",
        overnight_claimed: false,
      });
      const resumeSource = await send(
        returned.page,
        bChat.id,
        "刚才的准备还有什么需要我决定？",
      );
      const resumed = await waitTwin(returned.page, bChat.id, resumeSource.id);
      const evidence = await inspect();
      const resumeRequest = evidence.requests.find(
        (row) => row.id === resumed.request.id,
      );
      const reply = resumed.snapshot.messages
        .filter(
          (message) =>
            message.kind === "twin" && message.seq > resumeSource.seq,
        )
        .map((message) => message.body)
        .join("\n");
      assert.ok(
        reply.includes("蓝") && reply.includes("绿"),
        "generic return prompt resurfaces the actual packaging alternatives",
      );
      assert.match(
        reply,
        /未|待|选|决定|批准|拍板|pending|decid/i,
        "the packaging choice is still pending the owner",
      );
      const sameTopic = resumed.snapshot.tracking.find(
        (row) => row.id === first.topic.id,
      );
      assert.ok(sameTopic && isOngoing(sameTopic.status));
      assert.ok(
        first.request.run_id &&
          resumeRequest.run_id &&
          first.request.run_id !== resumeRequest.run_id,
      );
      const resumeIndex = evidence.context_manifests.some(
        (manifest) =>
          manifest.request_id === resumeRequest.id &&
          manifest.tracking_refs?.some(
            (ref) =>
              ref.tracking_id === first.topic.id && ref.chat_id === bChat.id,
          ),
      );
      const resumeRead = evidence.tools.some(
        (tool) =>
          tool.request_id === resumeRequest.id &&
          tool.name === "get_tracking" &&
          tool.success &&
          tool.tracking_id === first.topic.id,
      );
      assert.ok(
        resumeIndex || resumeRead,
        "new run actually receives the same Tracking topic through its index or detail read",
      );
      t.record("same_private_resumption", {
        status: "passed",
        same_topic_id: sameTopic.id,
        actual_index_reference: resumeIndex,
        actual_detail_read: resumeRead,
        original_run: first.request.run_id,
        returned_run: resumeRequest.run_id,
        original_sdk_session: first.request.session_id,
        returned_sdk_session: resumeRequest.session_id,
        same_sdk_session: first.request.session_id === resumeRequest.session_id,
        tracking_exclusively_caused_recall_claimed: false,
        context_also_contains_authorized_private_history: true,
      });
      await returned.page.screenshot({
        path: path.join(t.artifactDir, "tracking-sixty-second-return.png"),
        fullPage: true,
      });

      await runFirstPrivateGroupTracking(t, returned);
    },
  },
];
module.exports = { cases };
