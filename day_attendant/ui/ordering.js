/* Display order only: no view changes a task's stored priority, group or state. */
(() => {
'use strict';
const VIEWS=['priority','manual','newest','group','suggested'];
// Most pressing first; an unknown value sorts and groups as medium.
const PRIORITIES=['urgent','high','medium','low'],RANK=Object.fromEntries(PRIORITIES.map((p,i)=>[p,i]));
const cmp=(a,b)=>a<b?-1:a>b?1:0;
// Tasks not placed by hand follow placed ones by priority, deadline, creation and UUID.
const fallback=(a,b)=>(RANK[a.priority]??RANK.medium)-(RANK[b.priority]??RANK.medium)||cmp(a.due||'~',b.due||'~')||cmp(a.createdAt||'',b.createdAt||'')||cmp(a.id,b.id);
function manualIds(tasks,order=[]){const index=new Map(order.map((id,i)=>[id,i]));return [...tasks.filter(t=>index.has(t.id)).sort((a,b)=>index.get(a.id)-index.get(b.id)),...tasks.filter(t=>!index.has(t.id)).sort(fallback)].map(t=>t.id)}
const groupKey=t=>(t.group||'').trim().toLocaleLowerCase();
function hue(label){let h=0;for(const c of label.trim().toLocaleLowerCase())h=(h*31+c.codePointAt(0))%360;return h}
// Each section is the scope of a drag: a task moves only among tasks the view treats as equal.
function arrange(tasks,view,{order=[],suggestion=null}={}){
 const position=new Map(manualIds(tasks,order).map((id,i)=>[id,i])),manual=(a,b)=>position.get(a.id)-position.get(b.id);
 if(view==='newest')return [{key:'all',draggable:false,tasks:[...tasks].sort((a,b)=>cmp(b.createdAt||'',a.createdAt||'')||cmp(a.id,b.id))}];
 if(view==='suggested'){const ranks=new Map((suggestion?.order||[]).map((item,i)=>[item.id,i]));return [{key:'all',draggable:false,tasks:[...tasks].sort((a,b)=>(ranks.get(a.id)??Infinity)-(ranks.get(b.id)??Infinity)||manual(a,b))}]}
 if(view==='manual')return [{key:'all',draggable:true,tasks:[...tasks].sort(manual)}];
 if(view==='group'){
  const groups=new Map();for(const t of [...tasks].sort(manual)){const key=groupKey(t);if(!groups.has(key))groups.set(key,{key:'group:'+key,label:(t.group||'').trim()||null,draggable:true,tasks:[]});groups.get(key).tasks.push(t)}
  return [...groups.values()].sort((a,b)=>(a.label===null)-(b.label===null)||(a.label??'').localeCompare(b.label??'',undefined,{sensitivity:'base'}));
 }
 return PRIORITIES.map(p=>({key:p,draggable:true,tasks:tasks.filter(t=>(RANK[t.priority]===undefined?'medium':t.priority)===p).sort(manual)})).filter(s=>s.tasks.length);
}
// Reinsert next to the new neighbour within the whole order, so every other relative position persists.
function move(order,section,id,to){const rest=section.filter(x=>x!==id),index=Math.max(0,Math.min(to,rest.length));if(!rest.length||!order.includes(id)||section.indexOf(id)===index)return order.slice();const result=order.filter(x=>x!==id);result.splice(index<rest.length?result.indexOf(rest[index]):result.indexOf(rest.at(-1))+1,0,id);return result}
// A drop lands between two visible neighbours: the task goes right before the next one, or right
// after the previous one; with neither (an empty column) the whole order stays as it was.
function place(order,id,before,after){if(!order.includes(id)||id===before||id===after)return order.slice();const result=order.filter(x=>x!==id),at=after&&result.includes(after)?result.indexOf(after):before&&result.includes(before)?result.indexOf(before)+1:-1;if(at<0)return order.slice();result.splice(at,0,id);return result}
// Dropped among other priorities, a task takes the priority of where it lands: it keeps its own
// when that fits between its new neighbours, and otherwise takes the nearer neighbour's.
function adopt(own,before,after){const rank=p=>RANK[p]??RANK.medium;if(before&&rank(own)<rank(before))return before;if(after&&rank(own)>rank(after))return after;return own}
globalThis.dayOrdering={VIEWS,PRIORITIES,manualIds,arrange,move,place,adopt,hue};
})();
