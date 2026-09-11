# Live repair workspace assets

Serve this directory as the live service's static root. It uses same-origin `/api/live/*` endpoints and links to `https://repairbench.deltaxevaluate.com` for the default saved replay. No API keys or provider calls originate in the browser.

The frontend reads `GET /api/live/status` before enabling submission. Starting sends exactly one `POST /api/live/runs` with `{case:"schema"}` or `{source,checks}`. The custom contract is `{description,checks:[{id,args,kwargs?,expected}|{id,args,kwargs?,error}]}`. Limits: source 8 KB, one to eight checks, full request 24 KB; server validation remains authoritative.

It reads the returned `job_id` using `GET /api/live/runs/:id` every three seconds. A lost POST response never triggers a resend. Read failures pause polling and offer a same-run status check. The job identifier is retained in session storage to recover the same run after reload. `uncertain` is a stopping state and never offers another run. Other terminal states offer evidence download and an explicit **Start another repair** action, gated by fresh service availability and remaining visitor jobs. Preparing another run preserves the prior trace, source, download and session restoration until a new explicit submission returns its identifier. No server job or quota is cleared.

Expected run states: queued, running, completed, stopped, uncertain, failed. Trace rows: `{stage,message,at}`. Receipt rows: `{run_id,stage,status,proposal:{source,explanation},checks:[{id,passed}],sandbox:{completed}}`. Returned source and all model text are rendered with text nodes, never HTML or execution.

Worker phase execution is optional: when a read returns `needs_advance:true` plus `phase_token`, the client sends one `POST /api/live/runs/:id/advance` with `{phase_token}`. The token is reserved locally before dispatch and stored in session storage. Read-only polling continues concurrently while that POST runs. A lost response never resends the same token; subsequent reads either expose the completed phase and a new token or remain read-only for the reserved token. Reload restores token reservations and polls the existing job. The backend must enforce durable stale-token deduplication independently. Python responses without `needs_advance` preserve the original read-only polling behavior.

Run `node live-ui/test-live.cjs` from the parent checkout. Tests mock the service, so they make no model or sandbox calls. Visual review is left to the owning task against the running application.
