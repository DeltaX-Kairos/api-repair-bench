import contextlib
import io
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

import cli
import demo_server
from challenges import challenge
from sandbox_client import sdk_client


class PublicReleaseTests(unittest.TestCase):
    def test_frozen_challenges_match_current_generator(self):
        for case in cli.CASES:
            self.assertEqual(json.loads((cli.ROOT / 'fixtures' / (case + '.json')).read_text()), challenge(case))

    def test_no_key_commands_never_prompt(self):
        with patch('getpass.getpass', side_effect=AssertionError('must not request key')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['challenge', 'auth']), 0)
            self.assertEqual(cli.main(['results']), 0)

    def test_missing_project_fails_before_sdk(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            with sdk_client('dummy-key'):
                self.fail('missing project accepted')

    def test_paid_action_requires_explicit_flag(self):
        with patch('getpass.getpass', side_effect=AssertionError('must not request key')), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['run', 'auth'])

    def test_recorded_server_excludes_private_paths(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), demo_server.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for path in demo_server.ASSETS:
                with urlopen(base + path) as response:
                    self.assertEqual(response.status, 200)
            for path in ('/../provider_client.py', '/output/live-budget.sqlite3', '/account-status.json', '/api/run'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + path)
                self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
