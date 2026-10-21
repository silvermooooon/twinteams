/** Reproducible synthetic API/WS performance probe; never uses product records. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:8000';
const baseline = process.env.TWINTEAMS_PERFORMANCE_BASELINE === '1';
const reportPath = process.env.TWINTEAMS_PERFORMANCE_UI_REPORT || '/tmp/twinteams-performance-ui-report.json';
const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
let browser;
async function wait(fn,label,timeout=10000){const end=Date.now()+timeout;while(Date.now()<end){if(await fn())return;await sleep(20);}throw new Error(label);}
async function safety(){
  const context=await browser.newContext({viewport:{width:1360,height:900},reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(8000);
  const mock=await fixture(page,'en',true),sockets=[],calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
  const old={id:'old-safe',seq:1,chat_id:'private-alex',author_id:'owner-alex',kind:'human',body:'Synthetic older page',state:'published',read_by:[],created_at:'2026-10-21T00:00:00Z'};
  const latest={...old,id:'latest-safe',seq:2,body:'Synthetic latest page'};
  const pending={id:'safety-pending',chat_id:'private-alex',source_message_id:latest.id,target_twin_id:'twin-alex',requester_id:'owner-alex',source:'private',status:'queued',accepted_at:old.created_at,can_cancel:true,can_retry:false};
  const mayaTitle=mock.actors.find(a=>a.kind==='twin'&&a.owner_id==='owner-maya').name;const maya={...mock.chat,id:'private-maya',title:mayaTitle,member_owner_ids:['owner-maya']};
  const state={owner:'owner-alex',revision:1,newMessage:false,reset:false,hold:null,holdStatus:200,holding:false,groupHold:null};
  const answer=(route,value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  const hint=()=>{state.revision++;sockets.at(-1).send(JSON.stringify({v:1,type:'changed',chat_id:'private-alex',revision:state.revision}));};
  await page.routeWebSocket('**/api/ws',socket=>{sockets.push(socket);socket.onMessage(raw=>{const f=JSON.parse(String(raw));if(f.type==='command')socket.send(JSON.stringify({v:1,type:'ack',command_id:f.command_id}));});});
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;calls.push(path);
    if(path==='/api/auth/login'){state.owner='owner-maya';return answer(route,{owner:mock.actors.find(a=>a.id===state.owner),current_owner_id:state.owner,development_mode:true});}
    if(path==='/api/auth/logout')return answer(route,{ok:true});
    if(path==='/api/auth/me')return answer(route,{owner:mock.actors.find(a=>a.id===state.owner),current_owner_id:state.owner,development_mode:true});
    if(path==='/api/bootstrap')return answer(route,{current_owner_id:state.owner,actors:mock.actors,chats:[state.owner==='owner-alex'?mock.chat:maya,mock.group],reminders:[],model_configured:true,development_mode:true});
    const match=path.match(/^\/api\/chats\/(private-alex|private-maya|group-shared)$/);
    if(match){const id=match[1],older=!!url.searchParams.get('before_seq');const snapshot={revision:state.revision,chat:id==='group-shared'?mock.group:id==='private-maya'?maya:mock.chat,messages:id==='private-alex'&&!state.reset?(older?[old]:[latest,...(state.newMessage?[{...latest,id:'new-safe',seq:3,body:'Synthetic new canonical'}]:[])]):[],requests:id==='private-alex'&&!state.reset?[{...pending}]:[],has_more:id==='private-alex'&&!older&&!state.reset,before_seq:older?1:2,approvals:[],tracking:[],members:mock.actors,settings:{reply_for_me_enabled:false,prompt_override:null,sidebar_open:false,sidebar_tab:'workspace'}};
      if(older&&state.hold){const hold=state.hold;state.holding=true;await hold;state.holding=false;if(state.holdStatus!==200)return answer(route,{detail:'Synthetic reauthorization failure'},state.holdStatus);}
      if(id==='group-shared'&&state.groupHold)await state.groupHold;
      return answer(route,snapshot);
    }
    if(path.endsWith('/read'))return answer(route,{ok:true});return route.fallback();
  });
  await page.goto(base+'/#chat');await page.locator('[data-message-id=latest-safe]').waitFor();await page.locator('.connection-state.connected').waitFor({state:'attached'});await sleep(200);
  const bootstrapBefore=calls.filter(p=>p==='/api/bootstrap').length,socketBefore=sockets.length;
  sockets.at(-1).close({code:1000,reason:'Synthetic reconnect'});await wait(()=>sockets.length===socketBefore+1,'browser reconnect opens replacement owner socket');await wait(()=>calls.filter(p=>p==='/api/bootstrap').length>bootstrapBefore,'reconnect must synchronize bootstrap');await page.locator('.connection-state.connected').waitFor({state:'attached'});await sleep(200);
  await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();await page.locator('[data-message-id=old-safe]').waitFor();
  let release;state.hold=new Promise(r=>release=r);state.holdStatus=500;state.newMessage=true;pending.status='running';hint();await wait(()=>state.holding,'older page starts');await page.locator('.typing-row[data-reply-phase=running]').waitFor();
  assert.equal(await page.locator('[data-message-id=new-safe]').count(),0,'metadata update must not mix new body with old pages');release();state.hold=null;await page.locator('[data-message-id=new-safe]').waitFor();assert.equal(await page.locator('[data-message-id=old-safe]').count(),0,'failed reauthorization removes stale old pages');
  await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();await page.locator('[data-message-id=old-safe]').waitFor();state.hold=new Promise(r=>release=r);state.holdStatus=403;hint();await wait(()=>state.holding,'ACL request starts');release();state.hold=null;await wait(async()=>await page.locator('.composer textarea').count()===0,'older-page ACL refusal hides detail');
  state.holdStatus=200;await page.locator('.conversation-list .chat-item').filter({hasText:'Shared team'}).click();await page.locator('.chat-header h2').filter({hasText:'Shared team'}).waitFor();await page.locator('.conversation-list .chat-item').filter({hasText:mock.chat.title}).click();await page.locator('[data-message-id=latest-safe]').waitFor();
  if(!await page.locator('[data-message-id=old-safe]').count())await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();await page.locator('[data-message-id=old-safe]').waitFor();state.hold=new Promise(r=>release=r);hint();await wait(()=>state.holding,'chat switch old page starts');await page.locator('.conversation-list .chat-item').filter({hasText:'Shared team'}).click();await page.locator('.chat-header h2').filter({hasText:'Shared team'}).waitFor();release();state.hold=null;await sleep(150);assert.equal(await page.locator('[data-message-id=old-safe]').count(),0);
  await page.locator('.conversation-list .chat-item').filter({hasText:mock.chat.title}).click();await page.locator('[data-message-id=latest-safe]').waitFor();if(!await page.locator('[data-message-id=old-safe]').count())await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();await page.locator('[data-message-id=old-safe]').waitFor();state.hold=new Promise(r=>release=r);hint();await wait(()=>state.holding,'reset old page starts');state.reset=true;mock.remoteReset();sockets.at(-1).send(JSON.stringify({v:1,type:'personal_changed',section:'reset'}));await wait(async()=>await page.locator('[data-message-id=latest-safe]').count()===0,'reset removes previous generation');release();state.hold=null;await sleep(200);assert.equal(await page.locator('[data-message-id=old-safe]').count(),0);
  let releaseGroup;state.groupHold=new Promise(r=>releaseGroup=r);await page.locator('.conversation-list .chat-item').filter({hasText:'Shared team'}).click();await sleep(80);await page.getByRole('button',{name:'Account',exact:true}).click();await page.getByRole('button',{name:'Sign out / switch account',exact:true}).click();await page.getByPlaceholder('DEV-AAA').fill('DEV-BBB');await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.locator('.chat-header h2').filter({hasText:mayaTitle}).waitFor();releaseGroup();state.groupHold=null;await sleep(200);assert.equal(await page.locator('.chat-header h2').innerText(),mayaTitle);assert.equal(await page.locator('[data-message-id]').count(),0);assert.deepEqual(errors,[]);
  const result={simulated_disconnect_reconnect_resync:true,metadata_before_old_pages:true,no_mixed_body_windows:true,old_page_500_cleared:true,old_page_403_hides_detail:true,chat_reset_owner_isolation:true};
  await context.close();console.log('PASS synthetic safety',JSON.stringify(result));return result;
}
async function run(){
  browser=await chromium.launch({headless:true,channel:process.env.TWINTEAMS_BROWSER_CHANNEL||'chrome'});
  if(process.env.TWINTEAMS_PERFORMANCE_SAFETY_ONLY==='1'){const result=await safety();fs.writeFileSync(reportPath,JSON.stringify({base,safety:result},null,2));await browser.close();return;}
  const context=await browser.newContext({viewport:{width:1360,height:900},reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(10000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const mock=await fixture(page,'en',true);const calls=[],sockets=[],frames=[];
  const message=n=>({id:'synthetic-'+n,chat_id:'private-alex',seq:n,author_id:n%2?'owner-alex':'twin-alex',kind:n%2?'human':'twin',body:`## Synthetic message ${n}\n\nRepeated **safe fixture** with [link](https://example.invalid/).\n\n| Field | Value |\n|---|---|\n| One | Two |\n\n\`\`\`js\nconst value = ${n};\n\`\`\``,state:'published',created_at:'2026-10-21T00:00:00Z',read_by:[],attachments:[],reactions:[]});
  const messages=Array.from({length:200},(_,i)=>message(i+1));
  const request={id:'synthetic-pending',chat_id:'private-alex',source_message_id:'synthetic-199',target_twin_id:'twin-alex',requester_id:'owner-alex',source:'private',status:'queued',can_cancel:true,can_retry:false,accepted_at:'2026-10-21T00:00:01Z'};
  const state={revision:1,delay:120,olderDelay:500,inflight:0,maxInflight:0,blockedOlder:false,releaseOlder:null,commit:null};
  const settings={reply_for_me_enabled:false,prompt_override:null,sidebar_open:false,sidebar_tab:'workspace'};
  const answer=(route,value)=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  function hint(){state.revision++;sockets.at(-1).send(JSON.stringify({v:1,type:'changed',chat_id:'private-alex',revision:state.revision}));}
  await page.routeWebSocket('**/api/ws',socket=>{sockets.push(socket);socket.onMessage(raw=>{const frame=JSON.parse(String(raw));if(frame.type!=='command')return;frames.push(frame);if(frame.command==='send'){const m={...message(201),id:'canonical-send',body:frame.payload.body,author_id:'owner-alex',kind:'human',client_request_key:frame.payload.client_request_key};state.commit=m;socket.send(JSON.stringify({v:1,type:'ack',command_id:frame.command_id,result:{message:m,request:null}}));hint();}else socket.send(JSON.stringify({v:1,type:'ack',command_id:frame.command_id}));});});
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;calls.push({path,method:req.method(),before:url.searchParams.get('before_seq'),at:Date.now()});
    const match=path.match(/^\/api\/chats\/(private-alex|group-shared)$/);
    if(match){state.inflight++;state.maxInflight=Math.max(state.maxInflight,state.inflight);const older=!!url.searchParams.get('before_seq');const chat=match[1]==='private-alex'?mock.chat:mock.group;const snapshot={revision:state.revision,chat,messages:chat.kind==='private'?(older?messages.slice(0,100):[...messages.slice(100),...(state.commit?[state.commit]:[])]):[],requests:chat.kind==='private'?[{...request}]:[],approvals:[],tracking:[],members:mock.actors,settings,has_more:chat.kind==='private'&&!older,before_seq:older?1:101};try{await sleep(older?state.olderDelay:state.delay);return await answer(route,snapshot);}finally{state.inflight--;}}
    if(path.endsWith('/settings'))return answer(route,{...settings,...req.postDataJSON()});
    if(path==='/api/bootstrap'||path.startsWith('/api/me/twin'))await sleep(state.delay);
    return route.fallback();
  });
  const cdp=await context.newCDPSession(page);await cdp.send('Emulation.setCPUThrottlingRate',{rate:4});
  const started=Date.now();await page.goto(base+'/#chat');await page.locator('[data-message-id="synthetic-200"]').waitFor();await page.locator('.connection-state.connected').waitFor({state:'attached'});await wait(()=>state.inflight===0,'startup settle');await sleep(300);
  const visibleMs=Date.now()-started;const count=list=>Object.fromEntries([...new Set(list.map(c=>c.path))].map(path=>[path,list.filter(c=>c.path===path).length]));
  const startup=count(calls),initialSockets=sockets.length;
  const draft='Synthetic unsent performance draft';const typedStart=Date.now();await page.locator('.composer textarea').pressSequentially(draft);const typingMs=Date.now()-typedStart;
  const switchStart=calls.length,switchSockets=sockets.length;const switchAt=Date.now();await page.locator('.conversation-list .chat-item').filter({hasText:'Shared team'}).click();await page.locator('.chat-header h2').filter({hasText:'Shared team'}).waitFor();await wait(()=>state.inflight===0,'group settle');await sleep(150);const switchMs=Date.now()-switchAt,switchCalls=count(calls.slice(switchStart)),newSwitchSockets=sockets.length-switchSockets;
  await page.locator('.conversation-list .chat-item').filter({hasText:mock.chat.title}).click();await page.locator('[data-message-id="synthetic-200"]').waitFor();await wait(()=>state.inflight===0,'back settle');await sleep(200);
  await page.getByRole('button',{name:'Load earlier messages',exact:true}).click();await page.locator('[data-message-id="synthetic-1"]').waitFor();await wait(()=>state.inflight===0,'older settle');
  // Latest authorized request state should render before reauthorizing slow older pages.
  request.status='running';const runningCalls=calls.length,runningAt=Date.now();hint();await page.locator('.typing-row[data-reply-phase="running"]').waitFor();const runningVisibleMs=Date.now()-runningAt;await wait(()=>state.inflight===0,'running settle');
  const refreshCalls=calls.slice(runningCalls).filter(c=>/^\/api\/chats\/private-alex$/.test(c.path));
  const burstStart=calls.length;for(let i=0;i<20;i++)hint();await sleep(1200);await wait(()=>state.inflight===0,'burst settle');const burstDetailCalls=calls.slice(burstStart).filter(c=>c.path==='/api/chats/private-alex').length;
  // Fast metadata ACK with deliberately slow GET must leave the optimistic send visible.
  state.delay=800;await page.locator('.composer textarea').fill('Synthetic send timing');await page.evaluate(()=>{const button=document.querySelector('.send-button');window.__performanceSend={clicked:null,visible:null,sent:null};button.addEventListener('click',()=>window.__performanceSend.clicked=performance.now(),{once:true,capture:true});new MutationObserver(()=>{const p=window.__performanceSend;if(p.clicked===null)return;if(document.querySelector('article[data-local-send]')&&p.visible===null)p.visible=performance.now();if(document.querySelector('article[data-local-send] .local-send-status')?.textContent.includes('Sent')&&p.sent===null)p.sent=performance.now();}).observe(document.querySelector('.message-area'),{childList:true,subtree:true,characterData:true});});
  await page.getByRole('button',{name:'Send message',exact:true}).click();await wait(()=>frames.some(f=>f.command==='send'),'synthetic send command');await wait(async()=>await page.evaluate(()=>window.__performanceSend.sent!==null),'ACK visible');const send=await page.evaluate(()=>{const p=window.__performanceSend;return{echo_ms:p.visible-p.clicked,ack_visible_ms:p.sent-p.clicked};});await page.locator('[data-message-id="canonical-send"]').waitFor();
  assert.equal(await page.locator('.composer textarea').inputValue(),'');assert.deepEqual(errors,[]);
  const report={base,baseline,fixture_only:true,real_provider:false,cpu_throttle:4,api_delay_ms:120,older_page_delay_ms:500,visible_ms:visibleMs,startup,initial_sockets:initialSockets,typing_ms:typingMs,typed_chars:draft.length,switch_ms:switchMs,switch_calls:switchCalls,switch_new_sockets:newSwitchSockets,running_visible_ms:runningVisibleMs,running_snapshot_gets:refreshCalls.length,burst_detail_gets:burstDetailCalls,max_inflight:state.maxInflight,send,asset:await page.locator('script[src]').getAttribute('src')};
  fs.writeFileSync(reportPath,JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  if(!baseline){assert.equal(newSwitchSockets,0,'switching chat must reuse owner socket');assert.equal(switchCalls['/api/bootstrap']||0,0,'switch must not reload bootstrap');assert.ok(runningVisibleMs<650,'latest running state must render before the slow older-page roundtrip');assert.ok(burstDetailCalls<=4,'20 concurrent hints must coalesce');assert.ok(send.echo_ms<250&&send.ack_visible_ms<400,'local send/ACK should not wait snapshot');}
  if(!baseline){report.safety=await safety();fs.writeFileSync(reportPath,JSON.stringify(report,null,2));}
  await browser.close();
}
run().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
