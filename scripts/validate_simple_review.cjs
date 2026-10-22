// Dependency-free DOM contract checks. No browser, sockets or real human labels.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const payload = JSON.parse(html.match(/<script id="review-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
assert.equal(payload.origin, 'synthetic', 'Only a synthetic-mode page may be filled by this test');
const code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const elements = new Map(), all = [], downloads = [], storage = new Map();
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.attributes={}; this.dataset={}; this.style={}; this.value=''; this.checked=false; this.hidden=false; this.classList={toggle(){}}; all.push(this); }
  setAttribute(key,value){this.attributes[key]=value;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  click(){if(this.download)downloads.push(this.download); if(this.onclick)this.onclick();}
}
const document = {
  getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},
  createElement(tag){return new Element(tag);},
  querySelectorAll(){return all.filter(el=>el.dataset.quote);},
};
document.getElementById('review-data').textContent=JSON.stringify(payload);
const context=vm.createContext({document,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
  Blob,URL:{createObjectURL:()=> 'blob:synthetic-test',revokeObjectURL(){}},setTimeout:fn=>fn(),console});
const run=source=>vm.runInContext(source,context);
run(code);
assert.equal(run('Object.keys(entry().answers).length'),0);
assert.equal(run('entry().confirmed'),false);
document.getElementById('save').click();
assert.match(document.getElementById('message').textContent,/还有没选/);
// Explicit test-only choices; no prediction is used to produce these values.
run(`for(const field of Object.keys(labels)){const value=field==='type'?'unknown':'unknown'; entry().answers[field]=value; renderChoices(field);} `);
document.getElementById('save').click();
assert.match(document.getElementById('message').textContent,/本人看过/);
document.getElementById('confirm').checked=true;
document.getElementById('save').click();
assert.equal(run('state.index'),1);
assert.equal(run('Object.values(state.records).filter(v=>v.confirmed).length'),1);
assert.ok(storage.size);
run('go(0)');
assert.equal(document.getElementById('confirm').checked,true);
document.getElementById('export').click();
assert.match(downloads[0],/^测试标注-/);
run("toggleQuote(DATA.records[0].title)");
assert.equal(run('entry().confirmed'),false,'Changing evidence must require confirmation again');
assert.equal(document.getElementById('confirm').checked,false);
assert.ok(run('entry().quotes.length')>0);
run("entry().answers.featured='yes'; entry().criteria=[]");
assert.match(run('check()'),/主要原因/);
run("entry().criteria=[Object.keys(DATA.criteria)[0]]; entry().answers.type='notice'");
document.getElementById('confirm').checked=true;
assert.equal(run('check()'),'');
run('entry().confirmed=true; persist()');
// Reloading uses the actual persisted answer schema, not a separate fixture.
const saved = JSON.parse([...storage.values()][0]);
assert.equal(saved.records[payload.records[0].sample_id].confirmed,true);
assert.equal(run('Object.keys(validRecords(Object.values(state.records))).length'),2);
run('go(1)');
assert.equal(run('Object.keys(entry().answers).length'),0,'Next article must start without answers');
fs.writeFileSync(process.argv[3],run(`JSON.stringify({version:DATA.version,benchmark_id:DATA.benchmark_id,dataset_id:DATA.dataset_id,origin:DATA.origin,records:Object.values(state.records)},null,2)`));
console.log('DOM contract: blank state, confirmation, unknowns, quotes, draft reset, next article, local storage and JSON download passed (synthetic only).');
