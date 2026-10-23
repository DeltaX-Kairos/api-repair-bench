# Judge-access operations

**Status, October 23, 2026:** restored and verified on the public workspace. A fresh pagination run completed with 4/4 checks passing on its first proposal, using NVIDIA Nemotron and Nebius Sandbox (job `3da7b307490a4aeb8f4db0800aee7ada`, evaluation `19be67e3b5c3405997b4b7aa1b748fd1`). No patch was deployed. These instructions do not claim uninterrupted future availability.

## Access window and admission

- Public workspace: https://repairbench.deltaxevaluate.com/live/
- Access includes **January 1, 2027 in America/Los_Angeles**. The exclusive cutoff is **2027-01-02T08:00:00Z** (January 2, 00:00 Pacific).
- A browser may start **two jobs in a rolling 24-hour window**. This replaces the previous lifetime two-job allowance; it does not erase prior ledger records.
- **One active job globally** limits concurrent provider work. A busy response is not authorization to dispatch another job.
- Each job allows an initial proposal and at most one correction after a completed, bound failure. Uncertain effects are not automatically retried.

## Budget and provider prerequisites

The checked Nebius balance is **$24.96**. The application cumulative reservation ceiling is **$24.96**, inclusive of historical reservations: **$0.71** was already reserved (26 jobs at $0.02 plus $0.19 of historical reservations), leaving **$24.25** before the restoration-verification run. At $0.02 per admitted job, that admits at most 1,212 further jobs before that run, subject to the other gates.

Reservations are an application admission control, **not actual provider charges, a provider-enforced price, or an account-wide billing limit**. Available provider balance and the local reservation ledger can diverge. Do not clear historical reservations or uncertain records to regain allowance.

No top-up is currently needed. No billing setting change or automatic credit purchase is included. The configured provider key expires September 10, 2031, beyond the access window; validity, permissions, active project, model access, Sandbox availability and actual balance remain external dependencies.

## Daily health check

The active daily read-only monitor inspects the public workspace and health/admission endpoint for expected status, remaining application allowance and cutoff configuration. It should check provider availability/balance only through an authorized read-only account surface. It must not submit a repair, refresh/reset the ledger, reveal credentials or change billing.

Notify on an unavailable service, exhausted/invalid allowance, unexpected policy change, credential or provider error, or a required operator action. Unchanged healthy state needs no daily message. A scheduled check is not a guarantee of provider uptime.

## If access stops

1. Read the admission or provider error and retain the current ledger and run state.
2. Distinguish a rolling browser limit, global active job, cutoff or application ceiling from provider credit, authentication, project or service errors.
3. Keep the [saved replay](https://repairbench.deltaxevaluate.com/) and uninterrupted demonstration available with explicit recorded labels.
4. Resolve only the diagnosed fault. Do not automatically retry uncertain effectful stages, increase budget, buy credits, change billing or expand access limits.

After the verification run, the public admission endpoint reported enabled=true, 1,211 remaining job reservations and $24.23 remaining application reservation capacity. The daily read-only monitor is active; no paid test is scheduled. Keep the dated verification evidence with the release receipt.
