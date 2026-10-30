import assert from 'node:assert/strict';
import {execFileSync,spawn} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {readFile,mkdtemp,rm} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const group=process.argv[3]||'all';
assert.ok(['all','details','dates','conversation','dependencies','ordering','refresh','instant','compact','snooze','drag','groups','quick'].includes(group),'Unknown browser regression group');
const task=(title)=>({id:randomUUID(),title,state:'todo',priority:'medium',due:null,until:null,context:'',next:'',awaiting:false,remainingMinutes:null,dependsOn:[],relatedTo:[],blocked:false,blockedBy:[],attachmentIds:[],source:'general'});
const target=task('Responsabilidad original'),a=task('A'),b=task('B'),c=task('C');
// Historical DST fixtures remain stable as jurisdictions change future rules.
const state={profile:{id:randomUUID(),name:'Controlled profile',timezone:'America/Edmonton',locale:'en',harness:'codex',model:'controlled'},timezone:'America/Edmonton',now:'2025-10-28T12:00:00Z',revision:1,epoch:0,synthetic:true,readOnly:false,tasks:[target,a,b,c],events:[],proposals:[],messages:[],calendar_events:[],context:{availableMinutes:null,availableUntil:null,rest:false},agent:{connected:true,model:'controlled'},diagnostics:{operations:[],reminders:[]},operations_status:{},notification_policy:{enabled:false,weekdays:[0,1,2,3,4],start:'09:00',end:'18:00',quietStart:'22:00',quietEnd:'08:00',intervalMinutes:10,unchangedRepeatMinutes:1440,dailyBudget:12,pausedUntil:null}};
const proposalsBefore=new Map(),requests=[],settings=[],chats=[],clears=[],suggestions=[],undos=[],suggestionCalls=[],undoable=new Map();let catalogs=null,facts=0;
// State reads can be slowed to reproduce a service that answers after the poll interval.
// The service's own zone rules, as its state view sends them.
const zoneRules=new Map(),rulesFor=zone=>{if(!zoneRules.has(zone))zoneRules.set(zone,JSON.parse(execFileSync('python3',['-B','-c','import datetime,json,sys;from day_attendant.timeutil import zone_rules;print(json.dumps(zone_rules(sys.argv[1],datetime.date(2025,10,28))))',zone],{cwd:root,encoding:'utf8'})));return zoneRules.get(zone)};
const stateReads=[];let stateDelay=0,slowRevision=null,proposalReplied=0;
const server=createServer(async(req,res)=>{
 try{
  if(req.url.startsWith('/api/')){
   let body='';for await(const chunk of req)body+=chunk;body=body?JSON.parse(body):{};
   let data=state;
   if(req.url.startsWith('/api/state')){
    stateReads.push({url:req.url,at:Date.now()});const read={...structuredClone(state),zoneRules:rulesFor(state.timezone)},{now,...content}=read;
    const tag=createHash('sha256').update(JSON.stringify(content)).digest('hex').slice(0,32);
    if(stateDelay||read.revision===slowRevision)await new Promise(r=>setTimeout(r,read.revision===slowRevision?800:stateDelay));
    data=new URL(req.url,'http://local').searchParams.get('since')===tag?{unchanged:true,tag,now}:{...read,tag};
   }else if(req.url==='/api/proposals'){
    requests.push(body);
    if(body.expected_revision!==state.revision){res.writeHead(409,{'Content-Type':'application/json'});res.end(JSON.stringify({ok:false,error:{code:'stale_revision',message:'State changed; reopen this editor.'}}));return}
    const p=body.payload,t=state.tasks.find(t=>t.id===p.uuid),beforeTask=t&&{...t},beforeOrder=state.taskOrder,beforeColors={...state.groupColors};
    if(body.action==='dependency_update')t.dependsOn=p.dependsOn;
    if(body.action==='related_update')t.relatedTo=p.relatedTo;
    if(body.action==='task_update')for(const key of ['title','priority','due','context','next','awaiting','group'])if(key in p)t[key]=p[key];
    if(body.action==='continuity_update')for(const key of ['context','next','awaiting','remainingMinutes'])if(key in p)t[key]=p[key];
    if(p.order)state.taskOrder=p.order;
    // The quick capture group creates the task as the service does; other groups only record the request.
    if(body.action==='task_add'&&state.quickAdd)state.tasks.push({...task(p.title),priority:p.priority,group:p.group||null,due:p.due||null,createdAt:state.now});
    if(body.action==='group_color_update'){state.groupColors={...state.groupColors};if(p.color)state.groupColors[p.group.trim().toLowerCase()]=p.color;else delete state.groupColors[p.group.trim().toLowerCase()]}
    const nextState={front_activate:'active',front_todo:'todo',task_done:'done',front_retire:'retired',task_reopen:'todo'}[body.action];
    if(nextState)t.state=nextState;
    if(body.action==='task_snooze')Object.assign(t,{state:'snoozed',until:p.until});
    if(body.action==='context_update')Object.assign(state.context,p);
    if(body.action==='event_update')Object.assign(state.calendar_events.find(e=>e.uuid===p.uuid),{title:p.title,start_at:p.start,end_at:p.end});
    const canceledEvent=body.action==='event_cancel'&&state.calendar_events.find(e=>e.uuid===p.uuid);
    if(body.action==='event_cancel')state.calendar_events=state.calendar_events.filter(e=>e.uuid!==p.uuid);
    // The instant group records each change as an undoable fact, as the service does.
    const proposalId=randomUUID();proposalsBefore.set(proposalId,{id:t?.id,before:beforeTask,order:beforeOrder});
    if(state.instant&&canceledEvent){const fact=++facts;undoable.set(fact,{event:canceledEvent});state.lastFact={id:fact,proposalId,action:body.action,taskId:p.uuid,undoable:true,undoOf:null,reviewStatus:'deferred'}}
    if(state.instant&&(t||body.action==='group_color_update')){const fact=++facts;undoable.set(fact,t?{id:t.id,before:beforeTask}:{group:p.group.trim().toLowerCase(),before:beforeColors[p.group.trim().toLowerCase()]});state.lastFact={id:fact,proposalId,action:body.action,taskId:t?.id,undoable:true,undoOf:null,reviewStatus:'deferred'}}
    state.revision++;
    data={id:proposalId,key:body.key,intent:{action:body.action,payload:p},phase:'applied',generation:0,facts:[],result:{revision:state.revision}};proposalReplied=Date.now();
   }else if(req.url==='/api/undo'&&body.proposal_ids){
    // A drop's changes undo together, newest first.
    undos.push(body);const undone=[];
    for(const id of [...body.proposal_ids].reverse()){const was=proposalsBefore.get(id);if(!was)continue;if(was.id)Object.assign(state.tasks.find(t=>t.id===was.id),was.before);else state.taskOrder=was.order;state.revision++;
     const proposalId=randomUUID();undone.push({id:proposalId,phase:'applied',result:{revision:state.revision}});state.lastFact={id:++facts,proposalId,action:was.id?'task_restore':'order_update',taskId:was.id||null,undoable:true,undoOf:id,reviewStatus:'deferred'}}
    data=undone;
   }else if(req.url==='/api/undo'){
    undos.push(body);const last=undoable.get(Number(body.fact_id));
    const proposalId=randomUUID();
    if(last?.event){state.calendar_events=[...state.calendar_events,last.event];state.revision++;state.lastFact={id:++facts,proposalId,action:'event_restore',taskId:last.event.uuid,undoable:true,undoOf:Number(body.fact_id),reviewStatus:'deferred'}}
    else if(last){if(last.group!==undefined){if(last.before)state.groupColors[last.group]=last.before;else delete state.groupColors[last.group]}else Object.assign(state.tasks.find(t=>t.id===last.id),last.before);state.revision++;state.lastFact={id:++facts,proposalId,action:'task_restore',taskId:last.id,undoable:true,undoOf:Number(body.fact_id),reviewStatus:'deferred'}}
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
 // Cards glide to new places; positions are read once the board is still.
 const still="for(let moving;(moving=document.getAnimations().filter(a=>a.playState!=='finished'&&a.effect?.getComputedTiming().endTime!==Infinity&&a.effect?.target?.closest?.('#board,#motion-layer'))).length;)await Promise.all(moving.map(a=>a.finished.catch(()=>{})));";
 const fill=(id,value)=>evaluate(`(()=>{const e=document.getElementById(${JSON.stringify(id)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 const close=()=>evaluate(`document.querySelectorAll('dialog[open]').forEach(d=>d.close())`);
 const refresh=()=>evaluate('(async()=>{while(dayUI.flow.busy)await new Promise(resolve=>setTimeout(resolve,5));await dayUI.flow.refresh()})()');
 const row=selector=>`[data-task-id="${target.id}"] ${selector}`,text=selector=>evaluate(`document.querySelector(${JSON.stringify(selector)}).textContent`);
 const expand=()=>evaluate(`document.querySelector(${JSON.stringify(row('.expand'))})||dayUI.expandTask(${JSON.stringify(target.id)})`);
 const collapse=()=>evaluate(`document.querySelector(${JSON.stringify(row('.expand'))})&&document.querySelector(${JSON.stringify(row('[data-x=close]'))}).click()`);
 const put=(selector,value)=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.focus();e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}))})()`);
 const key=(selector,name)=>evaluate(`document.querySelector(${JSON.stringify(selector)}).dispatchEvent(new KeyboardEvent('keydown',{key:${JSON.stringify(name)},bubbles:true,cancelable:true}))`);
 const serverWait=async(predicate)=>{const deadline=Date.now()+5000;while(!predicate()){if(Date.now()>deadline)throw Error('Timed out: '+predicate);await new Promise(r=>setTimeout(r,15))}await evaluate('(async()=>{while(dayUI.flow.sending)await new Promise(r=>setTimeout(r,5))})()')};
 const openDialogs="document.querySelectorAll('dialog[open]').length";
 const submit=id=>evaluate(`document.querySelector('#'+${JSON.stringify(id)}+' form').requestSubmit()`);
 const saved=id=>wait(`!document.getElementById(${JSON.stringify(id)}).open`);
 const failed=id=>wait(`!document.getElementById(${JSON.stringify(id+'-error')}).hidden`);
 await rpc('Emulation.setTimezoneOverride',{timezoneId:'UTC'},sessionId);
 await rpc('Page.navigate',{url:'http://127.0.0.1:'+server.address().port},sessionId);
 await wait('globalThis.dayDaily && daySnapshot().profile');

 if(group==='all'||group==='details'){
  // The title edits in place: Enter saves at once with the latest revision; Esc and an empty title keep it.
  Object.assign(target,{state:'todo',group:null,due:null,context:'',next:'',awaiting:false,remainingMinutes:null,dependsOn:[],relatedTo:[]});state.revision++;await refresh();
  await click(row('[data-action=title]'));assert.equal(await evaluate(`document.activeElement===document.querySelector(${JSON.stringify(row('.title-edit'))})`),true);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(row('.title-edit'))}).value`),target.title);
  let before=requests.length;await put(row('.title-edit'),'Edited in place');await key(row('.title-edit'),'Enter');
  assert.equal(await text(row('[data-action=title]')),'Edited in place','the new title shows before the answer');
  await serverWait(()=>target.title==='Edited in place');
  assert.deepEqual([requests.length-before,requests.at(-1).action,requests.at(-1).payload,requests.at(-1).expected_revision],[1,'task_update',{uuid:target.id,title:'Edited in place'},state.revision-1]);
  before=requests.length;await click(row('[data-action=title]'));await put(row('.title-edit'),'Discarded');await key(row('.title-edit'),'Escape');
  assert.equal(await evaluate(`document.activeElement===document.querySelector(${JSON.stringify(row('[data-action=title]'))})`),true,'Esc returns to the title');
  await click(row('[data-action=title]'));await put(row('.title-edit'),'   ');await key(row('.title-edit'),'Enter');
  await new Promise(r=>setTimeout(r,100));assert.equal(requests.length,before);assert.equal(await text(row('[data-action=title]')),'Edited in place');
  // Someone else changes the same title while it is typed: nothing is sent, the row says so and keeps the draft.
  await click(row('[data-action=title]'));await put(row('.title-edit'),'My draft title');
  target.title='Changed on the phone';state.revision++;await refresh();
  assert.equal(await evaluate(`document.activeElement===document.querySelector(${JSON.stringify(row('.title-edit'))})`),true,'a redraw keeps the editor focused');
  before=requests.length;await key(row('.title-edit'),'Enter');await wait(`document.querySelector(${JSON.stringify(row('.field-conflict'))})!==null`);
  assert.equal(requests.length,before);assert.equal(await evaluate(`document.querySelector(${JSON.stringify(row('.title-edit'))}).value`),'My draft title');
  assert.match(await text(row('.field-conflict')),/It now says: Changed on the phone\. What you typed is still here\./);
  assert.equal(await evaluate(openDialogs),0,'nothing opens over the row');
  await click(row('[data-conflict=mine]'));await serverWait(()=>target.title==='My draft title');
  assert.deepEqual(requests.at(-1).payload,{uuid:target.id,title:'My draft title'});
  await wait(`document.querySelector(${JSON.stringify(row('.field-conflict'))})===null&&document.querySelector(${JSON.stringify(row('.title-edit'))})===null`);
  // Clicking the row's empty space opens its details below it, never a dialog; ⋯ and Esc close them.
  await click(row('.row-main'));await wait(`document.querySelector(${JSON.stringify(row('.expand'))})!==null`);
  assert.equal(await evaluate(openDialogs),0);
  assert.equal(await evaluate(`document.activeElement===document.querySelector(${JSON.stringify(row('[data-field=context]'))})`),true);
  await evaluate("document.activeElement.blur()");await evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(row('.expand'))})`),null,'Esc closes the details');
  await click(row('[data-action=more]'));assert.equal(await text('.row-menu [data-menu-act=details]'),'More details');await click('.row-menu [data-menu-act=details]');
  assert.notEqual(await evaluate(`document.querySelector(${JSON.stringify(row('.expand'))})`),null);
  await click(row('[data-action=more]'));assert.equal(await text('.row-menu [data-menu-act=details]'),'Hide details');await click('.menu-scrim, .row-menu [data-menu-act=details]');await expand();
  // Each field saves by itself after a pause, on blur or on Enter; typing keeps its focus and caret.
  before=requests.length;await put(row('[data-field=context]'),'Left at page 3');await new Promise(r=>setTimeout(r,250));
  assert.equal(requests.length,before,'typing waits for a pause');
  await serverWait(()=>target.context==='Left at page 3');assert.deepEqual([requests.at(-1).action,requests.at(-1).payload],['continuity_update',{uuid:target.id,context:'Left at page 3'}]);
  assert.equal(await evaluate(`document.activeElement===document.querySelector(${JSON.stringify(row('[data-field=context]'))})&&document.activeElement.selectionStart===14`),true);
  await wait(`/Where I left off saved/.test(document.querySelector(${JSON.stringify(row('.saved-note'))}).textContent)`);
  await click(row('[data-field=awaiting]'));await serverWait(()=>target.awaiting===true);assert.deepEqual(requests.at(-1).payload,{uuid:target.id,awaiting:true});
  before=requests.length;await put(row('[data-field=remainingMinutes]'),'-5');await key(row('[data-field=remainingMinutes]'),'Enter');
  await wait(`document.querySelector(${JSON.stringify(row('.field-error'))})!==null`);assert.equal(requests.length,before,'an invalid estimate is not sent');
  await put(row('[data-field=remainingMinutes]'),'25');await key(row('[data-field=remainingMinutes]'),'Enter');await serverWait(()=>target.remainingMinutes===25);
  assert.deepEqual(requests.at(-1).payload,{uuid:target.id,remainingMinutes:25});
  // The service saw a newer change elsewhere: the page reads it and, with this field untouched, saves once more.
  before=requests.length;await put(row('[data-field=next]'),'Call the bank');target.priority='high';state.revision++;
  await evaluate(`document.querySelector(${JSON.stringify(row('[data-field=next]'))}).blur()`);await serverWait(()=>target.next==='Call the bank');
  const retried=requests.slice(before);assert.deepEqual(retried.at(-1).payload,{uuid:target.id,next:'Call the bank'});assert.ok(retried.length<=2);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(row('.field-conflict'))})`),null);
  // The same field changed elsewhere: the draft stays, the row offers both, and nothing is overwritten.
  await put(row('[data-field=next]'),'My next step');target.next='Their next step';state.revision++;
  await evaluate(`document.querySelector(${JSON.stringify(row('[data-field=next]'))}).blur()`);await wait(`document.querySelector(${JSON.stringify(row('.field-conflict'))})!==null`);
  assert.equal(target.next,'Their next step');assert.equal(await evaluate(`document.querySelector(${JSON.stringify(row('[data-field=next]'))}).value`),'My next step');
  assert.match(await text(row('.field-conflict')),/It now says: Their next step/);
  before=requests.length;await click(row('[data-conflict=theirs]'));await wait(`document.querySelector(${JSON.stringify(row('[data-field=next]'))}).value==='Their next step'`);assert.equal(requests.length,before);
  // Retire asks in the row; Duplicate copies title, priority and group.
  await click(row('[data-x=retire]'));assert.match(await text(row('.inline-confirm')),/Retire this responsibility\?/);await click(row('[data-x=retire-no]'));assert.equal(requests.length,before);
  await click(row('[data-x=duplicate]'));await serverWait(()=>requests.length>before);assert.deepEqual([requests.at(-1).action,requests.at(-1).payload],['task_add',{title:target.title,priority:target.priority}]);
  before=requests.length;await click(row('[data-x=retire]'));await click(row('[data-x=retire-yes]'));await serverWait(()=>target.state==='retired');
  assert.deepEqual([requests.length-before,requests.at(-1).action,requests.at(-1).payload],[1,'front_retire',{uuid:target.id}]);
  await wait(`document.querySelector('[data-reopen-id="${target.id}"]')!==null`);await click(`[data-reopen-id="${target.id}"]`);await serverWait(()=>target.state==='todo');
  assert.deepEqual([requests.at(-1).action,requests.at(-1).payload],['task_reopen',{uuid:target.id}]);
  // Group: a one-level menu from its chip, with a new group typed in place or none.
  await wait(`document.querySelector(${JSON.stringify(row('[data-action=group]'))})!==null`);
  await click(row('[data-action=group]'));assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].map(e=>e.textContent)"),['No group']);
  before=requests.length;await put('.row-menu [data-menu-input=group]','Errands');await key('.row-menu [data-menu-input=group]','Enter');
  assert.match(await text(row('[data-action=group]')),/Errands$/);await serverWait(()=>target.group==='Errands');assert.deepEqual(requests.at(-1).payload,{uuid:target.id,group:'Errands'});
  await click(row('[data-action=group]'));await click('.row-menu [data-menu-act=group][data-value=""]');await serverWait(()=>target.group==='');assert.deepEqual(requests.at(-1).payload,{uuid:target.id,group:''});
  // Deadline: ⋯ swaps to the deadline choices in the same menu; presets are 18:00 in the profile's zone.
  await click(row('[data-action=more]'));await click('.row-menu [data-menu-act=set-due]');
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu [data-menu-act=due]')].map(e=>e.dataset.value)"),['2025-10-29T00:00:00.000Z','2025-10-30T00:00:00.000Z','2025-11-01T00:00:00.000Z','2025-11-05T01:00:00.000Z']);
  await click('.row-menu [data-menu-act=due]');await serverWait(()=>target.due==='2025-10-29T00:00:00.000Z');assert.deepEqual(requests.at(-1).payload,{uuid:target.id,due:'2025-10-29T00:00:00.000Z'});
  for(const invalid of ['2025-11-02T01:30','2025-03-09T02:30']){
   await wait(`document.querySelector(${JSON.stringify(row('[data-action=due]'))})!==null`);await click(row('[data-action=due]'));before=requests.length;
   await put('.row-menu [data-menu-input=due]',invalid);await click('.row-menu [data-menu-act=due-custom]');
   assert.equal(await evaluate("!document.querySelector('.row-menu .menu-error').hidden"),true,invalid+' is rejected in the menu');assert.equal(requests.length,before);
   await key('.row-menu [data-menu-input=due]','Escape');
  }
  await click(row('[data-action=due]'));await put('.row-menu [data-menu-input=due]','2025-11-02T03:30');await key('.row-menu [data-menu-input=due]','Enter');
  await serverWait(()=>target.due==='2025-11-02T10:30:00.000Z');
  await click(row('[data-action=due]'));await click('.row-menu [data-menu-act=due-clear]');await serverWait(()=>target.due===null);assert.deepEqual(requests.at(-1).payload,{uuid:target.id,due:null});
  assert.equal(await evaluate(openDialogs),0,'no field needed a dialog');
  await evaluate("document.activeElement?.blur?.()");await collapse();
  Object.assign(target,{title:'Responsabilidad original',priority:'medium',context:'',next:'',awaiting:false,remainingMinutes:null});state.revision++;await refresh();
  console.log('Inline editing regressions passed: title in place, conflicts keep the draft, details below the row, field autosave, menus for group and deadline');
 }
 if(group==='all'||group==='details'){
  target.state='active';state.revision++;await refresh();const opening=state.revision;
  target.state='done';state.revision++;
  await evaluate("dayUI.notice('')");await click(`[data-id="${target.id}"][data-action="todo"]`);
  await wait("document.getElementById('status').textContent!==''");
  assert.equal(requests.at(-1).action,'front_todo');assert.equal(requests.at(-1).expected_revision,opening);assert.equal(target.state,'done');
  target.state='active';state.revision++;await refresh();const current=state.revision;
  await click(`[data-id="${target.id}"][data-action="todo"]`);await wait(`daySnapshot().tasks.find(t=>t.id===${JSON.stringify(target.id)}).state==='todo'`);
  assert.equal(requests.at(-1).expected_revision,current);assert.equal(target.state,'todo');
  target.state='todo';state.revision++;await refresh();
  console.log('Row action revision regressions passed');
 }
 if(group==='all'||group==='dates'){
  for(const instant of ['2025-11-02T07:30:21Z','2025-11-02T08:30:21Z','2025-11-02T10:30:21Z']){
   target.due=instant;state.revision++;await refresh();await click(row('[data-action=title]'));
   const before=requests.length;await put(row('.title-edit'),'Title with '+instant);await key(row('.title-edit'),'Enter');await serverWait(()=>requests.length>before);
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
   await click(row('[data-action=due]'));await put('.row-menu [data-menu-input=due]',invalid);
   let before=requests.length;await click('.row-menu [data-menu-act=due-custom]');assert.equal(await evaluate("!document.querySelector('.row-menu .menu-error').hidden"),true);assert.equal(requests.length,before);
   assert.equal(await evaluate("document.querySelector('.row-menu [data-menu-input=due]').value"),invalid,'the typed date stays');await key('.row-menu [data-menu-input=due]','Escape');
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
  // A compact chip counts the links; its title names them.
  const chipText=(t,kind)=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(chip(t,kind))});return e.textContent+' | '+e.title})()`);
  assert.equal(await chipText(a,'blocking-label'),'Blocks 2 | Blocks · '+target.title+', B');
  assert.equal(await chipText(target,'blocked-label'),'Blocked by 1 | Blocked · A');
  assert.equal(await evaluate(`document.querySelector('[data-task-id="${c.id}"] .dependency-chip')`),null);await shows(none);
  const fromA={focus:true,roles:['source','dependent','dependent',null],tags:['Depends on it','Depends on it'],pressed:[a.id],lines:2};
  await click(chip(a,'blocking-label'));await shows(fromA);
  state.revision++;await refresh();await shows(fromA);
  await fill('locale-choice','es');await shows({...fromA,tags:['Depende de esta','Depende de esta']});
  assert.equal(await chipText(a,'blocking-label'),'Bloquea 2 | Bloquea · '+target.title+', B');
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
 // Links are added from a picker in the expanded row and removed with ×; unrequested links are kept.
 for(const [action,kind,key] of [['dependency_update','dep','dependsOn'],['related_update','rel','relatedTo']]){
  for(const t of [a,b,c])t.dependsOn=[];target[key]=[a.id];state.revision++;await refresh();await expand();
  assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(row('.rel-go'))})].map(e=>e.textContent)`),['A']);
  const picks=row(`[data-search=${kind}] + .picker-list:not([hidden]) [data-pick]`);
  await put(row(`[data-search=${kind}]`),'B');assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(picks)})].map(e=>e.dataset.pick)`),[kind+':'+b.id]);
  let before=requests.length;await click(picks);await serverWait(()=>target[key].length===2);
  assert.deepEqual([requests.length-before,requests.at(-1).action,requests.at(-1).payload],[1,action,{uuid:target.id,[key]:[a.id,b.id]}]);
  // A link changed elsewhere and not read yet: the service refuses, nothing is lost, and the next try keeps it.
  target[key]=[a.id,b.id,c.id];state.revision++;before=requests.length;await click(row(`[data-unlink="${kind}:${b.id}"]`));
  await wait(`document.getElementById('status').textContent!==''`);await serverWait(()=>requests.length>before);assert.deepEqual(target[key],[a.id,b.id,c.id]);
  await refresh();await wait(`document.querySelectorAll(${JSON.stringify(row('.rel-go'))}).length===3`);
  await click(row(`[data-unlink="${kind}:${b.id}"]`));await serverWait(()=>target[key].length===2);assert.deepEqual(target[key],[a.id,c.id]);
  target[key]=[];state.revision++;await refresh();
 }
 // A prerequisite never closes a loop: a task that waits for this one is not offered.
 b.dependsOn=[target.id];state.revision++;await refresh();await expand();await put(row('[data-search=dep]'),'');
 assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(row('[data-search=dep] + .picker-list:not([hidden]) [data-pick]'))})].map(e=>e.dataset.pick).sort()`),['dep:'+a.id,'dep:'+c.id].sort());
 // Required by lists the dependents; × removes this task from that dependent's prerequisites only.
 assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(row('[data-unlink^=dependent]'))})].map(e=>e.dataset.unlink)`),['dependent:'+b.id]);
 const sentLinks=requests.length;await click(row(`[data-unlink="dependent:${b.id}"]`));await serverWait(()=>b.dependsOn.length===0);
 assert.deepEqual([requests.length-sentLinks,requests.at(-1).action,requests.at(-1).payload],[1,'dependency_update',{uuid:b.id,dependsOn:[]}]);
 await evaluate("document.activeElement?.blur?.()");await collapse();
 target.title='Requested replacement';state.revision++;await refresh();

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
 const cardState=()=>evaluate(`(()=>{const card=document.querySelector(${JSON.stringify(cardSelector)});return {reason:card.querySelector('.blocked-label')?.title,actionDisabled:card.querySelector('.state-action').disabled,snoozeDisabled:card.querySelector('[data-action=snooze]').disabled,pendingDisabled:card.querySelector('[data-action=pending]').disabled,titleDisabled:card.querySelector('[data-action=title]').disabled}})()`);
 for(const mode of ['todo','active','snoozed']){
  target.state=mode;target.until=mode==='snoozed'?'2025-10-29T12:00:00Z':null;await refresh();
  // A blocked task cannot be started or resumed from its row; pausing stays available.
  const expected={reason:'Blocked · '+a.title,actionDisabled:mode!=='active',snoozeDisabled:false,pendingDisabled:false,titleDisabled:false};
  assert.deepEqual(await cardState(),expected);
  await evaluate(`globalThis.previousCard=document.querySelector(${JSON.stringify(cardSelector)})`);
  await click(cardSelector+' [data-action=pending]');
  // The board re-renders in place: an unchanged card keeps its element and its state.
  assert.equal(await evaluate(`previousCard===document.querySelector(${JSON.stringify(cardSelector)})`),true);
  assert.deepEqual(await cardState(),expected);
  await fill('locale-choice','es');await refresh();assert.deepEqual(await cardState(),{...expected,reason:'Bloqueada · '+a.title});
  await fill('locale-choice','en');await refresh();assert.deepEqual(await cardState(),expected);
 }
 target.state='todo';target.until=null;state.readOnly=true;await refresh();
 const readOnlyCard={reason:'Blocked · '+a.title,actionDisabled:true,snoozeDisabled:true,pendingDisabled:true,titleDisabled:false};
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
 target.due='2025-11-03T01:00:00Z';state.revision++;await refresh();await click(row('[data-action=due]'));await put('.row-menu [data-menu-input=due]','2025-11-02T01:30');let before=requests.length;await click('.row-menu [data-menu-act=due-custom]');assert.equal(await evaluate("!document.querySelector('.row-menu .menu-error').hidden"),true);assert.equal(requests.length,before);await put('.row-menu [data-menu-input=due]','2025-11-02T03:30');await click('.row-menu [data-menu-act=due-custom]');await serverWait(()=>target.due==='2025-11-02T10:30:00.000Z');
 await click('#settings-open');await fill('policy-pause','2025-11-02T01:30');await submit('settings-dialog');await failed('settings-dialog');await fill('policy-pause','2025-11-02T03:30');await submit('settings-dialog');await saved('settings-dialog');
 await click('#context-open');await fill('context-minutes','10');await fill('context-until','2025-11-02T01:30');before=requests.length;await submit('context-dialog');await wait("!document.getElementById('context-error').hidden");assert.equal(requests.length,before);await fill('context-until','2025-11-02T03:30');await submit('context-dialog');await saved('context-dialog');
 state.timezone='Australia/Lord_Howe';await refresh();assert.equal(await evaluate("dayUI.ambiguous(dayUI.parseLocal('2025-04-06T01:45'))"),true);
 for(const locale of ['en','es']){
  await fill('locale-choice',locale);
  for(const status of ['done','retired']){
   // A closed task stays listed with its state and reopens in one click.
   target.state=status;await refresh();const closedRow=await evaluate(`document.querySelector('[data-reopen-id="${target.id}"]').closest('.closed-row').textContent`);
   assert.ok(closedRow.includes(target.title)&&closedRow.includes(locale==='en'?(status==='done'?'Completed':'Retired'):(status==='done'?'Terminada':'Retirada')),closedRow);
   assert.equal(await text(`[data-reopen-id="${target.id}"]`),locale==='en'?'Reopen':'Reabrir');
  }
  // Its source is shown in the expanded row, with authored labels in the interface language.
  target.state='todo';state.revision++;await refresh();await expand();
  assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(row('.meta-grid dt'))})].slice(0,3).map(e=>e.textContent)`),locale==='en'?['Source','Person','Reference']:['Fuente','Persona','Referencia']);
  assert.deepEqual(await evaluate(`[...document.querySelectorAll(${JSON.stringify(row('.meta-grid dd'))})].slice(0,3).map(e=>e.textContent)`),['chat','Ana','Referencia literal']);await collapse();
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
  state.taskOrder=[];state.orderSuggestion=null;state.readOnly=false;state.agent={connected:true,model:'controlled'};state.instant=true;state.revision++;
  await close();await fill('locale-choice','en');await fill('order-choice','priority');await refresh();
  const lane="[...document.querySelectorAll('.lane[data-state=\"todo\"] .task')].map(e=>e.dataset.taskId)";
  const texts=selector=>evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].map(e=>e.textContent)`);
  const sections="[...document.querySelectorAll('#board .group-section')].map(s=>[s.querySelector('h2').textContent,[...s.querySelectorAll('.task')].map(e=>e.dataset.taskId)])";
  const settle=async(predicate,message)=>{const deadline=Date.now()+5000;while(Date.now()<deadline){if(predicate())return;await new Promise(r=>setTimeout(r,15))}throw Error('Timed out: '+message)};
  const priorities=()=>state.tasks.map(t=>t.priority).join();
  assert.deepEqual(await evaluate(lane),[a.id,c.id,target.id,b.id]);
  assert.deepEqual(await texts('.lane[data-state="todo"] .priority-pill'),['Priority: High','Priority: High','Priority: Medium','Priority: Low']);
  assert.deepEqual(await texts('.lane[data-state="todo"] .group-chip:not(.add)'),['Group: Home','Group: office move','Group: Office move']);
  // Orders other than Suggested need no standing notice.
  const noteHidden="(()=>{const n=document.getElementById('order-note');return n.textContent===''&&getComputedStyle(n).display==='none'})()";
  assert.equal(await evaluate(noteHidden),true);
  const quiet=requests.length,storedPriorities=priorities();
  for(const [view,expected] of [['manual',[a.id,c.id,target.id,b.id]],['newest',[c.id,b.id,a.id,target.id]],['group',[a.id,c.id,target.id,b.id]],['suggested',[a.id,c.id,target.id,b.id]],['priority',[a.id,c.id,target.id,b.id]]]){
   await fill('order-choice',view);
   assert.equal(await evaluate("document.querySelectorAll('.drag-handle').length>0"),['manual','group','priority'].includes(view),view+' handles');
   // In group order Today shows one section per group across states; the Board keeps group headings in each column.
   if(view==='group'){assert.deepEqual(await evaluate(sections),[['Home',[a.id]],['office move',[c.id,target.id]],['No group',[b.id]]]);assert.equal(await evaluate("document.querySelectorAll('#board .group-chip:not(.plain):not(.add), #board .task .group-dot').length"),0);
    await click('#board-view');assert.deepEqual(await evaluate(lane),expected,view);assert.deepEqual(await texts('.lane[data-state="todo"] .group-heading'),['Home1','office move2','No group1']);await click('#list-view')}
   else assert.deepEqual(await evaluate(lane),expected,view);
  }
  assert.equal(requests.length,quiet);assert.equal(suggestions.length,0);assert.equal(priorities(),storedPriorities);
  assert.equal(await evaluate("localStorage.getItem('day-attendant-order')"),'priority');
  // Keyboard: arrows move a task inside its priority and save only the display order.
  const handle=id=>`.drag-handle[data-id="${id}"]`;
  let revision=state.revision;await evaluate(`document.querySelector(${JSON.stringify(handle(c.id))}).focus()`);
  // The new order leaves within the same event: nothing waits for a pause before saving it.
  assert.equal(await evaluate(`(()=>{const real=window.fetch;let sent=0;window.fetch=(...a)=>{sent++;return real(...a)};document.querySelector(${JSON.stringify(handle(c.id))}).dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));window.fetch=real;return sent})()`),1);
  assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  assert.match(await evaluate("document.getElementById('status').textContent"),/position 1 of 2/);
  await settle(()=>requests.length===quiet+1,'keyboard order request');
  assert.deepEqual(requests.at(-1),{action:'order_update',payload:{order:[c.id,a.id,target.id,b.id]},key:requests.at(-1).key,expected_revision:revision});
  await wait(`document.activeElement?.dataset.id===${JSON.stringify(c.id)}&&document.activeElement.dataset.action==='move'`);
  await evaluate(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}))`);await new Promise(r=>setTimeout(r,500));
  assert.equal(requests.length,quiet+1);assert.equal(priorities(),storedPriorities);
  // Pointer: a real mouse drag in My order places the task first.
  const mouse=(type,point,buttons)=>rpc('Input.dispatchMouseEvent',{type,x:point.x,y:point.y,button:'left',buttons,clickCount:type==='mouseMoved'?0:1},sessionId);
  const point=(selector,edge)=>evaluate(`(async()=>{${still}const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:${edge==='top'?'r.top+3':edge==='bottom'?'r.bottom+40':edge==='below'?'r.bottom-3':'r.y+r.height/2'}}})()`);
  async function dragTo(from,to){await mouse('mouseMoved',from,0);await mouse('mousePressed',from,1);for(let i=1;i<=8;i++)await mouse('mouseMoved',{x:from.x,y:from.y+(to.y-from.y)*i/8},1);await mouse('mouseReleased',to,0)}
  await fill('order-choice','manual');assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  revision=state.revision;await dragTo(await point(handle(b.id)),await point(`.task[data-task-id="${c.id}"]`,'top'));
  assert.deepEqual(await evaluate(lane),[b.id,c.id,a.id,target.id]);
  await settle(()=>requests.length===quiet+2,'pointer order request');
  assert.deepEqual(requests.at(-1).payload,{order:[b.id,c.id,a.id,target.id]});assert.equal(requests.at(-1).expected_revision,revision);
  // In Priority a drop among other priorities takes that priority at once: a high task let go
  // below the low one becomes low (Bri: «Toma esa prioridad y aparece Deshacer»).
  await fill('order-choice','priority');assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  await dragTo(await point(handle(c.id)),await point(`.task[data-task-id="${b.id}"]`,'below'));
  assert.deepEqual(await evaluate(lane),[a.id,target.id,b.id,c.id]);
  assert.equal(await evaluate(`document.querySelector('[data-id="${c.id}"][data-action="priority"]').dataset.priority`),'low');
  await settle(()=>requests.length===quiet+3,'adopted priority request');
  assert.deepEqual([requests.at(-1).action,requests.at(-1).payload],['task_update',{uuid:c.id,priority:'low'}]);
  await wait("document.getElementById('status').textContent==='Priority: Low'&&!document.getElementById('undo').hidden");
  // One Undo reverts everything the drop changed and puts the card back at once.
  const dropped=requests.at(-1);await click('#undo');assert.deepEqual(await evaluate(lane),[c.id,a.id,target.id,b.id]);
  await settle(()=>undos.at(-1)?.proposal_ids?.length===1,'drop undo');assert.equal(c.priority,'high');await wait(`daySnapshot().revision>=${state.revision}`);
  assert.ok(dropped.key);
  assert.ok(requests.slice(quiet,quiet+2).every(r=>r.action==='order_update'));
  // The suggestion is fetched once on request and reused across view switches.
  await fill('order-choice','suggested');assert.match(await evaluate("document.getElementById('order-note').textContent"),/No suggestion yet/);
  await click('#suggest-order');await settle(()=>suggestions.length===1,'suggestion request');await refresh();
  assert.deepEqual(await evaluate(lane),[c.id,b.id,a.id,target.id]);assert.equal(await evaluate("document.querySelectorAll('#board .drag-handle').length"),0);
  assert.match((await texts('.lane[data-state="todo"] .suggestion-reason'))[0],/^1Controlled reason for /);
  const note=await evaluate("document.getElementById('order-note').textContent");assert.match(note,/Assistant suggestion from/);assert.match(note,/Controlled suggestion message\./);
  for(const view of ['priority','group','suggested','newest','suggested'])await fill('order-choice',view);
  assert.equal(suggestions.length,1);assert.equal(requests.length,quiet+3);
  state.orderSuggestion.stale=true;state.revision++;await refresh();assert.match(await evaluate("document.getElementById('order-note').textContent"),/Tasks changed since then/);
  // Localized copy; stored group labels are shown as typed.
  await fill('locale-choice','es');await fill('order-choice','group');
  assert.deepEqual((await evaluate(sections)).map(([name])=>name),['Home','office move','Sin grupo']);
  assert.deepEqual(await texts('#board .group-section .group-summary'),['0 de 1 hechas','0 de 2 hechas','0 de 1 hechas']);
  assert.equal((await texts('#board .priority-pill'))[0],'Prioridad: Alta');
  assert.equal(await evaluate("document.querySelector('#order-choice option[value=manual]').textContent"),'Mi orden');
  assert.equal(await evaluate(noteHidden),true);
  await fill('locale-choice','en');
  // Group is edited explicitly in the card editor; an empty value removes it.
  // Group is changed from its chip, also under group headings; No group removes it.
  assert.equal(await text(row('[data-action=group]')),'Group: Office move');await click(row('[data-action=group]'));
  assert.equal(await evaluate("document.querySelector('.row-menu [aria-checked=true]').dataset.value"),'Office move');
  await click('.row-menu [data-menu-act=group][data-value=""]');await serverWait(()=>target.group==='');
  assert.deepEqual(requests.at(-1).payload,{uuid:target.id,group:''});
  await click('#capture-open');await fill('capture-title','Grouped capture');await fill('capture-group','  Home ');await submit('capture-dialog');await saved('capture-dialog');
  assert.equal(requests.at(-1).payload.group,'Home');
  // Read-only profiles show no drag handles, and the narrowest layout does not overflow.
  state.readOnly=true;state.revision++;await refresh();assert.equal(await evaluate("document.querySelectorAll('.drag-handle').length"),0);
  state.readOnly=false;state.revision++;await refresh();
  await rpc('Emulation.setDeviceMetricsOverride',{width:320,height:900,deviceScaleFactor:1,mobile:true},sessionId);await new Promise(r=>setTimeout(r,100));
  for(const view of ['priority','group','suggested'])await fill('order-choice',view);
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);await fill('order-choice','priority');state.instant=false;
  console.log('Ordering regressions passed: views, keyboard and pointer order, priority sections, suggestion reuse, groups and localization');
 }

 if(group==='all'||group==='groups'){
  await close();await fill('locale-choice','en');await fill('order-choice','priority');await click('#list-view');
  Object.assign(target,{state:'active',group:'Home',due:'2025-10-30T12:00:00Z'});
  Object.assign(a,{state:'todo',group:'Home',due:'2025-10-29T12:00:00Z'});
  Object.assign(b,{state:'done',group:'Home'});Object.assign(c,{state:'todo',group:null});
  const retired={...task('Retired'),state:'retired',group:'Home'};state.tasks=[target,a,b,c,retired];state.groupColors={};state.revision++;await refresh();
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.filter-chip')].map(e=>[e.dataset.filter,e.querySelector('.filter-count').textContent])"),[['*','3'],['group:home','2'],['group:','1']]);
  const sent=requests.length;
  await click('.filter-chip[data-filter="group:home"]');
  assert.deepEqual(await evaluate("[...document.querySelectorAll('#board .task')].map(e=>e.dataset.taskId).sort()"),[target.id,a.id].sort());
  assert.equal(await evaluate("document.querySelector('.filter-chip[data-filter=\"group:home\"]').getAttribute('aria-pressed')"),'true');
  await fill('order-choice','group');
  assert.equal(await evaluate("document.querySelector('.group-summary').textContent"),'1 of 3 done · next: Oct 29, 06:00');
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.group-section:not(.filtered) .group-progress')].map(e=>[e.getAttribute('aria-valuenow'),e.getAttribute('aria-valuemax')])"),[['1','3']]);
  await click('[data-fold="home"]');assert.equal(await evaluate("document.querySelector('.group-section').classList.contains('collapsed')"),true);
  assert.equal(await evaluate("document.querySelector('.group-section .task').getClientRects().length"),0);
  await refresh();assert.equal(await evaluate("document.querySelector('[data-fold=home]').getAttribute('aria-expanded')"),'false');
  await click('[data-fold="home"]');await fill('order-choice','priority');
  assert.equal(requests.length,sent,'filtering and folding send no model or write request');
  const row=`#board [data-task-id="${a.id}"]`;state.instant=true;
  await click(row+' [data-action=group]');await click('[data-menu-act=group-color][data-value=teal]');
  assert.equal(await evaluate(`document.querySelector('${row}').style.getPropertyValue('--gc')`),'#12a594','color appears before the response');
  await serverWait(()=>state.groupColors.home==='teal');await refresh();
  assert.deepEqual(requests.at(-1).payload,{group:'Home',color:'teal'});
  assert.equal(await evaluate(`document.querySelector('#board [data-task-id="${target.id}"]').style.getPropertyValue('--gc')`),'#12a594');
  await wait("document.getElementById('status').textContent==='Group color changed'");
  // A later color on a different group survives this color's optimistic Undo.
  state.groupColors.office='pink';state.revision++;await refresh();
  await click('#undo');assert.equal(await evaluate("daySnapshot().groupColors.home"),undefined);
  assert.equal(await evaluate("daySnapshot().groupColors.office"),'pink');
  await serverWait(()=>!state.groupColors.home);await refresh();state.instant=false;
  await fill('locale-choice','es');await fill('order-choice','group');assert.match(await evaluate("document.querySelector('.group-summary').textContent"),/^1 de 3 hechas · próxima:/);
  await click('.filter-chip[data-filter="group:"]');assert.equal(await evaluate("document.querySelector('.group-section:not(.filtered) h2').textContent"),'Sin grupo');
  await click('.filter-chip[data-filter="*"]');await fill('locale-choice','en');await fill('order-choice','priority');
  // A closed task no longer holds an active filter that would hide everything else.
  await click('.filter-chip[data-filter="group:home"]');target.state='done';a.state='done';state.revision++;await refresh();
  assert.equal(await evaluate("document.getElementById('group-filters').hidden"),true);
  assert.equal(await evaluate("document.querySelectorAll('#board .task').length"),1);
  c.group='*';state.revision++;await refresh();await click('.filter-chip[data-filter="group:*"]');
  assert.equal(await evaluate("document.querySelectorAll('#board .task').length"),1,'a group named * is distinct from All');
  await click('.filter-chip[data-filter="*"]');
  // A realistic board: measure click through the completed DOM/layout, independent of network latency.
  state.tasks=[target,a,b,c,...Array.from({length:288},(_,i)=>({...task('Load '+i),group:i%2?'Home':'Work'}))];state.revision++;await refresh();
  await fill('order-choice','group');
  const latency=await evaluate(`(()=>{const result={tasks:daySnapshot().tasks.length,filter:[],fold:[]};
   for(let i=0;i<10;i++){const start=performance.now();document.querySelector('.filter-chip[data-filter="'+(i%2?'*':'group:home')+'"]').click();document.getElementById('board').getBoundingClientRect();result.filter.push(performance.now()-start)}
   for(let i=0;i<10;i++){const start=performance.now();document.querySelector('[data-fold=home]').click();document.getElementById('board').getBoundingClientRect();result.fold.push(performance.now()-start)}return result})()`);
  console.log('Groups 292-task DOM/layout latency (ms): '+JSON.stringify(latency));
  assert.ok(Math.max(...latency.filter,...latency.fold)<300,'group filtering and folding stay within 300 ms');
  state.tasks=[target,a,b,c];console.log('Groups regressions passed: one-click counts and filters, progress excludes retired, earliest deadline, persistent folding, instant shared color, both locales and exhausted filter');
 }

 if(group==='all'||group==='instant'){
  // Direct actions show at once with their time and Undo; the assistant's later look leaves a mark.
  await fill('locale-choice','en');await close();Object.assign(state,{instant:true,proposals:[],messages:[],review:{pending:0,running:false,status:'completed',finishedAt:state.now}});
  for(const t of [a,b,c])Object.assign(t,{state:'todo',dependsOn:[],blockedBy:[],blocked:false});state.revision++;await refresh();
  const toast=()=>evaluate("({show:document.getElementById('toast').classList.contains('show'),text:document.getElementById('status').textContent,detail:document.getElementById('toast-detail').textContent,undo:!document.getElementById('undo').hidden})");
  const lane=id=>evaluate(`document.querySelector('#board [data-task-id="${id}"]')?.closest('.lane')?.dataset.state`);
  // The change leaves before the page redraws it: the request starts with the card still in its old lane.
  await evaluate(`(()=>{const real=window.fetch;window.fetch=(url,init)=>{if(String(url).endsWith('/api/proposals')){globalThis.laneWhenSent=document.querySelector('#board [data-task-id="${a.id}"]').closest('.lane').dataset.state;window.fetch=real}return real(url,init)}})()`);
  await click(`[data-id="${a.id}"][data-action="activate"]`);await wait(`document.querySelector('#board [data-task-id="${a.id}"]').closest('.lane').dataset.state==='active'`);
  assert.equal(await evaluate('globalThis.laneWhenSent'),'todo');
  await wait("document.getElementById('status').textContent==='Start · A'");
  let shown=await toast();assert.equal(shown.show,true);assert.equal(shown.text,'Start · A');assert.match(shown.detail,/^\d+ ms · the assistant looks at it in the background$/);assert.equal(shown.undo,true);
  assert.equal(requests.at(-1).action,'front_activate');
  await click('#undo');await wait(`document.querySelector('#board [data-task-id="${a.id}"]').closest('.lane').dataset.state==='todo'`);
  await wait("document.getElementById('status').textContent==='Change undone'");
  assert.equal(undos.at(-1).fact_id,String(facts-1));shown=await toast();assert.equal(shown.undo,false);
  // A calendar addition shows before the answer; the next read holds what the service saved.
  await evaluate("(()=>{dayUI.flow.preview({action:'event_add',payload:{title:'New commitment',start:'2099-01-01T10:00:00Z',end:'2099-01-01T11:00:00Z'}});return true})()");
  assert.match(await evaluate("document.getElementById('calendar-area').textContent"),/New commitment/);
  await evaluate("(()=>{dayUI.flow.tag='';return dayUI.flow.refresh().then(()=>true)})()");
  assert.doesNotMatch(await evaluate("document.getElementById('calendar-area').textContent"),/New commitment/);
  assert.match(await evaluate("document.getElementById('review-strip').textContent"),/^Everything is saved · reviewed .+\d\d:\d\d$/);
  assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'Up to date');
  state.review={pending:2,running:false};await refresh();
  assert.equal(await evaluate("document.getElementById('review-strip').textContent"),'Saved. I look at it when you pause (2).');
  assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'2 changes to review');
  state.review={pending:2,running:true};await refresh();assert.equal(await evaluate("document.getElementById('agent-state').textContent"),'Reviewing your changes…');
  state.review={pending:0,running:false,status:'completed',finishedAt:state.now};
  // A suggestion marks its card without moving it; the bubble opens only on request.
  const box=()=>evaluate(`(async()=>{${still}const card=document.querySelector('#board [data-task-id="${b.id}"]'),r=card.getBoundingClientRect(),t=card.querySelector('.task-title').getBoundingClientRect();return [r.top,r.height,t.left,t.top]})()`);
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
  // Canceling a commitment leaves the agenda at once and offers Undo, which brings the same one back at once.
  const soon=hours=>new Date(Date.parse(state.now)+hours*3600000).toISOString(),dentist={uuid:randomUUID(),title:'Dentist',start_at:soon(24),end_at:soon(25)};
  state.calendar_events=[dentist];state.revision++;await refresh();
  const agenda="[...document.querySelectorAll('#calendar-area .daily-row .link-button')].map(b=>b.textContent)";
  // A read can be dropped while the previous block's sends settle; the next one shows the commitment.
  await wait(`${agenda}.length===1`);assert.deepEqual(await evaluate(agenda),['Dentist']);
  let releaseCancel;const cancelGate=new Promise(r=>releaseCancel=r);
  server.removeAllListeners('request');server.on('request',async(req,res)=>{if(req.url==='/api/proposals')await cancelGate;return original(req,res)});
  await click('#calendar-area .daily-row .btn');await wait(`${agenda}.length===0`);assert.equal(state.calendar_events.length,1);
  releaseCancel();await wait("document.getElementById('status').textContent==='Commitment canceled · Dentist'&&!document.getElementById('undo').hidden");
  assert.deepEqual([requests.at(-1).action,requests.at(-1).payload],['event_cancel',{uuid:dentist.uuid}]);assert.equal(state.calendar_events.length,0);
  assert.match((await toast()).detail,/^\d+ ms/);
  const cancelFact=facts;let releaseRestore;const restoreGate=new Promise(r=>releaseRestore=r);
  server.removeAllListeners('request');server.on('request',async(req,res)=>{if(req.url==='/api/undo')await restoreGate;return original(req,res)});
  await click('#undo');await wait(`${agenda}.length===1`);assert.deepEqual(await evaluate(agenda),['Dentist']);assert.equal(state.calendar_events.length,0);
  releaseRestore();await wait("document.getElementById('status').textContent==='Change undone'");
  assert.equal(undos.at(-1).fact_id,String(cancelFact));assert.deepEqual(state.calendar_events.map(e=>e.uuid),[dentist.uuid]);
  assert.equal((await toast()).undo,false);assert.deepEqual(await evaluate(agenda),['Dentist']);
  await fill('locale-choice','es');await refresh();await click('#calendar-area .daily-row .btn');
  await wait("document.getElementById('status').textContent==='Compromiso cancelado · Dentist'&&!document.getElementById('undo').hidden");
  await click('#undo');await wait("document.getElementById('status').textContent==='Cambio deshecho'");assert.deepEqual(state.calendar_events.map(e=>e.uuid),[dentist.uuid]);
  await fill('locale-choice','en');server.removeAllListeners('request');server.on('request',original);state.calendar_events=[];state.revision++;await refresh();
  state.instant=false;
  console.log('Instant action regressions passed: change and Undo shown before the answer, sequential revisions, toast timing, review state, suggestion mark, bubble, accept, dismiss and log, commitment cancel and its Undo');
 }
 if(group==='all'||group==='compact'){
  // Compact rows: many tasks per screen, every row action one click away and priority two.
  const size=(width,height,mobile=false)=>rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile},sessionId);
  const longTitle='Send the quarterly sales report to management with the changes Laura asked for on Tuesday and the October figures';
  const extra=Array.from({length:16},(_,i)=>({...task(i===3?longTitle:'Row '+(i+1)),priority:['high','medium','low'][i%3],group:i%2?'Home':null,due:i%4?null:'2025-10-29T18:00:00Z'}));
  const kept=state.tasks;await close();await fill('locale-choice','en');await fill('order-choice','priority');
  for(const t of [target,a,b,c])Object.assign(t,{state:'todo',dependsOn:[],blockedBy:[],blocked:false,group:null,priority:'medium'});
  Object.assign(state,{tasks:[...kept,...extra],readOnly:false,proposals:[],suggestions:[],messages:[],calendar_events:[],orderSuggestion:null,taskOrder:[]});state.revision++;
  const fully="[...document.querySelectorAll('#board .task')].filter(c=>{const r=c.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight}).length";
  await size(1440,900);await refresh();
  if(await evaluate("document.querySelector('#chat-toggle').getAttribute('aria-expanded')==='false'"))await click('#chat-toggle');
  assert.ok(await evaluate("document.getElementById('workspace').classList.contains('chat-open')"),'chat stays open beside the list');
  const desktopRows=await evaluate(fully);assert.ok(desktopRows>=12,'1440x900 shows '+desktopRows+' rows');
  // A long title is complete, never clipped, and takes at most two lines on a wide screen.
  const titleBox=id=>evaluate(`(()=>{const e=document.querySelector('[data-id="${id}"][data-action="title"]'),s=getComputedStyle(e),line=parseFloat(s.lineHeight);return {text:e.textContent,lines:Math.round(e.getBoundingClientRect().height/line),clipped:e.scrollWidth>e.clientWidth+1||s.textOverflow==='ellipsis'||s.webkitLineClamp!=='none'}})()`);
  assert.deepEqual(await titleBox(extra[3].id),{text:longTitle,lines:2,clipped:false});
  // Finish, start and pause are one click each and show before the answer.
  const row=extra[1],sel=action=>`[data-id="${row.id}"][data-action="${action}"]`,laneOf=id=>evaluate(`document.querySelector('#board [data-task-id="${id}"]')?.closest('.lane')?.dataset.state??null`);
  let sent=requests.length;await click(sel('activate'));assert.equal(await laneOf(row.id),'active');await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).action,requests.at(-1).payload],[1,'front_activate',{uuid:row.id}]);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(sel('todo'))}).getAttribute('aria-label')`),'Pause: '+row.title);
  sent=requests.length;await click(sel('todo'));assert.equal(await laneOf(row.id),'todo');await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).action],[1,'front_todo']);
  // Quick consecutive clicks: the first click's slower state read never puts its state back over the second.
  stateDelay=250;await click(sel('activate'));await new Promise(r=>setTimeout(r,60));
  assert.equal(await laneOf(row.id),'active','the started task stays started while its read is slow');await click(sel('todo'));const seen=[];for(let i=0;i<24;i++){seen.push((await laneOf(row.id))+'/'+await evaluate(`daySnapshot().tasks.find(t=>t.id===${JSON.stringify(row.id)}).state`));await new Promise(r=>setTimeout(r,25))}
  stateDelay=0;assert.ok(seen.every(s=>s==='todo/todo'),'the paused task stays paused: '+seen.join());
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);assert.equal(row.state,'todo');
  // Priority: the chip opens a one-level menu and the choice applies at once.
  sent=requests.length;await click(sel('priority'));
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].map(e=>e.textContent+':'+e.getAttribute('aria-checked'))"),['Urgent:false','High:false','Medium:true','Low:false']);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(sel('priority'))}).getAttribute('aria-expanded')`),'true');
  assert.equal(await evaluate("document.activeElement.closest('.row-menu')!==null"),true,'focus moves into the menu');
  await click('.row-menu [data-value="high"]');assert.equal(await evaluate(`document.querySelector(${JSON.stringify(sel('priority'))}).dataset.priority`),'high');
  assert.equal(await evaluate("document.querySelector('.row-menu')"),null);await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).action,requests.at(-1).payload],[1,'task_update',{uuid:row.id,priority:'high'}]);
  sent=requests.length;await click(sel('priority'));await click('.row-menu [data-value="high"]');assert.equal(requests.length,sent,'choosing the current priority sends nothing');
  // Urgent, the fourth level, applies the same way and leads the Priority order; back to High afterwards.
  sent=requests.length;await click(sel('priority'));await click('.row-menu [data-value="urgent"]');await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).payload],[1,{uuid:row.id,priority:'urgent'}]);
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(sel('priority'))}).textContent`),'Priority: Urgent');
  assert.equal(await evaluate(`document.querySelector('#board .lane[data-state="todo"] .task').dataset.taskId`),row.id,'urgent is first in the Priority order');
  sent=requests.length;await click(sel('priority'));await click('.row-menu [data-value="high"]');
  for(let i=0;i<100&&requests.length===sent;i++)await new Promise(r=>setTimeout(r,15));
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading&&!dayUI.flow.busy&&daySnapshot().tasks.find(t=>t.id===${JSON.stringify(row.id)}).priority==='high'`);await refresh();
  // Escape closes a menu and returns focus to its chip; arrows move inside it.
  await click(sel('priority'));await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}))");
  assert.equal(await evaluate("document.activeElement.dataset.value"),'medium','focus starts on the current level, High');
  await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  assert.equal(await evaluate(`document.querySelector('.row-menu')===null&&document.activeElement===document.querySelector(${JSON.stringify(sel('priority'))})`),true);
  // A right click opens the more menu where the pointer is; Retire asks once, Cancel keeps the task.
  const other=extra[4],point=await evaluate(`(()=>{const r=document.querySelector('[data-task-id="${other.id}"] .row-meta, [data-task-id="${other.id}"] .row-main').getBoundingClientRect();return {x:Math.round(r.right-4),y:Math.round(r.top+r.height/2)}})()`);
  await rpc('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'right',buttons:2,clickCount:1},sessionId);await rpc('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'right',buttons:0,clickCount:1},sessionId);
  await wait("document.querySelector('.row-menu')!==null");
  // It opens at the pointer, or ends there when it would not fit below.
  const placed=await evaluate("(()=>{const m=document.querySelector('.row-menu');return [parseFloat(m.style.left),parseFloat(m.style.top),m.offsetHeight]})()");
  assert.equal(placed[0],point.x);assert.ok(placed[1]===point.y||placed[1]+placed[2]===point.y,JSON.stringify([placed,point]));
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].map(e=>e.textContent)"),['Edit title','More details','Change deadline','Move down','Duplicate','Retire…']);
  sent=requests.length;await click('.row-menu [data-menu-act="retire"]');assert.match(await evaluate("document.querySelector('.row-menu').textContent"),/Retire this responsibility\?/);
  await click('.row-menu [data-menu-act="keep"]');assert.equal(await evaluate("document.querySelector('.row-menu')"),null);assert.equal(requests.length,sent);
  await click(`[data-id="${other.id}"][data-action="more"]`);await click('.row-menu [data-menu-act="retire"]');await click('.row-menu [data-menu-act="retire-yes"]');
  assert.equal(await laneOf(other.id),null);await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).action,requests.at(-1).payload],[1,'front_retire',{uuid:other.id}]);
  // Move down in the menu keeps the same section rules as dragging.
  await fill('order-choice','manual');const ids="[...document.querySelectorAll('.lane[data-state=\"todo\"] .task')].map(e=>e.dataset.taskId)";const order=await evaluate(ids);
  await click(`[data-id="${order[0]}"][data-action="more"]`);assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].map(e=>e.textContent)"),['Edit title','More details',state.tasks.find(t=>t.id===order[0]).due?'Change deadline':'Set deadline','Move down','Duplicate','Retire…']);
  await click('.row-menu [data-menu-act="move"]');assert.deepEqual(await evaluate(ids),[order[1],order[0],...order.slice(2)]);
  await wait(`${JSON.stringify(JSON.stringify(requests.at(-1)?.payload))}!==undefined&&true`);
  for(let i=0;i<100&&requests.at(-1).action!=='order_update';i++)await new Promise(r=>setTimeout(r,15));assert.equal(requests.at(-1).action,'order_update');
  await fill('order-choice','priority');
  // Finish is the checkbox, one click.
  sent=requests.length;await click(`[data-id="${row.id}"][data-action="done"]`);assert.equal(await laneOf(row.id),null);
  // The finish waits its turn behind the order save still in flight; it is shown at once, sent right after.
  for(let i=0;i<100&&requests.length===sent;i++)await new Promise(r=>setTimeout(r,15));await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).action,requests.at(-1).payload],[1,'task_done',{uuid:row.id}]);
  // The Board uses the same row pieces as small cards in three columns.
  await click('#board-view');assert.deepEqual(await evaluate("(()=>{const lanes=[...document.querySelectorAll('#board .lane')].map(l=>Math.round(l.getBoundingClientRect().top));const card=document.querySelector('.lane[data-state=todo] .task');return [new Set(lanes).size,['done','priority','activate','snooze','more'].every(a=>card.querySelector('[data-action='+a+']'))]})()"),[1,true]);
  // Urgente, the widest label, takes no more room on a card than the others, so it never wraps where they fit.
  await fill('locale-choice','es');const widest=state.tasks.find(t=>t.state==='todo');Object.assign(widest,{priority:'urgent'});state.revision++;await refresh();
  const pills=await evaluate("JSON.stringify([...document.querySelectorAll('#board .priority-pill[data-action=priority]')].map(p=>[p.dataset.priority,p.getBoundingClientRect().width]))");
  const widths=JSON.parse(pills),urgent=widths.find(([p])=>p==='urgent')[1];
  assert.ok(widths.every(([,w])=>urgent<=w+0.5),'Urgente is as wide as the other chips: '+pills);
  Object.assign(widest,{priority:'medium'});state.revision++;await fill('locale-choice','en');await refresh();
  await click('#list-view');
  // Phone: at least seven rows, 34 px targets, ⋯ through a long press, and no overflow.
  await size(390,844,true);await rpc('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1},sessionId);await new Promise(r=>setTimeout(r,100));
  if(await evaluate("document.getElementById('chat-dialog').open"))await click('#chat-close');
  const phoneRows=await evaluate(fully);assert.ok(phoneRows>=7,'390x844 shows '+phoneRows+' rows');
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth'),true);
  const first=extra[0].id,targets=await evaluate(`['done','priority','activate','snooze'].map(a=>{const r=document.querySelector('[data-id="${first}"][data-action="'+a+'"]').getBoundingClientRect();return a+':'+(Math.min(r.width,r.height)>=34)})`);
  assert.deepEqual(targets,['done:true','priority:true','activate:true','snooze:true']);
  assert.equal(await evaluate(`document.querySelector('[data-id="${first}"][data-action="more"]').getBoundingClientRect().width<=1`),true,'⋯ is out of sight on a phone');
  const titleCenter=await evaluate(`(()=>{const r=document.querySelector('[data-id="${first}"][data-action="title"]').getBoundingClientRect();return {x:Math.round(r.left+20),y:Math.round(r.top+r.height/2)}})()`);
  sent=requests.length;await rpc('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[titleCenter]},sessionId);await new Promise(r=>setTimeout(r,650));
  await rpc('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]},sessionId);await new Promise(r=>setTimeout(r,100));
  assert.equal(await evaluate("document.querySelector('.row-menu.sheet')!==null&&document.querySelector('.menu-scrim')!==null"),true,'a long press opens the sheet');
  assert.equal(await evaluate("document.querySelector('.title-edit,.expand')"),null,'the tap that ends a long press does nothing else');
  assert.ok(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].every(e=>e.getBoundingClientRect().height>=44)"));
  await click('.menu-scrim');assert.equal(await evaluate("document.querySelector('.row-menu')"),null);assert.equal(requests.length,sent);
  await rpc('Emulation.setTouchEmulationEnabled',{enabled:false},sessionId);
  // Localized controls.
  await fill('locale-choice','es');await size(1440,900);
  assert.deepEqual(await evaluate(`['done','priority','activate','snooze','more'].map(a=>document.querySelector('[data-id="${first}"][data-action="'+a+'"]').title)`),['Terminar','Cambiar prioridad','Iniciar','Posponer','Más acciones (también clic derecho o presión larga)']);
  await fill('locale-choice','en');await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);
  state.tasks=kept;state.revision++;await refresh();
  console.log('Compact row regressions passed: rows per screen, full long titles, one-click finish/start/pause, two-click priority, menus, Retire confirmation, board cards, phone targets and long press');
 }
 if(group==='all'||group==='snooze'){
  // Snooze: the clock opens one menu of presets, each showing the exact time it saves in the
  // profile's zone; one more click saves that time at once, with no dialog.
  const size=(width,height,mobile=false)=>rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile},sessionId);
  const kept={tasks:state.tasks,timezone:state.timezone,now:state.now},plain=task('Plain'),due=task('Due soon'),later=task('Snoozed already');
  Object.assign(due,{due:'2025-10-29T20:00:00Z'});Object.assign(later,{state:'snoozed',until:'2025-10-30T15:00:00Z'});
  await close();await fill('locale-choice','en');await fill('order-choice','priority');await size(1440,900);
  Object.assign(state,{tasks:[plain,due,later],instant:true,readOnly:false,proposals:[],suggestions:[],messages:[],calendar_events:[],orderSuggestion:null,taskOrder:[],timezone:'America/Edmonton',now:'2025-10-28T12:00:20Z'});
  state.profile.timezone=state.timezone;state.revision++;await refresh();
  const clock=id=>`[data-id="${id}"][data-action="snooze"]`,laneOf=id=>`document.querySelector('#board [data-task-id="${id}"]')?.closest('.lane')?.dataset.state??null`;
  const items="[...document.querySelectorAll('.row-menu .menu-item')].map(e=>[e.querySelector('.menu-label').firstChild.textContent,e.querySelector('.menu-hint')?.textContent??'',e.dataset.value])";
  await click(clock(plain.id));
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(clock(plain.id))}).getAttribute('aria-expanded')`),'true');
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-title').textContent"),'Snooze until');
  assert.equal(await evaluate("document.activeElement.closest('.row-menu')!==null"),true,'focus moves into the menu');
  // 06:00:20 in Edmonton: hours round up to the minute; days land at 9:00 local, also after the DST change on Nov 2.
  assert.deepEqual(await evaluate(items),[
   ['In 1 hour','07:01','2025-10-28T13:01:00.000Z'],['In 3 hours','09:01','2025-10-28T15:01:00.000Z'],['Tomorrow','09:00','2025-10-29T15:00:00.000Z'],
   ['In 2 days','Thu, Oct 30, 09:00','2025-10-30T15:00:00.000Z'],['In 1 week','Tue, Nov 4, 09:00','2025-11-04T16:00:00.000Z']]);
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-note').textContent"),'Times in America/Edmonton','a browser in another zone is told which zone the times use');
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-item').title"),await evaluate("dayUI.full('2025-10-28T13:01:00.000Z')"));
  assert.equal(await evaluate("document.querySelectorAll('.row-menu .menu-warn').length"),0);
  // The second click applies before the service answers: the row is in Snoozed in the same task.
  let sent=requests.length;
  const applied=await evaluate(`(()=>{const s=performance.now();document.querySelector('.row-menu [data-value="2025-10-30T15:00:00.000Z"]').click();return {lane:${laneOf(plain.id)},ms:performance.now()-s,menu:!!document.querySelector('.row-menu'),dialog:!!document.querySelector('dialog[open]')}})()`);
  assert.deepEqual({...applied,ms:undefined},{lane:'snoozed',ms:undefined,menu:false,dialog:false});assert.ok(applied.ms<100,'snooze shown in '+applied.ms+' ms');
  await wait(`daySnapshot().revision>=${state.revision}`);
  assert.deepEqual([requests.length-sent,requests.at(-1).action,requests.at(-1).payload],[1,'task_snooze',{uuid:plain.id,until:'2025-10-30T15:00:00.000Z'}]);
  assert.equal(plain.until,'2025-10-30T15:00:00.000Z');
  await wait("document.getElementById('status').textContent==='Snoozed until Oct 30, 09:00 · Plain'");assert.equal(await evaluate("!document.getElementById('undo').hidden"),true);
  assert.match(await evaluate(`document.querySelector('#board [data-task-id="${plain.id}"] .row-meta').textContent`),/Oct 30, 09:00/);
  // Presets past the deadline say so; the earlier ones do not.
  await click(clock(due.id));
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].map(e=>e.querySelector('.menu-warn')?.textContent??'')"),['','','','after the deadline','after the deadline']);
  // Escape closes it and returns focus to the clock; a second click on the clock closes it too.
  await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  assert.equal(await evaluate(`document.querySelector('.row-menu')===null&&document.activeElement===document.querySelector(${JSON.stringify(clock(due.id))})`),true);
  await click(clock(due.id));await click(clock(due.id));assert.equal(await evaluate("document.querySelector('.row-menu')"),null);
  // A snoozed task can come back at once from the same menu.
  await click(clock(later.id));assert.deepEqual(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].map(e=>e.textContent).at(-1)"),'Resume now');
  sent=requests.length;await click('.row-menu [data-menu-act="todo"]');assert.equal(await evaluate(laneOf(later.id)),'todo');
  await wait(`daySnapshot().revision>=${state.revision}`);assert.deepEqual([requests.length-sent,requests.at(-1).action,requests.at(-1).payload],[1,'front_todo',{uuid:later.id}]);
  // Spanish copy.
  await fill('locale-choice','es');await click(clock(due.id));
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-title').textContent"),'Posponer hasta');
  assert.deepEqual(await evaluate(items+".map(i=>i[0])"),['En 1 hora','En 3 horas','Mañana','En 2 días','En 1 semana']);
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-warn').textContent"),'después del vencimiento');
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-note').textContent"),'Horas en America/Edmonton');
  assert.equal(await evaluate(`document.querySelector(${JSON.stringify(clock(due.id))}).title`),'Posponer');
  await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");await fill('locale-choice','en');
  // When the browser shares the profile's zone, the note is left out.
  state.timezone='UTC';state.profile.timezone='UTC';state.revision++;await refresh();await click(clock(due.id));
  assert.equal(await evaluate("document.querySelector('.row-menu .menu-note')"),null);
  assert.deepEqual((await evaluate(items))[2],['Tomorrow','09:00','2025-10-29T09:00:00.000Z']);
  await evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  // Phone: the same menu as a bottom sheet with full touch targets and no overflow.
  await size(390,844,true);await new Promise(r=>setTimeout(r,100));if(await evaluate("document.getElementById('chat-dialog').open"))await click('#chat-close');
  await click(clock(due.id));
  assert.equal(await evaluate("document.querySelector('.row-menu.sheet')!==null&&document.querySelector('.menu-scrim')!==null"),true);
  assert.ok(await evaluate("[...document.querySelectorAll('.row-menu .menu-item')].every(e=>e.getBoundingClientRect().height>=44)"));
  assert.equal(await evaluate('document.documentElement.scrollWidth<=innerWidth&&[...document.querySelectorAll(".row-menu .menu-item")].every(e=>e.scrollWidth<=e.clientWidth)'),true);
  sent=requests.length;await click('.menu-scrim');assert.equal(await evaluate("document.querySelector('.row-menu')"),null);assert.equal(requests.length,sent);
  await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);
  Object.assign(state,{tasks:kept.tasks,timezone:kept.timezone,now:kept.now,instant:false});state.profile.timezone=kept.timezone;state.revision++;await refresh();
  console.log('Snooze regressions passed: one-click presets with the saved time in the profile zone, DST, deadline warning, instant apply, Resume now, Spanish copy and phone sheet');
 }

 if(group==='all'||group==='drag'){
  // Drag and transitions: the card lifts, the others make room, it lands without a jump, and the
  // drop's meaning (column, priority, group, place) applies at once with one Undo.
  const size=(width,height,mobile=false)=>rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile},sessionId);
  const kept=state.tasks;await close();await fill('locale-choice','en');
  const rows=Array.from({length:6},(_,i)=>({...task('Drag '+(i+1)),priority:'medium',group:i<3?'Home':null,createdAt:'2025-10-2'+i+'T00:00:00Z'}));
  const [r1,r2,r3,r4,r5,r6]=rows;const blocker=task('Prerequisite');r6.blocked=true;r6.blockedBy=[blocker.id];r6.dependsOn=[blocker.id];
  Object.assign(state,{tasks:[...rows,blocker],instant:true,readOnly:false,proposals:[],suggestions:[],messages:[],calendar_events:[],orderSuggestion:null,taskOrder:[...rows.map(r=>r.id),blocker.id]});state.revision++;
  await size(1440,1000);await fill('order-choice','manual');await refresh();await click('#board-view');
  const laneOf=id=>evaluate(`document.querySelector('#board [data-task-id="${id}"]')?.closest('.lane')?.dataset.state??null`);
  const ids=lane=>evaluate(`[...document.querySelectorAll('#board .lane[data-state="${lane}"] .task')].map(e=>e.dataset.taskId)`);
  const center=selector=>evaluate(`(async()=>{${still}const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}})()`);
  const mouse=(type,p,buttons)=>rpc('Input.dispatchMouseEvent',{type,x:p.x,y:p.y,button:'left',buttons,clickCount:type==='mouseMoved'?0:1},sessionId);
  async function drag(from,to,{release=true,steps=10}={}){await mouse('mouseMoved',from,0);await mouse('mousePressed',from,1);for(let i=1;i<=steps;i++){await mouse('mouseMoved',{x:from.x+(to.x-from.x)*i/steps,y:from.y+(to.y-from.y)*i/steps},1);await new Promise(r=>setTimeout(r,16))}if(release)await mouse('mouseReleased',to,0)}
  const grab=id=>evaluate(`(async()=>{${still}const r=document.querySelector('#board [data-task-id="${id}"] .row-meta, #board [data-task-id="${id}"] .row-text').getBoundingClientRect();return {x:Math.round(r.right-6),y:Math.round(r.top+r.height/2)}})()`);
  const columns=await evaluate("new Set([...document.querySelectorAll('#board .lane')].map(l=>Math.round(l.getBoundingClientRect().top))).size");assert.equal(columns,1,'the Board shows three columns');
  // The board is updated in place: an unchanged card keeps its element when another one changes.
  await evaluate(`document.querySelector('#board [data-task-id="${r2.id}"]').dataset.probe='kept'`);
  r1.title='Drag 1 renamed';state.revision++;await refresh();
  assert.equal(await evaluate(`document.querySelector('#board [data-task-id="${r2.id}"]').dataset.probe`),'kept');
  assert.match(await evaluate(`document.querySelector('#board [data-task-id="${r1.id}"] .task-title').textContent`),/renamed/);
  // A mouse drags from an empty part of the row into another column: it lifts, the column shows
  // where it lands, and it lands exactly on its place.
  let sent=requests.length;const into=await center('#board .lane[data-state="active"] .lane-cards');
  await drag(await grab(r3.id),into,{release:false});
  assert.equal(await evaluate("document.querySelector('#motion-layer .drag-ghost')!==null&&document.querySelector('#board .task.placeholder')!==null"),true,'a lifted copy follows the pointer over a slot');
  assert.equal(await evaluate("document.querySelector('#board .lane[data-state=active]').classList.contains('drop-target')"),true);
  assert.equal(await laneOf(r3.id),'active','the slot moved into the column under the pointer');
  assert.match(await evaluate("document.querySelector('#motion-layer .drag-ghost').style.transform"),/scale\(1\.02\)/);
  await mouse('mouseReleased',into,0);
  const landing=await evaluate(`(()=>{const g=document.querySelector('#motion-layer .drag-ghost'),end=g.getAnimations()[0].effect.getKeyframes().at(-1).transform.match(/-?[\d.]+/g).map(Number),layer=document.getElementById('motion-layer').getBoundingClientRect(),card=document.querySelector('#board [data-task-id="${r3.id}"]');card.getAnimations().forEach(a=>a.finish());const r=card.getBoundingClientRect();return [Math.abs(end[0]-(r.left-layer.left)),Math.abs(end[1]-(r.top-layer.top))]})()`);
  assert.ok(landing.every(d=>d<1.5),'the copy lands exactly on the card: '+landing);
  await wait("document.querySelector('#motion-layer .drag-ghost')===null&&document.querySelector('#board .placeholder')===null");
  // An empty column has no neighbours to keep a place next to, so only the state changes.
  for(let i=0;i<100&&requests.length<sent+1;i++)await new Promise(r=>setTimeout(r,15));await new Promise(r=>setTimeout(r,400));
  assert.deepEqual(requests.slice(sent).map(r=>[r.action,r.payload.uuid??null]),[['front_activate',r3.id]]);
  assert.equal(r3.state,'active');await wait("document.getElementById('status').textContent==='Moved to Active'&&!document.getElementById('undo').hidden");
  // One Undo puts it back in its column and its place.
  await click('#undo');assert.equal(await laneOf(r3.id),'todo');
  for(let i=0;i<100&&r3.state!=='todo';i++)await new Promise(r=>setTimeout(r,15));assert.equal(r3.state,'todo');assert.equal(undos.at(-1).proposal_ids.length,1);
  await wait(`daySnapshot().revision>=${state.revision}`);
  // The Snoozed column snoozes until tomorrow at 9:00 in the profile's zone.
  sent=requests.length;await drag(await grab(r4.id),await center('#board .lane[data-state="snoozed"] .lane-cards'));
  for(let i=0;i<100&&requests.length<sent+2;i++)await new Promise(r=>setTimeout(r,15));
  assert.deepEqual([requests[sent]?.action,requests[sent]?.payload],['task_snooze',{uuid:r4.id,until:'2025-10-29T15:00:00.000Z'}],JSON.stringify(requests.slice(sent-2)));
  await wait("document.getElementById('status').textContent.startsWith('Snoozed until ')");
  // A task waiting for prerequisites cannot be started by a drop: it returns and nothing is sent.
  sent=requests.length;await drag(await grab(r6.id),await center('#board .lane[data-state="active"] .lane-cards'));
  assert.equal(await laneOf(r6.id),'todo');await new Promise(r=>setTimeout(r,450));assert.equal(requests.length,sent);
  assert.equal(await evaluate("document.getElementById('status').textContent"),'It waits for its prerequisites, so it stays where it was.');
  // Escape cancels a drag: the card returns and nothing is sent.
  await drag(await grab(r5.id),await center('#board .lane[data-state="active"] .lane-cards'),{release:false});
  await evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))");
  assert.equal(await laneOf(r5.id),'todo');await mouse('mouseReleased',{x:5,y:5},0);await new Promise(r=>setTimeout(r,450));assert.equal(requests.length,sent);
  assert.equal(await evaluate("document.querySelector('#motion-layer .drag-ghost')"),null);
  // In Group order a drop into another group's section takes that group; into No group it has none.
  await click('#list-view');await fill('order-choice','group');await wait(`daySnapshot().revision>=${state.revision}`);
  sent=requests.length;const noGroup=await center('#board .group-section[data-group=""] .lane-heading');
  await drag(await grab(r1.id),{x:noGroup.x,y:noGroup.y+30});
  for(let i=0;i<100&&requests.length<sent+1;i++)await new Promise(r=>setTimeout(r,15));
  // The new place travels with the change: one request, one write, one Undo.
  const moved=request=>{const {order,...changed}=request.payload;return [request.action,changed]};
  assert.deepEqual(moved(requests[sent]),['task_update',{uuid:r1.id,group:''}]);assert.equal(r1.group,'');assert.deepEqual(state.taskOrder,requests[sent].payload.order);
  await wait("document.getElementById('status').textContent.startsWith('No group')");assert.equal(requests.length,sent+1);
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);
  sent=requests.length;const home=await center('#board .group-section[data-group="Home"] .lane-heading');
  await drag(await grab(r5.id),{x:home.x,y:home.y+30});
  for(let i=0;i<100&&requests.length<sent+1;i++)await new Promise(r=>setTimeout(r,15));
  assert.deepEqual(moved(requests[sent]),['task_update',{uuid:r5.id,group:'Home'}]);
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);
  // A folded section accepts a drop, keeps its tasks hidden, and leaves no lifted copy behind.
  await click('[data-fold="home"]');sent=requests.length;
  const foldedHome=await center('#board .group-section[data-group="Home"] .lane-heading');
  await drag(await grab(r1.id),foldedHome);
  await serverWait(()=>r1.group==='Home');
  assert.deepEqual(moved(requests[sent]),['task_update',{uuid:r1.id,group:'Home'}]);
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);
  assert.equal(await evaluate(`document.querySelector('#board [data-task-id="${r1.id}"]').getClientRects().length`),0);
  await wait("document.querySelector('#motion-layer .drag-ghost')===null");
  await click('[data-fold="home"]');await fill('order-choice','manual');
  // Transitions: starting a task glides it into its column; finishing one fades a copy out over its
  // old place while the others close the gap; nothing is left behind.
  await click('#board-view');
  await click(`[data-id="${r2.id}"][data-action="activate"]`);
  assert.ok(await evaluate(`document.querySelector('#board [data-task-id="${r2.id}"]').getAnimations().length>0`),'the started card glides');
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);
  await click(`[data-id="${r2.id}"][data-action="done"]`);assert.equal(await laneOf(r2.id),null);
  assert.equal(await evaluate("document.querySelectorAll('#motion-layer .task-leaving.completing').length"),1,'a finished card fades out');
  assert.equal(await evaluate("document.querySelector('#motion-layer [data-id],#motion-layer [data-action],#motion-layer [id]')"),null,'copies carry no live controls');
  await wait("document.querySelector('#motion-layer .task')===null");
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);
  // Reduced motion: the same changes show at once, with no animation at all.
  await rpc('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]},sessionId);
  sent=requests.length;await drag(await grab(r5.id),await center('#board .lane[data-state="active"] .lane-cards'),{release:false});
  assert.equal(await evaluate("document.getAnimations().filter(a=>a.effect?.target?.closest?.('#board,#motion-layer')).length"),0);
  await mouse('mouseReleased',await center('#board .lane[data-state="active"] .lane-cards'),0);
  assert.equal(await evaluate("document.querySelector('#motion-layer .task')"),null,'nothing floats with reduced motion');
  assert.equal(await laneOf(r5.id),'active');assert.equal(await evaluate("document.getAnimations().filter(a=>a.effect?.target?.closest?.('#board,#motion-layer')).length"),0);
  await wait(`daySnapshot().revision>=${state.revision}&&!dayUI.flow.loading`);
  await rpc('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'no-preference'}]},sessionId);
  // Thirty tasks: each pointer move during a drag costs a few milliseconds, well inside a 60 fps frame.
  const many=Array.from({length:30},(_,i)=>({...task('Load '+(i+1)),priority:['high','medium','low'][i%3]}));
  Object.assign(state,{tasks:many,taskOrder:many.map(t=>t.id)});state.revision++;await refresh();await click('#list-view');
  const cost=await evaluate(`(async()=>{${still}const card=document.querySelector('#board .lane[data-state="todo"] .task'),handle=card.querySelector('.drag-handle'),r=handle.getBoundingClientRect(),x=r.left+r.width/2;let y=r.top+r.height/2;
   const send=(type,y)=>document.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,pointerId:7,isPrimary:true,pointerType:'mouse',button:0,buttons:1,clientX:x,clientY:y}));
   handle.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,cancelable:true,pointerId:7,isPrimary:true,pointerType:'mouse',button:0,buttons:1,clientX:x,clientY:y}));
   const frames=[];for(let i=0;i<60;i++){y+=9;send('pointermove',y);const t=performance.now();await new Promise(requestAnimationFrame);frames.push(performance.now()-t)}
   // The work one move costs: handling it and the frame callback that moves the slot.
   // Frame callbacks run in order: one before the move's own callback and one after it time it.
   const work=[];for(let i=0;i<40;i++){y-=9;await new Promise(requestAnimationFrame);let t;requestAnimationFrame(()=>t=performance.now());send('pointermove',y);await new Promise(r=>requestAnimationFrame(()=>{work.push(performance.now()-t);r()}))}
   document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));send('pointerup',y);
   const idle=[];for(let i=0;i<60;i++){const t=performance.now();await new Promise(requestAnimationFrame);idle.push(performance.now()-t)}
   const q=(list,f)=>{const v=[...list].sort((a,b)=>a-b);return Math.round(v[Math.floor(f*(v.length-1))]*10)/10};
   return {frameP50:q(frames,.5),frameP95:q(frames,.95),idleP50:q(idle,.5),idleP95:q(idle,.95),workP50:q(work,.5),workP95:q(work,.95),workMax:q(work,1)}})()`);
  console.log('Drag timing with 30 tasks (ms):',JSON.stringify(cost));
  // Frame intervals depend on the machine's load; the work each move adds must fit a 60 fps frame.
  // The tail is logged, not asserted: a busy test machine stretches single frames.
  assert.ok(cost.workP50<8,'each drag move fits a 60 fps frame: '+JSON.stringify(cost));
  // Phone: hold a row, then drag it into another section; a hold without moving still opens the menu.
  Object.assign(state,{tasks:rows.slice(0,4).map(r=>({...r,state:'todo',blocked:false,blockedBy:[],dependsOn:[]}))});state.tasks.forEach(t=>Object.assign(rows.find(r=>r.id===t.id),t));state.tasks=rows.slice(0,4);state.revision++;
  await size(390,844,true);await rpc('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1},sessionId);await refresh();
  if(await evaluate("document.getElementById('chat-dialog').open"))await click('#chat-close');
  const touch=(type,points)=>rpc('Input.dispatchTouchEvent',{type,touchPoints:points},sessionId);
  const from=await evaluate(`(async()=>{${still}const r=document.querySelector('#board [data-task-id="${r4.id}"] .task-title').getBoundingClientRect();return {x:Math.round(r.left+10),y:Math.round(r.top+r.height/2)}})()`);
  const to=await center('#board .lane[data-state="active"] .lane-cards');
  sent=requests.length;await touch('touchStart',[from]);await new Promise(r=>setTimeout(r,600));
  assert.ok(await evaluate(`document.querySelector('#board [data-task-id="${r4.id}"]').classList.contains('pressing')`),'a held row shows it is ready to move');
  for(let i=1;i<=10;i++){await touch('touchMove',[{x:from.x+(to.x-from.x)*i/10,y:from.y+(to.y-from.y)*i/10}]);await new Promise(r=>setTimeout(r,16))}
  await touch('touchEnd',[]);for(let i=0;i<100&&requests.length<sent+1;i++)await new Promise(r=>setTimeout(r,15));
  assert.deepEqual(moved(requests[sent]),['front_activate',{uuid:r4.id}]);assert.equal(await laneOf(r4.id),'active');
  assert.equal(await evaluate("document.querySelector('.row-menu')"),null,'a drag opens no menu');
  await rpc('Emulation.setTouchEmulationEnabled',{enabled:false},sessionId);await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);
  // Spanish copy for the drop's confirmation.
  await fill('locale-choice','es');assert.equal(await evaluate("dayUI.tr('drop_moved').replace('{lane}',dayUI.tr('active'))"),'Movida a En curso');await fill('locale-choice','en');
  Object.assign(state,{tasks:kept,instant:false,taskOrder:[]});state.revision++;await refresh();
  console.log('Drag regressions passed: lift, slot, exact landing, columns, snooze column, blocked refusal, Escape, groups, one Undo, in-place render, transitions, reduced motion, frame timing and touch');
 }

 if(group==='all'||group==='quick'){
  // One line, Enter: the card shows before the answer, with the profile-zone date the preview showed.
  await close();await fill('locale-choice','es');await click('#list-view');
  const kept=state.tasks;Object.assign(a,{group:'Casa'});state.quickAdd=true;state.tasks=[target,a,b,c];state.revision++;await refresh();
  const preview=()=>text('#qa-preview'),before=()=>requests.length,quickRequest=()=>requests.filter(r=>r.action==='task_add').at(-1);
  assert.match(await preview(),/También entiende/);
  assert.equal(await evaluate("document.getElementById('qa-submit').textContent"),'Agregar');
  await put('#qa-input','pagar luz mañana !2 #casa');
  assert.match(await preview(),/Se agregará:\s*Pagar luz\s*Alta\s*Casa\s*vence /);assert.doesNotMatch(await preview(),/grupo nuevo/);
  let sent=before();const started=Date.now();
  const shown=await evaluate(`(()=>{const t0=performance.now();document.getElementById('quick-add').requestSubmit();const row=[...document.querySelectorAll('#board .task')].find(e=>e.textContent.includes('Pagar luz'));return {visible:!!row,ms:performance.now()-t0,dialogs:document.querySelectorAll('dialog[open]').length,line:document.getElementById('qa-input').value,focused:document.activeElement.id}})()`);
  assert.deepEqual([shown.visible,shown.dialogs,shown.line,shown.focused],[true,0,'','qa-input'],'the card shows at once, with no form, and the line is ready for the next one');
  await serverWait(()=>state.tasks.some(t=>t.title==='Pagar luz'));const savedAt=Date.now()-started;
  await wait(`daySnapshot().tasks.some(t=>t.title==='Pagar luz'&&!t.pending)`);const settledAt=Date.now()-started;
  assert.equal(requests.length-sent,1);
  assert.deepEqual(quickRequest().payload,{title:'Pagar luz',priority:'high',source:'general',group:'Casa',due:'2025-10-30T00:00:00.000Z'});
  console.log('Quick capture timing (ms): '+JSON.stringify({visible:Math.round(shown.ms*10)/10,serviceSaved:savedAt,stateReconciled:settledAt}));
  // «la mañana» is the morning: it stays in the title and sets no date.
  sent=before();await put('#qa-input','llamar a Ana por la mañana');assert.doesNotMatch(await preview(),/vence/);
  await evaluate("document.getElementById('quick-add').requestSubmit()");await serverWait(()=>requests.length>sent);
  assert.deepEqual(quickRequest().payload,{title:'Llamar a Ana por la mañana',priority:'medium',source:'general'});
  // The same weekday is a week ahead; an explicit time is read only after a date; a new group says so.
  await put('#qa-input','junta el martes 9:30 #Mudanza !baja');assert.match(await preview(),/grupo nuevo/);
  sent=before();await evaluate("document.getElementById('quick-add').requestSubmit()");await serverWait(()=>requests.length>sent);
  assert.deepEqual(quickRequest().payload,{title:'Junta',priority:'low',source:'general',group:'Mudanza',due:'2025-11-04T16:30:00.000Z'});
  // Doubtful lines are not saved: two priorities, two groups, a repeated DST hour, details without a title.
  for(const [line,reason] of [['revisar !1 !3','Dos prioridades'],['revisar #Casa #Otro','Dos grupos'],['cambio de hora domingo 1:30','inequívoca'],['mañana !2','Escribe qué agregar']]){
   sent=before();await put('#qa-input',line);await evaluate("document.getElementById('quick-add').requestSubmit()");await new Promise(r=>setTimeout(r,60));
   assert.equal(requests.length,sent,line+' is not sent');assert.match(await preview(),new RegExp(reason));assert.equal(await evaluate("document.getElementById('qa-input').value"),line,'the line is kept to fix it');
  }
  // Esc clears the line, then leaves it; no other key does anything new.
  await key('#qa-input','Escape');assert.equal(await evaluate("document.getElementById('qa-input').value"),'');
  // The full form stays and starts from the line.
  await put('#qa-input','pagar luz viernes !1 #Casa');await click('#capture-open');
  assert.deepEqual(await evaluate("['capture-title','capture-priority','capture-group','capture-due'].map(id=>document.getElementById(id).value)"),['Pagar luz','urgent','Casa','2025-10-31T18:00']);
  assert.equal(await evaluate("document.getElementById('capture-kind').querySelectorAll('option').length"),2,'calendar commitments stay in the full form');
  sent=before();await submit('capture-dialog');await saved('capture-dialog');assert.equal(requests.length-sent,1);
  assert.equal(await evaluate("document.getElementById('qa-input').value"),'','a line saved from the form is cleared');
  // A refused save gives the line back and removes the draft card.
  await put('#qa-input','no se guarda');state.revision++;sent=before();
  await evaluate("document.getElementById('quick-add').requestSubmit()");await serverWait(()=>requests.length>sent);
  await wait("document.getElementById('qa-input').value==='no se guarda'");await wait("!daySnapshot().tasks.some(t=>t.pending)");
  await refresh();await key('#qa-input','Escape');
  // English copy; typed content is not translated.
  await fill('locale-choice','en');await put('#qa-input','pay rent tomorrow 08:15 !urgent');
  assert.match(await preview(),/Will add:\s*Pay rent\s*Urgent\s*due /);assert.equal(await evaluate("document.getElementById('capture-open').textContent"),'Add with details');
  await key('#qa-input','Escape');
  // Focus keeps its own controls only; a read-only profile cannot type into the line.
  await click('#focus-view');assert.equal(await evaluate("document.getElementById('quick-add').getClientRects().length+document.getElementById('qa-preview').getClientRects().length"),0);await click('#list-view');
  state.readOnly=true;state.revision++;await refresh();assert.equal(await evaluate("document.getElementById('qa-input').disabled&&document.getElementById('qa-submit').disabled"),true);
  state.readOnly=false;state.revision++;await refresh();
  // Phone: the line stays one row with a 44 px target and nothing overflows.
  await rpc('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true},sessionId);await new Promise(r=>setTimeout(r,100));
  const phone=await evaluate("(()=>{const f=document.getElementById('quick-add').getBoundingClientRect(),b=document.getElementById('qa-submit').getBoundingClientRect();return {fits:document.documentElement.scrollWidth<=innerWidth,height:Math.round(f.height),button:Math.round(b.height)}})()");
  assert.equal(phone.fits,true);assert.ok(phone.button>=44&&phone.height<70,JSON.stringify(phone));
  await rpc('Emulation.clearDeviceMetricsOverride',{},sessionId);
  state.quickAdd=false;state.tasks=kept;state.revision++;await refresh();
  console.log('Quick capture regressions passed: one line and Enter, preview, priority, single group, profile-zone dates, morning, DST refusal, Esc, full form, refused save, localization, Focus, read-only and phone');
 }
}finally{
 if(socket)socket.close();chrome.kill('SIGTERM');await new Promise(resolve=>chrome.exitCode!==null?resolve():chrome.once('exit',resolve));
 await new Promise(resolve=>server.close(resolve));await rm(profile,{recursive:true,force:true,maxRetries:5,retryDelay:100});
}
