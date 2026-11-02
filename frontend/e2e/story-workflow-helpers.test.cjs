const test = require('node:test');
const assert = require('node:assert/strict');
const { initialRequest, isSettled, peerQuestion, sharedReadHashes, consultationSettled } = require('./story-workflow-live.cjs');

test('waits for the consultation child result even when it has no run id', () => {
  const data = {
    requests: [{ chat_id: 'private-maya', causal_chain_id: 'chain', session_id: 'parent', run_id: 'run', status: 'completed' }],
    runs: [{ root_run_id: 'parent/run', run_id: 'run', status: 'completed' }, { root_run_id: 'parent/run', run_id: 'child', status: 'completed' }],
    commands: [{ session_id: 'parent', run_id: null, kind: 'child_result', state: 'accepted' }],
  };
  assert.equal(consultationSettled(data), false);
  data.commands[0].state = 'completed';
  data.runs[1].status = 'running';
  assert.equal(consultationSettled(data), false);
  data.runs[1].status = 'completed';
  data.commands.push({ session_id: 'new-child', run_id: 'queued-run', kind: 'prompt', state: 'accepted' });
  assert.equal(consultationSettled(data), false);
  data.commands[1].state = 'completed';
  assert.equal(consultationSettled(data), true);
});

function evidence() {
  return {
    requests: [{ id: 'original', source: 'private', chat_id: 'private-a', source_message_id: 'message', status: 'completed' }],
    runs: [{ status: 'completed' }],
    commands: [{ kind: 'prompt', state: 'completed' }, { kind: 'child_result', state: 'completed' },
      { kind: 'approval_request', state: 'accepted' }, { kind: 'approval_decision', state: 'accepted' },
      { kind: 'approval_operation', state: 'accepted' }],
    center_results: [{ state: 'processed', result_sent: true, notified: true }],
    center: { deliveries: [{ status: 'processed', notified: true }] },
    workflow: { messages: [] }, approvals: [], questions: [], live_budget: { attempts: [{ response_at: 1 }] },
  };
}

test('finds the owner request when its continuation shares the source message', () => {
  const data = evidence();
  data.requests.unshift({ id: 'continuation', source: 'approval_result', chat_id: 'private-a', source_message_id: 'message', status: 'running' });
  assert.equal(initialRequest(data, 'private-a', 'message').id, 'original');
  assert.equal(initialRequest(data, 'private-b', 'message'), undefined);
});

test('settles durable approval facts but waits for executable child work and Center results', () => {
  const data = evidence();
  assert.equal(isSettled(data), true);
  data.commands[1].state = 'accepted';
  assert.equal(isSettled(data), false);
  data.commands[1].state = 'completed';
  data.center_results[0].result_sent = false;
  assert.equal(isSettled(data), false);
  data.center_results[0].result_sent = true;
  data.center_results[0].notified = false;
  data.center.deliveries[0].notified = false;
  assert.equal(isSettled(data), true, 'a processed silent delivery can be settled');
  data.live_budget.attempts[0] = {};
  assert.equal(isSettled(data), false);
});

test('accepts only an explicitly stopped child whose parent and child result completed', () => {
  const data = evidence();
  data.requests[0].id = 'parent';
  data.runs.push({ source: 'subagent', session_id: 'child', run_id: 'run', status: 'stopped' });
  data.commands.push({ kind: 'prompt', session_id: 'child', run_id: 'run', state: 'stopped' });
  data.commands.push({ kind: 'child_result', command_id: 'subagent-result:child:run:result', state: 'completed' });
  data.tools = [{ name: 'stop_agent', success: true, request_id: 'parent', arguments: { task_id: 'child' } }];
  assert.equal(isSettled(data), true);
  assert.equal(consultationSettled(data), true);
  data.tools[0].success = false;
  assert.throws(() => isSettled(data), /failed or cancelled/);
  data.tools[0].success = true;
  data.commands.at(-1).state = 'accepted';
  assert.throws(() => isSettled(data), /failed or cancelled/);
  data.commands.at(-1).state = 'completed';
  data.requests[0].status = 'running';
  assert.throws(() => isSettled(data), /failed or cancelled/);
  data.requests[0].status = 'completed';
  data.runs[1].status = 'failed';
  assert.throws(() => isSettled(data), /failed or cancelled/);
});

test('records held sends for review but does not hide owner decisions or cancelled calls', () => {
  const data = evidence();
  data.workflow.messages.push({ state: 'pending', id: 'held' });
  assert.equal(isSettled(data), true, 'a held message remains visible for semantic review');
  data.workflow.messages = [];
  data.approvals.push({ status: 'pending', id: 'approval' });
  assert.throws(() => isSettled(data), /blocked for an owner decision/);
  data.approvals = [];
  data.live_budget.attempts.push({ error_type: 'CancelledError', response_at: 1 });
  assert.throws(() => isSettled(data), /failed or cancelled/);
});

test('a failed request cannot be waived by other completed work', () => {
  const data = evidence();
  data.requests.push({ id: 'failed', status: 'failed', session_id: 's', run_id: 'r' });
  data.runs.push({ session_id: 's', run_id: 'r', status: 'failed' });
  data.commands.push({ command_id: 'failed', kind: 'prompt', state: 'stopped' });
  assert.throws(() => isSettled(data), /failed or cancelled/);
  assert.equal(consultationSettled(data), false);
});

test('selects only a new topical question caused by the Scrum Master Duet', () => {
  const data = evidence();
  data.workflow.chats = [{ id: 'duet', kind: 'duet', owner_ids: ['owner-maya', 'owner-jordan', 'owner-riley'] }];
  data.workflow.messages = [{ id: 'sm-message', author_id: 'twin-maya', state: 'published' },
    { id: 'other-message', author_id: 'twin-riley', state: 'published' }];
  data.requests.push({ id: 'request', source_message_id: 'sm-message' }, { id: 'other-request', source_message_id: 'other-message' });
  data.questions = [
    { id: 'old', owner_id: 'owner-jordan', origin_chat_id: 'duet', origin_request_id: 'request', status: 'pending', question: 'Can you take the story this sprint?' },
    { id: 'wrong-peer', owner_id: 'owner-jordan', origin_chat_id: 'duet', origin_request_id: 'other-request', status: 'pending', question: 'Can you take the story this sprint?' },
    { id: 'right', owner_id: 'owner-jordan', origin_chat_id: 'duet', origin_request_id: 'request', status: 'pending', question: 'Can you take the story this sprint?' },
  ];
  assert.equal(peerQuestion(data, 'owner-jordan', new Set(['old']), new Set()).id, 'right');
  assert.equal(peerQuestion(data, 'owner-jordan', new Set(['old']), new Set(['sm-message'])), undefined);
  data.workflow.messages[0].causal_chain_id = 'chain';
  data.workflow.messages[1].causal_chain_id = 'chain';
  assert.equal(peerQuestion(data, 'owner-jordan', new Set(['old']), new Set()).id, 'wrong-peer');
  assert.equal(peerQuestion(data, 'owner-jordan', new Set(['old']), new Set(['sm-message'])), undefined);
  data.questions = [];
  data.workflow.chats.push({ id: 'private-jordan', kind: 'private', private_owner_id: 'owner-jordan' });
  data.requests.push({ id: 'duet-request', chat_id: 'duet', source_message_id: 'sm-message' });
  data.workflow.messages.push({ id: 'private-question', chat_id: 'private-jordan', kind: 'twin', state: 'published', request_id: 'duet-request', body: 'Can you take this sprint work?' });
  assert.equal(peerQuestion(data, 'owner-jordan', new Set(), new Set()).prompt_kind, 'message');
});

test('recognizes both authorized workspace reads and SDK snapshot reads', () => {
  const data = evidence();
  data.workflow.attachments = [{ id: 'shared', sha256: 'snapshot-hash' }];
  data.tools = [
    { name: 'read_workspace_file', owner_id: 'owner-maya', success: true, result: { content: 'story bytes' } },
    { name: 'read', owner_id: 'owner-maya', success: true, attachment_ids_read: ['shared'] },
    { name: 'read', owner_id: 'owner-maya', success: true, attachment_ids_partially_read: ['partial'] },
    { name: 'read', owner_id: 'other', success: true, attachment_ids_read: ['other'] },
  ];
  const hashes = sharedReadHashes(data, 'owner-maya');
  assert.ok(hashes.has('snapshot-hash'));
  assert.ok(hashes.has(require('node:crypto').createHash('sha256').update('story bytes').digest('hex')));
  assert.equal(hashes.size, 2);
});
