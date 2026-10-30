/* Daily controls extend the approved interface without owning domain decisions. */
(() => {
'use strict';
const ui=globalThis.dayUI;
const $=id=>document.getElementById(id),{tr,esc,api,deliver,flow,safely,notice}=ui;
const text=(en,es)=>document.documentElement.lang==='es'?es:en;
const button=(id,en,es,extra='')=>`<button type="button" class="btn ${extra}" id="${id}">${esc(text(en,es))}</button>`;
function field(id,en,es,type='text',extra=''){return `<label class="field"><span>${esc(text(en,es))}</span><input id="${id}" type="${type}" ${extra}></label>`}
function dialog(id,title,body,footer){
 const el=document.createElement('dialog');el.id=id;el.setAttribute('aria-labelledby',id+'-title');el.innerHTML=`<form class="modal-form"><header class="modal-header"><h2 id="${id}-title">${esc(title)}</h2><button type="button" class="btn icon ghost" aria-label="${esc(tr('cancel'))}" data-dismiss>×</button></header><div class="modal-body">${body}<p class="error" role="alert" id="${id}-error" hidden></p></div><footer class="modal-footer">${footer}</footer></form>`;
 document.body.append(el);ui.registerDialog(el);el.querySelector('[data-dismiss]').onclick=()=>el.close('cancel');return el;
}
function fail(id,error){$(id+'-error').hidden=false;$(id+'-error').textContent=error.message}
async function formAction(id,fn){$(id+'-error').hidden=true;const buttons=[...$(id).querySelectorAll('button[type=submit]')];buttons.forEach(b=>b.disabled=true);try{await fn()}catch(e){fail(id,e)}finally{buttons.forEach(b=>b.disabled=false)}}
function requiredTime(id){const raw=$(id).value;if(!raw)return null;const date=ui.parseLocal(raw);if(!Number.isFinite(date.getTime())||ui.localValue(date)!==raw||ui.ambiguous(date))throw Error(text('Choose an unambiguous local date and time.','Elige una fecha y hora local inequívoca.'));return date.toISOString()}
function storedTime(id,instant){$(id).value=instant?ui.localValue(new Date(instant)):'';const opening=$(id).value;return ()=>$(id).value===opening?instant||null:requiredTime(id)}
function submit(label){return `<button type="submit" class="btn primary">${esc(label)}</button>`}
function open(el,origin){ui.openDialog(el,origin);el.querySelector('input,select,button')?.focus()}
let eventId=null,captureRevision=null,settingsRevision=null,lastProfile=null,lastDailyDigest='',lastDiagnostics='',lastCalendar='',selectedImages=[];
const toolbar=document.querySelector('.toolbar-right');toolbar.insertAdjacentHTML('afterbegin',button('capture-open','Add with details','Agregar con detalles'));
document.querySelector('.app-controls').insertAdjacentHTML('beforeend','<button type="button" class="btn" id="settings-open" aria-label="Settings">⚙ <span>Settings</span></button>');
const diagnostic=document.createElement('div');diagnostic.className='diagnostic-banner';diagnostic.hidden=true;diagnostic.id='daily-diagnostics';$('tasks-area').insertBefore(diagnostic,document.querySelector('.toolbar'));
const calendar=document.createElement('section');calendar.className='calendar-area';calendar.id='calendar-area';$('tasks-area').append(calendar);

function groupOptions(id){return `<datalist id="${id}">${[...new Set(flow.snapshot().tasks.map(t=>t.group).filter(Boolean))].sort((a,b)=>a.localeCompare(b)).map(g=>`<option value="${esc(g)}">`).join('')}</datalist>`}
const groupField=id=>field(id,'Group (optional)','Grupo (opcional)','text',`maxlength="120" list="${id}-options" placeholder="${esc(text('Project, initiative or category','Proyecto, iniciativa o categoría'))}"`)+groupOptions(id+'-options');
// Most pressing first; a new item starts at Medium.
const priorityOptions=()=>globalThis.dayOrdering.PRIORITIES.map(p=>`<option value="${p}"${p==='medium'?' selected':''}>${esc(tr('priority_'+p))}</option>`).join('');
function buildCapture(){
 $('capture-dialog')?.remove();return dialog('capture-dialog',text('New item','Nuevo elemento'),`<div class="daily-grid"><label class="field wide"><span>${text('Kind','Tipo')}</span><select id="capture-kind"><option value="task">${text('Responsibility','Responsabilidad')}</option><option value="event">${text('Calendar commitment','Compromiso de agenda')}</option></select></label>${field('capture-title','Title','Título','text','maxlength="2000" required class="wide"')}<label class="field" id="capture-priority-field"><span>${tr('priority')}</span><select id="capture-priority">${priorityOptions()}</select></label>${field('capture-due','Deadline (optional)','Vencimiento (opcional)','datetime-local')}${groupField('capture-group')}<div id="event-fields" class="daily-grid wide" hidden>${field('capture-start','Starts','Inicio','datetime-local')}${field('capture-end','Ends','Fin','datetime-local')}</div><details class="wide daily-section"><summary>${text('Source and observation time','Origen y hora de observación')}</summary><div class="daily-grid">${field('capture-person','Person (optional)','Persona (opcional)')}${field('capture-reference','Reference (optional)','Referencia (opcional)')}<label class="field"><span>${text('Source','Origen')}</span><select id="capture-source"><option value="general">General</option><option value="chat">Chat</option><option value="email">Email</option><option value="call">${text('Call','Llamada')}</option><option value="ticket">Ticket</option><option value="pr">PR</option><option value="research">${text('Research','Investigación')}</option></select></label>${field('capture-observed','Observed at (optional)','Observado el (opcional)','datetime-local')}</div><p class="hint">${text('Observation time and capture time are separate. This does not open an inbox.','La observación y la captura tienen horas separadas. Esto no abre ninguna bandeja.')}</p></details></div>`,submit(tr('save_context').replace(/ contexto?$/i,'')));
}
function openCapture(origin,event=null,draft=null){
 const d=buildCapture();eventId=event?.uuid||null;captureRevision=flow.snapshot().revision;$('capture-title').value=event?.title||draft?.title||'';
 if(draft){if(draft.priority)$('capture-priority').value=draft.priority;if(draft.group)$('capture-group').value=draft.group;if(draft.due)$('capture-due').value=ui.localValue(new Date(draft.due))}
 $('capture-kind').value=event?'event':'task';$('capture-kind').disabled=!!event;
 const kind=()=>{const isEvent=$('capture-kind').value==='event';$('event-fields').hidden=!isEvent;$('capture-due').closest('label').hidden=isEvent;$('capture-priority-field').hidden=isEvent;$('capture-group').closest('label').hidden=isEvent};$('capture-kind').onchange=kind;kind();
 const start=storedTime('capture-start',event?.start_at),end=storedTime('capture-end',event?.end_at);if(event)d.querySelector('details').hidden=true;
 d.querySelector('form').onsubmit=e=>{e.preventDefault();formAction(d.id,async()=>{const title=$('capture-title').value.trim();if(!title)throw Error(text('Enter a title.','Escribe un título.'));const isEvent=$('capture-kind').value==='event';let payload={title};if(isEvent){payload.start=start();payload.end=end();if(!payload.start||!payload.end)throw Error(text('Choose the start and end.','Elige el inicio y el fin.'));if(eventId)payload.uuid=eventId}else{payload.priority=$('capture-priority').value;payload.due=requiredTime('capture-due');const group=$('capture-group').value.trim();if(group)payload.group=group}
 if(!eventId){payload.source=$('capture-source').value;for(const [name,id] of [['person','capture-person'],['reference','capture-reference']])if($(id).value.trim())payload[name]=$(id).value.trim();const observed=requiredTime('capture-observed');if(observed)payload.observed_at=observed}
 await flow.request(eventId?'event_update':isEvent?'event_add':'task_add',payload,undefined,captureRevision);if(draft&&$('qa-input').value===draft.raw){$('qa-input').value='';renderQuick()}d.close('saved')})};open(d,origin);
}
// The full form starts from what the quick line already says; an unreadable line starts empty.
$('capture-open').onclick=e=>{const q=parseQuick($('qa-input').value);openCapture(e.currentTarget,null,q.error?null:q)};

// One line above the list: «pagar luz mañana !2 #Casa» and Enter. Every token it reads is shown in the
// preview before saving; a word it cannot place stays in the title, and a doubtful line is not saved.
const PRIORITY_WORDS={1:'urgent',2:'high',3:'medium',4:'low',urgente:'urgent',urgent:'urgent',alta:'high',high:'high',media:'medium',medium:'medium',baja:'low',low:'low'};
const DAY_WORDS={hoy:0,today:0,'mañana':1,manana:1,tomorrow:1};
const WEEKDAYS={domingo:0,sunday:0,lunes:1,monday:1,martes:2,tuesday:2,'miércoles':3,miercoles:3,wednesday:3,jueves:4,thursday:4,viernes:5,friday:5,'sábado':6,sabado:6,saturday:6};
// «la mañana», «esta mañana»: the morning, not tomorrow.
const MORNING=new Set(['la','esta','una','cada','toda','media','en']);
const LEADS=new Set(['el','para','on','by']);
const CLOCK=/^([01]?\d|2[0-3]):([0-5]\d)$/;
function quickDue(days,weekday,time){
 const [y,m,d]=ui.localValue(new Date(flow.snapshot().now||Date.now())).slice(0,10).split('-').map(Number),day=new Date(Date.UTC(y,m-1,d));
 // A weekday is its next occurrence; today's own weekday is a week ahead, as the approved demo does.
 if(weekday!==null){let add=(weekday-day.getUTCDay()+7)%7;days=add||7}
 day.setUTCDate(day.getUTCDate()+days);const raw=day.toISOString().slice(0,10)+'T'+(time||'18:00'),date=ui.parseLocal(raw);
 if(!Number.isFinite(date.getTime())||ui.localValue(date)!==raw||ui.ambiguous(date))return {error:text('That date and time is not unambiguous here. Choose it in Add with details.','Esa fecha y hora no es inequívoca aquí. Elígela en Agregar con detalles.')};
 return {due:date.toISOString()};
}
function parseQuick(value){
 const words=value.trim().split(/\s+/).filter(Boolean),title=[],errors=[];let priority=null,group=null,isNew=false,due=null,dated=false;
 const known=[...new Set(flow.snapshot().tasks.map(t=>t.group).filter(Boolean))];
 const twice=(en,es)=>errors.push(text(en,es));
 for(let i=0;i<words.length;i++){
  const word=words[i],bare=word.toLocaleLowerCase().replace(/[.,;]+$/,''),next=(words[i+1]||'').toLocaleLowerCase().replace(/[.,;]+$/,'');
  const p=/^!(\S+)$/.exec(bare);if(p&&PRIORITY_WORDS[p[1]]){if(priority)twice('Two priorities: keep one.','Dos prioridades: deja una.');priority=PRIORITY_WORDS[p[1]];continue}
  const g=/^#([\p{L}\p{N}_-]{1,120})$/u.exec(word.replace(/[.,;]+$/,''));if(g){if(group)twice('Two groups: a task has one group.','Dos grupos: una tarea tiene un solo grupo.');const match=known.find(k=>k.toLocaleLowerCase()===g[1].toLocaleLowerCase());group=match||g[1];isNew=!match;continue}
  let days=null,weekday=null,span=1;
  if(bare==='pasado'&&['mañana','manana'].includes(next)){days=2;span=2}
  else if(bare==='day'&&next==='after'&&(words[i+2]||'').toLocaleLowerCase().replace(/[.,;]+$/,'')==='tomorrow'){days=2;span=3}
  else if(bare in DAY_WORDS&&!(DAY_WORDS[bare]===1&&bare!=='tomorrow'&&MORNING.has((title.at(-1)||'').toLocaleLowerCase())))days=DAY_WORDS[bare];
  else if(bare in WEEKDAYS)weekday=WEEKDAYS[bare];
  if(days===null&&weekday===null){title.push(word);continue}
  if(dated)twice('Two dates: keep one.','Dos fechas: deja una.');dated=true;
  while(title.length&&LEADS.has(title.at(-1).toLocaleLowerCase()))title.pop();
  i+=span-1;const clock=CLOCK.exec(words[i+1]||'');if(clock)i++;
  const result=quickDue(days,weekday,clock?clock[1].padStart(2,'0')+':'+clock[2]:null);if(result.error)errors.push(result.error);else due=result.due;
 }
 let name=title.join(' ');name=name.charAt(0).toLocaleUpperCase()+name.slice(1);
 if(!name&&words.length)errors.push(text('Write what to add, not only its details.','Escribe qué agregar, no solo sus detalles.'));
 if(name.length>2000)errors.push(text('The title is too long.','El título es demasiado largo.'));
 return {raw:value,title:name,priority,group,isNew,due,error:errors[0]||null};
}
function renderQuick(showError=false){
 const value=$('qa-input').value,box=$('qa-preview');
 if(!value.trim()){box.innerHTML=`<span class="qa-hint">${esc(text('Also understands:','También entiende:'))} <span class="kbd">!1</span>…<span class="kbd">!4</span> ${esc(tr('priority').toLocaleLowerCase())} · <span class="kbd">#${esc(text('group','grupo'))}</span> · <span class="kbd">${esc(text('today','hoy'))}</span> <span class="kbd">${esc(text('tomorrow','mañana'))}</span> <span class="kbd">${esc(text('friday','viernes'))}</span> <span class="kbd">9:30</span> ${esc(text('for the deadline','para el vencimiento'))}</span>`;return}
 const q=parseQuick(value);
 if(q.error&&(showError||!q.title)){box.innerHTML=`<span class="qa-error">${esc(q.error)}</span>`;return}
 const past=q.due&&new Date(q.due)<new Date(flow.snapshot().now||Date.now());
 box.innerHTML=`<span>${esc(text('Will add:','Se agregará:'))}</span><b>${esc(q.title)}</b><span class="priority-pill" data-priority="${q.priority||'medium'}">${esc(tr('priority_'+(q.priority||'medium')))}</span>`
  +(q.group?`<span class="qa-group">${ui.groupDot(q.group)}${esc(q.group)}${q.isNew?` <span class="muted">(${esc(text('new group','grupo nuevo'))})</span>`:''}</span>`:'')
  +(q.due?`<span${past?' class="qa-warn"':''}>${esc(text('due ','vence ')+ui.full(q.due))}${past?' · '+esc(text('already past','ya pasó')):''}</span>`:'')
  +(q.error?`<span class="qa-error">${esc(q.error)}</span>`:`<span>· Enter</span>`);
}
const quick=document.createElement('form');quick.className='quick-add';quick.id='quick-add';quick.autocomplete='off';
quick.innerHTML=`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg><input id="qa-input" maxlength="2400" enterkeyhint="done" aria-describedby="qa-preview"><button class="btn primary" type="submit" id="qa-submit"></button>`;
const quickPreview=document.createElement('div');quickPreview.className='qa-preview';quickPreview.id='qa-preview';quickPreview.setAttribute('aria-live','polite');
$('tasks-area').insertBefore(quick,document.querySelector('.toolbar'));$('tasks-area').insertBefore(quickPreview,document.querySelector('.toolbar'));
const quickLabels=()=>{$('qa-input').placeholder=text('Add a task…  e.g. “pay electricity tomorrow !2 #Home”','Agregar tarea…  ej. «pagar luz mañana !2 #Casa»');$('qa-input').setAttribute('aria-label',text('Add a task','Agregar tarea'));$('qa-submit').textContent=text('Add','Agregar')};
$('qa-input').addEventListener('input',()=>renderQuick());
// Local keys only: Enter adds, Esc clears the line, then leaves it.
$('qa-input').addEventListener('keydown',e=>{if(e.key!=='Escape')return;e.preventDefault();e.stopPropagation();if($('qa-input').value){$('qa-input').value='';renderQuick()}else $('qa-input').blur()});
quick.onsubmit=e=>{e.preventDefault();const input=$('qa-input'),raw=input.value;if(!raw.trim()){input.focus();return}const q=parseQuick(raw);if(q.error){renderQuick(true);input.focus();return}
 const payload={title:q.title,priority:q.priority||'medium',source:'general',...(q.group?{group:q.group}:{}),...(q.due?{due:q.due}:{})};
 // The card shows at once; the line is free for the next one while the service saves it.
 input.value='';renderQuick();input.focus();
 safely(async()=>{try{await flow.request('task_add',payload)}catch(error){if(!input.value){input.value=raw;renderQuick()}throw error}})};

function buildSettings(){
 $('settings-dialog')?.remove();const days=text(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],['Lun','Mar','Mié','Jue','Vie','Sáb','Dom']);
 return dialog('settings-dialog',text('Daily settings','Configuración diaria'),`<div class="daily-grid">${field('setting-name','Profile name','Nombre del perfil')}${field('setting-zone','Timezone','Zona horaria')}<label class="field"><span>${text('Assistant app','Aplicación del asistente')}</span><select id="setting-harness"><option value="codex">Codex</option><option value="claude">Claude Code</option></select></label><label class="field"><span>${text('Model','Modelo')}</span><select id="setting-model"></select></label><label class="field"><span>${text('Reasoning level','Nivel de razonamiento')}</span><select id="setting-effort"></select></label><p class="hint wide">${text('Uses your existing subscription login. No extra purchases or automatic app changes. Your model choice is saved. Card text keeps its original language.','Usa tu sesión de suscripción existente. Sin compras adicionales ni cambios automáticos de aplicación. Se guarda el modelo elegido. Las tarjetas conservan su idioma original.')}</p></div><details class="daily-section" open><summary>${text('Local reminders','Recordatorios locales')}</summary><label class="context-checkbox"><input id="policy-enabled" type="checkbox">${text('Deliver local reminders','Entregar recordatorios locales')}</label><div class="day-list">${days.map((day,i)=>`<label><input type="checkbox" data-day="${i}">${day}</label>`).join('')}</div><div class="daily-grid">${field('policy-start','From','Desde','time')}${field('policy-end','Until','Hasta','time')}${field('policy-quiet-start','Quiet from','Silencio desde','time')}${field('policy-quiet-end','Quiet until','Silencio hasta','time')}${field('policy-interval','Check every (minutes)','Revisar cada (minutos)','number','min="1" max="1440"')}${field('policy-repeat','Repeat unchanged after (minutes)','Repetir sin cambios tras (minutos)','number','min="1" max="10080"')}${field('policy-budget','Maximum delivery attempts / day','Máximo de intentos de aviso / día','number','min="1" max="100"')}${field('policy-pause','Pause until (optional)','Pausar hasta (opcional)','datetime-local')}</div><p class="hint">${text('Initial schedule: weekdays 09:00–18:00. This is a delivery default, not an estimate of your availability. Reminders do not call the assistant.','Horario inicial: laborables 09:00–18:00. Es un valor de entrega, no una estimación de tu disponibilidad. Los recordatorios no llaman al asistente.')}</p></details><details class="daily-section"><summary>${text('Backups and operation','Respaldos y operación')}</summary><p class="hint">${text('Automatic daily backup: 7 daily + 4 weekly copies. A copy on this disk does not protect against losing the disk. Restore to a new folder using the terminal while the app is stopped; reminders start disabled.','Respaldo diario automático: 7 copias diarias + 4 semanales. Una copia en este disco no protege contra perderlo. Restaura a una carpeta nueva desde la terminal con la aplicación detenida; los avisos empiezan desactivados.')}</p><div class="actions">${button('backup-create','Back up now','Respaldar ahora')}${button('diagnostics-open','Diagnostics','Diagnóstico')}</div><div id="operations-info" class="hint"></div><div id="backup-list" class="hint"></div></details><details class="daily-section"><summary>${text('Portable continuity','Continuidad portable')}</summary><p class="hint">${text('The profile keeps tasks, calendar, declared context and the current conversation. Each message uses that conversation; New conversation (/clear) starts over without touching tasks. No global conversation histories are imported.','El perfil conserva tareas, agenda, contexto declarado y la conversación actual. Cada mensaje usa esa conversación; Nueva conversación (/clear) empieza de cero sin tocar las tareas. No importa historiales globales.')}</p><div id="continuity-info" class="hint"></div></details>`,submit(text('Save settings','Guardar configuración')));
}
function openSettings(origin){
 const state=flow.snapshot(),d=buildSettings(),p=state.profile,n=state.notification_policy;settingsRevision=state.revision;
 $('setting-name').value=p.name;$('setting-zone').value=p.timezone;$('setting-harness').value=p.harness;
 // Each harness lists its own models: Codex through model/list, Claude Code from its known IDs.
 // Without a catalog only the installed default and the saved choices are offered.
 let catalogs={[p.harness]:state.agent?.catalog||[]};
 const entry=()=>{const harness=$('setting-harness').value,model=$('setting-model').value,list=catalogs[harness]||[];return list.find(m=>m.model===(model||(harness===p.harness?state.agent?.model:null)))||(!model?list.find(m=>m.isDefault):undefined)};
 const efforts=()=>{const harness=$('setting-harness').value;return entry()?.efforts?.length?entry().efforts:harness==='claude'?['low','medium','high','xhigh','max']:['',...(harness===p.harness&&p.effort?[p.effort]:[])]};
 const fillModels=selected=>{const models=(catalogs[$('setting-harness').value]||[]).map(m=>m.model);if(selected&&!models.includes(selected))models.unshift(selected);$('setting-model').replaceChildren(...['',...models].map(value=>{const o=document.createElement('option');o.value=value;o.textContent=value||text('Installed default','Predeterminado instalado');return o}));$('setting-model').value=selected||''};
 const fillEfforts=selected=>{const select=$('setting-effort'),levels=efforts(),fallback=entry()?.defaultEffort;select.replaceChildren(...levels.map(value=>{const o=document.createElement('option');o.value=value;o.textContent=({low:text('Low','Bajo'),medium:text('Medium','Medio'),high:text('High','Alto'),xhigh:text('Very high','Muy alto'),max:text('Maximum','Máximo'),minimal:text('Minimal','Mínimo'),none:text('None','Ninguno')})[value]||value||text('Model default','Predeterminado del modelo');return o}));select.value=levels.includes(selected)?selected:levels.includes('medium')?'medium':levels.includes(fallback)?fallback:levels[0]};
 fillModels(p.model||'');fillEfforts(p.effort||state.agent?.effort||'medium');const initialEffort=$('setting-effort').value;
 $('setting-harness').onchange=()=>{fillModels($('setting-harness').value===p.harness?p.model||'':'');fillEfforts($('setting-effort').value)};$('setting-model').onchange=()=>fillEfforts($('setting-effort').value);
 api('/api/models').then(data=>{if(!d.open)return;for(const harness of ['codex','claude'])if(data[harness]?.length)catalogs[harness]=data[harness];const model=$('setting-model').value,effort=$('setting-effort').value;fillModels(model);fillEfforts(effort)}).catch(()=>{});
 $('policy-enabled').checked=n.enabled;for(const item of d.querySelectorAll('[data-day]'))item.checked=n.weekdays.includes(Number(item.dataset.day));
 for(const [id,key] of [['start','start'],['end','end'],['quiet-start','quietStart'],['quiet-end','quietEnd'],['interval','intervalMinutes'],['repeat','unchangedRepeatMinutes'],['budget','dailyBudget']])$('policy-'+id).value=n[key];const pausedUntil=storedTime('policy-pause',n.pausedUntil);
 const info=state.operations_status||{};$('operations-info').textContent=text('Scheduler: ','Programador: ')+(info.scheduler?.installed?info.scheduler.backend:text('not installed','sin instalar'))+' · '+text('Last check: ','Última revisión: ')+(info.last_tick?ui.full(info.last_tick.at)+' · '+info.last_tick.status:text('none','ninguna'));
 $('continuity-info').textContent=text('Messages in this conversation: ','Mensajes en esta conversación: ')+state.messages.length+(state.conversation?.clearedAt?' · '+text('since ','desde ')+ui.full(state.conversation.clearedAt):'');
 const loadBackups=()=>safely(async()=>{const list=await api('/api/backups');$('backup-list').replaceChildren();for(const b of list.backups.slice(0,8)){const row=document.createElement('p');row.textContent=(b.valid?ui.full(b.captured_at)+' · '+b.category:text('Invalid archive','Archivo inválido'))+' · '+b.path;$('backup-list').append(row)}});
 $('backup-create').onclick=()=>formAction(d.id,async()=>{$('backup-create').disabled=true;try{await deliver('/api/backups',{});await loadBackups();notice(text('Verified backup created.','Respaldo verificado creado.'))}finally{$('backup-create').disabled=false}});
 $('diagnostics-open').onclick=e=>showDiagnostics(e.currentTarget);loadBackups();
 d.querySelector('form').onsubmit=e=>{e.preventDefault();formAction(d.id,async()=>{const harness=$('setting-harness').value,model=$('setting-model').value||null,effort=$('setting-effort').value;
 const policy={...n,enabled:$('policy-enabled').checked,weekdays:[...d.querySelectorAll('[data-day]:checked')].map(x=>Number(x.dataset.day)),start:$('policy-start').value,end:$('policy-end').value,quietStart:$('policy-quiet-start').value,quietEnd:$('policy-quiet-end').value,intervalMinutes:Number($('policy-interval').value),unchangedRepeatMinutes:Number($('policy-repeat').value),dailyBudget:Number($('policy-budget').value),pausedUntil:pausedUntil(),technicalDefault:false};
 const values={name:$('setting-name').value.trim(),timezone:$('setting-zone').value.trim(),notification_policy:policy};if(harness!==p.harness||model!==p.model){values.harness=harness;values.model=harness!==p.harness&&model===p.model?null:model}if(effort!==initialEffort||values.harness)values.effort=effort||null
 await deliver('/api/settings',{values},settingsRevision);d.close('saved');await flow.refresh()})};open(d,origin);
}
$('settings-open').onclick=e=>openSettings(e.currentTarget);
async function showDiagnostics(origin){
 const id='diagnostic-dialog';$(id)?.remove();const data=await api('/api/doctor');const d=dialog(id,text('Operation diagnostics','Diagnóstico de operación'),'<div id="diagnostic-body"></div>',button('diagnostic-close','Close','Cerrar'));
 const body=$('diagnostic-body'),pre=document.createElement('pre');pre.className='operation-result';pre.textContent=JSON.stringify(data,null,2);body.append(pre);
 for(const operation of data.operations){const b=document.createElement('button');b.className='btn';b.type='button';b.textContent=text('Review recovery: ','Revisar recuperación: ')+operation.key;b.onclick=()=>safely(()=>reviewRecovery(operation.key,b));body.append(b)}
 $('diagnostic-close').onclick=()=>d.close('close');open(d,origin);
}
async function reviewRecovery(key,origin){
 const observed=await api('/api/recovery/inspect',{operation_key:key});const id='recovery-dialog';$(id)?.remove();const d=dialog(id,text('Review interrupted operation','Revisar operación interrumpida'),`<p class="warning">${text('Compare observed data with the intended change. A verified backup is made before either resolution. Keep observed preserves partial/external changes; it does not claim the original action completed.','Compara lo observado con el cambio previsto. Se crea un respaldo verificado antes de resolver. Conservar lo observado preserva cambios parciales o externos; no afirma que la acción original terminó.')}</p><pre id="recovery-data" class="operation-result"></pre>`,button('recovery-complete','Complete original action','Completar acción original')+button('recovery-keep','Keep observed data','Conservar datos observados'));
 $('recovery-data').textContent=JSON.stringify(observed,null,2);$('recovery-complete').disabled=!observed.can_complete;
 for(const [id,resolution] of [['recovery-complete','complete'],['recovery-keep','keep_observed']])$(id).onclick=()=>formAction(d.id,async()=>{await api('/api/recovery/resolve',{operation_key:key,resolution,precondition:observed.precondition});d.close('resolved');await flow.refresh()});open(d,origin);
}

const upload=document.createElement('div');upload.className='attachment-list';upload.innerHTML=`<label class="link-button" for="chat-images" data-i18n="attach_images">${text('Attach images','Adjuntar imágenes')}</label><input id="chat-images" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden><div id="chat-image-list"></div>`;$('composer').after(upload);
$('chat-images').onchange=()=>safely(async()=>{const files=[...$('chat-images').files];for(const file of files){const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file)});const result=await deliver('/api/attachments',{name:file.name,mime:file.type,base64:encoded});selectedImages.push(result)}$('chat-images').value='';renderSelectedImages()});
function renderSelectedImages(){$('chat-image-list').replaceChildren();for(const item of selectedImages){const row=document.createElement('div'),b=document.createElement('button');row.textContent=item.name+' ';b.type='button';b.className='link-button';b.textContent=text('Remove','Quitar');b.onclick=()=>{selectedImages=selectedImages.filter(x=>x.id!==item.id);renderSelectedImages()};row.append(b);$('chat-image-list').append(row)}}
const originalSend=$('send-message').onclick;$('send-message').onclick=()=>{if(!selectedImages.length||$('conversation-input').value.trim()==='/clear')return originalSend();return safely(async()=>{const input=$('conversation-input'),value=input.value.trim();if(!value)throw Error(text('Tell the assistant what to capture from these images.','Indica al asistente qué capturar de estas imágenes.'));$('send-message').disabled=true;const ids=selectedImages.map(x=>x.id);try{await deliver('/api/chat',{text:value,attachments:ids,capture_only:true});selectedImages=selectedImages.filter(x=>!ids.includes(x.id));renderSelectedImages();if(input.value.trim()===value)input.value=''}finally{$('send-message').disabled=false}})};
const undoneMessages=new Set();
function receipts(state){for(const el of $('messages').children){
 const message=state.messages.find(m=>m.key===el.dataset.key);if(message?.role!=='assistant')continue;let data;try{data=JSON.parse(message.data||'{}')||{}}catch{continue}
 let box=el.querySelector('.receipt-list');if(!box){box=document.createElement('div');box.className='receipt-list';el.append(box)}box.replaceChildren();
 const ids=data.proposal_ids||[];let saved=0,waiting=0,applied=0;
 for(const id of ids){const p=state.rawProposals?.find(x=>x.id===id)||state.proposals.find(x=>x.id===id);const row=document.createElement('span'),title=p?.intent?.payload?.title||state.tasks.find(t=>t.id===p?.intent?.payload?.uuid)?.title||text('Change','Cambio');
  if(['applied','unchanged'].includes(p?.phase))saved++;if(p?.phase==='applied')applied++;
  // A proposal waiting for the person says it was not saved and links to where it is decided.
  if(['unavailable','review','stale','invalid'].includes(p?.phase)){waiting++;const kind=p.phase==='unavailable'&&p.error_code==='review_insufficient'?'insufficient':p.phase;
   row.textContent=title+' · '+tr('receipt_wait_'+kind+(ui.creates(p.intent.action)?'':'_change'))+' ';const open=document.createElement('button');open.type='button';open.className='link-button';open.textContent=tr('receipt_open');open.onclick=()=>ui.showProposal(id);row.append(open)}
  else row.textContent=title+' · '+(p?tr('phase_'+p.phase):text('See activity for outcome','Ver resultado en actividad'));
  box.append(row)}
 if(waiting){const summary=document.createElement('p');summary.className='receipt-summary';summary.textContent=ui.formatted(waiting===1?'receipt_saved_summary_one':'receipt_saved_summary',{saved,total:ids.length,waiting});box.prepend(summary)}
 // Changes from the conversation applied at once; one Undo reverts all of them, newest first.
 const user=message.key.replace(/:assistant$/,'');if(applied&&!undoneMessages.has(user)&&!state.readOnly){const undo=document.createElement('button');undo.type='button';undo.className='link-button';undo.textContent=tr(applied===1?'undo_message_change':'undo_message_changes');undo.onclick=()=>safely(async()=>{undo.disabled=true;await deliver('/api/undo',{message_key:user});undoneMessages.add(user);notice(tr('changes_undone'));receipts(flow.snapshot())});box.append(undo)}
 for(const [items,label] of [[data.unprocessed,text('Not processed','Sin procesar')],[data.uncertainties,text('Clarification needed','Hace falta aclarar')]])for(const item of items||[]){const row=document.createElement('p');row.textContent=label+': '+item;box.append(row)}
}}
function update(state){
 if(!state.profile)return;
 const digest=JSON.stringify([state.profile,state.notification_policy,state.diagnostics,state.operations_status,state.calendar_events,state.tasks,state.rawProposals,state.messages,state.readOnly,document.documentElement.lang,state.timezone,(state.calendar_events||[]).map(e=>new Date(e.end_at)>=new Date(state.now))]);if(digest===lastDailyDigest)return;lastDailyDigest=digest;
 $('profile-badge').textContent=state.synthetic?text('Synthetic profile','Perfil ficticio'):state.profile.name;$('profile-footer').textContent=text('Local personal assistant','Asistente personal local');$('settings-open').title=text('Daily settings','Configuración diaria');$('settings-open').setAttribute('aria-label',text('Daily settings','Configuración diaria'));$('settings-open').querySelector('span').textContent=text('Settings','Configuración');$('capture-open').textContent=text('Add with details','Agregar con detalles');renderQuick();
 const diag=JSON.stringify([state.diagnostics,state.operations_status?.last_tick?.status]);if(diag!==lastDiagnostics){lastDiagnostics=diag;const issue=state.readOnly||state.diagnostics?.reminders?.length>0||state.operations_status?.last_tick?.status==='failed';diagnostic.hidden=!issue;diagnostic.replaceChildren();if(issue){const p=document.createElement('p');p.textContent=state.readOnly?text('Changes are paused while an interrupted operation or maintenance is reviewed.','Los cambios están pausados mientras se revisa una operación interrumpida o mantenimiento.'):state.diagnostics?.reminders?.length?text('A local reminder failed or has an uncertain outcome. Review delivery diagnostics.','Un recordatorio local falló o tiene un resultado incierto. Revisa el diagnóstico de entrega.'):text('The last scheduled check needs attention.','La última revisión programada requiere atención.');const b=document.createElement('button');b.className='link-button';b.textContent=text('Open diagnostics','Abrir diagnóstico');b.onclick=()=>safely(()=>showDiagnostics(b));diagnostic.append(p,b)}}
 const events=(state.calendar_events||[]).filter(e=>new Date(e.end_at)>=new Date(state.now)),eventDigest=JSON.stringify([events,state.timezone,document.documentElement.lang]);if(lastCalendar!==eventDigest){lastCalendar=eventDigest;calendar.innerHTML=`<h2>${text('Calendar commitments','Compromisos de agenda')}</h2>${events.length?'':`<p class="hint">${text('No upcoming commitments saved.','No hay compromisos próximos guardados.')}</p>`}`;for(const event of events){const row=document.createElement('div');row.className='daily-row';const copy=document.createElement('div'),b=document.createElement('button'),time=document.createElement('p');b.type='button';b.className='link-button';b.textContent=event.title;b.onclick=()=>openCapture(b,event);time.textContent=ui.full(event.start_at)+' — '+ui.full(event.end_at);copy.append(b,time);const cancel=document.createElement('button');cancel.type='button';cancel.className='btn ghost';cancel.textContent=tr('cancel');cancel.onclick=()=>safely(async()=>{await flow.request('event_cancel',{uuid:event.uuid});await flow.refresh()});row.append(copy,cancel);calendar.append(row)}}
 queueMicrotask(()=>{receipts(state);$('capture-open').disabled=state.readOnly;$('qa-input').disabled=$('qa-submit').disabled=state.readOnly;$('send-message').disabled=state.readOnly||state.messages.some(m=>m.role==='user'&&m.status==='pending')});
}
// Localization changes labels only; typed task content is never translated.
$('locale-choice').addEventListener('change',()=>{lastCalendar='';lastDiagnostics='';quickLabels();renderQuick();update(flow.snapshot())});
quickLabels();renderQuick();
globalThis.dayDaily={update};if(flow.snapshot().profile)update(flow.snapshot());
})();
