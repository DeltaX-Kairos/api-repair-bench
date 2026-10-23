import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {REMOTE_RUNNER,buildChallenge,makeBundle,compareResult,prepareRequest} from './repair-core.mjs';
import {HISTORICAL_RESERVED,JOB_RESERVATION,TOTAL_LIMIT,VISITOR_WINDOW_MS,DEADLINE,LIVE_UNTIL,configurationReason,STATS_SQL,statsBindings,admissionState,ADMISSION_SQL,admissionBindings} from './live-policy.mjs';
// Test the actual worker source without rebuilding or loading public assets.
const workerSource=readFileSync(new URL('./worker.mjs',import.meta.url),'utf8').replace("import assets from './assets.mjs';",'const assets={};').replaceAll(/from '(\.\/[^']+)'/g,(_,path)=>`from '${new URL('./'+path.slice(2),import.meta.url).href}'`);
const {default:worker}=await import('data:text/javascript;base64,'+Buffer.from(workerSource).toString('base64'));
const configured={LIVE_ENABLED:'true',NEBIUS_API_KEY:'synthetic-key',NEBIUS_PROJECT_ID:'aiproject-synthetic'};
const fixedNow=Date.parse('2026-10-23T19:00:00Z');
function ledger(){
 const db=new DatabaseSync(':memory:');
 db.exec(readFileSync(new URL('./schema.sql',import.meta.url),'utf8'));
 return db;
}
function seed(db,{id='old',owner='visitor',created=fixedNow-1000,reserved=JOB_RESERVATION,active=null,status='completed'}={}){
 db.prepare('INSERT INTO repair_jobs (id,owner,status,phase,token,data,reserved,active,lease,created) VALUES (?,?,?,?,?,?,?,?,?,?)').run(id,owner,status,'done','old-token','{}',reserved,active,0,created);
}
function state(db,owner='visitor',at=fixedNow){return admissionState(db.prepare(STATS_SQL).get(...statsBindings(owner,at)),configured,at);}
function admit(db,{id='new',owner='visitor',at=fixedNow,token='bound-token',data={synthetic:true}}={}){
 return db.prepare(ADMISSION_SQL).run(...admissionBindings({id,owner,token,data,at}));
}
function dbAdapter(db){return {prepare(sql){let args=[];return {bind(...values){args=values;return this;},async all(){return {results:db.prepare(sql).all(...args)};},async first(){return db.prepare(sql).get(...args);},async run(){return {meta:db.prepare(sql).run(...args)};}};}};}

test('the deployed Python runner parses before any provider call',()=>{
  // Parse the trusted runner only. No candidate source is executed locally.
  const result=spawnSync('python3',['-c','import ast,sys; ast.parse(sys.stdin.read())'],{input:REMOTE_RUNNER,encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
});

test('live admission requires a real project identifier rather than a placeholder',async(t)=>{
  t.mock.method(Date,'now',()=>fixedNow);
  const DB={prepare(){return {bind(){return this;},async all(){return {results:[]};},async first(){return {reserved:0,jobs:0,visitor:0,active:0};}};}};
  const base={DB,LIVE_ENABLED:'true',NEBIUS_API_KEY:'synthetic-key-not-a-real-credential'};
  for(const project of [undefined,'your-project-id','your_project_id','']){
    const response=await worker.fetch(new Request('http://127.0.0.1:18770/api/live/status'),{...base,NEBIUS_PROJECT_ID:project});
    assert.equal((await response.json()).enabled,false);
  }
  const response=await worker.fetch(new Request('http://127.0.0.1:18770/api/live/status'),{...base,NEBIUS_PROJECT_ID:'aiproject-synthetic'});
  assert.equal((await response.json()).enabled,true);
});

test('bound candidate syntax failures are failed checks eligible for one correction',async()=>{
  const challenge=await buildChallenge({case:'schema'});
  const proposal={challenge_sha256:challenge.challenge_sha256,decision:'patch',source:'def schema_bug(record, contract):\\n    return {}',explanation:'A malformed synthetic proposal'};
  const bundle=await makeBundle(proposal,challenge,'synthetic-syntax-failure');
  const plan=bundle.private_comparison_plan;
  const transcript={run_id:plan.run_id,challenge_sha256:plan.challenge_sha256,proposal_sha256:plan.proposal_sha256,
    observations:plan.expected_observations.map(row=>({id:row.id,calls:[],outcome:{kind:'error',type:'SyntaxError',message:'unexpected character after line continuation character'}}))};
  const result=await compareResult(plan,JSON.stringify(transcript),{exit_code:0});
  assert.equal(result.binding_valid,true);
  assert.equal(result.output_valid,true);
  assert.equal(result.checks.length,9);
  assert.equal(result.checks.filter(row=>row.passed).length,0);
  const next=await prepareRequest(challenge,{stage:'baseline',challenge_sha256:challenge.challenge_sha256,
    sandbox:{completed:true,observations_match:false},proposal,sandbox_result:result});
  assert.match(next.payload.messages.at(-1).content,/SyntaxError/);
  assert.equal(next.payload.chat_template_kwargs.enable_thinking,true);
  assert.equal(next.payload.response_format.type,'json_schema');
  assert.equal(next.payload.response_format.json_schema.strict,true);
  assert.deepEqual(next.payload.response_format.json_schema.schema.properties.challenge_sha256.enum,[challenge.challenge_sha256]);
});

test('ordinary visitor limits cannot be expanded by leftover recording settings',async(t)=>{
  t.mock.method(Date,'now',()=>fixedNow);
  const cookie='a'.repeat(64);
  const owner=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(cookie)))].map(v=>v.toString(16).padStart(2,'0')).join('');
  const DB={prepare(){return {bind(){return this;},async all(){return {results:[]};},async first(){return {reserved:40000,jobs:2,visitor:2,active:0};}};}};
  const env={DB,LIVE_ENABLED:'true',NEBIUS_API_KEY:'synthetic-key',NEBIUS_PROJECT_ID:'aiproject-synthetic',LIVE_VERIFICATION_VISITOR:owner,LIVE_VERIFICATION_LIMIT:'7',LIVE_VERIFICATION_UNTIL:new Date(Date.now()+1200000).toISOString()};
  const result=await (await worker.fetch(new Request('http://127.0.0.1:18770/api/live/status',{headers:{Cookie:'repair_visitor='+cookie}}),env)).json();
  assert.equal(result.enabled,false);
  assert.equal(result.reason,'session_limit');
  assert.equal(result.jobs_per_visitor,2);
  assert.equal(result.credit_budget_usd,24.96);
});

test('rolling visitor window expires from immutable creation times without refunding reservations',()=>{
 const db=ledger();
 seed(db,{id:'expired',created:fixedNow-VISITOR_WINDOW_MS});
 seed(db,{id:'inside',created:fixedNow-VISITOR_WINDOW_MS+1});
 seed(db,{id:'recent',created:fixedNow-1000});
 const blocked=state(db);
 assert.equal(blocked.reason,'session_limit');
 assert.equal(blocked.visitor_runs_used,2);
 assert.equal(blocked.visitor_runs_remaining,0);
 assert.equal(blocked.visitor_reset_at,new Date(fixedNow+1).toISOString());
 assert.equal(blocked.remaining_run_reservations,1235);
 assert.equal(admit(db).changes,0);
 const after=state(db,'visitor',fixedNow+1);
 assert.equal(after.enabled,true);
 assert.equal(after.visitor_runs_used,1);
 assert.equal(admit(db,{at:fixedNow+1}).changes,1);
 assert.equal(db.prepare('SELECT COUNT(*) AS total, SUM(reserved) AS reserved FROM repair_jobs').get().total,4);
 assert.equal(state(db,'other').remaining_run_reservations,1234);
 db.close();
});

test('all visitor windows share the authorized hard reservation cap and single active job',()=>{
 assert.equal(HISTORICAL_RESERVED,190000);
 assert.equal(JOB_RESERVATION,20000);
 assert.equal(TOTAL_LIMIT,24_960_000);
 const db=ledger();
 assert.equal(state(db).remaining_run_reservations,1238);
 seed(db,{reserved:TOTAL_LIMIT-HISTORICAL_RESERVED-JOB_RESERVATION,created:fixedNow-3*VISITOR_WINDOW_MS});
 assert.equal(state(db).remaining_run_reservations,1);
 assert.equal(admit(db).changes,1);
 assert.equal(admit(db,{id:'parallel',owner:'other'}).changes,0);
 assert.equal(state(db,'other').reason,'busy');
 db.prepare('UPDATE repair_jobs SET active=NULL WHERE id=?').run('new');
 assert.equal(state(db,'other').reason,'allowance_exhausted');
 assert.equal(admit(db,{id:'over-cap',owner:'other'}).changes,0);
 assert.equal(db.prepare('SELECT SUM(reserved) AS reserved FROM repair_jobs').get().reserved+HISTORICAL_RESERVED,TOTAL_LIMIT);
 db.close();
});

test('judge window includes December 15 and all January 1 Pacific time, then fails closed',()=>{
 assert.equal(LIVE_UNTIL,'2027-01-02T08:00:00.000Z');
 const db=ledger();
 for(const at of [Date.parse('2026-12-15T20:00:00Z'),Date.parse('2027-01-01T08:00:00Z'),DEADLINE-1]){
  assert.equal(configurationReason(configured,at),null);
  assert.equal(state(db,'visitor',at).enabled,true);
 }
 assert.equal(state(db,'visitor',DEADLINE).reason,'judging_window_ended');
 assert.equal(admit(db,{at:DEADLINE}).changes,0);
 assert.equal(admit(db,{at:DEADLINE-1}).changes,1);
 const row=db.prepare('SELECT * FROM repair_jobs WHERE id=?').get('new');
 assert.equal(row.created,DEADLINE-1);
 assert.equal(row.owner,'visitor');
 assert.equal(row.token,'bound-token');
 assert.equal(row.reserved,JOB_RESERVATION);
 assert.deepEqual(JSON.parse(row.data),{synthetic:true});
 db.close();
});

test('GET and atomic POST report the same rolling cap and safe metadata without provider calls',async(t)=>{
 t.mock.method(Date,'now',()=>fixedNow);
 t.mock.method(globalThis,'fetch',()=>{throw new Error('Unexpected provider call');});
 const cookie='b'.repeat(64);
 const owner=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(cookie)))].map(v=>v.toString(16).padStart(2,'0')).join('');
 const db=ledger();
 seed(db,{id:'first-private-id',owner,created:fixedNow-3000});
 seed(db,{id:'second-private-id',owner,created:fixedNow-2000});
 const env={...configured,DB:dbAdapter(db)};
 const headers={Cookie:'repair_visitor='+cookie,Origin:'http://127.0.0.1:18770','Content-Type':'application/json'};
 const get=await (await worker.fetch(new Request('http://127.0.0.1:18770/api/live/status',{headers}),env)).json();
 const response=await worker.fetch(new Request('http://127.0.0.1:18770/api/live/runs',{method:'POST',headers,body:JSON.stringify({case:'schema'})}),env);
 const post=await response.json();
 assert.equal(response.status,429);
 assert.equal(post.error,'session_limit');
 for(const key of ['reason','visitor_runs_used','visitor_runs_remaining','visitor_reset_at','remaining_run_reservations','live_until'])assert.equal(post[key],get[key]);
 assert.equal(post.visitor_runs_remaining,0);
 assert.equal(post.remaining_run_reservations,1236);
 assert.equal(JSON.stringify(post).includes(owner),false);
 assert.equal(JSON.stringify(post).includes('private-id'),false);
 assert.equal(db.prepare('SELECT COUNT(*) AS total FROM repair_jobs').get().total,2);
 db.close();
});

test('hosting settings cannot expand the owner-authorized reservation ceiling',()=>{
 const db=ledger();
 for(let i=0;i<26;i++)seed(db,{id:'historical-'+i,owner:'past-visitor',created:fixedNow-3*VISITOR_WINDOW_MS});
 const stats=db.prepare(STATS_SQL).get(...statsBindings('visitor',fixedNow));
 const env={...configured,TOTAL_LIMIT:'999999999',LIVE_TOTAL_LIMIT:'999999999',LIVE_BUDGET_USD:'999',LIVE_CREDIT_BUDGET_USD:'999'};
 const result=admissionState(stats,env,fixedNow);
 assert.equal(result.credit_budget_usd,24.96);
 assert.equal(result.remaining_budget_usd,24.25);
 assert.equal(result.remaining_run_reservations,1212);
 db.prepare('UPDATE repair_jobs SET reserved=0').run();
 db.prepare('UPDATE repair_jobs SET reserved=? WHERE id=?').run(TOTAL_LIMIT-HISTORICAL_RESERVED,'historical-0');
 const exhausted=admissionState(db.prepare(STATS_SQL).get(...statsBindings('visitor',fixedNow)),env,fixedNow);
 assert.equal(exhausted.reason,'allowance_exhausted');
 assert.equal(exhausted.remaining_run_reservations,0);
 assert.equal(admit(db,{env}).changes,0);
 assert.equal(db.prepare('SELECT SUM(reserved) AS reserved FROM repair_jobs').get().reserved+HISTORICAL_RESERVED,24_960_000);
 db.close();
});
