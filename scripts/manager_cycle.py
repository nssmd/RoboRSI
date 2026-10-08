"""Manager decision/apply cycle for one isolated evolving capability library."""
from pathlib import Path
import json,os,time,subprocess,re,fcntl
from roborsi.embodied.paths import home
from roborsi.runtime_mode import require_evolution
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools,capture_usage
from roborsi.channels.core.agent import _extract_text_block
from roborsi.agents.gt_firewall import redact
R=Path(__file__).resolve().parents[1]
os.environ['ROBORSI_HARNESS_NAMESPACE']='libero'
os.environ['ROBORSI_HARNESS_BACKEND']='libero-pro'
os.environ.setdefault('ROBORSI_HARNESS_PYTHON',str(Path.home()/'roborsi-lab/envs/roborsi-eval/bin/python'))
os.environ.setdefault('ROBORSI_OPENAI_MAX_OUTPUT_TOKENS','32768')
from roborsi.agents.flat_refinement import flat_enabled, FLAT_RULES

def log(kind,**kw):
 with (home()/'manager_events.jsonl').open('a') as f:f.write(json.dumps({'utc':time.time(),'kind':kind,**kw})+'\n')
def parse(text):
 text=text.strip()
 if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
 return json.loads(text)
NATIVE_REVIEW_SYSTEM = 'You are RoboRSI Manager following the original RoboHarness workflow. Engineer executes; Reviewer examines actual execution, diagnoses failures and AUTHORS skill changes; Manager reviews the complete process and can also suggest and AUTHOR changes. There is NO separate Developer role. Both Reviewer and Manager may add native base skills and UPDATE existing skills using the same name, including native perception, geometry and control implementations. They are not limited to composing existing tools. Read the supplied source and failure observations, diagnose implementation problems and return concrete native new_code when useful. Preserve the namespace dispatch ABI, real observations, collision/reachability/holding checks and honest failure reporting. Do not use hidden task predicates, hidden object state, simulator task source, teleports or fabricated success. Approval of an existing proposal allows its real harness trial; publication follows only after that declared harness passes. A primitive is checked by its own functional harness, while task compounds use task-success gates. Preserve all declared tasks, seeds, arguments and pass criteria. Any code YOU author is queued for a later separate review and never self-approved in this response. Return JSON only: {"decision":"approve"|"reject"|"defer","reason":"...","lesson_decision":"approve"|"reject"|"defer","lesson_evidence":"...","cross_task_lesson":"...","code_proposal":null or {"name":"skill_name","kind":"new"|"update","category":"base/libero","new_code":"complete policy.py","skill_md":"complete typed SKILL.md and harness","rationale":"..."}}.'

def validate_code(q):
    import yaml
    from roborsi.agents.validator import ProposalValidator
    md = q.get('skill_md','')
    if not md:
        from roborsi.embodied.skills import get_ns
        sk = get_ns(q.get('name',''),'libero')
        md = sk.path.read_text() if sk else ''
    try:
        fm=yaml.safe_load(md.split('---',2)[1]);h=(fm.get('metadata') or {}).get('harness',{})
        assert (h.get('pass_criteria') or {}).get('kind') in (('simulator_task_success','grasp_holds_actor','verify_returns_bool','move_completes','tool_returns_well_formed') if q.get('development_mode')=='native' else ('simulator_task_success',))
        assert len(set(h.get('seeds',[])))>=2
        assert int((h.get('pass_criteria') or {}).get('min_seeds_passing',0))>=2
    except Exception:
        raise ValueError('Code promotion needs independent simulator_task_success harness with >=2 distinct seeds and >=2 passes')
    for declared, field in [("development_task", "sim_task"),
                            ("development_seeds", "seeds"),
                            ("development_pass_criteria", "pass_criteria")]:
        if q.get(declared) is not None and h.get(field) != q[declared]:
            raise ValueError("Changed declared development fixture: " + declared)
    return ProposalValidator().validate(q)

def normalize_public_result(tool, result):
    """Remove only verified literal no-privilege disclaimers from public results."""
    if not isinstance(result,dict):return result
    result=dict(result)
    note=result.get('note')
    if tool=='grasp_object' and isinstance(note,str):
        result['note']=note.replace('perception grasp (no GT).','perception grasp.')
    if tool=='is_holding' and note=='Shared calibrated gripper-state check (no object ground truth).':
        result['note']='Shared calibrated gripper-state check.'
    return result

def cycle():
 require_evolution('Manager cross-task review and promotion');home().mkdir(parents=True,exist_ok=True)
 with (home()/'manager.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  hist=home()/'manager_decisions.jsonl';prior=hist.read_text().splitlines()[-12:] if hist.exists() else []
  from manager_patch_review import compact_history, materialize as materialize_manager_patch
  prior=compact_history(prior)
  for folder in ['wiki_review','plan_review','skill_review','policy_review']:
   if flat_enabled() and folder=='plan_review':
    log('flat_plan_promotion_disabled');continue
   for p in sorted((home()/folder).glob('*.json')):
    q=json.loads(p.read_text())
    if q.get('status','pending')!='pending' or q.get('manager_reviewed_at'):continue
    if folder=='skill_review' or q.get('kind')=='code_revision_request':q['development_mode']='native'
    from native_patch_context import materialize_policy_patch, review_text, HARNESS_CONTRACT
    from manager_native import attach_failure_source
    q=attach_failure_source(q,R)
    if q.get('development_mode')=='native':
     q=materialize_policy_patch(q,R)
    # Do not expose gate labels, scoring outcomes or private source identifiers.
    keys=['target_path','old_string','new_string','original_patch','development_mode','native_source','native_source_sha256','id','kind','task','name','category','root_cause','next_action','rationale','new_code','code','skill_md','workspace_plan_md','prior_persistent_md','policy_code','compound_name','validation_observations','revision_request','parent_proposal','development_task','development_seeds','development_pass_criteria','repair_root','repair_depth']
    visible={k:q[k] for k in keys if k in q}
    # Manager decisions need observations, not merely the Reviewer's conclusion.
    from roborsi.agents.reviewer import _sanitize_trace
    evidence=[]
    source=q.get('source_workspace')
    roots=[]
    if source:
     candidate_path=Path(source).resolve()
     if candidate_path.is_relative_to((home()/'workspaces').resolve()):roots=[candidate_path]
    if not roots and q.get('source_run_id'):
     roots=[p for p in (home()/'workspaces').glob('*'+str(q['source_run_id'])+'*') if p.is_dir()]
    if folder=='policy_review':
     from libero_compound_validation import source_workspaces
     try:roots=list(dict.fromkeys(roots+source_workspaces(q)))
     except Exception as exc:
      q.update(status='source_validation_blocked',validation_error=str(exc));p.write_text(json.dumps(q,indent=2));log('libero_compound_source_blocked',proposal=p.name,error=str(exc));continue
    source_task_key=q.get('task')
    for w in roots:
     identity=w/'episode_identity.json'
     if identity.exists():source_task_key=json.loads(identity.read_text()).get('task_key',source_task_key)
     trace_paths=sorted((w/'round_traces').glob('round_*.json'))
     if not trace_paths:trace_paths=list(w.rglob('trace.json'))
     for trace_path in trace_paths:
      for entry in _sanitize_trace(json.loads(trace_path.read_text())):
       call=entry.get('tool_call') or {};res=normalize_public_result(call.get('tool'),entry.get('result') or {})
       if call and isinstance(res,dict):evidence.append({'round':entry.get('episode_round'),'step':entry.get('step'),'tool':call.get('tool'),'args':call.get('args'),'ok':res.get('ok'),'result':res})
    # Discard a whole contaminated observation, not all unrelated evidence.
    safe_evidence=[];excluded_evidence=[]
    for entry in evidence:
     _,blocked=redact(str(q.get('task','')),json.dumps(entry,ensure_ascii=False))
     if blocked:excluded_evidence.append({'step':entry.get('step'),'tool':entry.get('tool'),'markers':blocked})
     else:safe_evidence.append(entry)
    # Proposal text still fails closed as a whole; do not salvage a tainted claim.
    native_code={}
    if q.get('development_mode')=='native':
     from manager_native import assert_native_candidate
     for code_key in ['new_code','code','native_source']:
      if visible.get(code_key):
       assert_native_candidate(visible[code_key]);native_code[code_key]=visible.pop(code_key)
    clean,dropped=redact(str(q.get('task','')),review_text(json.dumps(visible,ensure_ascii=False)))
    visible.update(native_code)
    if not dropped:
     visible['source_task_key']=source_task_key
     visible['observed_tool_evidence']=safe_evidence
     visible['evidence_scope']='all preserved public tool records, with whole contaminated records excluded'
     visible['evidence_exclusions_count']=len(excluded_evidence)
     clean=review_text(json.dumps(visible,ensure_ascii=False))
    log('evidence_filter',proposal=p.name,kept=len(safe_evidence),excluded=excluded_evidence,proposal_blocked=dropped)

    if dropped:
     q.update(manager_reviewed_at=time.time(),manager_decision='defer',manager_note='Proposal contains forbidden criterion/source text; refile from deployable evidence');p.write_text(json.dumps(q,indent=2));log('deferred_firewall',proposal=p.name);continue
    from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
    from roborsi.embodied.skills import discover_ns
    # One namespace scan per proposal, refreshed after any preceding publication.
    # get_ns rescans the entire skill tree; doing that per tool stalls on NFS.
    skills_by_name={skill.name:skill for skill in discover_ns('libero')}
    contracts=[]
    for spec in _build_tool_specs(ns='libero'):
     function=spec['function'];skill=skills_by_name.get(function['name'])
     if skill is None:continue
     contracts.append({'name':function['name'],'parameters':function['parameters'],
                       'returns':skill.frontmatter.get('returns',{}),
                       'description':function.get('description','')[:1000]})
    tools_block=json.dumps(contracts,ensure_ascii=False)
    model=os.environ.get('ROBORSI_MANAGER_MODEL','gpt-5.6-sol')
    from manager_native import unified_review_system
    actual_system=unified_review_system(q,NATIVE_REVIEW_SYSTEM,HARNESS_CONTRACT,FLAT_RULES if flat_enabled() else "")
    import hashlib
    log("manager_review_context",proposal=p.name,system_sha256=hashlib.sha256(actual_system.encode()).hexdigest(),native_source_sha256=q.get("native_source_sha256"),native_source_path=q.get("native_source_path"),proposal_kind=q.get("kind"))
    with capture_usage() as usage:
     resp=_call_vlm_tools(model,[{'role':'system','content':actual_system},{'role':'user','content':'Prior Manager decisions:\n'+'\n'.join(prior)+'\nPublic capability index:\n'+tools_block+'\nProposal:\n'+clean}],[],thinking_budget=0,tool_choice='none')
    content=getattr(resp,'content','') or ''
    if isinstance(content,list):content=''.join(_extract_text_block(c) for c in content)
    responses=home()/'manager_responses';responses.mkdir(exist_ok=True)
    response_path=responses/(p.stem+'-'+str(time.time_ns())+'.json')
    response_path.write_text(json.dumps({'proposal':p.name,'model':model,'utc':time.time(),'raw_response':content,'usage':usage.to_dict()},indent=2))
    try:
     d=parse(content)
     if d.get('decision') not in ['approve','reject','defer']:
      raise ValueError('invalid Manager decision')
    except (ValueError,TypeError,AttributeError) as exc:
     q.update(manager_reviewed_at=time.time(),manager_decision='defer',manager_note='Unusable structured response; explicit evidence repair required',manager_response_path=str(response_path))
     p.write_text(json.dumps(q,indent=2))
     log('manager_response_invalid',proposal=p.name,error=str(exc),response_path=str(response_path))
     continue

    rec={'utc':time.time(),'proposal':p.name,'source_task':source_task_key,'decision':d,'model':model,'usage':usage.to_dict()}
    with hist.open('a') as f:f.write(json.dumps(rec)+'\n')
    q.update(manager_reviewed_at=rec['utc'],manager_decision=d['decision'],manager_note=d.get('reason',''));p.write_text(json.dumps(q,indent=2))
    approved=d['decision']=='approve';applied=False
    if d['decision']=='reject':
     q['status']='rejected';p.write_text(json.dumps(q,indent=2))
    if folder=='wiki_review' and q.get('kind')=='failure_hypothesis' and d['decision']!='defer':
     from roborsi.agents.task_wiki import resolve_wiki_hypothesis
     resolve_wiki_hypothesis(p,approve=approved,manager_note=d.get('reason',''));applied=approved
    elif folder=='plan_review' and d['decision']!='defer':
     from roborsi.agents.task_wiki import resolve_plan_promotion
     resolve_plan_promotion(p,approve=approved,manager_note=d.get('reason',''));applied=approved
    elif folder=='skill_review' and approved:
     from roborsi.agents.validator import ProposalValidator
     try:gate=validate_code(q)
     except Exception as exc:
      log('code_gate_blocked',proposal=p.name,error=str(exc));continue
     log('code_gate',proposal=p.name,report=gate.to_dict())
     if gate.overall_pass:
      # Gate was just executed. apply performs its own capability safety check.
      r=subprocess.run([os.environ.get('ROBORSI_APPLY_PYTHON','python3'),'scripts/apply_selfevo_proposal.py',q['id'],'--skip-harness'],cwd=R,capture_output=True,text=True,timeout=180)
      log('code_apply',proposal=p.name,returncode=r.returncode,output=(r.stdout+r.stderr)[-1500:]);applied=r.returncode==0
     else:log('code_not_promoted',proposal=p.name,reason=gate.note)
    elif folder=='policy_review' and approved:
     from libero_compound_validation import enqueue
     try:log('libero_compound_validation_queued',proposal=p.name,job=enqueue(p))
     except Exception as exc:
      q.update(status='validation_blocked',validation_error=str(exc));p.write_text(json.dumps(q,indent=2));log('libero_compound_gate_blocked',proposal=p.name,error=str(exc))
    lesson=d.get('cross_task_lesson','').strip()
    if d.get('lesson_decision')=='approve' and d.get('lesson_evidence') and lesson:
     lesson,dropped=redact(str(q.get('task','')),lesson)
     if not dropped and lesson.strip():
      shared=home()/'cross_task_approved.json';rows=json.loads(shared.read_text()) if shared.exists() else []
      if lesson not in [r['lesson'] for r in rows]:rows.append({'status':'approved','source_proposal':p.name,'source_task':source_task_key,'lesson':lesson,'approved_at':time.time(),'lesson_evidence':d['lesson_evidence']});shared.write_text(json.dumps(rows,indent=2))
    candidate=d.get('code_proposal')
    if isinstance(candidate,dict):
     try:candidate=materialize_manager_patch(candidate,q)
     except (ValueError,SyntaxError) as exc:
      log('manager_patch_blocked',proposal=p.name,error=str(exc));continue
     from roborsi.embodied.skills import get_ns
     from roborsi.agents.proposal_safety import assert_safe_candidate,assert_safe_skill_text
     name=str(candidate.get('name',''))
     if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',name) or candidate.get('kind') not in ('new','update') or candidate.get('category')!='base/libero':
      log('manager_candidate_rejected',reason='Invalid native base skill identity',proposal=p.name)
     elif candidate.get('kind')=='new' and get_ns(name,'libero') is not None:
      log('manager_candidate_rejected',reason='Duplicate existing skill',name=name)
     elif candidate.get('kind')=='update' and get_ns(name,'libero') is None:
      log('manager_candidate_rejected',reason='Existing skill to update was not found',name=name)
     else:
      code=str(candidate.get('new_code',''));md=str(candidate.get('skill_md',''))
      raw_candidate=home()/'manager_candidates'/(p.stem+'-'+str(time.time_ns())+'.json')
      raw_candidate.parent.mkdir(exist_ok=True)
      raw_candidate.write_text(json.dumps({'source_proposal':p.name,'candidate':candidate,'model':model,'utc':time.time()},indent=2))
      try:
       from manager_native import assert_native_candidate
       assert_native_candidate(code)
       candidate['development_mode']='native'
       candidate.update(id='manager-'+str(time.time_ns()),status='pending',submitted_by='Manager',task=source_task_key,source_task=source_task_key,source_proposal=p.name)
       for origin in ['source_workspace','source_run_id','source_trace_dir','repair_root','repair_depth','development_task','development_seeds','development_pass_criteria','validation_observations','parent_proposal','revision_request','native_source','native_source_sha256']:
        if q.get(origin) is not None:candidate[origin]=q[origin]
       target=home()/'skill_review'/(candidate['id']+'.json');target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(candidate,indent=2))
       log('manager_candidate_queued',proposal=target.name,source_proposal=p.name,reason='Requires a separate review of generated code before simulator gate')
      except Exception as exc:log('manager_candidate_blocked',name=name,error=str(exc))
    log('review_complete',proposal=p.name,decision=d['decision'],applied=applied)
  # Commit only evolving capability artifacts, preserving a concrete version boundary.
  subprocess.run(['git','add','roborsi/embodied/skills'],cwd=R,check=True)
  if subprocess.run(['git','diff','--cached','--quiet'],cwd=R).returncode:
   subprocess.run(['git','-c','user.name=RoboRSI Manager','-c','user.email=manager@localhost','commit','-m','evolve: preserve Manager-reviewed capability iteration'],cwd=R,check=True,capture_output=True)
  from libero_compound_validation import run_pending
  run_pending()
  log('cycle_finished',code_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip())
if __name__=='__main__':cycle()
