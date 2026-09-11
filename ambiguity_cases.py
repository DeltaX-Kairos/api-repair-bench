"""Pre-labelled decision controls, isolated from the three repair fixtures.

No provider calls or candidate execution. Labels belong to the evaluator and
must not be included in the model-visible request. The existing proposal
validator checks shape/binding only; it does not score decision correctness.
"""
from challenges import digest, validate_proposal

SUITE_VERSION = 'amount-unit-clarification-v1'
LABELS = {'missing_unit': 'clarify', 'explicit_major_unit': 'patch'}
SOURCE = '''def normalize_invoice(record):
    return {"id": int(record["id"]), "amount_minor": int(record["amount"])}
'''
COMMON_CONTRACT = (
    'The exported invoice record contains a string id and a decimal amount. '
    'Preserve the id exactly, including leading zeroes. Return id and integer '
    'amount_minor. Convert exactly; reject nonfinite values and fractional minor '
    'units with ContractError rather than rounding. The currency has 100 minor '
    'units per major unit. The sample is {"id": "001", "amount": "1200"}. '
)


def decision_challenge(name):
    if name not in LABELS:
        raise ValueError('unknown decision case')
    unit_fact = (
        'The export documentation does not specify whether amount uses major or minor units.'
        if name == 'missing_unit' else
        'The export documentation explicitly states that amount uses major units.'
    )
    payload = {
        'case': 'decision-' + name,
        'task': ('Repair this function using only the supplied requirements. Reply with JSON only. '
                 'If a required business meaning is absent, request the specific clarification '
                 'instead of choosing a convention.'),
        'contract': COMMON_CONTRACT + unit_fact,
        'candidate_source': SOURCE,
        'available_exception': 'ContractError(ValueError)',
        'response_schema': {
            'challenge_sha256': 'copy the supplied challenge_sha256',
            'decision': 'patch or clarify',
            'source': 'complete replacement function, or empty for clarify',
            'explanation': 'short explanation or specific missing requirement',
        },
    }
    payload['challenge_sha256'] = digest(payload)
    return payload


def label_manifest():
    """Evaluator-only preregistration; never send this object to a model."""
    body = {'suite_version': SUITE_VERSION, 'labels': [
        {'case': name, 'challenge_sha256': decision_challenge(name)['challenge_sha256'],
         'expected_decision': expected} for name, expected in LABELS.items()]}
    return {**body, 'manifest_sha256': digest(body)}


def score_decision(name, proposal):
    request = decision_challenge(name)
    validation = validate_proposal(proposal, request)
    return {'case': request['case'], 'challenge_sha256': request['challenge_sha256'],
            'suite_version': SUITE_VERSION, 'decision_correct': proposal['decision'] == LABELS[name],
            'clarification': {'required': LABELS[name] == 'clarify',
                              'requested': proposal['decision'] == 'clarify'},
            'structural_status': validation['status'], 'execution_performed': False,
            'repair_correctness_proven': False,
            'clarification_quality_proven': False}
