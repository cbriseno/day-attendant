(() => {
'use strict';
const $=id=>document.getElementById(id),root=document.documentElement;
let locale='en';try{locale=localStorage.getItem('day-attendant-locale')||'en'}catch{}if(!['en','es'].includes(locale))locale='en';
const tr=key=>globalThis.dayTranslations[locale]?.[key]??globalThis.dayTranslations.en[key]??key;
const known=code=>tr('error_'+code)!=='error_'+code;
const errorText=(code,message)=>known(code)?tr('error_'+code):message;
const formatLocale=()=>locale==='es'?'es-MX':'en-US';
function translateChrome(){root.lang=locale;document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=tr(e.dataset.i18n));for(const attr of ['aria-label','placeholder','title'])document.querySelectorAll('[data-i18n-'+attr+']').forEach(e=>e.setAttribute(attr,tr(e.getAttribute('data-i18n-'+attr))));}
let NOW=new Date();let zone=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const full=value=>new Intl.DateTimeFormat(formatLocale(),{weekday:'short',day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone,timeZoneName:'shortOffset'}).format(new Date(value));
const short=value=>{const d=new Date(value);return (localValue(d).slice(0,10)===localValue(NOW).slice(0,10)?tr("today"):new Intl.DateTimeFormat(formatLocale(),{day:'numeric',month:'short',timeZone:zone}).format(d))+', '+new Intl.DateTimeFormat(formatLocale(),{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone}).format(d)};
const localValue=d=>{const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d).map(p=>[p.type,p.value]));return p.year+'-'+p.month+'-'+p.day+'T'+p.hour+':'+p.minute;};
function parseLocal(raw){const base=Date.parse(raw+'Z');if(!Number.isFinite(base))return new Date(NaN);let guess=base;for(let i=0;i<4;i++){const wall=Date.parse(localValue(new Date(guess))+'Z');const delta=base-wall;if(!delta)break;guess+=delta;}return new Date(guess)}
const clockIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/></svg>';
const arrowIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';
const gripIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6h.01M9 12h.01M9 18h.01M15 6h.01M15 12h.01M15 18h.01"/></svg>';
const checkIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>';
const playIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 5 12 7-12 7z"/></svg>';
const pauseIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14M16 5v14"/></svg>';
const editIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20h4L19 9l-4-4L4 16z"/></svg>';
const calendarIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>';
const copyIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>';
const moreIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>';
const formatted=(key,values)=>tr(key).replace(/\{(\w+)\}/g,(_,name)=>new Intl.NumberFormat(formatLocale()).format(values[name]));
const concernMessage=c=>c.message_key?formatted(c.message_key,c.message_params):c.message;
// Stable failure codes map to plain copy; internal error text is diagnostic and never shown.
const failureReasons={agent_invalid_evidence:'reason_invalid_evidence',agent_invalid_output:'reason_invalid_output',agent_invalid_intent:'reason_invalid_intent',
 agent_missed_known_concern:'reason_missed_concern',agent_timeout:'reason_timeout',agent_interrupted:'reason_interrupted',review_interrupted:'reason_interrupted',
 attachment_corrupt:'reason_attachment',agent_auth_required:'reason_auth',agent_restart_required:'reason_restart',
 agent_model_unavailable:'reason_model',agent_model_changed:'reason_model',agent_effort_unavailable:'reason_model',
 agent_unavailable:'reason_unavailable',agent_disconnected:'reason_unavailable',agent_missing:'reason_unavailable',agent_version_unsupported:'reason_unavailable',
 agent_config_unsupported:'reason_unavailable',agent_workspace_mismatch:'reason_unavailable',agent_isolation_unverified:'reason_unavailable'};
const failureReason=code=>tr(failureReasons[code]||'reason_failed');
const creates=action=>['add','task_add'].includes(action);
function messageText(item){
 if(item.role!=='assistant')return item.text;
 let data;try{data=JSON.parse(item.data||'null')||{}}catch{data={}}
 if(data.kind==='context_notice')return item.status==='incomplete'?tr('context_notice_incomplete')+' '+failureReason(data.code):item.status==='stale'?tr('context_notice_stale'):['pending','evaluating'].includes(item.status)?tr('context_notice_pending'):item.text;
 if(data.notice==='existing_proposals')return tr('chat_existing_proposals');
 if(item.status==='stale')return tr('chat_state_changed');
 if(item.status!=='incomplete')return item.text;
 if(data.code==='restart_interrupted')return tr('chat_restart_interrupted');
 if(data.system_code)return tr('chat_response_interrupted');
 return tr('chat_incomplete')+' '+(known(data.code)?tr('error_'+data.code):tr('chat_failed_generic'))+(data.proposal_ids?.length?'':' '+tr('chat_incomplete_saved_nothing'));
}
const origins=new WeakMap();
let flow,expandedProposal=null,lastSeenEvent,lastRequestedProposal=null,lastGuidanceNotice=null;
let phaseCache=new Map(),collapsedProposals=new Set(),dependencyFocus=null;
let toastTimer=null,bubbleFor=null;
// When each change was asked for and first shown, by proposal, so its confirmation reports its own time.
const timings=new Map();const timed=(result,started,shown,before)=>{if(result?.id)timings.set(result.id,{started,shown,before});if(timings.size>50)timings.delete(timings.keys().next().value);return result};
// What a change made on this page replaced, by fact, so its Undo shows at once too.
const undoViews=new Map();
// One short confirmation at a time; Undo stays while the pointer or focus is on it.
function toast(text,{undo=null,detail=''}={}){clearTimeout(toastTimer);$('status').textContent=text;$('toast-detail').textContent=detail;$('undo').hidden=undo===null;$('undo').dataset.event=undo??'';$('toast').classList.toggle('show',!!text);if(text)toastTimer=setTimeout(hideToast,undo===null?4000:6000)}
function hideToast(){if($('toast').matches(':hover,:focus-within')){toastTimer=setTimeout(hideToast,1500);return}$('toast').classList.remove('show');$('undo').hidden=true}
const notice=text=>toast(text);
const task=id=>flow.snapshot().tasks.find(t=>t.id===id);
const safely=async fn=>{try{return await fn()}catch(error){notice(error.message);return null}};
let actionLabel,phaseLabel;function refreshLabels(){actionLabel={activate:tr("start"),snooze:tr("snooze"),todo:tr("back_to_to_do"),done:tr("complete"),retire:tr("retire"),restore:tr("undo"),declaration:tr("declaration_updated"),add:tr("create"),update:tr("update"),reopen:tr("reopen"),dependency:tr("dependency_update"),related:tr("related_update")};
phaseLabel={evaluating:tr("the_assistant_is_reviewing"),review:tr("phase_waits_for_you"),unavailable:tr("phase_left_waiting"),stale:tr("context_changed"),invalid:tr("phase_not_applied")};
}
refreshLabels();
// Order views change presentation only. A drag stays inside its section (same lane and, in
// those views, the same priority or group) and saves the profile's manual order.
let orderView='priority';try{orderView=localStorage.getItem('day-attendant-order')||'priority'}catch{}if(!globalThis.dayOrdering.VIEWS.includes(orderView))orderView='priority';
let localOrder=null,orderBase=null,orderTimer=null,orderSaving=false,drag=null;
// Motion: a card that changes place glides there from where it was (FLIP), a new one fades in and
// a finished or retired one fades out over its old place. Reduced motion shows every change at once.
const DURATION=200,EASE='cubic-bezier(.2,.8,.2,1)',reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
const moving=()=>!reducedMotion.matches;
const boardCards=()=>[...$('board').querySelectorAll('.task[data-task-id]')];
const cardOf=id=>$('board').querySelector('.task[data-task-id="'+id+'"]');
const cardRects=()=>new Map(boardCards().map(c=>[c.dataset.taskId,c.getBoundingClientRect()]));
// Reads every position first and writes every animation after, so the browser lays out once.
function playFlip(first,{duration=DURATION+80,delay=0,enter=true}={}){
 const cards=boardCards();for(const card of cards)for(const a of card.getAnimations())a.cancel();
 const moves=cards.map(card=>{const was=first.get(card.dataset.taskId);if(!was)return [card,null];const now=card.getBoundingClientRect();return [card,[was.left-now.left,was.top-now.top]]});
 for(const [card,delta] of moves){
  if(!delta){if(enter)card.animate([{opacity:0,transform:'scale(.98)'},{opacity:1,transform:'none'}],{duration,delay,easing:'ease-out',fill:'backwards'});continue}
  const [dx,dy]=delta;if(Math.abs(dx)>=1||Math.abs(dy)>=1)card.animate([{transform:'translate('+dx+'px,'+dy+'px)'},{transform:'none'}],{duration,delay,easing:EASE,fill:'backwards'});
 }
 if(dependencyFocus)setTimeout(()=>drawDependencies(),duration+delay+20);
}
// Floating copies (a lifted card, a leaving one) live in a layer over the list that shares its styles.
function motionLayer(){let node=$('motion-layer');if(!node){node=document.createElement('div');node.id='motion-layer';node.className='board motion-layer';node.setAttribute('aria-hidden','true');$('tasks-area').append(node)}node.classList.toggle('is-list',$('board').classList.contains('is-list'));return node}
const inLayer=(x,y)=>{const r=motionLayer().getBoundingClientRect();return {x:x-r.left,y:y-r.top}};
function floating(card,rect,kind){const copy=card.cloneNode(true);copy.classList.remove('placeholder','pressing');copy.classList.add(kind);copy.removeAttribute('data-task-id');copy.removeAttribute('data-section');copy.removeAttribute('style');
 for(const e of copy.querySelectorAll('[id],[data-id],[data-action]')){e.removeAttribute('id');e.removeAttribute('data-id');e.removeAttribute('data-action')}
 copy.inert=true;const at=inLayer(rect.left,rect.top);Object.assign(copy.style,{width:rect.width+'px',height:rect.height+'px',transform:'translate('+at.x+'px,'+at.y+'px)'});motionLayer().append(copy);return copy}
function leave(card,rect,finished){const copy=floating(card,rect,'task-leaving');if(finished)copy.classList.add('completing');
 copy.animate([{opacity:1,transform:copy.style.transform},{opacity:0,transform:copy.style.transform+' translateX(16px)'}],{duration:DURATION+60,delay:finished?180:0,easing:'ease-in',fill:'forwards'}).onfinish=()=>copy.remove()}
// The board is updated in place: an unchanged card keeps its element, focus and any running
// animation; a changed one is replaced; a moved one is moved. Nothing is rebuilt as a whole.
function morphBoard(lanes,data){
 const board=$('board'),animate=moving(),first=animate?cardRects():new Map(),old=new Map(boardCards().map(c=>[c.dataset.taskId,c]));
 const make=html=>{const t=document.createElement('template');t.innerHTML=html;const node=t.content.firstElementChild;node._html=html;return node};
 lanes.forEach((lane,i)=>{
  let section=board.children[i];
  if(section?.dataset.state!==lane.state){section=make('<section class="lane" data-state="'+lane.state+'"><header class="lane-heading"></header><div class="lane-cards"></div></section>');board.insertBefore(section,board.children[i]||null)}
  const head=section.firstElementChild,box=section.lastElementChild;if(head._html!==lane.head){head.innerHTML=lane.head;head._html=lane.head}
  const extras=new Map([...box.children].filter(n=>n._key).map(n=>[n._key,n]));
  const wanted=lane.items.map(item=>{let node=item.id?old.get(item.id):extras.get(item.key);if(item.id)old.delete(item.id);if(!node||node._html!==item.html){node=make(item.html);node._key=item.key}return node});
  wanted.forEach((node,j)=>{if(box.children[j]!==node)box.insertBefore(node,box.children[j]||null)});
  while(box.children.length>wanted.length)box.lastElementChild.remove();
 });
 while(board.children.length>lanes.length)board.lastElementChild.remove();
 if(!animate||!first.size)return;
 // A capture shown before its answer comes back under its saved id: it stays where it was.
 const drafts=[...old].filter(([id])=>id.startsWith('draft:'));
 for(const [id,node] of old){if(id.startsWith('draft:')||!first.has(id))continue;leave(node,first.get(id),data.tasks.find(t=>t.id===id)?.state==='done')}
 for(const [id] of drafts){const title=data.tasks.find(t=>t.id===id)?.title??old.get(id).querySelector('.task-title')?.textContent,saved=data.tasks.find(t=>!first.has(t.id)&&t.title===title&&!t.id.startsWith('draft:'));if(saved)first.set(saved.id,first.get(id))}
 const closing=[...old.keys()].some(id=>!id.startsWith('draft:')&&first.has(id));
 playFlip(first,{delay:closing?260:0});
}
const groupDot=label=>'<span class="group-dot" style="--group-hue:'+globalThis.dayOrdering.hue(label)+'" aria-hidden="true"></span>';
const PRIORITIES=globalThis.dayOrdering.PRIORITIES;
// One compact row per task: handle · finish · title and its data · priority · start or pause · snooze · more.
// The Board uses the same pieces as small cards. Every action is one click; priority is a one-level menu.
function taskRow(t,s,data,proposal){
 const off=data.readOnly||t.pending,label=text=>esc(text+': '+t.title),blockedStart=t.blocked&&t.state!=='active';
 const handle=s.draggable&&!data.readOnly&&!t.pending?'<button type="button" class="drag-handle" data-id="'+t.id+'" data-action="move" aria-label="'+label(tr('reorder'))+'" title="'+esc(tr('reorder_hint'))+'">'+gripIcon+'</button>':'';
 const state=t.state==='active'?'<button type="button" class="ib state-action on" data-id="'+t.id+'" data-action="todo" aria-label="'+label(tr('pause'))+'" title="'+esc(tr('pause'))+'"'+(off?' disabled':'')+'>'+pauseIcon+'</button>'
  :'<button type="button" class="ib state-action" data-id="'+t.id+'" data-action="activate" aria-label="'+label(t.state==='snoozed'?tr('resume_b3bd0b'):tr('start'))+'" title="'+esc(blockedStart?tr('start_blocked'):t.state==='snoozed'?tr('resume_b3bd0b'):tr('start'))+'"'+(off||blockedStart?' disabled':'')+'>'+playIcon+'</button>';
 const open=expanded===t.id&&!t.pending;
 return '<article class="task'+(t.pending?' saving':'')+(open?' expanded':'')+(titleEdit?.id===t.id?' editing':'')+'" data-task-id="'+t.id+'" data-section="'+esc(t.state+':'+s.key)+'"><div class="row-main">'
  +'<span class="row-grip">'+handle+'</span>'
  +'<button type="button" class="check" data-id="'+t.id+'" data-action="done" aria-label="'+label(tr('complete'))+'" title="'+esc(tr('complete'))+'"'+(off?' disabled':'')+'><span class="box">'+checkIcon+'</span></button>'
  +'<div class="row-text">'+suggestionReason(t,data)+titleCell(t,data)+'<span class="row-meta">'+rowMeta(t,data,proposal)+'</span></div>'
  +'<div class="row-actions"><button type="button" class="priority-pill" data-priority="'+esc(t.priority)+'" data-id="'+t.id+'" data-action="priority" aria-haspopup="menu" aria-expanded="false" title="'+esc(tr('change_priority'))+'"'+(off?' disabled':'')+'><span class="visually-hidden">'+esc(tr('priority'))+': </span>'+esc(tr('priority_'+t.priority))+'</button>'
  +state+'<button type="button" class="ib" data-id="'+t.id+'" data-action="snooze" aria-haspopup="menu" aria-expanded="false" aria-label="'+label(tr('snooze'))+'" title="'+esc(tr('snooze'))+'"'+(off?' disabled':'')+'>'+clockIcon+'</button>'
  +'<button type="button" class="ib more" data-id="'+t.id+'" data-action="more" aria-haspopup="menu" aria-expanded="false" aria-label="'+label(tr('more_actions'))+'" title="'+esc(tr('more_actions_hint'))+'">'+moreIcon+'</button></div>'
  +'<span class="port-slot" aria-hidden="true"></span></div>'+suggestionBubble(t,data)+(open?expandView(t,data):'')+'</article>'}
// The data line under or beside the title: group, dates, waiting, estimate, dependencies and a pending change.
function rowMeta(t,data,proposal){const time=(key,value,cls)=>'<span class="'+cls+'" title="'+esc(full(value))+'">'+esc(tr(key).replace('{time}',short(value)))+'</span>';
 const off=data.readOnly||t.pending,menuButton=(action,cls,content,hint)=>'<button type="button" class="'+cls+'" data-id="'+t.id+'" data-action="'+action+'" aria-haspopup="menu" aria-expanded="false" title="'+esc(hint)+'"'+(off?' disabled':'')+'>'+content+'</button>';
 // Under group headings the chip is plain, without the dot the heading already shows.
 return (t.group?menuButton('group','group-chip'+(orderView==='group'?' plain':''),(orderView==='group'?'':groupDot(t.group))+'<span class="visually-hidden">'+esc(tr('group'))+': </span>'+esc(t.group),tr('change_group')):menuButton('group','group-chip add','<span aria-hidden="true">+ </span>'+esc(tr('add_group_chip')),tr('add_group')))
  +(t.due?menuButton('due','task-due'+(new Date(t.due)<NOW?' late':''),esc(tr(new Date(t.due)<NOW?'meta_overdue':'meta_due').replace('{time}',short(t.due))),full(t.due)+' · '+tr('change_deadline')):'')
  +(t.until?time('meta_resumes',t.until,'task-wait'):'')
  +(t.awaiting?'<span class="task-wait">'+esc(tr('meta_waiting'))+'</span>':'')
  +(data.context.availableMinutes!==null&&Number.isFinite(t.remainingMinutes)?'<span>'+esc(formatted('meta_remaining',{n:t.remainingMinutes}))+'</span>':'')
  +dependencyChips(t,data)
  +(proposal?'<button class="pending-badge" type="button"'+(data.readOnly?' disabled':'')+' data-id="'+t.id+'" data-action="pending">'+esc(tr('pending_change_badge'))+'</button>':'')}
// The assistant's later look leaves a discreet mark on the affected card; it never moves the card.
const currentSuggestion=(id,data)=>(data.suggestions||[]).find(s=>s.current&&s.taskId===id);
function suggestionText(s){if(s.message)return s.message;const p=s.messageParams||{};return tr(s.messageKey).replace(/\{(\w+)\}/g,(_,k)=>['due','until'].includes(k)?short(p[k]):typeof p[k]==='number'?new Intl.NumberFormat(formatLocale()).format(p[k]):String(p[k]??''))}
function suggestionLabel(s,data){const a=s.action,other=a.payload.uuid!==s.taskId&&data.tasks.find(t=>t.id===a.payload.uuid);
 if(a.action==='front_activate')return other?tr('suggest_do_start_other').replace('{title}',other.title):tr('suggest_do_start');
 if(a.action==='front_todo')return tr('suggest_do_todo');if(a.action==='task_snooze')return tr('suggest_do_snooze').replace('{time}',short(a.payload.until));
 if(a.action==='dependency_update')return tr('suggest_do_unlink');
 return a.payload.priority?tr('suggest_do_priority').replace('{priority}',tr('priority_'+a.payload.priority)):tr('suggest_do_due').replace('{time}',short(a.payload.due))}
function suggestionMark(t,data){return currentSuggestion(t.id,data)?'<button type="button" class="assistant-mark" data-id="'+t.id+'" data-action="suggestion" aria-expanded="'+(bubbleFor===t.id)+'" aria-label="'+esc(tr('suggestion_mark_label')+': '+t.title)+'" title="'+esc(tr('suggestion_mark_title'))+'"></button>':''}
function suggestionBubble(t,data){const s=currentSuggestion(t.id,data);if(!s||bubbleFor!==t.id)return '';
 return '<div class="assistant-bubble"><p><strong>'+esc(tr('suggestion_prefix'))+'</strong> '+esc(suggestionText(s))+'</p><div class="bubble-actions">'+(s.action&&!data.readOnly?'<button type="button" class="btn" data-suggestion="'+s.id+'" data-suggestion-action="accept">'+esc(suggestionLabel(s,data))+'</button>':'')+'<button type="button" class="link-button" data-suggestion="'+s.id+'" data-suggestion-action="dismiss">'+esc(tr('suggestion_dismiss'))+'</button></div></div>'}
function suggestionReason(t,data){const order=data.orderSuggestion?.result?.order;if(orderView!=='suggested'||!order)return '';const i=order.findIndex(x=>x.id===t.id);return '<p class="suggestion-reason">'+(i<0?esc(tr('not_in_suggestion')):'<span class="suggestion-rank">'+(i+1)+'</span>'+esc(order[i].reason))+'</p>'}
function groupHeading(s){return '<h3 class="group-heading" data-group="'+esc(s.label||'')+'">'+(s.label?groupDot(s.label)+esc(s.label):'<span class="group-dot ungrouped" aria-hidden="true"></span>'+esc(tr('no_group')))+'<span class="lane-count">'+s.tasks.length+'</span></h3>'}
// Only the suggested order has something to say or ask; the other orders explain themselves.
function orderNote(data){
 if(orderView!=='suggested')return '';
 const s=data.orderSuggestion,result=s?.result,pending=s?.request?.status==='pending',failed=s?.request?.status==='incomplete';
 const text=(result?tr('suggestion_from').replace('{time}',short(result.at))+(s.stale?' '+tr('suggestion_stale'):''):tr('suggestion_none'))+(pending?' '+tr('suggestion_pending'):failed?' '+tr('suggestion_failed')+(known(s.request.code)?tr('error_'+s.request.code):tr('chat_failed_generic')):'');
 const disabled=data.readOnly||pending||!data.agent?.connected||!data.tasks.some(t=>!['done','retired'].includes(t.state));
 return '<p>'+esc(text)+'</p><button type="button" class="btn ghost" id="suggest-order"'+(disabled?' disabled':'')+(data.agent?.connected?'':' title="'+esc(tr('agent_temporarily_unavailable'))+'"')+'>'+esc(tr(result?'suggest_again':'suggest_order'))+'</button>'+(result?.message?'<p class="suggestion-message">'+esc(result.message)+'</p>':'');
}
const sectionIds=section=>[...$('board').querySelectorAll('.task')].filter(c=>c.dataset.section===section).map(c=>c.dataset.taskId);
// The visible order applies at once; saving waits for a pause and keeps the revision the person saw.
const currentOrder=(data=flow.snapshot())=>globalThis.dayOrdering.manualIds(data.tasks.filter(t=>!['done','retired'].includes(t.state)),localOrder||data.taskOrder||[]);
function placeOrder(id,ids,to){return setOrder(globalThis.dayOrdering.move(currentOrder(),ids,id,to))}
function setOrder(next){const data=flow.snapshot(),order=currentOrder(data);if(next.join()===order.join())return false;if(localOrder===null)orderBase=data.revision;localOrder=next;render();clearTimeout(orderTimer);orderTimer=setTimeout(saveOrder,350);return true}
async function saveOrder(){if(orderSaving){orderTimer=setTimeout(saveOrder,150);return}const order=localOrder;if(!order)return;orderSaving=true;try{const p=await flow.request('order_update',{order},undefined,orderBase);orderBase=p?.result?.revision??flow.snapshot().revision}catch(error){localOrder=null;orderBase=null;notice(error.message);render();return}finally{orderSaving=false}if(localOrder===order){localOrder=null;orderBase=null;render()}}
// Blocked and blocking cards name their counterparts; a chip highlights those direct links.
const titles=(ids,data)=>ids.map(id=>data.tasks.find(r=>r.id===id)?.title||id).join(', ');
function dependencyChips(t,data){const blocks=data.tasks.filter(r=>r.blockedBy.includes(t.id)).map(r=>r.id),chip=(kind,count,names)=>'<button type="button" class="dependency-chip '+kind+'" data-id="'+t.id+'" data-action="dependencies" aria-pressed="false" aria-label="'+esc(names+' · '+tr('show_dependencies'))+'" title="'+esc(names)+'">'+esc(formatted(count,{n:kind==='blocked-label'?t.blockedBy.length:blocks.length}))+'</button>';return (t.blocked?chip('blocked-label','blocked_by_count',tr('blocked_prefix')+titles(t.blockedBy,data)):'')+(blocks.length?chip('blocking-label','blocks_count',tr('blocks_prefix')+titles(blocks,data)):'')}
const dependencyEdges=data=>dependencyFocus?data.tasks.flatMap(r=>r.blockedBy.filter(p=>p===dependencyFocus||r.id===dependencyFocus).map(p=>[p,r.id])):[];
const dependencyLines=document.createElementNS('http://www.w3.org/2000/svg','svg');dependencyLines.classList.add('dependency-lines');dependencyLines.setAttribute('aria-hidden','true');$('board').after(dependencyLines);
const dependencyResize=new ResizeObserver(()=>drawDependencies());
function showDependencies(data=flow.snapshot()){
 const edges=dependencyEdges(data),board=$('board');if(!edges.length)dependencyFocus=null;
 board.classList.toggle('dependency-focus',!!dependencyFocus);
 for(const card of board.querySelectorAll('[data-task-id]')){
  const id=card.dataset.taskId,role=id===dependencyFocus?'source':edges.some(([p,d])=>p===id&&d===dependencyFocus)?'prerequisite':edges.some(([p,d])=>d===id&&p===dependencyFocus)?'dependent':'';
  if((card.dataset.dependencyRole||'')!==role){if(role)card.dataset.dependencyRole=role;else delete card.dataset.dependencyRole;card.querySelector('.dependency-role')?.remove();if(role&&role!=='source')card.querySelector('.row-meta').insertAdjacentHTML('afterbegin','<span class="dependency-role">'+esc(tr('dependency_'+role))+'</span>')}
  for(const chip of card.querySelectorAll('[data-action=dependencies]'))if(chip.getAttribute('aria-pressed')!==String(role==='source'))chip.setAttribute('aria-pressed',String(role==='source'));
 }
 dependencyResize.disconnect();if(dependencyFocus){dependencyResize.observe(board);board.querySelectorAll('[data-dependency-role]').forEach(card=>dependencyResize.observe(card))}
 drawDependencies(data);
}
// Lines join cards across columns, or run in a side gutter wide enough to read; phone widths keep the highlight only.
function drawDependencies(data=flow.snapshot()){
 const board=$('board'),svg=dependencyLines,edges=dependencyEdges(data);svg.replaceChildren();svg.style.display='none';
 if(!edges.length||board.clientWidth<600)return;
 const box=board.getBoundingClientRect(),area=svg.parentElement.getBoundingClientRect(),cards=[...board.querySelectorAll('[data-task-id]')].map(c=>c.getBoundingClientRect()).filter(r=>r.width);
 const rect=id=>{const card=board.querySelector('[data-task-id="'+id+'"]');return card?.getClientRects().length?card.getBoundingClientRect():null};
 const room=r=>r.left-Math.max(0,...cards.filter(c=>c.right<=r.left).map(c=>c.right)),anchor=r=>r.top-box.top+Math.min(r.height/2,30);
 let gutter=0;const paths=[];
 for(const [p,d] of edges){const a=rect(p),b=rect(d);if(!a||!b)continue;const y1=anchor(a),y2=anchor(b);
  if(a.right<=b.left||b.right<=a.left){const forward=a.right<=b.left,x1=(forward?a.right:a.left)-box.left,x2=(forward?b.left:b.right)-box.left,dx=Math.max(24,Math.abs(x2-x1)/2)*(forward?1:-1);paths.push(`M${x1} ${y1}C${x1+dx} ${y1} ${x2-dx} ${y2} ${x2} ${y2}`);continue}
  const space=Math.min(room(a),room(b));if(space<14)continue;
  const depth=Math.min(space-4,12+6*gutter++),x=Math.min(a.left,b.left)-box.left,s=Math.sign(y2-y1)||1,r=Math.min(6,depth/2,Math.abs(y2-y1)/2);
  paths.push(`M${x} ${y1}H${x-depth+r}Q${x-depth} ${y1} ${x-depth} ${y1+s*r}V${y2-s*r}Q${x-depth} ${y2} ${x-depth+r} ${y2}H${x}`);
 }
 if(!paths.length)return;
 Object.assign(svg.style,{display:'block',left:box.left-area.left+'px',top:box.top-area.top+'px',width:box.width+'px',height:box.height+'px'});
 svg.innerHTML='<defs><marker id="dependency-arrow" viewBox="0 0 6 6" refX="5.5" refY="3" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto"><path d="M0 0L6 3L0 6z"/></marker></defs>'+paths.map(d=>'<path d="'+d+'" marker-end="url(#dependency-arrow)"/>').join('');
}
function toggleDependencies(id){dependencyFocus=dependencyFocus===id?null:id;showDependencies()}
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&dependencyFocus&&!document.querySelector('dialog[open]'))toggleDependencies(dependencyFocus)});
function render(data=flow.snapshot()) {
 if(drag?.active)return;
 const active=document.activeElement,focusTask=active?.dataset?.id,focusAction=active?.dataset?.action,focusProposal=active?.dataset?.proposal,reviewAction=active?.dataset?.reviewAction;
 const openIds=[...$('reviews').querySelectorAll('details[open]')].map(d=>d.dataset.proposal);
 const proposals=data.proposals;
 const newlyRelevant=proposals.find(p=>['review','unavailable'].includes(p.phase)&&!['review','unavailable'].includes(phaseCache.get(p.id)));
 phaseCache=new Map(proposals.map(p=>[p.id,p.phase]));
 $('task-count').textContent=data.tasks.filter(t=>!['done','retired'].includes(t.state)).length+tr("open");
 $('context-open').textContent=data.context.availableMinutes!==null?data.context.availableMinutes+tr("min_available"):tr("my_context");
 $('order-note').innerHTML=orderNote(data);
 if(expanded&&!data.tasks.some(t=>t.id===expanded&&!t.pending&&!['done','retired'].includes(t.state)))expanded=null;
 if(titleEdit&&!data.tasks.some(t=>t.id===titleEdit.id&&!['done','retired'].includes(t.state)))titleEdit=null;
 const typing=typingState();rendering=true;
 morphBoard([['active',tr("active")],['todo',tr("to_do")],['snoozed',tr("snoozed")]].map(([state,label])=>{
  const rows=data.tasks.filter(t=>t.state===state),sections=globalThis.dayOrdering.arrange(rows,orderView,{order:localOrder||data.taskOrder||[],suggestion:data.orderSuggestion?.result});
  const items=sections.flatMap(s=>[...(orderView==='group'?[{key:'heading:'+s.key,html:groupHeading(s)}]:[]),...s.tasks.map(t=>({id:t.id,html:taskRow(t,s,data,proposals.find(p=>p.intent.taskId===t.id))}))]);
  if(!rows.length)items.push({key:'empty',html:'<p class="lane-empty">'+esc(tr('lane_empty'))+'</p>'});
  return {state,head:'<span class="state-dot" aria-hidden="true"></span><h2>'+label+'</h2><span class="lane-count">'+rows.length+'</span>',items};
 }),data);
 // A focused control whose card moved keeps its focus.
 if(active?.isConnected&&active!==document.activeElement&&$('board').contains(active))active.focus({preventScroll:true});
 restoreTyping(typing);rendering=false;
 showDependencies(data);
 const closed=data.tasks.filter(t=>['done','retired'].includes(t.state));$('closed-count').textContent=closed.length;$('closed').innerHTML=closed.map(t=>'<p class="closed-row"><span class="closed-title">'+esc(t.title)+'</span> · '+(t.state==='done'?tr("completed"):tr("retired"))+'<button type="button" class="link-button" data-reopen-id="'+t.id+'"'+(data.readOnly?' disabled':'')+' aria-label="'+esc(tr('reopen')+': '+t.title)+'">'+esc(tr('reopen'))+'</button></p>').join('');
 // Only changes waiting for the person appear here: a retire from the conversation, or one an older
 // version left unapplied. Direct actions never wait.
 const shown=proposals.filter(p=>p.phase!=='evaluating');
 $('review-area').hidden=!shown.length;$('review-count').textContent=shown.length===1?tr("1_pending_change"):shown.length+tr("pending_changes");
 if(!shown.some(p=>p.id===expandedProposal))expandedProposal=shown[0]?.id||null;
 $('reviews').innerHTML=shown.map(p=>{
  const t=data.tasks.find(t=>t.id===p.intent.taskId),isOpen=!collapsedProposals.has(p.id)&&(openIds.includes(p.id)||expandedProposal===p.id);
  const adds=creates(p.intent.action),retire=p.intent.action==='retire';
  const button=(action,label,kind='btn')=>'<button type="button" class="'+kind+'" data-proposal="'+p.id+'" data-generation="'+p.generation+'" data-review-action="'+action+'">'+esc(label)+'</button>';
  const buttons=button('cancel',tr('choice_cancel_change'),'btn ghost')+(p.phase==='review'?button('continue',retire?tr('yes_retire'):tr('keep_my_decision'),'btn decision-confirm'+(retire?' danger':' primary')):button('retry',tr(retire?'choice_ask_again':adds?'choice_create_now':'choice_apply_now'),'btn primary'));
  const facts=p.phase==='review'?p.concerns.map(c=>'<li>'+esc(concernMessage(c))+'</li>').join(''):'';
  const reason=p.phase==='stale'?tr('stale_explanation'):p.phase==='invalid'?(known(p.error_code)?tr('error_'+p.error_code):tr('reason_not_applied')):p.phase==='unavailable'?tr('reason_left_waiting'):'';
  return '<details class="decision" data-proposal="'+p.id+'" data-generation="'+p.generation+'" '+(isOpen?'open':'')+'><summary><span><strong>'+esc((actionLabel[p.intent.action]||tr(p.intent.action))+' · '+(t?.title||p.intent.payload?.title||''))+'</strong><span class="decision-phase">'+esc(phaseLabel[p.phase]||'')+'</span></span></summary><div class="decision-body"><p class="decision-pending">'+esc(tr(adds?'not_created_yet':'not_applied_yet'))+'</p>'+(facts?'<ul class="decision-facts">'+facts+'</ul>':'')+(reason?'<p class="decision-error">'+esc(reason)+'</p>':'')+'<div class="decision-actions">'+buttons+'</div></div></details>';
 }).join('');
 const last=data.lastFact;
 // The first view only remembers the latest change; later ones are announced as they arrive.
 if(lastSeenEvent===undefined)lastSeenEvent=last?.id??null;else if(last&&last.id!==lastSeenEvent){lastSeenEvent=last.id;announce(last,data)}
 if(active&&!active.isConnected){requestAnimationFrame(()=>{if(document.querySelector('dialog[open]'))return;const current=document.activeElement;if(current&&current!==document.body&&current!==active&&current.isConnected)return;let target;if(focusProposal)target=$('reviews').querySelector('[data-proposal="'+focusProposal+'"][data-review-action="'+reviewAction+'"]');if(!target&&focusTask)target=$('board').querySelector('[data-id="'+focusTask+'"][data-action="'+focusAction+'"]')||$('board').querySelector('[data-id="'+focusTask+'"][data-action="title"]');if(!target)target=shown.length?$('review-title'):$('list-view');target.focus({preventScroll:true})})}
 if(newlyRelevant)notice(tr("a_pending_change_needs_review"));
 if(menu){const t=data.tasks.find(t=>t.id===menu.id);if(!t||['done','retired'].includes(t.state))closeMenu(false);else anchorOf(menu.id,menu.action)?.setAttribute('aria-expanded','true')}
}
// Confirms the latest saved change, how long it took to show, and Undo when it can be undone.
function announce(last,data){const t=data.tasks.find(t=>t.id===last.taskId),action=aliases[last.action]||last.action;
 const gesture=!last.undoOf&&gestureOf.get(last.proposalId);
 if(gesture){toast(gesture.notes.join(' · ')||tr('order_saved'),{undo:gesture.id,detail:[formatted('toast_ms',{ms:Math.max(1,Math.round(gesture.shown-gesture.started))}),gesture.notes.length&&data.agent?.connected?tr('toast_reviewed_later'):''].filter(Boolean).join(' · ')});return}
 const text=last.undoOf?tr('change_undone'):action==='order'?tr('order_saved'):action==='snooze'&&t?.until?tr('snoozed_until').replace('{time}',short(t.until))+' · '+t.title:[action==='declaration'&&t?tr('details_saved'):actionLabel[action]||tr(last.action),t?.title||(action==='declaration'?tr('my_context'):'')].filter(Boolean).join(' · ');
 // The time shown is from the click until the change was on screen.
 const timing=timings.get(last.proposalId);timings.delete(last.proposalId);if(timing?.before&&last.undoable)undoViews.set(String(last.id),{...timing.before,taskId:last.taskId});const ms=timing&&action!=='order'?Math.max(1,Math.round((timing.shown??performance.now())-timing.started)):null;
 toast(text,{undo:last.undoable&&!last.undoOf?last.id:null,detail:[ms===null?'':formatted('toast_ms',{ms}),last.reviewStatus==='deferred'&&data.agent?.connected?tr('toast_reviewed_later'):''].filter(Boolean).join(' · ')})}
async function request(action,payload,key,expectedRevision,gesture){return safely(async()=>{const p=await flow.request(action,payload,key,expectedRevision,gesture);lastRequestedProposal=p?.id||null;if(p?.phase==='unchanged')notice(p.superseded?.length?tr("the_pending_change_was_discarded_the_respons"):tr("there_are_no_new_changes_to_apply"));else if(p&&p.phase!=='applied')notice(tr('a_pending_change_needs_review'));return p;});}
function openDialog(dialog,origin,id,action){origins.set(dialog,{origin,id,action});dialog.returnValue='cancel';dialog.showModal();}
function restoreDialog(dialog){const saved=origins.get(dialog);origins.delete(dialog);requestAnimationFrame(()=>{if(document.querySelector('dialog[open]'))return;let target=saved?.origin;if(!target?.isConnected||!target.getClientRects().length)target=saved?.id?document.querySelector('[data-id="'+saved.id+'"][data-action="'+saved.action+'"]'):null;if(!target?.getClientRects().length)target=saved?.id?$('list-view'):$('chat-toggle');target.focus({preventScroll:true});})}
// Preserve v3 outside-dismiss and keyboard behavior for all dialogs.
function registerDialog(dialog){
 let down=false;const outside=e=>{const r=dialog.getBoundingClientRect();return e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom};
 dialog.addEventListener('pointerdown',e=>down=e.target===dialog&&outside(e));dialog.addEventListener('pointerup',e=>{if(down&&e.target===dialog&&outside(e))dialog.close('cancel');down=false});dialog.addEventListener('pointercancel',()=>down=false);
 dialog.addEventListener('keydown',e=>{if(e.key!=='Tab')return;const items=[...dialog.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href],summary,[tabindex]:not([tabindex="-1"])')].filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden'),first=items[0],last=items.at(-1);if(!first){e.preventDefault();return}if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}});
 dialog.addEventListener('close',()=>{down=false;restoreDialog(dialog)});
}
for(const dialog of document.querySelectorAll('[data-light-dismiss]'))registerDialog(dialog);
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close('cancel'));
function ambiguous(d){for(let n=-180;n<=180;n+=30){if(!n)continue;const other=new Date(d.getTime()+n*60000);if(localValue(other)===localValue(d))return true}return false}
// Editing stays in the row: the title in place, group and deadline from one-level menus, and the
// rest in the row expanded below it. Each field saves by itself; nothing opens over anything else.
let expanded=null,titleEdit=null,picker=null,savedNote=null,retireAsk=null,rendering=false;
// What the person typed, by task and field, with the value it started from. A draft survives
// every redraw and a conflict; it goes only once saved or discarded.
const drafts=new Map(),fieldTimers=new Map(),draftKey=(id,field)=>id+'|'+field;
const FIELD_ACTIONS={title:'task_update',context:'continuity_update',next:'continuity_update',remainingMinutes:'continuity_update',awaiting:'continuity_update'};
const same=(a,b)=>(a??null)===(b??null);
const draftValue=(t,field)=>{const d=drafts.get(draftKey(t.id,field));return d?d.value:field==='remainingMinutes'?(t.remainingMinutes??''):t[field]};
function startDraft(id,field,value){const t=task(id);if(!t)return null;const k=draftKey(id,field);let d=drafts.get(k);if(!d){d={id,field,value,base:t[field],conflict:null,error:''};drafts.set(k,d)}d.value=value;d.error='';return d}
function normalized(d){
 if(d.field==='title'){const v=String(d.value).trim();if(!v||v.length>500)throw Error(tr('error_invalid_title'));return v}
 if(d.field==='remainingMinutes'){const raw=String(d.value).trim();if(!raw)return null;const n=Number(raw);if(!Number.isFinite(n)||n<0||n>100000)throw Error(tr('error_invalid_estimate'));return n}
 return d.field==='awaiting'?!!d.value:String(d.value)}
function queueSave(id,field){const k=draftKey(id,field);clearTimeout(fieldTimers.get(k));fieldTimers.set(k,setTimeout(()=>saveDraft(id,field),700))}
// A field saves against the latest state read. If someone else changed that same field since the
// person started typing, nothing is sent: the row says so and keeps the draft. A service that saw a
// newer state than this page reads it again and, if this field is still untouched, saves once more.
async function saveDraft(id,field){
 const k=draftKey(id,field),d=drafts.get(k);clearTimeout(fieldTimers.get(k));fieldTimers.delete(k);if(!d||d.saving||d.conflict)return;
 const t=task(id);if(!t||flow.snapshot().readOnly)return;
 let value;try{value=normalized(d)}catch(error){d.error=error.message;savedNote=null;render();return}
 if(same(value,t[field])){drafts.delete(k);if(field==='title'&&titleEdit?.id===id)titleEdit=null;render();return}
 if(!same(d.base,t[field])){d.conflict={current:t[field]};savedNote=null;if(field==='title')titleEdit={id};render();return}
 d.saving=true;const sent=d.value;
 try{await flow.request(FIELD_ACTIONS[field],{uuid:id,[field]:value});d.saving=false;d.retried=false;
  if(drafts.get(k)===d){if(d.value===sent)drafts.delete(k);else{d.base=value;queueSave(id,field)}}
  savedNote={id,field};render()}
 catch(error){d.saving=false;
  if(error.code==='stale_revision'){await flow.refresh().catch(()=>{});const now=task(id);if(now&&same(d.base,now[field])&&!d.retried){d.retried=true;return saveDraft(id,field)}d.conflict={current:now?.[field]};if(field==='title')titleEdit={id}}
  else{d.error=error.message;if(field==='title')titleEdit={id}}
  savedNote=null;render()}}
const flush=()=>{for(const d of [...drafts.values()])if(!d.conflict&&!d.error&&d.field!=='title')saveDraft(d.id,d.field)};
addEventListener('pagehide',flush);document.addEventListener('visibilitychange',()=>{if(document.hidden)flush()});
function fieldNote(t,field){const d=drafts.get(draftKey(t.id,field));if(!d)return '';if(d.error)return '<p class="field-error" role="alert">'+esc(d.error)+'</p>';if(!d.conflict)return '';
 const cur=d.conflict.current,shown=field==='awaiting'?tr(cur?'value_yes':'value_no'):cur===null||cur===undefined||cur===''?tr('value_empty'):String(cur);
 const b=(kind,label,cls)=>'<button type="button" class="btn '+cls+'" data-conflict="'+kind+'" data-conflict-id="'+t.id+'" data-conflict-field="'+field+'">'+esc(tr(label))+'</button>';
 return '<div class="field-conflict" role="alert"><p>'+esc(tr('conflict_changed').replace('{value}',shown))+'</p><div class="conflict-actions">'+b('mine','conflict_keep_mine','primary')+b('theirs','conflict_use_current','ghost')+'</div></div>'}
function titleCell(t,data){
 if(titleEdit?.id===t.id)return '<span class="title-editor"><input class="title-edit" data-id="'+t.id+'" data-field="title" maxlength="500" autocomplete="off" value="'+esc(draftValue(t,'title'))+'" aria-label="'+esc(tr('edit_title')+': '+t.title)+'"'+(data.readOnly?' disabled':'')+'><span class="edit-hint">'+esc(tr('title_edit_hint'))+'</span>'+fieldNote(t,'title')+'</span>';
 return '<span class="title-wrap">'+suggestionMark(t,data)+'<button type="button" class="task-title" data-id="'+t.id+'" data-action="title" aria-label="'+esc(tr('edit_title')+': '+t.title)+'" title="'+esc(tr('edit_title_hint'))+'">'+esc(t.title)+'</button></span>'}
function startTitleEdit(id){const t=task(id),data=flow.snapshot();if(!t||t.pending||data.readOnly||['done','retired'].includes(t.state))return;closeMenu(false);titleEdit={id};render();const input=$('board').querySelector('.title-edit[data-id="'+id+'"]');if(input){input.focus({preventScroll:true});input.select()}}
function commitTitle(id){const d=drafts.get(draftKey(id,'title'));if(d?.conflict||d?.saving)return;titleEdit=null;
 // An emptied title keeps the saved one, as Esc does.
 if(d&&!String(d.value).trim())drafts.delete(draftKey(id,'title'));render();restoreTitleFocus(id);if(drafts.has(draftKey(id,'title')))saveDraft(id,'title')}
function cancelTitle(id){drafts.delete(draftKey(id,'title'));titleEdit=null;render();restoreTitleFocus(id)}
const restoreTitleFocus=id=>$('board').querySelector('[data-id="'+id+'"][data-action="title"]')?.focus({preventScroll:true});
// Expanded details: continuity, links and source, saved field by field.
const relationTitle=(o,data)=>esc(o.title)+(['done','retired'].includes(o.state)?' · '+esc(tr(o.state==='done'?'completed':'retired')):'');
function relationRows(ids,kind,t,data){const rows=ids.map(id=>data.tasks.find(x=>x.id===id)).filter(Boolean);
 return rows.length?rows.map(o=>'<div class="rel-item"><button type="button" class="rel-go" data-goto="'+o.id+'" title="'+esc(tr('go_to_task'))+'">'+relationTitle(o,data)+'</button>'+(data.readOnly||t.pending?'':'<button type="button" class="rel-remove" data-unlink="'+kind+':'+o.id+'" data-unlink-task="'+t.id+'" aria-label="'+esc(tr('remove_link')+': '+o.title)+'" title="'+esc(tr('remove_link'))+'"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10M17 7 7 17"/></svg></button>')+'</div>').join(''):'<p class="rel-none">'+esc(tr('none'))+'</p>'}
const pickerInput=(t,kind,data)=>data.readOnly||t.pending?'':'<div class="picker"><input type="text" class="picker-input" data-search="'+kind+'" data-picker-task="'+t.id+'" autocomplete="off" placeholder="'+esc(tr(kind==='dep'?'add_prerequisite':'add_related'))+'" aria-label="'+esc(tr(kind==='dep'?'add_prerequisite':'add_related'))+'" aria-expanded="false" aria-controls="picker-'+kind+'-'+t.id+'"><div class="picker-list" id="picker-'+kind+'-'+t.id+'" role="listbox" hidden></div></div>';
function expandView(t,data){const off=data.readOnly||t.pending?' disabled':'',id=part=>'x-'+part+'-'+t.id,dependents=data.tasks.filter(r=>r.dependsOn.includes(t.id)).map(r=>r.id);
 const field=(name,label,control)=>'<div class="xfield"><label class="xlabel" for="'+id(name)+'">'+esc(tr(label))+'</label>'+control+fieldNote(t,name)+'</div>';
 const source=[[tr('source_label'),t.source&&t.source!=='general'?t.source:null],[tr('person_label'),t.person],[tr('reference_label'),t.reference],[tr('observed_label'),t.observedAt?full(t.observedAt):null],[tr('captured_label'),t.capturedAt?full(t.capturedAt):null],[tr('created_label'),t.createdAt?full(t.createdAt):null]].filter(([,v])=>v);
 const note=savedNote?.id===t.id?tr('saved_field').replace('{field}',tr('field_'+savedNote.field)):'';
 const foot=retireAsk===t.id?'<div class="inline-confirm" role="alert"><span>'+esc(tr('retire_this_responsibility_it_will_be_kept_s'))+'</span><button type="button" class="btn danger" data-x="retire-yes">'+esc(tr('yes_retire'))+'</button><button type="button" class="btn" data-x="retire-no">'+esc(tr('cancel'))+'</button></div>'
  :'<button type="button" class="btn ghost danger" data-x="retire"'+off+'>'+esc(tr('retire_7ad0e0'))+'</button><button type="button" class="btn ghost" data-x="duplicate"'+off+'>'+esc(tr('duplicate'))+'</button><button type="button" class="btn ghost" data-x="close">'+esc(tr('close_details'))+'</button><span class="saved-note" aria-live="polite">'+(note?checkIcon+esc(note):'')+'</span>';
 return '<div class="expand" data-expand="'+t.id+'"><div class="xgrid">'
  +field('context','where_i_left_off','<textarea id="'+id('context')+'" data-id="'+t.id+'" data-field="context" rows="2" maxlength="2000" placeholder="'+esc(tr('where_i_left_off_placeholder'))+'"'+off+'>'+esc(draftValue(t,'context'))+'</textarea>')
  +field('next','next_step','<input type="text" id="'+id('next')+'" data-id="'+t.id+'" data-field="next" maxlength="2000" autocomplete="off" placeholder="'+esc(tr('next_step_placeholder'))+'" value="'+esc(draftValue(t,'next'))+'"'+off+'>')
  +field('remainingMinutes','remaining_minutes','<input type="number" id="'+id('remainingMinutes')+'" data-id="'+t.id+'" data-field="remainingMinutes" min="0" step="any" inputmode="decimal" placeholder="'+esc(tr('not_estimated'))+'" value="'+esc(draftValue(t,'remainingMinutes'))+'"'+off+'>')
  +'<div class="xfield xswitch"><label class="switch"><input type="checkbox" data-id="'+t.id+'" data-field="awaiting"'+(draftValue(t,'awaiting')?' checked':'')+off+'><span>'+esc(tr('awaiting_label'))+'</span></label>'+fieldNote(t,'awaiting')+'</div>'
  +'<div class="xfield"><span class="xlabel">'+esc(tr('requires_label'))+'</span><div class="rel-list">'+relationRows(t.dependsOn,'dep',t,data)+'</div>'+pickerInput(t,'dep',data)+'</div>'
  +'<div class="xfield"><span class="xlabel">'+esc(tr('required_by_label'))+'</span><div class="rel-list">'+relationRows(dependents,'dependent',t,data)+'</div></div>'
  +'<div class="xfield"><span class="xlabel">'+esc(tr('related_label'))+'</span><div class="rel-list">'+relationRows(t.relatedTo,'rel',t,data)+'</div>'+pickerInput(t,'rel',data)+'</div>'
  +'<div class="xfield"><span class="xlabel">'+esc(tr('source_and_dates'))+'</span><dl class="meta-grid">'+source.map(([k,v])=>'<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>').join('')+'</dl>'+(t.attachmentIds||[]).map(a=>'<a href="/api/attachments/'+esc(a)+'" target="_blank" rel="noopener">'+esc(tr('source_image'))+'</a>').join(' ')+'</div>'
  +'</div><div class="expand-foot">'+foot+'</div></div>'}
function toggleExpand(id,open=expanded!==id){const t=task(id);if(open&&(!t||t.pending))return;flush();expanded=open?id:null;retireAsk=null;savedNote=null;render();
 const card=$('board').querySelector('[data-task-id="'+id+'"]');// An instant scroll: a smooth one would keep scrolling and close a menu opened right after.
 if(open){card?.querySelector('.expand')?.scrollIntoView({block:'nearest'});card?.querySelector('[data-field="context"]')?.focus({preventScroll:true})}else restoreTitleFocus(id)}
// Prerequisites offered by the picker never close a loop: a task cannot wait for one that waits for it.
function waitsOn(from,to,data){const seen=new Set(),walk=id=>{if(id===to)return true;if(seen.has(id))return false;seen.add(id);return (data.tasks.find(x=>x.id===id)?.dependsOn||[]).some(walk)};return walk(from)}
function showPicker(input){const data=flow.snapshot(),id=input.dataset.pickerTask,kind=input.dataset.search,t=data.tasks.find(x=>x.id===id),list=input.nextElementSibling;if(!t||!list)return;picker={id,kind,value:input.value};
 const q=input.value.trim().toLocaleLowerCase(),taken=new Set([t.id,...(kind==='dep'?t.dependsOn:t.relatedTo)]);
 const found=data.tasks.filter(o=>!['done','retired'].includes(o.state)&&!o.pending&&!taken.has(o.id)&&!(kind==='dep'&&waitsOn(o.id,t.id,data))&&(!q||o.title.toLocaleLowerCase().includes(q))).slice(0,8);
 list.innerHTML=found.map(o=>'<button type="button" class="menu-item" role="option" data-pick="'+kind+':'+o.id+'" data-pick-task="'+t.id+'"><span class="priority-pill" data-priority="'+esc(o.priority)+'">'+esc(tr('priority_'+o.priority))+'</span><span class="menu-label">'+esc(o.title)+'</span></button>').join('')||'<p class="rel-none">'+esc(tr('no_matches'))+'</p>';
 list.hidden=false;input.setAttribute('aria-expanded','true')}
function hidePickers(except){for(const list of $('board').querySelectorAll('.picker-list:not([hidden])'))if(list!==except){list.hidden=true;list.previousElementSibling?.setAttribute('aria-expanded','false')}}
function link(kind,taskId,otherId,add){const data=flow.snapshot(),t=data.tasks.find(x=>x.id===taskId),o=data.tasks.find(x=>x.id===otherId);if(!t||!o)return;
 if(kind==='dep')request('dependency_update',{uuid:t.id,dependsOn:add?[...t.dependsOn,o.id]:t.dependsOn.filter(x=>x!==o.id)});
 else if(kind==='dependent')request('dependency_update',{uuid:o.id,dependsOn:o.dependsOn.filter(x=>x!==t.id)});
 else request('related_update',{uuid:t.id,relatedTo:add?[...t.relatedTo,o.id]:t.relatedTo.filter(x=>x!==o.id)})}
function duplicate(t){request('task_add',{title:t.title,priority:t.priority,...(t.group?{group:t.group}:{}),...(t.due?{due:t.due}:{})})}
function goTo(id){const card=$('board').querySelector('[data-task-id="'+id+'"]');if(!card){notice(tr('task_not_on_board'));return}card.scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});card.classList.remove('flash');void card.offsetWidth;card.classList.add('flash');card.querySelector('[data-action="title"]')?.focus({preventScroll:true})}
// Clicks inside a row that edit it in place. Returns true when it handled the click.
function inlineClick(e){const el=e.target;
 const c=el.closest('[data-conflict]');if(c){const {conflict,conflictId:id,conflictField:field}=c.dataset,k=draftKey(id,field),d=drafts.get(k);if(d){if(conflict==='mine'){d.base=d.conflict.current;d.conflict=null;d.retried=false;if(field==='title'){titleEdit=null;restoreTitleFocus(id)}saveDraft(id,field)}else{drafts.delete(k);if(field==='title')titleEdit=null;render()}}return true}
 const pick=el.closest('[data-pick]');if(pick){const [kind,other]=pick.dataset.pick.split(':');picker=null;link(kind,pick.dataset.pickTask,other,true);return true}
 const un=el.closest('[data-unlink]');if(un){const [kind,other]=un.dataset.unlink.split(':');link(kind,un.dataset.unlinkTask,other,false);return true}
 const go=el.closest('[data-goto]');if(go){goTo(go.dataset.goto);return true}
 const x=el.closest('[data-x]');if(x){const id=x.closest('[data-expand]').dataset.expand,act=x.dataset.x;
  if(act==='close')toggleExpand(id,false);else if(act==='duplicate'){const t=task(id);if(t)duplicate(t)}
  else if(act==='retire'){retireAsk=id;render();$('board').querySelector('[data-expand="'+id+'"] [data-x="retire-no"]')?.focus({preventScroll:true})}
  else if(act==='retire-no'){retireAsk=null;render();$('board').querySelector('[data-expand="'+id+'"] [data-x="retire"]')?.focus({preventScroll:true})}
  else if(act==='retire-yes'){retireAsk=null;expanded=null;request('retire',{taskId:id})}
  return true}
 if(el.closest('.expand,.title-editor,input,textarea,select,label,a'))return true;
 return false}
// The board redraws when state changes; whatever is being typed keeps its text, caret and focus.
function typingState(){const a=document.activeElement;if(!a||!$('board').contains(a)||!a.matches('input,textarea'))return null;let start=null,end=null;try{start=a.selectionStart;end=a.selectionEnd}catch{}return {id:a.dataset.id||a.dataset.pickerTask,field:a.dataset.field,search:a.dataset.search,start,end,scroll:a.scrollTop,value:a.value,picking:!!a.nextElementSibling&&!a.nextElementSibling.hidden}}
function restoreTyping(s){if(!s)return;const node=$('board').querySelector(s.search?'[data-search="'+s.search+'"][data-picker-task="'+s.id+'"]':'[data-field="'+s.field+'"][data-id="'+s.id+'"]');if(!node)return;
 if(s.search)node.value=s.value;node.focus({preventScroll:true});node.scrollTop=s.scroll;if(s.start!==null)try{node.setSelectionRange(s.start,s.end)}catch{}if(s.search&&s.picking)showPicker(node)}
$('board').addEventListener('input',e=>{const {field,id,search}=e.target.dataset||{};if(search){showPicker(e.target);return}if(!field||!id||field==='awaiting')return;startDraft(id,field,e.target.value);if(field!=='title')queueSave(id,field)});
$('board').addEventListener('change',e=>{const {field,id}=e.target.dataset||{};if(field==='awaiting'&&id){startDraft(id,'awaiting',e.target.checked);saveDraft(id,'awaiting')}});
$('board').addEventListener('focusin',e=>{if(e.target.dataset?.search){hidePickers(e.target.nextElementSibling);showPicker(e.target)}});
$('board').addEventListener('focusout',e=>{if(rendering)return;const {field,id,search}=e.target.dataset||{};
 if(search){setTimeout(()=>{const list=e.target.nextElementSibling;if(list&&!list.contains(document.activeElement)&&document.activeElement!==e.target){list.hidden=true;e.target.setAttribute('aria-expanded','false');if(picker?.id===e.target.dataset.pickerTask)picker=null}},150);return}
 if(!field||!id)return;if(field==='title'){if(e.relatedTarget?.closest?.('[data-conflict]'))return;commitTitle(id)}else if(drafts.has(draftKey(id,field)))saveDraft(id,field)});
$('board').addEventListener('keydown',e=>{const {field,id,search}=e.target.dataset||{};
 if(field==='title'&&id){if(e.key==='Enter'&&!e.isComposing){e.preventDefault();commitTitle(id)}else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();cancelTitle(id)}return}
 if(search&&e.key==='Enter'){e.preventDefault();e.target.nextElementSibling?.querySelector('[data-pick]')?.click();return}
 if(search&&e.key==='Escape'&&!e.target.nextElementSibling?.hidden){e.preventDefault();e.stopPropagation();hidePickers();return}
 if(field&&id&&e.key==='Enter'&&e.target.tagName==='INPUT'&&!e.isComposing){e.preventDefault();saveDraft(id,field)}});
// Conflict buttons keep the focus where it is, so pressing one never commits the field first.
$('board').addEventListener('mousedown',e=>{if(e.target.closest('[data-conflict],[data-pick]'))e.preventDefault()});
document.addEventListener('keydown',e=>{if(e.key!=='Escape'||!expanded||menu||document.querySelector('dialog[open]')||titleEdit)return;if(!$('board').querySelector('[data-expand="'+expanded+'"]'))return;e.preventDefault();toggleExpand(expanded,false)});
$('closed').onclick=e=>{const b=e.target.closest('[data-reopen-id]');if(b)request('task_reopen',{uuid:b.dataset.reopenId})};
// One menu at a time and one level deep, never over a dialog: priority from its chip, snooze from the
// clock, everything else from ⋯, a right click or a long press. On a phone it opens as a sheet at the bottom.
let menu=null,swallowClick=false;
const sheetMedia=matchMedia('(max-width:600px)');
const anchorOf=(id,action)=>$('board').querySelector('[data-id="'+id+'"][data-action="'+action+'"]');
const menuItem=(act,label,{value='',icon='',kind='',checked=null,disabled=false,hint='',title=''}={})=>'<button type="button" class="menu-item'+(kind?' '+kind:'')+'" role="'+(checked===null?'menuitem':'menuitemradio')+'"'+(checked===null?'':' aria-checked="'+checked+'"')+' data-menu-act="'+act+'" data-value="'+esc(value)+'"'+(title?' title="'+esc(title)+'"':'')+(disabled?' disabled':'')+'>'+icon+'<span class="menu-label">'+label+'</span>'+(hint?'<span class="menu-hint">'+esc(hint)+'</span>':'')+(checked?'<span class="tick">'+checkIcon+'</span>':'')+'</button>';
function priorityMenu(t,data){return '<p class="menu-title">'+esc(tr('priority'))+'</p>'+PRIORITIES.map(p=>menuItem('priority','<span class="priority-pill" data-priority="'+p+'">'+esc(tr('priority_'+p))+'</span>',{value:p,checked:t.priority===p,disabled:data.readOnly||t.pending})).join('')}
// Snooze presets (P7). Each one shows the exact time it saves, in the profile's zone, and that same
// value is sent. A day whose 9:00 does not exist or repeats there is left out rather than guessed.
const SNOOZE_PRESETS=[['snooze_in_1_hour',{hours:1}],['snooze_in_3_hours',{hours:3}],['snooze_tomorrow',{days:1}],['snooze_in_2_days',{days:2}],['snooze_in_1_week',{days:7}]];
function snoozeTime({hours,days}){if(hours)return new Date(Math.ceil((NOW.getTime()+hours*3600000)/60000)*60000);
 const day=new Date(localValue(NOW).slice(0,10)+'T00:00Z');day.setUTCDate(day.getUTCDate()+days);const wall=day.toISOString().slice(0,10)+'T09:00',d=parseLocal(wall);
 return localValue(d)===wall&&!ambiguous(d)?d:null}
function snoozeMenu(t,data){const off=data.readOnly||t.pending,today=localValue(NOW).slice(0,10),browserZone=Intl.DateTimeFormat().resolvedOptions().timeZone;
 const clock=(d,withDay)=>new Intl.DateTimeFormat(formatLocale(),{...(withDay?{weekday:'short',day:'numeric',month:'short'}:{}),hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone}).format(d);
 const items=SNOOZE_PRESETS.map(([key,span])=>{const d=snoozeTime(span);if(!d)return '';const late=t.due&&d>new Date(t.due);
  return menuItem('snooze',esc(tr(key))+(late?'<span class="menu-warn">'+esc(tr('snooze_after_deadline'))+'</span>':''),{value:d.toISOString(),hint:clock(d,key!=='snooze_tomorrow'&&localValue(d).slice(0,10)!==today),title:full(d),disabled:off})}).join('');
 return '<p class="menu-title">'+esc(tr('snooze_until'))+'</p>'+items+(browserZone!==zone?'<p class="menu-note">'+esc(tr('snooze_zone_note').replace('{zone}',zone))+'</p>':'')
  +(t.state==='snoozed'?'<div class="menu-sep" role="separator"></div>'+menuItem('todo',esc(tr('resume_now')),{disabled:off}):'')}
function moreMenu(t,data){const off=data.readOnly||t.pending,card=$('board').querySelector('[data-task-id="'+t.id+'"]'),ids=card?.querySelector('.drag-handle')?sectionIds(card.dataset.section):[],i=ids.indexOf(t.id);
 return '<p class="menu-title task-name">'+esc(t.title)+'</p>'+(t.state!=='todo'?menuItem('todo',esc(tr('back_to_to_do')),{disabled:off}):'')
  +menuItem('edit-title',esc(tr('edit_title')),{icon:editIcon,disabled:off})+menuItem('details',esc(tr(expanded===t.id?'hide_details':'more_details')),{icon:arrowIcon,disabled:t.pending})
  +menuItem('set-due',esc(tr(t.due?'change_deadline':'set_deadline')),{icon:calendarIcon,disabled:off})
  +(i>0?menuItem('move',esc(tr('move_up')),{value:i-1}):'')+(i>=0&&i<ids.length-1?menuItem('move',esc(tr('move_down')),{value:i+1}):'')
  +menuItem('duplicate',esc(tr('duplicate')),{icon:copyIcon,disabled:off})
  +'<div class="menu-sep" role="separator"></div>'+menuItem('retire',esc(tr('retire_7ad0e0')),{kind:'danger',disabled:off})}
// Groups already in use, none, or a new one typed in place.
function groupMenu(t,data){const off=data.readOnly||t.pending,groups=[...new Set(data.tasks.map(x=>x.group).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
 return '<p class="menu-title">'+esc(tr('group'))+'</p><div class="menu-scroll">'+groups.map(g=>menuItem('group',groupDot(g)+esc(g),{value:g,checked:t.group===g,disabled:off})).join('')+'</div>'
  +menuItem('group','<span class="group-dot ungrouped" aria-hidden="true"></span>'+esc(tr('no_group')),{value:'',checked:!t.group,disabled:off})
  +'<div class="menu-form"><input type="text" class="menu-input" data-menu-input="group" maxlength="120" autocomplete="off" placeholder="'+esc(tr('new_group_placeholder'))+'" aria-label="'+esc(tr('new_group'))+'"'+(off?' disabled':'')+'></div><p class="menu-error" role="alert" hidden></p>'}
// Deadlines at 18:00 in the profile's zone, or an exact local date and time.
function localAt(days,hour){const wall=new Date(localValue(NOW).slice(0,10)+'T00:00Z');wall.setUTCDate(wall.getUTCDate()+days);const raw=wall.toISOString().slice(0,10)+'T'+String(hour).padStart(2,'0')+':00',d=parseLocal(raw);return Number.isFinite(d.getTime())&&localValue(d)===raw&&!ambiguous(d)?d:null}
const dayHint=d=>new Intl.DateTimeFormat(formatLocale(),{weekday:'short',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:zone}).format(d);
function duePresets(){const weekday=new Date(localValue(NOW).slice(0,10)+'T00:00Z').getUTCDay(),seen=new Set();
 return [['due_today',0],['due_tomorrow',1],['due_friday',(5-weekday+7)%7||7],['due_next_week',7]].map(([key,days])=>[key,localAt(days,18)]).filter(([,d])=>d&&d>NOW&&!seen.has(d.getTime())&&seen.add(d.getTime()))}
function dueMenu(t,data){const off=data.readOnly||t.pending,opening=t.due?localValue(new Date(t.due)):(localAt(1,18)?localValue(localAt(1,18)):'');
 return '<p class="menu-title">'+esc(tr('deadline'))+'</p>'+duePresets().map(([key,d])=>menuItem('due','<span>'+esc(tr(key))+'</span><span class="menu-hint">'+esc(dayHint(d))+'</span>',{value:d.toISOString(),checked:t.due?new Date(t.due).getTime()===d.getTime():null,disabled:off})).join('')
  +'<div class="menu-form"><input type="datetime-local" step="60" class="menu-input" data-menu-input="due" value="'+esc(opening)+'" aria-label="'+esc(tr('exact_deadline'))+'"'+(off?' disabled':'')+'><button type="button" class="btn" data-menu-act="due-custom"'+(off?' disabled':'')+'>'+esc(tr('set'))+'</button></div><p class="menu-hint zone-hint">'+esc(zone)+'</p><p class="menu-error" role="alert" hidden></p>'
  +(t.due?'<div class="menu-sep" role="separator"></div>'+menuItem('due-clear',esc(tr('remove_deadline')),{disabled:off}):'')}
const menuBodies={priority:priorityMenu,snooze:snoozeMenu,more:moreMenu,group:groupMenu,due:dueMenu};
function openMenu(id,action,point){const data=flow.snapshot(),t=data.tasks.find(t=>t.id===id);if(!t||document.querySelector('dialog[open]'))return;closeMenu(false);
 const node=document.createElement('div');node.className='row-menu';node.setAttribute('role','menu');node.innerHTML=menuBodies[action](t,data);
 const sheet=sheetMedia.matches,scrim=sheet?document.createElement('div'):null;if(scrim){scrim.className='menu-scrim';document.body.append(scrim)}
 node.classList.toggle('sheet',sheet);document.body.append(node);menu={id,action,node,scrim,point,at:performance.now()};
 const anchor=anchorOf(id,action);anchor?.setAttribute('aria-expanded','true');if(!sheet)placeMenu(node,anchor,point);
 (node.querySelector('[aria-checked=true]:not([disabled])')||node.querySelector('button:not([disabled])'))?.focus({preventScroll:true})}
function placeMenu(node,anchor,point){const r=anchor?.getClientRects().length?anchor.getBoundingClientRect():null,w=node.offsetWidth,h=node.offsetHeight;
 // Menus for the data chips open under them, from their left edge; the others end at their button.
 const start=anchor?.matches?.('.row-meta *');let x=point?point.x:r?(start?r.left:r.right-w):8,y=point?point.y:r?r.bottom+6:8;if(y+h>innerHeight-8)y=(point?point.y:r?r.top-6:innerHeight)-h;
 node.style.left=Math.max(8,Math.min(x,innerWidth-w-8))+'px';node.style.top=Math.max(8,y)+'px'}
function closeMenu(restore=true){if(!menu)return;const {id,action,node,scrim}=menu;menu=null;node.remove();scrim?.remove();const anchor=anchorOf(id,action);anchor?.setAttribute('aria-expanded','false');
 if(restore)(anchor?.offsetWidth>1?anchor:anchorOf(id,'title'))?.focus({preventScroll:true})}
// The deadline editor replaces the ⋯ list in the same place, so it stays one level deep.
function swapMenu(kind){const t=task(menu.id);if(!t)return;menu.node.innerHTML=menuBodies[kind](t,flow.snapshot());if(!menu.node.classList.contains('sheet'))placeMenu(menu.node,null,{x:parseFloat(menu.node.style.left),y:parseFloat(menu.node.style.top)});(menu.node.querySelector('button:not([disabled])'))?.focus({preventScroll:true})}
function menuError(text){const node=menu?.node.querySelector('.menu-error');if(node){node.textContent=text;node.hidden=false}}
function customDue(){const raw=menu.node.querySelector('[data-menu-input=due]').value;if(!raw)return menuError(tr('choose_a_date_and_time')),null;const d=parseLocal(raw);if(!Number.isFinite(d.getTime())||localValue(d)!==raw)return menuError(tr('that_local_date_or_time_does_not_exist_choos')),null;if(ambiguous(d))return menuError(tr('that_time_repeats_in_this_zone_clarification')),null;return d.toISOString()}
function retireConfirm(){menu.node.innerHTML='<p class="menu-confirm">'+esc(tr('retire_this_responsibility_it_will_be_kept_s'))+'</p><div class="menu-confirm-actions"><button type="button" class="btn" data-menu-act="keep">'+esc(tr('cancel'))+'</button><button type="button" class="btn danger" data-menu-act="retire-yes">'+esc(tr('yes_retire'))+'</button></div>';if(!menu.node.classList.contains('sheet'))placeMenu(menu.node,null,{x:parseFloat(menu.node.style.left),y:parseFloat(menu.node.style.top)});menu.node.querySelector('button').focus({preventScroll:true})}
function menuAct(b){const {id}=menu,t=task(id),act=b.dataset.menuAct,value=b.dataset.value;if(!t){closeMenu(false);return}
 if(act==='retire'){retireConfirm();return}
 if(act==='keep'){closeMenu();return}
 if(act==='details'){closeMenu(false);toggleExpand(id);return}
 if(act==='edit-title'){closeMenu(false);startTitleEdit(id);return}
 if(act==='set-due'){swapMenu('due');return}
 let due;if(act==='due-custom'&&!(due=customDue()))return;
 closeMenu();
 if(act==='group'){if((t.group||'')!==value)request('task_update',{uuid:id,group:value})}
 else if(act==='due'||act==='due-custom'){due=due||value;if(!t.due||new Date(t.due).getTime()!==new Date(due).getTime())request('task_update',{uuid:id,due})}
 else if(act==='due-clear')request('task_update',{uuid:id,due:null});
 else if(act==='duplicate')duplicate(t);
 else if(act==='priority'){if(value!==t.priority)request('task_update',{uuid:id,priority:value})}
 else if(act==='snooze')request('snooze',{taskId:id,until:value});
 else if(act==='todo')request('todo',{taskId:id});
 else if(act==='retire-yes')request('retire',{taskId:id});
 else if(act==='move'){const card=$('board').querySelector('[data-task-id="'+id+'"]'),ids=sectionIds(card.dataset.section),to=Number(value);if(placeOrder(id,ids,to))notice(tr('moved_to').replace('{n}',to+1).replace('{total}',ids.length))}}
document.addEventListener('click',e=>{if(!menu)return;const b=e.target.closest('[data-menu-act]');if(b&&menu.node.contains(b))menuAct(b);else if(e.target===menu.scrim)closeMenu()});
// The click that ends a drag or a long press does nothing else; the confirmation's Undo always works.
document.addEventListener('click',e=>{if(swallowClick&&!e.target.closest?.('#toast')){swallowClick=false;e.preventDefault();e.stopPropagation()}},true);
for(const type of ['pointerup','pointercancel'])document.addEventListener(type,()=>{if(swallowClick)setTimeout(()=>swallowClick=false,80)},true);
document.addEventListener('pointerdown',e=>{if(e.isPrimary)swallowClick=false;if(menu&&!menu.node.contains(e.target)&&e.target!==menu.scrim&&e.target.closest?.('[data-action]')!==anchorOf(menu.id,menu.action))closeMenu(false)},true);
document.addEventListener('keydown',e=>{if(!menu||!menu.node.contains(document.activeElement)&&e.key!=='Escape')return;
 if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeMenu();return}
 const typing=document.activeElement?.matches?.('.menu-input');
 if(typing&&e.key==='Enter'&&!e.isComposing){e.preventDefault();const input=document.activeElement;if(input.dataset.menuInput==='due')menu.node.querySelector('[data-menu-act=due-custom]')?.click();else{const value=input.value.trim();if(!value)return;const t=task(menu.id);closeMenu();if(t&&value!==(t.group||''))request('task_update',{uuid:t.id,group:value})}return}
 // A menu with a field keeps Tab inside it; a plain list closes on Tab.
 if(e.key==='Tab'){e.preventDefault();const items=[...menu.node.querySelectorAll('button:not([disabled]),input:not([disabled])')];if(!menu.node.querySelector('.menu-input')||!items.length){closeMenu();return}const i=items.indexOf(document.activeElement);items[(i+(e.shiftKey?-1:1)+items.length)%items.length].focus();return}
 if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key)||typing&&['Home','End'].includes(e.key))return;e.preventDefault();
 const items=[...menu.node.querySelectorAll('button:not([disabled]),.menu-input:not([disabled])')],i=items.indexOf(document.activeElement),n=items.length;
 items[e.key==='Home'?0:e.key==='End'?n-1:(i+(e.key==='ArrowDown'?1:-1)+n)%n]?.focus()},true);
// Only a scroll that moves the rows affects a menu; the chat log scrolling does not. A menu opened
// from a button follows it, and closes once the button leaves the screen; one opened at the pointer closes.
addEventListener('scroll',e=>{if(!menu||menu.node.classList.contains('sheet')||!(e.target===document||e.target.contains?.($('board'))))return;const a=anchorOf(menu.id,menu.action),r=a?.getClientRects().length?a.getBoundingClientRect():null;
 if(menu.point||!r||r.bottom<0||r.top>innerHeight)closeMenu(false);else placeMenu(menu.node,a)},true);addEventListener('resize',()=>{if(!document.activeElement?.matches?.('.menu-input'))closeMenu(false)});
$('board').addEventListener('contextmenu',e=>{const card=e.target.closest('.task');if(!card)return;if(drag){e.preventDefault();return}e.preventDefault();if(menu?.id===card.dataset.taskId&&performance.now()-menu.at<800)return;openMenu(card.dataset.taskId,'more',{x:e.clientX,y:e.clientY})});
$('board').onclick=e=>{const sb=e.target.closest('[data-suggestion]');if(sb){const {suggestion,suggestionAction}=sb.dataset;bubbleFor=null;safely(async()=>{const started=performance.now();timed(flow.observe(await api('/api/suggestions/'+suggestion+'/'+suggestionAction,{})),started);await flow.refresh();render()});return}if(inlineClick(e))return;const b=e.target.closest('[data-action]');if(!b){const card=e.target.closest('.task');if(card&&!e.target.closest('.assistant-bubble'))toggleExpand(card.dataset.taskId);else if(dependencyFocus&&!card)toggleDependencies(dependencyFocus);return}const {id,action}=b.dataset;if(action==='suggestion'){bubbleFor=bubbleFor===id?null:id;render();$('board').querySelector('[data-id="'+id+'"][data-action="suggestion"]')?.focus({preventScroll:true});return}if(['priority','snooze','more','group','due'].includes(action)){if(menu?.id===id&&menu.action===action)closeMenu();else openMenu(id,action)}else if(action==='dependencies')toggleDependencies(id);else if(action==='title'){const t=task(id);if(t&&!t.pending&&!flow.snapshot().readOnly)startTitleEdit(id);else toggleExpand(id)}else if(action==='move')return;else if(action==='pending'){const p=flow.snapshot().proposals.find(p=>p.intent.taskId===id);if(p){collapsedProposals.delete(p.id);expandedProposal=p.id;render();$('review-title').scrollIntoView({block:'nearest'});$('review-title').focus()}}else request(action,{taskId:id})};
$('reviews').onclick=async e=>{const summary=e.target.closest('summary');if(summary){const d=summary.parentElement;if(d.open)collapsedProposals.add(d.dataset.proposal);else collapsedProposals.delete(d.dataset.proposal);}const b=e.target.closest('[data-review-action]');if(!b)return;const p=flow.snapshot().proposals.find(p=>p.id===b.dataset.proposal);if(!p)return;if(Number(b.dataset.generation)!==p.generation){notice(tr("the_review_changed_read_the_current_state"));return;}const action=b.dataset.reviewAction;if(action==='cancel'){await flow.cancel(p.id);notice(tr("change_canceled_the_responsibility_keeps_its"))}if(action==='retry')safely(()=>flow.retry(p.id));if(action==='continue')safely(()=>flow.commit(p.id,{generation:p.generation}))};
$('reviews').addEventListener('toggle',e=>{if(!e.target.matches('details'))return;const id=e.target.dataset.proposal;if(e.target.open){collapsedProposals.delete(id);expandedProposal=id}else collapsedProposals.add(id)},true);
$('undo').onclick=()=>{const id=$('undo').dataset.event,gesture=gestures.get(id);$('undo').hidden=true;safely(()=>gesture?flow.undoGesture(gesture):flow.undo(id))};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&bubbleFor&&!document.querySelector('dialog[open]')){const id=bubbleFor;bubbleFor=null;render();$('board').querySelector('[data-id="'+id+'"][data-action="suggestion"]')?.focus({preventScroll:true})}});
function openContext(origin){const revision=flow.snapshot().revision,c=flow.snapshot().context;$('context-minutes').value=c.availableMinutes??'';$('context-until').value=c.availableUntil?localValue(new Date(c.availableUntil)):'';const openingUntil=$('context-until').value;$('context-rest').checked=c.rest;$('context-error').hidden=true;
 $('context-form').onsubmit=async e=>{e.preventDefault();const raw=$('context-minutes').value,until=$('context-until').value;try{let availableUntil=c.availableUntil;if(raw.trim()){if(until!==openingUntil){const date=parseLocal(until);if(!Number.isFinite(date.getTime())||ambiguous(date)||localValue(date)!==until)throw Error(tr("provide_an_unambiguous_local_date_and_time_f"));availableUntil=date.toISOString()}if(!availableUntil)throw Error(tr("provide_an_unambiguous_local_date_and_time_f"))}await flow.setContext({availableMinutes:raw.trim()?Number(raw):null,availableUntil:raw.trim()?availableUntil:null,rest:$('context-rest').checked},revision);$('context-dialog').close('declared')}catch(error){$('context-error').hidden=false;$('context-error').textContent=error.message}};
 openDialog($('context-dialog'),origin,null,null)}
$('context-open').onclick=e=>openContext(e.currentTarget);$('context-cancel').onclick=()=>$('context-dialog').close('cancel');
$('list-view').onclick=()=>{$('board').classList.add('is-list');$('list-view').setAttribute('aria-pressed','true');$('board-view').setAttribute('aria-pressed','false')};$('board-view').onclick=()=>{$('board').classList.remove('is-list');$('list-view').setAttribute('aria-pressed','false');$('board-view').setAttribute('aria-pressed','true')};
$('order-choice').value=orderView;$('order-choice').onchange=()=>{orderView=$('order-choice').value;try{localStorage.setItem('day-attendant-order',orderView)}catch{}render()};
$('order-note').onclick=e=>{if(e.target.closest('#suggest-order'))safely(async()=>{await deliver('/api/suggestion',{});notice(tr('suggestion_pending'))})};
$('board').addEventListener('keydown',e=>{const handle=e.target.closest?.('.drag-handle');if(!handle||!['ArrowUp','ArrowDown'].includes(e.key)||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey)return;e.preventDefault();const ids=sectionIds(handle.closest('.task').dataset.section),from=ids.indexOf(handle.dataset.id),to=from+(e.key==='ArrowUp'?-1:1);if(to>=0&&to<ids.length&&placeOrder(handle.dataset.id,ids,to))notice(tr('moved_to').replace('{n}',to+1).replace('{total}',ids.length))});
// Dragging: the card lifts and follows the pointer with a slight tilt, the others make room and it
// lands where it is let go. A mouse drags from the handle or any empty part of the row; a touch
// holds the row first, and a hold let go without moving opens the row's menu instead.
// Another column changes the state, another priority or group (in those orders) takes that one,
// and the place is kept as the manual order. All of it applies at once, with one Undo.
const gestures=new Map(),gestureOf=new Map();let gestureSerial=0,scrollFrame=0;
const layoutTop=node=>{const parent=node.offsetParent;return (parent?parent.getBoundingClientRect().top+parent.clientTop:0)+node.offsetTop};
$('board').addEventListener('pointerdown',e=>{
 if(!e.isPrimary||drag)return;const card=e.target.closest('.task[data-task-id]'),handle=e.target.closest('.drag-handle'),mouse=e.pointerType==='mouse';
 if(!card||card.classList.contains('saving')||flow.snapshot().readOnly||mouse&&e.button!==0)return;
 if(mouse&&!handle&&(!e.target.closest('.row-main')||e.target.closest('button,input,textarea,select,a,label,[contenteditable]')))return;
 if(handle&&mouse){e.preventDefault();handle.focus({preventScroll:true})}
 drag={id:card.dataset.taskId,pointer:e.pointerId,x:e.clientX,y:e.clientY,touch:!mouse,armed:mouse,active:false};
 if(!mouse)drag.timer=setTimeout(()=>{if(!drag||drag.active)return;drag.armed=true;cardOf(drag.id)?.classList.add('pressing');navigator.vibrate?.(8)},450);
});
document.addEventListener('pointermove',e=>{
 if(e.pointerId!==drag?.pointer)return;
 if(!drag.active){const distance=Math.hypot(e.clientX-drag.x,e.clientY-drag.y);
  if(!drag.armed){if(distance>8){clearTimeout(drag.timer);drag=null}return}
  if(distance<(drag.touch?6:5)||!beginDrag(e))return}
 e.preventDefault();drag.px=e.clientX;drag.py=e.clientY;if(!drag.frame)drag.frame=requestAnimationFrame(()=>{if(!drag?.active)return;drag.frame=0;placeCard(drag.px,drag.py);moveGhost(drag.px,drag.py)});
},{passive:false});
// While a touch holds or drags a row, the page does not scroll under it.
document.addEventListener('touchmove',e=>{if(drag?.armed&&drag.touch)e.preventDefault()},{passive:false});
function beginDrag(e){
 const card=cardOf(drag.id);if(!card){drag=null;return false}
 clearTimeout(drag.timer);card.classList.remove('pressing');closeMenu(false);getSelection()?.removeAllRanges();
 const rect=card.getBoundingClientRect();Object.assign(drag,{card,active:true,width:rect.width,offX:e.clientX-rect.left,offY:e.clientY-rect.top,vx:0,lastX:e.clientX,ghost:floating(card,rect,'drag-ghost')});
 card.classList.add('placeholder');card.style.height=rect.height+'px';
 $('board').classList.add('is-dragging');document.body.classList.add('dragging-task');swallowClick=true;
 moveGhost(e.clientX,e.clientY);autoScroll();return true;
}
function moveGhost(x,y){const at=inLayer(x-drag.offX,y-drag.offY);drag.vx=drag.vx*.7+(x-drag.lastX)*.3;drag.lastX=x;
 // A slight tilt that follows the motion; a wide row tilts less, so its ends move at most a few pixels.
 const limit=Math.min(5,Math.atan(12/drag.width)*180/Math.PI),tilt=moving()?Math.max(-limit,Math.min(limit,drag.vx*.5)):0;drag.ghost.style.transform='translate('+at.x+'px,'+at.y+'px) rotate('+tilt.toFixed(2)+'deg) scale(1.02)'}
function laneAt(x,y){let best=null,distance=Infinity;
 for(const lane of $('board').querySelectorAll('.lane')){const r=lane.getBoundingClientRect(),dx=x<r.left?r.left-x:x>r.right?x-r.right:0,dy=y<r.top?r.top-y:y>r.bottom+30?y-r.bottom-30:0,d=dx*3+dy;if(d<distance){distance=d;best=lane}}
 return best}
// The slot moves under the pointer and the others glide out of its way. Every position is read
// before anything changes, so a move costs one layout.
function placeCard(x,y){
 const lane=laneAt(x,y),card=drag.card;if(!lane)return;
 const box=lane.querySelector('.lane-cards'),items=[...box.children].filter(n=>n.dataset.taskId&&n!==card),next=items.find(c=>y<layoutTop(c)+c.offsetHeight/2);
 let ref=next||null;const heading=next?.previousElementSibling;
 // Above a group's heading the slot ends the previous group instead of starting this one.
 if(heading?.classList.contains('group-heading')&&heading.previousElementSibling&&y<layoutTop(heading)+heading.offsetHeight)ref=heading;
 const stays=card.parentElement===box&&(ref?card.nextElementSibling===ref:card===box.lastElementChild),first=!stays&&moving()?cardRects():null;
 for(const l of $('board').querySelectorAll('.lane'))if(l.classList.contains('drop-target')!==(l===lane))l.classList.toggle('drop-target',l===lane);
 if(stays)return;
 box.insertBefore(card,ref);if(first)playFlip(first,{duration:DURATION,enter:false});
}
function autoScroll(){cancelAnimationFrame(scrollFrame);
 const step=()=>{if(!drag?.active)return;const edge=60,y=drag.py??drag.y;let v=0;
  if(y<edge)v=-Math.ceil((edge-y)/6);else if(y>innerHeight-edge)v=Math.ceil((y-(innerHeight-edge))/6);
  if(v){const before=scrollY;scrollBy(0,v);if(scrollY!==before)placeCard(drag.px??drag.x,y)}
  scrollFrame=requestAnimationFrame(step)};
 scrollFrame=requestAnimationFrame(step)}
function finishDrag(commit){
 const d=drag;if(!d)return;if(d.frame){cancelAnimationFrame(d.frame);d.frame=0;if(commit&&d.active){moveGhost(d.px,d.py);placeCard(d.px,d.py)}}drag=null;clearTimeout(d.timer);cancelAnimationFrame(scrollFrame);cardOf(d.id)?.classList.remove('pressing');
 if(!d.active){if(d.touch&&d.armed&&commit){swallowClick=true;openMenu(d.id,'more',{x:d.x,y:d.y})}return}
 // The release's own click comes in the same task; nothing after it is swallowed.
 $('board').classList.remove('is-dragging');document.body.classList.remove('dragging-task');setTimeout(()=>swallowClick=false);
 for(const l of $('board').querySelectorAll('.lane.drop-target'))l.classList.remove('drop-target');
 if(!(commit&&dropCard(d)))render();
 // The lifted copy lands on the card's place, wherever the drop or its refusal left it.
 const card=cardOf(d.id)||d.card;card.getAnimations().forEach(a=>a.finish());
 const rect=card.getBoundingClientRect(),at=inLayer(rect.left,rect.top),ghost=d.ghost;
 const landed=()=>{ghost.remove();for(const c of new Set([card,cardOf(d.id)]))if(c){c.classList.remove('placeholder');c.style.removeProperty('height')}};
 if(!moving()){landed();return}
 ghost.animate([{transform:ghost.style.transform},{transform:'translate('+at.x+'px,'+at.y+'px)'}],{duration:DURATION,easing:EASE,fill:'forwards'}).onfinish=landed;
}
document.addEventListener('pointerup',e=>{if(e.pointerId===drag?.pointer)finishDrag(true)});
document.addEventListener('pointercancel',e=>{if(e.pointerId===drag?.pointer)finishDrag(false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&drag?.active){e.preventDefault();finishDrag(false)}});
const laneName=state=>tr({active:'active',todo:'to_do',snoozed:'snoozed'}[state]);
function tomorrowAt(hour){const [y,m,d]=localValue(NOW).slice(0,10).split('-').map(Number);return parseLocal(new Date(Date.UTC(y,m-1,d+1)).toISOString().slice(0,10)+'T'+String(hour).padStart(2,'0')+':00')}
// What the drop means, read from where the card now sits; false when nothing changes.
function dropCard(d){
 const data=flow.snapshot(),t=data.tasks.find(t=>t.id===d.id),card=d.card;if(!t||!card.isConnected)return false;
 const lane=card.closest('.lane'),state=lane.dataset.state,near=dir=>{let n=card[dir];while(n&&!n.dataset.taskId)n=n[dir];return n&&data.tasks.find(r=>r.id===n.dataset.taskId)};
 const before=near('previousElementSibling'),after=near('nextElementSibling');let heading=card.previousElementSibling;while(heading&&!heading.classList.contains('group-heading'))heading=heading.previousElementSibling;
 const changes=[],notes=[];
 if(state!==t.state){
  if(state==='active'&&t.blocked){notice(tr('drop_blocked'));return false}
  if(state==='snoozed'){const until=tomorrowAt(9).toISOString();changes.push(['snooze',{taskId:t.id,until}]);notes.push(tr('drop_snoozed').replace('{time}',short(until)))}
  else{changes.push([state==='active'?'activate':'todo',{taskId:t.id}]);notes.push(tr('drop_moved').replace('{lane}',laneName(state)))}
 }
 const patch={};
 if(orderView==='priority'){const priority=globalThis.dayOrdering.adopt(t.priority,before?.priority,after?.priority);if(priority!==t.priority){patch.priority=priority;notes.push(tr('drop_priority').replace('{priority}',tr('priority_'+priority)))}}
 if(orderView==='group'&&heading){const group=heading.dataset.group;if((t.group||'').trim().toLocaleLowerCase()!==group.toLocaleLowerCase()){patch.group=group;notes.push(group?tr('drop_group').replace('{group}',group):tr('drop_no_group'))}}
 if(Object.keys(patch).length)changes.push(['task_update',{uuid:t.id,...patch}]);
 const order=currentOrder(data),next=['manual','priority','group'].includes(orderView)?globalThis.dayOrdering.place(order,t.id,before?.id,after?.id):order,reordered=next.join()!==order.join();
 if(!changes.length)return setOrder(next);
 // The new place is set before the changes show, so the card stays exactly where it was let go.
 const gesture={id:'gesture:'+(++gestureSerial),taskId:t.id,notes,proposals:[],before:{tasks:data.tasks,taskOrder:data.taskOrder},started:performance.now()};
 gestures.set(gesture.id,gesture);if(gestures.size>20)gestures.delete(gestures.keys().next().value);
 if(reordered){clearTimeout(orderTimer);localOrder=next}
 for(const [action,payload] of changes)request(action,payload,undefined,undefined,gesture);
 if(reordered)request('order_update',{order:next},undefined,undefined,gesture).then(()=>{if(localOrder===next){localOrder=null;orderBase=null;render()}});
 gesture.shown=performance.now();return true;
}
const themeMedia=matchMedia('(prefers-color-scheme:dark)');let themeChoice='system';try{themeChoice=localStorage.getItem('day-attendant-theme')||'system'}catch{}if(!['system','light','dark'].includes(themeChoice))themeChoice='system';$('theme-choice').value=themeChoice;const applyTheme=()=>root.dataset.theme=themeChoice==='system'?(themeMedia.matches?'dark':'light'):themeChoice;$('theme-choice').onchange=()=>{themeChoice=$('theme-choice').value;try{localStorage.setItem('day-attendant-theme',themeChoice)}catch{}applyTheme()};themeMedia.addEventListener('change',applyTheme);applyTheme();
const compact=matchMedia('(max-width:1279px)');let chatOpen=!compact.matches;
function placeChat(){const modal=$('chat-dialog'),panel=$('assistant'),dock=$('chat-dock');if(compact.matches&&!modal.open&&document.querySelector('dialog[open]:not(#chat-dialog)'))chatOpen=false;$('chat-toggle').setAttribute('aria-expanded',String(chatOpen));$('workspace').classList.toggle('chat-open',chatOpen&&!compact.matches);if(compact.matches){dock.hidden=true;if(panel.parentElement!==modal)modal.append(panel);if(chatOpen&&!modal.open){modal.returnValue='cancel';origins.set(modal,{origin:$('chat-toggle')});modal.showModal()}else if(!chatOpen&&modal.open)modal.close('collapse')}else{if(modal.open)modal.close('reflow');if(panel.parentElement!==dock)dock.append(panel);dock.hidden=!chatOpen}}
$('chat-dialog').addEventListener('close',()=>{if($('chat-dialog').returnValue==='reflow')return;chatOpen=false;placeChat()});$('chat-toggle').onclick=()=>{chatOpen=!chatOpen;placeChat()};$('chat-close').onclick=()=>{chatOpen=false;placeChat();$('chat-toggle').focus({preventScroll:true})};compact.addEventListener('change',placeChat);placeChat();


const aliases={front_activate:'activate',front_todo:'todo',task_snooze:'snooze',task_done:'done',front_retire:'retire',task_restore:'restore',task_add:'add',task_update:'update',task_reopen:'reopen',context_update:'declaration',continuity_update:'declaration',order_update:'order',dependency_update:'dependency',related_update:'related'};
const domain={activate:'front_activate',todo:'front_todo',snooze:'task_snooze',done:'task_done',retire:'front_retire'};
// Reads bypass the HTTP cache: otherwise the browser holds a read behind an identical one in flight.
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},credentials:'same-origin',cache:'no-store',body:body===undefined?undefined:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=Error(errorText(result.error?.code,result.error?.message||tr("the_request_could_not_be_completed")));error.code=result.error?.code;error.status=response.status;throw error;}return result.data;}

// A delivery keeps its identity until its receipt and refreshed state are known.
// Repeating a successful action later is a new intent; network retries are not.
const deliveries=new Map();
try{for(const [k,v] of JSON.parse(sessionStorage.getItem('day-attendant-deliveries')||'[]'))deliveries.set(k,v)}catch{}
function reconcileDeliveries(raw){for(const [identity,body] of deliveries){const route=identity.slice(0,identity.indexOf('{'));const seen=route==='/api/chat'?raw.messages.some(m=>m.role==='user'&&m.key===body.key):raw.proposals.some(p=>p.key===body.key);if(!seen)continue;if(route==='/api/chat'&&$('conversation-input').value.trim()===body.text.trim())$('conversation-input').value='';deliveries.delete(identity);saveDeliveries();}}
function saveDeliveries(){try{sessionStorage.setItem('day-attendant-deliveries',JSON.stringify([...deliveries]))}catch{}}
async function deliver(route,data,revision,{refresh=true}={}){
 const identity=route+JSON.stringify(data);let body=deliveries.get(identity);
 if(!body){body={...data,key:crypto.randomUUID(),...(revision===undefined?{}:{expected_revision:revision})};deliveries.set(identity,body);saveDeliveries()}
 try{const result=await api(route,body);if(refresh)await flow.refresh();deliveries.delete(identity);saveDeliveries();return result}
 catch(error){if(error.status>=400&&error.status<500){deliveries.delete(identity);saveDeliveries()}throw error}
}

class RemoteFlow{
 constructor(){this.data={tasks:[],context:{availableMinutes:null,availableUntil:null,rest:false},proposals:[],events:[],messages:[]};this.digest='';this.messageDigest='';this.tag='';this.started=0;this.applied=0;this.loading=0;this.localeLoaded=false;this.queue=Promise.resolve();this.seen=0;this.drafts=0;this.sending=0;this.barrier=0;}
 snapshot(){return this.data;}
 // Each refresh starts one load at once, so a caller that just saved waits only for the load that
 // includes its change. A slower older load never overwrites newer state, and polls never stack.
 // A load that started before a change shown on this page, or while one is still being sent, may
 // predate it; it is dropped so the change never flickers back.
 refresh(){return this.load()}
 poll(){return this.loading?null:this.load()}
 async load(){const seq=++this.started,settled=!this.sending;this.loading++;try{const raw=await api('/api/state'+(this.tag?'?since='+encodeURIComponent(this.tag):''));if(seq<this.applied||!settled||seq<=this.barrier)return;this.applied=seq;if(this.offline){this.offline=false;notice(tr("connection_restored"))}if(raw.unchanged){if(raw.tag===this.tag)this.tick(raw.now);else this.tag='';return}this.apply(raw)}catch(error){this.offline=true;this.data.agent={...(this.data.agent||{}),connected:false};$('agent-state').textContent=tr("disconnected");delete $('agent-state').dataset.label;throw error}finally{this.loading--}}
 // An unchanged poll only advances the clock; calendar rows are redrawn when one of them ends.
 tick(now){const before=new Date(this.data.now),after=new Date(now);NOW=after;this.data.now=now;if((this.data.calendar_events||[]).some(e=>{const end=new Date(e.end_at);return end>before&&end<=after}))globalThis.dayDaily?.update(this.data)}
 apply(raw){if(!this.localeLoaded&&raw.profile){this.localeLoaded=true;locale=raw.profile.locale||'en';$('locale-choice').value=locale;translateChrome();refreshLabels()}NOW=new Date(raw.now);zone=raw.timezone||zone;const state={...raw,revision:Math.max(raw.revision,this.seen),mode:'connected',rawProposals:raw.proposals,proposals:raw.proposals.filter(p=>['evaluating','review','unavailable','stale','invalid'].includes(p.phase)).map(p=>({...p,intent:{...p.intent,action:aliases[p.intent.action]||p.intent.action,taskId:p.intent.payload.uuid,until:p.intent.payload.until},concerns:p.facts,superseded:p.result?.superseded||[]})),suggestions:raw.suggestions||[],review:raw.review||{}};this.tag=raw.tag||'';this.data=state;reconcileDeliveries(raw);globalThis.dayDaily?.update(state);const digest=JSON.stringify([state.tasks,state.taskOrder,state.orderSuggestion,state.agent.connected,state.context,state.proposals,state.lastFact,state.suggestions,state.revision,state.readOnly]);if(digest!==this.digest){this.digest=digest;render(state)}const md=JSON.stringify([state.messages,state.suggestions,state.conversation]);if(md!==this.messageDigest){this.messageDigest=md;renderMessages(state.messages,state)}const setText=(node,value)=>{if(node.textContent!==value)node.textContent=value};const agentError=state.agent.error&&errorText(state.agent.errorCode,state.agent.error);renderReview(state);$('bridge-state').hidden=state.agent.connected;if(!state.agent.connected)setText($('bridge-state'),agentError||tr('agent_temporarily_unavailable'));renderContextStatus(state);setText($('agent-details'),(state.agent.model||(state.profile?.harness==='claude'?'Claude Code':'Codex'))+' · '+(state.profile?.harness||'codex'));const stateTitle=agentError||$('agent-state').dataset.label||'';if($('agent-state').title!==stateTitle)$('agent-state').title=stateTitle;setText(document.querySelector('.date'),new Intl.DateTimeFormat(formatLocale(),{weekday:'long',day:'numeric',month:'long',timeZone:zone}).format(NOW));}
 // A revision this page produced itself: the next change it sends already includes its own last one.
 observe(result){const revision=result?.result?.revision??result?.revision;if(Number.isInteger(revision)&&revision>this.seen){this.seen=revision;this.data.revision=Math.max(this.data.revision||0,revision)}return result}
 // Shows a direct change on screen at once; the service's answer and the next read reconcile it.
 preview(body){const p=body.payload,data=this.data;let tasks=data.tasks,context=data.context;
  const moves={front_activate:{state:'active',until:null},front_todo:{state:'todo',until:null},task_done:{state:'done',until:null},front_retire:{state:'retired',until:null},task_reopen:{state:'todo',until:null}};
  if(moves[body.action]||body.action==='task_snooze'||body.action==='task_update'){
   const fields=moves[body.action]||(body.action==='task_snooze'?{state:'snoozed',until:p.until}:Object.fromEntries(['title','priority','due','group'].filter(k=>k in p).map(k=>[k,k==='group'?p.group||null:p[k]])));
   if(!tasks.some(t=>t.id===p.uuid))return false;tasks=tasks.map(t=>t.id===p.uuid?{...t,...fields}:t)}
  else if(body.action==='task_add')tasks=[...tasks,{id:'draft:'+(++this.drafts),title:p.title,state:'todo',priority:p.priority||'medium',group:p.group||null,due:p.due||null,until:null,blocked:false,dependsOn:[],blockedBy:[],relatedTo:[],attachmentIds:[],context:'',next:'',awaiting:false,remainingMinutes:null,createdAt:NOW.toISOString(),pending:true}];
  else if(body.action==='continuity_update'){if(!tasks.some(t=>t.id===p.uuid))return false;const fields=Object.fromEntries(['context','next','awaiting','remainingMinutes'].filter(k=>k in p).map(k=>[k,p[k]]));tasks=tasks.map(t=>t.id===p.uuid?{...t,...fields}:t)}
  else if(body.action==='dependency_update'){if(!tasks.some(t=>t.id===p.uuid))return false;const done=new Set(tasks.filter(t=>t.state==='done').map(t=>t.id)),blockedBy=p.dependsOn.filter(id=>!done.has(id));tasks=tasks.map(t=>t.id===p.uuid?{...t,dependsOn:[...p.dependsOn],blockedBy,blocked:blockedBy.length>0}:t)}
  else if(body.action==='related_update'){if(!tasks.some(t=>t.id===p.uuid))return false;const wanted=new Set(p.relatedTo);tasks=tasks.map(t=>t.id===p.uuid?{...t,relatedTo:[...p.relatedTo]}:wanted.has(t.id)&&!t.relatedTo.includes(p.uuid)?{...t,relatedTo:[...t.relatedTo,p.uuid]}:!wanted.has(t.id)&&t.relatedTo.includes(p.uuid)?{...t,relatedTo:t.relatedTo.filter(x=>x!==p.uuid)}:t)}
  else if(body.action==='context_update')context={...context,...p};
  else return false;
  this.data={...data,tasks,context};render(this.data);return {tasks:data.tasks,context:data.context}}
 // Direct changes go out one at a time, each with the newest revision, so quick consecutive
 // clicks never collide with each other; none of them waits for a state read to be sent.
 async send(body,key,expectedRevision){const revision=expectedRevision??this.data.revision;return this.observe(key?await api('/api/proposals',{...body,key,expected_revision:revision}):await deliver('/api/proposals',body,revision,{refresh:false}))}
 async request(action,payload,key,expectedRevision,gesture){const started=performance.now();const body={action:domain[action]||action,payload:{...(payload.taskId?{uuid:payload.taskId}:{}),...(payload.until?{until:payload.until}:{}),...(!payload.taskId?payload:{})}};
  this.sending++;this.barrier=this.started;const previewed=this.preview(body),shown=previewed?performance.now():undefined,sent=this.queue.then(()=>this.send(body,key,expectedRevision));this.queue=sent.catch(()=>{});
  let p;try{p=timed(await sent,started,shown,previewed||undefined);if(gesture&&p?.id){gesture.proposals.push(p.id);gestureOf.set(p.id,gesture);if(gestureOf.size>60)gestureOf.delete(gestureOf.keys().next().value)}}catch(error){this.settle();if(previewed){this.digest='';this.tag='';await this.refresh().catch(()=>{})}throw error}this.settle();
  await this.refresh();return {...p,intent:{...p.intent,action:aliases[p.intent.action]||p.intent.action,taskId:p.intent.payload.uuid,until:p.intent.payload.until},concerns:p.facts,superseded:p.result?.superseded||[]};}
 settle(){this.sending--;this.barrier=this.started}
 async cancel(id){const r=await api('/api/proposals/'+id+'/cancel',{});await this.refresh();return r}
 async retry(id){const started=performance.now();const r=timed(this.observe(await api('/api/proposals/'+id+'/retry',{})),started);await this.refresh();return r}
 async commit(id,options){const started=performance.now();const r=this.observe(await api('/api/proposals/'+id+'/confirm',{generation:options.generation,without_review:!!options.withoutReview}));timings.set(id,{started});await this.refresh();return r}
 async undo(id){const started=performance.now(),view=undoViews.get(String(id));undoViews.delete(String(id));
  if(view){const back=new Map(view.tasks.map(t=>[t.id,t]));this.data={...this.data,tasks:this.data.tasks.filter(t=>!t.pending&&(back.has(t.id)||t.id!==view.taskId)).map(t=>back.get(t.id)||t),context:view.context};render(this.data)}
  this.sending++;this.barrier=this.started;const shown=view?performance.now():undefined,sent=this.queue.then(async()=>this.observe(await deliver('/api/undo',{fact_id:id},this.data.revision,{refresh:false})));this.queue=sent.catch(()=>{});
  let r;try{r=timed(await sent,started,shown)}catch(error){this.settle();if(view){this.digest='';this.tag='';await this.refresh().catch(()=>{})}throw error}this.settle();await this.refresh();return r}
 // Undo of a drop puts the card back at once, then reverts every change the drop saved, newest first.
 async undoGesture(gesture){const started=performance.now(),back=gesture.before.tasks.find(t=>t.id===gesture.taskId);gestures.delete(gesture.id);
  if(back){localOrder=null;this.data={...this.data,tasks:this.data.tasks.map(t=>t.id===back.id?back:t),taskOrder:gesture.before.taskOrder};render(this.data)}
  this.sending++;this.barrier=this.started;const shown=performance.now(),sent=this.queue.then(async()=>{const undone=await deliver('/api/undo',{proposal_ids:gesture.proposals},this.data.revision,{refresh:false});return this.observe(undone.at(-1))});this.queue=sent.catch(()=>{});
  let r;try{r=timed(await sent,started,shown)}catch(error){this.settle();this.digest='';this.tag='';await this.refresh().catch(()=>{});throw error}this.settle();await this.refresh();return r}
 async setEstimate(id,value,revision){return this.request('continuity_update',{uuid:id,remainingMinutes:value},undefined,revision)}
 async setContext(value,revision){return this.request('context_update',{availableMinutes:value.availableMinutes,availableUntil:value.availableUntil,rest:value.rest},undefined,revision)}
}
// The assistant state and the review strip say whether saved changes are still waiting for its later look.
function renderReview(state){const r=state.review||{},connected=!!state.agent?.connected,busy=connected&&!!(r.running||r.pending);
 const label=!connected?tr('disconnected'):r.running?tr('agent_reviewing'):r.pending?formatted(r.pending===1?'agent_pending_one':'agent_pending',{n:r.pending}):tr('agent_up_to_date');
 const node=$('agent-state');node.classList.toggle('busy',busy);node.classList.toggle('offline',!connected);
 if(node.dataset.label!==label){node.dataset.label=label;node.innerHTML='<span class="dot" aria-hidden="true"></span><span class="state-label">'+esc(label)+'</span>'}
 const text=!connected?tr('review_strip_offline'):r.running?tr('review_strip_running'):r.pending?formatted('review_strip_waiting',{n:r.pending}):['incomplete','interrupted'].includes(r.status)?tr('review_strip_incomplete'):r.finishedAt?tr('review_strip_done').replace('{time}',short(r.finishedAt)):tr('review_strip_saved');
 const strip=$('review-strip');if(strip.dataset.text!==text){strip.dataset.text=text;strip.innerHTML=(busy?'<span class="spinner" aria-hidden="true"></span>':checkIcon)+'<span>'+esc(text)+'</span>'}}
// Brings a marked card into view from the chat log and opens its suggestion.
function focusSuggestion(id){const data=flow.snapshot();if(currentSuggestion(id,data))bubbleFor=id;if($('chat-dialog').open){chatOpen=false;placeChat()}render();const card=$('board').querySelector('[data-task-id="'+id+'"]');card?.scrollIntoView({block:'center'});(card?.querySelector('[data-action="suggestion"]')||card?.querySelector('[data-action="title"]'))?.focus({preventScroll:true})}
$('messages').addEventListener('click',e=>{const b=e.target.closest('[data-focus-task]');if(b){e.preventDefault();focusSuggestion(b.dataset.focusTask)}});
function suggestionLog(s,state){const el=document.createElement('div');el.className='slog'+(s.current?'':' closed');const t=state.tasks.find(t=>t.id===s.taskId),link=document.createElement('a');link.href='#';link.dataset.focusTask=s.taskId;link.textContent='«'+(t?.title||'…')+'»';el.append(document.createTextNode(tr('suggestion_log_prefix')+' '),link,document.createTextNode(': '+suggestionText(s)+(s.current?'':' · '+tr('suggestion_handled'))));return el}
function renderMessages(messages,state=flow.snapshot()){const box=$('messages'),oldScroll=box.scrollTop,nearEnd=box.scrollHeight-box.clientHeight-box.scrollTop<70;box.replaceChildren();
 // The log of suggestions shares the conversation's time line and its /clear boundary.
 const since=state.conversation?.clearedAt||'',notes=(state.suggestions||[]).filter(s=>s.createdAt>since);
 const entries=[...messages.map(m=>({at:m.at,message:m})),...notes.map(s=>({at:s.createdAt,note:s}))].sort((a,b)=>String(a.at).localeCompare(String(b.at)));
 for(const {message:item,note} of entries){if(note){box.append(suggestionLog(note,state));continue}const el=document.createElement('div');el.className='message'+(item.role==='user'?' user':'');el.dataset.key=item.key;const h=document.createElement('strong');h.textContent=item.role==='user'?tr("you"):item.status==='incomplete'?tr("assistant_incomplete_response"):tr("assistant");const p=document.createElement('p');p.textContent=messageText(item);el.append(h,p);if(item.role==='user'&&item.status==='incomplete'){const retry=document.createElement('button');retry.type='button';retry.className='btn';retry.textContent=tr('retry_reply');retry.onclick=()=>safely(async()=>{await api('/api/chat/retry',{key:item.key});await flow.refresh()});el.append(retry)}if(item.status==='pending'){const status=document.createElement('p');status.className='small muted';status.textContent=tr("the_assistant_is_responding");el.append(status)}box.append(el)}if(nearEnd)box.scrollTop=box.scrollHeight;else box.scrollTop=oldScroll;const guidance=messages.findLast(m=>{try{return JSON.parse(m.data||'null')?.kind==='context_notice'}catch{return false}});if(guidance){const signature=guidance.id+':'+guidance.status+':'+guidance.text;if(signature!==lastGuidanceNotice){lastGuidanceNotice=signature;notice(messageText(guidance))}}}
// Chat receipts point here: open the pending change where it can be decided.
function showProposal(id){collapsedProposals.delete(id);expandedProposal=id;render();const reveal=()=>{const d=[...$('reviews').querySelectorAll('details')].find(d=>d.dataset.proposal===id);if(!d)return;d.open=true;d.scrollIntoView({block:'nearest'});d.querySelector('summary').focus({preventScroll:true})};if(!$('chat-dialog').open)return reveal();$('chat-dialog').addEventListener('close',()=>requestAnimationFrame(reveal),{once:true});chatOpen=false;placeChat()}
// Only what the harness reported on the latest reply of the selected model; nothing is estimated.
function contextUsage(state){for(const message of [...(state.messages||[])].reverse()){if(message.role!=='assistant')continue;let data;try{data=JSON.parse(message.data||'null')}catch{continue}const usage=data?.usage;if(!usage||!Number.isInteger(usage.contextTokens))continue;return data.provider===state.profile?.harness&&data.model===state.agent?.model?usage:null}return null}
function renderContextStatus(state){const node=$('context-status'),usage=contextUsage(state),count=n=>new Intl.NumberFormat(formatLocale(),{notation:'compact',maximumFractionDigits:1}).format(n);let text=tr('context_no_data'),tight=false;
 if(usage&&Number.isInteger(usage.contextWindow)&&usage.contextWindow>0){const free=Math.max(0,Math.floor(100*(usage.contextWindow-usage.contextTokens)/usage.contextWindow));tight=free<20;text=tr('context_window_status').replace('{used}',count(usage.contextTokens)).replace('{window}',count(usage.contextWindow)).replace('{free}',new Intl.NumberFormat(formatLocale(),{style:'percent'}).format(free/100))}
 else if(usage)text=tr('context_used_no_window').replace('{used}',count(usage.contextTokens));
 if(node.textContent!==text)node.textContent=text;node.classList.toggle('tight',tight);const title=usage?tr('context_status_hint'):'';if(node.title!==title)node.title=title}
async function clearConversation(){await api('/api/chat/clear',{key:crypto.randomUUID()});await flow.refresh()}
$('chat-clear').onclick=()=>safely(clearConversation);
$('send-message').onclick=()=>safely(async()=>{const input=$('conversation-input'),text=input.value.trim();if(!text)return;$('send-message').disabled=true;try{if(text==='/clear'){await clearConversation();if(input.value.trim()===text)input.value='';return}await deliver('/api/chat',{text});if(input.value.trim()===text)input.value='';await flow.refresh()}finally{$('send-message').disabled=false}});
// Enter sends and Shift+Enter adds a line. An open input-method composition keeps Enter;
// touch keyboards keep Enter for new lines and show the send button instead.
const touchComposer=matchMedia('(hover:none) and (pointer:coarse)');
$('conversation-input').addEventListener('keydown',e=>{if(e.key!=='Enter'||e.isComposing||e.keyCode===229)return;if(e.ctrlKey||e.metaKey||!e.shiftKey&&!e.altKey&&!touchComposer.matches){e.preventDefault();$('send-message').click()}});
$('connection-retry').onclick=()=>safely(()=>flow.refresh());
flow=new RemoteFlow();
$('locale-choice').value=locale;translateChrome();$('locale-choice').onchange=()=>{locale=$('locale-choice').value;try{localStorage.setItem('day-attendant-locale',locale)}catch{}translateChrome();refreshLabels();render();renderMessages(flow.snapshot().messages);renderReview(flow.snapshot());renderContextStatus(flow.snapshot());notice('');safely(()=>deliver('/api/settings',{values:{locale}}));};
async function boot(){const token=new URLSearchParams(location.hash.slice(1)).get('session');if(token){await api('/api/session',{token});history.replaceState(null,'',location.pathname)}await flow.refresh();}
addEventListener('hashchange',()=>{if(new URLSearchParams(location.hash.slice(1)).has('session'))safely(boot)});
safely(boot);
// The next poll is scheduled only after the previous one ends; a hidden page polls less often.
function poll(){safely(()=>flow.poll()).finally(()=>setTimeout(poll,document.hidden?5000:900))}
setTimeout(poll,900);document.addEventListener('visibilitychange',()=>{if(!document.hidden)safely(()=>flow.poll())});
globalThis.daySnapshot=()=>structuredClone(flow.snapshot());
globalThis.dayUI={api,deliver,flow,openDialog,expandTask:id=>toggleExpand(id,true),editTitle:startTitleEdit,registerDialog,tr,esc,full,localValue,parseLocal,ambiguous,notice,safely,translateChrome,formatted,creates,showProposal};
})();
