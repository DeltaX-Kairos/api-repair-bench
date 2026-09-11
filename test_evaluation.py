import copy
import unittest
import json
from pathlib import Path
from evaluation import summarize


def receipt(run='one', stage='baseline', passed=False):
    return {'schema_version': 1, 'run_id': run, 'case': 'auth', 'challenge_sha256': 'abc',
            'stage': stage, 'parent_run_id': 'one' if stage == 'correction' else None,
            'model': {'completed': True},
            'proposal': {'decision': 'patch', 'structurally_valid': True},
            'sandbox': {'completed': True, 'observations_match': passed, 'trusted_execution_proven': False},
            'checks': [{'id': 'preserves-403', 'passed': passed}]}


class EvidenceTests(unittest.TestCase):
    def test_empty_is_unknown_not_zero_success(self):
        result = summarize([])
        self.assertIsNone(result['stages']['baseline']['observed_repair_success']['rate'])
        self.assertFalse(result['cost']['complete'])
        self.assertFalse(result['trusted_execution_proven'])

    def test_proposal_without_execution_is_not_success(self):
        r = receipt(passed=True)
        r['sandbox']['completed'] = False
        result = summarize([r])
        self.assertEqual(result['completed_model_calls'], 1)
        self.assertEqual(result['stages']['baseline']['pending_or_incomplete'], 1)
        self.assertIsNone(result['stages']['baseline']['observed_repair_success']['rate'])

    def test_actual_pairs_recover_and_regress(self):
        a, b = receipt(), receipt('two', 'correction', True)
        result = summarize([a, b])
        self.assertEqual(result['paired_recovery']['rate'], 1)
        self.assertIsNone(result['paired_regression']['rate'])
        self.assertEqual(result['paired_comparisons'][0]['checks_recovered'], 1)
        a['sandbox']['observations_match'] = True
        b['sandbox']['observations_match'] = False
        self.assertEqual(summarize([a, b])['paired_regression']['rate'], 1)

    def test_missing_and_mismatched_pairs_not_comparisons(self):
        a, b = receipt(), receipt('two', 'correction', True)
        b['challenge_sha256'] = 'different'
        result = summarize([a, b])
        self.assertEqual(result['paired_comparisons'], [])
        self.assertEqual(len(result['unpaired_corrections']), 1)

    def test_missing_checks_never_claim_regression_free(self):
        a, b = receipt(), receipt('two', 'correction', True)
        b['checks'] = []
        pair = summarize([a, b])['paired_comparisons'][0]
        self.assertFalse(pair['checks_comparison_complete'])
        self.assertEqual(pair['missing_checks_after'], ['preserves-403'])

    def test_duplicate_identity_conflicts_fail(self):
        a = receipt()
        self.assertEqual(summarize([a, copy.deepcopy(a)])['unique_runs'], 1)
        b = copy.deepcopy(a)
        b['case'] = 'schema'
        with self.assertRaises(ValueError):
            summarize([a, b])

    def test_booleans_are_not_numbers_or_strings(self):
        for value in (1, 'true', None):
            a = receipt()
            a['sandbox']['observations_match'] = value
            with self.assertRaises(ValueError):
                summarize([a])

    def test_clarification_labels_are_required(self):
        a = receipt()
        self.assertEqual(summarize([a])['clarification']['labeled_decisions'], 0)
        a['clarification'] = {'required': True, 'requested': True}
        a['proposal']['decision'] = 'clarify'
        result = summarize([a])
        self.assertEqual(result['clarification']['required_request_rate']['rate'], 1)
        self.assertEqual(result['stages']['baseline']['remote_completed'], 0)

    def test_estimates_are_not_charges(self):
        a = receipt()
        a['estimated_cost_usd'] = '0.00012'
        result = summarize([a])['cost']
        self.assertEqual(result['estimated_usd'], '0.00012')
        self.assertEqual(result['runs_with_cost'], 0)
        self.assertFalse(result['complete'])
        a['cost_usd'] = 'NaN'
        with self.assertRaises(ValueError):
            summarize([a])

    def test_pending_null_observations_are_unknown(self):
        a = receipt()
        a['sandbox'].update(completed=False, observations_match=None)
        self.assertIsNone(summarize([a])['stages']['baseline']['observed_repair_success']['rate'])
        a['sandbox']['completed'] = True
        with self.assertRaises(ValueError):
            summarize([a])

    def test_receipt_trust_assertion_is_never_promoted(self):
        a = receipt(passed=True)
        a['sandbox']['trusted_execution_proven'] = True
        self.assertFalse(summarize([a])['trusted_execution_proven'])


if __name__ == '__main__':
    unittest.main()
