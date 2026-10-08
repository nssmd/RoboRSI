from pathlib import Path
import sys,os,tempfile,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from roborsi.agents import validator

class HarnessTimeoutTests(unittest.TestCase):
    def test_default_and_scoped_timeout(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(validator._harness_timeout_s('libero'),300)
        with patch.dict(os.environ,{'ROBORSI_NATIVE_HARNESS_TIMEOUT_S':'900'}):
            self.assertEqual(validator._harness_timeout_s('libero'),900)
            self.assertEqual(validator._harness_timeout_s('robotwin'),300)

    def test_original_source_restored_after_incomplete_gate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);skill=root/'roborsi/embodied/skills/base/example/libero';skill.mkdir(parents=True)
            (skill/'policy.py').write_text('original policy');(skill/'SKILL.md').write_text('original metadata')
            def run_gate(name,timeout_s):
                self.assertEqual(timeout_s,900)
                self.assertEqual((skill/'policy.py').read_text(),'candidate policy')
                return types.SimpleNamespace(verdict='ERROR',pass_count=None,total=None,reason='timeout',stdout_tail='partial evidence')
            fake=types.SimpleNamespace(run_gate_for=run_gate)
            with patch.object(validator,'_REPO',root),patch.dict(sys.modules,{'scripts_lib_harness_gate':fake}),patch.dict(os.environ,{'ROBORSI_NATIVE_HARNESS_TIMEOUT_S':'900'}):
                result=validator._stage_and_gate('example','candidate policy','candidate metadata',namespace='libero')
            self.assertFalse(result.passed)
            self.assertEqual(result.extras['verdict'],'ERROR')
            self.assertEqual((skill/'policy.py').read_text(),'original policy')
            self.assertEqual((skill/'SKILL.md').read_text(),'original metadata')

    def test_invalid_timeout_does_not_restore_a_false_default(self):
        with patch.dict(os.environ,{'ROBORSI_NATIVE_HARNESS_TIMEOUT_S':'0'}):
            with self.assertRaises(ValueError):validator._harness_timeout_s('libero')

if __name__=='__main__':unittest.main()
