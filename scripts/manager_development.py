"""One bounded candidate-development call per explicit repair request.

Independent Manager review and existing simulation gate still control publication.
"""
import ast,json,re,time
from pathlib import Path
from roborsi.embodied.paths import home
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools,capture_usage
from roborsi.channels.core.agent import _extract_text_block
from roborsi.agents.proposal_safety import assert_safe_candidate,assert_safe_skill_text

SYSTEM=''' EXACT runtime ABI (do not omit the import or state argument):
from roborsi.embodied.agent_loop.rollout import _dispatch_tool
def dispatch_runtime(state, args):
    result, observation = _dispatch_tool(state, "look", {"camera": "head"})
    return result, observation
Every _dispatch_tool call has exactly (state, literal_tool_name, args_dict). Every exit returns a TWO-ELEMENT tuple (result_dict, observation), never observation alone. Use SKILL.md frontmatter args mapping with typed entries for ALL candidate inputs (not a parameters field); include name, kind: base, robot: NAMESPACE, description, args, returns, and metadata.harness. Do not invent checks on undeclared return fields. This example only documents the ABI; develop the requested behavior, not a trivial look skill. Development requests are design tasks, not claims of validated behavior. If code is flawed, specify a changed implementation that can be tested instead of requiring the unwritten candidate to have already passed. You are the code-development step of RoboRSI Manager. You receive sanitized public execution evidence, exact tool contracts, and a failed proposal review. Produce one concrete changed reusable code candidate for independent review and simulator validation. An untested candidate is allowed: prior successful execution of this new candidate is not a prerequisite for proposing it. Do not claim it is validated or approved. If required public capabilities are absent, return a precise capability blocker instead of inventing them. Do not lower or bypass holding, geometry, reachability, collision or release gates. Do not change simulator adjudication. Use only synchronous def dispatch_runtime(state,args) and literal _dispatch_tool calls, each returns(result_dict,observation); every exit must return an actual public observation. No state inspection, private helpers, filesystem, network, exec_python, dynamic tool names, or invented result fields. Preserve unknown outcomes as unknown. Keep code compact and attempts bounded. Use an unused new base skill name. SKILL.md requires metadata.harness with sim_task, >=2 distinct development seeds, args as a list of dictionaries, pass_criteria.kind=simulator_task_success and min_seeds_passing>=2. Preserve development task/seeds if the request declares them. Return JSON only: {"candidate":null or {"name":"...","kind":"new","category":"base/NAMESPACE","new_code":"...","skill_md":"...","rationale":"..."},"blocker":"precise reason if candidate is null"}.'''

def assert_runtime_contract(code):
    tree=ast.parse(code)
    imported=any(isinstance(n,ast.ImportFrom) and n.module=='roborsi.embodied.agent_loop.rollout' and any(a.name=='_dispatch_tool' and a.asname in (None,'_dispatch_tool') for a in n.names) for n in tree.body)
    if not imported:raise ValueError('Missing explicit public dispatcher import')
    fn=next((n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='dispatch_runtime'),None)
    if fn is None or [a.arg for a in fn.args.args]!=['state','args']:raise ValueError('Expected synchronous dispatch_runtime(state, args)')
    calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_dispatch_tool']
    if not calls:raise ValueError('No public dispatch call')
    for call in calls:
        if len(call.args)!=3 or call.keywords or not isinstance(call.args[0],ast.Name) or call.args[0].id!='state' or not isinstance(call.args[1],ast.Constant) or not isinstance(call.args[1].value,str):raise ValueError('Expected _dispatch_tool(state, literal_name, args_dict)')
    returns=[n for n in ast.walk(fn) if isinstance(n,ast.Return)]
    if not returns or any(not isinstance(n.value,ast.Tuple) or len(n.value.elts)!=2 for n in returns):raise ValueError('Every exit must return (result_dict, observation)')


def develop_once(request, proposal_name, sanitized_proposal, contracts, review, namespace, model):
    if request.get('kind')!='code_revision_request':return None
    root=home()/'manager_development';root.mkdir(exist_ok=True)
    # Claim before provider call: uncertain transport outcomes must not auto-retry.
    marker=root/(proposal_name+'.attempt.json')
    if marker.exists():return None
    with marker.open('x') as f:json.dump({'utc':time.time(),'proposal':proposal_name,'state':'started'},f)
    try:
        system=SYSTEM.replace('NAMESPACE',namespace)
        from roborsi.agents.flat_refinement import flat_enabled, FLAT_RULES
        if flat_enabled():system += '\n\n' + FLAT_RULES
        with capture_usage() as usage:
            response=_call_vlm_tools(model,[{'role':'system','content':system},{'role':'user','content':json.dumps({'public_contracts':contracts,'public_proposal':json.loads(sanitized_proposal),'review':review},ensure_ascii=False)}],[],thinking_budget=0,tool_choice='none')
        raw=getattr(response,'content','') or ''
        if isinstance(raw,list):raw=''.join(_extract_text_block(c) for c in raw)
        (root/(proposal_name+'.response.json')).write_text(json.dumps({'raw_response':raw,'usage':usage.to_dict(),'model':model,'utc':time.time()},indent=2))
        stripped=re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip())
        result=json.loads(stripped);candidate=result.get('candidate')
        if candidate is None:
            marker.write_text(json.dumps({'state':'deferred','blocker':result.get('blocker'),'utc':time.time()},indent=2));return {'state':'deferred','blocker':result.get('blocker')}
        (root/(proposal_name+'.candidate.json')).write_text(json.dumps(candidate,indent=2))
        name=candidate.get('name','')
        if not re.fullmatch(r'[a-z][a-z0-9_]{1,39}',name) or candidate.get('kind')!='new' or candidate.get('category')!='base/'+namespace:raise ValueError('Invalid candidate identity')
        from roborsi.embodied.skills import get_ns
        if get_ns(name,namespace) is not None:raise ValueError('Name already published')
        code=candidate.get('new_code','');md=candidate.get('skill_md','')
        from skill_schema_format import normalize_arg_mapping
        md, schema_changed = normalize_arg_mapping(md)
        if schema_changed:
            candidate['skill_md'] = md
            (root/(proposal_name+'.schema-normalized.json')).write_text(json.dumps({'candidate':candidate,'change':'equivalent named argument list to mapping; policy and harness criteria unchanged'},indent=2))
        if code.strip()==str(request.get('new_code') or request.get('code') or '').strip():raise ValueError('Unchanged code candidate')
        assert_safe_candidate(code,namespace=namespace,candidate_name=name);assert_safe_skill_text(md)
        assert_runtime_contract(code)
        import yaml
        frontmatter=yaml.safe_load(md.split('---',2)[1]);harness=frontmatter.get('metadata',{}).get('harness',{})
        if not isinstance(frontmatter.get('args'),dict) or not frontmatter['args']:raise ValueError('Missing typed SKILL.md args mapping')
        seeds=harness.get('seeds',[])
        assert len(set(seeds))>=2 and all(type(s) is int for s in seeds)
        assert harness.get('pass_criteria',{}).get('kind')=='simulator_task_success'
        assert int(harness['pass_criteria'].get('min_seeds_passing',0))>=2
        assert isinstance(harness.get('args'),list) and harness['args'] and all(isinstance(a,dict) for a in harness['args'])
        declared=request.get('development_seeds')
        if declared is not None and seeds!=declared:raise ValueError('Changed declared development seeds')
        if request.get('development_task') and harness.get('sim_task')!=request['development_task']:raise ValueError('Changed declared development task')
        if request.get('development_pass_criteria') is not None and harness.get('pass_criteria')!=request['development_pass_criteria']:raise ValueError('Changed declared pass criteria')
        if not set().union(*(set(a) for a in harness['args'])).issubset(frontmatter['args']):raise ValueError('Harness uses undeclared skill args')
        pid='manager-dev-'+str(time.time_ns())
        candidate.update(id=pid,status='pending',submitted_by='Manager development',submitted_at=time.time(),source_proposal=proposal_name)
        for key in ['task','source_workspace','source_run_id','source_trace_dir','repair_root','repair_depth','development_seeds','development_task','development_pass_criteria']:
            if request.get(key) is not None:candidate[key]=request[key]
        queue=home()/'skill_review';queue.mkdir(exist_ok=True)
        target=queue/(pid+'.json');tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(candidate,indent=2));tmp.replace(target)
        marker.write_text(json.dumps({'state':'queued_for_independent_review','candidate':str(target),'utc':time.time()},indent=2))
        return {'state':'queued_for_independent_review','candidate':str(target)}
    except Exception as exc:
        marker.write_text(json.dumps({'state':'blocked','error':str(exc),'utc':time.time()},indent=2))
        return {'state':'blocked','error':str(exc)}
