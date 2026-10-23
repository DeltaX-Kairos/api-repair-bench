# Hosted Worker template

The live public page uses this Worker orchestration with the live-ui assets. The Worker keeps the API key server-side, stores job state in the repair_jobs D1 table, and advances one durable phase at a time. NEBIUS_API_KEY and NEBIUS_PROJECT_ID are runtime values; they are never part of the repository.

The Worker calls Nebius Token Factory for NVIDIA Nemotron and Nebius Sandbox for candidate execution. It does not execute generated source in the Worker. The public deployment uses the same bounded transport and phase-token behavior shown in the portable tests.

`schema.sql` is the corresponding D1 migration. `test-worker-loader.mjs` loads the real orchestration for offline API tests with a deliberately empty static asset map. It is not a production entrypoint; the Sites build generates the production asset module. No provider request is made by those tests.

The production admission policy in `live-policy.mjs` permits two jobs per browser in each rolling 24-hour window, one active job globally, and a $24.96 cumulative reservation ceiling inclusive of retained history. The judging window includes January 1, 2027, Pacific time. Recording overrides cannot expand these limits. See [judge-access operations](../docs/judge-access.md) for current verification and dependencies.
