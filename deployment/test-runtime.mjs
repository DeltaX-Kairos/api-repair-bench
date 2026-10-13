import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {REMOTE_RUNNER,buildChallenge,makeBundle,compareResult,prepareRequest} from './repair-core.mjs';
import worker from './test-worker-loader.mjs';

test('the deployed Python runner parses before any provider call',()=>{
  // Parse the trusted runner only. No candidate source is executed locally.
  const result=spawnSync('python3',['-c','import ast,sys; ast.parse(sys.stdin.read())'],{input:REMOTE_RUNNER,encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
});

test('live admission requires a real project identifier rather than a placeholder',async()=>{
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

test('the bounded verification allowance applies only to its selected visitor',async()=>{
  const cookie='a'.repeat(64);
  const owner=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(cookie)))].map(v=>v.toString(16).padStart(2,'0')).join('');
  const DB={prepare(){return {bind(){return this;},async all(){return {results:[]};},async first(){return {reserved:40000,jobs:2,visitor:2,active:0};}};}};
  const env={DB,LIVE_ENABLED:'true',NEBIUS_API_KEY:'synthetic-key',NEBIUS_PROJECT_ID:'aiproject-synthetic'};
  const read=async extra=>(await (await worker.fetch(new Request('http://127.0.0.1:18770/api/live/status',{headers:{Cookie:'repair_visitor='+cookie}}),{...env,...extra})).json());
  assert.equal((await read({})).enabled,false);
  assert.equal((await read({LIVE_VERIFICATION_VISITOR:'unrelated-visitor'})).enabled,false);
  const permitted=await read({LIVE_VERIFICATION_VISITOR:owner});
  assert.equal(permitted.enabled,true);
  assert.equal(permitted.jobs_per_visitor,6);
  assert.equal(permitted.credit_budget_usd,2);
});
