import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFile} from 'node:fs/promises';
import worker from '../judge-site/dist/server/index.js';
import {makeBundle} from './repair-core.mjs';

const ORIGIN='http://127.0.0.1:18770';
const sql=await readFile(new URL('../judge-site/drizzle/0000_mysterious_sharon_ventura.sql',import.meta.url),'utf8');
class D1 {
 constructor(){this.sql=new DatabaseSync(':memory:');this.sql.exec(sql);}
 prepare(sql){const stmt=this.sql.prepare(sql);let values=[];const wrapper={bind(...args){values=args;return wrapper;},async first(){return stmt.get(...values)??null;},async all(){return{results:stmt.all(...values)};},async run(){const result=stmt.run(...values);return{meta:{changes:Number(result.changes)}};}};return wrapper;}
 close(){this.sql.close();}
}
function fixture(t){const db=new D1();t.after(()=>db.close());return{DB:db,LIVE_ENABLED:'true',NEBIUS_API_KEY:'synthetic-credential-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'};}
async function request(env,path,body,cookie,method=body===undefined?'GET':'POST'){
 const headers={};if(cookie)headers.Cookie=cookie;if(method==='POST'){headers.Origin=ORIGIN;headers['Content-Type']='application/json';}
 const res=await worker.fetch(new Request(ORIGIN+path,{method,headers,...(body===undefined?{}:{body:JSON.stringify(body)})}),env);
 return{status:res.status,data:await res.json(),cookie:res.headers.get('set-cookie')?.split(';')[0]??cookie};
}
async function create(env,cookie){return request(env,'/api/live/runs',{case:'schema'},cookie);}
async function step(env,job,cookie){return request(env,'/api/live/runs/'+job.job_id+'/advance',{phase_token:job.phase_token},cookie);}
function installFetch(t,fn){const original=globalThis.fetch;globalThis.fetch=fn;t.after(()=>globalThis.fetch=original);}
const hash=async b=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',b))].map(x=>x.toString(16).padStart(2,'0')).join('');

// All provider traffic is replaced with synthetic in-memory responses; no remote calls.
test('HTTP blocks cross-origin writes, defaults disabled, and keeps jobs visitor-scoped',async t=>{
 const env=fixture(t);installFetch(t,()=>assert.fail('Unexpected network'));
 const disabled=await request({...env,LIVE_ENABLED:'false'},'/api/live/status');
 assert.equal(disabled.data.enabled,false);
 const foreign=await worker.fetch(new Request(ORIGIN+'/api/live/runs',{method:'POST',headers:{Origin:'https://attacker.invalid'},body:'{}'}),env);
 assert.equal(foreign.status,403);
 const job=await create(env);assert.equal(job.status,201);assert.match(job.cookie,/repair_visitor=/);
 const hidden=await request(env,'/api/live/runs/'+job.data.job_id);assert.equal(hidden.status,404);
 const visible=await request(env,'/api/live/runs/'+job.data.job_id,undefined,job.cookie);
 assert.equal(visible.status,200);assert.equal(Object.hasOwn(visible.data,'owner'),false);assert.equal(Object.hasOwn(visible.data,'challenge'),false);
});

test('SQLite admission atomically limits concurrent active jobs and budget including history',async t=>{
 const env=fixture(t);installFetch(t,()=>assert.fail('Unexpected network'));
 const jobs=await Promise.all([create(env),create(env)]);
 assert.deepEqual(jobs.map(j=>j.status).sort(),[201,429]);
 assert.equal(env.DB.sql.prepare('SELECT COUNT(*) AS n FROM repair_jobs').get().n,1);
 env.DB.sql.prepare("UPDATE repair_jobs SET active=NULL,status='completed',reserved=1790001").run();
 assert.equal((await create(env)).status,429);
 assert.equal((await request(env,'/api/live/status')).data.reason,'allowance_exhausted');
});

test('effectful phase token permits one inference despite simultaneous or replayed advances',async t=>{
 const env=fixture(t);let sends=0;let unblock;
 installFetch(t,async(url,options)=>{sends++;await new Promise(resolve=>unblock=resolve);
  const challenge=JSON.parse(JSON.parse(options.body).messages[1].content);
  return Response.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify({challenge_sha256:challenge.challenge_sha256,decision:'clarify',source:'',explanation:'Need a clearer requirement.'})}}]});});
 const job=await create(env);
 const pending=step(env,job.data,job.cookie);
 while(!unblock)await new Promise(resolve=>setTimeout(resolve,0));
 const concurrent=await step(env,job.data,job.cookie);assert.equal(concurrent.data.status,'running');
 unblock();const done=await pending;assert.equal(done.data.status,'stopped');
 const replay=await step(env,job.data,job.cookie);assert.equal(replay.data.status,'stopped');assert.equal(sends,1);
});

test('lost provider response becomes uncertain and cannot be resent',async t=>{
 const env=fixture(t);let sends=0;installFetch(t,async()=>{sends++;throw Error('synthetic network loss');});
 const job=await create(env),done=await step(env,job.data,job.cookie);
 assert.equal(done.data.status,'uncertain');
 await step(env,job.data,job.cookie);assert.equal(sends,1);
});

test('kill switch stops next phase with no inference',async t=>{
 const env=fixture(t);installFetch(t,()=>assert.fail('Unexpected network'));
 const job=await create(env);env.LIVE_ENABLED='false';
 assert.equal((await step(env,job.data,job.cookie)).data.status,'stopped');
});

test('stale lease is reconciled by read-only status and never resumes old effect',async t=>{
 const env=fixture(t);installFetch(t,()=>assert.fail('Unexpected network'));
 const job=await create(env);
 env.DB.sql.prepare('UPDATE repair_jobs SET lease=? WHERE id=?').run(Date.now()-400000,job.data.job_id);
 await request(env,'/api/live/status',undefined,job.cookie);
 const current=await request(env,'/api/live/runs/'+job.data.job_id,undefined,job.cookie);
 assert.equal(current.data.status,'uncertain');
 await step(env,job.data,job.cookie);
});

async function driveToPoll(t,env,stdoutBuilder){
 let sends=0;let publicJob;const uploaded=new Map();
 installFetch(t,async(url,options)=>{
  if(url.endsWith('/chat/completions')){
   sends++;const req=JSON.parse(JSON.parse(options.body).messages[1].content);
   return Response.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify({challenge_sha256:req.challenge_sha256,decision:'patch',source:req.candidate_source,explanation:'Synthetic baseline'})}}]});
  }
  if(url.endsWith('/files')){
   const data=options.body,uuid='file-'+uploaded.size;uploaded.set(uuid,new TextDecoder().decode(data));
   return Response.json({uuid,sha256:await hash(data),size:data.length});
  }
  if(url.endsWith('/instances'))return Response.json({uuid:'synthetic-operation'});
  if(url.endsWith('/operations/synthetic-operation')){
   const row=env.DB.sql.prepare('SELECT data FROM repair_jobs WHERE id=?').get(publicJob.job_id),data=JSON.parse(row.data);
   const stdout=stdoutBuilder(data.plan);
   return Response.json({uuid:'synthetic-operation',status:'SUCCESS',metadata:{result:{state:{exit_code:0,timed_out:false},stdout:{encoding:'ascii',value:stdout,truncated:false},stderr:{encoding:'ascii',value:'',truncated:false}}}});
  }
  assert.fail('Unexpected URL');
 });
 const created=await create(env);publicJob=created.data;
 let next=await step(env,publicJob,created.cookie);next=await step(env,next.data,created.cookie);next=await step(env,next.data,created.cookie);
 return{...next,cookie:created.cookie,sends:()=>sends};
}

test('wrong-bound sandbox output never becomes verified failure or correction input',async t=>{
 const env=fixture(t);
 const done=await driveToPoll(t,env,plan=>JSON.stringify({run_id:'wrong',challenge_sha256:plan.challenge_sha256,proposal_sha256:plan.proposal_sha256,observations:plan.expected_observations}));
 assert.equal(done.data.status,'uncertain');assert.equal(done.data.receipts[0].sandbox.completed,false);
 assert.notEqual(done.data.receipts[0].status,'repair_failed');assert.equal(done.sends(),1);
});

test('bound complete observations yield result while private oracle stays inaccessible',async t=>{
 const env=fixture(t);
 const done=await driveToPoll(t,env,plan=>JSON.stringify({run_id:plan.run_id,challenge_sha256:plan.challenge_sha256,proposal_sha256:plan.proposal_sha256,observations:plan.expected_observations}));
 assert.equal(done.data.status,'completed');assert.equal(done.data.receipts[0].checks.length,9);
 assert.equal(done.data.receipts[0].sandbox.observations_match,true);assert.equal(done.data.receipts[0].sandbox.trusted_execution_proven,false);
 assert.equal(JSON.stringify(done.data).includes('expected_observations'),false);
});

test('verified failure supplies bounded diagnostics to exactly one correction',async t=>{
 const env=fixture(t);
 const first=await driveToPoll(t,env,plan=>{
  const rows=structuredClone(plan.expected_observations);rows[0].outcome={kind:'error',type:'ValueError',message:'synthetic mismatch'};
  return JSON.stringify({run_id:plan.run_id,challenge_sha256:plan.challenge_sha256,proposal_sha256:plan.proposal_sha256,observations:rows});
 });
 assert.equal(first.data.status,'running');assert.equal(first.data.receipts[0].status,'repair_failed');
 let correctionCalls=0;
 installFetch(t,async(url,options)=>{
  correctionCalls++;const messages=JSON.parse(options.body).messages;
  assert.equal(messages.length,4);assert.match(messages[3].content,/synthetic mismatch/);assert.match(messages[3].content,/major/);
  const challenge=JSON.parse(messages[1].content);
  return Response.json({choices:[{finish_reason:'stop',message:{content:JSON.stringify({challenge_sha256:challenge.challenge_sha256,decision:'clarify',source:'',explanation:'Need additional information.'})}}]});
 });
 const done=await step(env,first.data,first.cookie);
 assert.equal(done.data.status,'stopped');assert.equal(done.data.receipts.length,2);
 assert.equal(done.data.receipts[1].stage,'correction');
 await step(env,first.data,first.cookie);assert.equal(correctionCalls,1);
});

test('expired service stops queued jobs without provider access',async t=>{
 const env=fixture(t);installFetch(t,()=>assert.fail('Unexpected network'));
 const originalNow=Date.now;
 Date.now=()=>Date.parse('2026-12-08T23:59:59Z');t.after(()=>Date.now=originalNow);
 const job=await create(env);Date.now=()=>Date.parse('2026-12-09T00:00:01Z');
 assert.equal((await request(env,'/api/live/status',undefined,job.cookie)).data.enabled,false);
 assert.equal((await step(env,job.data,job.cookie)).data.status,'stopped');
 assert.equal((await create(env)).status,403);
});
