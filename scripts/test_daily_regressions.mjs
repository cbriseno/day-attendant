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
assert.ok(['all','details','dates'].includes(group),'Unknown browser regression group');
const task=(title)=>({id:randomUUID(),title,state:'todo',priority:'medium',due:null,until:null,context:'',next:'',awaiting:false,remainingMinutes:null,dependsOn:[],relatedTo:[],blocked:false,blockedBy:[],attachmentIds:[],source:'general'});
const target=task('Responsabilidad original'),a=task('A'),b=task('B'),c=task('C');
// Historical DST fixtures remain stable as jurisdictions change future rules.
const state={profile:{id:randomUUID(),name:'Controlled profile',timezone:'America/Edmonton',locale:'en',harness:'codex',model:'controlled'},timezone:'America/Edmonton',now:'2025-10-28T12:00:00Z',revision:1,epoch:0,synthetic:true,readOnly:false,tasks:[target,a,b,c],events:[],proposals:[],messages:[],calendar_events:[],context:{availableMinutes:null,availableUntil:null,rest:false},agent:{connected:true,model:'controlled'},diagnostics:{operations:[],reminders:[]},operations_status:{},notification_policy:{enabled:false,weekdays:[0,1,2,3,4],start:'09:00',end:'18:00',quietStart:'22:00',quietEnd:'08:00',intervalMinutes:10,unchangedRepeatMinutes:1440,dailyBudget:12,pausedUntil:null}};
const requests=[],settings=[];
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
 receipt=await evaluate("document.querySelector('.receipt-list').textContent");for(const label of ['Saved','Review incomplete','Context changed','Invalid','Needs review','Canceled','Replaced','Unchanged'])assert.ok(receipt.includes(label),receipt);
 for(const [phase,label] of [['ready','Ready to save'],['admitted','Admitted; check recovery']]){state.proposals[0].phase=phase;await refresh();assert.ok((await evaluate("document.querySelector('.receipt-list').textContent")).includes(label))}
 state.messages.push({id:3,key:'question',role:'assistant',text:'Please clarify.',status:'completed',data:JSON.stringify({proposal_ids:[],unprocessed:['Unscheduled request'],uncertainties:['Who is the person?']})});await refresh();
 assert.match(await evaluate("document.getElementById('messages').textContent"),/Unscheduled request.*Who is the person/s);

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
