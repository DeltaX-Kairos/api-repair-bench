# Nebius Sandbox REST transport

Verified against installed `contree-client` 0.2.2 and the reviewed Python SDK source, September 11, 2026. This is ordinary HTTPS with JSON and byte uploads, not gRPC or Connect. Nebius also publishes a fetch-based [JavaScript client](https://github.com/nebius/contree-client/tree/master/client-js), generated from the same API specification.

Base URL: `https://api.tokenfactory.nebius.com/sandboxes/v1`

Every request has `Authorization: Bearer <private key>` and `Project: <configured project ID>`. Keys stay in the server-side Worker secret store. Neither credential belongs in a browser request, uploaded runner or public response.

## Wire sequence

1. `POST /files`, `Content-Type: application/octet-stream`, raw file bytes. Response fields: `uuid`, `sha256`, `size`. Upload the three bundle files individually, validating the returned hashes and byte counts.
2. `POST /instances`, JSON containing fixed `command: python3`, `image: tag:python:3.12-slim`, `args: [-I, runner.py]`, `cwd: /tmp`, and `files` mapping each full `/tmp/...` path to `{uuid, mode: '0644', uid: 0, gid: 0}`. The adapter fixes `shell:false`, `env:{}`, `preserve_env:false`, `disposable:true`, `timeout:20`, `truncate_output_at:65536`, closed empty stdin, and `networking:{enabled:false}`. The response's `uuid` identifies the operation.
3. `GET /operations/{uuid}` once per poll. Active states: `PENDING`, `ASSIGNED`, `EXECUTING`. Terminal states: `SUCCESS`, `FAILED`, `CANCELLED`. A successful operation contains `metadata.result.state.exit_code`, optional `timed_out`, and `stdout`/`stderr` representations with `value`, `encoding` (`ascii` or `base64`) and optional `truncated`.
4. Decode bounded output and pass it to the independent core comparator. A completed operation is not itself a successful repair; output remains candidate-reported and untrusted.

The upload/builders are in installed `contree_client/operations.py` at `build_upload_file` and `build_spawn_instance`. URL/header logic is `base.py:163–180`; response models are `models.py:544–595`, `766–788`, `1030–1120`, and `1453–1535`. The reviewed SDK maps uploaded file IDs in `sdk/objects/image_like/_sync.py:301–312`.

## Adapter contract

`startSandbox(bundle, {apiKey, projectId, fetchImpl})` validates and snapshots the core bundle, makes three upload requests and one spawn request, then returns immediately with a `running` result and `operation_id`. The caller must durably reserve before invoking it and persist the returned ID. A lost spawn response is `unverified`, never a reason to repeat the POST.

`pollSandbox(operationId, settings)` makes one GET and never waits for the operation or starts another one. `completed` contains `stdout`, `exit_code`, `timed_out`, and `stdout_sha256`; feed these into `compareResult`. `poll_unavailable` marks an unavailable read with `retry_safe:true`; only another GET for that known operation is safe. Malformed or failed terminal results remain `unverified`.

Each HTTP request has a 10-second abort deadline, 256 KiB response ceiling, manual redirect handling with 3xx rejection and sanitized errors. Remote execution requests have a 20-second limit and 64 KiB per output stream. Source bundles are limited to 256 KiB, require matching hashes and job bindings, and exclude the comparison plan.

## Worker feasibility and remaining verification

The transport uses only fetch, Web Crypto, TextEncoder/TextDecoder, AbortController and standard JavaScript. It imports no Python, Node-only runtime, subprocess or filesystem interface. It can therefore be called by a Worker without a Python host. Sandbox needs no public incoming port: the Worker supplies the public HTTP API and makes outbound provider calls.

Durable job ownership, budget reservations, polling phases, provider secrets and model-request lifecycle belong to the outer Worker. This transport does not supply them or establish that the current hosting account enables the required bindings. No long model invocation should depend on a background task surviving after its request closes.

Eighteen offline tests pass, including exact wire shapes, hashing/tamper checks, no POST retries, bounded output, sanitized failures and integration with the actual portable core bundle. No provider request or deployment was made for these tests. A bounded live verification of these exact REST calls remains necessary before claiming the Worker path works publicly.

Safe diagnostics include only fixed `failure_stage`, static `error_code`, and optional numeric `http_status`; raw provider bodies, headers and exception messages are never returned. The Python SDK also uses an optional GET-by-hash deduplication before upload; direct raw-byte POST remains its supported upload operation. Its OCI normalizer resolves the image used here to exactly `python:3.12-slim`.

The default fetch is bound to `globalThis`, preserving the receiver required by Worker runtimes. Injected test fetch functions remain supported.
