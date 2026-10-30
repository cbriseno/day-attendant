// Run by test_dependency_ui.py in an isolated browser against disposable SQLite state.
await page.eval(async()=>{
 const rows=daySnapshot().tasks;window.u8ids=rows.map(t=>t.id);
 window.u8={times:[],checks:[],posts:0};const original=window.fetch;window.fetch=(url,opts)=>{if(opts?.method==='POST'&&url==='/api/proposals')u8.posts++;return original(url,opts)};
 window.u8test={
 check:(name,ok)=>{if(!ok)throw Error(name);u8.checks.push(name)},
 task:id=>daySnapshot().tasks.find(t=>t.id===id),
 el:id=>document.querySelector('[data-task-id="'+id+'"]'),
 wait:async fn=>{for(let i=0;i<150;i++){if(await fn())return;await new Promise(r=>setTimeout(r,40))}throw Error('save timeout')},
 saved:async()=>{await u8test.wait(async()=>{const s=await(await fetch('/api/state')).json();return s.data.revision===daySnapshot().revision&&s.data.lastFact?.id===daySnapshot().lastFact?.id&&s.data.tasks.every(t=>JSON.stringify(t.dependsOn)===JSON.stringify(u8test.task(t.id).dependsOn)&&JSON.stringify(t.relatedTo)===JSON.stringify(u8test.task(t.id).relatedTo))})},
 drag:(from,to,knob=false)=>{const target=u8test.el(to),source=knob?document.querySelector('.link-knob'):u8test.el(from).querySelector('.port'),p=source.getBoundingClientRect(),q=target.getBoundingClientRect(),x=p.x+p.width/2,y=p.y+p.height/2,tx=q.x+q.width/2,ty=q.y+q.height/2;
 const options={bubbles:true,pointerType:'mouse',pointerId:31,isPrimary:true,button:0,buttons:1};const start=performance.now();source.dispatchEvent(new PointerEvent('pointerdown',{...options,clientX:x,clientY:y}));document.dispatchEvent(new PointerEvent('pointermove',{...options,clientX:tx,clientY:ty}));document.dispatchEvent(new PointerEvent('pointerup',{...options,buttons:0,clientX:tx,clientY:ty}));u8.times.push({action:knob?'move':'create',ms:performance.now()-start})},
 select:(from,to,type='dep')=>{u8test.el(from).dispatchEvent(new PointerEvent('pointerover',{bubbles:true,pointerType:'mouse'}));const path=[...document.querySelectorAll('[data-link]')].find(x=>x.dataset.link===from+'|'+to+'|'+type);if(!path)throw Error('link missing '+from+' '+to);path.dispatchEvent(new MouseEvent('click',{bubbles:true}));u8test.check('select '+type,!!document.querySelector('.link-line.selected'))},
 click:selector=>{const start=performance.now();document.querySelector(selector).click();u8.times.push({action:selector,ms:performance.now()-start})}
 };return {ready:true}
});

console.log(JSON.stringify(await page.eval(`(async()=>{const [a,b,c,d,e]=u8ids;const {check,task,drag,saved,select,click,wait}=u8test;
 drag(a,b);check('create shown synchronously',task(b).dependsOn.includes(a));await saved();
 drag(a,c);check('preserve existing prerequisite',task(c).dependsOn.includes(a)&&task(c).dependsOn.includes(b));await saved();
 const count=u8.posts;drag(c,a);check('cycle refused before saving',u8.posts===count&&!task(a).dependsOn.length);drag(a,b);check('duplicate refused before saving',u8.posts===count);drag(a,a);check('self link refused before saving',u8.posts===count);
 await new Promise(r=>setTimeout(r,30));select(a,c);click('[data-line="type"]');check('related shown synchronously',task(a).relatedTo.includes(c)&&task(c).relatedTo.includes(a)&&task(c).dependsOn.includes(b)&&!task(c).dependsOn.includes(a));await saved();
 click('#undo');await saved();check('undo type restores both lists',task(c).dependsOn.includes(a)&&!task(a).relatedTo.includes(c));
 select(a,c);click('[data-line="type"]');await saved();
 let [from,to]=[a,c].sort();select(from,to,'rel');
 if(from===c){const count=u8.posts;click('[data-line="type"]');check('type conversion cycle refused before saving',u8.posts===count);}
 drag(from,d,true);check('related move shown synchronously',task(from).relatedTo.includes(d)&&!task(from).relatedTo.includes(to));await saved();
 [from,to]=[from,d].sort();select(from,to,'rel');click('[data-line="type"]');await saved();check('converted back to blocking',task(to).dependsOn.includes(from));
 select(from,to);drag(from,e,true);check('move shown synchronously',task(e).dependsOn.includes(from)&&!task(to).dependsOn.includes(from));await saved();
 click('#undo');await saved();check('undo move restores both tasks',task(to).dependsOn.includes(from)&&!task(e).dependsOn.includes(from));
 select(from,to);drag(from,e,true);await saved();
 select(from,e);click('[data-line="remove"]');check('remove shown synchronously',!task(e).dependsOn.includes(from));await saved();
 click('#undo');await saved();check('undo remove restores relation',task(e).dependsOn.includes(from));
 check('unrequested link remains',task(c).dependsOn.includes(b));
 return u8;
})()`)));
// PHONE
if(await page.eval(()=>document.querySelector('#chat-dialog').open))await page.click('#chat-close');
const [a,b,c,d,e]=await page.eval(()=>u8ids);
const action=(id,name)=>'[data-id="'+id+'"][data-action="'+name+'"]';
await page.click(action(b,'dependencies'));
console.log(JSON.stringify(await page.eval(()=>{u8test.check('phone sheet opens from chip',!!document.querySelector('.row-menu.sheet'));return {sheet:true}})));
await page.click('[data-menu-act="link-go"][data-value="'+a+'"]');
await page.eval(()=>u8test.check('phone go to task',document.activeElement.dataset.id===u8ids[0]));
await page.click(action(b,'dependencies'));
await page.click('[data-menu-act="link-remove"][data-value="dep:'+a+'"]');
await page.eval(async()=>{u8test.check('phone remove shown immediately',!u8test.task(u8ids[1]).dependsOn.includes(u8ids[0]));await u8test.saved()});
await page.click('[data-menu-act="connect-needs"]');
await page.click(action(d,'title'));
await page.eval(async()=>{u8test.check('phone add requirement',u8test.task(u8ids[1]).dependsOn.includes(u8ids[3]));await u8test.saved()});
await page.click(action(b,'more'));await page.click('[data-menu-act="connect"]');
await page.click(action(e,'title'));
await page.eval(async()=>{u8test.check('phone add dependent',u8test.task(u8ids[4]).dependsOn.includes(u8ids[1]));await u8test.saved()});
await page.eval(()=>{const card=u8test.el(u8ids[2]),r=card.querySelector('.row-main').getBoundingClientRect();card.querySelector('.row-main').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',pointerId:32,isPrimary:true,clientX:r.x+10,clientY:r.y+10}));});
await page.wait(500);
await page.eval(()=>document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,pointerType:'touch',pointerId:32,isPrimary:true})));
await page.eval(()=>u8test.check('long press offers connect',!!document.querySelector('[data-menu-act="connect"]')));
await page.click('[data-menu-act="connect"]');await page.click('[data-connect-cancel]');
await page.eval(()=>{for(const locale of ['en','es']){const el=document.querySelector('#locale-choice');el.value=locale;el.dispatchEvent(new Event('change',{bubbles:true}));u8test.check('phone fits '+locale,document.documentElement.scrollWidth<=innerWidth)}});
console.log(JSON.stringify(await page.eval(()=>u8)));
