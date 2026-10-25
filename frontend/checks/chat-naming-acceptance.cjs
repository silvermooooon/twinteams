/** Real Chrome over fully synthetic API/WS; no actual messages or model requests. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('playwright');
const {fixture}=require('./my-twin-ui.cjs');
const base=process.env.TWINTEAMS_UI_URL||'http://127.0.0.1:8000';
let browser;
async function scenario(viewer){
 const context=await browser.newContext({viewport:{width:1360,height:900},reducedMotion:'reduce'});
 const page=await context.newPage();const mock=await fixture(page,'en',true);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const humans=['alex','maya','jordan'].map((slug,i)=>({id:`owner-${slug}`,owner_id:`owner-${slug}`,kind:'human',name:['AAA','BBB','CCC'][i],initials:['AA','BB','CC'][i],color:'#7962bd'}));
 const twins=humans.map(h=>({id:h.id.replace('owner','twin'),owner_id:h.id,kind:'twin',name:`${h.name}'s Duet`,initials:'TT',color:'#6dbdad'}));
 const owner=humans[viewer],twin=twins[viewer],peer=humans[viewer?0:1];
 const privateId=`private-${viewer?'maya':'alex'}`;
 let tripleName='Launch group',failRename=false;const writes=[];
 const privateChat=()=>({id:privateId,kind:'private',title:twin.name,member_owner_ids:[owner.id],can_rename:false,unread_count:0});
 const pair=()=>({id:'pair',kind:'group',title:peer.name,member_owner_ids:[humans[0].id,humans[1].id],can_rename:false,unread_count:0});
 const triple=()=>({id:'triple',kind:'group',title:tripleName,member_owner_ids:humans.map(h=>h.id),can_rename:true,unread_count:0});
 const reply=(route,data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
 await page.addInitScript(()=>{window.__sent=[];const send=WebSocket.prototype.send;WebSocket.prototype.send=function(data){try{window.__sent.push(JSON.parse(data));}catch{}return send.call(this,data);};});
 await page.route('**/api/**',async route=>{
  const request=route.request(),path=new URL(request.url()).pathname;
  if(path==='/api/auth/me')return reply(route,{owner,current_owner_id:owner.id,development_mode:true});
  if(path==='/api/me/twin')return reply(route,{actor:twin});
  if(path==='/api/bootstrap')return reply(route,{current_owner_id:owner.id,actors:[...humans,...twins],chats:[privateChat(),pair(),triple()],reminders:[],model_configured:true,development_mode:true});
  if(path==='/api/settings')return reply(route,{default_reply_prompt:'Rewrite concisely'});
  const found=[privateChat(),pair(),triple()].find(c=>path===`/api/chats/${c.id}`);
  if(found){
   if(request.method()==='PATCH'){const body=request.postDataJSON();writes.push(body);if(failRename)return reply(route,{detail:'Synthetic rename rejection'},503);tripleName=body.title.trim();return reply(route,triple());}
   return reply(route,{revision:1,chat:found,messages:[],requests:[],approvals:[],tracking:[],settings:{reply_for_me_enabled:true,prompt_override:null,sidebar_open:false,sidebar_tab:found.kind==='private'?'activity':'workspace'},members:[...humans,...twins].filter(a=>found.member_owner_ids.includes(a.owner_id))});
  }
  if(path.endsWith('/summary')){const id=path.split('/')[3];const c=[privateChat(),pair(),triple()].find(c=>c.id===id);if(c)return reply(route,c);}
  if(path.endsWith('/settings'))return reply(route,{reply_for_me_enabled:true,prompt_override:null,sidebar_open:false,sidebar_tab:'workspace'});
  return route.fallback();
 });
 await page.goto(base+'/#/chat');await page.locator('.composer textarea').waitFor();
 const input=page.locator('.composer textarea');
 assert.equal(await page.locator('.twin-toggle').count(),0,'Own Duet hides reply-for-me even when persisted true');
 assert.equal(await page.locator('.mode-hint').count(),0);assert.equal(await page.locator('.composer.reply-mode').count(),0);
 const normalPlaceholder=await input.getAttribute('placeholder');
 await input.fill('Synthetic private input');await page.locator('.send-button').click();
 const privateSend=await page.evaluate(()=>window.__sent.find(f=>f.type==='command'&&f.command==='send'));
 assert.equal(privateSend.payload.reply_for_me,false,'Private send ignores stale true setting');
 assert.equal(privateSend.chat_id,privateId);
 await page.locator('.chat-item').filter({has:page.getByText(peer.name,{exact:true})}).click();
 await page.locator('.chat-header h2').filter({hasText:peer.name}).waitFor();
 assert.equal(await page.locator('.chat-header h2').innerText(),peer.name);
 assert.equal(await page.getByRole('button',{name:'Rename group',exact:true}).count(),0);
 assert.equal(await page.locator('.twin-toggle').count(),1);assert.equal(await page.locator('.mode-hint').count(),1);
 assert.notEqual(await input.getAttribute('placeholder'),normalPlaceholder);
 await input.fill('Synthetic group input');await page.locator('.send-button').click();
 const groupSend=await page.evaluate(()=>window.__sent.find(f=>f.type==='command'&&f.command==='send'&&f.chat_id==='pair'));
 assert.equal(groupSend.payload.reply_for_me,true,'Group reply for me retained');
 await page.locator('.chat-item').filter({has:page.getByText('Launch group',{exact:true})}).click();
 await page.getByRole('button',{name:'Rename group',exact:true}).click();
 let dialog=page.getByRole('dialog');await dialog.getByLabel('Group name',{exact:true}).fill('   ');
 assert.equal(await dialog.getByRole('button',{name:'Save',exact:true}).isDisabled(),true);
 await dialog.getByLabel('Group name',{exact:true}).fill('  Updated launch  ');
 failRename=true;await dialog.getByRole('button',{name:'Save',exact:true}).click();
 await dialog.getByText('Synthetic rename rejection',{exact:true}).waitFor();
 assert.equal(await dialog.getByLabel('Group name',{exact:true}).inputValue(),'  Updated launch  ');
 failRename=false;await dialog.getByRole('button',{name:'Save',exact:true}).click();await dialog.waitFor({state:'hidden'});
 await page.locator('.chat-header h2').filter({hasText:'Updated launch'}).waitFor();
 assert.equal(writes.at(-1).title,'Updated launch');
 assert.equal(await page.locator('.chat-item').filter({has:page.getByText('Updated launch',{exact:true})}).count(),1);
 // Canonical change from another client refreshes both selected header and navigation.
 tripleName='Remote group name';mock.sockets.at(-1).send(JSON.stringify({v:1,type:'changed',chat_id:'triple',revision:2}));
 await page.locator('.chat-header h2').filter({hasText:'Remote group name'}).waitFor();
 assert.equal(await page.locator('.chat-item').filter({has:page.getByText('Remote group name',{exact:true})}).count(),1);
 await page.getByRole('button',{name:'Rename group',exact:true}).click();dialog=page.getByRole('dialog');assert.equal(await dialog.getByLabel('Group name',{exact:true}).getAttribute('maxlength'),'256');await dialog.getByRole('button',{name:'Cancel',exact:true}).click();
 assert.deepEqual(errors,[]);const asset=await page.locator('script[src]').getAttribute('src');
 await page.screenshot({path:`/tmp/twinteams-chat-naming-${viewer?'bbb':'aaa'}.png`});
 await context.close();return {viewer:owner.name,peer_title:peer.name,private_payload_false:true,group_payload_true:true,rename_error_keeps_draft:true,rename_ws_refresh:true,errors,asset};
}
(async()=>{browser=await chromium.launch({headless:true,channel:'chrome'});const results=[];for(const viewer of [0,1])results.push(await scenario(viewer));await browser.close();const report={result:'passed',real_chrome:true,synthetic_api_ws:true,real_llm:false,results};fs.writeFileSync('/tmp/twinteams-chat-naming-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));})().catch(async e=>{console.error(e);await browser?.close();process.exitCode=1;});
