import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pipeline
from challenges import challenge


class PipelineTests(unittest.TestCase):
    def test_correction_sends_actual_error_without_oracle_and_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run_id = 'e' * 32
            receipt = {'case': 'schema', 'stage': 'baseline', 'status': 'repair_failed',
                       'sandbox_result': {'reason': 'mismatch', 'mismatches': ['major'],
                           'exit_code': 0, 'expected_observations': 'PRIVATE ORACLE',
                           'diagnostics': [{'id': 'major', 'exception_type': 'AttributeError',
                               'message': 'Decimal has no attribute is_integer',
                               'expected': 'PRIVATE ORACLE'}]}}
            (root / (run_id + '.json')).write_text(json.dumps(receipt))
            with patch.object(pipeline, 'RECEIPTS', root), patch.object(pipeline, 'run_case') as run:
                pipeline.propose_correction(run_id, 'synthetic')
                feedback = run.call_args.kwargs['correction']['feedback']
                self.assertIn('is_integer', feedback)
                self.assertNotIn('PRIVATE ORACLE', feedback)
                self.assertEqual(json.loads(feedback)['diagnostic_provenance'], 'untrusted candidate runtime')
                with self.assertRaises(FileExistsError):
                    pipeline.propose_correction(run_id, 'synthetic')
                self.assertEqual(run.call_count, 1)

    def test_feedback_remains_valid_bounded_json_with_large_unicode_errors(self):
        result = {'reason': '\u2603' * 3000,
                  'mismatches': [str(i) for i in range(30)],
                  'diagnostics': [{'id': str(i), 'exception_type': '\u2603' * 1000,
                                   'message': '\u2603' * 3000} for i in range(30)]}
        feedback = pipeline.correction_feedback(result)
        self.assertLessEqual(len(feedback), 4000)
        self.assertIsInstance(json.loads(feedback), dict)

    def test_ambiguous_remote_cannot_be_resent_or_corrected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run_id = 'a' * 32
            request = challenge('auth')
            proposal = {'challenge_sha256': request['challenge_sha256'], 'decision': 'patch',
                        'source': request['candidate_source'], 'explanation': 'Original fictional proposal'}
            receipt = {'run_id': run_id, 'case': 'auth', 'stage': 'baseline',
                       'status': 'awaiting_remote_sandbox', 'proposal': proposal}
            path = root / (run_id + '.json'); path.write_text(json.dumps(receipt))
            calls = []
            def uncertain(bundle, key):
                calls.append(bundle)
                return {'status': 'unverified_remote_attempt', 'observations_match': False}
            with patch.object(pipeline, 'RECEIPTS', root):
                result = pipeline.verify_saved(run_id, 'synthetic', executor=uncertain)
                self.assertFalse(result['sandbox']['completed'])
                self.assertIsNone(result['sandbox']['observations_match'])
                with self.assertRaises(ValueError): pipeline.verify_saved(run_id, 'synthetic', executor=uncertain)
                with self.assertRaises(ValueError): pipeline.propose_correction(run_id, 'synthetic')
            self.assertEqual(len(calls), 1)

    def test_crash_marker_prevents_duplicate_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run_id = 'b' * 32; request = challenge('auth')
            receipt = {'run_id': run_id, 'case': 'auth', 'status': 'awaiting_remote_sandbox',
                       'proposal': {'challenge_sha256': request['challenge_sha256'], 'decision': 'patch',
                                    'source': request['candidate_source'], 'explanation': 'synthetic'}}
            (root / (run_id + '.json')).write_text(json.dumps(receipt))
            (root / (run_id + '.sandbox-reserved')).write_text('reserved')
            with patch.object(pipeline, 'RECEIPTS', root):
                with self.assertRaises(FileExistsError):
                    pipeline.verify_saved(run_id, 'synthetic', executor=lambda *_: self.fail('duplicate dispatch'))


class ReverificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run_id = 'c' * 32
        self.path = self.root / (self.run_id + '.json')
        request = challenge('auth')
        self.receipt = {
            'run_id': self.run_id, 'case': 'auth', 'stage': 'baseline',
            'status': 'sandbox_unverified', 'sandbox_dispatch_reserved': True,
            'proposal': {'challenge_sha256': request['challenge_sha256'], 'decision': 'patch',
                         'source': request['candidate_source'], 'explanation': 'synthetic'},
            'sandbox': {'completed': False, 'process_completed': True, 'observations_match': None},
            'sandbox_result': {'status': 'completed', 'remote_attempted': True,
                               'exit_code': 0.0, 'observations_match': None},
            'reconciliation_history': [{'reason': 'Adapter error; output unavailable',
                                        'original_result': {'status': 'completed'}}]}
        self.path.with_suffix('.sandbox-reserved').write_text('original marker')
        self.patcher = patch.object(pipeline, 'RECEIPTS', self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write(self):
        self.path.write_text(json.dumps(self.receipt))

    def test_completed_reconciled_run_can_receive_one_fresh_verification(self):
        self.write()
        calls = []
        def executor(bundle, key):
            calls.append(bundle)
            self.assertTrue(json.loads(self.path.read_text())['sandbox_reverification_reserved'])
            return {'status': 'completed', 'observations_match': False,
                    'checks': [{'id': 'expired_token', 'passed': False}]}
        result = pipeline.reverify_saved(self.run_id, 'synthetic', executor)
        self.assertEqual(result['status'], 'repair_failed')
        self.assertEqual(result['checks'], [{'id': 'expired_token', 'passed': False}])
        self.assertEqual(result['sandbox_verification_history'][0]['sandbox_result'],
                         self.receipt['sandbox_result'])
        self.assertEqual(result['proposal'], self.receipt['proposal'])
        self.assertEqual(result['reconciliation_history'], self.receipt['reconciliation_history'])
        self.assertEqual(self.path.with_suffix('.sandbox-reserved').read_text(), 'original marker')
        with self.assertRaises(ValueError):
            pipeline.reverify_saved(self.run_id, 'synthetic', executor)
        self.assertEqual(len(calls), 1)

    def test_unknown_outcomes_and_unreconciled_runs_cannot_be_retried(self):
        import copy
        original = copy.deepcopy(self.receipt)
        for mutation in (
            lambda r: r['sandbox'].update(process_completed=False),
            lambda r: r['sandbox_result'].update(status='unverified_remote_attempt'),
            lambda r: r['sandbox_result'].update(exit_code=None),
            lambda r: r.update(reconciliation_history=[]),
            lambda r: r.update(run_id='d' * 32),
        ):
            self.receipt = copy.deepcopy(original)
            mutation(self.receipt)
            self.write()
            with self.assertRaises(ValueError):
                pipeline.reverify_saved(self.run_id, 'synthetic', lambda *_: self.fail('dispatch'))

    def test_crashed_fresh_attempt_preserves_history_and_blocks_another_dispatch(self):
        self.write()
        def crash(*_):
            raise RuntimeError('simulated lost remote response')
        with self.assertRaises(RuntimeError):
            pipeline.reverify_saved(self.run_id, 'synthetic', crash)
        result = json.loads(self.path.read_text())
        self.assertEqual(len(result['sandbox_verification_history']), 1)
        self.assertTrue(self.path.with_suffix('.sandbox-reverification-reserved').is_file())
        with self.assertRaises(ValueError):
            pipeline.reverify_saved(self.run_id, 'synthetic', lambda *_: self.fail('dispatch'))

    def test_fresh_unknown_response_does_not_enable_model_correction(self):
        self.write()
        result = pipeline.reverify_saved(self.run_id, 'synthetic', lambda *_: {
            'status': 'unverified_remote_attempt', 'observations_match': False})
        self.assertEqual(result['status'], 'sandbox_unverified')
        self.assertIsNone(result['sandbox']['observations_match'])
        with self.assertRaises(ValueError):
            pipeline.propose_correction(self.run_id, 'synthetic')


if __name__ == '__main__': unittest.main()
