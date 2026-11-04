/** Real provider and Center worker, driven from the real browser UI. */
const { assert, api, questionAnswerInput, privateChat, sendMessage, poll } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');

function inspect() {
  return JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    'inspect', '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8' }));
}

async function state(page, id) {
  return api(page, `/chats/${id}?limit=100`);
}

async function send(page, text) {
  return (await sendMessage(page, text)).getAttribute('data-message-id');
}

async function finish(page, chat, source) {
  return poll(async () => {
    const evidence = inspect();
    const request = evidence.requests.find(row => row.source_message_id === source);
    if (request?.status === 'failed') throw Error(`Request ${request.id} failed`);
    if (request?.status === 'completed') {
      return { evidence, snapshot: await state(page, chat.id), request };
    }
    return false;
  }, 'model request completion', { timeout: 180000, interval: 2000 });
}

async function answer(page, chat, snapshot, text) {
  const question = snapshot.questions?.find(row => row.status === 'pending');
  if (question) {
    const card = page.locator(`[data-question-id="${question.id}"]`);
    await (await questionAnswerInput(card)).fill(text);
    await card.getByRole('button', { name: 'Send answer' }).click();
    return;
  }
  const id = await send(page, text);
  await finish(page, chat, id);
}

function assertOneQuestion(evidence, delivery, snapshot) {
  const questions = evidence.questions.filter(row => row.origin_request_id === delivery.request_id);
  assert.ok(questions.length <= 1, 'at most one question per delivery');
  if (questions.length) {
    const questionMessageIds = new Set(questions.map(row => row.message_id));
    const otherMessages = snapshot.messages.filter(row => row.request_id === delivery.request_id
      && row.kind === 'twin' && !questionMessageIds.has(row.id));
    assert.ok(otherMessages.every(row => !/[?？]/.test(row.body)),
      'do not repeat the question after displaying its card');
  }
}

const cases = [{
  name: 'real model and browser: scoped invitations and attendance across Center',
  timeout: 850000,
  run: async t => {
    const sessions = {};
    for (const [name, code] of [['alex', 'DEV-AAA'], ['maya', 'DEV-BBB'], ['jordan', 'DEV-CCC']]) {
      const { page } = await t.session(code);
      sessions[name] = { page, chat: await privateChat(page) };
    }
    const { page, chat } = sessions.alex;
    const prompt = 'Please organize a team lunch next Friday and invite people to sign up.';
    const source = await send(page, prompt);
    const first = await finish(page, chat, source);
    const replies = first.snapshot.messages
      .filter(row => row.request_id === first.request.id && row.kind === 'twin')
      .map(row => row.body);
    const questions = (first.snapshot.questions || [])
      .filter(row => row.status === 'pending').map(row => row.question);
    const scopeText = [...replies, ...questions].join('\n');
    t.record('scope_check', {
      prompt, source, scopeText, audienceAnswer: 'Please invite Alex, Maya, and Jordan.',
      centerMessages: first.evidence.center.messages,
      toolCalls: first.evidence.tools.filter(row => row.request_id === first.request.id),
    });
    await page.screenshot({ path: path.join(t.artifactDir, 'scope-question.png'), fullPage: true });
    assert.match(scopeText, /who|whom|which people|audience|invitees|邀请|哪些人|谁/i,
      'Duet asks for the audience');
    assert.equal(first.evidence.center.messages.length, 0, 'no publication before audience specified');

    await answer(page, chat, first.snapshot, 'Please invite Alex, Maya, and Jordan.');
    const published = await poll(() => {
      const evidence = inspect();
      return evidence.center.messages.length === 1 && evidence;
    }, 'first publication', { timeout: 180000, interval: 2000 });
    const firstMessage = published.center.messages[0];
    assert.deepEqual([...firstMessage.recipients].sort(), ['owner-alex', 'owner-jordan', 'owner-maya']);
    const completed = await poll(() => {
      const evidence = inspect();
      const rows = evidence.center.deliveries.filter(row => row.message_id === firstMessage.id);
      return rows.length === 3 && rows.every(row => row.status === 'processed' && row.notified) && evidence;
    }, 'all three processed deliveries', { timeout: 240000, interval: 2000 });
    assert.equal(completed.center.responses.length, 0, 'delivery alone must not record attendance');
    assert.ok(completed.center.deliveries.filter(row => row.message_id === firstMessage.id)
      .every(row => !row.human_read), 'notification does not claim human read');
    const deliveryStates = {};
    for (const [name, entry] of Object.entries(sessions)) {
      const delivery = completed.center.deliveries.find(row =>
        row.message_id === firstMessage.id && row.owner_id === `owner-${name}`);
      const snapshot = await state(entry.page, entry.chat.id);
      assertOneQuestion(completed, delivery, snapshot);
      deliveryStates[name] = {
        delivery,
        messages: snapshot.messages.filter(row => row.request_id === delivery.request_id)
          .map(row => ({ kind: row.kind, body: row.body })),
        questions: (snapshot.questions || []).filter(row => row.status === 'pending')
          .map(row => ({ id: row.id, question: row.question })),
      };
      await entry.page.screenshot({ path: path.join(t.artifactDir, `delivery-${name}.png`), fullPage: true });
      const text = [...deliveryStates[name].messages.map(row => row.body),
        ...deliveryStates[name].questions.map(row => row.question)].join('\n');
      assert.match(text, /lunch|午餐|聚餐/i);
      assert.match(text, /attend|join|participat|sign.?up|participation|参加|报名|出席/i);
      assert.match(text, /\?|？|would you|will you|do you|是否|愿意|吗/i,
        'asks whether owner wishes to participate');
    }
    t.record('all_delivery', {
      message: firstMessage, owners: deliveryStates,
      publishCalls: completed.tools.filter(row => row.name === 'publish_center_message'),
    });
    for (const name of ['alex', 'maya', 'jordan']) {
      const entry = sessions[name];
      const snapshot = await state(entry.page, entry.chat.id);
      await answer(entry.page, entry.chat, snapshot, 'Yes, I will attend. Please tell the organizer.');
    }
    const responses = await poll(() => {
      const evidence = inspect();
      const rows = evidence.center.responses.filter(row =>
        row.message_id === firstMessage.id && row.response === 'attending');
      return rows.length === 3 && evidence;
    }, 'three structured attendance responses', { timeout: 240000, interval: 2000 });
    t.record('attendance', {
      ownerAnswer: 'Yes, I will attend. Please tell the organizer.',
      responses: responses.center.responses,
      calls: responses.tools.filter(row => row.name === 'respond_center_message'),
    });

    const secondSource = await send(page,
      'Please organize a book club next Wednesday and invite only Maya and Jordan to sign up.');
    await finish(page, chat, secondSource);
    const second = await poll(() => {
      const evidence = inspect();
      return evidence.center.messages.length === 2 && evidence;
    }, 'subset publication', { timeout: 180000, interval: 2000 });
    const secondMessage = second.center.messages.find(row => row.id !== firstMessage.id);
    assert.deepEqual([...secondMessage.recipients].sort(), ['owner-jordan', 'owner-maya']);
    const final = await poll(() => {
      const evidence = inspect();
      const rows = evidence.center.deliveries.filter(row => row.message_id === secondMessage.id);
      return rows.length === 2 && rows.every(row => row.status === 'processed' && row.notified) && evidence;
    }, 'subset deliveries', { timeout: 240000, interval: 2000 });
    const subsetDeliveries = final.center.deliveries.filter(row => row.message_id === secondMessage.id);
    assert.deepEqual(subsetDeliveries.map(row => row.owner_id).sort(), ['owner-jordan', 'owner-maya']);
    assert.ok(subsetDeliveries.every(row => !row.human_read));
    const subsetQuestions = {};
    for (const name of ['maya', 'jordan']) {
      const entry = sessions[name];
      const delivery = subsetDeliveries.find(row => row.owner_id === `owner-${name}`);
      const snapshot = await state(entry.page, entry.chat.id);
      assertOneQuestion(final, delivery, snapshot);
      const messages = snapshot.messages.filter(row => row.request_id === delivery.request_id).map(row => row.body);
      const questions = (snapshot.questions || []).filter(row => row.status === 'pending').map(row => row.question);
      const text = [...messages, ...questions].join('\n');
      assert.match(text, /book club|读书|读书会/i);
      assert.match(text, /attend|join|participat|sign.?up|参加|报名|出席/i);
      assert.match(text, /\?|？|would you|will you|do you|是否|愿意|吗/i);
      subsetQuestions[name] = { messages, questions };
      await entry.page.screenshot({ path: path.join(t.artifactDir, `subset-${name}.png`), fullPage: true });
    }
    assert.equal(final.center.responses.filter(row => row.message_id === secondMessage.id).length, 0);
    t.record('subset', {
      prompt: 'Please organize a book club next Wednesday and invite only Maya and Jordan to sign up.',
      source: secondSource, message: secondMessage, deliveries: subsetDeliveries,
      questions: subsetQuestions, publishCalls: final.tools.filter(row => row.name === 'publish_center_message'),
    });
    const settled = await poll(() => {
      const evidence = inspect();
      return evidence.requests.every(row => row.status === 'completed') && evidence;
    }, 'all requests complete', { timeout: 120000, interval: 2000 });
    t.record('closure', {
      requests: settled.requests.map(row => ({ id: row.id, source: row.source, status: row.status })),
      failedTools: settled.tools.filter(row => !row.success),
    });
    await fs.writeFile(path.join(t.artifactDir, 'center-final.json'), JSON.stringify(settled, null, 2));
  },
}];
module.exports = { cases };
