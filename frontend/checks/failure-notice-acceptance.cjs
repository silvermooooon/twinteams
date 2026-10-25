/** Failure notices: synthetic API/WS only; no product messages, worker or model. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5198';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let browser;const results=[];
async function until(fn,label){for(let i=0;i<250;i++){if(await fn())return;await sleep(20);}throw new Error(label);}
async function setup(width=1360,blockedStorage=false){
  const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(7000);
  if(blockedStorage)await page.addInitScript(()=>{for(const name of ['getItem','setItem']){const original=Storage.prototype[name];Storage.prototype[name]=function(key,...args){if(String(key).startsWith('twinteams.dismissedFailures:'))throw new DOMException('Synthetic storage denial','SecurityError');return original.call(this,key,...args);};}});
  const mock=await fixture(page,'zh-CN',true),errors=[],actions=[],sockets=[];page.on('pageerror',e=>errors.push(e.message));
  const makeMessage=(chat,id='source-message',seq=1)=>({id,chat_id:chat,seq,author_id:'owner-alex',kind:'human',body:'Synthetic retained message '+id,state:'published',created_at:'2026-10-22T00:00:00Z',read_by:[]});
  const makeRequest=(chat,id,source)=>({id,chat_id:chat,source_message_id:source,target_twin_id:'twin-alex',requester_id:'owner-alex',source:'private',status:'failed',error:'Synthetic failure diagnostic',accepted_at:'2026-10-22T00:00:01Z',can_retry:true,can_cancel:false});
  const chats={'private-alex':mock.chat,'group-shared':mock.group,'private-maya':{...mock.chat,id:'private-maya',title:'Maya Duet',member_owner_ids:['owner-maya']}};
  const state={owner:'owner-alex',messages:{},requests:{},revision:{}};
  for(const chat of Object.keys(chats)){state.messages[chat]=chat==='private-maya'?[]:[makeMessage(chat)];state.requests[chat]=chat==='private-maya'?[]:[makeRequest(chat,'message-failure','source-message'),makeRequest(chat,'composer-failure','older-source')];state.revision[chat]=1;}
  const reply=(route,value)=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  await page.routeWebSocket('**/api/ws',socket=>{sockets.push(socket);socket.onMessage(raw=>{const f=JSON.parse(String(raw));if(f.type==='command')socket.send(JSON.stringify({v:1,type:'ack',command_id:f.command_id}));});});
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(path==='/api/auth/login'){state.owner=req.postDataJSON().username==='DEV-BBB'?'owner-maya':'owner-alex';return reply(route,{owner:mock.actors.find(a=>a.id===state.owner),current_owner_id:state.owner,development_mode:true});}
    if(path==='/api/auth/logout')return reply(route,{ok:true});
    if(path==='/api/auth/me')return reply(route,{owner:mock.actors.find(a=>a.id===state.owner),current_owner_id:state.owner,development_mode:true});
    if(path==='/api/bootstrap')return reply(route,{current_owner_id:state.owner,actors:mock.actors,chats:[state.owner==='owner-alex'?mock.chat:chats['private-maya'],mock.group],reminders:[],model_configured:true,development_mode:true});
    const match=path.match(/^\/api\/chats\/([^/]+)$/);
    if(match&&chats[match[1]]){const id=match[1];return reply(route,{revision:state.revision[id],chat:chats[id],messages:state.messages[id],requests:state.requests[id].map(r=>({...r,can_retry:r.status==='failed'&&state.owner==='owner-alex'})),approvals:[],tracking:[],members:mock.actors,settings:{reply_for_me_enabled:false,prompt_override:null,sidebar_open:false,sidebar_tab:'workspace'}});}
    const retry=path.match(/^\/api\/reply-requests\/([^/]+)\/retry$/);
    if(retry){actions.push({path,body:req.postDataJSON()});for(const id of Object.keys(chats))if(state.requests[id].some(r=>r.id===retry[1])){const old=state.requests[id].find(r=>r.id===retry[1]);const next={...old,id:'retry-new-id',status:'queued',can_retry:false,can_cancel:true};state.requests[id]=state.requests[id].filter(r=>r.id!==old.id).concat(next);state.revision[id]++;return reply(route,next);}}
    if(/\/reply-requests\/.+\/(cancel|retry)$/.test(path))actions.push({path});
    if(path.endsWith('/read'))return reply(route,{ok:true});return route.fallback();
  });
  function hint(chat='private-alex'){state.revision[chat]++;sockets.at(-1).send(JSON.stringify({v:1,type:'changed',chat_id:chat,revision:state.revision[chat]}));}
  await page.goto(base+'/#chat');await page.locator('.composer textarea').waitFor({state:'attached'});if(width<600)await page.locator('.conversation-list .chat-item').filter({hasText:mock.chat.title}).click();await page.locator('[data-failed-request="message-failure"]').waitFor();await page.locator('.connection-state.connected').waitFor({state:'attached'});
  const notice=id=>page.locator(`[data-failed-request="${id}"]`);
  async function finish(name){assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);results.push({name,width});console.log('PASS',name,width);await context.close();}
  return{page,state,notice,hint,actions,makeMessage,makeRequest,finish};
}
async function dismissalAndScopes(){
  const h=await setup(),{page,state,notice}=h;
  await notice('message-failure').getByRole('button',{name:'Dismiss',exact:true}).click();await notice('composer-failure').getByRole('button',{name:'Dismiss',exact:true}).click();assert.equal(await page.locator('[data-failed-request]').count(),0);assert.deepEqual(h.actions,[]);
  const stored=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('twinteams.dismissedFailures:'+JSON.stringify(['owner-alex','private-alex']))));assert.deepEqual(stored.sort(),['composer-failure','message-failure']);
  // The same failed request moves from the composer into a newly loaded old page.
  state.messages['private-alex'].unshift(h.makeMessage('private-alex','older-source',0));h.hint();await sleep(200);assert.equal(await page.locator('[data-failed-request]').count(),0);
  await page.reload();await page.locator('.composer textarea').waitFor();await sleep(150);assert.equal(await page.locator('[data-failed-request]').count(),0);assert.equal(state.requests['private-alex'].every(r=>r.status==='failed'),true);assert.equal(await page.locator('[data-message-id]').count(),2);
  await page.locator('.conversation-list .chat-item').filter({hasText:'Shared team'}).click();await notice('message-failure').waitFor();await notice('message-failure').getByRole('button',{name:'Dismiss',exact:true}).click();
  await page.getByRole('button',{name:'Account',exact:true}).click();await page.getByRole('button',{name:'Sign out / switch account',exact:true}).click();await page.getByPlaceholder('DEV-AAA').fill('DEV-BBB');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.locator('.composer textarea').waitFor();await page.locator('.conversation-list .chat-item').filter({hasText:'Shared team'}).click();await notice('message-failure').waitFor();assert.equal(await notice('message-failure').getByRole('button',{name:'Retry',exact:true}).count(),0,'only backend-approved retry is offered');
  await h.finish('two notices merge storage, reload/page migration, chat and owner isolation');
}
async function retryLifecycle(width){
  const h=await setup(width),{page,state,notice}=h;
  await notice('message-failure').getByRole('button',{name:'Dismiss',exact:true}).click();await notice('composer-failure').getByRole('button',{name:'Dismiss',exact:true}).click();
  state.requests['private-alex'].push(h.makeRequest('private-alex','new-failure','source-message'));h.hint();await notice('new-failure').waitFor();await notice('new-failure').getByRole('button',{name:'Retry',exact:true}).click();await until(()=>h.actions.length===1,'retry action');assert.ok(h.actions[0].body.client_request_key);await page.locator('.typing-row[data-request-id="retry-new-id"]').waitFor();assert.equal(await notice('new-failure').count(),0);
  const retry=state.requests['private-alex'].find(r=>r.id==='retry-new-id');retry.status='running';h.hint();await page.locator('.typing-row[data-reply-phase="running"]').waitFor();
  state.requests['private-alex']=state.requests['private-alex'].filter(r=>r.id!=='retry-new-id');h.hint();await until(async()=>await page.locator('.typing-row').count()===0,'completed retry leaves no notice');
  assert.equal(await page.locator('[data-failed-request]').count(),0);assert.equal(h.actions.length,1);assert.equal(await page.locator('[data-message-id="source-message"]').count(),1);
  await h.finish('new failed ID remains actionable and retry queued/running/completed stays canonical');
}
async function storageUnavailable(){
  const h=await setup(390,true),{page}=h;await h.notice('message-failure').getByRole('button',{name:'Dismiss',exact:true}).focus();await page.keyboard.press('Enter');await h.notice('composer-failure').getByRole('button',{name:'Dismiss',exact:true}).click();assert.equal(await page.locator('[data-failed-request]').count(),0);h.hint();await sleep(180);assert.equal(await page.locator('[data-failed-request]').count(),0);assert.equal(h.actions.length,0);await h.finish('storage denial still permits local keyboard/mobile dismissal');
}
async function alignment(){
  const h=await setup(),{page,state}=h;const other={...h.makeMessage('private-alex','twin-source',2),author_id:'twin-alex',kind:'twin'};state.messages['private-alex'].push(other);state.requests['private-alex'].push(h.makeRequest('private-alex','twin-message-failure',other.id));h.hint();await h.notice('twin-message-failure').waitFor();
  const own=await page.locator('[data-message-id="source-message"] .message-request-chips').evaluate(el=>getComputedStyle(el).justifyContent);
  const nonown=await page.locator('[data-message-id="twin-source"] .message-request-chips').evaluate(el=>getComputedStyle(el).justifyContent);
  assert.equal(own,'flex-end');assert.notEqual(nonown,'flex-end');await page.screenshot({path:'/tmp/twinteams-failure-notice-alignment.png'});await h.finish('own failure chips right-aligned and Duet message chips retain left alignment');
}
async function run(){browser=await chromium.launch({headless:true,channel:process.env.TWINTEAMS_BROWSER_CHANNEL||'chrome'});await dismissalAndScopes();await retryLifecycle(1360);await retryLifecycle(390);await storageUnavailable();await alignment();await browser.close();fs.writeFileSync('/tmp/twinteams-failure-notice-report.json',JSON.stringify({base,fixture_only:true,real_provider:false,tests:results},null,2));}
run().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
