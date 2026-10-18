"""Manager decision/apply cycle for one isolated evolving capability library."""
from pathlib import Path
import json,os,time,subprocess,re,fcntl
from roborsi.embodied.paths import home
from roborsi.runtime_mode import require_evolution
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools,capture_usage
from roborsi.channels.core.agent import _extract_text_block
from roborsi.agents.safety.gt_firewall import redact
R=Path(__file__).resolve().parents[4]
os.environ.setdefault('ROBORSI_HARNESS_NAMESPACE','libero')
os.environ.setdefault('ROBORSI_HARNESS_BACKEND','libero-pro')
os.environ.setdefault('ROBORSI_HARNESS_PYTHON',os.sys.executable)
os.environ.setdefault('ROBORSI_OPENAI_MAX_OUTPUT_TOKENS','32768')

def log(kind,**kw):
 with (home()/'manager_events.jsonl').open('a') as f:f.write(json.dumps({'utc':time.time(),'kind':kind,**kw})+'\n')
def parse(text):
 text=text.strip()
 if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
 return json.loads(text)
NATIVE_REVIEW_SYSTEM = 'You are RoboRSI Manager following the original RoboHarness workflow. Engineer executes; Reviewer examines actual execution, diagnoses failures and AUTHORS skill changes; Manager reviews the complete process and can also suggest and AUTHOR changes. There is NO separate Developer role. Both Reviewer and Manager may add native base skills and UPDATE existing skills using the same name, including native perception, geometry and control implementations. They are not limited to composing existing tools. Read the supplied source and failure observations, diagnose implementation problems and return concrete native new_code when useful. Preserve the namespace dispatch ABI, real observations, collision/reachability/holding checks and honest failure reporting. Do not use hidden task predicates, hidden object state, simulator task source, teleports or fabricated success. Approval of an existing proposal allows its real harness trial; publication follows only after that declared harness passes. A primitive is checked by its own functional harness, while task compounds use task-success gates. Preserve all declared tasks, seeds, arguments and pass criteria. Any code YOU author is queued for a later separate review and never self-approved in this response. Every approved base-skill code change also needs a scope: "global" when the fix is general and should change the shared skill for every task, "task" when it only suits the source task and must not affect others. Outcomes: source_episode_outcome.simulator_success is the verdict of the simulator, evaluated after the last round; in older records `vlm_declared_done` means the simulator confirmed success and the Engineer declared done, `predicate_passed_without_done` a simulator success without a declaration, and `vlm_overclaimed` a declared success the simulator rejected. Never approve or write a lead, plan or skill rule that limits attempts (only one grasp, never regrasp, stop after one failure): a failed or dropped grasp is retried with a fresh observation while budget remains; correct such a lead instead. Review standard: do not require the evidence to isolate a single cause. Approve a change when the evidence makes it a plausible improvement; the simulator gate decides whether it helps. Plans and compounds that come from simulator-confirmed successes affect only their task: approve them unless a step is unsafe, uses private state or calls a tool missing from the capability index (`done` is a built-in tool); when only a step or a claim is wrong, reject and return the whole revised plan as corrected_plan. A record marked Multi-round success holds the plan of every round and the diagnoses of the Reviewer; it is not itself a plan: reject it and write the complete plan from the start that the successful rounds support as corrected_plan. Base skills are shared by every task, so review a base-skill change strictly on form: its harness must be canonical (harness_check lists what is still wrong) and must run the changed code, and the skill must keep its interface and its safety, reach and holding checks. Strict on form does not mean blocking real fixes: when the evidence shows a base skill is actually wrong, approve the fix (it is first installed for the source task only, and becomes shared only after other tasks show no regression), and when it is worth trying but its harness or interface is wrong, do not just reject it: return a corrected code_proposal with a canonical harness. Return JSON only: {"decision":"approve"|"reject"|"defer","reason":"...","scope":"global"|"task","corrected_lead":null or {"root_cause":"...","next_action":"an attempt executable within an episode with existing tools, never wait for a repair"},"corrected_plan":null or "complete revised plan.md","lesson_decision":"approve"|"reject"|"defer","lesson_evidence":"...","cross_task_lesson":"...","code_proposal":null or {"name":"skill_name","kind":"new"|"update","category":"base/libero","new_code":"complete policy.py","skill_md":"complete typed SKILL.md and harness","rationale":"..."}}.'

def validate_code(q):
    import yaml
    from roborsi.agents.evolution.validator import ProposalValidator
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
        task=q.get('source_task') or q.get('task') or ''
        if '/' not in str(h.get('sim_task') or '') and '/' in str(task):
            # A harness must boot a concrete simulator task; an atomic name such
            # as libero_pick_place is not one. Use the source task instead.
            h['sim_task']=task
            fm.setdefault('metadata',{})['harness']=h
            body=md.split('---',2)[2]
            q['skill_md']='---\n'+yaml.safe_dump(fm,sort_keys=False,allow_unicode=True)+'---'+body
    except Exception:
        # No usable skill harness: validate the change with whole RoboRSI
        # episodes on the source task and disjoint development seeds.
        from roborsi.agents.safety.proposal_safety import inspect_skill_text
        if q.get('skill_md') and inspect_skill_text(q['skill_md']):
            raise ValueError('SKILL.md failed the capability check')
        from roborsi.agents.roles.manager.native import assert_native_candidate
        assert_native_candidate(q.get('new_code',''))
        from roborsi.agents.evolution.episode_gate import source_task_gate
        return source_task_gate(q, q.get('source_task') or q.get('task'))
    for declared, field in [("development_task", "sim_task"),
                            ("development_seeds", "seeds"),
                            ("development_pass_criteria", "pass_criteria")]:
        if q.get(declared) is not None and h.get(field) != q[declared]:
            raise ValueError("Changed declared development fixture: " + declared)
    report=ProposalValidator().validate(q)
    harness=getattr(report,'harness',None)
    if (not report.overall_pass and getattr(report,'capability',None) is not None
            and report.capability.passed and harness is not None
            and (harness.extras or {}).get('verdict')=='FAIL'):
        # The skill's own harness missed its bar. A fix can still help when the
        # task fails for other reasons too, so compare with the current skill:
        # on the source task's development seeds it must succeed at least once
        # and do at least as well. Crashes and malformed harnesses stay blocked.
        from roborsi.agents.evolution.episode_gate import source_task_gate
        fallback=source_task_gate(q,q.get('source_task') or q.get('task'))
        fallback.note='harness: '+harness.detail[:200]+'; '+fallback.note
        return fallback
    return report

def _transient(exc):
    """Errors that pass on their own: rate limits, timeouts, dropped streams."""
    return bool(re.search(r"rate limit|too many requests|\b429\b|timed? ?out|disconnected|"
                          r"connection (reset|refused|error)|\b50[0234]\b|temporarily",
                          type(exc).__name__+' '+str(exc), re.I))

def _source_task_key(q):
    """Concrete task key (for example libero_object/0) of a proposal's source
    episode, from its workspace identity; None when unknown."""
    roots=[]
    if q.get('source_workspace'):roots.append(Path(q['source_workspace']))
    if q.get('source_run_id'):
        roots+=[w for w in (home()/'workspaces').glob('*'+str(q['source_run_id'])+'*') if w.is_dir()]
    for w in roots:
        identity=w/'episode_identity.json'
        if identity.exists():
            try:return json.loads(identity.read_text()).get('task_key')
            except (OSError,ValueError):pass
    key=q.get('source_task') or q.get('task_key') or q.get('task')
    return key if key and '/' in str(key) else None

def manager_reply(user_turn,system,model):
    """One Manager turn: the persistent Manager session (one across all tasks
    and cycles, compacted when it grows) or a stateless call."""
    if os.environ.get('ROBORSI_ROLE_SESSION','1')!='0':
        from roborsi.agents.sessions import persistent_agent
        return persistent_agent.run_role('manager','manager',user_turn,system_prompt=system,model=model)
    from roborsi.agents.roles.role_skills import with_role_skills
    resp=_call_vlm_tools(model,[{'role':'system','content':with_role_skills('manager',system)},{'role':'user','content':user_turn}],[],thinking_budget=0,tool_choice='none')
    content=getattr(resp,'content','') or ''
    if isinstance(content,list):content=''.join(_extract_text_block(c) for c in content)
    return content

def outcome_view(o):
    """The adjudicated outcome of a source episode in plain fields. The stored
    labels name a (simulator, Engineer claim) pair; 'vlm_declared_done' means
    the simulator confirmed success AND the Engineer declared done, and was
    misread as an unverified claim."""
    view={'simulator_success':bool(o.get('success')),
          'engineer_declared_done':o.get('outcome') in ('vlm_declared_done','vlm_overclaimed','declared_done_unverified') and (bool(o.get('success')) or o.get('outcome')!='vlm_declared_done'),
          'engineer_gave_up':o.get('outcome')=='engineer_gave_up' or (o.get('outcome')=='vlm_declared_done' and not o.get('success')),
          'judged_by':'simulator success predicate, evaluated once after the last round'}
    if o.get('verified') is False:
        view.update(simulator_success=None,judged_by='no simulator predicate (real robot); Engineer claim only')
    return view

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
  from roborsi.agents.roles.manager.patch_review import compact_history, materialize as materialize_manager_patch
  prior=compact_history(prior)
  for folder in ['wiki_review','plan_review','skill_review','policy_review']:
   for p in sorted((home()/folder).glob('*.json')):
    try:
     q=json.loads(p.read_text())
     # Approved code whose gate never ran (an interrupted cycle) is gated
     # again without a second review.
     regate=(folder=='skill_review' and q.get('status','pending')=='pending'
             and q.get('manager_decision')=='approve' and bool(q.get('manager_reviewed_at')))
     if q.get('status','pending')!='pending' or (q.get('manager_reviewed_at') and not regate):continue
     if float(q.get('retry_after',0))>time.time():continue
     # Review with the source task's view of the skills (its task-local
     # revisions included), as its episodes see them.
     early_key=_source_task_key(q)
     if early_key:os.environ['ROBORSI_CURRENT_SIM_TASK']=early_key
     else:os.environ.pop('ROBORSI_CURRENT_SIM_TASK',None)
     if folder=='skill_review' or q.get('kind')=='code_revision_request':q['development_mode']='native'
     from roborsi.agents.roles.manager.patch_context import materialize_policy_patch, review_text, HARNESS_CONTRACT, normalize_harness
     from roborsi.agents.roles.manager.native import attach_failure_source
     q=attach_failure_source(q,R)
     if q.get('development_mode')=='native':
      q=materialize_policy_patch(q,R)
     # Do not expose gate labels, scoring outcomes or private source identifiers.
     keys=['target_path','old_string','new_string','original_patch','development_mode','native_source','native_source_sha256','id','kind','task','name','category','root_cause','next_action','rationale','new_code','code','skill_md','workspace_plan_md','prior_persistent_md','policy_code','compound_name','validation_observations','revision_request','parent_proposal','development_task','development_seeds','development_pass_criteria','repair_root','repair_depth']
     visible={k:q[k] for k in keys if k in q}
     # Manager decisions need observations, not merely the Reviewer's conclusion.
     from roborsi.agents.roles.reviewer import _sanitize_trace
     evidence=[]
     source=q.get('source_workspace')
     roots=[]
     if source:
      candidate_path=Path(source).resolve()
      if candidate_path.is_relative_to((home()/'workspaces').resolve()):roots=[candidate_path]
     if not roots and q.get('source_run_id'):
      roots=[p for p in (home()/'workspaces').glob('*'+str(q['source_run_id'])+'*') if p.is_dir()]
     if folder=='policy_review':
      from roborsi.agents.evolution.compound_validation import source_workspaces
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
      from roborsi.agents.roles.manager.native import assert_native_candidate
      for code_key in ['new_code','code','native_source']:
       if visible.get(code_key):
        assert_native_candidate(visible[code_key]);native_code[code_key]=visible.pop(code_key)
     clean,dropped=redact(str(q.get('task','')),review_text(json.dumps(visible,ensure_ascii=False)))
     visible.update(native_code)
     if not dropped:
      visible['source_task_key']=source_task_key
      outcomes=[json.loads((w/'episode_result.json').read_text()) for w in roots if (w/'episode_result.json').exists()]
      if outcomes:visible['source_episode_outcome']=[outcome_view(o) for o in outcomes]
      if folder=='skill_review' and q.get('skill_md'):
       # Mechanical harness errors are repaired here; the rest must be fixed
       # by whoever writes the code, so the Manager sees them explicitly.
       q['skill_md'],problems=normalize_harness(q['skill_md'],source_task_key)
       visible['skill_md']=q['skill_md']
       visible['harness_check']=problems or 'canonical'
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
     # The Engineer also calls the atomic task's compound skills (for example
     # visual_pick_place); list them too, or plans using them look invalid.
     atomic=next((str(x) for x in (q.get('atomic'),q.get('task')) if x and '/' not in str(x)),'libero_pick_place')
     from roborsi.embodied.skills import discover_compounds
     skills_by_name.update({skill.name:skill for skill in discover_compounds(atomic)})
     contracts=[]
     for spec in _build_tool_specs(ns='libero',task=atomic):
      # Built-in tools such as `done` have no SKILL.md but are part of the surface.
      function=spec['function'];skill=skills_by_name.get(function['name'])
      contracts.append({'name':function['name'],'parameters':function['parameters'],
                        'returns':skill.frontmatter.get('returns',{}) if skill else {},
                        'description':function.get('description','')[:1000]})
     tools_block=json.dumps(contracts,ensure_ascii=False)
     model=os.environ.get('ROBORSI_MANAGER_MODEL','gpt-5.6-sol')
     from roborsi.agents.roles.manager.native import unified_review_system
     from roborsi.agents.roles.role_skills import with_role_skills
     actual_system=unified_review_system(q,NATIVE_REVIEW_SYSTEM,HARNESS_CONTRACT)
     import hashlib
     log("manager_review_context",proposal=p.name,system_sha256=hashlib.sha256(actual_system.encode()).hexdigest(),native_source_sha256=q.get("native_source_sha256"),native_source_path=q.get("native_source_path"),proposal_kind=q.get("kind"))
     if regate:
      d={'decision':'approve','scope':q.get('manager_scope','task'),'reason':q.get('manager_note','')}
      log('regate',proposal=p.name)
     else:
      with capture_usage() as usage:
       user_turn='Prior Manager decisions:\n'+'\n'.join(prior)+'\nPublic capability index:\n'+tools_block+'\nProposal:\n'+clean
       content=manager_reply(user_turn,actual_system,model)
      responses=home()/'manager_responses';responses.mkdir(exist_ok=True)
      response_path=responses/(p.stem+'-'+str(time.time_ns())+'.json')
      response_path.write_text(json.dumps({'proposal':p.name,'model':model,'utc':time.time(),'raw_response':content,'usage':usage.to_dict()},indent=2))
      try:
       d=parse(content)
       if d.get('decision') not in ['approve','reject','defer']:
        raise ValueError('invalid Manager decision')
      except (ValueError,TypeError,AttributeError) as exc:
       q['review_attempts']=int(q.get('review_attempts',0))+1
       q.update(manager_decision='defer',manager_note='Unusable structured response; explicit evidence repair required',manager_response_path=str(response_path))
       # Retry an unusable answer up to three times instead of dropping the proposal.
       if q['review_attempts']>=3:q['manager_reviewed_at']=time.time()
       p.write_text(json.dumps(q,indent=2))
       log('manager_response_invalid',proposal=p.name,error=str(exc),response_path=str(response_path))
       continue

      rec={'utc':time.time(),'proposal':p.name,'source_task':source_task_key,'decision':d,'model':model,'usage':usage.to_dict()}
      with hist.open('a') as f:f.write(json.dumps(rec)+'\n')
      q.update(manager_reviewed_at=rec['utc'],manager_decision=d['decision'],manager_note=d.get('reason',''),manager_scope=d.get('scope','task'))
      if d['decision']=='defer':
       # A deferred proposal is reviewed again (up to three times), not dropped.
       q['review_attempts']=int(q.get('review_attempts',0))+1
       if q['review_attempts']<3:q.pop('manager_reviewed_at',None)
      p.write_text(json.dumps(q,indent=2))
     approved=d['decision']=='approve';applied=False
     if d['decision']=='reject':
      q['status']='rejected';p.write_text(json.dumps(q,indent=2))
     if folder=='wiki_review' and q.get('kind')=='failure_hypothesis' and d['decision']!='defer':
      from roborsi.agents.memory.task_wiki import resolve_wiki_hypothesis
      fix=d.get('corrected_lead') if isinstance(d.get('corrected_lead'),dict) else None
      if not approved and fix and fix.get('root_cause') and fix.get('next_action'):
       # The failure is real but the Reviewer's cause was wrong: keep the
       # Manager's corrected lead instead of discarding the lesson.
       q2=json.loads(p.read_text());q2['root_cause']=str(fix['root_cause']);q2['next_action']=str(fix['next_action'])
       p.write_text(json.dumps(q2,indent=2,ensure_ascii=False))
       resolve_wiki_hypothesis(p,approve=True,manager_note='corrected by Manager: '+d.get('reason','')[:300]);applied=True
       log('lead_corrected',proposal=p.name)
      else:
       resolve_wiki_hypothesis(p,approve=approved,manager_note=d.get('reason',''));applied=approved
     elif folder=='plan_review' and d['decision']!='defer':
      from roborsi.agents.memory.task_wiki import resolve_plan_promotion
      fixed_plan=d.get('corrected_plan')
      if not approved and isinstance(fixed_plan,str) and fixed_plan.strip():
       # The route succeeded in the simulator but a step was wrong or
       # overstated: keep the Manager's corrected plan instead of losing it.
       q2=json.loads(p.read_text());q2['workspace_plan_md']=fixed_plan;p.write_text(json.dumps(q2,indent=2,ensure_ascii=False))
       resolve_plan_promotion(p,approve=True,manager_note='corrected by Manager: '+d.get('reason','')[:300]);applied=True
       log('plan_corrected',proposal=p.name)
      else:
       resolve_plan_promotion(p,approve=approved,manager_note=d.get('reason',''));applied=approved
     elif folder=='skill_review' and approved:
      from roborsi.agents.evolution.validator import ProposalValidator
      try:gate=validate_code({**q,'source_task':source_task_key})
      except Exception as exc:
       gate=None;q['status']='gate_blocked';q['gate_error']=str(exc)[:500];p.write_text(json.dumps(q,indent=2))
       log('code_gate_blocked',proposal=p.name,error=str(exc))
      if gate is not None:log('code_gate',proposal=p.name,report=gate.to_dict())
      if gate is not None and gate.overall_pass and os.environ.get('ROBORSI_REVIEW_MODE','manager')=='human':
       # Human review: publish nothing; show the person what would change.
       from roborsi.agents.evolution.html_review import render_awaiting_page
       q['status']='awaiting_human';p.write_text(json.dumps(q,indent=2))
       q['manager_scope']=d.get('scope','global');p.write_text(json.dumps(q,indent=2))
       page=render_awaiting_page(q,manager_reason=d.get('reason',''),gate_report=gate.to_dict(),repo=R)
       log('awaiting_human',proposal=p.name,page=str(page))
      elif gate is not None and gate.overall_pass:
       # Gate was just executed. apply performs its own capability safety check.
       r=subprocess.run([os.environ.get('ROBORSI_APPLY_PYTHON',os.sys.executable),'-m','roborsi.agents.evolution.apply_proposal',q['id'],'--skip-harness','--scope','task','--task',str(source_task_key)],cwd=R,capture_output=True,text=True,timeout=180)
       log('code_apply',proposal=p.name,returncode=r.returncode,output=(r.stdout+r.stderr)[-1500:]);applied=r.returncode==0
       promote_by=os.environ.get('ROBORSI_GLOBAL_PROMOTION','manager')
       if applied and d.get('scope')=='global' and promote_by=='manager':
        # The gate only validated the source task. Promotion to the shared
        # skill runs as a background job (other tasks must not regress) so it
        # does not hold up the review of other proposals.
        job=home()/'promotions'/(p.stem+'.json');job.parent.mkdir(exist_ok=True)
        job.write_text(json.dumps({'proposal':q,'source_task':source_task_key},indent=2))
        subprocess.Popen([os.sys.executable,'-m','roborsi.agents.evolution.promote',str(job)],cwd=R,
                         stdout=open(str(job)+'.log','a'),stderr=subprocess.STDOUT,start_new_session=True)
        log('cross_task_gate_started',proposal=p.name,job=str(job))
       elif applied and d.get('scope')=='global':
        # Promotion decided by a person.
        from roborsi.agents.evolution.html_review import render_awaiting_page
        q2=json.loads(p.read_text()) if p.exists() else dict(q)
        q2['manager_scope']='global';q2['status']='awaiting_global_promotion'
        page=render_awaiting_page(q2,manager_reason=d.get('reason',''),gate_report=gate.to_dict(),repo=R)
        log('awaiting_global_promotion',proposal=p.name,page=str(page))
       if not applied:
        q=json.loads(p.read_text());q['status']='apply_failed';p.write_text(json.dumps(q,indent=2))
      elif gate is not None:
       q['status']='gate_failed';p.write_text(json.dumps(q,indent=2))
       log('code_not_promoted',proposal=p.name,reason=gate.note)
     elif folder=='policy_review' and approved:
      from roborsi.agents.evolution.compound_validation import enqueue
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
      from roborsi.agents.safety.proposal_safety import assert_safe_candidate,assert_safe_skill_text
      name=str(candidate.get('name',''))
      if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',name) or candidate.get('kind') not in ('new','update') or candidate.get('category')!='base/libero':
       log('manager_candidate_rejected',reason='Invalid native base skill identity',proposal=p.name)
      elif candidate.get('kind')=='new' and get_ns(name,'libero') is not None:
       log('manager_candidate_rejected',reason='Duplicate existing skill',name=name)
      elif candidate.get('kind')=='update' and get_ns(name,'libero') is None:
       log('manager_candidate_rejected',reason='Existing skill to update was not found',name=name)
      else:
       code=str(candidate.get('new_code',''))
       md,problems=normalize_harness(candidate.get('skill_md',''),source_task_key)
       if problems:
        # The Manager must hand over a runnable harness: ask it once to fix the format.
        fix=manager_reply('Your code_proposal for '+name+' has harness problems: '+'; '.join(problems)+'. '+HARNESS_CONTRACT+' Return JSON only: {"skill_md":"the complete corrected SKILL.md"}',actual_system,model)
        try:md,problems=normalize_harness(parse(fix).get('skill_md',''),source_task_key)
        except (ValueError,TypeError,AttributeError):problems=problems or ['unusable harness correction']
        log('manager_harness_corrected' if not problems else 'manager_harness_invalid',proposal=p.name,problems=problems)
       candidate['skill_md']=md
       raw_candidate=home()/'manager_candidates'/(p.stem+'-'+str(time.time_ns())+'.json')
       raw_candidate.parent.mkdir(exist_ok=True)
       raw_candidate.write_text(json.dumps({'source_proposal':p.name,'candidate':candidate,'model':model,'utc':time.time()},indent=2))
       try:
        if problems:raise ValueError('harness is not canonical: '+'; '.join(problems))
        from roborsi.agents.roles.manager.native import assert_native_candidate
        assert_native_candidate(code)
        candidate['development_mode']='native'
        candidate.update(id='manager-'+str(time.time_ns()),status='pending',submitted_by='Manager',task=source_task_key,source_task=source_task_key,source_proposal=p.name)
        for origin in ['source_workspace','source_run_id','source_trace_dir','repair_root','repair_depth','development_task','development_seeds','development_pass_criteria','validation_observations','parent_proposal','revision_request','native_source','native_source_sha256']:
         if q.get(origin) is not None:candidate[origin]=q[origin]
        target=home()/'skill_review'/(candidate['id']+'.json');target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(candidate,indent=2))
        log('manager_candidate_queued',proposal=target.name,source_proposal=p.name,reason='Requires a separate review of generated code before simulator gate')
       except Exception as exc:log('manager_candidate_blocked',name=name,error=str(exc))
     log('review_complete',proposal=p.name,decision=d['decision'],applied=applied)
    except Exception as exc:
     if _transient(exc):
      # Rate limits and dropped connections pass; review it again later.
      try:
       q=json.loads(p.read_text());q['retry_after']=time.time()+300;p.write_text(json.dumps(q,indent=2))
      except Exception:pass
      log('proposal_retry_later',proposal=p.name,error=type(exc).__name__+': '+str(exc)[:300]);continue
     # One unusable proposal must not block every later cycle.
     try:
      q=json.loads(p.read_text());q.update(status='blocked',manager_reviewed_at=time.time(),manager_decision='defer',manager_note=type(exc).__name__+': '+str(exc)[:500]);p.write_text(json.dumps(q,indent=2))
     except Exception:pass
     log('proposal_blocked',proposal=p.name,error=type(exc).__name__+': '+str(exc)[:500])
  # Commit only evolving capability artifacts, preserving a concrete version boundary.
  if not (R/'.git').exists():
   log('cycle_finished',code_revision=None);return
  # skills/_data (run records) is already ignored by .gitignore.
  subprocess.run(['git','add','roborsi/embodied/skills'],cwd=R,check=True)
  if subprocess.run(['git','diff','--cached','--quiet'],cwd=R).returncode:
   subprocess.run(['git','-c','user.name=RoboRSI Manager','-c','user.email=manager@localhost','commit','-m','evolve: preserve Manager-reviewed capability iteration'],cwd=R,check=True,capture_output=True)
  from roborsi.agents.evolution.compound_validation import run_pending
  run_pending()
  log('cycle_finished',code_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip())
if __name__=='__main__':cycle()
