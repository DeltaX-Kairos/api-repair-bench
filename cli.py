"""No-key recorded review by default; paid provider actions require explicit opt-in."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import warnings

CASES = ('pagination', 'schema', 'auth', 'decision-missing_unit', 'decision-explicit_major_unit')
ROOT = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    demo = commands.add_parser('demo', help='serve the recorded judging demo; no API calls')
    demo.add_argument('--port', type=int, default=18767)
    snap = commands.add_parser('challenge', help='print a model-visible challenge; no API calls')
    snap.add_argument('case', choices=CASES)
    commands.add_parser('results', help='print the bundled recorded evaluation; no API calls')
    live = commands.add_parser('run', help='one new model request, optional remote verification')
    live.add_argument('case', choices=CASES)
    live.add_argument('--verify', action='store_true')
    for name in ('verify', 'correct'):
        sub = commands.add_parser(name, help='explicit remote verification' if name == 'verify' else 'one model correction after a verified failed baseline')
        sub.add_argument('run_id')
        sub.add_argument('--allow-paid', action='store_true', required=True)
    live.add_argument('--allow-paid', action='store_true', required=True)
    args = parser.parse_args(argv)
    if args.command == 'demo':
        from demo_server import serve
        serve(args.port)
        return 0
    if args.command == 'challenge':
        from challenges import challenge
        print(json.dumps(challenge(args.case), indent=2))
        return 0
    if args.command == 'results':
        print((ROOT / 'demo/evidence.json').read_text())
        return 0
    if (args.command == 'verify' or (args.command == 'run' and args.verify)) and not os.environ.get('REPAIR_BENCH_PROJECT_ID', '').strip():
        parser.error('set REPAIR_BENCH_PROJECT_ID before requesting Sandbox execution')
    key = None
    try:
        # Do not fall back to echoing a credential when a terminal is unavailable.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('Your Nebius API key (hidden; never saved): ')
        from provider_client import run_case
        from pipeline import verify_saved, propose_correction
        if args.command == 'run':
            receipt = run_case(args.case, key)
            if args.verify and receipt.get('status') == 'awaiting_remote_sandbox' and not args.case.startswith('decision-'):
                receipt = verify_saved(receipt['run_id'], key)
        elif args.command == 'verify':
            receipt = verify_saved(args.run_id, key)
        else:
            receipt = propose_correction(args.run_id, key)
        print(json.dumps({k: receipt.get(k) for k in ('run_id', 'case', 'status', 'checks', 'sandbox')}, indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        # Provider exception bodies can contain private account details.
        print('Operation stopped (' + type(exc).__name__ + '). Inspect local receipt status before any further action. No automatic retry.', file=sys.stderr)
        return 1
    finally:
        key = None


if __name__ == '__main__':
    raise SystemExit(main())
