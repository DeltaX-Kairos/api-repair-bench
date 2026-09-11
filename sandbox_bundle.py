"""Build remote-only execution files and compare untrusted observations externally.

This module never imports or executes candidate source. The embedded runner MUST
only be launched by a future approved remote sandbox adapter, never on this host.
"""
import copy
import hashlib
import json
import secrets

from challenges import challenge, digest, validate_proposal

# This standalone remote script contains no oracle, reference repair, or scorer.
REMOTE_RUNNER = '''import json
from pathlib import Path

class ContractError(ValueError):
    pass

class API:
    def __init__(self, pages):
        self.pages = {entry["cursor"]: entry["page"] for entry in pages}
        self.calls = []
    def fetch(self, cursor):
        self.calls.append(cursor)
        if len(self.calls) > 10:
            raise ContractError("request budget exhausted")
        return self.pages[cursor]

class AuthAPI:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []
        self.requests = 0
    def request(self, token):
        self.calls.append({"request": token})
        self.requests += 1
        if self.requests > 3:
            raise ContractError("request budget exhausted")
        if token == self.fixture["fresh_token"]:
            return dict(self.fixture["replacement_response"])
        return dict(self.fixture["initial_response"])
    def refresh(self):
        self.calls.append({"refresh": True})
        return self.fixture["fresh_token"]

job = json.loads(Path("job.json").read_text())
namespace = {"ContractError": ContractError}
exec(compile(Path("candidate.py").read_text(), "candidate.py", "exec"), namespace)
observations = []
for fixture in job["fixtures"]:
    api = API(fixture["pages"]) if job["case"] == "pagination" else (AuthAPI(fixture) if job["case"] == "auth" else None)
    try:
        if job["case"] == "custom":
            value = namespace[job["function_name"]](*fixture["args"], **fixture["kwargs"])
        elif job["case"] == "schema":
            value = namespace["schema_bug"](fixture["record"], fixture["contract"])
        else:
            value = namespace[job["case"] + "_bug"](api)
        outcome = {"kind": "return", "value": value}
    except Exception as exc:
        outcome = {"kind": "error", "type": type(exc).__name__, "message": str(exc)}
    observations.append({"id": fixture["id"], "outcome": outcome, "calls": api.calls if api else []})
print(json.dumps({"run_id": job["run_id"], "challenge_sha256": job["challenge_sha256"],
                  "proposal_sha256": job["proposal_sha256"], "observations": observations}))
'''


def make_bundle(proposal, request, run_id=None):
    """Return upload files and a separate private comparison plan; no execution."""
    case = request.get("case") if isinstance(request, dict) else None
    if case == "custom":
        from custom_cases import make_custom_bundle
        return make_custom_bundle(proposal, request, run_id or secrets.token_hex(16))
    if case not in ("pagination", "schema", "auth") or request != challenge(case):
        raise ValueError("only current known challenges are supported")
    validated = validate_proposal(proposal, request)
    if validated["status"] != "awaiting_remote_sandbox":
        raise ValueError("a patch is required")
    run_id = run_id or secrets.token_hex(16)
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
        raise ValueError("invalid run id")
    # Per-run cursor/data variation limits accidental hardcoding. It is not secret
    # from candidate code and is not an adversarial evaluation safeguard.
    tag = hashlib.sha256(run_id.encode()).hexdigest()[:12]
    c2, c3, loop = 'p2-' + tag, 'p3-' + tag, 'loop-' + tag
    a, b = '001-' + tag, '1-' + tag
    fixtures = [
        {"id": "empty-middle-duplicates", "pages": [
            {"cursor": None, "page": {"items": [a], "next": c2}},
            {"cursor": c2, "page": {"items": [], "next": c3}},
            {"cursor": c3, "page": {"items": [a, b], "next": None}}]},
        {"id": "empty-terminal", "pages": [
            {"cursor": None, "page": {"items": [], "next": None}}]},
        {"id": "cursor-cycle", "pages": [
            {"cursor": None, "page": {"items": [], "next": loop}},
            {"cursor": loop, "page": {"items": [], "next": loop}}]},
    ]
    binding = {"run_id": run_id, "challenge_sha256": validated["challenge_sha256"],
               "proposal_sha256": validated["proposal_sha256"]}
    expected = [
        {"id": "empty-middle-duplicates", "outcome": {"kind": "return", "value": [a, a, b]}, "calls": [None, c2, c3]},
        {"id": "empty-terminal", "outcome": {"kind": "return", "value": []}, "calls": [None]},
        {"id": "cursor-cycle", "outcome": {"kind": "error", "type": "ContractError", "message": "repeated pagination cursor"}, "calls": [None, loop]},
    ]
    if case == "schema":
        fixtures, expected = schema_fixtures(tag)
    elif case == "auth":
        fixtures, expected = auth_fixtures(tag)
    files = {"candidate.py": proposal["source"], "runner.py": REMOTE_RUNNER,
             "job.json": json.dumps({**binding, "case": case, "fixtures": fixtures}, sort_keys=True)}
    plan = {**binding, "expected_observations": expected,
            "file_sha256": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in files.items()}}
    return {"upload_files": files, "private_comparison_plan": plan,
            "command": ["python3", "runner.py"], "execution_performed": False}


def schema_fixtures(tag):
    units = int(tag[:4], 16) + 1
    fixtures, expected = [], []
    cases = [
        ("major", {"id": "00" + tag, "amount": f"{units}.37"}, {"amount_unit": "major"},
         {"kind": "return", "value": {"id": "00" + tag, "amount_minor": units * 100 + 37}}),
        ("minor", {"id": "01" + tag, "amount": units}, {"amount_unit": "minor"},
         {"kind": "return", "value": {"id": "01" + tag, "amount_minor": units}}),
        ("refund", {"id": "refund-" + tag, "amount": "-0.01"}, {"amount_unit": "major"},
         {"kind": "return", "value": {"id": "refund-" + tag, "amount_minor": -1}}),
        ("missing-unit", {"id": tag, "amount": units}, {},
         {"kind": "error", "type": "ContractError", "message": "amount unit needs confirmation"}),
    ]
    for name, amount, unit in (("precision", "0.001", "major"), ("fractional-minor", "1.5", "minor"),
                               ("nan", "NaN", "major"), ("infinity", "Infinity", "minor"),
                               ("invalid", "not-money", "major")):
        cases.append((name, {"id": tag, "amount": amount}, {"amount_unit": unit},
                      {"kind": "error", "type": "ContractError"}))
    for name, record, contract, outcome in cases:
        fixtures.append({"id": name, "record": record, "contract": contract})
        expected.append({"id": name, "outcome": outcome, "calls": []})
    return fixtures, expected


def auth_fixtures(tag):
    success = {"status": 200, "data": ["item-" + tag], "receipt": tag}
    expired = {"status": 401, "error": "expired_token"}
    fixtures, expected = [], []
    for name, initial, replacement in (
        ("expired", expired, success), ("replacement-failed", expired, expired),
        ("success", success, success),
        ("forbidden", {"status": 403, "error": "forbidden", "detail": tag}, success),
        ("server-error", {"status": 500, "error": "temporary_failure", "retry_after": 17}, success),
        ("other-401", {"status": 401, "error": "invalid_audience", "detail": tag}, success),
    ):
        token = "fresh-" + name + "-" + tag
        fixtures.append({"id": name, "fresh_token": token,
                         "initial_response": initial, "replacement_response": replacement})
        calls = [{"request": "fixture-expired"}]
        refresh = name in ("expired", "replacement-failed")
        if refresh:
            calls += [{"refresh": True}, {"request": token}]
        expected.append({"id": name, "outcome": {"kind": "return", "value": replacement if refresh else initial},
                         "calls": calls})
    return fixtures, expected


def compare_result(plan, stdout, *, exit_code, timed_out=False):
    """Compare bounded JSON literally. Matching output is NOT trusted execution proof."""
    result = {"observations_match": False, "trusted_execution_proven": False,
              "transcript_provenance": "self-reported by candidate runtime"}
    if timed_out or type(exit_code) is not int or exit_code != 0:
        return {**result, "reason": "remote execution incomplete or failed"}
    if not isinstance(stdout, str) or len(stdout.encode()) > 65536:
        return {**result, "reason": "invalid or oversized output"}
    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON key")
            obj[key] = value
        return obj
    try:
        observed = json.loads(stdout, object_pairs_hook=unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        expected = {key: plan[key] for key in ("run_id", "challenge_sha256", "proposal_sha256")}
        expected["observations"] = plan["expected_observations"]
        # Invalid-amount wording is not prescribed by the schema contract.
        # Require the error class but permit a string message for those rows only.
        reported = observed
        observed = copy.deepcopy(observed)
        if isinstance(observed, dict) and isinstance(observed.get("observations"), list):
            for actual, wanted in zip(observed["observations"], expected["observations"]):
                outcome = actual.get("outcome") if isinstance(actual, dict) else None
                target = wanted["outcome"]
                if (target.get("kind") == "error" and "message" not in target
                        and isinstance(outcome, dict) and isinstance(outcome.get("message"), str)):
                    outcome.pop("message")
        # Canonical bytes distinguish true from 1, unlike Python equality.
        matches = digest(observed) == digest(expected)
    except (ValueError, TypeError, RecursionError):
        return {**result, "reason": "malformed output"}
    checks, diagnostics = [], []
    if isinstance(observed, dict) and isinstance(observed.get("observations"), list):
        binding_matches = all(observed.get(k) == expected[k] for k in ("run_id", "challenge_sha256", "proposal_sha256"))
        actual_rows = observed["observations"]
        for index, wanted in enumerate(expected["observations"]):
            actual = actual_rows[index] if index < len(actual_rows) else None
            checks.append({"id": wanted["id"], "passed": binding_matches and digest(actual) == digest(wanted)})
            # Candidate-supplied exceptions only: no expected values or reference
            # source. Retain raw wording before error-message normalization.
            raw_rows = reported.get("observations", [])
            raw = raw_rows[index] if index < len(raw_rows) else None
            outcome = raw.get("outcome") if isinstance(raw, dict) else None
            if (binding_matches and not checks[-1]["passed"] and len(diagnostics) < 8
                    and isinstance(raw, dict) and raw.get("id") == wanted["id"]
                    and isinstance(outcome, dict) and outcome.get("kind") == "error"
                    and isinstance(outcome.get("type"), str)):
                diagnostic = {"id": str(raw["id"])[:80],
                              "exception_type": outcome["type"][:64]}
                if isinstance(outcome.get("message"), str):
                    diagnostic["message"] = outcome["message"][:160]
                diagnostics.append(diagnostic)
    return {**result, "observations_match": matches, "checks": checks,
            "diagnostics": diagnostics,
            "mismatches": [c["id"] for c in checks if not c["passed"]],
            "reason": "observations match; runtime can spoof them" if matches else "binding, outcome or request transcript mismatch"}
