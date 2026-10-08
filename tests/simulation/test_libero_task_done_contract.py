import ast
from threading import RLock
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
class Array:
    def __init__(self,x):self.x=x
    def flatten(self):return self.x

class TaskDoneContractTest(unittest.TestCase):
    def make(self,returned_done=True,physical_done=False):
        source=ROOT/'roborsi/embodied/sim/libero/adapter.py'
        tree=ast.parse(source.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LiberoProEnv')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='step')
        for a in method.args.args:a.annotation=None
        method.returns=None
        ns={'Step':lambda **kw:SimpleNamespace(**kw),'Observation':lambda:SimpleNamespace(),'_visible_raw_obs':lambda r:r,'_to_sim_obs':lambda r,i:r}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'actual-adapter-step','exec'),ns)
        physical=SimpleNamespace(done=physical_done);calls=[]
        def step(action):
            calls.append(action);return {'image':'fresh'},10,returned_done,{'success':True,'task_success':True,'is_success':True,'other':'public'}
        env=SimpleNamespace(_sensor_lock=RLock(),_observation_generation=0,_terminated=False,_env=SimpleNamespace(env=physical,step=step),_last_obs={'image':'old'},_bind_gl_context=lambda:None,instruction='public instruction',_tick_cb=None,_capture_frame=lambda:None)
        return ns['step'],env,calls

    def invoke(self,fn,env):
        with patch.dict(sys.modules,{'numpy':SimpleNamespace(asarray=lambda x,**kw:Array(x),float64=float)}):return fn(env,[0]*7)

    def test_task_success_does_not_freeze_release_or_leak_done(self):
        fn,env,calls=self.make(returned_done=True)
        first=self.invoke(fn,env);second=self.invoke(fn,env)
        self.assertEqual(len(calls),2);self.assertFalse(env._terminated);self.assertFalse(first.done);self.assertFalse(second.done)
        self.assertEqual(first.reward,0.0);self.assertEqual(first.info,{'other':'public'})

    def test_true_simulator_termination_still_blocks_motion(self):
        fn,env,calls=self.make(physical_done=True)
        result=self.invoke(fn,env);self.assertEqual(calls,[]);self.assertTrue(result.done);self.assertEqual(result.info,{'terminated':True})

    def test_normal_unfinished_task_keeps_stepping(self):
        fn,env,calls=self.make(returned_done=False)
        result=self.invoke(fn,env);self.assertEqual(len(calls),1);self.assertFalse(result.done)

    def test_latched_physical_termination_is_preserved(self):
        fn,env,calls=self.make();env._terminated=True
        result=self.invoke(fn,env);self.assertEqual(calls,[]);self.assertTrue(result.done)

if __name__=='__main__':unittest.main()
