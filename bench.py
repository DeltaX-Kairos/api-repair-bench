"""Original fictional API failures. Runs reviewed built-ins only, not uploaded code."""
import argparse
import hashlib
import json
from pathlib import Path


class ContractError(ValueError):
    pass


class FixtureAPI:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, cursor):
        self.calls.append(cursor)
        if len(self.calls) > 10:
            raise ContractError("request budget exhausted")
        return self.pages[cursor]


def pagination_bug(api):
    return api.fetch(None)["items"]


def pagination_reference(api):
    cursor, seen, rows = None, set(), []
    while True:
        if cursor in seen:
            raise ContractError("repeated pagination cursor")
        seen.add(cursor)
        page = api.fetch(cursor)
        rows.extend(page["items"])
        cursor = page.get("next")
        if cursor is None:
            return rows


def schema_bug(record, contract):
    return {"id": int(record["id"]), "amount_minor": int(record["amount"])}


def schema_reference(record, contract):
    from decimal import Decimal, InvalidOperation
    if contract.get("amount_unit") not in ("major", "minor"):
        raise ContractError("amount unit needs confirmation")
    if not isinstance(record.get("id"), str):
        raise ContractError("identifier must remain a string")
    try:
        amount = Decimal(str(record["amount"]))
        minor = amount * (100 if contract["amount_unit"] == "major" else 1)
        if not minor.is_finite() or minor != minor.to_integral_value():
            raise ContractError("amount is not an exact minor-unit value")
        return {"id": record["id"], "amount_minor": int(minor)}
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ContractError("invalid amount") from exc


class AuthAPI:
    def __init__(self, failure="expired", refresh_valid=True, *, seed=0):
        self.failure, self.refresh_valid = failure, refresh_valid
        self.requests, self.refreshes = 0, 0
        self.tokens = []
        self.fresh_token = f"fixture-fresh-{seed}"
        self.success = {"status": 200, "data": [f"fictional-item-{seed}", str(seed * 17)],
                        "receipt": f"receipt-{seed}"}
        self.failure_response = {
            "expired": {"status": 401, "error": "expired_token"},
            "forbidden": {"status": 403, "error": "forbidden"},
            "server_error": {"status": 500, "error": "temporary_failure", "retry_after": seed + 1},
            "other_401": {"status": 401, "error": "invalid_audience", "detail": f"audience-{seed}"},
            "success": self.success,
        }[failure]

    def request(self, token):
        self.requests += 1
        self.tokens.append(token)
        if self.requests > 3:
            raise ContractError("request budget exhausted")
        if self.failure == "expired" and token == self.fresh_token and self.refresh_valid:
            return dict(self.success)
        return dict(self.failure_response)

    def refresh(self):
        self.refreshes += 1
        return self.fresh_token


def auth_bug(api):
    return api.request("fixture-expired")


def auth_reference(api):
    response = api.request("fixture-expired")
    if response.get("status") == 401 and response.get("error") == "expired_token":
        response = api.request(api.refresh())
    return response


def require(condition, message="contract check failed"):
    """Scoring must remain active when Python runs with optimization enabled."""
    if not condition:
        raise AssertionError(message)


def pagination_checks(fn):
    api = FixtureAPI({None: {"items": ["001"], "next": "p2"},
                      "p2": {"items": [], "next": "p3"},
                      "p3": {"items": ["001", "1"], "next": None}})
    require(fn(api) == ["001", "001", "1"], "all pages and duplicate occurrences must survive")
    require(api.calls == [None, "p2", "p3"], "empty page is not end of pagination")
    single = FixtureAPI({None: {"items": [], "next": None}})
    require(fn(single) == [] and len(single.calls) == 1, 'contract check failed')
    cyclic = FixtureAPI({None: {"items": [], "next": "a"},
                         "a": {"items": [], "next": "a"}})
    try:
        fn(cyclic)
    except ContractError as exc:
        require(str(exc) == "repeated pagination cursor", 'contract check failed')
        require(len(cyclic.calls) == 2, 'contract check failed')
    else:
        raise AssertionError("cursor cycle must stop before another request")


def schema_checks(fn):
    require(fn({"id": "001", "amount": "12.34"}, {"amount_unit": "major"}) == {"id": "001", "amount_minor": 1234}, 'contract check failed')
    require(fn({"id": "1", "amount": 1234}, {"amount_unit": "minor"}) == {"id": "1", "amount_minor": 1234}, 'contract check failed')
    require(fn({"id": "refund", "amount": "-0.01"}, {"amount_unit": "major"})["amount_minor"] == -1, 'contract check failed')
    for amount in ["0.001", "NaN", "Infinity"]:
        try:
            fn({"id": "001", "amount": amount}, {"amount_unit": "major"})
        except ContractError:
            pass
        else:
            raise AssertionError("invalid precision or nonfinite amount must fail")
    try:
        fn({"id": "001", "amount": 1200}, {})
    except ContractError as exc:
        require(str(exc) == "amount unit needs confirmation", 'contract check failed')
    else:
        raise AssertionError("missing unit must not be guessed")


def auth_checks(fn):
    for seed in (3, 19, 71):
        for failure in ("expired", "success", "forbidden", "server_error", "other_401"):
            api = AuthAPI(failure, seed=seed)
            expected = dict(api.success if failure == "expired" else api.failure_response)
            result = fn(api)
            require(result == expected, f"response must be preserved for {failure}, seed {seed}")
            refresh_expected = failure == "expired"
            require((api.requests, api.refreshes) == ((2, 1) if refresh_expected else (1, 0)),
                    f"only expired-token 401 permits refresh: {failure}")
            expected_tokens = ["fixture-expired"] + ([api.fresh_token] if refresh_expected else [])
            require(api.tokens == expected_tokens, "use the actual replacement token")
        invalid = AuthAPI(refresh_valid=False, seed=seed)
        expected = dict(invalid.failure_response)
        require(fn(invalid) == expected, "failed replacement response must be preserved")
        require((invalid.requests, invalid.refreshes) == (2, 1),
                "invalid replacement must stop without a retry loop")
        require(invalid.tokens == ["fixture-expired", invalid.fresh_token],
                "use the actual replacement token even when invalid")


CASES = {
    "pagination": (pagination_bug, pagination_reference, pagination_checks),
    "schema": (schema_bug, schema_reference, schema_checks),
    "auth": (auth_bug, auth_reference, auth_checks),
}


def evaluate(fn, checks):
    try:
        checks(fn)
        return {"passed": True}
    except Exception as exc:
        return {"passed": False, "failure_type": type(exc).__name__, "detail": str(exc)}


def report():
    cases = []
    for name, (broken, reference, checks) in CASES.items():
        cases.append({"case": name, "baseline": evaluate(broken, checks),
                      "reviewed_reference": evaluate(reference, checks)})
    return {
        "scope": "original fictional local fixtures; reviewed reference repairs, not autonomous repairs",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "live_provider_calls": 0,
        "untrusted_code_execution": False,
        "cases": cases,
        "milestone_passed": all(not c["baseline"]["passed"] and c["reviewed_reference"]["passed"] for c in cases),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("report.json"))
    args = parser.parse_args()
    result = report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["milestone_passed"] else 1)


if __name__ == "__main__":
    main()
