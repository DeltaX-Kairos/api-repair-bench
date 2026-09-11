import assets from './assets.mjs';
import {buildChallenge,prepareRequest,parseProposalResponse,makeBundle,compareResult,parseStrictJSON} from './repair-core.mjs';
import {startSandbox,pollSandbox} from './sandbox-rest.mjs';
const ORIGINS=new Set(['https://repairbench.deltaxevaluate.com','https://api-repair-bench.deltax-evaluate.chatgpt.site','http://127.0.0.1:18770']);
const TERMINAL=new Set(['completed','stopped','uncertain','failed']);
const HISTORICAL_RESERVED=190000, JOB_RESERVATION=20000, TOTAL_LIMIT=2000000;
const DEADLINE=Date.parse('2026-12-09T00:00:00Z');
const now=()=>Date.now(), uuid=()=>crypto.randomUUID().replaceAll('-','');
const textBytes=s=>new TextEncoder().encode(s).length;
async function sha(s){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('');}
function trace(data,stage,message){data.trace.push({stage,message,at:now()/1000});}
function enabled(env){return env.LIVE_ENABLED==='true'&&!!env.NEBIUS_API_KEY&&now()<DEADLINE;}
function response(data,status=200,cookie){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...(cookie?{'Set-Cookie':cookie}:{})}});}
function publicJob(row){const d=JSON.parse(row.data);return {job_id:row.id,case:d.challenge.case,status:row.status,trace:d.trace,receipts:d.receipts,created_at:row.created/1000,needs_advance:!TERMINAL.has(row.status)&&!row.lease,phase_token:row.token};}
async function save(db,row,d,phase,status='running'){
 const token=uuid();
 await db.prepare('UPDATE repair_jobs SET data=?,phase=?,status=?,token=?,lease=0,active=? WHERE id=? AND token=?').bind(JSON.stringify(d),phase,status,token,TERMINAL.has(status)?null:1,row.id,row.token).run();
 return db.prepare('SELECT * FROM repair_jobs WHERE id=?').bind(row.id).first();
}
async function stopStale(db){
 // A lost write response never permits repeating an effectful stage.
 const stale=await db.prepare("SELECT * FROM repair_jobs WHERE active=1 AND ((lease>0 AND lease<?) OR (lease=0 AND created<?))").bind(now()-360000,now()-1800000).all();
 for(const row of stale.results){const d=JSON.parse(row.data);trace(d,'uncertain','The session stopped before its outcome was confirmed. No request will be repeated.');await save(db,row,d,'done','uncertain');}
}
async function info(db,owner,env){
 const stats=await db.prepare('SELECT COALESCE(SUM(reserved),0) AS reserved,COUNT(*) AS jobs,COALESCE(SUM(CASE WHEN owner=? THEN 1 ELSE 0 END),0) AS visitor,COALESCE(MAX(active),0) AS active FROM repair_jobs').bind(owner).first();
 const reason=!enabled(env)?'operator_disabled':stats.active?'busy':stats.visitor>=2?'session_limit':stats.reserved+HISTORICAL_RESERVED+JOB_RESERVATION>TOTAL_LIMIT?'allowance_exhausted':null;
 return {enabled:!reason,replay_only:!!reason,reason,jobs_per_visitor:2,execution_provider:'Nebius Sandbox',model_provider:'Nebius Token Factory',model:'NVIDIA Nemotron',credit_budget_usd:2};
}
async function readJson(request){
 if(!/^application\/json(?:;|$)/i.test(request.headers.get('content-type')||''))throw new Error('invalid_input');
 if(Number(request.headers.get('content-length')||0)>24576||!request.body)throw new Error('invalid_input');
 const reader=request.body.getReader();let size=0,parts=[];
 while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>24576){await reader.cancel();throw new Error('invalid_input');}parts.push(value);}
 const body=new Uint8Array(size);let at=0;for(const part of parts){body.set(part,at);at+=part.length;}
 return parseStrictJSON(new TextDecoder().decode(body),24576);
}
async function boundedJson(res){if(!res.ok)throw new Error('provider_http_'+res.status);const reader=res.body.getReader();let size=0;const chunks=[];while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>262144){await reader.cancel();throw new Error('response_limit');}chunks.push(value);}const all=new Uint8Array(size);let p=0;for(const chunk of chunks){all.set(chunk,p);p+=chunk.length;}return parseStrictJSON(new TextDecoder().decode(all),262144);}
async function advance(row,env){
 const db=env.DB,d=JSON.parse(row.data);
 if(!enabled(env)){trace(d,'stop','The operator has paused live execution.');return save(db,row,d,'done','stopped');}
   const settings={apiKey:env.NEBIUS_API_KEY,projectId:env.NEBIUS_PROJECT_ID || 'your-project-id'};
 try{
  if(row.phase==='propose'||row.phase==='correct'){
   const correcting=row.phase==='correct';
   trace(d,correcting?'correct':'propose',correcting?'Nemotron is preparing one correction from the failed checks.':'Nemotron is writing a proposed repair.');
   await db.prepare('UPDATE repair_jobs SET data=? WHERE id=? AND token=?').bind(JSON.stringify(d),row.id,row.token).run();
   const parent=correcting?d.receipts.at(-1):undefined;
   const prepared=await prepareRequest(d.challenge,parent);
   const result=await boundedJson(await fetch(prepared.endpoint,{method:'POST',headers:{Authorization:'Bearer '+env.NEBIUS_API_KEY,'Content-Type':'application/json'},body:JSON.stringify(prepared.payload),redirect:'manual',signal:AbortSignal.timeout(300000)}));
   if(JSON.stringify(result).includes(env.NEBIUS_API_KEY))throw new Error('private_output');
   const parsed=await parseProposalResponse(result,d.challenge);
   const receipt={...parsed,run_id:uuid(),case:d.challenge.case,challenge_sha256:d.challenge.challenge_sha256,stage:correcting?'correction':'baseline',model:{provider:'Nebius',model:'nvidia/Nemotron-3_5-Lightning',completed:true},sandbox:{completed:false,observations_match:null},checks:[]};
   d.receipts.push(receipt);
   if(parsed.status!=='awaiting_remote_sandbox'){trace(d,'stop','The model response was not eligible for execution.');return save(db,row,d,'done','stopped');}
   trace(d,'proposal','The proposed function is ready for Nebius Sandbox checks.');
   return save(db,row,d,'sandbox_start');
  }
  if(row.phase==='sandbox_start'){
   const receipt=d.receipts.at(-1);
   const bundle=await makeBundle(receipt.proposal,d.challenge,receipt.run_id);
   d.plan=bundle.private_comparison_plan;
   trace(d,'sandbox','Nebius Sandbox is running the function against the contract.');
   await db.prepare('UPDATE repair_jobs SET data=? WHERE id=? AND token=?').bind(JSON.stringify(d),row.id,row.token).run();
   const result=await startSandbox(bundle,settings);
   if(!result.operation_id)throw new Error('sandbox_'+result.failure_stage+'_'+result.error_code+'_'+(result.http_status||''));
   d.operation=result.operation_id;d.sandbox_started=now();
   return save(db,row,d,'sandbox_poll');
  }
  if(row.phase==='sandbox_poll'){
   if(now()-d.sandbox_started>180000)throw new Error('sandbox_deadline');
   const result=await pollSandbox(d.operation,settings);
   if(result.status==='running'||result.status==='poll_unavailable')return save(db,row,d,'sandbox_poll');
   if(result.status!=='completed')throw new Error('sandbox_unconfirmed');
   const compared=await compareResult(d.plan,result.stdout,{exit_code:result.exit_code,timed_out:result.timed_out});
   if(compared.binding_valid!==true||compared.output_valid!==true||!compared.checks?.length)throw new Error('unbound_output');
   const receipt=d.receipts.at(-1);receipt.sandbox={completed:true,observations_match:compared.observations_match,trusted_execution_proven:compared.trusted_execution_proven===true};receipt.sandbox_result=compared;receipt.checks=compared.checks||[];receipt.status=compared.observations_match?'review_ready':'repair_failed';
   receipt.execution={provider:'Nebius Sandbox',operation_id:d.operation,stdout_sha256:await sha(result.stdout)};
   delete d.plan;delete d.operation;
   trace(d,'verification',`${receipt.checks.filter(x=>x.passed).length} of ${receipt.checks.length} checks passed in Nebius Sandbox.`);
   if(!compared.observations_match&&d.receipts.length===1)return save(db,row,d,'correct');
   trace(d,'stop',compared.observations_match?'All declared checks passed. Review the code before using it.':'The correction did not pass every check. The bounded workflow has stopped.');
   return save(db,row,d,'done','completed');
  }
  throw new Error('invalid_phase');
 }catch(error){console.error(JSON.stringify({phase:row.phase,name:error.name,detail:String(error.message).replaceAll(env.NEBIUS_API_KEY,'[redacted]').slice(0,200)}));trace(d,'uncertain','This stage could not be confirmed. It will not be repeated automatically.');return save(db,row,d,'done','uncertain');}
}
export default {async fetch(request,env){
 const url=new URL(request.url);
 if(!url.pathname.startsWith('/api/live/')){
  let name=url.pathname;if(name==='/'||name==='/replay/')name+='index.html';if(name==='/live'||name==='/live/')name='/live/index.html';
  const content=assets[name];if(content===undefined)return new Response('Not found',{status:404});
  const type=name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':name.endsWith('.json')?'application/json':'text/html';
  return new Response(content,{headers:{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}});
 }
 if(!ORIGINS.has(url.origin))return response({error:'origin_not_allowed'},403);
 const origin=request.headers.get('Origin');if((origin&&origin!==url.origin)||(request.method==='POST'&&origin!==url.origin))return response({error:'origin_not_allowed'},403);
 if(!['GET','POST'].includes(request.method))return response({error:'method_not_allowed'},405);
 let visitor=(request.headers.get('Cookie')||'').match(/(?:^|;\s*)repair_visitor=([a-f0-9]{64})(?:;|$)/)?.[1],cookie;
 if(!visitor){visitor=uuid()+uuid();cookie=`repair_visitor=${visitor}; Path=/api/live; HttpOnly; SameSite=Strict; Max-Age=7776000${url.protocol==='https:'?'; Secure':''}`;}
 const owner=await sha(visitor),db=env.DB;
 if(!db)return response({error:'unavailable'},503,cookie);
 try{
  await stopStale(db);
  if(request.method==='GET'&&url.pathname==='/api/live/status')return response(await info(db,owner,env),200,cookie);
  if(request.method==='POST'&&url.pathname==='/api/live/runs'){
   if(!enabled(env))return response({error:'disabled'},403,cookie);
   await stopStale(db);
   let challenge;try{challenge=await buildChallenge(await readJson(request));}catch(_){return response({error:'invalid_input'},400,cookie);}
   const id=uuid(),token=uuid(),d={challenge,trace:[],receipts:[]};trace(d,'queued','Live repair accepted. At most two model proposals will be made.');
   const inserted=await db.prepare('INSERT INTO repair_jobs (id,owner,status,phase,token,data,reserved,active,lease,created) SELECT ?,?,\'queued\',\'propose\',?,?,?,1,0,? WHERE (SELECT COALESCE(SUM(reserved),0) FROM repair_jobs)+?+?<=? AND (SELECT COUNT(*) FROM repair_jobs WHERE owner=?)<2 AND NOT EXISTS(SELECT 1 FROM repair_jobs WHERE active=1)').bind(id,owner,token,JSON.stringify(d),JOB_RESERVATION,now(),HISTORICAL_RESERVED,JOB_RESERVATION,TOTAL_LIMIT,owner).run();
   if(!inserted.meta.changes)return response({error:'quota_exhausted'},429,cookie);
   return response(publicJob(await db.prepare('SELECT * FROM repair_jobs WHERE id=?').bind(id).first()),201,cookie);
  }
  const match=url.pathname.match(/^\/api\/live\/runs\/([a-f0-9]{32})(\/advance)?$/);
  if(!match)return response({error:'not_found'},404,cookie);
  let row=await db.prepare('SELECT * FROM repair_jobs WHERE id=? AND owner=?').bind(match[1],owner).first();
  if(!row)return response({error:'not_found'},404,cookie);
  if(request.method==='GET'&&!match[2])return response(publicJob(row),200,cookie);
  if(request.method!=='POST'||!match[2])return response({error:'method_not_allowed'},405,cookie);
  const body=await readJson(request);
  if(body.phase_token!==row.token||row.lease||TERMINAL.has(row.status))return response(publicJob(row),200,cookie);
  const locked=await db.prepare('UPDATE repair_jobs SET lease=?,status=\'running\' WHERE id=? AND token=? AND lease=0 AND active=1').bind(now(),row.id,row.token).run();
  if(!locked.meta.changes)return response(publicJob(await db.prepare('SELECT * FROM repair_jobs WHERE id=?').bind(row.id).first()),200,cookie);
  row=await advance(row,env);return response(publicJob(row),200,cookie);
 }catch(_){return response({error:'unavailable'},503,cookie);}
}};
