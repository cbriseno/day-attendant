import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const group=process.argv[3]||'all';
assert.ok(['all','details','dates','conversation'].includes(group),'Unknown browser regression group');
const task=(title)=>({id:randomUUID(),title,state:'todo',priority:'medium',due:null,until:null,context:'',next:'',awaiting:false,remainingMinutes:null,dependsOn:[],relatedTo:[],blocked:false,blockedBy:[],attachmentIds:[],source:'general'});
const target=task('Responsabilidad original'),a=task('A'),b=task('B'),c=task('C');
// Historical DST fixtures remain stable as jurisdictions change future rules.
const state={profile:{id:randomUUID(),name:'Controlled profile',timezone:'America/Edmonton',locale:'en',harness:'codex',model:'controlled'},timezone:'America/Edmonton',now:'2025-10-28T12:00:00Z',revision:1,epoch:0,synthetic:true,readOnly:false,tasks:[target,a,b,c],events:[],proposals:[],messages:[],calendar_events:[],context:{availableMinutes:null,availableUntil:null,rest:false},agent:{connected:true,model:'controlled'},diagnostics:{operations:[],reminders:[]},operations_status:{},notification_policy:{enabled:false,weekdays:[0,1,2,3,4],start:'09:00',end:'18:00',quietStart:'22:00',quietEnd:'08:00',intervalMinutes:10,unchangedRepeatMinutes:1440,dailyBudget:12,pausedUntil:null}};
const requests=[],settings=[],chats=[],clears=[];let catalogs=null;
const server=createServer(async(req,res)=>{
 try{
  if(req.url.startsWith('/api/')){
   let body='';for await(const chunk of req)body+=chunk;body=body?JSON.parse(body):{};
   let data=state;
   if(req.url==='/api/proposals'){
    requests.push(body);
    if(body.expected_revision!==state.revision){res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:{code:'stale_revision',message:'State changed; reopen this editor.'}}));return}
    const p=body.payload,t=state.tasks.find(t=>t.id===p.uuid);
    if(body.action==='dependency_update')t.dependsOn=p.dependsOn;
    if(body.action==='related_update')t.relatedTo=p.relatedTo;
    if(body.action==='task_update')for(const key of ['title','priority','due','context','next','awaiting'])if(key in p)t[key]=p[key];
    const nextState={front_activate:'active',front_todo:'todo',task_done:'done',front_retire:'retired',task_reopen:'todo'}[body.action];
    if(nextState)t.state=nextState;
    if(body.action==='context_update')Object.assign(state.context,p);
    if(body.action==='event_update')Object.assign(state.calendar_events.find(e=>e.uuid===p.uuid),{title:p.title,start_at:p.start,end_at:p.end});
    if(body.action==='event_cancel')state.calendar_events=state.calendar_events.filter(e=>e.uuid!==p.uuid);
    state.revision++;
    data={id:randomUUID(),key:body.key,intent:{action:body.action,payload:p},phase:'applied',generation:0,facts:[]};
   }else if(req.url==='/api/settings'){
    settings.push(body);
    if(body.expected_revision!==undefined&&body.expected_revision!==state.revision){res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:{message:'State changed'}}));return}
    const {notification_policy,...profileValues}=body.values||{};Object.assign(state.profile,profileValues);
    if(profileValues.timezone)state.timezone=profileValues.timezone;
    if(notification_policy)state.notification_policy=notification_policy;
    data=state.profile;
   }else if(req.url==='/api/chat'){
    chats.push(body);const at=state.now;
    state.messages.push({key:body.key,role:'user',text:body.text,status:'completed',at,attachments:[]},{key:body.key+':assistant',role:'assistant',text:'Controlled reply.',status:'completed',at,attachments:[]});
    data={key:body.key,status:'completed'};
   }else if(req.url==='/api/models'){
    data=catalogs||{codex:[],claude:[],errors:{}};
   }else if(req.url==='/api/chat/clear'){
    clears.push(body);state.messages=[];data={key:body.key,status:'cleared'};
   }else if(req.url==='/api/backups')data={backups:[]};
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,data}));return;
  }
  const filename={'/':'index.html','/interface.js':'interface.js','/daily.js':'daily.js','/locales.js':'locales.js'}[req.url];
  if(!filename){res.writeHead(404);res.end();return}
  res.writeHead(200,{'Content-Type':filename.endsWith('.js')?'application/javascript':'text/html'});res.end(await readFile(path.join(root,'day_attendant/ui',filename)));
 }catch(error){res.writeHead(500);res.end(String(error))}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const profile=await mkdtemp(path.join(tmpdir(),'day-review-chrome-'));
const chrome=spawn(process.argv[2],['--headless=new','--no-sandbox','--disable-gpu','--no-first-run','--disable-background-networking','--disable-component-update','--disable-extensions','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
let socket;
try{
 const endpoint=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Chrome startup timed out: '+output)),10000);
  chrome.stderr.on('data',chunk=>{output+=chunk;const match=output.match(/DevTools listening on (ws:\/\/\S+)/);if(match){clearTimeout(timer);resolve(match[1])}});
  chrome.once('error',reject);chrome.once('exit',code=>reject(Error('Chrome exited: '+code+' '+output)));
 });
 socket=new WebSocket(endpoint);await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});
 let serial=0;const pending=new Map();socket.addEventListener('message',event=>{const message=JSON.parse(event.data),p=pending.get(message.id);if(p){pending.delete(message.id);message.error?p.reject(Error(JSON.stringify(message.error))):p.resolve(message.result)}});
 const rpc=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params,sessionId}))});
 const {targetId}=await rpc('Target.createTarget',{url:'about:blank'}),{sessionId}=await rpc('Target.attachToTarget',{targetId,flatten:true});
 const evaluate=async expression=>{const value=await rpc('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true},sessionId);if(value.exceptionDetails)throw Error(JSON.stringify(value.exceptionDetails));return value.result.value};
 const wait=async expression=>{const deadline=Date.now()+5000;while(Date.now()<deadline){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,15))}throw Error('Timed out: '+expression)};
 const click=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
 const fill=(id,value)=>evaluate(`(()=>{const e=document.getElementById(${JSON.stringify(id)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 const close=()=>evaluate(`document.querySelectorAll('dialog[open]').forEach(d=>d.close())`);
 const refresh=()=>evaluate('(async()=>{while(dayUI.flow.busy)await new Promise(resolve=>setTimeout(resolve,5));await dayUI.flow.refresh()})()');
 const detail=()=>evaluate(`dayUI.openDetail(${JSON.stringify(target.id)},document.getElementById('list-view'))`);
 const submit=id=>evaluate(`document.querySelector('#'+${JSON.stringify(id)}+' form').requestSubmit()`);
 const saved=id=>wait(`!document.getElementById(${JSON.stringify(id)}).open`);
 const failed=id=>wait(`!document.getElementById(${JSON.stringify(id+'-error')}).hidden`);
 await rpc('Emulation.setTimezoneOverride',{timezoneId:'UTC'},sessionId);
 await rpc('Page.navigate',{url:'http://127.0.0.1:'+server.address().port},sessionId);
 await wait('globalThis.dayDaily && daySnapshot().profile');

 if(group==='all'||group==='details'){
  for(const [status,control,action,label] of [
   ['todo','detail-focus','front_activate','Start'],['snoozed','detail-focus','front_activate','Resume'],
   ['done','detail-focus','task_reopen','Reopen'],['retired','detail-focus','task_reopen','Reopen'],
   ['active','detail-done','task_done','Complete'],['todo','confirm-retire','front_retire','Yes, retire']
  ]){
   target.state=status;state.revision++;await refresh();await detail();
   if(control==='confirm-retire')await click('#detail-retire');
   assert.equal(await evaluate(`document.getElementById(${JSON.stringify(control)}).textContent.trim()`),label);
   await fill('estimate-input','17');const opening=state.revision,before=requests.length;
   target.state=['done','retired'].includes(status)?'todo':'done';state.revision++;await refresh();
   const externalState=target.state;await evaluate("dayUI.notice('')");await click('#'+control);await wait(`daySnapshot().revision>=${state.revision}`);
   await wait(`document.getElementById('status').textContent!=='' || !document.getElementById('detail-dialog').open`);
   const sent=requests[before];assert.ok(sent,label+' must submit');
   assert.equal(sent.action,action,label+' keeps its displayed action');assert.deepEqual(sent.payload,{uuid:target.id});
   assert.equal(sent.expected_revision,opening,label+' keeps its opening revision');assert.equal(target.state,externalState);
   assert.equal(await evaluate("document.getElementById('detail-dialog').open"),true);
   assert.equal(await evaluate("document.getElementById('estimate-input').value"),'17');
   assert.equal(await evaluate("document.getElementById('estimate-error').hidden"),false);
   assert.match(await evaluate("document.getElementById('estimate-error').textContent"),/change/i);await close();
   target.state=status;state.revision++;await refresh();await detail();
   if(control==='confirm-retire')await click('#detail-retire');
   await click('#'+control);await saved('detail-dialog');assert.equal(requests.at(-1).action,action);
  }
  target.state='active';state.revision++;await refresh();const opening=state.revision;
  target.state='done';state.revision++;
  await evaluate("dayUI.notice('')");await click(`[data-id="${target.id}"][data-action="todo"]`);
  await wait("document.getElementById('status').textContent!==''");
  assert.equal(requests.at(-1).action,'front_todo');assert.equal(requests.at(-1).expected_revision,opening);assert.equal(target.state,'done');
  target.state='active';state.revision++;await refresh();const current=state.revision;
  await click(`[data-id="${target.id}"][data-action="todo"]`);await wait(`daySnapshot().tasks.find(t=>t.id===${JSON.stringify(target.id)}).state==='todo'`);
  assert.equal(requests.at(-1).expected_revision,current);assert.equal(target.state,'todo');
  target.state='todo';state.revision++;await refresh();
  console.log('Details action regressions passed');
 }
 if(group==='all'||group==='dates'){
  for(const instant of ['2025-11-02T07:30:21Z','2025-11-02T08:30:21Z','2025-11-02T10:30:21Z']){
   target.due=instant;state.revision++;await refresh();await detail();await click('#edit-card');
   const before=requests.length;await fill('card-title','Title with '+instant);await submit('card-dialog');await saved('card-dialog');
   assert.equal(requests.length,before+1);assert.equal(requests.at(-1).payload.title,'Title with '+instant);
   assert.equal('due' in requests.at(-1).payload,false);assert.equal(target.due,instant);
   const start=instant.replace(':21Z',':45Z'),end=instant.replace(':30:21Z',':50:59Z');
   const event={uuid:randomUUID(),title:'Original event',start_at:start,end_at:end};
   state.calendar_events=[event];state.revision++;await refresh();await click('#calendar-area .link-button');
   await fill('capture-title','Only event title');await submit('capture-dialog');await saved('capture-dialog');
   assert.equal(event.start_at,start);assert.equal(event.end_at,end);
   assert.equal(requests.at(-1).payload.start,start);assert.equal(requests.at(-1).payload.end,end);
   state.notification_policy.pausedUntil=instant;state.revision++;await refresh();await click('#settings-open');
   await fill('setting-name','Name with '+instant);await submit('settings-dialog');await saved('settings-dialog');
   assert.equal(settings.at(-1).values.notification_policy.pausedUntil,instant);assert.equal(state.profile.name,'Name with '+instant);
   state.context={availableMinutes:20,availableUntil:instant,rest:false};state.revision++;await refresh();await click('#context-open');
   await click('#context-rest');await submit('context-dialog');await saved('context-dialog');
   assert.equal(requests.at(-1).payload.availableUntil,instant);assert.equal(state.context.availableUntil,instant);assert.equal(state.context.rest,true);
   await click('#settings-open');await fill('setting-zone','UTC');await submit('settings-dialog');await saved('settings-dialog');
   assert.equal(state.notification_policy.pausedUntil,instant);
   state.timezone='America/Edmonton';state.profile.timezone=state.timezone;state.revision++;await refresh();
  }
  for(const invalid of ['2025-11-02T01:45','2025-03-09T02:30']){
   await detail();await click('#edit-card');await fill('card-title','Draft retained');await fill('card-due',invalid);
   let before=requests.length;await submit('card-dialog');await failed('card-dialog');assert.equal(requests.length,before);
   assert.equal(await evaluate("document.getElementById('card-title').value"),'Draft retained');await close();
   for(const id of ['capture-start','capture-end']){
    await click('#calendar-area .link-button');await fill('capture-title','Draft event');await fill(id,invalid);
    before=requests.length;await submit('capture-dialog');await failed('capture-dialog');assert.equal(requests.length,before);
    assert.equal(await evaluate("document.getElementById('capture-title').value"),'Draft event');await close();
   }
   await click('#settings-open');await fill('setting-name','Draft profile');await fill('policy-pause',invalid);
   before=settings.length;await submit('settings-dialog');await failed('settings-dialog');assert.equal(settings.length,before);
   assert.equal(await evaluate("document.getElementById('setting-name').value"),'Draft profile');await close();
   await click('#context-open');await fill('context-until',invalid);before=requests.length;await submit('context-dialog');
   await wait("!document.getElementById('context-error').hidden");assert.equal(requests.length,before);
   assert.equal(await evaluate("document.getElementById('context-until').value"),invalid);await close();
  }
  target.due=null;target.title='Responsabilidad original';state.calendar_events=[];state.notification_policy.pausedUntil=null;
  state.context={availableMinutes:null,availableUntil:null,rest:false};state.revision++;await refresh();
  console.log('Stored date regressions passed');
 }
 if(group==='all'||group==='conversation'){
  // Typed /clear and the button use the clear action; neither becomes a model message.
  state.agent={connected:true,model:'controlled',effort:'high',catalog:[{model:'controlled',isDefault:true,efforts:['low','high'],defaultEffort:'high'}]};await refresh();
  const shown=n=>wait(`document.querySelectorAll('#messages .message').length===${n}`);
  await fill('conversation-input','First message');await click('#send-message');await shown(2);
  await fill('conversation-input','/clear');await click('#send-message');await shown(0);
  assert.equal(chats.length,1);assert.equal(clears.length,1);assert.equal(await evaluate("document.getElementById('conversation-input').value"),'');
  await fill('conversation-input','Another message');await click('#send-message');await shown(2);await click('#chat-clear');await shown(0);
  assert.equal(chats.length,2);assert.equal(clears.length,2);assert.notEqual(clears[0].key,clears[1].key);
  // Enter sends, Shift+Enter adds a line, and Enter that commits an input-method composition never sends.
  const typed="document.getElementById('conversation-input').value";
  const press=async(modifiers=0,code=13)=>{await evaluate("document.getElementById('conversation-input').focus()");for(const type of ['keyDown','keyUp'])await rpc('Input.dispatchKeyEvent',{type,key:'Enter',code:'Enter',windowsVirtualKeyCode:code,modifiers,...(type==='keyDown'&&code===13?{text:'\r'}:{})},sessionId)};
  assert.equal(await evaluate("document.getElementById('send-message').getClientRects().length"),0);
  if(await evaluate("document.getElementById('chat-toggle').getAttribute('aria-expanded')==='false'"))await click('#chat-toggle');
  await fill('conversation-input','Line one');await press(8);assert.equal(await evaluate(typed),'Line one\n');assert.equal(chats.length,2);
  await evaluate("document.getElementById('conversation-input').value+='Line two'");await press(0,229);assert.equal(chats.length,2);
  await evaluate("document.getElementById('conversation-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}))");assert.equal(chats.length,2);
  await press();await shown(2);assert.equal(chats.at(-1).text,'Line one\nLine two');assert.equal(await evaluate(typed),'');
  await press();assert.equal(chats.length,3);assert.equal(await evaluate(typed),'');
  // A reply in progress keeps the draft instead of sending it twice.
  state.messages.push({key:'pending-reply',role:'user',text:'Waiting',status:'pending',at:state.now,attachments:[]});await refresh();
  await fill('conversation-input','Not yet');await press();assert.equal(chats.length,3);assert.equal(await evaluate(typed),'Not yet');state.messages.pop();await refresh();
  // Touch keyboards keep Enter for new lines; the send button stays reachable there.
  await rpc('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1},sessionId);await rpc('Emulation.setEmulatedMedia',{features:[{name:'pointer',value:'coarse'},{name:'hover',value:'none'}]},sessionId);
  assert.equal(await evaluate("matchMedia('(hover:none) and (pointer:coarse)').matches"),true);
  assert.equal(await evaluate("document.getElementById('send-message').getClientRects().length>0&&document.getElementById('composer-hint').getClientRects().length===0"),true);
  await press();assert.equal(chats.length,3);assert.equal(await evaluate(typed),'Not yet\n');state.messages=[];await refresh();await click('#send-message');await wait('dayUI.flow.snapshot().messages.length===2');assert.equal(chats.at(-1).text,'Not yet');
  await rpc('Emulation.setEmulatedMedia',{features:[]},sessionId);await rpc('Emulation.setTouchEmulationEnabled',{enabled:false},sessionId);
  // The status line shows only what the harness reported for the current model; never a zero for missing data.
  const status=()=>evaluate("document.getElementById('context-status').textContent.replace(/\\s/g,' ')");
  const reply=(usage,extra={})=>({key:'r:assistant',role:'assistant',text:'Reply',status:'completed',at:state.now,attachments:[],data:JSON.stringify({provider:'codex',model:'controlled',usage,...extra})});
  state.messages=[];await refresh();assert.equal(await status(),'Context: no data');
  state.messages=[reply({contextTokens:9706,contextWindow:475000})];await refresh();assert.equal(await status(),'Context 9.7K / 475K · 97% free');
  assert.equal(await evaluate("document.getElementById('context-status').classList.contains('tight')"),false);
  state.messages.push({key:'s',role:'user',text:'Next',status:'incomplete',at:state.now,attachments:[]},{key:'s:assistant',role:'assistant',text:'Incomplete',status:'incomplete',at:state.now,attachments:[],data:JSON.stringify({code:'agent_error'})});await refresh();
  assert.equal(await status(),'Context 9.7K / 475K · 97% free');
  state.messages=[reply({contextTokens:190000,contextWindow:200000})];await refresh();assert.equal(await status(),'Context 190K / 200K · 5% free');
  assert.equal(await evaluate("document.getElementById('context-status').classList.contains('tight')"),true);
  state.messages=[reply({contextTokens:1200,contextWindow:null})];await refresh();assert.equal(await status(),'Context 1.2K used · window not reported');
  state.messages=[reply(null)];await refresh();assert.equal(await status(),'Context: no data');
  state.messages=[reply({contextTokens:1200,contextWindow:200000},{model:'previous-model'})];await refresh();assert.equal(await status(),'Context: no data');
  state.messages=[reply({contextTokens:9706,contextWindow:475000})];await refresh();await fill('locale-choice','es');
  assert.equal(await status(),'Contexto 9.7 k / 475 k · 97% libre');assert.equal(await evaluate("document.getElementById('composer-hint').textContent"),'Enter para enviar · Mayús+Enter para nueva línea');
  await fill('locale-choice','en');state.messages=[];await refresh();await click('#chat-close');
  // Reasoning choices follow the model catalog for Codex and the fixed Claude Code set.
  const efforts="[...document.getElementById('setting-effort').options].map(o=>o.value)";
  await click('#settings-open');assert.deepEqual(await evaluate(efforts),['low','high']);assert.equal(await evaluate("document.getElementById('setting-effort').value"),'high');
  await fill('setting-harness','claude');assert.deepEqual(await evaluate(efforts),['low','medium','high','xhigh','max']);
  await fill('setting-harness','codex');await fill('setting-effort','low');const sent=settings.length;await submit('settings-dialog');await saved('settings-dialog');
  assert.equal(settings[sent].values.effort,'low');assert.equal('harness' in settings[sent].values,false);
  delete state.profile.effort;state.agent={connected:true,model:'controlled'};await refresh();
  // A model without medium falls back to its catalog default; without a catalog only the model default is offered.
  state.agent={connected:true,model:'controlled',effort:'medium',catalog:[{model:'controlled',isDefault:true,efforts:['low','medium','high'],defaultEffort:'medium'},{model:'deep',isDefault:false,efforts:['low','high'],defaultEffort:'high'}]};await refresh();
  await click('#settings-open');await fill('setting-model','deep');assert.equal(await evaluate("document.getElementById('setting-effort').value"),'high');await close();
  state.profile.harness='claude';state.agent={connected:true,model:'claude-controlled',effort:'medium'};await fill('locale-choice','es');await refresh();
  await click('#settings-open');await fill('setting-harness','codex');
  assert.deepEqual(await evaluate("[...document.getElementById('setting-effort').options].map(o=>[o.value,o.textContent])"),[['','Predeterminado del modelo']]);
  const switched=settings.length;await submit('settings-dialog');await saved('settings-dialog');
  assert.equal(settings[switched].values.harness,'codex');assert.equal(settings[switched].values.effort,null);
  // A level refused at startup is shown in the interface language.
  state.agent={connected:false,model:null,error:'The selected model does not offer this reasoning level; no other level was selected.',errorCode:'agent_effort_unavailable'};await refresh();
  assert.equal(await evaluate("document.getElementById('bridge-state').textContent"),'El modelo elegido no ofrece este nivel de razonamiento. Elige otro nivel.');
  await fill('locale-choice','en');state.profile.model='controlled';delete state.profile.effort;state.agent={connected:true,model:'controlled'};await refresh();
  // The model is a real selector per harness: every Codex model, and only Claude models after switching.
  catalogs={codex:[{model:'controlled',isDefault:true,efforts:['low','medium'],defaultEffort:'medium'},{model:'gpt-terra',isDefault:false,efforts:['low','high'],defaultEffort:'high'},{model:'gpt-luna',isDefault:false,efforts:['medium'],defaultEffort:'medium'}],
   claude:[{model:'claude-opus-test',isDefault:false,efforts:['low','medium','high','xhigh','max'],defaultEffort:null}],errors:{}};
  const models="[...document.getElementById('setting-model').options].map(o=>o.value)";
  await click('#settings-open');await wait(`${models}.length===4`);
  assert.deepEqual(await evaluate(models),['','controlled','gpt-terra','gpt-luna']);assert.equal(await evaluate("document.getElementById('setting-model').value"),'controlled');
  await fill('setting-model','gpt-terra');assert.deepEqual(await evaluate(efforts),['low','high']);
  await fill('setting-harness','claude');assert.deepEqual(await evaluate(models),['','claude-opus-test']);assert.equal(await evaluate("document.getElementById('setting-model').value"),'');
  await fill('setting-model','claude-opus-test');const chosen=settings.length;await submit('settings-dialog');await saved('settings-dialog');
  assert.equal(settings[chosen].values.harness,'claude');assert.equal(settings[chosen].values.model,'claude-opus-test');
  state.profile.harness='codex';state.profile.model='controlled';catalogs=null;await refresh();
 }
 if(group==='all'){
 for(const [action,control,key] of [['dependency_update','edit-dependencies','dependsOn'],['related_update','edit-related','relatedTo']]){
  await detail();target[key]=[a.id];state.revision++;await refresh();await click('#'+control);
  assert.deepEqual(await evaluate("[...document.querySelectorAll('#links-dialog input:checked')].map(e=>e.value)"),[a.id]);
  await click(`#links-dialog input[value="${b.id}"]`);await submit('links-dialog');await saved('links-dialog');assert.deepEqual(target[key],[a.id,b.id]);
  assert.equal(requests.at(-1).action,action);
  await close();await detail();await click('#'+control);const opening=state.revision;
  target[key]=[a.id,b.id,c.id];state.revision++;await refresh();await click(`#links-dialog input[value="${b.id}"]`);await submit('links-dialog');await failed('links-dialog');
  assert.equal(requests.at(-1).expected_revision,opening);assert.deepEqual(target[key],[a.id,b.id,c.id]);await close();
 }
 await detail();target.title='Latest human title';target.context='Latest context';state.revision++;await refresh();await click('#edit-card');
 assert.equal(await evaluate("document.getElementById('card-title').value"),target.title);
 assert.equal(await evaluate("document.getElementById('card-context').value"),target.context);
 await fill('card-title','Requested replacement');const opening=state.revision;target.next='External next step';state.revision++;await refresh();await submit('card-dialog');await failed('card-dialog');
 assert.equal(target.title,'Latest human title');assert.equal(target.next,'External next step');assert.equal(requests.at(-1).expected_revision,opening);
 await close();await detail();await click('#edit-card');await fill('card-title','Requested replacement');await submit('card-dialog');await saved('card-dialog');assert.equal(target.title,'Requested replacement');

 state.proposals=Array.from({length:8},(_,i)=>({id:randomUUID(),intent:{action:'task_add',payload:{title:'Item '+(i+1)}},generation:0,facts:[],phase:'evaluating'}));
 state.messages=[{id:1,key:'capture',role:'user',text:'Capture nine items',status:'completed'}, {id:2,key:'capture:assistant',role:'assistant',text:'Checking the items.',status:'completed',data:JSON.stringify({proposal_ids:state.proposals.map(p=>p.id),unprocessed:['Ninth item remains'],uncertainties:['Which date for the ninth item?']})}];await refresh();
 let receipt=await evaluate("document.querySelector('.receipt-list').textContent");assert.match(receipt,/Ninth item remains/);assert.match(receipt,/Which date/);assert.match(receipt,/Reviewing/);
 const phases=['applied','unavailable','stale','invalid','review','canceled','superseded','unchanged'];state.proposals.forEach((p,i)=>p.phase=phases[i]);await refresh();
 receipt=await evaluate("document.querySelector('.receipt-list').textContent");for(const label of ['Saved','Item 2 · not created yet: the automatic review could not be completed. Decide in Before applying.','Item 3 · not created yet: the context changed.','Item 4 · not created yet: adjust it','Item 5 · not created yet: a consequence needs your decision','Canceled','Replaced','Unchanged','2 of 8 saved · 4 waiting for you in Before applying.'])assert.ok(receipt.includes(label),receipt);
 assert.equal(await evaluate("document.querySelectorAll('.receipt-list .link-button').length"),4);
 for(const [phase,label] of [['ready','Ready to save'],['admitted','Admitted; check recovery']]){state.proposals[0].phase=phase;await refresh();assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes(label))}
 state.messages.push({id:3,key:'question',role:'assistant',text:'Please clarify.',status:'completed',data:JSON.stringify({proposal_ids:[],unprocessed:['Unscheduled request'],uncertainties:['Who is the person?']})});await refresh();
 assert.match(await evaluate("document.getElementById('messages').textContent"),/Unscheduled request.*Who is the person/s);
 // A failed review never shows internal error text; it says nothing was created yet and what each choice does.
 const raw='The concern is not grounded in the supplied evidence.';
 const failedReview=state.proposals[1];Object.assign(failedReview,{error:raw,error_code:'agent_invalid_evidence',review:null});
 const panel=()=>evaluate(`(()=>{const d=[...document.querySelectorAll('#reviews details')].find(d=>d.dataset.proposal===${JSON.stringify(failedReview.id)});return d&&{text:d.textContent,open:d.open,buttons:[...d.querySelectorAll('.decision-actions button')].map(b=>[b.textContent,b.dataset.reviewAction,b.classList.contains('primary')])}})()`);
 await refresh();
 await evaluate(`document.querySelector('#reviews details[open]')?.removeAttribute('open')`);
 await evaluate(`[...document.querySelectorAll('.receipt-list .link-button')][0].click()`);
 let shownPanel=await panel();assert.equal(shownPanel.open,true);
 assert.equal(await evaluate(`document.activeElement===[...document.querySelectorAll('#reviews details')].find(d=>d.dataset.proposal===${JSON.stringify(failedReview.id)}).querySelector('summary')`),true);
 assert.ok(!shownPanel.text.includes(raw),shownPanel.text);
 for(const copy of ['Automatic review could not be completed','This task has not been created yet.',"The assistant's review cited information it had not been given",'not a problem found in what you asked for','This is a low-risk change','Create without review: saves the task as you asked','Retry review: asks the assistant to review it again','Cancel change: discards it'])assert.ok(shownPanel.text.includes(copy),copy+' in '+shownPanel.text);
 assert.deepEqual(shownPanel.buttons,[['Cancel change','cancel',false],['Retry review','retry',false],['Create without review','without',true]]);
 await fill('locale-choice','es');await refresh();shownPanel=await panel();
 assert.ok(!shownPanel.text.includes(raw),shownPanel.text);
 for(const copy of ['No se pudo completar la revisión automática','Esta tarea aún no se ha creado.','citó información que no se le había dado','no un problema encontrado en lo que pediste','Crear sin revisión: guarda la tarea'])assert.ok(shownPanel.text.includes(copy),copy+' in '+shownPanel.text);
 assert.deepEqual(shownPanel.buttons,[['Cancelar cambio','cancel',false],['Reintentar revisión','retry',false],['Crear sin revisión','without',true]]);
 assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes('Item 2 · aún no se creó: no se pudo completar la revisión automática. Decide en «Antes de aplicar».'));
 await fill('locale-choice','en');
 // A dated change keeps retrying the review as the primary step.
 failedReview.intent.payload.due='2025-10-30T12:00:00Z';await refresh();shownPanel=await panel();
 assert.deepEqual(shownPanel.buttons,[['Cancel change','cancel',false],['Create without review','without',false],['Retry review','retry',true]]);assert.ok(!shownPanel.text.includes('low-risk'));
 delete failedReview.intent.payload.due;
 // An insufficient verdict is the assistant's own question, not a failure.
 Object.assign(failedReview,{error:null,error_code:'review_insufficient',review:{output:{verdict:'insufficient',message:'Which project is this feedback for?',alternatives:[]}}});await refresh();shownPanel=await panel();
 for(const copy of ['The assistant needs a clarification','This task has not been created yet.','Which project is this feedback for?'])assert.ok(shownPanel.text.includes(copy),copy);
 assert.ok(!shownPanel.text.includes('failure of the review'));assert.equal(shownPanel.buttons.some(b=>b[2]),false);
 assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes('Item 2 · not created yet: the assistant needs a clarification.'));
 Object.assign(failedReview,{error:'Changed by admitted proposal:'+randomUUID(),error_code:'context_changed',phase:'stale',review:null});await refresh();shownPanel=await panel();
 assert.ok(!shownPanel.text.includes('Changed by admitted proposal'));assert.ok(shownPanel.text.includes('Something relevant changed after the review started'));
 Object.assign(failedReview,{phase:'unavailable',error:raw,error_code:'agent_invalid_evidence'});
 // Chat failures are localized by code too.
 state.messages.push({id:4,key:'broken',role:'user',text:'Add three tasks',status:'incomplete'},{id:5,key:'broken:assistant',role:'assistant',text:'The response is incomplete. The intent is not grounded in the current human message.',status:'incomplete',data:JSON.stringify({code:'agent_invalid_intent',proposal_ids:[]})});await refresh();
 let chatText=await evaluate("document.getElementById('messages').textContent");
 assert.ok(!chatText.includes('not grounded'),chatText);assert.ok(chatText.includes('The reply could not be completed. The assistant proposed something that does not match your message. Nothing from it was saved.'),chatText);
 await fill('locale-choice','es');await refresh();chatText=await evaluate("document.getElementById('messages').textContent");
 assert.ok(chatText.includes('No se pudo completar la respuesta. El asistente propuso algo que no coincide con tu mensaje. No se guardó nada de ella.'),chatText);
 await fill('locale-choice','en');state.messages.splice(-2);await refresh();

 const captureProposals=state.proposals;
 state.proposals=[{id:randomUUID(),intent:{action:'task_update',payload:{uuid:target.id,title:'Pending title'}},phase:'unavailable',generation:0,facts:[]}];
 target.dependsOn=[a.id];target.blocked=true;target.blockedBy=[a.id];
 target.source='chat';target.person='Ana';target.reference='Referencia literal';
 const cardSelector=`[data-task-id="${target.id}"]`;
 const cardState=()=>evaluate(`(()=>{const card=document.querySelector(${JSON.stringify(cardSelector)});return {reason:card.querySelector('.blocked-label')?.textContent,actionDisabled:card.querySelector('.state-action').disabled,snoozeDisabled:card.querySelector('[data-action=snooze]').disabled,pendingDisabled:card.querySelector('[data-action=pending]').disabled,detailDisabled:card.querySelector('[data-action=detail]').disabled}})()`);
 for(const mode of ['todo','active','snoozed']){
  target.state=mode;target.until=mode==='snoozed'?'2025-10-29T12:00:00Z':null;await refresh();
  const expected={reason:'Blocked · '+a.title,actionDisabled:mode==='todo',snoozeDisabled:false,pendingDisabled:false,detailDisabled:false};
  assert.deepEqual(await cardState(),expected);
  await evaluate(`globalThis.previousCard=document.querySelector(${JSON.stringify(cardSelector)})`);
  await click(cardSelector+' [data-action=pending]');
  assert.equal(await evaluate(`previousCard===document.querySelector(${JSON.stringify(cardSelector)})`),false);
  assert.deepEqual(await cardState(),expected);
  await fill('locale-choice','es');await refresh();assert.deepEqual(await cardState(),{...expected,reason:'Bloqueada · '+a.title});
  await fill('locale-choice','en');await refresh();assert.deepEqual(await cardState(),expected);
 }
 target.state='todo';target.until=null;state.readOnly=true;await refresh();
 const readOnlyCard={reason:'Blocked · '+a.title,actionDisabled:true,snoozeDisabled:true,pendingDisabled:true,detailDisabled:false};
 assert.deepEqual(await cardState(),readOnlyCard);
 assert.equal(await evaluate("document.getElementById('capture-open').disabled && document.getElementById('send-message').disabled"),true);
 await fill('locale-choice','es');await refresh();assert.deepEqual(await cardState(),{...readOnlyCard,reason:'Bloqueada · '+a.title});
 await fill('locale-choice','en');await refresh();assert.deepEqual(await cardState(),readOnlyCard);
 await evaluate(`globalThis.previousCard=document.querySelector(${JSON.stringify(cardSelector)});globalThis.boardMutations=0;globalThis.boardObserver=new MutationObserver(records=>boardMutations+=records.length);boardObserver.observe(document.getElementById('board'),{subtree:true,childList:true,attributes:true,characterData:true})`);
 await refresh();await refresh();
 assert.equal(await evaluate('boardMutations'),0);assert.equal(await evaluate(`previousCard===document.querySelector(${JSON.stringify(cardSelector)})`),true);
 await evaluate('boardObserver.disconnect()');
 state.readOnly=false;target.blocked=false;target.blockedBy=[];state.proposals=captureProposals;await refresh();
 assert.equal(await evaluate(`document.querySelector(${JSON.stringify(cardSelector+' .blocked-label')})===null && !document.querySelector(${JSON.stringify(cardSelector+' .state-action')}).disabled`),true);

 state.calendar_events=Array.from({length:13},(_,i)=>({uuid:randomUUID(),title:'Commitment '+(i+1),start_at:'2025-10-28T11:00:00Z',end_at:i===0?'2025-10-28T12:00:10Z':'2025-10-29T13:00:00Z'}));await refresh();
 assert.equal(await evaluate("document.querySelectorAll('#calendar-area .daily-row').length"),13);
 const oldTime=await evaluate("document.querySelector('#calendar-area .daily-row p').textContent");state.timezone='UTC';state.profile.timezone='UTC';await refresh();assert.notEqual(await evaluate("document.querySelector('#calendar-area .daily-row p').textContent"),oldTime);
 state.now='2025-10-28T12:00:11Z';await refresh();assert.equal(await evaluate("document.querySelectorAll('#calendar-area .daily-row').length"),12);
 await click('#calendar-area .daily-row:last-child .link-button');assert.equal(await evaluate("document.getElementById('capture-title').value"),'Commitment 13');await fill('capture-title','Edited thirteenth');await submit('capture-dialog');await saved('capture-dialog');assert.equal(state.calendar_events.at(-1).title,'Edited thirteenth');
 await wait("document.querySelector('#calendar-area .daily-row:last-child .link-button').textContent==='Edited thirteenth'");
 await click('#calendar-area .daily-row:last-child .btn');await wait("!document.getElementById('calendar-area').textContent.includes('Edited thirteenth')");assert.equal(state.calendar_events.length,12);
 state.timezone='America/Edmonton';state.profile.timezone=state.timezone;await refresh();
 assert.equal(await evaluate("new Date().getTimezoneOffset()"),0);
 for(const date of ['2025-11-02T07:30:00Z','2025-11-02T08:30:00Z'])assert.equal(await evaluate(`dayUI.ambiguous(new Date(${JSON.stringify(date)}))`),true);
 assert.equal(await evaluate("dayUI.ambiguous(dayUI.parseLocal('2025-11-02T03:30'))"),false);
 assert.notEqual(await evaluate("dayUI.localValue(dayUI.parseLocal('2025-03-09T02:30'))"),'2025-03-09T02:30');
 for(const id of ['capture-due','capture-observed','capture-start','capture-end']){
  await click('#capture-open');await fill('capture-title','Date validation');
  if(id==='capture-start'||id==='capture-end'){await fill('capture-kind','event');await fill('capture-start','2025-11-02T03:30');await fill('capture-end','2025-11-02T04:30')}
  await fill(id,id==='capture-observed'?'2024-11-03T01:30':'2025-11-02T01:30');const before=requests.length;await submit('capture-dialog');await failed('capture-dialog');assert.equal(requests.length,before);
  await fill(id,id==='capture-observed'?'2024-11-03T03:30':id==='capture-end'?'2025-11-02T04:30':'2025-11-02T03:30');await submit('capture-dialog');await saved('capture-dialog');assert.equal(requests.length,before+1);
 }
 await detail();await click('#edit-card');await fill('card-due','2025-11-02T01:30');let before=requests.length;await submit('card-dialog');await failed('card-dialog');assert.equal(requests.length,before);await fill('card-due','2025-11-02T03:30');await submit('card-dialog');await saved('card-dialog');assert.equal(target.due,'2025-11-02T10:30:00.000Z');
 await click('#settings-open');await fill('policy-pause','2025-11-02T01:30');await submit('settings-dialog');await failed('settings-dialog');await fill('policy-pause','2025-11-02T03:30');await submit('settings-dialog');await saved('settings-dialog');
 await click('#context-open');await fill('context-minutes','10');await fill('context-until','2025-11-02T01:30');before=requests.length;await submit('context-dialog');await wait("!document.getElementById('context-error').hidden");assert.equal(requests.length,before);await fill('context-until','2025-11-02T03:30');await submit('context-dialog');await saved('context-dialog');
 await click(`[data-id="${target.id}"][data-action="snooze"]`);await click('#date-mode');await fill('snooze-date','2025-11-02T01:30');assert.equal(await evaluate("document.getElementById('snooze-apply').disabled"),true);await fill('snooze-date','2025-11-02T03:30');assert.equal(await evaluate("document.getElementById('snooze-apply').disabled"),false);await click('#snooze-apply');await saved('snooze-dialog');
 state.timezone='Australia/Lord_Howe';await refresh();assert.equal(await evaluate("dayUI.ambiguous(dayUI.parseLocal('2025-04-06T01:45'))"),true);
 for(const locale of ['en','es']){
  await fill('locale-choice',locale);
  for(const status of ['done','retired']){
   target.state=status;await refresh();await detail();const label=await evaluate("document.querySelector('#detail-data dd').textContent");assert.equal(label,locale==='en'?(status==='done'?'Completed':'Retired'):(status==='done'?'Terminada':'Retirada'));
   assert.equal(await evaluate("document.getElementById('detail-title').textContent"),'Requested replacement');
   assert.deepEqual(await evaluate("[...document.querySelectorAll('#detail-extras .detail-grid dt')].slice(0,3).map(e=>e.textContent)"),locale==='en'?['Source','Person','Reference']:['Origen','Persona','Referencia']);
   assert.deepEqual(await evaluate("[...document.querySelectorAll('#detail-extras .detail-grid dd')].slice(0,3).map(e=>e.textContent)"),['chat','Ana','Referencia literal']);await close();
  }
 }
 console.log('Daily UI regressions passed: snapshot revisions, capture outcomes, dates, calendar and localization');
 }
}finally{
 if(socket)socket.close();chrome.kill('SIGTERM');await new Promise(resolve=>chrome.exitCode!==null?resolve():chrome.once('exit',resolve));
 await new Promise(resolve=>server.close(resolve));await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
