"""Separate model-visible challenges from reference repairs and regression checks."""
import ast
import hashlib
import inspect
import json
from pathlib import Path

import bench


SPECIFICATIONS = {
    "pagination": "Interface: api.fetch(cursor) returns a Python dict with items (a list) and next (a string cursor or None). Initial cursor is None. Return all items in order, preserving duplicate occurrences. Follow the next field until None, including after empty pages. Stop with ContractError('repeated pagination cursor') before fetching a cursor twice. Make at most ten fetch requests; if more would be required, raise ContractError('request budget exhausted') instead of issuing an eleventh request.",
    "schema": "Interface: record and contract are Python dicts. record contains id (a string) and amount (a numeric value or decimal string); contract may contain the amount_unit key. Return a Python dict with id and amount_minor. Preserve string identifiers exactly; reject non-string identifiers with ContractError. Convert amount to integer minor units only when contract['amount_unit'] is explicitly 'major' (100 minor units per major) or 'minor'. Reject absent or unsupported units with ContractError('amount unit needs confirmation'). Reject invalid, nonfinite or fractional minor-unit amounts with ContractError; do not round them. Python standard-library imports may be placed inside the replacement function.",
    "auth": "Interface: api.request(token: str) returns a Python dict with integer status and optional string error keys; other keys are arbitrary response data. api.refresh() returns the replacement token string. Call api.request('fixture-expired'). Only status 401 with error expired_token permits one api.refresh(), followed by one request with that result. Preserve all other responses, including 403. Stop after the replacement token fails. Never retry forever.",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def challenge(name):
    # Control labels stay in the evaluator module; only its public challenge is
    # routed here. Existing inference and durable-budget handling remain shared.
    if isinstance(name, str) and name.startswith("decision-"):
        from ambiguity_cases import decision_challenge
        return decision_challenge(name[len("decision-"):])
    broken, _, _ = bench.CASES[name]
    payload = {
        "case": name,
        "task": "Repair this function against the stated contract. Reply with JSON only.",
        "contract": SPECIFICATIONS[name],
        "candidate_source": inspect.getsource(broken),
        "available_exception": "ContractError(ValueError)",
        "response_schema": {
            "challenge_sha256": "copy the supplied challenge_sha256",
            "decision": "patch or clarify",
            "source": "complete replacement function, or empty for clarify",
            "explanation": "short explanation or specific missing requirement",
        },
    }
    payload["challenge_sha256"] = digest(payload)
    return payload


def validate_proposal(proposal, request):
    """Structural check only. Never compile or execute model code on this host."""
    required = {"challenge_sha256", "decision", "source", "explanation"}
    if not isinstance(proposal, dict) or set(proposal) != required:
        raise ValueError("unexpected proposal fields")
    if not all(isinstance(proposal[k], str) for k in required):
        raise ValueError("proposal fields must be strings")
    request_body = {k: v for k, v in request.items() if k != "challenge_sha256"}
    if digest(request_body) != request.get("challenge_sha256"):
        raise ValueError("challenge integrity mismatch")
    if proposal["challenge_sha256"] != request["challenge_sha256"]:
        raise ValueError("proposal is for a different challenge")
    if not 1 <= len(proposal["explanation"].strip()) <= 2000:
        raise ValueError("explanation required and limited to 2000 characters")
    if proposal["decision"] == "clarify":
        if proposal["source"]:
            raise ValueError("clarification must not include a patch")
        return {"status": "needs_clarification", "executed": False}
    if proposal["decision"] != "patch" or not 1 <= len(proposal["source"]) <= 16000:
        raise ValueError("invalid patch decision or size")
    try:
        tree = ast.parse(proposal["source"])
    except (SyntaxError, RecursionError) as exc:
        raise ValueError("invalid Python proposal") from exc
    original = ast.parse(request["candidate_source"]).body[0]
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError("proposal must contain exactly one function")
    fn = tree.body[0]
    if fn.name != original.name or ast.dump(fn.args) != ast.dump(original.args):
        raise ValueError("function name and signature must be preserved")
    if fn.decorator_list or fn.returns is not None:
        raise ValueError("decorators and return annotations are not accepted")
    return {"status": "awaiting_remote_sandbox", "executed": False,
            "proposal_sha256": digest(proposal), "challenge_sha256": request["challenge_sha256"]}


def export(directory):
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for name in bench.CASES:
        data = challenge(name)
        path = directory / (name + ".json")
        path.write_text(json.dumps(data, indent=2) + "\n")
        entries.append({"case": name, "challenge_sha256": data["challenge_sha256"]})
    (directory / "manifest.json").write_text(json.dumps({"entries": entries,
        "contains_reference_repairs": False, "contains_regression_tests": False}, indent=2) + "\n")
    return entries


if __name__ == "__main__":
    print(json.dumps(export(Path("output/challenges")), indent=2))
