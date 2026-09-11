import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import provider_client as client
from challenges import challenge
from inference_request import MODEL


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(client, 'LEDGER', root / 'budget.sqlite3'),
                        patch.object(client, 'RECEIPTS', root / 'receipts')]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def test_length_limited_completion_is_not_a_valid_repair(self):
        calls = []
        def transport(url, key, payload=None):
            calls.append(payload)
            if payload is None: return {'data': [{'id': MODEL}]}
            return {'id': 'synthetic', 'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}
        result = client.run_case('auth', 'x' * 40, transport)
        self.assertEqual(len(calls), 2)
        self.assertTrue(result['model']['completed'])
        self.assertFalse(result['proposal']['structurally_valid'])
        self.assertEqual(result['status'], 'proposal_rejected')

    def test_timeout_is_uncertain_and_never_retried(self):
        sends = []
        def transport(url, key, payload=None):
            if payload is None: return {'data': [{'id': MODEL}]}
            sends.append(1); raise TimeoutError('private error details')
        result = client.run_case('auth', 'x' * 40, transport)
        self.assertEqual(sends, [1])
        self.assertEqual(result['status'], 'uncertain')
        self.assertNotIn('private error details', json.dumps(result))
        self.assertEqual(result['reservation']['remaining_micro_usd'], __import__('budget_guard').TEST_LIMIT_MICRO_USD - __import__('budget_guard').ATTEMPT_RESERVATION_MICRO_USD)

    def test_correction_rejects_unverified_failure_before_network(self):
        with self.assertRaises(ValueError):
            client.run_case('auth', 'x' * 40, lambda *_: self.fail('unexpected call'),
                            correction={'parent_receipt': {'case': 'auth'}, 'feedback': 'timeout'})

    def test_redirects_and_unapproved_destinations_rejected(self):
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))
        with self.assertRaises(ValueError): client.request_json('https://example.com', 'x' * 40)


if __name__ == '__main__': unittest.main()
