"""Summarize saved evidence; never call providers or execute candidate source.

Receipt data is evidence supplied by the caller, not independent attestation.
No missing result is converted to a pass and no fixture reference is an AI run.
"""
import argparse
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _rate(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': numerator / denominator if denominator else None}


def _validate(receipt):
    if not isinstance(receipt, dict) or receipt.get('schema_version') != 1:
        raise ValueError('expected evidence receipt schema_version 1')
    for key in ('run_id', 'case', 'challenge_sha256'):
        if not isinstance(receipt.get(key), str) or not receipt[key].strip():
            raise ValueError('missing receipt identity: ' + key)
    if receipt.get('stage') not in ('baseline', 'correction'):
        raise ValueError('stage must be baseline or correction')
    if receipt['stage'] == 'correction' and not isinstance(receipt.get('parent_run_id'), str):
        raise ValueError('correction requires parent_run_id')
    for group, keys in (('model', ('completed',)),
                        ('proposal', ('structurally_valid',)),
                        ('sandbox', ('completed', 'observations_match', 'trusted_execution_proven')),
                        ('clarification', ('required', 'requested'))):
        obj = receipt.get(group, {})
        if not isinstance(obj, dict):
            raise ValueError(group + ' must be an object')
        for key in keys:
            if (group == 'sandbox' and key == 'observations_match'
                    and obj.get(key) is None and obj.get('completed') is not True):
                continue
            if key in obj and type(obj[key]) is not bool:
                raise ValueError(group + '.' + key + ' must be boolean')
    proposal = receipt.get('proposal', {})
    if 'decision' in proposal and proposal['decision'] not in ('patch', 'clarify'):
        raise ValueError('invalid proposal decision')
    checks = receipt.get('checks', [])
    if not isinstance(checks, list):
        raise ValueError('checks must be a list')
    ids = set()
    for check in checks:
        if (not isinstance(check, dict) or not isinstance(check.get('id'), str)
                or not check['id'] or type(check.get('passed')) is not bool
                or check['id'] in ids):
            raise ValueError('check IDs must be unique and results explicit booleans')
        ids.add(check['id'])
    for cost_key in ('cost_usd', 'estimated_cost_usd'):
        if cost_key not in receipt:
            continue
        try:
            cost = Decimal(str(receipt[cost_key]))
        except InvalidOperation as exc:
            raise ValueError('invalid cost') from exc
        if not cost.is_finite() or cost < 0:
            raise ValueError('cost must be finite and nonnegative')


def _executed(r):
    return (r.get('model', {}).get('completed') is True
            and r.get('proposal', {}).get('structurally_valid') is True
            and r.get('proposal', {}).get('decision') == 'patch'
            and r.get('sandbox', {}).get('completed') is True)


def _success(r):
    return _executed(r) and r.get('sandbox', {}).get('observations_match') is True


def summarize(receipts):
    """Compare explicit baseline/correction pairs; absence remains unknown.

    baseline means first model attempt, NOT bench.py's reviewed broken fixture.
    trusted_execution_proven is deliberately never promoted by this summarizer.
    """
    runs, duplicates = {}, 0
    for receipt in receipts:
        _validate(receipt)
        run_id = receipt['run_id']
        if run_id in runs:
            if runs[run_id] != receipt:
                raise ValueError('conflicting duplicate run_id: ' + run_id)
            duplicates += 1
        else:
            runs[run_id] = receipt
    stage_metrics = {}
    for stage in ('baseline', 'correction'):
        selected = [r for r in runs.values() if r['stage'] == stage]
        executed = [r for r in selected if _executed(r)]
        stage_metrics[stage] = {
            'recorded_attempts': len(selected), 'remote_completed': len(executed),
            'pending_or_incomplete': len(selected) - len(executed),
            'observed_repair_success': _rate(sum(_success(r) for r in executed), len(executed)),
        }
    pairs, unpaired = [], []
    for r in runs.values():
        if r['stage'] != 'correction':
            continue
        parent = runs.get(r['parent_run_id'])
        if not parent or parent['stage'] != 'baseline' or any(
                parent[k] != r[k] for k in ('case', 'challenge_sha256')):
            unpaired.append({'run_id': r['run_id'], 'reason': 'missing or mismatched baseline'})
            continue
        if not _executed(parent) or not _executed(r):
            unpaired.append({'run_id': r['run_id'], 'reason': 'incomplete paired execution'})
            continue
        before = {c['id']: c['passed'] for c in parent.get('checks', [])}
        after = {c['id']: c['passed'] for c in r.get('checks', [])}
        comparable = before.keys() & after.keys()
        pairs.append({'baseline_run_id': parent['run_id'], 'correction_run_id': r['run_id'],
                      'case': r['case'], 'baseline_observations_match': _success(parent),
                      'correction_observations_match': _success(r),
                      'recovered': not _success(parent) and _success(r),
                      'regressed': _success(parent) and not _success(r),
                      'comparable_checks': len(comparable),
                      'checks_recovered': sum(not before[k] and after[k] for k in comparable),
                      'checks_regressed': sum(before[k] and not after[k] for k in comparable),
                      'missing_checks_after': sorted(before.keys() - after.keys()),
                      'checks_comparison_complete': bool(before) and before.keys() == after.keys()})
    # Only completed model decisions with an explicit expected clarification label.
    labeled = [r for r in runs.values() if r.get('model', {}).get('completed') is True
               and r.get('proposal', {}).get('structurally_valid') is True
               and all(k in r.get('clarification', {}) for k in ('required', 'requested'))]
    needed = [r for r in labeled if r['clarification']['required']]
    unnecessary = [r for r in labeled if not r['clarification']['required']]
    costs = [Decimal(str(r['cost_usd'])) for r in runs.values() if 'cost_usd' in r]
    estimates = [Decimal(str(r['estimated_cost_usd'])) for r in runs.values() if 'estimated_cost_usd' in r]
    return {
        'schema_version': 1,
        'evidence_scope': 'caller-supplied saved receipts; original synthetic cases only',
        'trusted_execution_proven': False,
        'trust_limit': 'Matching candidate-runtime output can be spoofed; receipts are not attestation.',
        'unique_runs': len(runs), 'duplicate_receipts_ignored': duplicates,
        'completed_model_calls': sum(r.get('model', {}).get('completed') is True for r in runs.values()),
        'stages': stage_metrics, 'paired_comparisons': pairs, 'unpaired_corrections': unpaired,
        'paired_recovery': _rate(sum(p['recovered'] for p in pairs),
                                 sum(not p['baseline_observations_match'] for p in pairs)),
        'paired_regression': _rate(sum(p['regressed'] for p in pairs),
                                   sum(p['baseline_observations_match'] for p in pairs)),
        'clarification': {'labeled_decisions': len(labeled),
                          'required_request_rate': _rate(sum(r['clarification']['requested'] for r in needed), len(needed)),
                          'unnecessary_request_rate': _rate(sum(r['clarification']['requested'] for r in unnecessary), len(unnecessary))},
        'cost': {'reported_usd': str(sum(costs, Decimal(0))), 'runs_with_cost': len(costs),
                 'complete': bool(runs) and len(costs) == len(runs), 'source': 'receipt reported, not reconciled billing',
                 'estimated_usd': str(sum(estimates, Decimal(0))), 'runs_with_estimate': len(estimates)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipts', nargs='*', type=Path)
    args = parser.parse_args()
    receipts = [json.loads(path.read_text()) for path in args.receipts]
    print(json.dumps(summarize(receipts), indent=2))


if __name__ == '__main__':
    main()
