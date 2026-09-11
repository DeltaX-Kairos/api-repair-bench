import copy
import hashlib
import json
import os
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from types import SimpleNamespace

from sandbox_bundle import REMOTE_RUNNER
from sandbox_client import run_bundle, OUTPUT_BYTES, BoundedOutput


def bundle():
    binding = dict(run_id="test", challenge_sha256="challenge", proposal_sha256="proposal")
    files = {"candidate.py": "raise RuntimeError('MUST NOT EXECUTE LOCALLY')", "runner.py": REMOTE_RUNNER,
             "job.json": json.dumps(binding)}
    return {"upload_files": files, "command": ["python3", "runner.py"],
            "private_comparison_plan": {**binding, "expected_observations": [],
                "file_sha256": {k: hashlib.sha256(v.encode()).hexdigest() for k,v in files.items()}}}


class Fake:
    def __init__(self, output, *, truncated=None, exit_code=0, fail=False):
        self.output, self.truncated, self.exit_code, self.fail = output, truncated or {}, exit_code, fail
        self.calls = []
        self.images = self
    @contextmanager
    def factory(self, key):
        self.key = key
        yield self
    def use(self, image):
        self.image = image
        return self
    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self
    def wait(self):
        if self.fail:
            raise RuntimeError(self.key)
        self.calls[-1]["stdout"].write(self.output)
        return SimpleNamespace(result=SimpleNamespace(truncated=self.truncated, exit_code=self.exit_code))


class AdapterTests(unittest.TestCase):
    def test_only_bundle_bytes_sent_and_external_comparison(self):
        b = bundle()
        output = {k:b['private_comparison_plan'][k] for k in ('run_id','challenge_sha256','proposal_sha256')}
        output['observations'] = []
        fake = Fake(json.dumps(output))
        r = run_bundle(b, 'transport-only-secret', client_factory=fake.factory)
        self.assertTrue(r['observations_match'])
        self.assertEqual(r['stdout'], json.dumps(output))
        self.assertEqual(r['stdout_sha256'], hashlib.sha256(r['stdout'].encode()).hexdigest())
        self.assertFalse(r['trusted_execution_proven'])
        call = fake.calls[0]
        self.assertEqual(set(call['files']), {'/tmp/candidate.py','/tmp/runner.py','/tmp/job.json'})
        self.assertTrue(all(type(v) is bytes for v in call['files'].values()))
        self.assertNotIn(b'expected_observations', b''.join(call['files'].values()))
        self.assertEqual(call['env'], {})
        self.assertTrue(call['disposable'])
        self.assertFalse(call['preserve_env'])
        self.assertEqual(call['timeout'],20)
        self.assertEqual(call['truncate_output_at'],OUTPUT_BYTES)
    def test_provider_numeric_exit_codes(self):
        b = bundle()
        output = {k:b["private_comparison_plan"][k] for k in ("run_id", "challenge_sha256", "proposal_sha256")}
        output["observations"] = []
        for code, expected in ((0.0, True), (1.0, False), (0.5, False), (False, False), ("0", False), (float("nan"), False), (float("inf"), False)):
            with self.subTest(code=code):
                fake = Fake(json.dumps(output), exit_code=code)
                result = run_bundle(b, "secret", client_factory=fake.factory)
                self.assertEqual(result["observations_match"], expected)
                self.assertEqual(len(fake.calls), 1)

    def test_bad_uploads_fail_before_client(self):
        for change in ('extra','tamper','path','command','key'):
            b = bundle()
            if change == 'extra': b['upload_files']['plan.json'] = 'private'
            if change == 'tamper': b['upload_files']['candidate.py'] += '# changed'
            if change == 'path': b['upload_files']['candidate.py'] = object()
            if change == 'command': b['command'] = ['sh','-c','oops']
            if change == 'key': b['upload_files']['candidate.py'] = 'transport-only-secret'
            fake = Fake('')
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_bundle(b,'transport-only-secret',client_factory=fake.factory)
            self.assertFalse(fake.calls)
    def test_failure_no_retry_and_no_secret_leak(self):
        fake = Fake('',fail=True)
        r = run_bundle(bundle(),'transport-only-secret',client_factory=fake.factory)
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(r['status'],'unverified_remote_attempt')
        self.assertNotIn('transport-only-secret',json.dumps(r))
    def test_truncation_and_overflow_fail_closed(self):
        for fake in (Fake('{}',truncated={'stdout':True}),Fake(b'x'*(OUTPUT_BYTES+1))):
            r = run_bundle(bundle(),'secret',client_factory=fake.factory)
            self.assertEqual(r['status'],'rejected_output')
            self.assertFalse(r['observations_match'])
    def test_nonzero_and_forged_binding_fail(self):
        for fake in (Fake('{}',exit_code=1),Fake('{"run_id":"wrong"}')):
            self.assertFalse(run_bundle(bundle(),'secret',client_factory=fake.factory)['observations_match'])
    def test_run_preparation_exception_is_uncertain(self):
        class DispatchFailure(Fake):
            def run(self, **kwargs):
                self.calls.append(kwargs)
                raise RuntimeError("ambiguous dispatch")
        fake = DispatchFailure('')
        r = run_bundle(bundle(),'secret',client_factory=fake.factory)
        self.assertEqual(r['status'],'unverified_remote_attempt')
        self.assertEqual(len(fake.calls),1)
    def test_missing_dependency_is_unavailable(self):
        @contextmanager
        def missing(key):
            raise ImportError('private-path')
            yield
        r = run_bundle(bundle(),'secret',client_factory=missing)
        self.assertEqual(r['status'],'unavailable')
        self.assertEqual(r['reason'],'SDK dependency unavailable')
        self.assertFalse(r['remote_attempted'])
    def test_installed_sdk_constructor_without_network(self):
        try:
            import contree_sdk
            import httpx
        except ImportError:
            self.skipTest('optional SDK not installed')
        from sandbox_client import sdk_client, ENDPOINT
        from contree_client.base import RequestSpec
        with patch.dict(os.environ, {'REPAIR_BENCH_PROJECT_ID':'test-project', 'NEBIUS_PROJECT_ID':'wrong-project', 'NEBIUS_API_KEY':'wrong-key'}), patch.object(httpx.Client, 'send', side_effect=AssertionError('network forbidden')) as send:
            with sdk_client('x' * 40) as client:
                self.assertEqual(client.api.project,"test-project")
                self.assertEqual(client.api.base_url,ENDPOINT)
                self.assertEqual(client.api.token,"x" * 40)
                headers = dict(client.api.build_headers(RequestSpec("GET","/images")))
                self.assertEqual(headers["Project"],"test-project")
                self.assertEqual(headers["Authorization"],"Bearer " + "x" * 40)
                self.assertNotIn("wrong-project", str(headers))
                self.assertEqual(client.api.retry.max_attempts,1)
                self.assertEqual(client.operation_run_timeout,20)
                self.assertEqual(client.default_truncate_output_at,OUTPUT_BYTES)
            send.assert_not_called()
    def test_incompatible_sdk_fails_before_transport(self):
        try:
            import contree_sdk
        except ImportError:
            self.skipTest('optional SDK not installed')
        from sandbox_client import sdk_client, SDKCompatibilityError
        with patch.dict(os.environ, {'REPAIR_BENCH_PROJECT_ID':'test-project'}), patch.object(contree_sdk, 'ContreeSync', lambda config=None, *, token=None: None):
            with self.assertRaises(SDKCompatibilityError):
                with sdk_client('x' * 40):
                    self.fail('incompatible client constructed')
    def test_sink_memory_cap(self):
        sink = BoundedOutput(10)
        sink.write(b'x'*100)
        sink.write(b'y'*100)
        self.assertEqual(len(sink.data),10)
        self.assertTrue(sink.overflow)

if __name__ == '__main__':
    unittest.main()
