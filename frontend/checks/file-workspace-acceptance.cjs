/** Independent compiled acceptance. All API/WS/files are synthetic, no provider calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5198';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDAAAAABJRU5ErkJggg==','base64');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const results = []; let browser;
async function until(fn, label) { for(let i=0;i<200;i++){if(await fn())return;await sleep(25);}throw new Error(label); }
async function setup(width=1360, group=false, workspace=false) {
  const context = await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  const mock = await fixture(page,'zh-CN',true); const chat = group ? mock.group : mock.chat;
  const state = { messages:[], requests:[], revision:1, frames:[], http:[], sockets:[], uploads:[], workspaceCalls:[], newFile:false,
    settings:{reply_for_me_enabled:group,prompt_override:null,sidebar_open:workspace,sidebar_tab:'workspace'} };
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const answer=(route,value)=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  function detail(){return {revision:state.revision,chat,messages:state.messages,requests:state.requests,approvals:[],tracking:[],members:mock.actors,settings:state.settings};}
  function hint(){state.revision++; for(const s of state.sockets)s.send(JSON.stringify({v:1,type:'changed',chat_id:chat.id,revision:state.revision}));}
  function commit(payload) {
    const message={id:'canonical-'+(state.messages.length+1),chat_id:chat.id,seq:state.messages.length+1,author_id:'owner-alex',kind:payload.reply_for_me?'reply_for_me':'human',body:payload.reply_for_me?'Synthetic rewritten reply.':payload.body,raw_owner_input:payload.reply_for_me?payload.body:undefined,state:'published',created_at:'2026-10-21T00:00:00Z',read_by:[],client_request_key:payload.client_request_key,attachments:payload.reply_for_me?[]:state.uploads.filter(a=>payload.attachment_ids.includes(a.id))};
    state.messages.push(message); return message;
  }
  await page.routeWebSocket('**/api/ws',s=>{state.sockets.push(s);s.onMessage(raw=>{const f=JSON.parse(String(raw));if(f.type!=='command')return;if(f.command==='send')state.frames.push(f);else s.send(JSON.stringify({v:1,type:'ack',command_id:f.command_id}));});});
  await page.route('**/api/**',async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname;
    if(path===`/api/chats/${chat.id}`)return answer(route,detail());
    if(path===`/api/chats/${chat.id}/settings`){Object.assign(state.settings,req.postDataJSON());return answer(route,state.settings);}
    if(path===`/api/chats/${chat.id}/messages`){const payload=req.postDataJSON();state.http.push(payload);const message=commit(payload);return answer(route,{message,request:null});}
    if(path===`/api/chats/${chat.id}/attachments`&&req.method()==='POST'){
      const name=url.searchParams.get('name'),image=name.endsWith('.png');
      const item={id:'upload-'+(state.uploads.length+1),name,mime_type:req.headers()['content-type'],size:req.postDataBuffer().length,kind:image?'image':'file',url:'/api/synthetic-image',width:image?1:null,height:image?1:null};state.uploads.push(item);return answer(route,item);
    }
    if(path==='/api/synthetic-image')return route.fulfill({contentType:'image/png',body:png});
    if(path===`/api/chats/${chat.id}/workspace`){
      const folder=url.searchParams.get('path')||'';state.workspaceCalls.push(folder);
      return answer(route,folder===''?[{id:'generated-dir',name:'generated',kind:'folder',path:'generated'}]:folder==='generated'?[{id:'reports-dir',name:'reports',kind:'folder',path:'generated/reports'}]:[{id:'path:opaque+中文=',name:'report.md',kind:'file',path:'generated/reports/report.md',size:30},...(state.newFile?[{id:'new-file',name:'new-result.txt',kind:'file',path:'generated/reports/new-result.txt',size:10}]:[])]);
    }
    if(path.startsWith(`/api/chats/${chat.id}/workspace/files/`)&&path.endsWith('/preview'))return route.fulfill({contentType:'text/plain',body:'Synthetic agent-generated file.'});
    if(path.endsWith('/read'))return answer(route,{ok:true});
    return route.fallback();
  });
  await page.goto(base+'/#chat');await page.locator('.composer textarea').waitFor({state:'attached'});
  if(group||width<600)await page.locator('.conversation-list .chat-item').filter({hasText:chat.title}).click();
  await page.locator('.composer textarea').waitFor();await page.locator('.connection-state.connected').waitFor({state:'attached'});
  await sleep(120);
  async function finish(name){assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);results.push({name,width});console.log('PASS',name,width);await context.close();}
  return {page,state,chat,hint,commit,finish};
}
async function uploadCase(width,group){
  const h=await setup(width,group),{page,state}=h;
  const input=page.locator('.chat-main > input[type=file]').first();assert.equal(await input.getAttribute('accept'),null);
  await input.setInputFiles([{name:'report.pdf',mimeType:'application/pdf',buffer:Buffer.from('synthetic PDF')},{name:'archive.zip',mimeType:'application/zip',buffer:Buffer.from('synthetic ZIP')},{name:'opaque.bin',mimeType:'application/octet-stream',buffer:Buffer.from([0,255,1])},{name:'tiny.png',mimeType:'image/png',buffer:png}]);
  await until(async()=>await page.locator('.draft-attachment.ready').count()===4,'four uploads ready');
  assert.equal(await page.getByText(/Unsupported attachment|not supported for/i).count(),0);
  await page.getByRole('button',{name:'Send message',exact:true}).click();
  await until(()=>state.frames.length===1,'small attachment send uses websocket');
  const frame=state.frames[0];assert.deepEqual(frame.payload.attachment_ids,state.uploads.map(a=>a.id));assert.equal(frame.payload.body,'');assert.equal(frame.payload.reply_for_me,group);
  const local=page.locator('article[data-local-send]');await local.waitFor();assert.equal(await local.locator('.file-attachment').count(),3);assert.equal(await local.locator('.message-image-button').count(),1);
  if(group)await local.getByText('Preparing reply…',{exact:true}).waitFor();
  await page.screenshot({path:`/tmp/twinteams-files-${group?'rewrite':'private'}-${width}.png`,fullPage:true});
  const message=h.commit(frame.payload);
  state.sockets.at(-1).send(JSON.stringify({v:1,type:'ack',command_id:frame.command_id,result:{message,request:null}}));h.hint();
  await page.locator(`[data-message-id="${message.id}"]`).waitFor();await until(async()=>await local.count()===0,'canonical replaces local');
  if(!group){assert.equal(await page.locator('article .file-attachment').count(),3);assert.equal(await page.locator('article .message-image-button').count(),1);}
  // Historical records still render as downloadable cards without an unsupported warning.
  state.messages.push({id:'legacy-file',chat_id:h.chat.id,seq:2,author_id:'owner-alex',kind:'human',body:'',state:'published',read_by:[],created_at:message.created_at,attachments:[{id:'legacy',name:'legacy.dat',size:12,mime_type:'application/octet-stream',kind:'unsupported',url:'/api/legacy-file'}]});h.hint();
  await page.getByRole('link',{name:'legacy.dat',exact:true}).waitFor();
  await h.finish('all types local/canonical + files-only '+(group?'own reply-for-me':'private'));
}
async function longCase(width,escape=false){
  const h=await setup(width),{page,state}=h;const body=escape?'"'.repeat(40000):'中文'.repeat(20000);
  await page.locator('.composer textarea').fill(body);await page.getByRole('button',{name:'Send message',exact:true}).click();
  await until(()=>state.http.length===1,'oversized UTF8/escaped frame must use HTTP');assert.equal(state.frames.length,0);assert.equal(state.http[0].body,body);assert.ok(state.http[0].client_request_key);
  await page.locator('[data-message-id="canonical-1"]').waitFor();assert.equal(await page.locator('[data-message-id="canonical-1"] .message-text').textContent(),body);
  assert.equal(await page.locator('.composer textarea').inputValue(),'');await h.finish(escape?'escaped JSON long message HTTP':'40k Chinese full message HTTP');
}
async function workspaceCase(width){
  const h=await setup(width,false,true),{page,state}=h;
  await page.getByRole('button',{name:'generated',exact:true}).waitFor();assert.ok(state.workspaceCalls.length > 0 && state.workspaceCalls.every(path=>path===''));
  await page.getByRole('button',{name:'generated',exact:true}).click();await page.getByRole('button',{name:'reports',exact:true}).waitFor();assert.equal(state.workspaceCalls.filter(path=>path==='generated').length,1);assert.equal(state.workspaceCalls.filter(path=>path==='generated/reports').length,0);
  await page.getByRole('button',{name:'reports',exact:true}).click();await page.getByRole('button',{name:'report.md',exact:true}).click();
  await page.getByRole('dialog').getByText('Synthetic agent-generated file.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('dialog').getByRole('link',{name:'Download',exact:true}).getAttribute('href'),`/api/chats/${h.chat.id}/workspace/files/${encodeURIComponent('path:opaque+中文=')}`);
  await page.keyboard.press('Escape');state.newFile=true;const before=state.workspaceCalls.length;await page.getByRole('button',{name:'Refresh workspace',exact:true}).click();await page.getByRole('button',{name:'new-result.txt',exact:true}).waitFor();
  assert.ok(state.workspaceCalls.slice(before).includes(''));assert.ok(state.workspaceCalls.slice(before).includes('generated/reports'));
  const settled=state.workspaceCalls.length;await sleep(300);assert.equal(state.workspaceCalls.length,settled,'no workspace polling');
  await page.screenshot({path:`/tmp/twinteams-workspace-lazy-${width}.png`,fullPage:true});await h.finish('lazy generated files / encoded IDs / refresh expanded folders');
}
async function run(){
  browser=await chromium.launch({headless:true,channel:process.env.TWINTEAMS_BROWSER_CHANNEL||'chrome'});
  const failures=[];
  for(const [name,fn]of [['private-files',()=>uploadCase(1360,false)],['mobile-rewrite-files',()=>uploadCase(390,true)],['long-chinese',()=>longCase(1360)],['long-escaped',()=>longCase(390,true)],['workspace-desktop',()=>workspaceCase(1360)],['workspace-mobile',()=>workspaceCase(390)]]){
    if(process.env.TWINTEAMS_FILES_CASE&&!new RegExp(process.env.TWINTEAMS_FILES_CASE).test(name))continue;
    try{await fn();}catch(error){failures.push({name,error:error.message});console.error('FAIL',name,error);const p=browser.contexts().at(-1)?.pages().at(-1);await p?.screenshot({path:`/tmp/twinteams-files-${name}-failure.png`,fullPage:true}).catch(()=>{});}finally{for(const c of browser.contexts())await c.close();}
  }
  fs.writeFileSync('/tmp/twinteams-file-workspace-report.json',JSON.stringify({base,fixture_only:true,tests:results,failures},null,2));await browser.close();assert.deepEqual(failures,[]);
}
run().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
