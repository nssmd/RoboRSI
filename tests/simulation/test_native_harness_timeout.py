from pathlib import Path
import sys,os,tempfile,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from roborsi.agents.evolution import validator

class HarnessTimeoutTests(unittest.TestCase):
    def test_default_and_scoped_timeout(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(validator._harness_timeout_s('libero'),300)
        with patch.dict(os.environ,{'ROBORSI_NATIVE_HARNESS_TIMEOUT_S':'900'}):
            self.assertEqual(validator._harness_timeout_s('libero'),900)
            self.assertEqual(validator._harness_timeout_s('robotwin'),300)

    def test_candidate_runs_in_a_worktree_and_the_library_is_untouched(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);skill=root/'roborsi/embodied/skills/base/example/libero';skill.mkdir(parents=True)
            (skill/'policy.py').write_text('original policy');(skill/'SKILL.md').write_text('original metadata')
            for cmd in (['git','init','-q'],['git','add','-A'],['git','-c','user.name=t','-c','user.email=t@t','commit','-qm','init']):
                subprocess.run(cmd,cwd=root,check=True)
            seen={}
            def run_gate(name,timeout_s,repo):
                self.assertEqual(timeout_s,900)
                seen['candidate']=(repo/'roborsi/embodied/skills/base/example/libero/policy.py').read_text()
                seen['live']=(skill/'policy.py').read_text()
                return types.SimpleNamespace(verdict='ERROR',pass_count=None,total=None,reason='timeout',stdout_tail='partial evidence')
            import roborsi.agents.evolution.harness_gate as hg
            with patch.object(validator,'_REPO',root),patch.object(hg,'run_gate_for',run_gate),patch.dict(os.environ,{'ROBORSI_NATIVE_HARNESS_TIMEOUT_S':'900'}):
                result=validator._stage_and_gate('example','candidate policy','candidate metadata',namespace='libero')
            self.assertFalse(result.passed)
            self.assertEqual(result.extras['verdict'],'ERROR')
            self.assertEqual(seen,{'candidate':'candidate policy','live':'original policy'})
            self.assertEqual((skill/'policy.py').read_text(),'original policy')
            self.assertEqual((skill/'SKILL.md').read_text(),'original metadata')

    def test_invalid_timeout_does_not_restore_a_false_default(self):
        with patch.dict(os.environ,{'ROBORSI_NATIVE_HARNESS_TIMEOUT_S':'0'}):
            with self.assertRaises(ValueError):validator._harness_timeout_s('libero')

if __name__=='__main__':unittest.main()
