/** Live, un-scripted model workflow. Inputs contain business goals, never tool instructions. */
const {
  assert,
  api,
  poll,
  sleep,
  sendMessage,
  privateChat,
  logout,
  openTwinTab,
  chooseChat,
} = require("./helpers.cjs");
const { startChat } = require("./conversations.cjs");
const { randomInt, randomUUID } = require("node:crypto");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const fs = require("node:fs/promises");
const path = require("node:path");
const execute = promisify(execFile);
const isOngoing = (status) =>
  /^(active|ongoing|in[_ -]?progress|pending|blocked|waiting|open)$/i.test(
    status,
  );

async function inspect(operation = "inspect") {
  const { stdout } = await execute(
    process.env.TWINTEAMS_E2E_PYTHON,
    [
      path.resolve(__dirname, "../../backend/checks/browser_e2e_fixture.py"),
      operation,
      "--state",
      process.env.TWINTEAMS_E2E_STATE,
    ],
    { timeout: 25000 },
  );
  const value = JSON.parse(stdout);
  if (operation !== "restart-worker") {
    assert.equal(value.browser_e2e, true);
    assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  }
  return value;
}
async function state(page, chatId) {
  return api(page, `/chats/${chatId}?limit=100`);
}
async function send(page, chatId, body) {
  const row = await sendMessage(page, body);
  const id = await row.getAttribute("data-message-id");
  return (await state(page, chatId)).messages.find(
    (message) => message.id === id,
  );
}
async function waitTwin(page, chatId, sourceId, timeout = 180000) {
  return poll(
    async () => {
      const snapshot = await state(page, chatId);
      const evidence = await inspect();
      const request = evidence.requests.find(
        (row) =>
          row.source_message_id === sourceId &&
          row.target_twin_id ===
            (chatId === "private-maya" ? "twin-maya" : "twin-alex"),
      );
      if (request?.status === "failed")
        throw new Error(`Live request failed: ${request.id}`);
      return (
        request?.status === "completed" &&
        snapshot.messages.find(
          (row) => row.kind === "twin" && row.request_id === request.id,
        ) && { request, snapshot }
      );
    },
    "live model reply persisted",
    { timeout, interval: 1000 },
  );
}
async function openSidebar(page, tab) {
  if (!(await page.locator(".detail-sidebar").isVisible()))
    await page
      .getByRole("button", { name: "Toggle sidebar", exact: true })
      .click();
  await page.getByRole("tab", { name: tab, exact: true }).click();
}

const cases = [
  {
    name: "live continuous workflow / private goal, peer Duet collaboration, real deliverable, autonomous Tracking and later resumption",
    timeout: 900000,
    run: async (t) => {
      const code = randomUUID().slice(0, 8),
        project = `Orchid-${code}`,
        sku = `SKU-${randomUUID().slice(0, 6)}`;
      const quantity = randomInt(23, 48),
        budget = randomInt(6000, 9000),
        unitPrice = randomInt(79, 120);
      const quote = `Q-${randomUUID().slice(0, 8)}`,
        delivery = new Date(Date.now() + 5 * 86400000)
          .toISOString()
          .slice(0, 10);
      const filename = `${project}-launch-plan.md`;
      const brief = `# ${project}\n\n目标：准备一场仍需负责人最终批准的试点发布。\n产品SKU：${sku}\n数量：${quantity} 件\n预算上限：人民币 ${budget} 元\n团队：AAA 是项目负责人；BBB 负责供应与交付；CCC 负责财务。\n供应商报价、可交付日期尚待负责交付的人确认。\n负责人尚未批准正式下单，不得把准备建议或拿到报价当成发布完成。\n交付物：一份可下载的Markdown发布建议，写明已确认的数量、预算、供应报价编号和交期，以及仍需决定的事项。\n`;
      const memoryKey = `projects/${project}/supplier-confirmation`;
      const projectMemory = `项目 ${project}，产品 ${sku}。BBB确认：供应报价编号 ${quote}，单价人民币 ${unitPrice} 元，最早可交付日 ${delivery}。这些项目事实已获BBB本人授权向AAA及其项目助理公开，但不代表获批下单。`;
      const sharedAuthorization = `我是 BBB，负责 ${project} 的供应交付。本项目的已确认报价和交期已存入我的项目资料。AAA 或其助理在这个项目群向我协调时，我授权我的助理向他们提供该项目资料里的报价编号、单价与交期，并代表我确认；不代表负责人已经批准下单。此条只是项目共享授权通知，现在无需答复。`;
      const kickoff = `请帮我推进附件里的试点发布准备。先找到负责供应交付的同事，与你能联系到的对方助理确认真实报价、报价编号和交期，再整理一份可下载的建议，保存为 ${filename}。你可以代表我在平台内联系资料里相关同事。这个项目不是一次性问答：负责人最终审批还没给出，请持续保留待办和阻碍，有新结果及时告诉我；不要擅自下单、付款或当作已经发布完成。`;
      await fs.writeFile(
        path.join(t.artifactDir, "continuous-workflow-inputs.json"),
        JSON.stringify(
          {
            project,
            sku,
            quantity,
            budget,
            unit_price: unitPrice,
            quote,
            delivery,
            filename,
            brief,
            public_owner_authorization: sharedAuthorization,
            kickoff,
            model: "gpt-6-luna",
          },
          null,
          2,
        ),
      );
      t.record("ground_truth", {
        project,
        sku,
        quantity,
        budget,
        quote,
        delivery,
        filename,
      });
      t.record("boundaries", {
        tool_names_in_user_goal: false,
        scripted_model_answers: false,
        clock_advanced: false,
        claimed_overnight: false,
        sdk_session_identity_expected_stable_in_same_chat: true,
      });

      const maya = await t.session("DEV-BBB", { baseURL: t.secondaryURL });
      await openTwinTab(maya.page, "Memory");
      for (const [key, content] of [
        [memoryKey, projectMemory],
        [
          "MEMORY",
          `BBB负责供应交付。${project} 的已确认供应资料保存在 ${memoryKey}，其中项目报价与交期允许向AAA公开。请按项目需要查阅，不扩展到其他私人资料。`,
        ],
      ]) {
        await maya.page
          .getByRole("button", { name: "Add memory", exact: true })
          .click();
        const modal = maya.page.locator('.modal[role="dialog"]');
        await modal.getByLabel("Memory key", { exact: true }).fill(key);
        await modal.getByLabel("Memory content", { exact: true }).fill(content);
        await modal.getByRole("button", { name: "Save", exact: true }).click();
        await modal.waitFor({ state: "hidden" });
        assert.equal(
          (
            await api(
              maya.page,
              `/me/twin/memory/entry?key=${encodeURIComponent(key)}`,
            )
          ).content,
          content,
        );
      }
      await maya.page
        .getByRole("button", { name: "Back to chats", exact: true })
        .click();
      const authorizationChat = await startChat(maya.page, ["AAA"]);
      const authorizationMessage = await send(
        maya.page,
        authorizationChat.id,
        sharedAuthorization,
      );
      t.record("business_setup", {
        kind: "public_owner_authorization",
        chat_id: authorizationChat.id,
        authorization_message_id: authorizationMessage.id,
        peer_quote_facts_only_in_BBB_GitMemory: true,
        private_source_seeded: false,
      });
      // Real logout plus device expiry makes the peer genuinely offline; no Redis rewrites.
      await logout(maya.page);
      const alex = await t.session();
      const own = await privateChat(alex.page);
      const config = await api(alex.page, "/config");
      await sleep((config.presence_ttl_seconds + 2) * 1000);
      const before = await inspect();
      await alex.page
        .locator(".chat-main > input[type=file]")
        .first()
        .setInputFiles({
          name: `${project}-brief.md`,
          mimeType: "text/markdown",
          buffer: Buffer.from(brief),
        });
      await poll(
        async () =>
          (await alex.page.locator(".draft-attachment.ready").count()) === 1,
        "real brief upload",
      );
      const source = await send(alex.page, own.id, kickoff);
      t.record("origin_message_id", source.id);
      const uploaded = source.attachments.find(
        (attachment) => attachment.name === `${project}-brief.md`,
      );
      assert.ok(
        uploaded?.workspace_path,
        "uploaded brief has a real workspace reference",
      );

      const milestone = await poll(
        async () => {
          const current = await state(alex.page, own.id),
            evidence = await inspect();
          const topics = current.tracking.filter(
            (topic) =>
              isOngoing(topic.status) &&
              JSON.stringify(topic).includes(project),
          );
          const files = await api(alex.page, `/chats/${own.id}/workspace`);
          const file = files.find(
            (entry) => entry.kind === "file" && entry.name === filename,
          );
          const newTools = evidence.tools.filter(
            (tool) =>
              !before.tools.some(
                (old) =>
                  old.session_id === tool.session_id &&
                  old.run_id === tool.run_id &&
                  old.name === tool.name,
              ),
          );
          const collaboration = (
            await api(alex.page, "/bootstrap")
          ).chats.filter(
            (chat) =>
              chat.kind === "group" &&
              chat.member_owner_ids.includes("owner-maya"),
          );
          let peerAnswer;
          for (const chat of collaboration) {
            const shared = await state(alex.page, chat.id);
            peerAnswer =
              peerAnswer ||
              shared.messages.find(
                (message) =>
                  message.author_id === "twin-maya" &&
                  (message.body.includes(quote) ||
                    message.body.includes(delivery)),
              );
          }
          const originRequest = evidence.requests.find(
            (request) =>
              request.source_message_id === source.id &&
              request.target_twin_id === "twin-alex",
          );
          const related = evidence.requests.filter(
            (request) =>
              request.causal_chain_id === originRequest?.causal_chain_id,
          );
          const activity = await api(alex.page, `/chats/${own.id}/activity`);
          const pending = related.filter((request) =>
            ["queued", "running"].includes(request.status),
          );
          const aReport = current.messages.find(
            (message) =>
              message.author_id === "twin-alex" &&
              message.seq > source.seq &&
              (message.body.includes(quote) ||
                (message.body.includes(delivery) &&
                  message.body.includes(String(unitPrice)))),
          );
          const settled =
            originRequest?.status === "completed" &&
            !pending.length &&
            !activity.items.some((run) =>
              ["queued", "running"].includes(run.status),
            );
          t.record("latest_phase_one_observation", {
            topics: current.tracking.map((topic) => ({
              id: topic.id,
              title: topic.title,
              status: topic.status,
            })),
            workspace_files: files.map((file) => file.name),
            tool_names: newTools.map((tool) => tool.name),
            peer_answer_found: !!peerAnswer,
            owner_report_found: !!aReport,
            pending_request_ids: pending.map((request) => request.id),
            sdk_work_settled: settled,
          });
          const blocked = evidence.followups?.find(
            (followup) =>
              followup.origin_request_id === originRequest?.id &&
              followup.notification_state === "blocked",
          );
          if (blocked && settled && (!peerAnswer || !aReport)) {
            t.record("phase_one", {
              status: "failed",
              reason:
                "A real peer reply settled but its follow-up is explicitly blocked",
              followup_id: blocked.id,
              notification_state: blocked.notification_state,
              reply_message_id: blocked.reply_message_id,
            });
            throw new Error(
              `Live workflow blocked after settled peer reply: follow-up ${blocked.id}`,
            );
          }
          if (
            !topics.length ||
            !file ||
            !peerAnswer ||
            !aReport ||
            !settled ||
            !newTools.some(
              (tool) => tool.name === "create_tracking" && tool.success,
            )
          )
            return false;
          return {
            current,
            evidence,
            topic: topics[0],
            file,
            peerAnswer,
            newTools,
            originRequest,
            aReport,
          };
        },
        "autonomous collaboration, artifact and ongoing topic actually exist",
        { timeout: 420000, interval: 1500 },
      );
      const topicAudit = milestone.evidence.tracking.find(
        (topic) => topic.id === milestone.topic.id,
      );
      assert.ok(
        topicAudit?.source_message_ids.length > 0,
        "autonomous topic cites real source messages",
      );
      const topicSourceState =
        milestone.topic.chat_id === own.id
          ? milestone.current
          : await state(alex.page, milestone.topic.chat_id);
      const visibleIds = new Set(
        [...milestone.current.messages, ...topicSourceState.messages].map(
          (message) => message.id,
        ),
      );
      assert.ok(
        topicAudit.source_message_ids.every((id) => visibleIds.has(id)),
        "topic source IDs name persisted authorized messages",
      );
      assert.ok(
        milestone.newTools.some(
          (tool) =>
            ["read", "bash", "read_workspace_file"].includes(tool.name) &&
            tool.success &&
            tool.owner_id === "owner-alex" &&
            (tool.arguments?.path === uploaded.workspace_path ||
              tool.attachment_ids_read?.includes(uploaded.id)),
        ),
        "uploaded file was actually read",
      );
      assert.ok(
        milestone.newTools.some(
          (tool) => tool.name === "send_chat_message" && tool.success,
        ),
        "collaboration includes a real cross-chat tool send",
      );
      assert.ok(
        milestone.newTools.some(
          (tool) =>
            tool.success &&
            tool.owner_id === "owner-maya" &&
            ((tool.name === "memory_read" && tool.memory_key === memoryKey) ||
              (tool.name === "memory_search" &&
                tool.result_memory_keys?.includes(memoryKey))) &&
            milestone.evidence.requests.some(
              (request) =>
                request.id === tool.request_id &&
                request.chat_id === authorizationChat.id &&
                request.target_twin_id === "twin-maya",
            ),
        ),
        "peer pricing requires real retrieval of the exact project Memory entry",
      );
      await openSidebar(alex.page, "Workspace");
      await alex.page
        .getByRole("button", { name: "Refresh workspace", exact: true })
        .click();
      await alex.page
        .getByRole("button", { name: filename, exact: true })
        .click();
      const [download] = await Promise.all([
        alex.page.waitForEvent("download"),
        alex.page
          .getByRole("dialog")
          .getByRole("link", { name: "Download", exact: true })
          .click(),
      ]);
      const contents = await fs.readFile(await download.path(), "utf8");
      for (const fact of [project, sku, quote])
        assert.ok(
          contents.includes(fact),
          `downloaded artifact contains actual fact: ${fact}`,
        );
      const numericContents = contents.replace(/[,， ]/g, "");
      for (const number of [quantity, budget, unitPrice])
        assert.ok(
          numericContents.includes(String(number)),
          `artifact contains numeric fact ${number}`,
        );
      const [year, month, day] = delivery.split("-").map(Number);
      const deliveryVariants = [
        delivery,
        `${year}/${month}/${day}`,
        `${year}/${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}`,
        `${year}年${month}月${day}日`,
        `${year}.${month}.${day}`,
        `${month}/${day}/${year}`,
        new Date(`${delivery}T12:00:00Z`).toLocaleDateString("en-US", {
          timeZone: "UTC",
          year: "numeric",
          month: "long",
          day: "numeric",
        }),
      ];
      const comparableDateText = contents.replace(/[\s,，]/g, "").toLowerCase();
      assert.ok(
        deliveryVariants.some((value) =>
          comparableDateText.includes(
            value.replace(/[\s,，]/g, "").toLowerCase(),
          ),
        ),
        "artifact contains the actual confirmed delivery date, independent of presentation",
      );
      await fs.writeFile(path.join(t.artifactDir, filename), contents);
      await alex.page
        .getByRole("dialog")
        .getByRole("button", { name: "Close", exact: true })
        .click();
      await openSidebar(alex.page, "Tracking");
      await alex.page
        .getByRole("button", {
          name: new RegExp(
            milestone.topic.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
          ),
        })
        .first()
        .waitFor();
      await alex.page.screenshot({
        path: path.join(
          t.artifactDir,
          "continuous-workflow-deliverable-and-tracking.png",
        ),
        fullPage: true,
      });
      t.record("phase_one", {
        status: "passed",
        topic_id: milestone.topic.id,
        topic_chat_id: milestone.topic.chat_id,
        source_message_ids: topicAudit.source_message_ids,
        generated_file_id: milestone.file.id,
        peer_message_id: milestone.peerAnswer.id,
        screenshot: "continuous-workflow-deliverable-and-tracking.png",
        artifact: filename,
      });

      // A new, public-only change tests actual group-to-private knowledge transfer.
      // No private prompt, Memory entry, or uploaded/generated file contains this change.
      const changeCode = `CHANGE-${randomUUID().slice(0, 8)}`,
        surcharge = randomInt(11, 30);
      const collaborator = await t.session("DEV-BBB", {
        baseURL: t.secondaryURL,
      });
      const groupChat = (await api(collaborator.page, "/bootstrap")).chats.find(
        (chat) => chat.id === milestone.peerAnswer.chat_id,
      );
      assert.ok(
        groupChat,
        "peer collaboration belongs to an existing authorized group",
      );
      await chooseChat(collaborator.page, groupChat.title);
      const groupPrompt = `@AAA，请把大家已经确认的供应方案和仍需负责人决定的事项整理好，后续我们继续推进。另外刚收到一项仅此群的新变更：${changeCode}，供应商建议改用防潮包装，需额外人民币 ${surcharge} 元/件。这笔加急包装费和是否采纳都还没有得到负责人批准，不要当作已确认。`;
      const changeSource = await send(
        collaborator.page,
        groupChat.id,
        groupPrompt,
      );
      t.record("cross_sdk_group_stage", {
        status: "running",
        group_id: groupChat.id,
        change_code: changeCode,
        source_message_id: changeSource.id,
        change_seeded_in_private: false,
      });
      let groupMilestone;
      try {
        groupMilestone = await poll(
          async () => {
            const snapshot = await state(collaborator.page, groupChat.id),
              audit = await inspect();
            const request = audit.requests.find(
              (row) =>
                row.source_message_id === changeSource.id &&
                row.target_twin_id === "twin-alex",
            );
            if (request?.status === "failed")
              throw new Error(`Group owner run failed: ${request.id}`);
            let topic;
            for (const row of snapshot.tracking.filter(
              (row) => row.chat_id === groupChat.id && isOngoing(row.status),
            )) {
              const details = await api(
                collaborator.page,
                `/chats/${groupChat.id}/tracking/${row.id}`,
              );
              if (JSON.stringify(details).includes(changeCode)) {
                topic = details;
                break;
              }
            }
            const aTools = audit.tools.filter(
              (tool) =>
                tool.request_id === request?.id &&
                tool.owner_id === "owner-alex" &&
                tool.success,
            );
            const actualTrackingWrite =
              topic &&
              aTools.some(
                (tool) =>
                  [
                    "create_tracking",
                    "update_tracking",
                    "add_tracking_update",
                  ].includes(tool.name) &&
                  (tool.result_ids?.id === topic.id ||
                    tool.arguments?.id === topic.id),
              );
            if (
              request?.status !== "completed" ||
              !topic ||
              !actualTrackingWrite
            )
              return false;
            const privateSnapshot = await state(alex.page, own.id);
            const alternate = privateSnapshot.messages.filter((message) =>
              message.body.includes(changeCode),
            );
            return {
              request,
              topic,
              aTools,
              alternate_private_message_ids: alternate.map(
                (message) => message.id,
              ),
            };
          },
          "A Duet records the new public-only blocker in its actual group topic",
          { timeout: 180000, interval: 1500 },
        );
      } catch (error) {
        t.record("cross_sdk_group_stage", {
          status: "failed",
          group_id: groupChat.id,
          change_code: changeCode,
          error: error.message,
        });
        throw error;
      }
      t.record("cross_sdk_group_stage", {
        status: "passed",
        group_id: groupChat.id,
        topic_id: groupMilestone.topic.id,
        change_code: changeCode,
        group_run_id: groupMilestone.request.run_id,
        group_session_id: groupMilestone.request.session_id,
        alternate_private_message_ids:
          groupMilestone.alternate_private_message_ids,
        exclusive_tracking_causality_claimed: false,
      });
      await logout(collaborator.page);
      await collaborator.context.tracing.stop({
        path: path.join(t.artifactDir, "continuous-workflow-public-group.zip"),
      });
      await collaborator.context.close();
      const originalRequest = milestone.originRequest;
      assert.ok(originalRequest, "original live SDK request exists");
      await logout(alex.page);
      await alex.context.tracing.stop({
        path: path.join(t.artifactDir, "continuous-workflow-phase-one.zip"),
      });
      await alex.context.close();
      await maya.context.close();
      const leftWall = new Date().toISOString(),
        leftMonotonic = performance.now();
      await sleep(60000);
      const restart = await inspect("restart-worker");
      assert.equal(restart.restarted, true);
      const returned = await t.session();
      await privateChat(returned.page);
      const actualElapsed = performance.now() - leftMonotonic;
      assert.ok(actualElapsed >= 60000);
      t.record("real_time_gap", {
        left_wall: leftWall,
        returned_wall: new Date().toISOString(),
        monotonic_elapsed_ms: actualElapsed,
        required_wait_ms: 60000,
        worker_restarted: true,
        clock_advanced: false,
      });
      const privateIndex = await api(
        returned.page,
        `/chats/${own.id}/tracking`,
      );
      assert.ok(
        privateIndex.tracking.some(
          (topic) =>
            topic.id === groupMilestone.topic.id &&
            topic.chat_id === groupChat.id,
        ),
        "new private context is authorized to see the real group topic",
      );
      const alternateBeforeReturn = (
        await state(returned.page, own.id)
      ).messages
        .filter((message) => message.body.includes(changeCode))
        .map((message) => message.id);
      const question = "我回来了，现在最值得我关注什么？";
      const resumeSource = await send(returned.page, own.id, question);
      const resumed = await waitTwin(returned.page, own.id, resumeSource.id);
      const resumedEvidence = await inspect();
      const resumedRequest = resumedEvidence.requests.find(
        (request) => request.id === resumed.request.id,
      );
      const resumedRuns = resumedEvidence.tools.filter(
        (tool) =>
          tool.name === "get_tracking" &&
          tool.success &&
          tool.request_id === resumedRequest.id &&
          tool.tracking_id === groupMilestone.topic.id &&
          tool.arguments?.chat_id === groupChat.id,
      );
      assert.ok(
        resumedRuns.length > 0,
        "private live run actually reads the exact originating group Tracking details",
      );
      const answers = resumed.snapshot.messages
        .filter(
          (message) =>
            message.kind === "twin" && message.seq > resumeSource.seq,
        )
        .map((message) => message.body)
        .join("\n");
      assert.ok(
        answers.includes(project) || answers.includes(sku),
        "generic return prompt causes a grounded project mention",
      );
      assert.ok(
        answers.includes(changeCode),
        "private generic reply mentions the new group-only random blocker",
      );
      assert.notEqual(
        resumedRequest.session_id,
        groupMilestone.request.session_id,
        "group-to-private resumption uses a genuinely different SDK session",
      );
      assert.match(
        answers,
        /审批|批准|决策|approval|sign.off/i,
        "reply resurfaces the actual unresolved approval",
      );
      const stillActive = (await state(returned.page, own.id)).tracking.find(
        (topic) => topic.id === milestone.topic.id,
      );
      assert.ok(
        stillActive && isOngoing(stillActive.status),
        "unapproved project was not falsely completed or recreated",
      );
      assert.notEqual(
        resumedRequest.run_id,
        originalRequest.run_id,
        "resumption uses a distinct SDK run",
      );
      t.record("resumption", {
        status: "passed",
        source_group_request_id: groupMilestone.request.id,
        source_group_session_id: groupMilestone.request.session_id,
        group_topic_id: groupMilestone.topic.id,
        group_change_code: changeCode,
        alternate_private_message_ids_before_return: alternateBeforeReturn,
        original_request_id: originalRequest.id,
        new_request_id: resumedRequest.id,
        original_run_id: originalRequest.run_id,
        new_run_id: resumedRequest.run_id,
        original_session_id: originalRequest.session_id,
        new_session_id: resumedRequest.session_id,
        same_topic_id: stillActive.id,
        tracking_exclusively_caused_recall_claimed: false,
        note: "Same private chat normally retains its SDK session ID; authorized chat history is also available.",
      });
      await returned.page.screenshot({
        path: path.join(t.artifactDir, "continuous-workflow-resumed.png"),
        fullPage: true,
      });
      t.record("phase_two_screenshot", "continuous-workflow-resumed.png");
    },
  },
];
module.exports = {
  cases,
  inspect,
  state,
  send,
  waitTwin,
  openSidebar,
  isOngoing,
};
