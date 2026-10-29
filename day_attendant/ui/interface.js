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
let flow,snoozing=null,selected=null,editingProposal=null,snoozeMode='duration',previewTarget=null,expandedProposal=null,lastSeenEvent=null,lastRequestedProposal=null,lastGuidanceNotice=null;
let phaseCache=new Map(),collapsedProposals=new Set(),dependencyFocus=null;
let detailRevision,snoozeRevision;
const notice=text=>$('status').textContent=text;
const task=id=>flow.snapshot().tasks.find(t=>t.id===id);
const safely=async fn=>{try{return await fn()}catch(error){notice(error.message);return null}};
let actionLabel,phaseLabel;function refreshLabels(){actionLabel={activate:tr("start"),snooze:tr("snooze"),todo:tr("back_to_to_do"),done:tr("complete"),retire:tr("retire"),restore:tr("undo"),declaration:tr("declaration_updated"),add:tr("create"),update:tr("update")};
phaseLabel={evaluating:tr("the_assistant_is_reviewing"),review:tr("a_consequence_needs_review"),unavailable:tr("review_incomplete"),stale:tr("context_changed"),invalid:tr("adjust_the_change")};
}
refreshLabels();
// Order views change presentation only. A drag stays inside its section (same lane and, in
// those views, the same priority or group) and saves the profile's manual order.
let orderView='priority';try{orderView=localStorage.getItem('day-attendant-order')||'priority'}catch{}if(!globalThis.dayOrdering.VIEWS.includes(orderView))orderView='priority';
let localOrder=null,orderBase=null,orderTimer=null,orderSaving=false,drag=null;
const groupDot=label=>'<span class="group-dot" style="--group-hue:'+globalThis.dayOrdering.hue(label)+'" aria-hidden="true"></span>';
function cardTags(t,section,data){const handle=section.draggable&&section.tasks.length>1&&!data.readOnly?'<button type="button" class="drag-handle" data-id="'+t.id+'" data-action="move" aria-label="'+esc(tr('reorder')+': '+t.title)+'" title="'+esc(tr('reorder_hint'))+'">'+gripIcon+'</button>':'';
 return '<div class="task-tags">'+handle+'<span class="priority-pill" data-priority="'+esc(t.priority)+'">'+priorityIcon(t.priority)+'<span class="visually-hidden">'+esc(tr('priority'))+': </span>'+esc(tr('priority_'+t.priority))+'</span>'+(t.group&&orderView!=='group'?'<span class="group-chip">'+groupDot(t.group)+'<span class="visually-hidden">'+esc(tr('group'))+': </span>'+esc(t.group)+'</span>':'')+'</div>'}
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
   return '<article class="task '+(state==='active'?'in-progress':'')+'" data-task-id="'+t.id+'" data-section="'+esc(state+':'+s.key)+'"><div>'+cardTags(t,s,data)+'<button type="button" class="task-title" data-id="'+t.id+'" data-action="detail" aria-label="'+tr("view_details")+': '+esc(t.title)+'" aria-haspopup="dialog"><span>'+esc(t.title)+'</span>'+arrowIcon+'</button>'+suggestionReason(t,data)+dependencyChips(t,data)+'<p class="task-context">'+esc(t.context)+'</p><p class="task-meta">'+(t.due?'<span class="task-due" title="'+esc(full(t.due))+tr("due")+esc(short(t.due))+'</span>':'')+(t.until?'<span class="task-wait" title="'+esc(full(t.until))+tr("resume")+esc(short(t.until))+'</span>':'')+(t.awaiting?tr("waiting_for_a_reply"):'')+(data.context.availableMinutes!==null&&Number.isFinite(t.remainingMinutes)?'<span>'+t.remainingMinutes+tr("min_remaining_declared_estimate"):'')+'</p>'+(p?'<button class="pending-badge" type="button"'+(data.readOnly?' disabled':'')+' data-id="'+t.id+tr("data_action_pending_pending_change"):'')+'</div><div class="actions"><button type="button" class="btn state-action" data-id="'+t.id+'" data-action="'+(state==='todo'?'activate':'todo')+'"'+(data.readOnly||state==='todo'&&t.blocked?' disabled':'')+'>'+(state==='todo'?tr("start"):tr("to_do"))+'</button><button type="button" class="btn" data-id="'+t.id+'" data-action="snooze"'+(data.readOnly?' disabled':'')+' aria-haspopup="dialog">'+clockIcon+tr("snooze_6f15c5");
  }).join('')).join('')+(rows.length?'':tr("nothing_here"))+'</div></section>';
 }).join('');
 showDependencies(data);
 const closed=data.tasks.filter(t=>['done','retired'].includes(t.state));$('closed-count').textContent=closed.length;$('closed').innerHTML=closed.map(t=>'<p class="closed-row"><button type="button" class="link-button" data-closed-id="'+t.id+'">'+esc(t.title)+'</button> · '+(t.state==='done'?tr("completed"):tr("retired"))+'</p>').join('');
 const shown=proposals.filter(p=>p.phase!=='evaluating'||p.concerns.length||data.mode!=='normal');
 $('review-area').hidden=!shown.length;$('review-count').textContent=shown.length===1?tr("1_pending_change"):shown.length+tr("pending_changes");
 if(!shown.some(p=>p.id===expandedProposal))expandedProposal=shown[0]?.id||null;
 $('reviews').innerHTML=shown.map(p=>{
  const t=data.tasks.find(t=>t.id===p.intent.taskId),isOpen=!collapsedProposals.has(p.id)&&(openIds.includes(p.id)||expandedProposal===p.id);
  const current=['stale','invalid'].includes(p.phase);
  const facts=current?'':p.concerns.map(c=>'<li>'+esc(concernMessage(c))+'</li>').join('');
  const target=p.intent.action==='snooze'?tr("resume_c19473")+esc(full(p.intent.until))+'</p>':'';
  const due=!current&&t?.due&&p.intent.action==='snooze'?tr("due_3dc108")+esc(full(t.due))+'. </p>':'';
  const alternative=p.review?.output?.alternatives?.join(' ')||(p.concerns.some(c=>c.code==='capacity')?tr("you_can_correct_the_remaining_effort_reduce_"):p.concerns.some(c=>c.code==='rest_goal')?tr("you_can_preserve_your_rest_and_resume_later"):tr("you_can_choose_another_time_or_review_the_co"));
  const adds=creates(p.intent.action),insufficient=p.error_code==='review_insufficient',failed=p.phase==='unavailable'&&!insufficient;
  // A failed review of a new undated, unprioritized task leaves little to protect: saving it is the obvious next step.
  const lowRisk=failed&&adds&&!p.intent.payload?.due&&!p.intent.payload?.priority&&!p.concerns.length;
  const button=(action,label,kind='btn')=>'<button type="button" class="'+kind+'" data-proposal="'+p.id+'" data-generation="'+p.generation+'" data-review-action="'+action+'">'+esc(label)+'</button>';
  const cancel=button('cancel',tr('choice_cancel_change'),'btn ghost');
  let buttons=cancel,choices='';
  if(p.phase==='review'){const capacity=p.concerns.some(c=>c.code==='capacity'),restOnly=p.concerns.every(c=>c.code==='rest_goal');buttons=(restOnly?'':button('adjust',p.intent.action==='snooze'?tr("adjust_time"):capacity?tr("correct_estimate"):tr("adjust_context")))+cancel+button('continue',p.intent.action==='snooze'?tr("snooze_anyway"):tr("keep_my_decision"),'btn decision-confirm');}
  if(p.phase==='unavailable'){
   const without=adds?'create_without_review':'apply_without_review',order=lowRisk?['retry_review',without]:[without,'retry_review'];
   buttons=cancel+order.map((choice,i)=>button(choice==='retry_review'?'retry':'without',tr('choice_'+choice),failed&&i===order.length-1?'btn primary':'btn')).join('');
   choices=(lowRisk?'<p class="decision-alternative">'+esc(tr('low_risk_hint'))+'</p>':'')+'<ul class="decision-choices">'+[...order].reverse().concat('cancel_change').map(choice=>'<li><strong>'+esc(tr('choice_'+choice))+'</strong>: '+esc(tr('explain_'+choice))+'</li>').join('')+'</ul><p class="decision-alternative">'+esc(tr('explain_leave_pending'))+'</p>';
  }
  if(p.phase==='stale')buttons=cancel+button('retry',tr("review_again"),'btn primary');
  if(p.phase==='invalid')buttons=button('adjust',tr("adjust"))+cancel;
  const reason=failed?failureReason(p.error_code)+(p.error_code==='agent_missed_known_concern'?'':' '+tr('review_failure_not_about_request')):p.phase==='stale'?tr('stale_explanation'):'';
  const label=p.phase==='unavailable'?tr(insufficient?'review_needs_clarification':'review_could_not_complete'):phaseLabel[p.phase];
  const pending='<p class="decision-pending">'+esc(tr(adds?'not_created_yet':'not_applied_yet'))+'</p>';
  return '<details class="decision" data-proposal="'+p.id+'" data-generation="'+p.generation+'" '+(isOpen?'open':'')+'><summary><span><strong>'+esc((actionLabel[p.intent.action]||tr(p.intent.action))+' · '+(t?.title||p.intent.payload?.title||''))+'</strong><span class="decision-phase">'+esc(label)+tr("codex_contextual_review")+pending+(!current&&p.review?.output?.message?'<p>'+esc(p.review.output.message)+'</p>':'')+target+due+(facts?'<ul class="decision-facts">'+facts+'</ul>':'')+(reason?'<p class="decision-error">'+esc(reason)+'</p>':'')+(p.phase==='review'?'<p class="decision-alternative">'+alternative+'</p>':'')+choices+'<div class="decision-actions">'+buttons+'</div></div></details>';
 }).join('');
 const last=data.events.at(-1);
 if(last&&last.id!==lastSeenEvent){lastSeenEvent=last.id;const t=data.tasks.find(t=>t.id===last.taskId);notice((last.action==='order'?tr('order_saved'):last.action==='restore'?tr("change_undone"):(actionLabel[last.action]||tr(last.action))+' · '+(t?.title||tr("my_context")))+(last.reviewComplete?'':tr("contextual_review_incomplete")));}
 $('undo').hidden=!last||!last.undoable||last.action==='restore';$('undo').dataset.event=last?.id||'';
 if(active&&!active.isConnected){requestAnimationFrame(()=>{if(document.querySelector('dialog[open]'))return;const current=document.activeElement;if(current&&current!==document.body&&current!==active&&current.isConnected)return;let target;if(focusProposal)target=$('reviews').querySelector('[data-proposal="'+focusProposal+'"][data-review-action="'+reviewAction+'"]');if(!target&&focusTask)target=$('board').querySelector('[data-id="'+focusTask+'"][data-action="'+focusAction+'"]')||$('board').querySelector('[data-id="'+focusTask+'"][data-action="detail"]');if(!target)target=shown.length?$('review-title'):$('list-view');target.focus({preventScroll:true})})}
 if(newlyRelevant)notice(tr("a_pending_change_needs_review"));
}
async function request(action,payload,key,expectedRevision){return safely(async()=>{const p=await flow.request(action,payload,key,expectedRevision);lastRequestedProposal=p?.id||null;if(p?.phase==='unchanged')notice(p.superseded?.length?tr("the_pending_change_was_discarded_the_respons"):tr("there_are_no_new_changes_to_apply"));else if(p)notice(p.phase==='applied'?tr("declaration_updated_1a4094"):tr("pending_change_the_assistant_is_reviewing"));return p;});}
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
$('snooze-form').onsubmit=async e=>{e.preventDefault();if(!previewTarget)return;const id=snoozing,until=previewTarget.toISOString(),origin=origins.get($('snooze-dialog'))?.origin;const p=await request('snooze',{taskId:id,until},undefined,snoozeRevision);if(!p)return;if(!$('snooze-dialog').open){await safely(()=>flow.cancel(p.id));return;}expandedProposal=p.id;if(p.concerns?.length)origins.set($('snooze-dialog'),{origin:$('review-title')});$('snooze-dialog').close('proposed');requestAnimationFrame(()=>{if(flow.snapshot().proposals.some(item=>item.id===p.id&&item.concerns.length))$('review-title').focus({preventScroll:false})});};
$('snooze-dialog').addEventListener('close',()=>{snoozing=null;editingProposal=null;previewTarget=null});$('snooze-cancel').onclick=()=>$('snooze-dialog').close('cancel');$('duration-mode').onclick=()=>setMode('duration');$('date-mode').onclick=()=>setMode('date');for(const id of ['snooze-quantity','snooze-unit','snooze-date'])$(id).addEventListener('input',updatePreview);document.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{const [n,unit]=b.dataset.preset.split(':');$('snooze-quantity').value=n;$('snooze-unit').value=unit;setMode('duration')});
function openDetail(id,origin,editEstimate=false){const t=task(id);if(!t)return;selected=id;const revision=flow.snapshot().revision;detailRevision=revision;const focusAction=['done','retired'].includes(t.state)?'task_reopen':'activate';const act=async action=>{$('estimate-error').hidden=true;if(await request(action,{uuid:id},undefined,revision))$('detail-dialog').close('proposed');else{$('estimate-error').textContent=$('status').textContent;$('estimate-error').hidden=false}};$('detail-focus').onclick=()=>act(focusAction);$('detail-done').onclick=()=>act('done');$('confirm-retire').onclick=()=>act('retire');$('detail-title').textContent=t.title;$('detail-context').textContent=t.context+' '+t.next;$('detail-data').innerHTML=[[tr("state"),({todo:tr("to_do"),active:tr("active"),snoozed:tr("snoozed"),done:tr("completed"),retired:tr("retired")})[t.state]],[tr("priority"),tr('priority_'+t.priority)],[tr("group"),t.group||tr("no_group")],[tr("deadline"),t.due?full(t.due):tr("no_deadline")],...(t.until?[[tr("resume_b3bd0b"),full(t.until)]]:[]),[tr("time_zone"),zone]].map(([k,v])=>'<div><dt>'+k+'</dt><dd>'+esc(v)+'</dd></div>').join('');$('detail-focus').hidden=t.state==='active';$('detail-focus').textContent=t.state==='snoozed'?tr("resume_b3bd0b"):tr("start");$('estimate-input').value=t.remainingMinutes??'';$('estimate-error').hidden=true;$('retire-confirm').hidden=true;globalThis.dayDaily?.detail(t);openDialog($('detail-dialog'),origin,id,'detail');if(editEstimate){$('estimate-input').focus();$('estimate-input').select()}}
$('estimate-save').onclick=async()=>{const raw=$('estimate-input').value,value=raw.trim()?Number(raw):null;try{await flow.setEstimate(selected,value,detailRevision);notice(tr("estimate_updated_the_responsibility_keeps_it"));$('detail-dialog').close('declared')}catch(error){$('estimate-error').hidden=false;$('estimate-error').textContent=error.message}};
$('detail-retire').onclick=()=>{$('retire-confirm').hidden=false;$('confirm-retire').focus()};$('keep-task').onclick=()=>{$('retire-confirm').hidden=true;$('detail-retire').focus()};
$('closed').onclick=e=>{const b=e.target.closest('[data-closed-id]');if(b)openDetail(b.dataset.closedId,b)};
$('board').onclick=e=>{const b=e.target.closest('[data-action]');if(!b){if(dependencyFocus&&!e.target.closest('.task'))toggleDependencies(dependencyFocus);return}const {id,action}=b.dataset;if(action==='dependencies')toggleDependencies(id);else if(action==='snooze')openSnooze(id,b);else if(action==='detail')openDetail(id,b);else if(action==='move')return;else if(action==='pending'){const p=flow.snapshot().proposals.find(p=>p.intent.taskId===id);if(p){collapsedProposals.delete(p.id);expandedProposal=p.id;render();$('review-title').scrollIntoView({block:'nearest'});$('review-title').focus()}}else request(action,{taskId:id})};
$('reviews').onclick=async e=>{const summary=e.target.closest('summary');if(summary){const d=summary.parentElement;if(d.open)collapsedProposals.add(d.dataset.proposal);else collapsedProposals.delete(d.dataset.proposal);}const b=e.target.closest('[data-review-action]');if(!b)return;const p=flow.snapshot().proposals.find(p=>p.id===b.dataset.proposal);if(!p)return;if(Number(b.dataset.generation)!==p.generation){notice(tr("the_review_changed_read_the_current_state"));return;}const action=b.dataset.reviewAction;if(action==='cancel'){await flow.cancel(p.id);notice(tr("change_canceled_the_responsibility_keeps_its"))}if(action==='retry')safely(()=>flow.retry(p.id));if(action==='continue')safely(()=>flow.commit(p.id,{generation:p.generation}));if(action==='without')safely(()=>flow.commit(p.id,{generation:p.generation,withoutReview:true}));if(action==='adjust'){if(p.intent.action==='snooze')openSnooze(p.intent.taskId,b,p);else if(p.concerns.some(c=>c.code==='capacity'))openDetail(p.intent.taskId,b,true);else openContext(b)}};
$('reviews').addEventListener('toggle',e=>{if(!e.target.matches('details'))return;const id=e.target.dataset.proposal;if(e.target.open){collapsedProposals.delete(id);expandedProposal=id}else collapsedProposals.add(id)},true);
$('undo').onclick=()=>safely(()=>flow.undo($('undo').dataset.event));
function openContext(origin){const revision=flow.snapshot().revision,c=flow.snapshot().context;$('context-minutes').value=c.availableMinutes??'';$('context-until').value=c.availableUntil?localValue(new Date(c.availableUntil)):'';const openingUntil=$('context-until').value;$('context-rest').checked=c.rest;$('context-error').hidden=true;
 $('context-form').onsubmit=async e=>{e.preventDefault();const raw=$('context-minutes').value,until=$('context-until').value;try{let availableUntil=c.availableUntil;if(raw.trim()){if(until!==openingUntil){const date=parseLocal(until);if(!Number.isFinite(date.getTime())||ambiguous(date)||localValue(date)!==until)throw Error(tr("provide_an_unambiguous_local_date_and_time_f"));availableUntil=date.toISOString()}if(!availableUntil)throw Error(tr("provide_an_unambiguous_local_date_and_time_f"))}await flow.setContext({availableMinutes:raw.trim()?Number(raw):null,availableUntil:raw.trim()?availableUntil:null,rest:$('context-rest').checked},revision);$('context-dialog').close('declared');notice(tr("declared_context_updated"))}catch(error){$('context-error').hidden=false;$('context-error').textContent=error.message}};
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
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=Error(errorText(result.error?.code,result.error?.message||tr("the_request_could_not_be_completed")));error.code=result.error?.code;error.status=response.status;throw error;}return result.data;}

// A delivery keeps its identity until its receipt and refreshed state are known.
// Repeating a successful action later is a new intent; network retries are not.
const deliveries=new Map();
try{for(const [k,v] of JSON.parse(sessionStorage.getItem('day-attendant-deliveries')||'[]'))deliveries.set(k,v)}catch{}
function reconcileDeliveries(raw){for(const [identity,body] of deliveries){const route=identity.slice(0,identity.indexOf('{'));const seen=route==='/api/chat'?raw.messages.some(m=>m.role==='user'&&m.key===body.key):raw.proposals.some(p=>p.key===body.key);if(!seen)continue;if(route==='/api/chat'&&$('conversation-input').value.trim()===body.text.trim())$('conversation-input').value='';deliveries.delete(identity);saveDeliveries();}}
function saveDeliveries(){try{sessionStorage.setItem('day-attendant-deliveries',JSON.stringify([...deliveries]))}catch{}}
async function deliver(route,data,revision){
 const identity=route+JSON.stringify(data);let body=deliveries.get(identity);
 if(!body){body={...data,key:crypto.randomUUID(),...(revision===undefined?{}:{expected_revision:revision})};deliveries.set(identity,body);saveDeliveries()}
 try{const result=await api(route,body);await flow.refresh();deliveries.delete(identity);saveDeliveries();return result}
 catch(error){if(error.status>=400&&error.status<500){deliveries.delete(identity);saveDeliveries()}throw error}
}

class RemoteFlow{
 constructor(){this.data={tasks:[],context:{availableMinutes:null,availableUntil:null,rest:false},proposals:[],events:[],messages:[]};this.digest='';this.messageDigest='';this.busy=false;this.localeLoaded=false;}
 snapshot(){return this.data;}
 // A refresh requested while one is in flight runs again afterwards, so a caller that just
 // saved always waits for state that includes its own change.
 refresh(){if(this.busy){this.again=true;return this.pending}this.busy=true;this.pending=(async()=>{try{do{this.again=false;await this.load()}while(this.again)}finally{this.busy=false}})();return this.pending}
 async load(){try{const raw=await api('/api/state');if(!this.localeLoaded&&raw.profile){this.localeLoaded=true;locale=raw.profile.locale||'en';$('locale-choice').value=locale;translateChrome();refreshLabels()}NOW=new Date(raw.now);zone=raw.timezone||zone;const state={...raw,mode:'connected',rawProposals:raw.proposals,proposals:raw.proposals.filter(p=>['evaluating','review','unavailable','stale','invalid'].includes(p.phase)).map(p=>({...p,intent:{...p.intent,action:aliases[p.intent.action]||p.intent.action,taskId:p.intent.payload.uuid,until:p.intent.payload.until},concerns:p.facts,superseded:p.result?.superseded||[]})),events:raw.events.map(e=>{const plan=JSON.parse(e.plan);return {...e,action:aliases[e.action]||e.action,undoable:plan.steps.length>0&&Object.keys(plan.effect).every(k=>k==='attachments_link'),taskId:plan.target_uuid,reviewComplete:plan.review_status==='not_required'||plan.review_complete!==false,undoOf:plan.undo_of};})};if(this.offline){this.offline=false;notice(tr("connection_restored"))}this.data=state;reconcileDeliveries(raw);globalThis.dayDaily?.update(state);const digest=JSON.stringify([state.tasks,state.taskOrder,state.orderSuggestion,state.agent.connected,state.context,state.proposals,state.events,state.revision,state.readOnly]);if(digest!==this.digest){this.digest=digest;render(state)}const md=JSON.stringify(state.messages);if(md!==this.messageDigest){this.messageDigest=md;renderMessages(state.messages)}const setText=(node,value)=>{if(node.textContent!==value)node.textContent=value};const agentError=state.agent.error&&errorText(state.agent.errorCode,state.agent.error);setText($('agent-state'),state.agent.connected?tr("connected"):tr("disconnected"));$('bridge-state').hidden=state.agent.connected;if(!state.agent.connected)setText($('bridge-state'),agentError||tr('agent_temporarily_unavailable'));renderContextStatus(state);setText($('agent-details'),(state.agent.model||(state.profile?.harness==='claude'?'Claude Code':'Codex'))+' · '+(state.profile?.harness||'codex'));if($('agent-state').title!==(agentError||''))$('agent-state').title=agentError||'';setText(document.querySelector('.date'),new Intl.DateTimeFormat(formatLocale(),{weekday:'long',day:'numeric',month:'long',timeZone:zone}).format(NOW));}catch(error){this.offline=true;this.data.agent={...(this.data.agent||{}),connected:false};$('agent-state').textContent=tr("disconnected");throw error}}
 async request(action,payload,key,expectedRevision){const body={action:domain[action]||action,payload:{...(payload.taskId?{uuid:payload.taskId}:{}),...(payload.until?{until:payload.until}:{}),...(!payload.taskId?payload:{})}};const p=key?await api('/api/proposals',{...body,key,expected_revision:expectedRevision??this.data.revision}):await deliver('/api/proposals',body,expectedRevision??this.data.revision);await this.refresh();return {...p,intent:{...p.intent,action:aliases[p.intent.action]||p.intent.action,taskId:p.intent.payload.uuid,until:p.intent.payload.until},concerns:p.facts,superseded:p.result?.superseded||[]};}
 async cancel(id){const r=await api('/api/proposals/'+id+'/cancel',{});await this.refresh();return r}
 async retry(id){const r=await api('/api/proposals/'+id+'/retry',{});await this.refresh();return r}
 async commit(id,options){const r=await api('/api/proposals/'+id+'/confirm',{generation:options.generation,without_review:!!options.withoutReview});await this.refresh();return r}
 async undo(id){const r=await deliver('/api/undo',{fact_id:id},this.data.revision);await this.refresh();return r}
 async setEstimate(id,value,revision){return this.request('continuity_update',{uuid:id,remainingMinutes:value},undefined,revision)}
 async setContext(value,revision){return this.request('context_update',{availableMinutes:value.availableMinutes,availableUntil:value.availableUntil,rest:value.rest},undefined,revision)}
}
function renderMessages(messages){const box=$('messages'),oldScroll=box.scrollTop,nearEnd=box.scrollHeight-box.clientHeight-box.scrollTop<70;box.replaceChildren();for(const item of messages){const el=document.createElement('div');el.className='message'+(item.role==='user'?' user':'');const h=document.createElement('strong');h.textContent=item.role==='user'?tr("you"):item.status==='incomplete'?tr("assistant_incomplete_response"):tr("assistant");const p=document.createElement('p');p.textContent=messageText(item);el.append(h,p);if(item.role==='user'&&item.status==='incomplete'){const retry=document.createElement('button');retry.type='button';retry.className='btn';retry.textContent=tr('retry_reply');retry.onclick=()=>safely(async()=>{await api('/api/chat/retry',{key:item.key});await flow.refresh()});el.append(retry)}if(item.status==='pending'){const status=document.createElement('p');status.className='small muted';status.textContent=tr("the_assistant_is_responding");el.append(status)}box.append(el)}if(nearEnd)box.scrollTop=box.scrollHeight;else box.scrollTop=oldScroll;const guidance=messages.findLast(m=>{try{return JSON.parse(m.data||'null')?.kind==='context_notice'}catch{return false}});if(guidance){const signature=guidance.id+':'+guidance.status+':'+guidance.text;if(signature!==lastGuidanceNotice){lastGuidanceNotice=signature;notice(messageText(guidance))}}}
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
$('locale-choice').value=locale;translateChrome();$('locale-choice').onchange=()=>{locale=$('locale-choice').value;try{localStorage.setItem('day-attendant-locale',locale)}catch{}translateChrome();refreshLabels();render();renderMessages(flow.snapshot().messages);renderContextStatus(flow.snapshot());notice('');safely(()=>deliver('/api/settings',{values:{locale}}));};
async function boot(){const token=new URLSearchParams(location.hash.slice(1)).get('session');if(token){await api('/api/session',{token});history.replaceState(null,'',location.pathname)}await flow.refresh();}
addEventListener('hashchange',()=>{if(new URLSearchParams(location.hash.slice(1)).has('session'))safely(boot)});
safely(boot);
setInterval(()=>safely(()=>flow.refresh()),900);
globalThis.daySnapshot=()=>structuredClone(flow.snapshot());
globalThis.dayUI={api,deliver,flow,openDialog,openDetail,registerDialog,tr,esc,full,localValue,parseLocal,ambiguous,notice,safely,translateChrome,formatted,creates,showProposal};
})();
