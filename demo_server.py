"""Serve only bundled public replay assets, without credentials or paid actions."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / 'demo'
ASSETS = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/styles.css': ('styles.css', 'text/css'), '/app.js': ('app.js', 'text/javascript'),
          '/demo-data.js': ('demo-data.js', 'text/javascript'),
          '/recorded-data.js': ('recorded-data.js', 'text/javascript'),
          '/recorded-review.js': ('recorded-review.js', 'text/javascript'),
          '/evidence.json': ('evidence.json', 'application/json'), '/runs.json': ('runs.json', 'application/json')}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlsplit(self.path).path
        if route not in ASSETS:
            self.send_error(404)
            return
        name, kind = ASSETS[route]
        body = (ROOT / name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(port=18767):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('Recorded review available at http://127.0.0.1:' + str(server.server_port), flush=True)
    server.serve_forever()
