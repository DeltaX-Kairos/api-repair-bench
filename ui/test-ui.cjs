/* Dependency-free state/event checks; not a browser or visual test. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor() { this.children=[]; this.events={}; this.attributes={}; this.dataset={}; this.checked=false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren() { this.children=[]; }
  setAttribute(name,value) { this.attributes[name]=value; }
  addEventListener(name,fn) { this.events[name]=fn; }
  click() { this.events.click?.(); }
  focus() { this.focused=true; }
}
const ids = new Map();
const get = id => { if (!ids.has(id)) ids.set(id,new Element()); return ids.get(id); };
const tabs = ['change','evidence','context'].map(name=> { const el=new Element();el.dataset.tab=name;return el; });
let exported;
const context = {window:{},document:{getElementById:get,createElement:()=>new Element(),querySelectorAll:selector=>selector==='[role="tab"]'?tabs:get('case-list').children},Blob:class {constructor(parts){exported=JSON.parse(parts[0]);}},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},setTimeout:fn=>fn()};
vm.createContext(context);
for(const file of ['demo-data.js','app.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,file),'utf8'),context);
assert.equal(get('case-list').children.length,3);
assert.equal(get('export').disabled,true);
get('export').click(); assert.equal(exported,undefined);
get('approve').checked=true;get('approve').events.change();get('export').click();
assert.equal(exported.case.id,'pagination');assert.equal(exported.review.deployment_authorized,false);assert.equal(exported.review.live_model_repair,false);
get('case-list').children[1].click();assert.equal(get('approve').checked,false);assert.equal(get('export').disabled,true);assert.match(get('source-code').textContent,/int\(record/);
tabs[1].click();assert.equal(get('panel-evidence').hidden,false);assert.equal(get('panel-change').hidden,true);
tabs[1].events.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(tabs[2].focused,true);assert.equal(get('panel-context').hidden,false);
get('case-list').children[2].click();assert.equal(get('checks').children.length,4);assert.match(get('patch-code').textContent,/api.refresh/);
console.log('PASS: case selection, approval gate and reset, truthful export, tab switching and keyboard navigation. Visual rendering not tested.');

(async()=>{
context.fetch=async()=>({ok:true,json:async()=>({schema_version:1,completed_model_calls:1,stages:{baseline:{remote_completed:0},correction:{remote_completed:0}},paired_comparisons:[],trust_limit:'Not attested.'})});
await get('load-evidence').events.click();
assert.equal(get('provider-metrics').hidden,false);
assert.equal(get('provider-metrics').children[2].children[0].textContent,'Unknown');
assert.match(get('provider-status').textContent,/No remotely tested repairs/);
context.fetch=async()=>{throw new Error('offline');};
await get('load-evidence').events.click();
assert.equal(get('provider-metrics').hidden,true);
assert.match(get('provider-status').textContent,/missing evidence is not a failed test/);
assert.equal(get('load-evidence').disabled,false);
console.log('PASS: saved receipt read, unknown evidence, no-sandbox boundary and unavailable endpoint recovery.');
context.fetch=async(url)=>({ok:true,json:async()=>url==='/api/runs'?{runs:[
{run_id:'r1',case:'pagination',status:'uncertain',model_completed:false,source:null,remote_completed:false},
{run_id:'r2',case:'schema',status:'proposal_rejected',model_completed:true,source:null,finish_reason:'length'},
{run_id:'r3',case:'auth',status:'completed',model_completed:true,source:'<script>never execute</script>',explanation:'A synthetic candidate',remote_completed:false,review_findings:['Uses getattr on a dictionary','Discards replacement token']}
]}:{schema_version:1,completed_model_calls:2,stages:{baseline:{remote_completed:0},correction:{remote_completed:0}},paired_comparisons:[]}});
await get('load-evidence').events.click();
const cards=get('provider-runs').children;
assert.equal(cards.length,3);
assert.equal(cards[0].children[0].children[1].textContent,'Uncertain outcome');
assert.equal(cards[1].children[0].children[1].textContent,'Proposal rejected');
assert.equal(cards[2].children[0].children[1].textContent,'Candidate awaiting verification');
assert.equal(cards[2].children[4].children[1].children[0].textContent,'<script>never execute</script>');
assert.match(get('runs-status').textContent,/cannot approve, execute or deploy/);
assert.equal(cards[2].children[3].children[0].textContent,'Source review: changes required');
assert.match(cards[2].children[3].children[1].textContent,/not runtime-test results/);
assert.equal(cards[2].children[3].children[2].children.length,2);
console.log('PASS: run log labels uncertain and rejected attempts, treats source as inert text, and denies unverified approval.');
})().catch(error=>{console.error(error);process.exitCode=1;});
