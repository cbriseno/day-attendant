import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const group=process.argv[3]||'all';
assert.ok(['all','details','dates','conversation','dependencies','ordering','refresh','instant'].includes(group),'Unknown browser regression group');
const task=(title)=>({id:randomUUID(),title,state:'todo',priority:'medium',due:null,until:null,context:'',next:'',awaiting:false,remainingMinutes:null,dependsOn:[],relatedTo:[],blocked:false,blockedBy:[],attachmentIds:[],source:'general'});
const target=task('Responsabilidad original'),a=task('A'),b=task('B'),c=task('C');
// Historical DST fixtures remain stable as jurisdictions change future rules.
const state={profile:{id:randomUUID(),name:'Controlled profile',timezone:'America/Edmonton',locale:'en',harness:'codex',model:'controlled'},timezone:'America/Edmonton',now:'2025-10-28T12:00:00Z',revision:1,epoch:0,synthetic:true,readOnly:false,tasks:[target,a,b,c],events:[],proposals:[],messages:[],calendar_events:[],context:{availableMinutes:null,availableUntil:null,rest:false},agent:{connected:true,model:'controlled'},diagnostics:{operations:[],reminders:[]},operations_status:{},notification_policy:{enabled:false,weekdays:[0,1,2,3,4],start:'09:00',end:'18:00',quietStart:'22:00',quietEnd:'08:00',intervalMinutes:10,unchangedRepeatMinutes:1440,dailyBudget:12,pausedUntil:null}};
const requests=[],settings=[],chats=[],clears=[],suggestions=[],undos=[],suggestionCalls=[],undoable=new Map();let catalogs=null,facts=0;
// State reads can be slowed to reproduce a service that answers after the poll interval.
const stateReads=[];let stateDelay=0,slowRevision=null,proposalReplied=0;
const server=createServer(async(req,res)=>{
 try{
  if(req.url.startsWith('/api/')){
   let body='';for await(const chunk of req)body+=chunk;body=body?JSON.parse(body):{};
   let data=state;
   if(req.url.startsWith('/api/state')){
    stateReads.push({url:req.url,at:Date.now()});const read=structuredClone(state),{now,...content}=read;
    const tag=createHash('sha256').update(JSON.stringify(content)).digest('hex').slice(0,32);
    if(stateDelay||read.revision===slowRevision)await new Promise(r=>setTimeout(r,read.revision===slowRevision?800:stateDelay));
    data=new URL(req.url,'http://local').searchParams.get('since')===tag?{unchanged:true,tag,now}:{...read,tag};
   }else if(req.url==='/api/proposals'){
    requests.push(body);
    if(body.expected_revision!==state.revision){res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:{code:'stale_revision',message:'State changed; reopen this editor.'}}));return}
    const p=body.payload,t=state.tasks.find(t=>t.id===p.uuid),beforeTask=t&&{...t};
    if(body.action==='dependency_update')t.dependsOn=p.dependsOn;
    if(body.action==='related_update')t.relatedTo=p.relatedTo;
    if(body.action==='task_update')for(const key of ['title','priority','due','context','next','awaiting','group'])if(key in p)t[key]=p[key];
    if(body.action==='order_update')state.taskOrder=p.order;
    const nextState={front_activate:'active',front_todo:'todo',task_done:'done',front_retire:'retired',task_reopen:'todo'}[body.action];
    if(nextState)t.state=nextState;
    if(body.action==='context_update')Object.assign(state.context,p);
    if(body.action==='event_update')Object.assign(state.calendar_events.find(e=>e.uuid===p.uuid),{title:p.title,start_at:p.start,end_at:p.end});
    if(body.action==='event_cancel')state.calendar_events=state.calendar_events.filter(e=>e.uuid!==p.uuid);
    // The instant group records each change as an undoable fact, as the service does.
    const proposalId=randomUUID();
    if(state.instant&&t){const fact=++facts;undoable.set(fact,{id:t.id,before:beforeTask});state.lastFact={id:fact,proposalId,action:body.action,taskId:t.id,undoable:true,undoOf:null,reviewStatus:'deferred'}}
    state.revision++;
    data={id:proposalId,key:body.key,intent:{action:body.action,payload:p},phase:'applied',generation:0,facts:[],result:{revision:state.revision}};proposalReplied=Date.now();
   }else if(req.url==='/api/undo'){
    undos.push(body);const last=undoable.get(Number(body.fact_id));
    const proposalId=randomUUID();
    if(last){Object.assign(state.tasks.find(t=>t.id===last.id),last.before);state.revision++;state.lastFact={id:++facts,proposalId,action:'task_restore',taskId:last.id,undoable:true,undoOf:Number(body.fact_id),reviewStatus:'deferred'}}
    data={id:proposalId,phase:'applied',result:{revision:state.revision}};
   }else if(req.url.startsWith('/api/suggestions/')){
    const [,,,id,act]=req.url.split('/'),item=state.suggestions.find(s=>s.id===id);suggestionCalls.push([id,act,body]);
    const proposalId=randomUUID();
    if(act==='accept'){const t=state.tasks.find(t=>t.id===item.action.payload.uuid),fact=++facts;undoable.set(fact,{id:t.id,before:{...t}});t.state='active';state.revision++;state.lastFact={id:fact,proposalId,action:'front_activate',taskId:t.id,undoable:true,undoOf:null,reviewStatus:'deferred'}}
    Object.assign(item,{status:act==='accept'?'applied':'dismissed',current:false});data=act==='accept'?{id:proposalId,phase:'applied',result:{revision:state.revision}}:{id,status:item.status};
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
   }else if(req.url==='/api/suggestion'){
    suggestions.push(body);const order=[...state.tasks].filter(t=>t.state==='todo').reverse().map(t=>({id:t.id,reason:'Controlled reason for '+t.title}));
    state.orderSuggestion={request:{key:body.key,status:'completed'},result:{key:body.key,at:state.now,message:'Controlled suggestion message.',order},stale:false};data=state.orderSuggestion;
   }else if(req.url==='/api/backups')data={backups:[]};
   res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:true,data}));return;
  }
  const filename={'/':'index.html','/interface.js':'interface.js','/daily.js':'daily.js','/locales.js':'locales.js','/ordering.js':'ordering.js'}[req.url];
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
  assert.equal(chats.length,1);assert.equal(clears.length,1);await wait("document.getElementById('conversation-input').value===''");
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
 if(group==='all'||group==='dependencies'){
  // Dependency chips only change the view: direct links are highlighted, and lines appear where there is room.
  const before=requests.length,chip=(t,kind)=>`[data-task-id="${t.id}"] .${kind}`,size=(width,height)=>rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<600},sessionId);
  const view=`(()=>{const ids=${JSON.stringify([a,target,b,c].map(t=>t.id))};return JSON.stringify({focus:document.getElementById('board').classList.contains('dependency-focus'),roles:ids.map(id=>document.querySelector('[data-task-id="'+id+'"]').dataset.dependencyRole||null),tags:[...document.querySelectorAll('#board .dependency-role')].map(e=>e.textContent),pressed:[...new Set([...document.querySelectorAll('#board [aria-pressed=true]')].map(e=>e.closest('[data-task-id]').dataset.taskId))],lines:document.querySelectorAll('.dependency-lines > path').length})})()`;
  const shows=expected=>wait(`${view}===${JSON.stringify(JSON.stringify(expected))}`).catch(async error=>{throw Error(error.message+' '+await evaluate(view))});
  const none={focus:false,roles:[null,null,null,null],tags:[],pressed:[],lines:0};
  await size(1440,1000);a.state='active';for(const t of [target,b]){t.state='todo';t.dependsOn=[a.id];t.blockedBy=[a.id];t.blocked=true}state.revision++;await refresh();
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(chip(a,'blocking-label'))}).textContent`),'Blocks · '+target.title+', B');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(chip(target,'blocked-label'))}).textContent`),'Blocked · A');
  assert.equal(await evaluate(`document.querySelector('[data-task-id="${c.id}"] .dependency-chip')`),null);await shows(none);
  const fromA={focus:true,roles:['source','dependent','dependent',null],tags:['Depends on it','Depends on it'],pressed:[a.id],lines:2};
  await click(chip(a,'blocking-label'));await shows(fromA);
  state.revision++;await refresh();await shows(fromA);
  await fill('locale-choice','es');await shows({...fromA,tags:['Depende de esta','Depende de esta']});
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(chip(a,'blocking-label'))}).textContent`),'Bloquea · '+target.title+', B');
  await fill('locale-choice','en');await shows(fromA);
  await click('#board-view');await shows(fromA);
  // Lines redraw after the board relayout; list-mode gutter lines also count two until then.
   await wait("(()=>{const paths=[...document.querySelectorAll('.dependency-lines > path')];return paths.length===2&&paths.every(p=>p.getAttribute('d').includes('C'))})()").catch(()=>assert.fail('Board columns use curved cross-column links'));
  for(const width of [390,320]){await size(width,844);await shows({...fromA,lines:0});assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true,'Dependency chips fit at '+width+'px')}
  await size(1440,1000);await shows(fromA);
  await click('#list-view');await shows(fromA);
  await click(chip(target,'blocked-label'));await shows({focus:true,roles:['prerequisite','source',null,null],tags:['Prerequisite'],pressed:[target.id],lines:1});
  await evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");await shows(none);
  await click(chip(a,'blocking-label'));await shows(fromA);await click(chip(a,'blocking-label'));await shows(none);
  await click(chip(a,'blocking-label'));await shows(fromA);await click('#board');await shows(none);
  state.readOnly=true;await refresh();assert.equal(await evaluate(`document.querySelector(${JSON.stringify(chip(a,'blocking-label'))}).disabled`),false);
  await click(chip(a,'blocking-label'));await shows(fromA);
  for(const t of [target,b]){t.dependsOn=[];t.blockedBy=[];t.blocked=false}state.readOnly=false;state.revision++;await refresh();
  await shows(none);assert.equal(await evaluate("document.querySelector('#board .dependency-chip')"),null);
  assert.equal(requests.length,before,'Showing dependencies never sends a request');
  a.state='todo';state.revision++;await refresh();await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);
  console.log('Dependency indicator regressions passed');
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
 let receipt=await evaluate("document.querySelector('.receipt-list').textContent");assert.match(receipt,/Ninth item remains/);assert.match(receipt,/Which date/);assert.match(receipt,/Saving/);
 const phases=['applied','unavailable','stale','invalid','review','canceled','superseded','unchanged'];state.proposals.forEach((p,i)=>p.phase=phases[i]);await refresh();
 receipt=await evaluate("document.querySelector('.receipt-list').textContent");for(const label of ['Saved','Item 2 · not created yet: an earlier version left it waiting. Decide in Needs your decision.','Item 3 · not created yet: the responsibility changed. Decide again in Needs your decision.','Item 4 · not created: it could not be applied as asked. See Needs your decision.','Item 5 · not created yet: it waits for your confirmation in Needs your decision.','Canceled','Replaced','Unchanged','2 of 8 saved · 4 waiting for your decision.','Undo this change'])assert.ok(receipt.includes(label),receipt);
 assert.equal(await evaluate("document.querySelectorAll('.receipt-list .link-button').length"),5);
 for(const [phase,label] of [['ready','Ready to save'],['admitted','Admitted; check recovery']]){state.proposals[0].phase=phase;await refresh();assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes(label))}
 state.proposals[0].phase='applied';
 state.messages.push({id:3,key:'question',role:'assistant',text:'Please clarify.',status:'completed',data:JSON.stringify({proposal_ids:[],unprocessed:['Unscheduled request'],uncertainties:['Who is the person?']})});await refresh();
 assert.match(await evaluate("document.getElementById('messages').textContent"),/Unscheduled request.*Who is the person/s);
 // A change an older version left waiting never shows internal error text; it says nothing was created yet and offers to apply it now.
 const raw='The concern is not grounded in the supplied evidence.';
 const failedReview=state.proposals[1];Object.assign(failedReview,{error:raw,error_code:'agent_invalid_evidence',review:null});
 const panel=()=>evaluate(`(()=>{const d=[...document.querySelectorAll('#reviews details')].find(d=>d.dataset.proposal===${JSON.stringify(failedReview.id)});return d&&{text:d.textContent,open:d.open,buttons:[...d.querySelectorAll('.decision-actions button')].map(b=>[b.textContent,b.dataset.reviewAction,b.classList.contains('primary')])}})()`);
 await refresh();
 assert.equal(await evaluate("document.getElementById('review-title').textContent"),'Needs your decision');
 await evaluate(`document.querySelector('#reviews details[open]')?.removeAttribute('open')`);
 await evaluate(`[...document.querySelectorAll('.receipt-list .link-button')].find(b=>b.textContent==='Show').click()`);
 let shownPanel=await panel();assert.equal(shownPanel.open,true);
 assert.equal(await evaluate(`document.activeElement===[...document.querySelectorAll('#reviews details')].find(d=>d.dataset.proposal===${JSON.stringify(failedReview.id)}).querySelector('summary')`),true);
 assert.ok(!shownPanel.text.includes(raw),shownPanel.text);
 for(const copy of ['Left waiting','This task has not been created yet.','An earlier version left this change waiting for a review that no longer runs.'])assert.ok(shownPanel.text.includes(copy),copy+' in '+shownPanel.text);
 assert.deepEqual(shownPanel.buttons,[['Cancel change','cancel',false],['Create now','retry',true]]);
 await fill('locale-choice','es');await refresh();shownPanel=await panel();
 assert.ok(!shownPanel.text.includes(raw),shownPanel.text);
 for(const copy of ['Quedó en espera','Esta tarea aún no se ha creado.','Una versión anterior dejó este cambio esperando'])assert.ok(shownPanel.text.includes(copy),copy+' in '+shownPanel.text);
 assert.deepEqual(shownPanel.buttons,[['Cancelar cambio','cancel',false],['Crear ahora','retry',true]]);
 assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes('Item 2 · aún no se creó: una versión anterior lo dejó en espera. Decide en «Necesita tu decisión».'));
 assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes('Deshacer este cambio'));
 await fill('locale-choice','en');
 // An insufficient verdict from an older review is the assistant's own question, not a failure.
 Object.assign(failedReview,{error:null,error_code:'review_insufficient',review:{output:{verdict:'insufficient',message:'Which project is this feedback for?',alternatives:[]}}});await refresh();shownPanel=await panel();
 assert.ok(shownPanel.text.includes('This task has not been created yet.'));assert.ok(!shownPanel.text.includes('failure of the review'));
 assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes('Item 2 · not created yet: the assistant needed a clarification.'));
 Object.assign(failedReview,{error:'Changed by admitted proposal:'+randomUUID(),error_code:'context_changed',phase:'stale',review:null});await refresh();shownPanel=await panel();
 assert.ok(!shownPanel.text.includes('Changed by admitted proposal'));assert.ok(shownPanel.text.includes('Something relevant changed since this was proposed'));
 // A retire from the conversation waits for confirmation; nothing else does.
 Object.assign(state.proposals[4],{intent:{action:'front_retire',payload:{uuid:target.id}},facts:[{code:'confirmation',message:'x',message_key:'confirm_retire_chat',message_params:{},refs:[]}]});await refresh();
 const retire=await evaluate(`(()=>{const d=[...document.querySelectorAll('#reviews details')].find(d=>d.dataset.proposal===${JSON.stringify(state.proposals[4].id)});return {text:d.textContent,buttons:[...d.querySelectorAll('.decision-actions button')].map(b=>[b.textContent,b.dataset.reviewAction])}})()`);
 assert.ok(retire.text.includes('Retiring needs your confirmation.'),retire.text);assert.ok(retire.text.includes('Waits for your confirmation'),retire.text);
 assert.deepEqual(retire.buttons,[['Cancel change','cancel'],['Yes, retire','continue']]);
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
 if(group==='all'||group==='refresh'){
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  // Polls send the last content tag; an unchanged reply carries only the clock and keeps the board.
  await refresh();const tasks=await evaluate("document.querySelectorAll('#board .task').length");let first=stateReads.length;await sleep(2000);
  const polls=stateReads.slice(first).map(r=>r.url);assert.ok(polls.length>=1&&polls.every(u=>u.includes('since=')),'polls are conditional: '+polls);
  assert.equal(await evaluate("document.querySelectorAll('#board .task').length"),tasks);
  // A service slower than the poll interval never stacks polls, and a caller waits only for its own load.
  stateDelay=1500;first=stateReads.length;
  for(let i=0;i<3;i++){const waited=await evaluate('(async()=>{const s=performance.now();await Promise.race([dayUI.flow.refresh(),new Promise(r=>setTimeout(r,5000))]);return performance.now()-s})()');assert.ok(waited<2500,'refresh waited '+waited+' ms');await sleep(300)}
  const reads=stateReads.length-first;await sleep(3000);assert.ok(stateReads.length-first-reads<=2,'polls stacked: '+(stateReads.length-first-reads)+' in 3 s');
  stateDelay=0;await sleep(1600);
  // A slower older load never overwrites state that a newer load already showed.
  slowRevision=state.revision;await evaluate('window.__older=dayUI.flow.refresh()');await sleep(50);
  target.title='Newer title';state.revision++;await refresh();await evaluate('window.__older');await sleep(1000);slowRevision=null;
  assert.equal(await evaluate(`daySnapshot().tasks.find(t=>t.id===${JSON.stringify(target.id)}).title`),'Newer title');
  assert.equal(await evaluate('daySnapshot().revision'),state.revision);
  // A saved action waits for one read that includes it, not a second one.
  await evaluate(`dayUI.flow.request('order_update',{order:${JSON.stringify([c.id,b.id,a.id,target.id])}})`);const done=Date.now();
  assert.equal(stateReads.filter(r=>r.at>=proposalReplied&&r.at<=done).length,1);delete state.taskOrder;
  // The view sends only the last fact, already summarized, without its plan.
  const summary={id:7,action:'task_done',at:state.now,proposal_id:randomUUID(),taskId:target.id,undoOf:null,undoable:true,reviewComplete:true,reviewStatus:'deferred'};
  Object.assign(state,{events:[summary],lastFact:{...summary,proposalId:summary.proposal_id}});state.revision++;await refresh();
  assert.equal(await evaluate("document.getElementById('undo').hidden"),false);
  assert.match(await evaluate("document.getElementById('status').textContent"),/Newer title/);
  state.events=[];delete state.lastFact;target.title='Responsabilidad original';state.revision++;await refresh();
  console.log('Refresh regressions passed: conditional polls, slow service, ordering of loads and summarized facts');
 }
 if(group==='all'||group==='ordering'){
  // Views and drags only rearrange the display: no priority, group or state change and no model call.
  await rpc('Emulation.setDeviceMetricsOverride',{width:1280,height:2400,deviceScaleFactor:1,mobile:false},sessionId);
  Object.assign(target,{title:'Target task',state:'todo',priority:'medium',group:'Office move',createdAt:'2025-10-20T00:00:00Z'});
  Object.assign(a,{state:'todo',priority:'high',group:'Home',createdAt:'2025-10-21T00:00:00Z'});
  Object.assign(b,{state:'todo',priority:'low',group:null,createdAt:'2025-10-22T00:00:00Z'});
  Object.assign(c,{state:'todo',priority:'high',group:'office move',createdAt:'2025-10-23T00:00:00Z'});
  state.taskOrder=[];state.orderSuggestion=null;state.readOnly=false;state.agent={connected:true,model:'controlled'};state.revision++;
  await close();await fill('locale-choice','en');await fill('order-choice','priority');await refresh();
  const lane="[...document.querySelectorAll('.lane[data-state=\"todo\"] .task')].map(e=>e.dataset.taskId)";
  const texts=selector=>evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].map(e=>e.textContent)`);
  const settle=async(predicate,message)=>{const deadline=Date.now()+5000;while(Date.now()<deadline){if(predicate())return;await new Promise(r=>setTimeout(r,15))}throw Error('Timed out: '+message)};
  const priorities=()=>state.tasks.map(t=>t.priority).join();
  assert.deepEqual(await evaluate(lane),[a.id,c.id,target.id,b.id]);
  assert.deepEqual(await texts('.lane[data-state="todo"] .priority-pill'),['Priority: High','Priority: High','Priority: Medium','Priority: Low']);
  assert.deepEqual(await texts('.lane[data-state="todo"] .group-chip'),['Group: Home','Group: office move','Group: Office move']);
  assert.match(await evaluate("document.getElementById('order-note').textContent"),/High priority first/);
  const quiet=requests.length,storedPriorities=priorities();
  for(const [view,expected] of [['manual',[a.id,c.id,target.id,b.id]],['newest',[c.id,b.id,a.id,target.id]],['group',[a.id,c.id,target.id,b.id]],['suggested',[a.id,c.id,target.id,b.id]],['priority',[a.id,c.id,target.id,b.id]]]){
   await fill('order-choice',view);assert.deepEqual(await evaluate(lane),expected,view);
   assert.equal(await evaluate("document.querySelectorAll('.drag-handle').length>0"),['manual','group','priority'].includes(view),view+' handles');
   if(view==='group'){assert.deepEqual(await texts('.lane[data-state="todo"] .group-heading'),['Home1','office move2','No group1']);assert.equal(await evaluate("document.querySelectorAll('.lane[data-state=\"todo\"] .group-chip').length"),0)}
  }
  assert.equal(requests.length,quiet);assert.equal(suggestions.length,0);assert.equal(priorities(),storedPriorities);
  assert.equal(await evaluate("localStorage.getItem('day-attendant-order')"),'priority');
  // Keyboard: arrows move a task inside its priority and save only the display order.
  const handle=id=>`.drag-handle[data-id="${id}"]`;
  let revision=state.revision;await evaluate(`document.querySelector(${JSON.stringify(handle(c.id))}).focus()`);
  await evaluate(`document.querySelector(${JSON.stringify(handle(c.id))}).dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))`);
  assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  assert.match(await evaluate("document.getElementById('status').textContent"),/position 1 of 2/);
  await settle(()=>requests.length===quiet+1,'keyboard order request');
  assert.deepEqual(requests.at(-1),{action:'order_update',payload:{order:[c.id,a.id,target.id,b.id]},key:requests.at(-1).key,expected_revision:revision});
  await wait(`document.activeElement?.dataset.id===${JSON.stringify(c.id)}&&document.activeElement.dataset.action==='move'`);
  await evaluate(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))`);await new Promise(r=>setTimeout(r,500));
  assert.equal(requests.length,quiet+1);assert.equal(priorities(),storedPriorities);
  // Pointer: a real mouse drag in My order places the task first.
  const mouse=(type,point,buttons)=>rpc('Input.dispatchMouseEvent',{type,x:point.x,y:point.y,button:'left',buttons,clickCount:type==='mouseMoved'?0:1},sessionId);
  const point=(selector,edge)=>evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:${edge==='top'?'r.top+3':edge==='bottom'?'r.bottom+40':'r.y+r.height/2'}}})()`);
  async function dragTo(from,to){await mouse('mouseMoved',from,0);await mouse('mousePressed',from,1);for(let i=1;i<=8;i++)await mouse('mouseMoved',{x:from.x,y:from.y+(to.y-from.y)*i/8},1);await mouse('mouseReleased',to,0)}
  await fill('order-choice','manual');assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  revision=state.revision;await dragTo(await point(handle(b.id)),await point(`.task[data-task-id="${c.id}"]`,'top'));
  assert.deepEqual(await evaluate(lane),[b.id,c.id,a.id,target.id]);
  await settle(()=>requests.length===quiet+2,'pointer order request');
  assert.deepEqual(requests.at(-1).payload,{order:[b.id,c.id,a.id,target.id]});assert.equal(requests.at(-1).expected_revision,revision);
  // In Priority a drag cannot leave its priority: dropping a high task below a low one keeps it high.
  await fill('order-choice','priority');assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  await dragTo(await point(handle(c.id)),await point(`.task[data-task-id="${b.id}"]`,'bottom'));
  assert.deepEqual(await evaluate(lane),[a.id,c.id,target.id,b.id]);
  await settle(()=>requests.length===quiet+3,'clamped order request');
  assert.deepEqual(requests.at(-1).payload,{order:[b.id,a.id,c.id,target.id]});
  assert.ok(requests.slice(quiet).every(r=>r.action==='order_update'));assert.equal(priorities(),storedPriorities);
  // The suggestion is fetched once on request and reused across view switches.
  await fill('order-choice','suggested');assert.match(await evaluate("document.getElementById('order-note').textContent"),/No suggestion yet/);
  await click('#suggest-order');await settle(()=>suggestions.length===1,'suggestion request');await refresh();
  assert.deepEqual(await evaluate(lane),[c.id,b.id,a.id,target.id]);assert.equal(await evaluate("document.querySelectorAll('.drag-handle').length"),0);
  assert.match((await texts('.lane[data-state="todo"] .suggestion-reason'))[0],/^1Controlled reason for /);
  const note=await evaluate("document.getElementById('order-note').textContent");assert.match(note,/Assistant suggestion from/);assert.match(note,/Controlled suggestion message\./);
  for(const view of ['priority','group','suggested','newest','suggested'])await fill('order-choice',view);
  assert.equal(suggestions.length,1);assert.equal(requests.length,quiet+3);
  state.orderSuggestion.stale=true;state.revision++;await refresh();assert.match(await evaluate("document.getElementById('order-note').textContent"),/Tasks changed since then/);
  // Localized copy; stored group labels are shown as typed.
  await fill('locale-choice','es');await fill('order-choice','group');
  assert.deepEqual(await texts('.lane[data-state="todo"] .group-heading'),['Home1','office move2','Sin grupo1']);
  assert.equal((await texts('.lane[data-state="todo"] .priority-pill'))[0],'Prioridad: Alta');
  assert.equal(await evaluate("document.querySelector('#order-choice option[value=manual]').textContent"),'Mi orden');
  assert.match(await evaluate("document.getElementById('order-note').textContent"),/Agrupado por proyecto/);
  await fill('locale-choice','en');
  // Group is edited explicitly in the card editor; an empty value removes it.
  await detail();assert.match(await evaluate("document.getElementById('detail-data').textContent"),/GroupOffice move/);
  await click('#edit-card');assert.equal(await evaluate("document.getElementById('card-group').value"),'Office move');
  await fill('card-group','');await submit('card-dialog');await saved('card-dialog');
  assert.deepEqual(requests.at(-1).payload,{uuid:target.id,group:''});assert.equal(target.group,'');await close();
  await click('#capture-open');await fill('capture-title','Grouped capture');await fill('capture-group','  Home ');await submit('capture-dialog');await saved('capture-dialog');
  assert.equal(requests.at(-1).payload.group,'Home');
  // Read-only profiles show no drag handles, and the narrowest layout does not overflow.
  state.readOnly=true;state.revision++;await refresh();assert.equal(await evaluate("document.querySelectorAll('.drag-handle').length"),0);
  state.readOnly=false;state.revision++;await refresh();
  await rpc('Emulation.setDeviceMetricsOverride',{width:320,height:900,deviceScaleFactor:1,mobile:true},sessionId);await new Promise(r=>setTimeout(r,100));
  for(const view of ['priority','group','suggested'])await fill('order-choice',view);
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);await fill('order-choice','priority');
  console.log('Ordering regressions passed: views, keyboard and pointer order, priority sections, suggestion reuse, groups and localization');
 }

 if(group==='all'||group==='instant'){
  // Direct actions show at once with their time and Undo; the assistant's later look leaves a mark.
  await fill('locale-choice','en');await close();Object.assign(state,{instant:true,proposals:[],messages:[],review:{pending:0,running:false,status:'completed',finishedAt:state.now}});
  for(const t of [a,b,c])Object.assign(t,{state:'todo',dependsOn:[],blockedBy:[],blocked:false});state.revision++;await refresh();
  const toast=()=>evaluate("({show:document.getElementById('toast').classList.contains('show'),text:document.getElementById('status').textContent,detail:document.getElementById('toast-detail').textContent,undo:!document.getElementById('undo').hidden})");
  const lane=id=>evaluate(`document.querySelector('#board [data-task-id="${id}"]')?.closest('.lane')?.dataset.state`);
  await click(`[data-id="${a.id}"][data-action="activate"]`);await wait(`document.querySelector('#board [data-task-id="${a.id}"]').closest('.lane').dataset.state==='active'`);
  await wait("document.getElementById('status').textContent==='Start · A'");
  let shown=await toast();assert.equal(shown.show,true);assert.equal(shown.text,'Start · A');assert.match(shown.detail,/^\d+ ms · the assistant looks at it in the background$/);assert.equal(shown.undo,true);
  assert.equal(requests.at(-1).action,'front_activate');
  await click('#undo');await wait(`document.querySelector('#board [data-task-id="${a.id}"]').closest('.lane').dataset.state==='todo'`);
  await wait("document.getElementById('status').textContent==='Change undone'");
  assert.equal(undos.at(-1).fact_id,String(facts-1));shown=await toast();assert.equal(shown.undo,false);
  assert.match(await evaluate("document.getElementById('review-strip').textContent"),/^Everything is saved · reviewed .+\d\d:\d\d$/);
  assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'Up to date');
  state.review={pending:2,running:false};await refresh();
  assert.equal(await evaluate("document.getElementById('review-strip').textContent"),'Saved. I look at it when you pause (2).');
  assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'2 changes to review');
  state.review={pending:2,running:true};await refresh();assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'Reviewing your changes…');
  state.review={pending:0,running:false,status:'completed',finishedAt:state.now};
  // A suggestion marks its card without moving it; the bubble opens only on request.
  const box=()=>evaluate(`(()=>{const card=document.querySelector('#board [data-task-id="${b.id}"]'),r=card.getBoundingClientRect(),t=card.querySelector('.task-title').getBoundingClientRect();return [r.top,r.height,t.left,t.top]})()`);
  await refresh();const before=await box();
  const rule={id:randomUUID(),taskId:b.id,code:'unblocked',origin:'rules',message:null,messageKey:'suggest_unblocked',messageParams:{title:'A'},action:{action:'front_activate',payload:{uuid:b.id}},status:'open',current:true,createdAt:state.now};
  state.suggestions=[rule];await refresh();
  assert.deepEqual(await box(),before);
  assert.equal(await evaluate(`document.querySelectorAll('#board [data-task-id="${b.id}"] .assistant-mark').length`),1);
  assert.equal(await evaluate(`document.querySelectorAll('#board .assistant-bubble').length`),0);
  assert.match(await evaluate("document.getElementById('messages').textContent"),/Marked «B»: Finishing «A» unblocked this one\./);
  await click(`[data-id="${b.id}"][data-action="suggestion"]`);
  assert.equal(await evaluate(`document.querySelector('#board [data-task-id="${b.id}"] .assistant-bubble').textContent`),'Suggestion: Finishing «A» unblocked this one.Start nowIgnore');
  assert.equal(await evaluate(`document.querySelector('[data-id="${b.id}"][data-action="suggestion"]').getAttribute('aria-expanded')`),'true');
  await fill('locale-choice','es');await refresh();
  assert.equal(await evaluate(`document.querySelector('#board [data-task-id="${b.id}"] .assistant-bubble').textContent`),'Sugerencia: Al terminar «A» se desbloqueó esta tarea.Iniciar ahoraIgnorar');
  await fill('locale-choice','en');await refresh();
  await click(`[data-suggestion="${rule.id}"][data-suggestion-action="accept"]`);await wait(`document.querySelector('#board [data-task-id="${b.id}"]').closest('.lane').dataset.state==='active'`);
  assert.deepEqual(suggestionCalls.at(-1).slice(0,2),[rule.id,'accept']);
  assert.equal(await evaluate('document.querySelectorAll("#board .assistant-mark").length'),0);
  assert.match(await evaluate("document.getElementById('messages').textContent"),/Marked «B»: Finishing «A» unblocked this one\. · handled/);
  await wait("document.getElementById('status').textContent==='Start · B'");shown=await toast();assert.equal(shown.undo,true);
  const note={id:randomUUID(),taskId:c.id,code:'assistant',origin:'assistant',message:'Consider doing this before the meeting.',messageKey:null,messageParams:{},action:null,status:'open',current:true,createdAt:state.now};
  state.suggestions.push(note);await refresh();await click(`[data-id="${c.id}"][data-action="suggestion"]`);
  assert.equal(await evaluate('document.querySelector("#board .assistant-bubble").textContent'),'Suggestion: Consider doing this before the meeting.Ignore');
  await click(`[data-suggestion="${note.id}"][data-suggestion-action="dismiss"]`);await wait('!document.querySelector("#board .assistant-mark")');
  assert.deepEqual(suggestionCalls.at(-1).slice(0,2),[note.id,'dismiss']);
  // Phone widths keep the mark a full touch target and never overflow.
  note.current=true;note.status='open';await refresh();
  await rpc('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true},sessionId);await new Promise(r=>setTimeout(r,100));
  assert.ok(await evaluate('document.querySelector("#board .assistant-mark").getBoundingClientRect().width>=40'));
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);
  state.agent={...state.agent,connected:false};await refresh();assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'Disconnected');
  state.agent={...state.agent,connected:true};Object.assign(state,{suggestions:[],review:{}});await refresh();
  let release;const gate=new Promise(r=>release=r);const original=server.listeners('request')[0];
  server.removeAllListeners('request');server.on('request',async(req,res)=>{if(req.url==='/api/proposals')await gate;return original(req,res)});
  await click(`[data-id="${c.id}"][data-action="activate"]`);
  assert.equal(await lane(c.id),'active');assert.equal(c.state,'todo');
  release();await wait(`daySnapshot().tasks.find(t=>t.id===${JSON.stringify(c.id)}).state==='active'`);assert.equal(c.state,'active');
  await wait("document.getElementById('status').textContent==='Start · C'&&!document.getElementById('undo').hidden");
  let releaseUndo;const undoGate=new Promise(r=>releaseUndo=r);
  server.removeAllListeners('request');server.on('request',async(req,res)=>{if(req.url==='/api/undo')await undoGate;return original(req,res)});
  await click('#undo');assert.equal(await lane(c.id),'todo');assert.equal(c.state,'active');
  releaseUndo();await wait("document.getElementById('status').textContent==='Change undone'");assert.equal(c.state,'todo');
  server.removeAllListeners('request');server.on('request',original);
  await click(`[data-id="${c.id}"][data-action="activate"]`);await wait(`document.getElementById('status').textContent==='Start · C'`);
  const stale=state.revision;c.state='todo';state.revision++;
  await evaluate(`dayUI.flow.request('front_retire',{taskId:${JSON.stringify(c.id)}},undefined,${stale}).catch(()=>{})`);
  await wait(`document.querySelector('#board [data-task-id="${c.id}"]')?.closest('.lane')?.dataset.state==='todo'`);
  // Quick consecutive clicks each carry the revision the previous answer produced.
  const sentBefore=requests.length,opening=state.revision;await click(`[data-id="${a.id}"][data-action="activate"]`);await click(`[data-id="${b.id}"][data-action="todo"]`);
  for(let i=0;i<200&&b.state!=='todo';i++)await new Promise(r=>setTimeout(r,15));
  assert.equal(b.state,'todo');assert.equal(a.state,'active');
  assert.deepEqual(requests.slice(sentBefore).map(r=>[r.action,r.expected_revision]),[['front_activate',opening],['front_todo',opening+1]]);
  state.instant=false;
  console.log('Instant action regressions passed: change and Undo shown before the answer, sequential revisions, toast timing, review state, suggestion mark, bubble, accept, dismiss and log');
 }
}finally{
 if(socket)socket.close();chrome.kill('SIGTERM');await new Promise(resolve=>chrome.exitCode!==null?resolve():chrome.once('exit',resolve));
 await new Promise(resolve=>server.close(resolve));await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
