import json
import unittest
from ambiguity_cases import decision_challenge, label_manifest, score_decision
from challenges import challenge, validate_proposal, digest
from inference_request import prepare_request


def proposal(name, decision):
    return {'challenge_sha256': decision_challenge(name)['challenge_sha256'],
            'decision': decision,
            'source': 'def normalize_invoice(record):\n    return record\n' if decision == 'patch' else '',
            'explanation': 'Does the exported amount use major or minor currency units?' if decision == 'clarify' else 'Preserve the supplied fields.'}


class AmbiguityTests(unittest.TestCase):
    def test_controls_use_bounded_inference_without_evaluator_labels(self):
        for name in ('missing_unit', 'explicit_major_unit'):
            prepared = prepare_request('decision-' + name)
            request = decision_challenge(name)
            self.assertEqual(prepared['challenge'], request)
            self.assertEqual(json.loads(prepared['payload']['messages'][1]['content']), request)
            self.assertEqual(prepared['reservation_usd'], '0.01')
            self.assertEqual(prepared['automatic_retries'], 0)
            serialized = json.dumps(prepared['payload'])
            self.assertNotIn('expected_decision', serialized)
            self.assertNotIn('manifest_sha256', serialized)
            self.assertNotIn('decision_correct', serialized)
        with self.assertRaisesRegex(ValueError, 'unknown decision case'):
            challenge('decision-not-registered')

    def test_predeclared_labels_bound_to_requests(self):
        manifest = label_manifest()
        self.assertEqual(digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}), manifest['manifest_sha256'])
        self.assertEqual([row['expected_decision'] for row in manifest['labels']], ['clarify', 'patch'])
        for row in manifest['labels']:
            request = decision_challenge(row['case'])
            self.assertEqual(row['challenge_sha256'], request['challenge_sha256'])
            self.assertNotIn('expected_decision', request)
            self.assertNotIn('labels', request)

    def test_missing_fact_correctly_requests_clarification(self):
        p = proposal('missing_unit', 'clarify')
        self.assertEqual(validate_proposal(p, decision_challenge('missing_unit'))['status'], 'needs_clarification')
        result = score_decision('missing_unit', p)
        self.assertTrue(result['decision_correct'])
        self.assertEqual(result['clarification'], {'required': True, 'requested': True})
        self.assertFalse(result['clarification_quality_proven'])

    def test_always_ask_strategy_fails_control(self):
        result = score_decision('explicit_major_unit', proposal('explicit_major_unit', 'clarify'))
        self.assertFalse(result['decision_correct'])
        self.assertEqual(result['clarification'], {'required': False, 'requested': True})

    def test_patch_decision_does_not_prove_correct_code(self):
        result = score_decision('explicit_major_unit', proposal('explicit_major_unit', 'patch'))
        self.assertTrue(result['decision_correct'])
        self.assertFalse(result['repair_correctness_proven'])
        self.assertFalse(result['execution_performed'])

    def test_guessing_missing_unit_is_wrong(self):
        self.assertFalse(score_decision('missing_unit', proposal('missing_unit', 'patch'))['decision_correct'])

    def test_different_case_proposal_rejected(self):
        with self.assertRaises(ValueError):
            score_decision('missing_unit', proposal('explicit_major_unit', 'patch'))


if __name__ == '__main__':
    unittest.main()
