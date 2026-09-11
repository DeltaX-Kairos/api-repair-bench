"""Regression checks for fixture scoring, including optimized Python execution."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import bench


def refresh_every_401(api):
    response = api.request("fixture-expired")
    if response.get("status") == 401:
        response = api.request(api.refresh())
    return response


def hardcoded_refresh_token(api):
    response = api.request("fixture-expired")
    if response.get("status") == 401 and response.get("error") == "expired_token":
        api.refresh()
        response = api.request("fixture-fresh")
    return response


def discard_response_metadata(api):
    response = bench.auth_reference(api)
    return {key: value for key, value in response.items() if key != "receipt"}


class ScoringTests(unittest.TestCase):
    def test_broken_rejected_and_reviewed_references_pass(self):
        for name, (broken, reference, checks) in bench.CASES.items():
            with self.subTest(case=name):
                self.assertFalse(bench.evaluate(broken, checks)["passed"])
                self.assertTrue(bench.evaluate(reference, checks)["passed"])

    def test_auth_rejects_overbroad_refresh(self):
        result = bench.evaluate(refresh_every_401, bench.auth_checks)
        self.assertFalse(result["passed"])
        self.assertIn("other_401", result["detail"])

    def test_auth_rejects_hardcoded_token_and_lost_metadata(self):
        for candidate in (hardcoded_refresh_token, discard_response_metadata):
            with self.subTest(candidate=candidate.__name__):
                self.assertFalse(bench.evaluate(candidate, bench.auth_checks)["passed"])

    def test_auth_fixture_varies_data_and_replacement_tokens(self):
        fixtures = [bench.AuthAPI(seed=seed) for seed in (3, 19, 71)]
        self.assertEqual(len({api.fresh_token for api in fixtures}), 3)
        self.assertEqual(len({json.dumps(api.success, sort_keys=True) for api in fixtures}), 3)

    def test_optimization_cannot_remove_scoring(self):
        result = subprocess.run(
            [sys.executable, "-B", "-O", "-c",
             "import json, bench; print(json.dumps(bench.report()))"],
            cwd=Path(bench.__file__).parent, capture_output=True, text=True,
            check=True, timeout=10,
        )
        report = json.loads(result.stdout)
        self.assertTrue(report["milestone_passed"])
        for case in report["cases"]:
            with self.subTest(case=case["case"]):
                self.assertFalse(case["baseline"]["passed"])
                self.assertTrue(case["reviewed_reference"]["passed"])


if __name__ == "__main__":
    unittest.main()
