"""One proposal and one isolated verification; corrections require new explicit calls."""
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from challenges import challenge
from provider_client import RECEIPTS, run_case
from sandbox_bundle import make_bundle
from sandbox_client import execute_bundle


def _save(path, receipt):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(receipt, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _record_result(receipt, result):
    receipt['sandbox_result'] = result
    if 'checks' in result:
        receipt['checks'] = deepcopy(result['checks'])
    completed = result.get('status') == 'completed'
    match = result.get('observations_match') if completed else None
    # A completed process without a comparison verdict is still unverified.
    evaluated = completed and type(match) is bool
    receipt['sandbox'] = {'completed': evaluated,
                          'observations_match': match if evaluated else None,
                          'trusted_execution_proven': False}
    receipt['status'] = ('review_ready' if match else 'repair_failed') if evaluated else 'sandbox_unverified'


def verify_saved(run_id, api_key, executor=execute_bundle):
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('invalid run ID')
    path = RECEIPTS / (run_id + '.json')
    receipt = json.loads(path.read_text())
    if receipt.get('status') != 'awaiting_remote_sandbox':
        raise ValueError('run has no untested valid proposal')
    if receipt.get('sandbox_dispatch_reserved'):
        raise ValueError('sandbox attempt already reserved; reconcile before retry')
    proposal = {k: receipt['proposal'][k] for k in ('challenge_sha256', 'decision', 'source', 'explanation')}
    bundle = make_bundle(proposal, receipt['challenge_snapshot'] if receipt['case'] == 'custom' else challenge(receipt['case']), run_id=run_id)
    # Exclusive marker survives crashes and prevents duplicate remote dispatch.
    marker = path.with_suffix('.sandbox-reserved')
    with marker.open('x') as f:
        f.write('Single sandbox attempt reserved. Never remove automatically.\n')
    os.chmod(marker, 0o600)
    result = executor(bundle, api_key)
    receipt['sandbox_dispatch_reserved'] = True
    _record_result(receipt, result)
    _save(path, receipt)
    return receipt


def reverify_saved(run_id, api_key, executor=execute_bundle):
    """Explicit one-time fresh execution after a reconciled, completed adapter error.

    Never used as an automatic retry. Unknown remote outcomes are ineligible.
    The original dispatch marker and all prior evidence remain intact.
    """
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('invalid run ID')
    path = RECEIPTS / (run_id + '.json')
    receipt = json.loads(path.read_text())
    prior = receipt.get('sandbox_result', {})
    sandbox = receipt.get('sandbox', {})
    exit_code = prior.get('exit_code')
    reconciled = any(
        entry.get('original_result', {}).get('status') == 'completed'
        and 'adapter' in str(entry.get('reason', '')).lower()
        for entry in receipt.get('reconciliation_history', [])
    )
    if not (receipt.get('run_id') == run_id
            and receipt.get('status') == 'sandbox_unverified'
            and receipt.get('sandbox_dispatch_reserved') is True
            and sandbox.get('process_completed') is True
            and sandbox.get('completed') is False
            and sandbox.get('observations_match') is None
            and prior.get('status') == 'completed'
            and prior.get('remote_attempted') is True
            and prior.get('observations_match') is None
            and type(exit_code) in (int, float)
            and math.isfinite(exit_code) and exit_code == 0
            and reconciled):
        raise ValueError('fresh verification requires a reconciled completed adapter error')
    if receipt.get('sandbox_reverification_reserved'):
        raise ValueError('fresh verification already reserved; no automatic retry')
    if not path.with_suffix('.sandbox-reserved').is_file():
        raise ValueError('original sandbox reservation missing')
    proposal = {k: receipt['proposal'][k] for k in ('challenge_sha256', 'decision', 'source', 'explanation')}
    bundle = make_bundle(proposal, receipt['challenge_snapshot'] if receipt['case'] == 'custom' else challenge(receipt['case']), run_id=run_id)
    marker = path.with_suffix('.sandbox-reverification-reserved')
    with marker.open('x') as f:
        f.write('Single explicit fresh sandbox verification reserved. Never remove automatically.\n')
    os.chmod(marker, 0o600)
    receipt.setdefault('sandbox_verification_history', []).append(deepcopy({
        'status': receipt['status'], 'sandbox': sandbox, 'sandbox_result': prior,
        'reason': 'Explicit fresh execution after reconciled completed adapter error; original output unavailable.'
    }))
    receipt['sandbox_reverification_reserved'] = True
    # Persist original evidence and reservation before the remote effect.
    _save(path, receipt)
    result = executor(bundle, api_key)
    _record_result(receipt, result)
    _save(path, receipt)
    return receipt


def correction_feedback(result):
    """Bounded untrusted diagnostics, with no reference or expected values."""
    feedback = {'reason': str(result.get('reason', 'External checks failed.'))[:256],
                'mismatches': [], 'diagnostics': [],
                'diagnostic_provenance': 'untrusted candidate runtime'}
    if type(result.get('exit_code')) is int and -(2**31) <= result['exit_code'] < 2**31:
        feedback['exit_code'] = result['exit_code']
    mismatches = result.get('mismatches', [])
    if isinstance(mismatches, list):
        feedback['mismatches'] = [item[:80] for item in mismatches[:16] if isinstance(item, str)]
    diagnostics = result.get('diagnostics', [])
    if isinstance(diagnostics, list):
        for item in diagnostics[:8]:
            if not isinstance(item, dict) or item.get('id') not in feedback['mismatches']:
                continue
            feedback['diagnostics'].append({key: item[key][:limit]
                for key, limit in (('id', 80), ('exception_type', 64), ('message', 160))
                if isinstance(item.get(key), str)})
    # Remove whole entries rather than truncating serialized JSON mid-string.
    while len(json.dumps(feedback)) > 4000:
        if feedback['diagnostics']:
            feedback['diagnostics'].pop()
        elif feedback['mismatches']:
            feedback['mismatches'].pop()
        else:
            feedback['reason'] = feedback['reason'][:64]
    return json.dumps(feedback)


def propose_correction(run_id, api_key):
    if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('invalid run ID')
    parent = json.loads((RECEIPTS / (run_id + '.json')).read_text())
    if parent.get('status') != 'repair_failed' or parent.get('stage') != 'baseline':
        raise ValueError('only one correction after a verified baseline failure is allowed')
    marker = RECEIPTS / (run_id + '.correction-reserved')
    with marker.open('x') as f:
        f.write('Single correction attempt reserved. Never remove automatically.\n')
    # Send only bounded mismatch description, never private oracle or reference code.
    result = parent.get('sandbox_result', {})
    feedback = correction_feedback(result)
    return run_case(parent['case'], api_key, request_override=parent.get('challenge_snapshot') if parent['case'] == 'custom' else None, correction={'parent_receipt': parent, 'feedback': feedback or 'External observations did not match the stated contract.'})
