// Immutable reservation accounting and a rolling visitor window are independent limits.
// The owner authorized the $24.96 gifted balance as the total application ceiling,
// including historical and current reservations. Hosting variables cannot raise it.
export const HISTORICAL_RESERVED=190000, JOB_RESERVATION=20000, TOTAL_LIMIT=24_960_000;
export const VISITOR_LIMIT=2, VISITOR_WINDOW_MS=24*60*60*1000;
// January 1 is included in the published America/Los_Angeles access window.
export const LIVE_UNTIL='2027-01-02T08:00:00.000Z';
export const DEADLINE=Date.parse(LIVE_UNTIL);
export function configurationReason(env,at){
 if(at>=DEADLINE)return 'judging_window_ended';
 if(env.LIVE_ENABLED!=='true')return 'operator_disabled';
 if(!env.NEBIUS_API_KEY||!/^aiproject-[A-Za-z0-9_-]+$/.test(env.NEBIUS_PROJECT_ID||''))return 'provider_unconfigured';
 return null;
}
export const STATS_SQL='SELECT COALESCE(SUM(reserved),0) AS reserved,COALESCE(SUM(CASE WHEN owner=? AND created>? THEN 1 ELSE 0 END),0) AS visitor,MIN(CASE WHEN owner=? AND created>? THEN created END) AS visitor_oldest,COALESCE(MAX(active),0) AS active FROM repair_jobs';
export function statsBindings(owner,at){return [owner,at-VISITOR_WINDOW_MS,owner,at-VISITOR_WINDOW_MS];}
export function admissionState(stats,env,at){
 const remaining=Math.max(0,TOTAL_LIMIT-HISTORICAL_RESERVED-Number(stats.reserved||0));
 const visitor=Number(stats.visitor||0);
 const reason=configurationReason(env,at)||(stats.active?'busy':remaining<JOB_RESERVATION?'allowance_exhausted':visitor>=VISITOR_LIMIT?'session_limit':null);
 return {enabled:!reason,replay_only:!!reason,reason,jobs_per_visitor:VISITOR_LIMIT,
  visitor_window_seconds:VISITOR_WINDOW_MS/1000,visitor_runs_used:visitor,visitor_runs_remaining:Math.max(0,VISITOR_LIMIT-visitor),
  visitor_reset_at:stats.visitor_oldest==null?null:new Date(Number(stats.visitor_oldest)+VISITOR_WINDOW_MS).toISOString(),
  remaining_run_reservations:Math.floor(remaining/JOB_RESERVATION),remaining_budget_usd:remaining/1000000,
  live_until:LIVE_UNTIL,live_until_inclusive_date:'2027-01-01',live_timezone:'America/Los_Angeles',
  cases:['schema','pagination','custom'],execution_provider:'Nebius Sandbox',model_provider:'Nebius Token Factory',model:'NVIDIA Nemotron',credit_budget_usd:TOTAL_LIMIT/1000000};
}
// All mutable admission predicates are evaluated in the same INSERT statement.
// Historical rows are never deleted, refunded, or reset when a visitor window expires.
export const ADMISSION_SQL="INSERT INTO repair_jobs (id,owner,status,phase,token,data,reserved,active,lease,created) SELECT ?,?,'queued','propose',?,?,?,1,0,? WHERE ?<? AND (SELECT COALESCE(SUM(reserved),0) FROM repair_jobs)+?+?<=? AND (SELECT COUNT(*) FROM repair_jobs WHERE owner=? AND created>?)<? AND NOT EXISTS(SELECT 1 FROM repair_jobs WHERE active=1)";
export function admissionBindings({id,owner,token,data,at}){return [id,owner,token,JSON.stringify(data),JOB_RESERVATION,at,at,DEADLINE,HISTORICAL_RESERVED,JOB_RESERVATION,TOTAL_LIMIT,owner,at-VISITOR_WINDOW_MS,VISITOR_LIMIT];}
