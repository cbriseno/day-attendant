// Controlled clock tests for the actual shared UI formatter and in-place clock update.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../day_attendant/ui/interface.js',import.meta.url),'utf8');
const sandbox={Intl,Date,locale:'en',NOW:new Date('2026-10-29T12:00:00Z'),nodes:[]};
sandbox.globalThis=sandbox;sandbox.document={querySelectorAll:()=>sandbox.nodes};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(new URL('../day_attendant/ui/locales.js',import.meta.url),'utf8'),sandbox);
vm.runInContext(`const tr=key=>dayTranslations[locale][key];const formatLocale=()=>locale==='es'?'es-MX':'en-US';const full=value=>new Date(value).toISOString();const esc=value=>String(value);`+source.slice(source.indexOf('const formatters=new Map()'),source.indexOf('const esc='))+source.slice(source.indexOf('function creationAge('),source.indexOf('const short=value=>')),sandbox);
for(const locale of ['en','es']){
 sandbox.locale=locale;
 for(const [seconds,unit,n] of [[59,null,0],[60,'minute',1],[3599,'minute',59],[3600,'hour',1],[86399,'hour',23],[86400,'day',1],[172800,'day',2]]){
  const value=new Date(+sandbox.NOW-seconds*1000).toISOString();
  const expected=unit?new Intl.NumberFormat(locale==='es'?'es-MX':'en-US',{style:'unit',unit,unitDisplay:'short'}).format(n):locale==='es'?'ahora':'now';
  assert.equal(sandbox.creationAge(value),expected);
 }
 assert.match(sandbox.creationTime({createdAt:null}),locale==='es'?/desconocida/:/unknown/);
 assert.match(sandbox.creationTime({createdAt:'invalid'}),locale==='es'?/desconocida/:/unknown/);
 assert.equal(sandbox.creationTime({pending:true,createdAt:null}),'');
 assert.match(sandbox.creationTime({createdAt:'2020-01-01T12:00:00Z'}),/datetime="2020-01-01T12:00:00Z".*aria-label=/);
 assert.equal(sandbox.creationAge('2026-10-30T12:00:00Z'),'2026-10-30T12:00:00.000Z');
 sandbox.nodes=[{dateTime:new Date(+sandbox.NOW-59000).toISOString(),textContent:'old'}];
 sandbox.refreshCreationAges();assert.equal(sandbox.nodes[0].textContent,locale==='es'?'ahora':'now');
 sandbox.NOW=new Date(+sandbox.NOW+1000);sandbox.refreshCreationAges();
 assert.equal(sandbox.nodes[0].textContent,'1 min');
}
assert.match(source,/this.data.now=now;refreshCreationAges\(\)/);
console.log('Creation age: both locales, 59/60s, hour/day, old/missing/future, pending and shared clock passed');
