/** Synthetic API/WS + real Chrome; no application mutations or model requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:8000';
const baseline = process.env.TWINTEAMS_WORKSPACE_BASELINE === '1';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
async function scenario(kind, nonempty) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const mock = await fixture(page, 'en', true);
  const chatId = kind === 'private' ? 'private-alex' : 'group-shared';
  const owner = { id:'owner-alex', owner_id:'owner-alex', kind:'human', name:'AAA', initials:'AA', color:'#7962bd' };
  const twin = { id:'twin-alex', owner_id:owner.id, kind:'twin', name:"AAA's Twin", initials:'AT', color:'#6dbdad' };
  const extraOwners = ['maya','jordan'].map((slug,i)=>({id:`owner-${slug}`,owner_id:`owner-${slug}`,kind:'human',name:i?'CCC':'BBB',initials:i?'CC':'BB',color:'#428b7d'}));
  const groupMembers = [owner,twin,...extraOwners];
  const chat = { id:chatId, kind, title:kind === 'private' ? twin.name : 'Shared team', member_owner_ids:kind==='group'?[owner.id,...extraOwners.map(a=>a.id)]:[owner.id], unread_count:0 };
  const settings = { reply_for_me_enabled:false, prompt_override:null, sidebar_open:true, sidebar_tab:'workspace' };
  const entry = i => ({id:`file-${i}`,kind:'file',name:`document-${i}.txt`,path:`docs/document-${i}.txt`,size:24});
  let entries = nonempty ? [{id:'docs',kind:'folder',name:'docs',path:'docs',children:Array.from({length:35},(_,i)=>entry(i))}] : [];
  let revision = 1, failure = null, workspaceRequests = 0, previews = 0;
  const reply = (route,data,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  await page.route('**/api/chats/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    if (path === `/api/chats/${chatId}`) {
      if (failure === 'abort') return route.abort('failed');
      if (failure) return reply(route,{detail:`Synthetic ${failure}`},failure);
      return reply(route,{revision,chat,messages:[],requests:[],approvals:[],tracking:[],settings,members:kind==='group'?groupMembers:[owner,twin]});
    }
    if (path === `/api/chats/${chatId}/workspace`) { workspaceRequests++; await sleep(210); return reply(route,entries); }
    if (path === `/api/chats/${chatId}/activity`) return reply(route,{items:[],next_cursor:null});
    if (path === `/api/chats/${chatId}/settings`) { Object.assign(settings,req.postDataJSON()); return reply(route,settings); }
    if (path.startsWith(`/api/chats/${chatId}/workspace/files/`) && path.endsWith('/preview')) { previews++; return route.fulfill({contentType:'text/plain',body:'Synthetic preview content.\nNo actual user files.'}); }
    return route.fallback();
  });
  if(kind==='group') await page.route('**/api/bootstrap',route=>reply(route,{current_owner_id:owner.id,actors:groupMembers,chats:[{id:'private-alex',kind:'private',title:twin.name,member_owner_ids:[owner.id],unread_count:0},chat],reminders:[],model_configured:true,development_mode:true}));
  await page.goto(`${base}/#/chat`);
  if (kind === 'group') await page.locator('.conversation-list').getByText('Shared team',{exact:true}).click();
  await page.locator('.detail-sidebar').waitFor();
  await page.locator(nonempty ? '.tree-row.folder' : '.detail-sidebar .empty-state').waitFor();
  await page.waitForTimeout(300);
  const input = page.locator('.composer textarea');
  await input.fill('Unsent regression draft'); await input.focus();
  await page.evaluate(() => {
    window.__workspaceProbe = { sidebar:document.querySelector('.detail-sidebar'), input:document.querySelector('.composer textarea'), loading:0, emptyRemoved:0, focus:[] };
    const probe=window.__workspaceProbe;
    const record = () => {if(document.querySelector('.detail-sidebar .loading-row'))probe.loading++;};
    probe.observer=new MutationObserver(records=>{record();for(const mutation of records)for(const node of mutation.removedNodes)if(node.nodeType===1&&(node.matches('.empty-state')||node.querySelector('.empty-state')))probe.emptyRemoved++;});
    probe.observer.observe(document.querySelector('.detail-sidebar'),{childList:true,subtree:true});
    document.addEventListener('focusin',event=>probe.focus.push(event.target.tagName+':'+(event.target.className||'')));
  });
  const hint = async () => {revision++; mock.sockets.at(-1).send(JSON.stringify({v:1,type:'changed',chat_id:chatId,revision})); await page.waitForTimeout(390);};
  for(let i=0;i<4;i++) await hint();
  const emptyMetrics = await page.evaluate(() => {const p=window.__workspaceProbe;return {loading:p.loading,emptyRemoved:p.emptyRemoved,sameSidebar:p.sidebar===document.querySelector('.detail-sidebar'),sameInput:p.input===document.querySelector('.composer textarea'),focused:document.activeElement===p.input};});
  if (baseline && !nonempty) assert.ok(emptyMetrics.loading>0 && emptyMetrics.emptyRemoved>0,'baseline must reproduce empty/loading flicker');
  else assert.equal(emptyMetrics.loading,0,'background refresh must not display initial loading');
  assert.equal(emptyMetrics.sameSidebar,true);assert.equal(emptyMetrics.sameInput,true);assert.equal(emptyMetrics.focused,true);
  assert.equal(await input.inputValue(),'Unsent regression draft');
  for(const status of [500,'abort']) {failure=status;await hint();assert.equal(await page.evaluate(()=>window.__workspaceProbe.sidebar===document.querySelector('.detail-sidebar')),true);assert.equal(await input.evaluate(el=>el===document.activeElement),true);failure=null;await hint();}
  let treeStable = null, previewStable = null;
  if(nonempty) {
    const folder=page.locator('.tree-row.folder');await folder.click();await page.locator('.tree-row.file').first().waitFor();
    await page.locator('.detail-content').evaluate(el=>el.scrollTop=340);
    await page.evaluate(()=>{const p=window.__workspaceProbe;p.folder=document.querySelector('.tree-row.folder');p.scroll=document.querySelector('.detail-content').scrollTop;});
    await input.focus(); await hint();
    treeStable=await page.evaluate(()=>{const p=window.__workspaceProbe;return p.folder===document.querySelector('.tree-row.folder')&&p.folder.getAttribute('aria-expanded')==='true'&&Math.abs(p.scroll-document.querySelector('.detail-content').scrollTop)<2;});
    assert.equal(treeStable,true);
    await page.locator('.tree-row.file').first().click();await page.locator('.text-file-preview').filter({hasText:'Synthetic preview'}).waitFor();
    await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).focus();
    await page.evaluate(()=>{window.__workspaceProbe.modal=document.querySelector('[role=dialog]');window.__workspaceProbe.modalFocus=document.activeElement;});
    // File list updates while the already opened preview remains a stable snapshot.
    entries=[{...entries[0],children:[...entries[0].children,entry(40)]}];
    for(let i=0;i<3;i++) await hint();
    failure=500;await hint();failure=null;await hint();
    previewStable=await page.evaluate(()=>{const p=window.__workspaceProbe;return p.modal===document.querySelector('[role=dialog]')&&p.modalFocus===document.activeElement;});
    assert.equal(previewStable,true);assert.equal(previews,1,'updates do not remount/reload existing preview');
    await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});
    assert.equal(await page.locator('.tree-row.folder').getAttribute('aria-expanded'),'true');
    await page.screenshot({path:`/tmp/twinteams-workspace-${kind}-populated.png`});
  } else await page.screenshot({path:`/tmp/twinteams-workspace-${kind}-empty.png`});
  const permissionChecks=[];
  for(const status of [401,403,404,409]) {
    failure=status;await hint();assert.equal(await page.locator('.detail-sidebar').count(),0,`${status} must remove old authorized detail`);assert.equal(await page.locator('.composer textarea').count(),0);
    permissionChecks.push(status);failure=null;await hint();await page.locator('.detail-sidebar').waitFor();
  }
  assert.deepEqual(errors,[]);
  assert.equal(mock.calls.filter(c=>c.path.endsWith('/messages')).length,0);
  const assets=await page.locator('script[src]').evaluateAll(nodes=>nodes.map(n=>new URL(n.src).pathname));
  await context.close();
  console.log('PASS scenario',kind,nonempty?'populated':'empty');
  return {kind,nonempty,emptyMetrics,treeStable,previewStable,workspaceRequests,permissionChecks,assets,errors};
}
async function main(){
  browser=await chromium.launch({headless:true,channel:process.env.TWINTEAMS_BROWSER_CHANNEL||'chrome'});
  const results=[];for(const kind of ['private','group'])for(const nonempty of [false,true])results.push(await scenario(kind,nonempty));
  await browser.close();
  const report={base,baseline,synthetic_api_ws:true,real_chrome:true,real_llm:false,results};
  fs.writeFileSync(process.env.TWINTEAMS_WORKSPACE_REPORT||'/tmp/twinteams-workspace-focus-report.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
}
main().catch(async error=>{console.error(error);await browser?.close();process.exitCode=1;});
