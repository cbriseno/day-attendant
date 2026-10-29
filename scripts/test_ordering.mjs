// Pure ordering rules from the shipped UI file; no browser, server or model.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const context={};vm.createContext(context);
vm.runInContext(await readFile(path.join(root,'day_attendant/ui/ordering.js'),'utf8'),context);
const {VIEWS,manualIds,arrange,move,hue}=context.dayOrdering;
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
console.log('Ordering rules passed');
