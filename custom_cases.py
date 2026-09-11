"""Bounded user-supplied JSON contracts; source is parsed but never run locally."""
import ast
import copy
import json
import re
from challenges import digest, challenge, validate_proposal


def build_custom_challenge(payload):
    if not isinstance(payload, dict) or set(payload) != {'source', 'checks'}:
        raise ValueError('Provide one source function and a JSON checks contract.')
    source, contract = payload['source'], payload['checks']
    if not isinstance(source, str) or not 1 <= len(source.encode()) <= 8000:
        raise ValueError('Function source must be between 1 and 8000 bytes.')
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError) as exc:
        raise ValueError('Source must be valid Python.') from exc
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise ValueError('Provide exactly one synchronous Python function.')
    fn = tree.body[0]
    if (fn.decorator_list or fn.returns or fn.args.defaults or any(fn.args.kw_defaults)
            or any(a.annotation for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs])
            or fn.args.vararg or fn.args.kwarg):
        raise ValueError('Use a plain function without decorators, annotations, defaults or variadic arguments.')
    if not isinstance(contract, dict) or set(contract) != {'description', 'checks'}:
        raise ValueError('Checks contract needs description and checks fields.')
    description, checks = contract['description'], contract['checks']
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 2000:
        raise ValueError('Describe intended behavior in 1–2000 characters.')
    if not isinstance(checks, list) or not 1 <= len(checks) <= 8:
        raise ValueError('Provide 1–8 JSON checks.')
    try:
        if len(json.dumps(contract, allow_nan=False).encode()) > 8000:
            raise ValueError('Checks contract exceeds 8000 bytes.')
    except (TypeError, RecursionError) as exc:
        raise ValueError('Checks must contain finite JSON values.') from exc
    ids = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) - {'id','args','kwargs','expected','error'}:
            raise ValueError('Unexpected check fields.')
        if not {'id','args'} <= set(check) or ('expected' in check) == ('error' in check):
            raise ValueError('Each check needs id, args and exactly one of expected or error.')
        if not isinstance(check['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', check['id']) or check['id'] in ids:
            raise ValueError('Check IDs must be unique short identifiers.')
        ids.add(check['id'])
        if not isinstance(check['args'], list) or len(check['args']) > 8:
            raise ValueError('Each args field must be a list of up to eight values.')
        kwargs = check.get('kwargs', {})
        if not isinstance(kwargs, dict) or len(kwargs) > 8 or any(not isinstance(k,str) or not k.isidentifier() for k in kwargs):
            raise ValueError('kwargs must be an object of up to eight named values.')
        if 'error' in check and check['error'] not in ('ValueError','TypeError','KeyError','IndexError','ContractError','ZeroDivisionError'):
            raise ValueError('Choose a supported expected exception class.')
    request = copy.deepcopy(challenge('schema'))
    request.update(case='custom', contract=description + '\nInterface: JSON arguments and JSON-serializable result. Python standard-library imports inside the function are permitted. Preserve the exact function name and signature. These checks are user-supplied examples, not a complete correctness proof.', candidate_source=source, user_checks=copy.deepcopy(checks), function_name=fn.name)
    request.pop('challenge_sha256')
    request['challenge_sha256'] = digest(request)
    return request


def validate_custom_request(request):
    if not isinstance(request, dict) or request.get('case') != 'custom':
        raise ValueError('Invalid custom challenge.')
    # Re-validate supplied fields and integrity; never look up another visitor input.
    source = request.get('candidate_source')
    description = request.get('contract', '').split('\nInterface: JSON arguments',1)[0]
    rebuilt = build_custom_challenge({'source':source,'checks':{'description':description,'checks':request.get('user_checks')}})
    if rebuilt != request:
        raise ValueError('Custom challenge integrity mismatch.')
    return request


def make_custom_bundle(proposal, request, run_id):
    from sandbox_bundle import REMOTE_RUNNER
    import hashlib
    validate_custom_request(request)
    validated = validate_proposal(proposal, request)
    if validated['status'] != 'awaiting_remote_sandbox':
        raise ValueError('A patch is required.')
    binding = {'run_id':run_id, 'challenge_sha256':request['challenge_sha256'], 'proposal_sha256':validated['proposal_sha256']}
    fixtures, expected = [], []
    for check in request['user_checks']:
        fixtures.append({'id':check['id'],'args':check['args'],'kwargs':check.get('kwargs',{})})
        outcome = {'kind':'error','type':check['error']} if 'error' in check else {'kind':'return','value':check['expected']}
        expected.append({'id':check['id'],'outcome':outcome,'calls':[]})
    files = {'candidate.py':proposal['source'],'runner.py':REMOTE_RUNNER,
             'job.json':json.dumps({**binding,'case':'custom','function_name':request['function_name'],'fixtures':fixtures},sort_keys=True)}
    plan = {**binding,'expected_observations':expected,'file_sha256':{name:hashlib.sha256(value.encode()).hexdigest() for name,value in files.items()}}
    return {'upload_files':files,'private_comparison_plan':plan,'command':['python3','runner.py'],'execution_performed':False}
