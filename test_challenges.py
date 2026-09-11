import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path

import bench
import challenges


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.request = challenges.challenge("pagination")
        self.proposal = {"challenge_sha256": self.request["challenge_sha256"],
                         "decision": "patch", "source": self.request["candidate_source"],
                         "explanation": "Test structural validation, not repair correctness."}

    def test_structural_pass_is_not_correctness(self):
        result = challenges.validate_proposal(self.proposal, self.request)
        self.assertEqual(result["status"], "awaiting_remote_sandbox")
        self.assertFalse(result["executed"])
        self.assertFalse(bench.evaluate(bench.pagination_bug, bench.pagination_checks)["passed"])

    def test_challenge_contains_no_reference_or_regression_source(self):
        with tempfile.TemporaryDirectory() as directory:
            challenges.export(Path(directory))
            for name, (_, reference, checks) in bench.CASES.items():
                data = (Path(directory) / (name + ".json")).read_text()
                self.assertNotIn(inspect.getsource(reference), json.loads(data).values())
                self.assertNotIn(reference.__name__, data)
                self.assertNotIn(checks.__name__, data)

    def test_stale_and_tampered_requests(self):
        proposal = {**self.proposal, "challenge_sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "different challenge"):
            challenges.validate_proposal(proposal, self.request)
        request = copy.deepcopy(self.request)
        request["contract"] = "replace contract"
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            challenges.validate_proposal(self.proposal, request)

    def test_clarification(self):
        proposal = {**self.proposal, "decision": "clarify", "source": "",
                    "explanation": "Which unit does the amount use?"}
        self.assertEqual(challenges.validate_proposal(proposal, self.request)["status"], "needs_clarification")

    def test_code_is_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "must-not-exist"
            proposal = {**self.proposal, "source": f'def pagination_bug(api):\n    open({str(target)!r}, "w").write("bad")\n'}
            result = challenges.validate_proposal(proposal, self.request)
            self.assertFalse(result["executed"])
            self.assertFalse(target.exists())

    def test_reject_wrong_shape_and_top_level_code(self):
        for source in ["print('bad')", "def changed(api):\n    return []", "@print\ndef pagination_bug(api):\n    return []", "def pagination_bug(api, other):\n    return []"]:
            with self.assertRaises(ValueError):
                challenges.validate_proposal({**self.proposal, "source": source}, self.request)
        with self.assertRaises(ValueError):
            challenges.validate_proposal({**self.proposal, "extra": "ignored?"}, self.request)


if __name__ == "__main__":
    unittest.main()
