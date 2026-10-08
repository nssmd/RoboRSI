"""Actual Manager approval, isolated functional test, versioned publication."""
from pathlib import Path
import json,os,re,signal,socket,subprocess,sys,time
from roborsi.embodied.paths import home
from roborsi.agents.compound_source import records,stable
from libero_compound_protocol import validate,passed
R=Path(__file__).resolve().parents[1];S=home();A=S.parent;O=A.parent;J=A/'compound-gates'

def save(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));tmp.replace(path)

def event(kind,**kw):
    with (S/'manager_events.jsonl').open('a') as f:f.write(json.dumps({'utc':time.time(),'kind':kind,**kw})+'\n')

def source_workspaces(q):
    validate(q)
    if not stable(q['task_key']):raise ValueError('Need >=3 verified evolving successes and >=50% on this exact task')
    return [Path(r['workspace']) for r in records(q['task_key']) if r['verified_success']][-3:]

def enqueue(path):
    path=Path(path).resolve();assert path.parent==(S/'policy_review').resolve()
    q=json.loads(path.read_text());validate(q);source_workspaces(q)
    if q.get('manager_decision')!='approve' or not q.get('manager_reviewed_at'):raise ValueError('Actual independent Manager approval required')
    from roborsi.agents.task_wiki import _assert_compound_policy_safe,compound_dir
    from roborsi.embodied.skills import get_ns
    from manager_development import assert_runtime_contract
    _assert_compound_policy_safe(q['task'],q['compound_name'],q['policy_code'],q['skill_md'])
    assert_runtime_contract(q['policy_code'])
    if get_ns(q['compound_name'],'libero') is not None or compound_dir(q['task'],q['compound_name']).exists():raise ValueError('Never overwrite an existing tool')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',q['id']):raise ValueError('Invalid proposal id')
    job=J/q['id']
    if job.exists():return str(job)
    job.mkdir(parents=True);save(job/'request.json',{'utc':time.time(),'repo':str(R),'proposal_path':str(path),'proposal':q})
    event('libero_compound_queued',proposal=q['id'],job=str(job));return str(job)

def reserved_manager_boundary():
    state=json.loads((A/'controller-state.json').read_text())
    if state.get('phase')!='manager' or state.get('manager_pid')!=os.getpid() or state.get('pid')!=os.getppid():return False
    parent=Path('/proc')/str(os.getppid())
    if str(A/'controller.py') not in (parent/'cmdline').read_bytes().decode().split('\0'):return False
    leases=json.loads((O/'formal-resume/gpu-reservations.json').read_text())
    if not any(r['pid']==os.getppid() and r.get('host',socket.gethostname())==socket.gethostname() for r in leases):return False
    for p in Path('/proc').glob('[0-9]*'):
        try:args=(p/'cmdline').read_bytes().decode().split('\0')
        except (OSError,UnicodeError):continue
        if str(A/'pro_worker.py') in args:return False
        if 'eval-suite' in args and '--out' in args and args[args.index('--out')+1].startswith(str(A/'campaigns')):return False
    return True

def run_pending():
    # Use the existing Manager GPU and serial gate; no additional simulator slot.
    pending=[p for p in sorted(J.glob('*')) if (p/'request.json').exists() and not (p/'result.json').exists()]
    if not pending:return
    if not reserved_manager_boundary():
        event('libero_compound_deferred',reason='No verified drained Manager reservation');return
    if subprocess.run(['git','diff','--quiet','HEAD','--','roborsi','scripts'],cwd=R).returncode:
        event('libero_compound_deferred',reason='Runtime changes not committed');return
    job=pending[0];req=json.loads((job/'request.json').read_text());q=req['proposal'];path=Path(req['proposal_path'])
    current=json.loads(path.read_text())
    for key in ['id','task','task_key','compound_name','policy_code','skill_md']:
        if current.get(key)!=q.get(key):raise RuntimeError('Reviewed proposal changed')
    if current.get('manager_decision')!='approve' or current.get('status')!='pending':return
    if (job/'started.json').exists():
        event('libero_compound_uncertain_attempt',job=str(job),reason='Preserved interrupted attempt; no unchanged retry');return
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()
    save(job/'started.json',{'utc':time.time(),'manager_pid':os.getpid(),'revision':revision})
    result={}
    try:
        snapshot=job/'runtime'
        subprocess.run(['git','worktree','add','--detach',str(snapshot),revision],cwd=R,check=True,capture_output=True,timeout=600)
        req.update(runtime_revision=revision,validation_repo=str(snapshot));save(job/'request.json',req)
        env=dict(os.environ,PYTHONPATH=str(snapshot),ROBORSI_HOME=str(job/'state'),ROBORSI_WORKSPACE=str(job/'state/workspace'),
                 ROBORSI_TRACE_DB=str(job/'state/trace.db'),ROBORSI_RUN_MODE='eval',ROBORSI_CURRENT_SIM_TASK=q['task_key'],
                 ROBORSI_HARNESS_NAMESPACE='libero',ROBORSI_HARNESS_BACKEND='libero-pro',PYTHONUNBUFFERED='1')
        with (job/'native.log').open('xb') as log:
            command=[os.environ['ROBORSI_HARNESS_PYTHON'],str(snapshot/'scripts/libero_compound_native.py'),str(job)]
            child=subprocess.Popen(command,cwd=snapshot,env=env,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
            save(job/'launch.json',{'utc':time.time(),'native_pid':child.pid,'command':command,'manager_reserved_slot':True})
            try:rc=child.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                actual=(Path('/proc')/str(child.pid)/'cmdline').read_bytes().decode().split('\0')
                assert str(snapshot/'scripts/libero_compound_native.py') in actual
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                raise RuntimeError('Owned compound test exceeded 30 minutes')
        report=json.loads((job/'harness-report.json').read_text()) if (job/'harness-report.json').exists() else {}
        result={'utc':time.time(),'state':'complete','rc':rc,'passed':rc==0 and passed(q,report),'runtime_revision':revision}
        if not result['passed']:
            current.update(status='validation_failed',validation_report=str(job/'harness-report.json'));save(path,current);save(job/'result.json',result);event('libero_compound_not_published',proposal=q['id'],result=result);return
        if not reserved_manager_boundary():raise RuntimeError('Manager boundary lost before publication')
        if subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()!=revision:raise RuntimeError('Validated source changed')
        if subprocess.run(['git','diff','--quiet','HEAD','--','roborsi','scripts'],cwd=R).returncode:raise RuntimeError('Uncommitted source changed')
        latest=json.loads(path.read_text())
        for key in ['policy_code','skill_md','task_key','manager_decision']:
            if latest.get(key)!=current.get(key):raise RuntimeError('Approval or candidate changed')
        from roborsi.agents.task_wiki import compound_dir,resolve_policy_proposal
        target=compound_dir(q['task'],q['compound_name']);assert not target.exists()
        try:
            resolve_policy_proposal(path,approve=True,manager_note='Independent Manager approval and exact original-LIBERO simulator gate passed')
            relative=str(target.relative_to(R))
            subprocess.run(['git','add',relative],cwd=R,check=True)
            subprocess.run(['git','-c','user.name=RoboRSI Manager','-c','user.email=manager@localhost','commit','--only','-m','evolve: publish verified compound '+q['compound_name'],relative],cwd=R,check=True,capture_output=True)
        except Exception:
            if target.exists():
                subprocess.run(['git','reset','HEAD','--',str(target.relative_to(R))],cwd=R,capture_output=True)
                import shutil
                shutil.move(str(target),str(job/'publication-error-artifact'))
            path.write_text(json.dumps(current,indent=2));raise
        result.update(state='published',revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip())
        event('libero_compound_published',proposal=q['id'],**result)
    except Exception as exc:
        result={'utc':time.time(),'state':'infrastructure_error','passed':False,'error':repr(exc)}
        current.update(status='validation_error',validation_error=repr(exc));save(path,current)
        event('libero_compound_error',proposal=q['id'],error=repr(exc))
    save(job/'result.json',result)
