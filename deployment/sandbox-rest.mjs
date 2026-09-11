/** Narrow official ConTree REST adapter. No local candidate execution or retries.
 * Caller MUST durably reserve each start before invoking it, then persist uuid.
 * Workers can poll in separate requests; this module never waits for execution.
 */
export const SANDBOX_BASE = 'https://api.tokenfactory.nebius.com/sandboxes/v1';
export const LIMITS = Object.freeze({uploadBytes:262144, outputBytes:65536, responseBytes:262144, requestMs:10000, executionSeconds:20});
const NAMES = ['candidate.py','runner.py','job.json'];
const enc = new TextEncoder();
const identifier = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const base = {provider:'nebius-contree-rest', trusted_execution_proven:false};
class AdapterError extends Error {
  constructor(code,httpStatus){super(code);this.code=code;if(Number.isInteger(httpStatus)&&httpStatus>=100&&httpStatus<=599)this.http_status=httpStatus;}
}
function diagnostic(error,stage){
  const known=error instanceof AdapterError;
  return {failure_stage:stage,error_code:known?error.code:error instanceof TypeError?'runtime_type_error':error instanceof SyntaxError?'malformed_json':error instanceof ReferenceError?'runtime_reference_error':'unexpected_failure',...(known&&error.http_status?{http_status:error.http_status}:{})};
}

async function hash(bytes) {return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');}
function options({apiKey,projectId,fetchImpl=globalThis.fetch.bind(globalThis)}={}) {
  if(typeof apiKey!=='string'||apiKey.length<30||/\s/.test(apiKey)||!identifier(projectId)||typeof fetchImpl!=='function') throw new AdapterError('configuration');
  return {apiKey,projectId,fetchImpl};
}
async function request(path, method, body, config) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),LIMITS.requestMs);
  try {
    const response=await config.fetchImpl(SANDBOX_BASE+path,{method,redirect:'manual',signal:controller.signal,
      headers:{Authorization:'Bearer '+config.apiKey,Project:config.projectId,Accept:'application/json',...(body===undefined?{}:{'Content-Type':body instanceof Uint8Array?'application/octet-stream':'application/json'})},
      ...(body===undefined?{}:{body:body instanceof Uint8Array?body:JSON.stringify(body)})});
    if(!response.ok) {try{await response.body?.cancel();}catch{}throw new AdapterError('http_response',response.status);}
    const declared=response.headers.get('content-length');
    if(declared!==null&&(!/^\d+$/.test(declared)||Number(declared)>LIMITS.responseBytes)){await response.body?.cancel();throw new AdapterError('size');}
    if(!response.body)throw new AdapterError('missing_body');
    const reader=response.body.getReader();let length=0;const chunks=[];
    try {while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>LIMITS.responseBytes){await reader.cancel();throw new AdapterError('size');}chunks.push(value);}}
    finally{reader.releaseLock();}
    const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
    const text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);
    if(text.includes(config.apiKey))throw new AdapterError('private_response');
    const parsed=JSON.parse(text);if(!plain(parsed))throw new AdapterError('invalid_response');return parsed;
  } catch(error){if(controller.signal.aborted)throw new AdapterError('request_timeout');throw error;} finally {clearTimeout(timer);}
}
async function snapshot(bundle, apiKey) {
  // Snapshot strings/bindings before the first await; caller mutation cannot alter uploads.
  if(!plain(bundle)||JSON.stringify(bundle.command)!==JSON.stringify(['python3','-I','runner.py'])||!plain(bundle.upload_files)||!plain(bundle.private_comparison_plan))throw new AdapterError('bundle');
  if(Object.keys(bundle.upload_files).sort().join('|')!==[...NAMES].sort().join('|'))throw new AdapterError('files');
  const plan=structuredClone(bundle.private_comparison_plan);
  const files={};let total=0;
  for(const name of NAMES){const value=bundle.upload_files[name];if(typeof value!=='string'||value.includes(apiKey))throw new AdapterError('upload');files[name]=enc.encode(value);total+=files[name].byteLength;}
  if(total>LIMITS.uploadBytes)throw new AdapterError('size');
  const job=JSON.parse(new TextDecoder().decode(files['job.json']));
  for(const field of ['run_id','challenge_sha256','proposal_sha256'])if(typeof plan[field]!=='string'||job[field]!==plan[field])throw new AdapterError('binding');
  if(!plain(plan.file_sha256))throw new AdapterError('hashes');
  const digests={};for(const name of NAMES){digests[name]=await hash(files[name]);if(plan.file_sha256[name]!==digests[name])throw new AdapterError('tampered');}
  return {files,digests};
}
export async function startSandbox(bundle, settings) {
  let dispatched=false,uploadAttempted=false,stage='validation',failureStage='validation';
  try {
    const config=options(settings);const {files,digests}=await snapshot(bundle,config.apiKey);const mapped={};
    for(const name of NAMES){stage='upload';failureStage='upload_'+name;uploadAttempted=true;const stored=await request('/files','POST',files[name],config);
      if(!identifier(stored.uuid))throw new AdapterError('upload_uuid_invalid');
      if(stored.sha256!==digests[name])throw new AdapterError('upload_hash_mismatch');
      if(stored.size!==files[name].byteLength)throw new AdapterError('upload_size_mismatch');
      mapped['/tmp/'+name]={uuid:stored.uuid,mode:'0644',uid:0,gid:0};}
    stage='dispatch';failureStage='dispatch';dispatched=true;
    const spawned=await request('/instances','POST',{command:'python3',image:'tag:python:3.12-slim',hostname:'localhost',args:['-I','runner.py'],shell:false,env:{},preserve_env:false,networking:{enabled:false},stdin:{value:'',encoding:'ascii',close:true},cwd:'/tmp',disposable:true,timeout:LIMITS.executionSeconds,truncate_output_at:LIMITS.outputBytes,files:mapped},config);
    if(!identifier(spawned.uuid))throw new AdapterError('operation_id');
    return {...base,status:'running',operation_id:spawned.uuid,remote_attempted:true,upload_attempted:true,automatic_retries:0};
  } catch(error) {return {...base,...diagnostic(error,failureStage),status:stage==='validation'?'rejected':'unverified',remote_attempted:dispatched,upload_attempted:uploadAttempted,stage,automatic_retries:0,reason:stage==='validation'?'Invalid configuration or synthetic bundle; no request sent.':'Provider response unavailable or invalid; do not repeat this start automatically.'};}
}
function decodeStream(stream){
  if(!plain(stream)||typeof stream.value!=='string'||(stream.truncated!==undefined&&typeof stream.truncated!=='boolean')||stream.truncated)throw new AdapterError('stream');
  let bytes;
  if(stream.encoding==='ascii'){if(stream.value.length>LIMITS.outputBytes)throw new AdapterError('size');bytes=enc.encode(stream.value);}
  else if(stream.encoding==='base64'){
    if(stream.value.length>Math.ceil(LIMITS.outputBytes/3)*4||! /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(stream.value))throw new AdapterError('encoding');
    bytes=Uint8Array.from(atob(stream.value),c=>c.charCodeAt(0));
  }else throw new AdapterError('encoding');
  if(bytes.length>LIMITS.outputBytes)throw new AdapterError('size');return new TextDecoder('utf-8',{fatal:true}).decode(bytes);
}
export async function pollSandbox(operationId, settings) {
  let config;
  try {config=options(settings);if(!identifier(operationId))throw new AdapterError('id');}
  catch(error) {return {...base,...diagnostic(error,'poll_validation'),status:'rejected',reason:'Invalid provider configuration or operation ID.'};}
  let operation;
  try{operation=await request('/operations/'+encodeURIComponent(operationId),'GET',undefined,config);}
  catch(error){return {...base,...diagnostic(error,'poll_read'),status:'poll_unavailable',operation_id:operationId,retry_safe:true,reason:'Read-only operation status unavailable. No execution was restarted.'};}
  try {
    if(operation.uuid!==operationId)throw new AdapterError('binding');
    if(['PENDING','ASSIGNED','EXECUTING'].includes(operation.status))return {...base,status:'running',operation_id:operationId};
    if(operation.status!=='SUCCESS')return {...base,status:'unverified',operation_id:operationId,reason:'Remote operation did not complete successfully.'};
    const result=operation.metadata?.result;
    if(!plain(result?.state)||typeof result.state.exit_code!=='number'||!Number.isSafeInteger(result.state.exit_code)||(result.state.timed_out!==undefined&&typeof result.state.timed_out!=='boolean'))throw new AdapterError('result');
    const stdout=decodeStream(result.stdout);const stderr=decodeStream(result.stderr);
    if(stdout.includes(config.apiKey)||stderr.includes(config.apiKey))throw new AdapterError('private_output');
    return {...base,status:'completed',operation_id:operationId,stdout,exit_code:result.state.exit_code,timed_out:result.state.timed_out===true,stdout_sha256:await hash(enc.encode(stdout)),transcript_provenance:'self-reported by candidate runtime'};
  } catch(error){return {...base,...diagnostic(error,'poll_result'),status:'unverified',operation_id:operationId,reason:'Remote result missing, mismatched, truncated or malformed; no repair verdict available.'};}
}
