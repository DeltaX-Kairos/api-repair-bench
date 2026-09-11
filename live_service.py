"""Bounded asynchronous live jobs. Disabled by default; never executes candidate code locally."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

import budget_guard
import pipeline
import provider_client

MAX_BODY_BYTES = 24576
ID_PATTERN = re.compile(r'^[a-f0-9]{32}$')
TERMINAL = {'completed', 'stopped', 'uncertain', 'failed'}


class LiveError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def _public_receipt(receipt):
    result = {k: copy.deepcopy(receipt[k]) for k in
              ('run_id', 'stage', 'status', 'checks', 'sandbox', 'estimated_cost_usd') if k in receipt}
    result['proposal'] = {k: receipt.get('proposal', {}).get(k) for k in ('source', 'explanation', 'decision')}
    # Raw provider responses, headers, fixtures and private comparison plans stay private.
    return result


class LiveService:
    def __init__(self, storage, key_supplier, *, enabled=False, max_jobs=3, max_visitor_jobs=2,
                 credential_available=True, ledger=None, runner=None, verifier=None,
                 corrector=None, custom_builder=None):
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        os.chmod(self.storage, 0o700)
        self._lease = (self.storage / '.service-lock').open('a')
        os.chmod(self.storage / '.service-lock', 0o600)
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lease.close()
            raise LiveError('service_already_running', 409)
        self.key_supplier = key_supplier
        self.enabled = enabled is True
        self.credential_available = credential_available is True
        self.max_jobs = max_jobs
        self.max_visitor_jobs = max_visitor_jobs
        if type(max_jobs) is not int or type(max_visitor_jobs) is not int or min(max_jobs, max_visitor_jobs) < 1:
            self._lease.close()
            raise ValueError('job limits must be positive integers')
        self.ledger = Path(ledger) if ledger else provider_client.LEDGER
        self.runner = runner or provider_client.run_case
        self.verifier = verifier or pipeline.verify_saved
        self.corrector = corrector or pipeline.propose_correction
        self.custom_builder = custom_builder
        self.lock = threading.RLock()
        self.active = None
        self.jobs = {}
        self.threads = []
        for path in self.storage.glob('*.json'):
            job = json.loads(path.read_text())
            if job['status'] not in TERMINAL:
                job['status'] = 'uncertain'
                job['trace'].append({'stage': 'uncertain', 'message': 'Service restarted. Previous work is not retried.', 'at': time.time()})
                self._save(job)
            self.jobs[job['job_id']] = job

    def close(self):
        self.enabled = False
        for thread in self.threads:
            thread.join(timeout=0)
        if any(thread.is_alive() for thread in self.threads):
            raise LiveError('job_still_running', 409)
        self._lease.close()

    def allowed(self):
        return self.enabled and self.credential_available and not (self.storage / 'STOP').exists()

    def disable(self):
        with self.lock:
            self.enabled = False
            stop = self.storage / 'STOP'
            stop.write_text('Live execution disabled. In-flight calls may finish; no next stage will start.\n')
            os.chmod(stop, 0o600)

    def _budget(self):
        used = 0
        if self.ledger.exists():
            with sqlite3.connect('file:' + str(self.ledger) + '?mode=ro', uri=True) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempts'").fetchone():
                    used = conn.execute('SELECT COALESCE(SUM(reserved),0) FROM attempts').fetchone()[0]
        return max(0, budget_guard.TEST_LIMIT_MICRO_USD - used)

    def info(self):
        with self.lock:
            remaining = self._budget()
            reason = ('credential_unavailable' if not self.credential_available else
                      'operator_disabled' if not self.allowed() else
                      'allowance_exhausted' if remaining < 2 * budget_guard.ATTEMPT_RESERVATION_MICRO_USD else
                      'session_limit' if len(self.jobs) >= self.max_jobs else
                      'busy' if self.active is not None else 'ready')
            available = self.allowed() and reason not in ('allowance_exhausted', 'session_limit')
            return {'enabled': available, 'replay_only': not available,
                    'max_model_attempts': 2, 'max_corrections': 1, 'cases': ['schema'],
                    'busy': self.active is not None, 'remaining_reservation_usd': remaining / 1000000,
                    'reservation_is_billed_cost': False, 'reason': reason,
                    'limits': {'jobs_per_visitor': self.max_visitor_jobs, 'jobs_total': self.max_jobs,
                               'jobs_remaining': max(0, self.max_jobs - len(self.jobs))}}

    def _save(self, job):
        path = self.storage / (job['job_id'] + '.json')
        tmp = path.with_suffix('.tmp')
        with tmp.open('w') as stream:
            os.chmod(tmp, 0o600)
            json.dump(job, stream, indent=2)
            stream.write('\n')
        tmp.replace(path)

    def _emit(self, job, stage, message, receipt=None):
        with self.lock:
            job['trace'].append({'stage': stage, 'message': message, 'at': time.time()})
            if receipt is not None:
                sanitized = _public_receipt(receipt)
                existing = next((i for i, r in enumerate(job['receipts']) if r['run_id'] == sanitized['run_id']), None)
                if existing is None:
                    job['receipts'].append(sanitized)
                else:
                    job['receipts'][existing] = sanitized
            self._save(job)

    def _guard(self):
        if not self.allowed():
            raise LiveError('live_disabled', 403)

    @staticmethod
    def _owner(visitor):
        if not isinstance(visitor, str) or not ID_PATTERN.fullmatch(visitor):
            raise LiveError('invalid_visitor', 403)
        return hashlib.sha256(visitor.encode()).hexdigest()

    def start(self, visitor, payload):
        owner = self._owner(visitor)
        if not isinstance(payload, dict) or len(json.dumps(payload, allow_nan=False).encode()) > MAX_BODY_BYTES:
            raise LiveError('invalid_payload')
        request = None
        if set(payload) == {'case'} and payload['case'] == 'schema':
            case = 'schema'
        elif set(payload) == {'source', 'checks'}:
            if self.custom_builder is None:
                from custom_cases import build_custom_challenge
                builder = build_custom_challenge
            else:
                builder = self.custom_builder
            try:
                request = builder(payload)
            except (ValueError, TypeError, SyntaxError, RecursionError):
                raise LiveError('invalid_custom_case') from None
            case = 'custom'
        else:
            raise LiveError('invalid_case')
        with self.lock:
            self._guard()
            if self.active is not None:
                raise LiveError('busy', 429)
            if sum(j['owner'] == owner for j in self.jobs.values()) >= self.max_visitor_jobs:
                raise LiveError('visitor_limit', 429)
            if len(self.jobs) >= self.max_jobs:
                raise LiveError('session_limit', 429)
            if self._budget() < 2 * budget_guard.ATTEMPT_RESERVATION_MICRO_USD:
                raise LiveError('allowance_exhausted', 429)
            job = {'job_id': secrets.token_hex(16), 'owner': owner, 'case': case,
                   'status': 'queued', 'trace': [], 'receipts': [], 'created_at': time.time()}
            self.jobs[job['job_id']] = job
            self.active = job['job_id']
            self._save(job)
            thread = threading.Thread(target=self._run, args=(job, request), daemon=True)
            self.threads.append(thread)
            thread.start()
            return self.get(visitor, job['job_id'])

    def get(self, visitor, job_id):
        owner = self._owner(visitor)
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job['owner'] != owner:
                raise LiveError('not_found', 404)
            return copy.deepcopy({k: v for k, v in job.items() if k != 'owner'})

    def _run(self, job, request):
        key = None
        try:
            self._guard()
            job['status'] = 'running'
            self._emit(job, 'propose', 'Generating one proposed repair.')
            key = self.key_supplier()
            self._guard()
            receipt = (self.runner(job['case'], key, request_override=request) if request is not None
                       else self.runner(job['case'], key))
            self._emit(job, 'proposal', 'Proposal returned; checking eligibility for remote evaluation.', receipt)
            if receipt.get('status') == 'awaiting_remote_sandbox':
                self._guard()
                self._emit(job, 'verify', 'Testing the proposal in the remote sandbox.')
                receipt = self.verifier(receipt['run_id'], key)
                self._emit(job, 'verification', 'Remote verification returned.', receipt)
            if (receipt.get('status') == 'repair_failed'
                    and receipt.get('sandbox', {}).get('completed') is True
                    and receipt.get('sandbox', {}).get('observations_match') is False):
                self._guard()
                self._emit(job, 'correct', 'Requesting one correction using failed-check feedback.')
                receipt = self.corrector(receipt['run_id'], key)
                self._emit(job, 'correction', 'Correction returned. No further corrections are allowed.', receipt)
                if receipt.get('status') == 'awaiting_remote_sandbox':
                    self._guard()
                    self._emit(job, 'verify_correction', 'Testing the correction in the remote sandbox.')
                    receipt = self.verifier(receipt['run_id'], key)
                    self._emit(job, 'correction_verification', 'Correction verification returned.', receipt)
            status = receipt.get('status')
            job['status'] = ('uncertain' if status in ('uncertain', 'sandbox_unverified') else
                             'completed' if status in ('review_ready', 'repair_failed') else 'stopped')
            self._emit(job, 'stop', 'Run ended. No automatic retry or further correction will occur.')
        except LiveError as exc:
            job['status'] = 'stopped'
            self._emit(job, 'stop', 'Live execution stopped before its next stage.')
        except Exception:
            # An exception may follow a remote effect. Do not expose secret-bearing errors or retry.
            job['status'] = 'uncertain'
            self._emit(job, 'uncertain', 'Run interrupted. Inspect private receipts; no automatic retry will occur.')
        finally:
            key = None
            with self.lock:
                self.active = None


def handle_live_request(handler, service, *, allowed_origins, secure_cookie=False):
    """Mount in an HTTP handler. Return False for paths outside /api/live/."""
    path = handler.path.split('?', 1)[0]
    if not path.startswith('/api/live/'):
        return False
    cookie_header = None
    try:
        if handler.command not in ('GET', 'POST'):
            raise LiveError('method_not_allowed', 405)
        if handler.headers.get('Host') not in {urlsplit(o).netloc for o in allowed_origins}:
            raise LiveError('host_forbidden', 403)
        origin = handler.headers.get('Origin')
        if origin and origin not in allowed_origins:
            raise LiveError('origin_forbidden', 403)
        if handler.command == 'POST' and origin not in allowed_origins:
            raise LiveError('origin_required', 403)
        cookies = SimpleCookie()
        cookies.load(handler.headers.get('Cookie', ''))
        visitor = cookies['repair_visitor'].value if 'repair_visitor' in cookies else ''
        if not ID_PATTERN.fullmatch(visitor):
            visitor = secrets.token_hex(16)
            cookie_header = 'repair_visitor=' + visitor + '; HttpOnly; SameSite=Strict; Path=/api/live/; Max-Age=86400'
            if secure_cookie:
                cookie_header += '; Secure'
        if handler.command == 'GET' and path == '/api/live/status':
            payload, status = service.info(), 200
        elif handler.command == 'GET' and path.startswith('/api/live/runs/'):
            payload, status = service.get(visitor, path.rsplit('/', 1)[-1]), 200
        elif handler.command == 'POST' and path == '/api/live/runs':
            if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise LiveError('json_required', 415)
            if handler.headers.get('Transfer-Encoding'):
                raise LiveError('unsupported_transfer_encoding')
            try:
                length = int(handler.headers.get('Content-Length', ''))
            except ValueError:
                raise LiveError('content_length_required', 411) from None
            if not 0 < length <= MAX_BODY_BYTES:
                raise LiveError('payload_too_large', 413)
            raw = handler.rfile.read(length)
            if len(raw) != length:
                raise LiveError('incomplete_payload')
            try:
                data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise LiveError('invalid_json') from None
            payload, status = service.start(visitor, data), 202
        else:
            raise LiveError('not_found', 404)
    except LiveError as exc:
        payload, status = {'error': exc.code}, exc.status
    except Exception:
        payload, status = {'error': 'service_unavailable'}, 503
    encoded = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(encoded)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    if cookie_header:
        handler.send_header('Set-Cookie', cookie_header)
    handler.end_headers()
    handler.wfile.write(encoded)
    return True


def make_handler(service, *, allowed_origins, secure_cookie=False, ui_root=None):
    """Serve only the live page's three static assets and scoped JSON endpoints."""
    from http.server import BaseHTTPRequestHandler
    ui_root = Path(ui_root) if ui_root else Path(__file__).parent / 'live-ui'
    assets = {'/': ('index.html', 'text/html'), '/live': ('index.html', 'text/html'),
              '/live/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
              '/styles.css': ('styles.css', 'text/css'), '/app.js': ('app.js', 'text/javascript')}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'RepairBench'

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def do_GET(self):
            self._dispatch()

        def do_POST(self):
            self._dispatch()

        def _dispatch(self):
            if handle_live_request(self, service, allowed_origins=allowed_origins, secure_cookie=secure_cookie):
                return
            if self.headers.get('Host') not in {urlsplit(o).netloc for o in allowed_origins}:
                self.send_error(403); return
            asset = assets.get(self.path.split('?', 1)[0])
            if self.command != 'GET' or asset is None or not (ui_root / asset[0]).is_file():
                self.send_error(404); return
            body = (ui_root / asset[0]).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', asset[1] + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass  # Do not log user input, job IDs, cookies or request parameters.

    return Handler


def main():
    import argparse
    from http.server import ThreadingHTTPServer
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18768)
    parser.add_argument('--origin', action='append', required=True)
    parser.add_argument('--storage', default=str(Path(__file__).parent/'output'/'live-jobs'))
    parser.add_argument('--enabled', action='store_true', help='Explicitly enable budgeted live calls; default is replay-only.')
    parser.add_argument('--secure-cookie', action='store_true')
    parser.add_argument('--max-jobs', type=int, default=3)
    parser.add_argument('--max-visitor-jobs', type=int, default=2)
    args = parser.parse_args()
    if min(args.max_jobs, args.max_visitor_jobs) < 1:
        parser.error('job limits must be positive')
    for origin in args.origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            parser.error('origin must be an exact HTTP(S) origin without path or credentials')
    def key_supplier():
        key = os.environ.get('NEBIUS_API_KEY')
        if not key:
            raise LiveError('credential_unavailable', 503)
        return key
    service = LiveService(args.storage, key_supplier, enabled=args.enabled, max_jobs=args.max_jobs,
                          max_visitor_jobs=args.max_visitor_jobs,
                          credential_available=bool(os.environ.get('NEBIUS_API_KEY')))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service,
        allowed_origins=set(args.origin), secure_cookie=args.secure_cookie))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        service.disable()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
