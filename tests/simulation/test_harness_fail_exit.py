from pathlib import Path
import json,sys,tempfile,types,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
import scripts_lib_harness_gate as gate

class FailureExitTests(unittest.TestCase):
 def invoke(self,rc,verdict):
  with tempfile.TemporaryDirectory() as d:
   report={'skill':'example','verdict':verdict,'pass_count':0,'total':2,'reason':'measured outcome'}
   response=types.SimpleNamespace(returncode=rc,stdout=json.dumps(report),stderr='')
   with patch.object(gate,'_BASELINES',Path(d)/'baselines.json'),patch.object(gate,'_harness_environment',return_value=({'ROBORSI_HARNESS_NAMESPACE':'libero'},Path(d))),patch.object(gate.subprocess,'run',return_value=response):
    return gate._invoke_harness('example',900)[0]
 def test_expected_exit_one_retains_complete_failure(self):
  self.assertEqual(self.invoke(1,'FAIL')['verdict'],'FAIL')
 def test_unexpected_exit_does_not_become_valid_failure(self):
  self.assertEqual(self.invoke(2,'FAIL')['verdict'],'ERROR')
 def test_nonzero_exit_cannot_claim_success(self):
  self.assertEqual(self.invoke(1,'PASS')['verdict'],'ERROR')

if __name__=='__main__':unittest.main()
