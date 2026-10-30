// Pure ordering rules from the shipped UI file; no browser, server or model.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const context={};vm.createContext(context);
vm.runInContext(await readFile(path.join(root,'day_attendant/ui/ordering.js'),'utf8'),context);
const {VIEWS,PRIORITIES,manualIds,arrange,move,place,adopt,hue}=context.dayOrdering;
const task=(id,priority,extra={})=>({id,priority,group:null,due:null,createdAt:'2026-10-0'+id.length+'T00:00:00Z',...extra});
// Values cross the vm realm boundary; compare them as plain data.
const plain=value=>JSON.parse(JSON.stringify(value));
const ids=sections=>plain(sections.flatMap(s=>s.tasks.map(t=>t.id)));

assert.deepEqual(plain(VIEWS),['priority','manual','newest','group','suggested']);
const a=task('a','low',{createdAt:'2026-10-01T00:00:00Z',group:'Move'}),b=task('b','high',{createdAt:'2026-10-03T00:00:00Z',due:'2026-10-30T00:00:00Z'}),
 c=task('c','high',{createdAt:'2026-10-02T00:00:00Z',group:'home'}),d=task('d','medium',{createdAt:'2026-10-04T00:00:00Z',group:'Home'}),
 e=task('e','high',{createdAt:'2026-10-05T00:00:00Z',group:'move'});
const all=[a,b,c,d,e];

// Unplaced tasks follow placed ones by priority, then deadline, then creation.
assert.deepEqual(plain(manualIds(all)),['b','c','e','d','a']);
assert.deepEqual(plain(manualIds(all,['a','d','missing'])),['a','d','b','c','e']);

// Priority view: priority first, the manual order inside each priority, one drag section per priority.
const byPriority=arrange(all,'priority',{order:['e','a','c','b','d']});
assert.deepEqual(ids(byPriority),['e','c','b','d','a']);
assert.deepEqual(plain(byPriority.map(s=>[s.key,s.draggable])),[['high',true],['medium',true],['low',true]]);
assert.deepEqual(ids(arrange([a],'priority')),['a']);
// Urgent leads, in its own section; an unknown value is treated as medium.
const u=task('u','urgent',{createdAt:'2026-10-06T00:00:00Z'}),odd=task('odd','critical',{createdAt:'2026-10-07T00:00:00Z'});
assert.deepEqual(plain(PRIORITIES),['urgent','high','medium','low']);
assert.deepEqual(plain(manualIds([...all,u,odd])),['u','b','c','e','d','odd','a']);
const withUrgent=arrange([...all,u,odd],'priority',{order:['e','a','c','b','d','odd','u']});
assert.deepEqual(plain(withUrgent.map(s=>[s.key,s.tasks.map(t=>t.id)])),[['urgent',['u']],['high',['e','c','b']],['medium',['d','odd']],['low',['a']]]);

// My order shows the stored order exactly; Newest uses creation time and accepts no drops.
assert.deepEqual(ids(arrange(all,'manual',{order:['e','a','c','b','d']})),['e','a','c','b','d']);
const newest=arrange(all,'newest',{order:['a','b','c','d','e']});
assert.deepEqual(ids(newest),['e','d','b','c','a']);assert.equal(newest[0].draggable,false);

// Group view clusters labels case-insensitively, sorts names and keeps the ungrouped last.
const grouped=arrange(all,'group',{order:['d','e','c','a','b']});
assert.deepEqual(plain(grouped.map(s=>s.label)),['Home','move',null]);
assert.deepEqual(plain(grouped.map(s=>s.tasks.map(t=>t.id))),[['d','c'],['e','a'],['b']]);
assert.ok(grouped.every(s=>s.draggable));
assert.equal(hue(' Home'),hue('home'));assert.notEqual(hue('home'),hue('move'));

// Suggested view follows the stored suggestion; tasks it omits follow in manual order.
const suggested=arrange(all,'suggested',{order:['a','b','c','d','e'],suggestion:{order:[{id:'d',reason:'x'},{id:'b',reason:'y'}]}});
assert.deepEqual(ids(suggested),['d','b','a','c','e']);assert.equal(suggested[0].draggable,false);
assert.deepEqual(ids(arrange(all,'suggested',{order:['e','d','c','b','a']})),['e','d','c','b','a']);

// A move inside a section keeps every other relative position of the whole order.
const order=['e','a','c','b','d'];
assert.deepEqual(plain(move(order,['e','c','b'],'b',0)),['b','e','a','c','d']);
assert.deepEqual(plain(move(order,['e','c','b'],'e',3)),['a','c','b','e','d']);
assert.deepEqual(plain(move(order,['e','c','b'],'e',2)),['a','c','b','e','d']);
assert.deepEqual(plain(move(order,['e','c','b'],'c',1)),['e','a','c','b','d']);
assert.deepEqual(plain(move(order,['a'],'a',0)),order);
assert.deepEqual(plain(move(order,['e','c','b'],'e',-4)),order);
const moved=move(order,['e','c','b'],'b',1);
assert.deepEqual(ids(arrange(all,'priority',{order:moved})),['e','b','c','d','a']);
assert.deepEqual(plain(moved.filter(id=>id!=='b')),order.filter(id=>id!=='b'));
// A drop goes next to its new neighbours anywhere in the whole order; with none it changes nothing.
assert.deepEqual(plain(place(order,'d','e','a')),['e','d','a','c','b']);
assert.deepEqual(plain(place(order,'e','b',null)),['a','c','b','e','d']);
assert.deepEqual(plain(place(order,'a',null,'e')),['a','e','c','b','d']);
assert.deepEqual(plain(place(order,'c',null,null)),order);
assert.deepEqual(plain(place(order,'missing','e','a')),order);
assert.deepEqual(plain(place(order,'c','gone','also-gone')),order);
// Dropped among other priorities it takes the nearer neighbour's; between them it keeps its own.
assert.equal(adopt('high','low',null),'low');assert.equal(adopt('low',null,'high'),'high');
assert.equal(adopt('medium','high','low'),'medium');assert.equal(adopt('high','high','low'),'high');
assert.equal(adopt('low','high','medium'),'medium');assert.equal(adopt('high','medium','low'),'medium');
assert.equal(adopt('medium',null,null),'medium');
// Urgent is the highest: dropped among urgent tasks a task becomes urgent, and an urgent one let go lower takes that priority.
assert.equal(adopt('high','urgent','urgent'),'urgent');assert.equal(adopt('low',null,'urgent'),'urgent');assert.equal(adopt('urgent','high','medium'),'high');
console.log('Ordering rules passed');
