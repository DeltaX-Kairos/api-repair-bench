# Hosted Worker template

The live public page uses this Worker orchestration with the live-ui assets. The Worker keeps the API key server-side, stores job state in the repair_jobs D1 table, and advances one durable phase at a time. NEBIUS_API_KEY and NEBIUS_PROJECT_ID are runtime values; they are never part of the repository.

The Worker calls Nebius Token Factory for NVIDIA Nemotron and Nebius Sandbox for candidate execution. It does not execute generated source in the Worker. The public deployment uses the same bounded transport and phase-token behavior shown in the portable tests.
