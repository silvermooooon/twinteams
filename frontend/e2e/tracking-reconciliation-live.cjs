/** Real peer reports reconcile only relevant private Tracking work. */
const path = require('node:path');
const { assert, privateChat } = require('./helpers.cjs');
const { inspect, say, answer, waitFor, isSettled, reviewReports } = require('./story-workflow-live.cjs');

const cases = [{
  name: 'live peer reports reconcile private Tracking without duplicate reports or loops',
  timeout: 850000,
  run: async t => {
    assert.equal(process.env.TWINTEAMS_E2E_MODEL_MODE, 'live-gpt-6-luna');
    const a = await t.session('DEV-AAA');
    const b = await t.session('DEV-BBB');
    const owner = { page: a.page, chat: await privateChat(a.page), allowedPeers: ['owner-maya'], scope: 'once' };
    const peer = { page: b.page, chat: await privateChat(b.page), allowedPeers: ['owner-alex'], scope: 'once' };
    const initial = inspect();
    const initialTopics = new Set(initial.workflow.tracking.filter(row => row.chat_id === owner.chat.id).map(row => row.id));
    const unrelatedTurn = await say(owner, 'I am maintaining our onboarding checklist over the next several weeks. This is ongoing and unfinished; track it as its own goal. Do not do anything with it yet.');
    const unrelated = await waitFor('unrelated ongoing goal', [owner], evidence => {
      const topics = evidence.workflow.tracking.filter(row => row.chat_id === owner.chat.id && row.status === 'active' && !initialTopics.has(row.id));
      return topics.length === 1 && { topic: topics[0], evidence };
    });
    const beforeHandoff = new Set(unrelated.evidence.workflow.tracking.map(row => row.id));
    const handoffTurn = await say(owner, 'I am handing Maya two short story summaries to review. First: prevent duplicate refunds for the same request, with safe retries. Second: show customers a safe failure reason without internal details. Ask Maya’s Duet to send her those two summaries and ask her to review each. Track my handoff as a separate ongoing goal: my responsibility ends only when Maya confirms she has reviewed both summaries. Implementation is someone else’s later work, not part of my handoff.', [owner, peer]);
    const established = await waitFor('separate handoff goal and approved peer conversation', [owner, peer], evidence => {
      const topic = evidence.workflow.tracking.find(row => row.chat_id === owner.chat.id && row.status === 'active' && !beforeHandoff.has(row.id));
      const chat = evidence.workflow.chats.find(row => row.kind === 'duet' && row.duet_status === 'active'
        && row.owner_ids.length === 2 && row.owner_ids.includes('owner-alex') && row.owner_ids.includes('owner-maya'));
      const opening = chat && evidence.workflow.messages.find(row => row.chat_id === chat.id && row.author_id === 'twin-alex' && row.state === 'published');
      return topic && chat && opening && { evidence, topic, chat, opening };
    });
    let initialQuiet = 0;
    await waitFor('initial handoff to settle', [owner, peer], evidence => {
      initialQuiet = isSettled(evidence) ? initialQuiet + 1 : 0;
      return initialQuiet >= 3 && evidence;
    });
    t.record('established', { unrelated: unrelated.topic, handoff: established.topic, chat: established.chat, opening: established.opening,
      sourceMessages: [unrelatedTurn.source, handoffTurn.source] });
    await owner.page.screenshot({ path: path.join(t.artifactDir, 'owner-before-peer-report.png'), fullPage: true });

    await exercisePeerReports(t, { owner, peer, established, unrelated, initialTopics });
  },
}];
async function exercisePeerReports(t, { owner, peer, established, unrelated, initialTopics }) {
    async function peerUpdate(stage, text, wantedStatus) {
      const before = inspect();
      const priorMessages = new Set(before.workflow.messages.map(row => row.id));
      const priorRequests = new Set(before.requests.map(row => row.id));
      const priorUpdates = new Set(before.workflow.tracking_updates.map(row => row.id));
      const previous = before.workflow.tracking.find(row => row.id === established.topic.id);
      const previousOther = before.workflow.tracking.find(row => row.id === unrelated.topic.id);
      const pending = before.questions.filter(row => row.owner_id === 'owner-maya' && row.status === 'pending' && row.origin_chat_id === established.chat.id);
      assert.ok(pending.length <= 1, 'the owner can answer the single relevant question without guessing');
      const turn = pending.length ? await answer(peer, text, [owner, peer], pending[0].id) : await say(peer, text, [owner, peer]);
      let quiet = 0;
      const result = await waitFor(`${stage} peer report and private reconciliation`, [owner, peer], evidence => {
        const published = evidence.workflow.messages.filter(row => !priorMessages.has(row.id) && row.state === 'published');
        const peerReplies = published.filter(row => row.chat_id === established.chat.id && row.author_id === 'twin-maya');
        const reports = published.filter(row => row.chat_id === owner.chat.id && row.author_id === 'twin-alex'
          && evidence.requests.some(request => request.id === row.request_id && request.source === 'duet_message'));
        const reconciliations = evidence.requests.filter(row => !priorRequests.has(row.id) && row.source === 'peer_report_reconcile');
        const failed = reconciliations.find(row => row.status === 'failed');
        if (failed) throw Error(`Private reconciliation ${failed.id} failed`);
        const current = evidence.workflow.tracking.find(row => row.id === established.topic.id);
        const other = evidence.workflow.tracking.find(row => row.id === unrelated.topic.id);
        const updates = evidence.workflow.tracking_updates.filter(row => row.topic_id === established.topic.id && !priorUpdates.has(row.id));
        const otherUpdates = evidence.workflow.tracking_updates.filter(row => row.topic_id === unrelated.topic.id && !priorUpdates.has(row.id));
        const changed = updates.length || current?.description !== previous.description || current?.status !== previous.status;
        const activeWork = evidence.requests.some(row => ['queued', 'running'].includes(row.status));
        if (!peerReplies.length || !reports.length || !reconciliations.length || !reconciliations.every(row => row.status === 'completed')
          || current?.status !== wantedStatus || other?.status !== 'active' || !changed || activeWork) { quiet = 0; return false; }
        quiet = isSettled(evidence) ? quiet + 1 : 0;
        return quiet >= 3 && { evidence, current, other, updates, otherUpdates, reports, peerReplies, reconciliations };
      });
      const requests = result.reconciliations;
      assert.ok(requests.every(request => result.reports.some(report => report.id === request.source_message_id)), 'every new reconciliation follows a real peer-origin private report');
      assert.equal(new Set(requests.map(row => row.source_message_id)).size, requests.length, 'a private report queues at most one reconciliation');
      assert.deepEqual({ title: result.other.title, description: result.other.description, status: result.other.status },
        { title: previousOther.title, description: previousOther.description, status: previousOther.status }, 'unrelated work is unchanged');
      assert.equal(result.otherUpdates.length, 0, 'no new updates on unrelated work');
      for (const request of requests) {
        assert.equal(request.chat_id, owner.chat.id, 'reconciliation runs in the private owner scope');
        const messages = result.evidence.workflow.messages.filter(row => row.request_id === request.id);
        assert.equal(messages.length, 0, 'the silent reconciliation publishes no duplicate owner or peer message');
        const tools = result.evidence.tools.filter(row => row.request_id === request.id);
        assert.ok(tools.every(tool => ['list_tracking', 'get_tracking', 'update_tracking', 'add_tracking_update'].includes(tool.name)), 'reconciliation uses only existing Tracking tools');
      }
      const allTopics = result.evidence.workflow.tracking.filter(row => row.chat_id === owner.chat.id);
      assert.ok(allTopics.some(row => row.id === established.topic.id) && allTopics.some(row => row.id === unrelated.topic.id));
      assert.equal(allTopics.filter(row => !initialTopics.has(row.id)).length, 2, 'no duplicated private goals');
      const pendingQuestions = result.evidence.questions.filter(row => row.status === 'pending' && row.origin_chat_id === established.chat.id);
      if (stage === 'final') assert.equal(pendingQuestions.length, 0, 'no required Duet owner question remains unanswered');
      t.record(stage, { ownerTurn: turn.source || turn.question?.answer_message_id, pendingQuestions, peerReplies: result.peerReplies, reports: result.reports, reconciliations: requests,
        previous, current: result.current, previousOther, other: result.other, updates: result.updates, otherUpdates: result.otherUpdates });
      await reviewReports(t, stage, { peerOwnerMessage: text, peerReplies: result.peerReplies, privateReports: result.reports,
        reconciliations: requests, pendingQuestions, handoff: result.current, unrelated: result.other, trackingUpdates: result.updates,
        instruction: 'Compare the real peer owner input, peer reply and private report with the relevant Tracking status and scope. Approve only if the partial/final status is factually justified and unrelated work remains open.' });
      await owner.page.screenshot({ path: path.join(t.artifactDir, `${stage}-owner.png`), fullPage: true });
      return result;
    }

    await peerUpdate('partial', 'I reviewed the duplicate-refund summary. I have not reviewed the failure-reason summary yet. Please tell AAA’s Duet exactly that in our conversation.', 'active');
    const completed = await peerUpdate('final', 'I have now also reviewed the failure-reason summary. I have reviewed both summaries. Please tell AAA’s Duet; the later implementation work has not started.', 'completed');
    t.record('settled', { requests: completed.evidence.requests, runs: completed.evidence.runs, commands: completed.evidence.commands,
      questions: completed.evidence.questions, pendingMessages: completed.evidence.workflow.messages.filter(row => row.state === 'pending'),
      attempts: completed.evidence.live_budget.attempts });
}
module.exports = { cases, exercisePeerReports };
