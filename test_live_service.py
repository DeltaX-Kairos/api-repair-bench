import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from live_service import LiveService, LiveError, handle_live_request


def receipt(run='a'*32, status='awaiting_remote_sandbox', stage='baseline', match=None):
    return {'run_id': run, 'status': status, 'stage': stage,
            'proposal': {'source': 'def f(): return 1', 'explanation': 'synthetic'},
            'sandbox': {'completed': match is not None, 'observations_match': match},
            'checks': [{'id': 'check', 'passed': match}], 'private': 'must not leak'}


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.services = []
        self.addCleanup(self.close_services)

    def close_services(self):
        for service in self.services:
            for thread in service.threads:
                thread.join(2)
            service.close()

    def service(self, **kwargs):
        service = LiveService(self.root/'jobs', lambda: 'synthetic key', ledger=self.root/'budget.sqlite', **kwargs)
        self.services.append(service)
        return service

    def completed(self, service, job_id):
        for thread in service.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        return service.get('a'*32, job_id)

    def test_disabled_by_default_and_invalid_inputs_do_not_dispatch(self):
        service = self.service(runner=lambda *_: self.fail('dispatch'))
        with self.assertRaises(LiveError): service.start('a'*32, {'case': 'schema'})
        service.enabled = True
        for payload in ({'case': 'auth'}, {'case': 'schema', 'enabled': True}, {'source':'x'}):
            with self.assertRaises(LiveError): service.start('a'*32, payload)
        self.assertEqual(service.jobs, {})

    def test_missing_key_is_replay_only_without_reading_supplier(self):
        service = self.service(enabled=True, credential_available=False)
        service.key_supplier = lambda: self.fail('credential read')
        info = service.info()
        self.assertFalse(info['enabled'])
        self.assertTrue(info['replay_only'])
        self.assertEqual(info['reason'], 'credential_unavailable')
        with self.assertRaises(LiveError): service.start('a'*32, {'case':'schema'})

    def test_one_correction_and_final_failure_stops(self):
        calls = []
        def run(*_):
            calls.append('propose'); return receipt()
        def verify(run_id, *_):
            calls.append('verify'); return receipt(run_id, 'repair_failed', match=False)
        def correct(*_):
            calls.append('correct'); return receipt('b'*32, stage='correction')
        service = self.service(enabled=True, max_visitor_jobs=1, runner=run, verifier=verify, corrector=correct)
        job = service.start('a'*32, {'case': 'schema'})
        done = self.completed(service, job['job_id'])
        self.assertEqual(calls, ['propose', 'verify', 'correct', 'verify'])
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(len(done['receipts']), 2)
        self.assertNotIn('private', json.dumps(done))
        with self.assertRaises(LiveError): service.get('b'*32, job['job_id'])
        with self.assertRaises(LiveError): service.start('a'*32, {'case': 'schema'})

    def test_uncertain_model_result_does_not_verify_or_correct(self):
        service = self.service(enabled=True, runner=lambda *_: receipt(status='uncertain'),
                               verifier=lambda *_: self.fail('verify'), corrector=lambda *_: self.fail('correct'))
        job = service.start('a'*32, {'case': 'schema'})
        self.assertEqual(self.completed(service, job['job_id'])['status'], 'uncertain')

    def test_kill_switch_stops_before_next_remote_stage_and_persists(self):
        entered, release = threading.Event(), threading.Event()
        def run(*_):
            entered.set(); release.wait(2); return receipt()
        service = self.service(enabled=True, runner=run, verifier=lambda *_: self.fail('verify'))
        job = service.start('a'*32, {'case':'schema'})
        self.assertTrue(entered.wait(2))
        service.disable(); release.set()
        self.assertEqual(self.completed(service, job['job_id'])['status'], 'stopped')
        service.enabled = True
        self.assertFalse(service.allowed())

    def test_restart_preserves_uncertainty_and_visitor_quota(self):
        service = self.service(enabled=True, runner=lambda *_: receipt(status='uncertain'))
        job = service.start('a'*32, {'case':'schema'})
        self.completed(service, job['job_id'])
        service.close(); self.services.remove(service)
        path = self.root/'jobs'/(job['job_id']+'.json')
        data = json.loads(path.read_text()); data['status'] = 'running'; path.write_text(json.dumps(data))
        restarted = self.service(enabled=True, max_visitor_jobs=1)
        self.assertEqual(restarted.get('a'*32, job['job_id'])['status'], 'uncertain')
        with self.assertRaises(LiveError): restarted.start('a'*32, {'case':'schema'})

    def test_custom_request_follows_same_bounded_flow(self):
        received = []
        def runner(case, key, request_override=None):
            received.append((case, request_override)); return receipt(status='needs_clarification')
        service = self.service(enabled=True, runner=runner, custom_builder=lambda p: {'contract':'synthetic'})
        job = service.start('a'*32, {'source':'def f(): return 1','checks':{}})
        self.assertEqual(self.completed(service, job['job_id'])['status'], 'stopped')
        self.assertEqual(received, [('custom', {'contract':'synthetic'})])

    def test_budget_preflight_refuses_run_without_two_attempt_capacity(self):
        import sqlite3
        with sqlite3.connect(self.root/'budget.sqlite') as conn:
            conn.execute('CREATE TABLE attempts (reserved INTEGER)')
            conn.execute('INSERT INTO attempts VALUES (1000000)')
        service = self.service(enabled=True, runner=lambda *_: self.fail('dispatch'))
        with self.assertRaises(LiveError) as error:
            service.start('a'*32, {'case':'schema'})
        self.assertEqual(error.exception.code, 'allowance_exhausted')
        self.assertFalse(service.info()['enabled'])
        self.assertTrue(service.info()['replay_only'])

    def test_default_visitor_can_run_canned_and_custom_but_not_third_job(self):
        calls = []
        def runner(case, key, **kwargs):
            calls.append(case)
            return receipt(status='needs_clarification')
        service = self.service(enabled=True, runner=runner, custom_builder=lambda p: {'contract':'synthetic'})
        first = service.start('a'*32, {'case':'schema'})
        self.completed(service, first['job_id'])
        second = service.start('a'*32, {'source':'def f(): return 1', 'checks':{}})
        self.completed(service, second['job_id'])
        with self.assertRaises(LiveError) as error:
            service.start('a'*32, {'case':'schema'})
        self.assertEqual(error.exception.code, 'visitor_limit')
        self.assertEqual(calls, ['schema', 'custom'])
        self.assertEqual(len(service.jobs), 2)

    def test_global_job_limit_disables_status_and_survives_restart(self):
        service = self.service(enabled=True, max_jobs=1, runner=lambda *_: receipt(status='uncertain'))
        first = service.start('a'*32, {'case':'schema'})
        self.completed(service, first['job_id'])
        self.assertFalse(service.info()['enabled'])
        self.assertEqual(service.info()['reason'], 'session_limit')
        service.close(); self.services.remove(service)
        restarted = self.service(enabled=True, max_jobs=1)
        self.assertFalse(restarted.info()['enabled'])
        self.assertEqual(restarted.info()['limits']['jobs_remaining'], 0)
        with self.assertRaises(LiveError): restarted.start('b'*32, {'case':'schema'})


class Handler:
    def __init__(self, command='GET', path='/api/live/status', headers=None, body=b''):
        self.command, self.path = command, path
        self.headers = {'Host':'127.0.0.1:18768', **(headers or {})}
        self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
        self.response_headers = {}
    def send_response(self, status): self.status = status
    def send_header(self, name, value): self.response_headers[name] = value
    def end_headers(self): pass


class HTTPTests(unittest.TestCase):
    def test_untrusted_host_is_rejected_even_for_readonly_status(self):
        handler = Handler(headers={'Host':'attacker.example'})
        handle_live_request(handler, None, allowed_origins={'http://127.0.0.1:18768'})
        self.assertEqual(handler.status, 403)

    def test_post_requires_origin_and_rejects_oversize_before_read(self):
        for headers, expected in (({}, 403),
            ({'Origin':'https://evil.example'},403),
            ({'Origin':'http://127.0.0.1:18768','Content-Type':'application/json','Content-Length':'999999'},413)):
            handler=Handler('POST','/api/live/runs',headers)
            handle_live_request(handler, None, allowed_origins={'http://127.0.0.1:18768'})
            self.assertEqual(handler.status, expected)

    def test_status_issues_private_cookie_without_cors(self):
        class Service:
            def info(self): return {'enabled':False}
        handler=Handler()
        handle_live_request(handler,Service(),allowed_origins={'http://127.0.0.1:18768'})
        self.assertEqual(handler.status,200)
        self.assertIn('HttpOnly; SameSite=Strict',handler.response_headers['Set-Cookie'])
        self.assertEqual(handler.response_headers['Cache-Control'],'no-store')
        self.assertNotIn('Access-Control-Allow-Origin',handler.response_headers)


if __name__ == '__main__': unittest.main()
