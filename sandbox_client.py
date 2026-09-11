"""Remote ConTree adapter. Never executes candidate code on the caller's host."""
import copy
import hashlib
import json
import inspect
import math
import os
from contextlib import contextmanager

from sandbox_bundle import REMOTE_RUNNER, compare_result

ENDPOINT = "https://api.tokenfactory.nebius.com/sandboxes"
IMAGE = "python:3.12-slim"
TIMEOUT_SECONDS = 20
OUTPUT_BYTES = 65536
UPLOAD_BYTES = 262144


class BoundedOutput:
    """SDK writable sink; retain at most limit bytes even if server cap fails."""
    def __init__(self, limit=OUTPUT_BYTES):
        self.limit = limit
        self.data = bytearray()
        self.overflow = False

    def write(self, value):
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        remaining = self.limit - len(self.data)
        self.data.extend(raw[:remaining])
        self.overflow |= len(raw) > remaining
        return len(raw)

    def text(self):
        return self.data.decode("utf-8", errors="strict")


class SDKCompatibilityError(RuntimeError):
    pass


@contextmanager
def sdk_client(api_key):
    # Key is explicit and in memory; project is caller configuration, never bundled.
    project_id = os.environ.get("REPAIR_BENCH_PROJECT_ID", "").strip()
    if not project_id or any(c.isspace() for c in project_id):
        raise ValueError("set REPAIR_BENCH_PROJECT_ID to your Sandbox project ID")
    from contree_client.httpx import ContreeClient
    from contree_client.runtime import RetryPolicy
    from contree_sdk import ContreeSync
    parameters = inspect.signature(ContreeSync).parameters
    if "client" not in parameters or "operation_timeout" not in parameters:
        raise SDKCompatibilityError("install the reviewed SDK Git revision")
    with ContreeClient(api_key, base_url=ENDPOINT, project=project_id, timeout=30.0,
                       retry=RetryPolicy(max_attempts=1)) as transport:
        yield ContreeSync(transport, operation_timeout=30,
                          operation_run_timeout=TIMEOUT_SECONDS,
                          default_truncate_output_at=OUTPUT_BYTES)


def _validated_snapshot(bundle, api_key):
    snapshot = copy.deepcopy(bundle)
    if snapshot.get("command") != ["python3", "runner.py"]:
        raise ValueError("unsupported remote command")
    files = snapshot.get("upload_files")
    if not isinstance(files, dict) or set(files) != {"candidate.py", "runner.py", "job.json"}:
        raise ValueError("unexpected upload files")
    if any(type(value) is not str for value in files.values()):
        raise ValueError("uploads must be in-memory text")
    if files["runner.py"] != REMOTE_RUNNER:
        raise ValueError("runner differs from approved bundle runner")
    encoded = {"/tmp/" + name: value.encode("utf-8") for name, value in files.items()}
    if sum(map(len, encoded.values())) > UPLOAD_BYTES:
        raise ValueError("upload size limit exceeded")
    if any(api_key in value for value in files.values()):
        raise ValueError("credential present in upload")
    plan = snapshot["private_comparison_plan"]
    hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in files.items()}
    if hashes != plan.get("file_sha256"):
        raise ValueError("bundle hash mismatch")
    job = json.loads(files["job.json"])
    if any(job.get(k) != plan.get(k) for k in ("run_id", "challenge_sha256", "proposal_sha256")):
        raise ValueError("bundle binding mismatch")
    return encoded, plan


def run_bundle(bundle, api_key, *, client_factory=None):
    """One remote attempt; caller must authorize costs/access before calling.

    Inject a context-managed SDK-shaped client for offline tests. Exceptions after
    dispatch have uncertain remote effects; never retry automatically. No key or
    raw provider exception is included in returned evidence.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("an explicit in-memory API key is required")
    files, plan = _validated_snapshot(bundle, api_key)
    stdout, stderr = BoundedOutput(), BoundedOutput()
    base = {"provider": "nebius-contree", "remote_attempted": False,
            "trusted_execution_proven": False, "cost": None,
            "limits_requested": {"timeout_seconds": TIMEOUT_SECONDS,
                                 "output_bytes_per_stream": OUTPUT_BYTES,
                                 "disposable": True}}
    try:
        with (client_factory or sdk_client)(api_key) as client:
            image = client.images.use(IMAGE)
            # Conservative across SDK revisions: preparation may become effectful.
            base["remote_attempted"] = True
            prepared = image.run(command="python3", args=["-I", "runner.py"],
                                 cwd="/tmp", files=files, env={}, stdin=None,
                                 stdout=stdout, stderr=stderr, disposable=True,
                                 preserve_env=False, timeout=TIMEOUT_SECONDS,
                                 truncate_output_at=OUTPUT_BYTES)
            completed = prepared.wait()
            result = completed.result
            if stdout.overflow or stderr.overflow or result.truncated:
                return {**base, "status": "rejected_output", "observations_match": False,
                        "reason": "remote output exceeded limits or was truncated"}
            exit_code = result.exit_code
            # Live SDK returned integral float 0.0 despite its int annotation.
            if type(exit_code) is float and math.isfinite(exit_code) and exit_code.is_integer():
                exit_code = int(exit_code)
            output = stdout.text()
            if api_key in output:
                raise ValueError("credential unexpectedly present in output")
            comparison = compare_result(plan, output, exit_code=exit_code)
            return {**base, **comparison, "status": "completed",
                    "exit_code": exit_code, "stdout": output,
                    "stdout_sha256": hashlib.sha256(output.encode()).hexdigest()}
    except (ImportError, SDKCompatibilityError):
        return {**base, "status": "unverified_remote_attempt" if base["remote_attempted"] else "unavailable",
                "observations_match": False,
                "reason": "SDK dependency unavailable" if not base["remote_attempted"] else "remote completion unknown; do not automatically retry"}
    except Exception:
        return {**base, "status": "unverified_remote_attempt" if base["remote_attempted"] else "unavailable",
                "observations_match": False,
                "reason": "provider request failed; remote completion and cost unknown; do not automatically retry"}

# Descriptive integration entry point retained alongside run_bundle.
execute_bundle = run_bundle
