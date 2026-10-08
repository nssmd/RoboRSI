"""Preserve invalid generated candidates and request bounded actual Manager repair."""
from pathlib import Path
import hashlib,json,os,time

def record_rejection(task, run_id, name, code, skill_md, rationale, error):
    # The caller owns compound-capture.lock; this never executes candidate code.
    from roborsi.embodied.paths import home
    from roborsi.agents.compound_source import stable
    state=home();task_key=os.environ.get('ROBORSI_CURRENT_SIM_TASK')
    if task!='libero_pick_place' or not stable(task_key):
        raise ValueError('Unqualified source for compound repair feedback')
    matches=list((state/'workspaces').glob('*'+run_id+'*'))
    if len(matches)!=1:raise ValueError('Expected one exact source workspace')
    identity=json.loads((matches[0]/'episode_identity.json').read_text())
    if identity.get('task_key')!=task_key:raise ValueError('Mismatched source workspace')
    root=state/'policy_review_deferred';root.mkdir(exist_ok=True)
    digest=hashlib.sha256((code+'\n'+skill_md).encode()).hexdigest()
    p=root/(str(time.time_ns())+'-'+digest[:12]+'.json')
    data={'kind':'compound_capture_rejected','status':'validation_rejected',
          'source_run_id':run_id,'source_workspace':str(matches[0]),'task':task,'task_key':task_key,
          'compound_name':name,'policy_code':code,'skill_md':skill_md,'rationale':rationale,
          'validation_error':str(error),'candidate_sha256':digest,'created_utc':time.time()}
    # One existing lineage per physical source task; neither an unchanged model
    # output nor a renamed candidate resets the Manager's repair-depth limit.
    repair_root='libero-compound-capture-'+task_key.replace('/','_')
    queue=state/'wiki_review';queue.mkdir(exist_ok=True)
    request_path=queue/('000-'+repair_root+'.json')
    request={'id':request_path.stem,'kind':'code_revision_request','status':'pending',
             'task':task,'task_key':task_key,'name':name,'category':'base/libero',
             'source_run_id':run_id,'source_workspace':str(matches[0]),
             'new_code':code,'skill_md':skill_md,'rationale':rationale,
             'repair_root':repair_root,'repair_depth':0,'parent_proposal':str(p),
             'development_task':task_key,'development_seeds':[21,22],
             'development_pass_criteria':{'kind':'simulator_task_success','min_seeds_passing':2},
             'validation_observations':{'stage':'static_candidate_validation','error':str(error)},
             'revision_request':'Review and repair this actual Planner-authored candidate. Static validation rejected it: '+str(error)+'. The exact permitted import is from roborsi.embodied.agent_loop.rollout import _dispatch_tool. Keep the declared original-LIBERO task, seeds21/22 and both-success gate. Preserve original candidate evidence; do not claim validation or publication. A changed candidate requires separate actual Manager review and simulator validation.',
             'created_utc':time.time()}
    p.write_text(json.dumps(data,indent=2))
    temporary=queue/('.'+request_path.stem+'-'+str(time.time_ns())+'.tmp')
    temporary.write_text(json.dumps(request,indent=2))
    try:
        os.link(temporary,request_path)
        data['manager_request_created']=True
    except FileExistsError:
        data['manager_request_created']=False
    finally:
        temporary.unlink(missing_ok=True)
    data['manager_request']=str(request_path);p.write_text(json.dumps(data,indent=2))
    return p
