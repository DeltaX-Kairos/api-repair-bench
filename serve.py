"""Local review desk. Read-only; no credential, inference, upload or execution route."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from evaluation import summarize

ROOT = Path(__file__).resolve().parent
ASSETS = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/styles.css': ('styles.css', 'text/css'), '/app.js': ('app.js', 'text/javascript'),
          '/demo-data.js': ('demo-data.js', 'text/javascript')}


def evidence():
    receipts = [json.loads(p.read_text()) for p in sorted((ROOT / 'output/live').glob('*.json'))]
    result = summarize(receipts)
    result['uncertain_calls'] = sum(r.get('status') == 'uncertain' for r in receipts)
    result['pending_calls'] = sum('status' not in r for r in receipts)
    result['provider_calls_triggered_by_view'] = 0
    return result


def run_details():
    receipts = [json.loads(p.read_text()) for p in (ROOT / 'output/live').glob('*.json')]
    runs = []
    for r in sorted(receipts, key=lambda x: x.get('started_at_unix', 0), reverse=True):
        proposal = r.get('proposal', {})
        valid = proposal.get('structurally_valid') is True
        sandbox = r.get('sandbox', {})
        runs.append({'run_id': r['run_id'], 'case': r['case'], 'status': r.get('status', 'pending'),
                     'model_completed': r.get('model', {}).get('completed') is True,
                     'finish_reason': r.get('finish_reason'),
                     'source': proposal.get('source') if valid else None,
                     'explanation': proposal.get('explanation') if valid else None,
                     'review_findings': r.get('static_review', {}).get('findings', []),
                     'remote_completed': sandbox.get('completed') is True,
                     'observations_match': sandbox.get('observations_match')})
    return {'runs': runs}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlsplit(self.path).path
        if route in ('/api/evidence', '/api/runs'):
            try:
                body = json.dumps(evidence() if route == '/api/evidence' else run_details()).encode()
            except (ValueError, OSError):
                self.send_error(503, 'Evidence unavailable'); return
            kind = 'application/json'
        elif route == '/api/review-data':
            body = (ROOT / 'ui/demo-data.json').read_bytes(); kind = 'application/json'
        elif route in ASSETS:
            name, kind = ASSETS[route]
            body = (ROOT / 'ui' / name).read_bytes()
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('Repair desk available at http://127.0.0.1:' + str(args.port), flush=True)
    server.serve_forever()
