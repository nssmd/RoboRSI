"""Execute reviewed compound via the real isolated LIBERO harness."""
from pathlib import Path
import importlib.util,json,sys

def prepare_dispatch(harness,module,task):
    original_state=harness._State
    class CompoundState(original_state):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.task=task
    harness._State=CompoundState
    # Preserve candidate globals so the harness can instrument its actual calls.
    return module.dispatch_runtime

def main(job):
    job=Path(job).resolve();request=json.loads((job/'request.json').read_text());R=Path(request['validation_repo']).resolve()
    assert R.is_relative_to(job)
    sys.path[:0]=[str(R),str(R/'scripts')]
    from libero_compound_protocol import validate
    q=request['proposal'];fm,_=validate(q)
    from roborsi.agents.task_wiki import _assert_compound_policy_safe
    _assert_compound_policy_safe(q['task'],q['compound_name'],q['policy_code'],q['skill_md'])
    import test_base_skill as harness
    code=job/'candidate.py';code.write_text(q['policy_code'])
    spec=importlib.util.spec_from_file_location('reviewed_libero_compound',code);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    dispatch=prepare_dispatch(harness,module,q['task'])
    harness._load_frontmatter=lambda name:fm if name==q['compound_name'] else (_ for _ in ()).throw(ValueError('Unexpected skill'))
    original=harness._load_skill_dispatch
    harness._load_skill_dispatch=lambda name:dispatch if name==q['compound_name'] else original(name)
    from roborsi.embodied.agent_loop.vlm_io import capture_usage
    run=harness._run_one
    def metered(*args,**kwargs):
        with capture_usage() as usage:result,state=run(*args,**kwargs)
        result['usage']=usage.to_dict();return result,state
    harness._run_one=metered
    report=harness._run_skill_from_frontmatter(q['compound_name'])
    (job/'harness-report.json').write_text(json.dumps(report,indent=2,default=str))
    print(json.dumps({'sim_task':report.get('sim_task'),'verdict':report.get('verdict')}),flush=True)

if __name__=='__main__':main(sys.argv[1])
