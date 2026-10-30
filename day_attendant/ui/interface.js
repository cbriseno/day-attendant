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
const priorityIcon=p=>'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 19v-3"/><path d="M12 19v-7"'+(p==='low'?' opacity=".3"':'')+'/><path d="M18 19V8"'+(p==='high'?'':' opacity=".3"')+'/></svg>';
const checkIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>';
const linkIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 13.5a4 4 0 0 0 6 .5l3-3a4 4 0 0 0-5.7-5.7l-1.2 1.2M14 10.5a4 4 0 0 0-6-.5l-3 3a4 4 0 0 0 5.7 5.7l1.2-1.2"/></svg>';
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
let flow,snoozing=null,selected=null,editingProposal=null,snoozeMode='duration',previewTarget=null,expandedProposal=null,lastSeenEvent,lastRequestedProposal=null,lastGuidanceNotice=null;
let phaseCache=new Map(),collapsedProposals=new Set(),dependencyFocus=null;
let detailRevision,snoozeRevision;
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
let actionLabel,phaseLabel;function refreshLabels(){actionLabel={activate:tr("start"),snooze:tr("snooze"),todo:tr("back_to_to_do"),done:tr("complete"),retire:tr("retire"),restore:tr("undo"),declaration:tr("declaration_updated"),add:tr("create"),update:tr("update")};
phaseLabel={evaluating:tr("the_assistant_is_reviewing"),review:tr("phase_waits_for_you"),unavailable:tr("phase_left_waiting"),stale:tr("context_changed"),invalid:tr("phase_not_applied")};
}
refreshLabels();
// Order views change presentation only. A drag stays inside its section (same lane and, in
// those views, the same priority or group) and saves the profile's manual order.
let orderView='priority';try{orderView=localStorage.getItem('day-attendant-order')||'priority'}catch{}if(!globalThis.dayOrdering.VIEWS.includes(orderView))orderView='priority';
let localOrder=null,orderBase=null,orderTimer=null,orderSaving=false,drag=null;
const groupDot=label=>'<span class="group-dot" style="--group-hue:'+globalThis.dayOrdering.hue(label)+'" aria-hidden="true"></span>';
function cardTags(t,section,data){const handle=section.draggable&&section.tasks.length>1&&!data.readOnly?'<button type="button" class="drag-handle" data-id="'+t.id+'" data-action="move" aria-label="'+esc(tr('reorder')+': '+t.title)+'" title="'+esc(tr('reorder_hint'))+'">'+gripIcon+'</button>':'';
 return '<div class="task-tags">'+handle+'<span class="priority-pill" data-priority="'+esc(t.priority)+'">'+priorityIcon(t.priority)+'<span class="visually-hidden">'+esc(tr('priority'))+': </span>'+esc(tr('priority_'+t.priority))+'</span>'+(t.group&&orderView!=='group'?'<span class="group-chip">'+groupDot(t.group)+'<span class="visually-hidden">'+esc(tr('group'))+': </span>'+esc(t.group)+'</span>':'')+suggestionMark(t,data)+'</div>'}
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
function groupHeading(s){return '<h3 class="group-heading">'+(s.label?groupDot(s.label)+esc(s.label):'<span class="group-dot ungrouped" aria-hidden="true"></span>'+esc(tr('no_group')))+'<span class="lane-count">'+s.tasks.length+'</span></h3>'}
function orderNote(data){
 if(orderView!=='suggested')return '<p>'+esc(tr('order_hint_'+orderView)).replace('{grip}','<span class="grip-inline" aria-hidden="true">'+gripIcon+'</span>')+'</p>';
 const s=data.orderSuggestion,result=s?.result,pending=s?.request?.status==='pending',failed=s?.request?.status==='incomplete';
 const text=(result?tr('suggestion_from').replace('{time}',short(result.at))+(s.stale?' '+tr('suggestion_stale'):''):tr('suggestion_none'))+(pending?' '+tr('suggestion_pending'):failed?' '+tr('suggestion_failed')+(known(s.request.code)?tr('error_'+s.request.code):tr('chat_failed_generic')):'');
 const disabled=data.readOnly||pending||!data.agent?.connected||!data.tasks.some(t=>!['done','retired'].includes(t.state));
 return '<p>'+esc(text)+'</p><button type="button" class="btn ghost" id="suggest-order"'+(disabled?' disabled':'')+(data.agent?.connected?'':' title="'+esc(tr('agent_temporarily_unavailable'))+'"')+'>'+esc(tr(result?'suggest_again':'suggest_order'))+'</button>'+(result?.message?'<p class="suggestion-message">'+esc(result.message)+'</p>':'');
}
const sectionIds=section=>[...$('board').querySelectorAll('.task')].filter(c=>c.dataset.section===section).map(c=>c.dataset.taskId);
// The visible order applies at once; saving waits for a pause and keeps the revision the person saw.
function placeOrder(id,ids,to){const data=flow.snapshot(),open=data.tasks.filter(t=>!['done','retired'].includes(t.state)),order=globalThis.dayOrdering.manualIds(open,localOrder||data.taskOrder||[]),next=globalThis.dayOrdering.move(order,ids,id,to);if(next.join()===order.join())return false;if(localOrder===null)orderBase=data.revision;localOrder=next;render();clearTimeout(orderTimer);orderTimer=setTimeout(saveOrder,350);return true}
async function saveOrder(){if(orderSaving){orderTimer=setTimeout(saveOrder,150);return}const order=localOrder;if(!order)return;orderSaving=true;try{const p=await flow.request('order_update',{order},undefined,orderBase);orderBase=p?.result?.revision??flow.snapshot().revision}catch(error){localOrder=null;orderBase=null;notice(error.message);render();return}finally{orderSaving=false}if(localOrder===order){localOrder=null;orderBase=null;render()}}
// Blocked and blocking cards name their counterparts; a chip highlights those direct links.
const titles=(ids,data)=>ids.map(id=>data.tasks.find(r=>r.id===id)?.title||id).join(', ');
function dependencyChips(t,data){const blocks=data.tasks.filter(r=>r.blockedBy.includes(t.id)).map(r=>r.id),chip=(kind,label)=>'<button type="button" class="dependency-chip '+kind+'" data-id="'+t.id+'" data-action="dependencies" aria-pressed="false" title="'+esc(tr('show_dependencies'))+'">'+linkIcon+'<span>'+esc(label)+'</span></button>';const chips=(t.blocked?chip('blocked-label',tr('blocked_prefix')+titles(t.blockedBy,data)):'')+(blocks.length?chip('blocking-label',tr('blocks_prefix')+titles(blocks,data)):'');return chips?'<div class="dependency-chips">'+chips+'</div>':''}
const dependencyEdges=data=>dependencyFocus?data.tasks.flatMap(r=>r.blockedBy.filter(p=>p===dependencyFocus||r.id===dependencyFocus).map(p=>[p,r.id])):[];
const dependencyLines=document.createElementNS('http://www.w3.org/2000/svg','svg');dependencyLines.classList.add('dependency-lines');dependencyLines.setAttribute('aria-hidden','true');$('board').after(dependencyLines);
const dependencyResize=new ResizeObserver(()=>drawDependencies());
function showDependencies(data=flow.snapshot()){
 const edges=dependencyEdges(data),board=$('board');if(!edges.length)dependencyFocus=null;
 board.classList.toggle('dependency-focus',!!dependencyFocus);
 for(const card of board.querySelectorAll('[data-task-id]')){
  const id=card.dataset.taskId,role=id===dependencyFocus?'source':edges.some(([p,d])=>p===id&&d===dependencyFocus)?'prerequisite':edges.some(([p,d])=>d===id&&p===dependencyFocus)?'dependent':'';
  if((card.dataset.dependencyRole||'')!==role){if(role)card.dataset.dependencyRole=role;else delete card.dataset.dependencyRole;card.querySelector('.dependency-role')?.remove();if(role&&role!=='source')card.querySelector('.task-title').insertAdjacentHTML('afterend','<span class="dependency-role">'+esc(tr('dependency_'+role))+'</span>')}
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
 if(drag)return;
 const active=document.activeElement,focusTask=active?.dataset?.id,focusAction=active?.dataset?.action,focusProposal=active?.dataset?.proposal,reviewAction=active?.dataset?.reviewAction;
 const openIds=[...$('reviews').querySelectorAll('details[open]')].map(d=>d.dataset.proposal);
 const proposals=data.proposals;
 const newlyRelevant=proposals.find(p=>['review','unavailable'].includes(p.phase)&&!['review','unavailable'].includes(phaseCache.get(p.id)));
 phaseCache=new Map(proposals.map(p=>[p.id,p.phase]));
 $('task-count').textContent=data.tasks.filter(t=>!['done','retired'].includes(t.state)).length+tr("open");
 $('context-open').textContent=data.context.availableMinutes!==null?data.context.availableMinutes+tr("min_available"):tr("my_context");
 $('order-note').innerHTML=orderNote(data);
 $('board').innerHTML=[['active',tr("active")],['todo',tr("to_do")],['snoozed',tr("snoozed")]].map(([state,label])=>{
  const rows=data.tasks.filter(t=>t.state===state),sections=globalThis.dayOrdering.arrange(rows,orderView,{order:localOrder||data.taskOrder||[],suggestion:data.orderSuggestion?.result});
  return '<section class="lane" data-state="'+state+'"><header class="lane-heading"><span class="state-dot" aria-hidden="true"></span><h2>'+label+'</h2><span class="lane-count">'+rows.length+'</span></header><div class="lane-cards">'+sections.map(s=>(orderView==='group'?groupHeading(s):'')+s.tasks.map(t=>{
   const p=proposals.find(p=>p.intent.taskId===t.id);
   return '<article class="task '+(state==='active'?'in-progress':'')+(t.pending?' saving':'')+'" data-task-id="'+t.id+'" data-section="'+esc(state+':'+s.key)+'"><div>'+cardTags(t,s,data)+'<button type="button" class="task-title" data-id="'+t.id+'" data-action="detail" aria-label="'+tr("view_details")+': '+esc(t.title)+'" aria-haspopup="dialog"><span>'+esc(t.title)+'</span>'+arrowIcon+'</button>'+suggestionReason(t,data)+dependencyChips(t,data)+'<p class="task-context">'+esc(t.context)+'</p><p class="task-meta">'+(t.due?'<span class="task-due" title="'+esc(full(t.due))+tr("due")+esc(short(t.due))+'</span>':'')+(t.until?'<span class="task-wait" title="'+esc(full(t.until))+tr("resume")+esc(short(t.until))+'</span>':'')+(t.awaiting?tr("waiting_for_a_reply"):'')+(data.context.availableMinutes!==null&&Number.isFinite(t.remainingMinutes)?'<span>'+t.remainingMinutes+tr("min_remaining_declared_estimate"):'')+'</p>'+(p?'<button class="pending-badge" type="button"'+(data.readOnly?' disabled':'')+' data-id="'+t.id+tr("data_action_pending_pending_change"):'')+suggestionBubble(t,data)+'</div><div class="actions"><button type="button" class="btn state-action" data-id="'+t.id+'" data-action="'+(state==='todo'?'activate':'todo')+'"'+(data.readOnly||t.pending||state==='todo'&&t.blocked?' disabled':'')+'>'+(state==='todo'?tr("start"):tr("to_do"))+'</button><button type="button" class="btn" data-id="'+t.id+'" data-action="snooze"'+(data.readOnly||t.pending?' disabled':'')+' aria-haspopup="dialog">'+clockIcon+tr("snooze_6f15c5");
  }).join('')).join('')+(rows.length?'':tr("nothing_here"))+'</div></section>';
 }).join('');
 showDependencies(data);
 const closed=data.tasks.filter(t=>['done','retired'].includes(t.state));$('closed-count').textContent=closed.length;$('closed').innerHTML=closed.map(t=>'<p class="closed-row"><button type="button" class="link-button" data-closed-id="'+t.id+'">'+esc(t.title)+'</button> · '+(t.state==='done'?tr("completed"):tr("retired"))+'</p>').join('');
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
 if(active&&!active.isConnected){requestAnimationFrame(()=>{if(document.querySelector('dialog[open]'))return;const current=document.activeElement;if(current&&current!==document.body&&current!==active&&current.isConnected)return;let target;if(focusProposal)target=$('reviews').querySelector('[data-proposal="'+focusProposal+'"][data-review-action="'+reviewAction+'"]');if(!target&&focusTask)target=$('board').querySelector('[data-id="'+focusTask+'"][data-action="'+focusAction+'"]')||$('board').querySelector('[data-id="'+focusTask+'"][data-action="detail"]');if(!target)target=shown.length?$('review-title'):$('list-view');target.focus({preventScroll:true})})}
 if(newlyRelevant)notice(tr("a_pending_change_needs_review"));
}
// Confirms the latest saved change, how long it took to show, and Undo when it can be undone.
function announce(last,data){const t=data.tasks.find(t=>t.id===last.taskId),action=aliases[last.action]||last.action;
 const text=last.undoOf?tr('change_undone'):action==='order'?tr('order_saved'):[actionLabel[action]||tr(last.action),t?.title||(action==='declaration'?tr('my_context'):'')].filter(Boolean).join(' · ');
 // The time shown is from the click until the change was on screen.
 const timing=timings.get(last.proposalId);timings.delete(last.proposalId);if(timing?.before&&last.undoable)undoViews.set(String(last.id),{...timing.before,taskId:last.taskId});const ms=timing&&action!=='order'?Math.max(1,Math.round((timing.shown??performance.now())-timing.started)):null;
 toast(text,{undo:last.undoable&&!last.undoOf?last.id:null,detail:[ms===null?'':formatted('toast_ms',{ms}),last.reviewStatus==='deferred'&&data.agent?.connected?tr('toast_reviewed_later'):''].filter(Boolean).join(' · ')})}
async function request(action,payload,key,expectedRevision){return safely(async()=>{const p=await flow.request(action,payload,key,expectedRevision);lastRequestedProposal=p?.id||null;if(p?.phase==='unchanged')notice(p.superseded?.length?tr("the_pending_change_was_discarded_the_respons"):tr("there_are_no_new_changes_to_apply"));else if(p&&p.phase!=='applied')notice(tr('a_pending_change_needs_review'));return p;});}
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
function destination(){if(snoozeMode==='date'){const raw=$('snooze-date').value;if(!raw)throw Error(tr("choose_a_date_and_time"));const d=parseLocal(raw);if(!Number.isFinite(d.getTime())||localValue(d)!==raw)throw Error(tr("that_local_date_or_time_does_not_exist_choos"));return d}const raw=$('snooze-quantity').value,n=Number(raw),unit=$('snooze-unit').value;if(!raw.trim()||!Number.isFinite(n)||n<=0)throw Error(tr("use_a_quantity_greater_than_zero"));if(unit==='days'&&!Number.isInteger(n))throw Error(tr("calendar_days_require_an_integer"));const d=new Date(NOW);if(unit==='days'){const wall=new Date(localValue(NOW)+'Z');wall.setUTCDate(wall.getUTCDate()+n);d.setTime(parseLocal(wall.toISOString().slice(0,16)).getTime())}else d.setTime(d.getTime()+n*(unit==='hours'?3600000:60000));if(!Number.isFinite(d.getTime())||d.getFullYear()>9999)throw Error(tr("the_quantity_is_too_large"));if(unit==='days'&&localValue(d).slice(11)!==localValue(NOW).slice(11))throw Error(tr("the_local_time_changes_on_that_day_clarifica"));return d}
function updatePreview(){previewTarget=null;$('snooze-error').hidden=true;$('deadline-warning').hidden=true;$('unit-hint').hidden=snoozeMode!=='duration'||$('snooze-unit').value!=='days';$('snooze-zone').textContent=zone;try{const d=destination();if(d<=NOW)throw Error(tr("choose_a_future_date_and_time"));if(ambiguous(d))throw Error(tr("that_time_repeats_in_this_zone_clarification"));previewTarget=d;$('snooze-preview').textContent=full(d);$('snooze-apply').disabled=false;const t=task(snoozing);if(t?.due&&d>new Date(t.due)){$('deadline-warning').hidden=false;$('deadline-warning').textContent=tr("past_the_deadline")+short(t.due)+tr("review_the_consequence_before_applying")}}catch(e){$('snooze-preview').textContent=tr("date_pending");$('snooze-error').hidden=false;$('snooze-error').textContent=e.message;$('snooze-apply').disabled=true}}
function setMode(mode){snoozeMode=mode;$('duration-fields').hidden=mode!=='duration';$('date-fields').hidden=mode!=='date';$('duration-mode').setAttribute('aria-pressed',String(mode==='duration'));$('date-mode').setAttribute('aria-pressed',String(mode==='date'));updatePreview()}
function openSnooze(id,origin,proposal=null){const t=task(id);if(!t||['done','retired'].includes(t.state))return;snoozing=id;snoozeRevision=flow.snapshot().revision;editingProposal=proposal;$('snooze-task').textContent=t.title;$('snooze-clock').textContent=tr("now")+full(NOW);$('snooze-quantity').value='1';$('snooze-unit').value='hours';const until=proposal?.intent.until||t.until;$('snooze-date').value=localValue(until?new Date(until):new Date(NOW.getTime()+3600000));setMode(until?'date':'duration');openDialog($('snooze-dialog'),origin,id,'snooze');const input=$(snoozeMode==='date'?'snooze-date':'snooze-quantity');input.focus();if(snoozeMode==='duration')input.select()}
$('snooze-form').onsubmit=async e=>{e.preventDefault();if(!previewTarget)return;const id=snoozing,until=previewTarget.toISOString();const p=await request('snooze',{taskId:id,until},undefined,snoozeRevision);if(p)$('snooze-dialog').close('saved')};
$('snooze-dialog').addEventListener('close',()=>{snoozing=null;editingProposal=null;previewTarget=null});$('snooze-cancel').onclick=()=>$('snooze-dialog').close('cancel');$('duration-mode').onclick=()=>setMode('duration');$('date-mode').onclick=()=>setMode('date');for(const id of ['snooze-quantity','snooze-unit','snooze-date'])$(id).addEventListener('input',updatePreview);document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{const [n,unit]=b.dataset.preset.split(':');$('snooze-quantity').value=n;$('snooze-unit').value=unit;setMode('duration')});
function openDetail(id,origin,editEstimate=false){const t=task(id);if(!t||t.pending)return;selected=id;const revision=flow.snapshot().revision;detailRevision=revision;const focusAction=['done','retired'].includes(t.state)?'task_reopen':'activate';const act=async action=>{$('estimate-error').hidden=true;if(await request(action,{uuid:id},undefined,revision))$('detail-dialog').close('proposed');else{$('estimate-error').textContent=$('status').textContent;$('estimate-error').hidden=false}};$('detail-focus').onclick=()=>act(focusAction);$('detail-done').onclick=()=>act('done');$('confirm-retire').onclick=()=>act('retire');$('detail-title').textContent=t.title;$('detail-context').textContent=t.context+' '+t.next;$('detail-data').innerHTML=[[tr("state"),({todo:tr("to_do"),active:tr("active"),snoozed:tr("snoozed"),done:tr("completed"),retired:tr("retired")})[t.state]],[tr("priority"),tr('priority_'+t.priority)],[tr("group"),t.group||tr("no_group")],[tr("deadline"),t.due?full(t.due):tr("no_deadline")],...(t.until?[[tr("resume_b3bd0b"),full(t.until)]]:[]),[tr("time_zone"),zone]].map(([k,v])=>'<div><dt>'+k+'</dt><dd>'+esc(v)+'</dd></div>').join('');$('detail-focus').hidden=t.state==='active';$('detail-focus').textContent=t.state==='snoozed'?tr("resume_b3bd0b"):tr("start");$('estimate-input').value=t.remainingMinutes??'';$('estimate-error').hidden=true;$('retire-confirm').hidden=true;globalThis.dayDaily?.detail(t);openDialog($('detail-dialog'),origin,id,'detail');if(editEstimate){$('estimate-input').focus();$('estimate-input').select()}}
$('estimate-save').onclick=async()=>{const raw=$('estimate-input').value,value=raw.trim()?Number(raw):null;try{await flow.setEstimate(selected,value,detailRevision);$('detail-dialog').close('declared')}catch(error){$('estimate-error').hidden=false;$('estimate-error').textContent=error.message}};
$('detail-retire').onclick=()=>{$('retire-confirm').hidden=false;$('confirm-retire').focus()};$('keep-task').onclick=()=>{$('retire-confirm').hidden=true;$('detail-retire').focus()};
$('closed').onclick=e=>{const b=e.target.closest('[data-closed-id]');if(b)openDetail(b.dataset.closedId,b)};
$('board').onclick=e=>{const sb=e.target.closest('[data-suggestion]');if(sb){const {suggestion,suggestionAction}=sb.dataset;bubbleFor=null;safely(async()=>{const started=performance.now();timed(flow.observe(await api('/api/suggestions/'+suggestion+'/'+suggestionAction,{})),started);await flow.refresh();render()});return}const b=e.target.closest('[data-action]');if(!b){if(dependencyFocus&&!e.target.closest('.task'))toggleDependencies(dependencyFocus);return}const {id,action}=b.dataset;if(action==='suggestion'){bubbleFor=bubbleFor===id?null:id;render();$('board').querySelector('[data-id="'+id+'"][data-action="suggestion"]')?.focus({preventScroll:true});return}if(action==='dependencies')toggleDependencies(id);else if(action==='snooze')openSnooze(id,b);else if(action==='detail')openDetail(id,b);else if(action==='move')return;else if(action==='pending'){const p=flow.snapshot().proposals.find(p=>p.intent.taskId===id);if(p){collapsedProposals.delete(p.id);expandedProposal=p.id;render();$('review-title').scrollIntoView({block:'nearest'});$('review-title').focus()}}else request(action,{taskId:id})};
$('reviews').onclick=async e=>{const summary=e.target.closest('summary');if(summary){const d=summary.parentElement;if(d.open)collapsedProposals.add(d.dataset.proposal);else collapsedProposals.delete(d.dataset.proposal);}const b=e.target.closest('[data-review-action]');if(!b)return;const p=flow.snapshot().proposals.find(p=>p.id===b.dataset.proposal);if(!p)return;if(Number(b.dataset.generation)!==p.generation){notice(tr("the_review_changed_read_the_current_state"));return;}const action=b.dataset.reviewAction;if(action==='cancel'){await flow.cancel(p.id);notice(tr("change_canceled_the_responsibility_keeps_its"))}if(action==='retry')safely(()=>flow.retry(p.id));if(action==='continue')safely(()=>flow.commit(p.id,{generation:p.generation}))};
$('reviews').addEventListener('toggle',e=>{if(!e.target.matches('details'))return;const id=e.target.dataset.proposal;if(e.target.open){collapsedProposals.delete(id);expandedProposal=id}else collapsedProposals.add(id)},true);
$('undo').onclick=()=>{const id=$('undo').dataset.event;$('undo').hidden=true;safely(()=>flow.undo(id))};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&bubbleFor&&!document.querySelector('dialog[open]')){const id=bubbleFor;bubbleFor=null;render();$('board').querySelector('[data-id="'+id+'"][data-action="suggestion"]')?.focus({preventScroll:true})}});
function openContext(origin){const revision=flow.snapshot().revision,c=flow.snapshot().context;$('context-minutes').value=c.availableMinutes??'';$('context-until').value=c.availableUntil?localValue(new Date(c.availableUntil)):'';const openingUntil=$('context-until').value;$('context-rest').checked=c.rest;$('context-error').hidden=true;
 $('context-form').onsubmit=async e=>{e.preventDefault();const raw=$('context-minutes').value,until=$('context-until').value;try{let availableUntil=c.availableUntil;if(raw.trim()){if(until!==openingUntil){const date=parseLocal(until);if(!Number.isFinite(date.getTime())||ambiguous(date)||localValue(date)!==until)throw Error(tr("provide_an_unambiguous_local_date_and_time_f"));availableUntil=date.toISOString()}if(!availableUntil)throw Error(tr("provide_an_unambiguous_local_date_and_time_f"))}await flow.setContext({availableMinutes:raw.trim()?Number(raw):null,availableUntil:raw.trim()?availableUntil:null,rest:$('context-rest').checked},revision);$('context-dialog').close('declared')}catch(error){$('context-error').hidden=false;$('context-error').textContent=error.message}};
 openDialog($('context-dialog'),origin,null,null)}
$('context-open').onclick=e=>openContext(e.currentTarget);$('context-cancel').onclick=()=>$('context-dialog').close('cancel');
$('list-view').onclick=()=>{$('board').classList.add('is-list');$('list-view').setAttribute('aria-pressed','true');$('board-view').setAttribute('aria-pressed','false')};$('board-view').onclick=()=>{$('board').classList.remove('is-list');$('list-view').setAttribute('aria-pressed','false');$('board-view').setAttribute('aria-pressed','true')};
$('order-choice').value=orderView;$('order-choice').onchange=()=>{orderView=$('order-choice').value;try{localStorage.setItem('day-attendant-order',orderView)}catch{}render()};
$('order-note').onclick=e=>{if(e.target.closest('#suggest-order'))safely(async()=>{await deliver('/api/suggestion',{});notice(tr('suggestion_pending'))})};
$('board').addEventListener('keydown',e=>{const handle=e.target.closest?.('.drag-handle');if(!handle||!['ArrowUp','ArrowDown'].includes(e.key)||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey)return;e.preventDefault();const ids=sectionIds(handle.closest('.task').dataset.section),from=ids.indexOf(handle.dataset.id),to=from+(e.key==='ArrowUp'?-1:1);if(to>=0&&to<ids.length&&placeOrder(handle.dataset.id,ids,to))notice(tr('moved_to').replace('{n}',to+1).replace('{total}',ids.length))});
$('board').addEventListener('pointerdown',e=>{const handle=e.target.closest('.drag-handle');if(!handle||e.button!==0||flow.snapshot().readOnly)return;e.preventDefault();handle.focus({preventScroll:true});$('board').setPointerCapture(e.pointerId);const card=handle.closest('.task');drag={id:handle.dataset.id,card,section:card.dataset.section,ids:sectionIds(card.dataset.section),pointer:e.pointerId};card.classList.add('dragging');$('board').classList.add('is-dragging')});
$('board').addEventListener('pointermove',e=>{if(e.pointerId!==drag?.pointer)return;const others=[...$('board').querySelectorAll('.task')].filter(c=>c!==drag.card&&c.dataset.section===drag.section),next=others.find(c=>{const r=c.getBoundingClientRect();return e.clientY<r.top+r.height/2});if(next){if(next.previousElementSibling!==drag.card)next.before(drag.card)}else if(others.length&&others.at(-1).nextElementSibling!==drag.card)others.at(-1).after(drag.card)});
function endDrag(commit){if(!drag)return;const {id,section,ids}=drag,to=sectionIds(section).indexOf(id);drag=null;$('board').classList.remove('is-dragging');if(!(commit&&placeOrder(id,ids,to)))render()}
$('board').addEventListener('pointerup',e=>{if(e.pointerId===drag?.pointer)endDrag(true)});
for(const type of ['pointercancel','lostpointercapture'])$('board').addEventListener(type,e=>{if(e.pointerId===drag?.pointer)endDrag(false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&drag){e.preventDefault();endDrag(false)}});
const themeMedia=matchMedia('(prefers-color-scheme:dark)');let themeChoice='system';try{themeChoice=localStorage.getItem('day-attendant-theme')||'system'}catch{}if(!['system','light','dark'].includes(themeChoice))themeChoice='system';$('theme-choice').value=themeChoice;const applyTheme=()=>root.dataset.theme=themeChoice==='system'?(themeMedia.matches?'dark':'light'):themeChoice;$('theme-choice').onchange=()=>{themeChoice=$('theme-choice').value;try{localStorage.setItem('day-attendant-theme',themeChoice)}catch{}applyTheme()};themeMedia.addEventListener('change',applyTheme);applyTheme();
const compact=matchMedia('(max-width:1279px)');let chatOpen=!compact.matches;
function placeChat(){const modal=$('chat-dialog'),panel=$('assistant'),dock=$('chat-dock');if(compact.matches&&!modal.open&&document.querySelector('dialog[open]:not(#chat-dialog)'))chatOpen=false;$('chat-toggle').setAttribute('aria-expanded',String(chatOpen));$('workspace').classList.toggle('chat-open',chatOpen&&!compact.matches);if(compact.matches){dock.hidden=true;if(panel.parentElement!==modal)modal.append(panel);if(chatOpen&&!modal.open){modal.returnValue='cancel';origins.set(modal,{origin:$('chat-toggle')});modal.showModal()}else if(!chatOpen&&modal.open)modal.close('collapse')}else{if(modal.open)modal.close('reflow');if(panel.parentElement!==dock)dock.append(panel);dock.hidden=!chatOpen}}
$('chat-dialog').addEventListener('close',()=>{if($('chat-dialog').returnValue==='reflow')return;chatOpen=false;placeChat()});$('chat-toggle').onclick=()=>{chatOpen=!chatOpen;placeChat()};$('chat-close').onclick=()=>{chatOpen=false;placeChat();$('chat-toggle').focus({preventScroll:true})};compact.addEventListener('change',placeChat);placeChat();


const aliases={front_activate:'activate',front_todo:'todo',task_snooze:'snooze',task_done:'done',front_retire:'retire',task_restore:'restore',task_add:'add',task_update:'update',task_reopen:'reopen',context_update:'declaration',continuity_update:'declaration',order_update:'order'};
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
 constructor(){this.data={tasks:[],context:{availableMinutes:null,availableUntil:null,rest:false},proposals:[],events:[],messages:[]};this.digest='';this.messageDigest='';this.tag='';this.started=0;this.applied=0;this.loading=0;this.localeLoaded=false;this.queue=Promise.resolve();this.seen=0;this.drafts=0;}
 snapshot(){return this.data;}
 // Each refresh starts one load at once, so a caller that just saved waits only for the load that
 // includes its change. A slower older load never overwrites newer state, and polls never stack.
 refresh(){return this.load()}
 poll(){return this.loading?null:this.load()}
 async load(){const seq=++this.started;this.loading++;try{const raw=await api('/api/state'+(this.tag?'?since='+encodeURIComponent(this.tag):''));if(seq<this.applied)return;this.applied=seq;if(this.offline){this.offline=false;notice(tr("connection_restored"))}if(raw.unchanged){if(raw.tag===this.tag)this.tick(raw.now);else this.tag='';return}this.apply(raw)}catch(error){this.offline=true;this.data.agent={...(this.data.agent||{}),connected:false};$('agent-state').textContent=tr("disconnected");delete $('agent-state').dataset.label;throw error}finally{this.loading--}}
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
  else if(body.action==='context_update')context={...context,...p};
  else return false;
  this.data={...data,tasks,context};render(this.data);return {tasks:data.tasks,context:data.context}}
 // Direct changes go out one at a time, each with the newest revision, so quick consecutive
 // clicks never collide with each other; none of them waits for a state read to be sent.
 async send(body,key,expectedRevision){const revision=expectedRevision??this.data.revision;return this.observe(key?await api('/api/proposals',{...body,key,expected_revision:revision}):await deliver('/api/proposals',body,revision,{refresh:false}))}
 async request(action,payload,key,expectedRevision){const started=performance.now();const body={action:domain[action]||action,payload:{...(payload.taskId?{uuid:payload.taskId}:{}),...(payload.until?{until:payload.until}:{}),...(!payload.taskId?payload:{})}};
  const previewed=this.preview(body),shown=previewed?performance.now():undefined,sent=this.queue.then(()=>this.send(body,key,expectedRevision));this.queue=sent.catch(()=>{});
  let p;try{p=timed(await sent,started,shown,previewed||undefined)}catch(error){if(previewed){this.digest='';this.tag='';await this.refresh().catch(()=>{})}throw error}
  await this.refresh();return {...p,intent:{...p.intent,action:aliases[p.intent.action]||p.intent.action,taskId:p.intent.payload.uuid,until:p.intent.payload.until},concerns:p.facts,superseded:p.result?.superseded||[]};}
 async cancel(id){const r=await api('/api/proposals/'+id+'/cancel',{});await this.refresh();return r}
 async retry(id){const started=performance.now();const r=timed(this.observe(await api('/api/proposals/'+id+'/retry',{})),started);await this.refresh();return r}
 async commit(id,options){const started=performance.now();const r=this.observe(await api('/api/proposals/'+id+'/confirm',{generation:options.generation,without_review:!!options.withoutReview}));timings.set(id,{started});await this.refresh();return r}
 async undo(id){const started=performance.now(),view=undoViews.get(String(id));undoViews.delete(String(id));
  if(view){const back=new Map(view.tasks.map(t=>[t.id,t]));this.data={...this.data,tasks:this.data.tasks.filter(t=>!t.pending&&(back.has(t.id)||t.id!==view.taskId)).map(t=>back.get(t.id)||t),context:view.context};render(this.data)}
  const shown=view?performance.now():undefined,sent=this.queue.then(async()=>this.observe(await deliver('/api/undo',{fact_id:id},this.data.revision,{refresh:false})));this.queue=sent.catch(()=>{});
  let r;try{r=timed(await sent,started,shown)}catch(error){if(view){this.digest='';this.tag='';await this.refresh().catch(()=>{})}throw error}await this.refresh();return r}
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
function focusSuggestion(id){const data=flow.snapshot();if(currentSuggestion(id,data))bubbleFor=id;if($('chat-dialog').open){chatOpen=false;placeChat()}render();const card=$('board').querySelector('[data-task-id="'+id+'"]');card?.scrollIntoView({block:'center'});(card?.querySelector('[data-action="suggestion"]')||card?.querySelector('[data-action="detail"]'))?.focus({preventScroll:true})}
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
globalThis.dayUI={api,deliver,flow,openDialog,openDetail,registerDialog,tr,esc,full,localValue,parseLocal,ambiguous,notice,safely,translateChrome,formatted,creates,showProposal};
})();
