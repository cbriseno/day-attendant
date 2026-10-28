// Real pointer/keyboard UI -> real Codex -> real Taskwarrior/SQLite.
// Setup also uses the product API and real reviews; no state writes bypass it.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash,randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const state=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);
const connection=JSON.parse(fs.readFileSync(path.join(state,'ui-server.json'),'utf8'));
const report={scope:'Real browser/CDP pointer and keyboard, real Codex and native Taskwarrior; synthetic state only',checks:[],run_id:randomUUID()};
const bootstrap=process.argv[4]==='--bootstrap';const continuation=process.argv[4]&&!bootstrap?JSON.parse(fs.readFileSync(process.argv[4],'utf8')):null;if(continuation){report.continuation={file:path.basename(process.argv[4]),sha256:createHash('sha256').update(fs.readFileSync(process.argv[4])).digest('hex'),error:continuation.error};report.checks=continuation.checks.map(c=>({...c,importedFrom:report.continuation.file}));}
const pause=(n=200)=>new Promise(r=>setTimeout(r,n));
async function api(route,body){const r=await fetch(connection.origin+route,{method:body===undefined?'GET':'POST',headers:{Origin:connection.origin,Authorization:'Bearer '+connection.token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d));return d.data;}
const snap=()=>api('/api/state');
function sql(expression){return JSON.parse(execFileSync('python3',['-c',`import sqlite3,json,sys\nc=sqlite3.connect(sys.argv[1]);c.row_factory=sqlite3.Row\n${expression}`,path.join(state,'assistant.sqlite3')],{encoding:'utf8'}));}
const ids=sql("print(json.dumps([json.loads(c.execute('SELECT result FROM operations WHERE key=?',(f'bootstrap-task-{i}',)).fetchone()[0])['uuid'] for i in range(4)]))");
function native(){return JSON.parse(execFileSync(path.join(root,'.runtime/taskwarrior/bin/task'),['rc.confirmation=off','rc.verbose=nothing','export'],{encoding:'utf8',env:{...process.env,TASKRC:path.join(state,'taskwarrior/taskrc'),TASKDATA:path.join(state,'taskwarrior/data')}}));}
async function until(predicate,timeout=120000){const start=Date.now();while(Date.now()-start<timeout){const d=await snap();if(predicate(d))return d;await pause(250)}throw Error('Timed out waiting for product state');}
function record(name,checks,extra={}){if(Object.values(checks).some(x=>x===false))throw Error(name+': '+JSON.stringify(checks));report.checks.push({name,checks,...extra});fs.writeFileSync(out,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({name,checks}));}
async function apply(action,payload,key){const p=await api('/api/proposals',{action,payload,key});const d=await until(d=>!['evaluating'].includes(d.proposals.find(x=>x.id===p.id).phase));const final=d.proposals.find(x=>x.id===p.id);if(!['applied','unchanged'].includes(final.phase))throw Error('Setup required unexpected review: '+JSON.stringify(final));return final;}
const targets=await(await fetch('http://127.0.0.1:9337/json/list')).json();
const target=targets.find(t=>t.type==='page'&&t.url.startsWith(connection.origin));if(!target)throw Error('Isolated browser page missing');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.addEventListener('open',r,{once:true});ws.addEventListener('error',j,{once:true})});
let serial=0;const pending=new Map();ws.addEventListener('message',e=>{const d=JSON.parse(e.data),p=pending.get(d.id);if(p){pending.delete(d.id);d.error?p.reject(Error(d.error.message)):p.resolve(d.result)}});
const rpc=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
const evaluate=async expression=>{const r=await rpc('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
async function clickSelector(selector){const point=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e||!e.getClientRects().length)throw Error('Missing or hidden control');e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);await rpc('Input.dispatchMouseEvent',{type:'mouseMoved',...point});await rpc('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',buttons:1,clickCount:1});await rpc('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',buttons:0,clickCount:1});await pause(80)}
async function fill(selector,text){await clickSelector(selector);await rpc('Input.dispatchKeyEvent',{type:'keyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});await rpc('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});if(text)await rpc('Input.insertText',{text});else{await rpc('Input.dispatchKeyEvent',{type:'keyDown',key:'Backspace',code:'Backspace',windowsVirtualKeyCode:8});await rpc('Input.dispatchKeyEvent',{type:'keyUp',key:'Backspace',code:'Backspace',windowsVirtualKeyCode:8});}}
async function sync(){await pause(1100);}
const action=(id,name)=>`[data-id="${id}"][data-action="${name}"]`;
async function uiAction(id,name,expectedState){const before=await snap();if(before.tasks.find(t=>t.id===id).state===expectedState)return before;await clickSelector(action(id,name));const d=await until(d=>d.tasks.find(t=>t.id===id).state===expectedState);await sync();return d;}
async function chat(text){if(await evaluate("document.querySelector('#chat-toggle').getAttribute('aria-expanded')==='false'"))await clickSelector('#chat-toggle');await fill('#conversation-input',text);await clickSelector('#send-message');return until(d=>d.messages.some(m=>m.role==='user'&&m.text===text&&m.status!=='pending'));}
try {
 await rpc('Page.navigate',{url:connection.origin+'/#session='+connection.token});await rpc('Page.reload');await pause(1000);await sync();
 const before=await snap();const databaseCounts=()=>sql("print(json.dumps({t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['evaluations','facts','decisions','proposals','messages']}))");
 const counts=databaseCounts();
 const content=s=>JSON.stringify([s.tasks,s.messages,s.events,s.proposals,s.context,s.revision]);
 const control=async(id,value)=>evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(id)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
 for(const locale of ['es','en']){
  await control('#locale-choice',locale);await sync();const current=await snap();
  record('locale-'+locale+'-preserves-domain',{sameContent:content(current)===content(before),sameCallsAndReceipts:JSON.stringify(databaseCounts())===JSON.stringify(counts),htmlLanguage:await evaluate('document.documentElement.lang')===locale});
 }
 const layouts=[];
 for(const width of [320,768,1440])for(const locale of ['en','es'])for(const theme of ['light','dark']){
  await rpc('Emulation.setDeviceMetricsOverride',{width,height:1050,deviceScaleFactor:1,mobile:width<500});await control('#locale-choice',locale);await control('#theme-choice',theme);await pause(80);
  for(const chatOpen of [false,true]){
   const open=await evaluate("document.querySelector('#chat-toggle').getAttribute('aria-expanded')==='true'");if(open!==chatOpen)await clickSelector('#chat-toggle');await pause(80);
   const metrics=await evaluate("JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth,chatExpanded:document.querySelector('#chat-toggle').getAttribute('aria-expanded'),dialogs:document.querySelectorAll('dialog[open]').length})");
   const m=JSON.parse(metrics);if(m.scroll>width+1)throw Error('Horizontal overflow: '+JSON.stringify({width,locale,theme,chatOpen,...m}));layouts.push({width,locale,theme,chatOpen,...m});
   if(width<1280&&chatOpen)await clickSelector('#chat-close');
  }
 }
 record('responsive-localized-layouts',{all24NoPageOverflow:layouts.length===24},{layouts});
 await rpc('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});await control('#locale-choice','en');await control('#theme-choice','dark');await sync();
 if(await evaluate("document.querySelector('#chat-toggle').getAttribute('aria-expanded')==='true'"))await clickSelector('#chat-close');
 await clickSelector(action(ids[1],'detail'));
 const opened=await evaluate("document.querySelector('#estimate-input').value");
 await api('/api/proposals',{action:'continuity_update',payload:{uuid:ids[1],remainingMinutes:10},key:'stale-editor-correction-'+report.run_id});await sync();
 await clickSelector('#estimate-save');await pause(300);
 const afterConflict=await snap();record('stale-estimate-form-preserves-correction-and-draft',{otherClientEstimatePreserved:afterConflict.tasks.find(t=>t.id===ids[1]).remainingMinutes===10,oldDraftPreserved:await evaluate("document.querySelector('#estimate-input').value")===opened,conflictVisible:await evaluate("!document.querySelector('#estimate-error').hidden && /reopen/i.test(document.querySelector('#estimate-error').textContent)"),dialogRemainsOpen:await evaluate("document.querySelector('#detail-dialog').open")});
 await clickSelector('[data-close="detail-dialog"]');
 await clickSelector('#context-open');const originalRest=await evaluate("document.querySelector('#context-rest').checked");
 await api('/api/proposals',{action:'context_update',payload:{rest:!originalRest},key:'stale-context-correction-'+report.run_id});await sync();await clickSelector('#context-form button[type="submit"]');await pause(300);
 record('stale-context-form-does-not-overwrite-correction',{otherClientRestPreserved:(await snap()).context.rest===!originalRest,draftPreserved:await evaluate("document.querySelector('#context-rest').checked")===originalRest,conflictVisible:await evaluate("!document.querySelector('#context-error').hidden"),dialogRemainsOpen:await evaluate("document.querySelector('#context-dialog').open")});
 await clickSelector('#context-cancel');
 // Drop only the first HTTP reply after the real server receives the message.
 // Hold polling until the second send, making uncertain delivery reproducible.
 await evaluate(`(()=>{window.__realFetch=window.fetch;window.__lostReply={keys:[],hold:true};window.fetch=async(input,init)=>{const url=typeof input==='string'?input:input.url;const state=window.__lostReply;if(url==='/api/state'&&state.hold&&state.keys.length)throw TypeError('Controlled polling hold');if(url==='/api/chat'){state.keys.push(JSON.parse(init.body).key);if(state.keys.length===2)state.hold=false;const response=await window.__realFetch(input,init);if(state.keys.length===1){await response.clone().json();throw TypeError('Controlled lost HTTP reply')}return response}return window.__realFetch(input,init)}})()`);
 const title='[SYNTHETIC] Delivery receipt '+report.run_id.slice(0,8),text='Add a task titled "'+title+'". Do not set a due date.';
 const originalTasks=(await snap()).tasks.length;
 if(await evaluate("document.querySelector('#chat-toggle').getAttribute('aria-expanded')==='false'"))await clickSelector('#chat-toggle');
 await fill('#conversation-input',text);await clickSelector('#send-message');await pause(400);
 const retained=await evaluate("document.querySelector('#conversation-input').value");await clickSelector('#send-message');
 const final=await until(s=>s.tasks.some(t=>t.title===title));await sync();const sent=await evaluate('window.__lostReply.keys');
 record('lost-chat-reply-reuses-identity',{draftRetainedAfterLostReply:retained===text,twoDeliveries:sent.length===2,sameKey:sent[0]===sent[1],oneUserMessage:final.messages.filter(m=>m.role==='user'&&m.key===sent[0]).length===1,oneTask:final.tasks.filter(t=>t.title===title).length===1,oneTaskAdded:final.tasks.length===originalTasks+1});
 await evaluate('window.fetch=window.__realFetch');
 // Native outside click and Escape dismiss dialogs without applying changes.
 await clickSelector('#context-open');const box=await evaluate("(()=>{const r=document.querySelector('#context-dialog').getBoundingClientRect();return {x:Math.max(2,r.left-10),y:Math.max(2,r.top-10)}})()");
 await rpc('Input.dispatchMouseEvent',{type:'mousePressed',...box,button:'left',buttons:1,clickCount:1});await rpc('Input.dispatchMouseEvent',{type:'mouseReleased',...box,button:'left',buttons:0,clickCount:1});await pause(150);
 const outsideClosed=await evaluate("!document.querySelector('#context-dialog').open");await clickSelector(action(ids[0],'snooze'));await rpc('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await rpc('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});await pause(150);
 record('native-dialog-dismissal',{outsideClosed,escapeClosed:await evaluate("!document.querySelector('#snooze-dialog').open")});
 // Preserve a final screenshot from this exact CDP viewport for visual review.
 const shot=await rpc('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(root,'arquitecto/20261027_investigacion/final-ui-dark.png'),Buffer.from(shot.data,'base64'));
 report.completed=true;fs.writeFileSync(out,JSON.stringify(report,null,2)+'\n');
}catch(error){report.completed=false;report.error=String(error);fs.writeFileSync(out,JSON.stringify(report,null,2)+'\n');throw error}
finally{await evaluate("if(window.__realFetch)window.fetch=window.__realFetch").catch(()=>{});await rpc('Emulation.clearDeviceMetricsOverride');ws.close()}
