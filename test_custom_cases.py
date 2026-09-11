import copy
import json
import unittest
from unittest.mock import patch
from custom_cases import build_custom_challenge, make_custom_bundle, validate_custom_request
from inference_request import prepare_request
from challenges import validate_proposal, digest
from sandbox_bundle import compare_result


class CustomTests(unittest.TestCase):
    def payload(self):
        return {'source':'def normalize(value):\n    return int(value)\n','checks':{'description':'Return string IDs exactly unchanged.', 'checks':[{'id':'leading-zero','args':['001'],'expected':'001'},{'id':'not-string','args':[1],'error':'TypeError'}]}}
    def proposal(self,r):
        return {'challenge_sha256':r['challenge_sha256'],'decision':'patch','source':'def normalize(value):\n    if not isinstance(value, str):\n        raise TypeError("string required")\n    return value\n','explanation':'Preserve string identity.'}
    def test_source_never_executed_and_expected_not_uploaded(self):
        p=self.payload();p['source']='def normalize(value):\n    raise Exception("must not execute")\n'
        with patch('builtins.exec',side_effect=AssertionError('local candidate execution')):
            r=build_custom_challenge(p);bundle=make_custom_bundle(self.proposal(r),r,'a'*32)
        job=json.loads(bundle['upload_files']['job.json'])
        self.assertNotIn('expected',json.dumps(job));self.assertEqual(job['function_name'],'normalize')
        self.assertFalse(bundle['execution_performed'])
    def test_comparison_and_diagnostics(self):
        r=build_custom_challenge(self.payload());b=make_custom_bundle(self.proposal(r),r,'b'*32);plan=b['private_comparison_plan']
        observations=copy.deepcopy(plan['expected_observations']);observations[1]['outcome']['message']='actual runtime error'
        raw={k:plan[k] for k in ('run_id','challenge_sha256','proposal_sha256')};raw['observations']=observations
        result=compare_result(plan,json.dumps(raw),exit_code=0)
        self.assertTrue(result['observations_match']);self.assertEqual(len(result['checks']),2)
        raw['observations'][0]['outcome']={'kind':'return','value':1}
        self.assertFalse(compare_result(plan,json.dumps(raw),exit_code=0)['observations_match'])
    def test_custom_inference_and_signature_preserved(self):
        r=build_custom_challenge(self.payload());prepared=prepare_request('custom',request_override=r)
        self.assertEqual(prepared['challenge'],r)
        self.assertEqual(validate_proposal(self.proposal(r),r)['status'],'awaiting_remote_sandbox')
        p=self.proposal(r);p['source']='def other(value):\n    return value\n'
        with self.assertRaises(ValueError):validate_proposal(p,r)
    def test_reject_malformed_contracts(self):
        changes=[{'source':'print("hi")'}, {'source':'@danger\ndef normalize(value):\n return value'}, {'source':'def normalize(value=evil()):\n return value'}, {'checks':{'description':'x','checks':[]}}, {'checks':{'description':'x','checks':[{'id':'a','args':[],'expected':float('nan')}]}}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):build_custom_challenge({**self.payload(),**change})
    def test_integrity_and_duplicate_check_ids(self):
        r=build_custom_challenge(self.payload());r['user_checks'][0]['expected']='changed'
        with self.assertRaises(ValueError):validate_custom_request(r)
        p=self.payload();p['checks']['checks'][1]['id']='leading-zero'
        with self.assertRaises(ValueError):build_custom_challenge(p)

if __name__=='__main__':unittest.main()
