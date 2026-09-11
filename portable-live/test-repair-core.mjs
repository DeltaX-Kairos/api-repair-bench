import test from 'node:test';
import assert from 'node:assert/strict';
import {buildChallenge,prepareRequest,parseProposalResponse,makeBundle,compareResult,parseStrictJSON,digest,canonical,REMOTE_RUNNER} from './repair-core.mjs';
const proposalFor = r=>({challenge_sha256:r.challenge_sha256,decision:'patch',source:r.candidate_source,explanation:'Original synthetic proposal'});
const responseFor=p=>({choices:[{finish_reason:'stop',message:{content:JSON.stringify(p)}}],usage:{prompt_tokens:100,completion_tokens:200}});
const custom = ()=>({source:'def total(items):\n    return 0\n',checks:{description:'Return the sum of the supplied numeric values.',checks:[{id:'nonempty',args:[[1,2]],expected:3},{id:'empty',args:[[]],expected:0}]}});

test('strict JSON rejects duplicate keys, nonfinite, unsafe integer, depth and trailing content',()=>{
  for(const input of ['{"a":1,"a":2}','NaN','1e999','9007199254740993','{} true','['.repeat(34)+'0'+']'.repeat(34)])assert.throws(()=>parseStrictJSON(input));
  assert.equal(parseStrictJSON('{"a":"quote\\\"","n":1.5}').n,1.5);
  assert.equal(parseStrictJSON('{"__proto__":{"safe":true}}').__proto__.safe,true);
});

test('schema prompt binds a versioned complete contract and estimates no billed charges',async()=>{
  const req=await buildChallenge({case:'schema'}), prepared=await prepareRequest(req);
  assert.match(req.contract,/Python dicts/);
  assert.equal(prepared.payload.model,'nvidia/Nemotron-3_5-Lightning');
  assert.equal(prepared.payload.max_completion_tokens,16384);
  assert.equal(prepared.automatic_retries,0);
  await assert.rejects(()=>prepareRequest({...req,contract:'altered'}));
});

test('pagination challenge binds empty pages, duplicate items and cursor safety',async()=>{
  const req=await buildChallenge({case:'pagination'}), prepared=await prepareRequest(req);
  assert.equal(req.case,'pagination');
  assert.equal(req.function_name,'pagination_bug');
  assert.match(req.contract,/empty pages/);
  assert.equal(prepared.payload.model,'nvidia/Nemotron-3_5-Lightning');
  const bundle=await makeBundle({challenge_sha256:req.challenge_sha256,decision:'patch',source:req.candidate_source,explanation:'Synthetic pagination baseline.'},req,'pagination-run');
  assert.equal(bundle.private_comparison_plan.expected_observations.length,4);
  assert.deepEqual(bundle.private_comparison_plan.expected_observations.map(row=>row.id),['all-pages','empty-page','repeated-cursor','request-budget']);
  assert.match(bundle.upload_files['runner.py'],/FixtureAPI/);
});

test('custom checks reject ambiguous fields and bind immutable examples',async()=>{
  const input=custom(),req=await buildChallenge(input);
  assert.equal(req.function_name,'total');
  input.checks.checks[0].expected=100;
  assert.equal(req.user_checks[0].expected,3);
  await assert.rejects(()=>buildChallenge({...custom(),source:'import os\ndef f(): return 1'}));
  const invalid=custom();invalid.checks.checks[0].error='ValueError';
  await assert.rejects(()=>buildChallenge(invalid));
  const duplicate=custom();duplicate.checks.checks[1].id='nonempty';
  await assert.rejects(()=>buildChallenge(duplicate));
  const tampered={...req,function_name:'other'};delete tampered.challenge_sha256;tampered.challenge_sha256=await digest(tampered);
  await assert.rejects(()=>prepareRequest(tampered));
});

test('proposal parser rejects truncation, wrong binding and duplicate fields',async()=>{
  const req=await buildChallenge({case:'schema'}),proposal=proposalFor(req);
  const valid=await parseProposalResponse(responseFor(proposal),req);
  assert.equal(valid.status,'awaiting_remote_sandbox');
  assert.equal(valid.structural_validation,'python_ast_pending_remote');
  assert.equal(valid.cost_is_provider_billed_amount,false);
  const truncated=responseFor(proposal);truncated.choices[0].finish_reason='length';
  await assert.rejects(()=>parseProposalResponse(truncated,req));
  await assert.rejects(()=>parseProposalResponse(responseFor({...proposal,challenge_sha256:'wrong'}),req));
  const dup=responseFor(proposal);dup.choices[0].message.content='{"decision":"patch","decision":"clarify"}';
  await assert.rejects(()=>parseProposalResponse(dup,req));
});

test('bundle separates private expected values and checks Python AST remotely before compilation',async()=>{
  const req=await buildChallenge(custom()),bundle=await makeBundle(proposalFor(req),req,'a'.repeat(32));
  assert.deepEqual(Object.keys(bundle.upload_files).sort(),['candidate.py','job.json','runner.py']);
  assert.deepEqual(bundle.command,['python3','-I','runner.py']);
  const job=JSON.parse(bundle.upload_files['job.json']);
  assert.equal(Object.hasOwn(job.fixtures[0],'expected'),false);
  assert.equal(bundle.private_comparison_plan.expected_observations[0].outcome.value,3);
  assert.ok(REMOTE_RUNNER.indexOf('ast.dump(candidate.args)') < REMOTE_RUNNER.indexOf('exec(compile('));
  assert.equal(bundle.execution_performed,false);
  assert.equal(Object.keys(bundle.private_comparison_plan.file_sha256).length,3);
});

test('comparison produces nine observations without claiming trusted execution',async()=>{
  const req=await buildChallenge({case:'schema'}),bundle=await makeBundle(proposalFor(req),req,'b'.repeat(32));
  const plan=bundle.private_comparison_plan;
  const output={run_id:plan.run_id,challenge_sha256:plan.challenge_sha256,proposal_sha256:plan.proposal_sha256,observations:structuredClone(plan.expected_observations)};
  output.observations.find(r=>r.id==='precision').outcome.message='Arbitrary valid diagnostic';
  const result=await compareResult(plan,JSON.stringify(output),{exit_code:0});
  assert.equal(result.binding_valid,true);assert.equal(result.output_valid,true);
  assert.equal(result.observations_match,true);assert.equal(result.checks.length,9);assert.equal(result.trusted_execution_proven,false);
  output.run_id='wrong';
  const unbound=await compareResult(plan,JSON.stringify(output),{exit_code:0});
  assert.equal(unbound.binding_valid,false);assert.equal(unbound.output_valid,false);
  assert.equal(unbound.checks.filter(c=>c.passed).length,0);
  output.run_id=plan.run_id;output.observations=[];
  assert.equal((await compareResult(plan,JSON.stringify(output),{exit_code:0})).output_valid,false);
});

test('comparison distinguishes booleans from integers and bounds diagnostics',async()=>{
  const req=await buildChallenge(custom()),plan=(await makeBundle(proposalFor(req),req,'c'.repeat(32))).private_comparison_plan;
  const output={run_id:plan.run_id,challenge_sha256:plan.challenge_sha256,proposal_sha256:plan.proposal_sha256,observations:structuredClone(plan.expected_observations)};
  output.observations[0].outcome={kind:'error',type:'ValueError',message:'x'.repeat(500)};
  const result=await compareResult(plan,JSON.stringify(output),{exit_code:0});
  assert.deepEqual(result.mismatches,['nonempty']);assert.equal(result.diagnostics[0].message.length,160);
  output.observations[1].outcome.value=false;
  assert.equal((await compareResult(plan,JSON.stringify(output),{exit_code:0})).checks[1].passed,false);
  assert.equal((await compareResult(plan,'{}',{exit_code:true})).observations_match,false);
  assert.equal((await compareResult(plan,'{}',{exit_code:0,timed_out:true})).observations_match,false);
});

test('one correction requires verified baseline failure for identical challenge',async()=>{
  const req=await buildChallenge(custom());
  const parent={challenge_sha256:req.challenge_sha256,stage:'baseline',proposal:proposalFor(req),sandbox:{completed:true,observations_match:false},sandbox_result:{reason:'Mismatch',mismatches:['nonempty']}};
  assert.equal((await prepareRequest(req,parent)).payload.messages.length,4);
  await assert.rejects(()=>prepareRequest(req,{...parent,stage:'correction'}));
  await assert.rejects(()=>prepareRequest(req,{...parent,sandbox:{completed:false,observations_match:null}}));
});

test('canonical values are key-order stable and preserve JSON value types',()=>{
  assert.equal(canonical({b:2,a:1}),canonical({a:1,b:2}));
  assert.notEqual(canonical(true),canonical(1));
});
