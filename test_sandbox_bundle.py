"""Inspect upload files and synthetic responses only; never run candidate code."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from challenges import challenge
from sandbox_bundle import make_bundle, compare_result


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.request = challenge('pagination')
        self.proposal = dict(challenge_sha256=self.request['challenge_sha256'], decision='patch',
                             source=self.request['candidate_source'], explanation='Structural example only.')
        self.bundle = make_bundle(self.proposal, self.request, 'test-run')
        self.plan = self.bundle['private_comparison_plan']
        self.response = {key: self.plan[key] for key in ('run_id', 'challenge_sha256', 'proposal_sha256')}
        self.response['observations'] = copy.deepcopy(self.plan['expected_observations'])

    def compare(self, response, **kwargs):
        return compare_result(self.plan, json.dumps(response), exit_code=0, **kwargs)

    def test_upload_is_reference_and_oracle_free(self):
        files = self.bundle['upload_files']
        self.assertEqual(set(files), {'runner.py', 'candidate.py', 'job.json'})
        serialized = json.dumps(files)
        for excluded in ('bench.py', 'pagination_reference', 'pagination_checks', 'expected_observations', 'observations_match'):
            self.assertNotIn(excluded, serialized)
        self.assertEqual(set(json.loads(files['job.json'])), {'run_id', 'challenge_sha256', 'proposal_sha256', 'case', 'fixtures'})
        self.assertFalse(self.bundle['execution_performed'])

    def test_candidate_never_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'must-not-exist'
            malicious = {**self.proposal, 'source': f'def pagination_bug(api):\n    open({str(target)!r}, "w").write("bad")\n'}
            bundle = make_bundle(malicious, self.request)
            self.assertFalse(target.exists())
            self.assertIn(str(target), bundle['upload_files']['candidate.py'])

    def test_matching_synthetic_output_is_not_execution_proof(self):
        result = self.compare(self.response)
        self.assertTrue(result['observations_match'])
        self.assertFalse(result['trusted_execution_proven'])
        self.assertIn('self-reported', result['transcript_provenance'])

    def test_per_case_evidence_retains_passes_and_failures(self):
        self.response["observations"][0]["calls"] = []
        result = self.compare(self.response)
        self.assertFalse(result["checks"][0]["passed"])
        self.assertTrue(all(c["passed"] for c in result["checks"][1:]))
        self.assertEqual(result["mismatches"], [self.response["observations"][0]["id"]])
        self.response["run_id"] = "wrong"
        self.assertFalse(any(c["passed"] for c in self.compare(self.response)["checks"]))

    def test_wrong_values_transcript_or_binding_fail(self):
        for key in ('run_id', 'proposal_sha256', 'challenge_sha256'):
            response = copy.deepcopy(self.response)
            response[key] = 'stale'
            self.assertFalse(self.compare(response)['observations_match'])
        for mutate in (
            lambda r: r['observations'][0]['outcome']['value'].pop(),
            lambda r: r['observations'][0]['calls'].pop(),
            lambda r: r['observations'][2]['calls'].append('duplicate-fetch'),
            lambda r: r['observations'].reverse(),
            lambda r: r.update(extra='ignored?'),
        ):
            response = copy.deepcopy(self.response)
            mutate(response)
            self.assertFalse(self.compare(response)['observations_match'])

    def test_failed_remote_or_bad_json_fails_closed(self):
        for output in ('not json', '{"run_id":"a","run_id":"b"}', '{"x":NaN}', 'x' * 65537):
            self.assertFalse(compare_result(self.plan, output, exit_code=0)['observations_match'])
        for options in ({'exit_code': 1}, {'exit_code': None}, {'exit_code': False}, {'exit_code': 0, 'timed_out': True}):
            self.assertFalse(compare_result(self.plan, json.dumps(self.response), **options)['observations_match'])

    def test_runtime_exception_feedback_is_bounded_and_contains_no_oracle(self):
        self.response['observations'][0]['outcome'] = {
            'kind': 'error', 'type': 'AttributeError',
            'message': "Decimal has no attribute is_integer. " + 'x' * 1000}
        result = self.compare(self.response)
        diagnostic = result['diagnostics'][0]
        self.assertEqual(diagnostic['exception_type'], 'AttributeError')
        self.assertIn('is_integer', diagnostic['message'])
        self.assertLessEqual(len(diagnostic['message']), 160)
        self.assertEqual(set(diagnostic), {'id', 'exception_type', 'message'})
        for value in self.plan['expected_observations'][0]['outcome']['value']:
            self.assertNotIn(value, json.dumps(result['diagnostics']))
        self.assertTrue(result['checks'][1]['passed'])
        self.response['run_id'] = 'unbound'
        self.assertEqual(self.compare(self.response)['diagnostics'], [])

    def test_no_clarification_or_other_challenge_execution(self):
        with self.assertRaises(ValueError):
            make_bundle({**self.proposal, 'decision': 'clarify', 'source': ''}, self.request)
        with self.assertRaises(ValueError):
            make_bundle(self.proposal, challenge('schema'))
        other = make_bundle(self.proposal, self.request, 'different-run')
        self.assertNotEqual(other['upload_files']['job.json'], self.bundle['upload_files']['job.json'])


class AdditionalCaseTests(unittest.TestCase):
    def bundle(self, case, run='variation-a'):
        request = challenge(case)
        return make_bundle(dict(challenge_sha256=request['challenge_sha256'], decision='patch',
                                source=request['candidate_source'], explanation='Structural test only.'), request, run)

    def response(self, bundle):
        plan = bundle['private_comparison_plan']
        response = {key: plan[key] for key in ('run_id', 'challenge_sha256', 'proposal_sha256')}
        response['observations'] = copy.deepcopy(plan['expected_observations'])
        return response

    def matches(self, bundle, response):
        return compare_result(bundle['private_comparison_plan'], json.dumps(response), exit_code=0)['observations_match']

    def test_each_bundle_has_variation_and_no_reference(self):
        import ast
        for case in ('schema', 'auth'):
            bundle = self.bundle(case)
            ast.parse(bundle['upload_files']['runner.py'])  # Parsing only, never execute.
            self.assertNotEqual(bundle['upload_files']['job.json'], self.bundle(case, 'variation-b')['upload_files']['job.json'])
            for text in bundle['upload_files'].values():
                self.assertNotIn(case + '_reference', text)
                self.assertNotIn('expected_observations', text)
            self.assertTrue(self.matches(bundle, self.response(bundle)))

    def test_schema_exact_values_error_types_and_ambiguity(self):
        bundle = self.bundle('schema')
        for row, key, value in ((0, 'amount_minor', 1), (1, 'id', 'leading-zero-lost')):
            response = self.response(bundle)
            response['observations'][row]['outcome']['value'][key] = value
            self.assertFalse(self.matches(bundle, response))
        response = self.response(bundle)
        response['observations'][3]['outcome']['message'] = 'guessed unit'
        self.assertFalse(self.matches(bundle, response))
        response = self.response(bundle)
        response['observations'][4]['outcome']['message'] = 'different valid error wording'
        self.assertTrue(self.matches(bundle, response))
        response['observations'][4]['outcome']['type'] = 'ValueError'
        self.assertFalse(self.matches(bundle, response))

    def test_auth_rejects_metadata_loss_wrong_token_and_overbroad_retry(self):
        bundle = self.bundle('auth')
        response = self.response(bundle)
        del response['observations'][0]['outcome']['value']['receipt']
        self.assertFalse(self.matches(bundle, response))
        response = self.response(bundle)
        response['observations'][0]['calls'][2]['request'] = 'hardcoded-token'
        self.assertFalse(self.matches(bundle, response))
        for row in (1, 3, 4, 5):
            response = self.response(bundle)
            response['observations'][row]['calls'].append({'refresh': True})
            self.assertFalse(self.matches(bundle, response))



if __name__ == '__main__':
    unittest.main()
