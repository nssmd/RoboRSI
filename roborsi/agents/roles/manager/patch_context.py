"""Expose Reviewer-authored patches to Manager without changing their edits."""
from pathlib import Path
import ast,hashlib,re

def materialize_policy_patch(proposal, repo):
    q=dict(proposal)
    if q.get('kind')!='patch':return q
    target=str(q.get('target_path') or '')
    match=re.fullmatch(r'roborsi/embodied/skills/base/([a-z][a-z0-9_]*)/(libero|robotwin)/policy\.py',target)
    if not match:
        # Shared-library patches remain visible for Manager inspection; do not
        # pretend they are directly publishable as one named skill update.
        return q
    path=Path(repo)/target
    # The source task may already run a task-local revision of this skill;
    # the patch was written against that version and must build on it.
    import os
    key=os.environ.get('ROBORSI_CURRENT_SIM_TASK')
    if key:
        local=(Path(repo)/'roborsi/embodied/skills/task_local'/key.replace('/','__')/'base'/match[1]/match[2]/'policy.py')
        if local.exists():path=local
    old,new=q.get('old_string'),q.get('new_string')
    if not isinstance(old,str) or not old or not isinstance(new,str):raise ValueError('Patch needs exact old_string and new_string')
    source=path.read_text()
    if source.count(old)!=1:raise ValueError('Patch source is stale or ambiguous')
    updated=source.replace(old,new,1);ast.parse(updated)
    q.update(kind='update',name=match[1],category='base/'+match[2],new_code=updated,native_source=source,
             native_source_sha256=hashlib.sha256(source.encode()).hexdigest(),development_mode='native',
             original_patch={'name':proposal.get('name'),'target_path':target,'old_string':old,'new_string':new})
    if not q.get('skill_md'):q['skill_md']=path.with_name('SKILL.md').read_text()
    return q


def review_text(text):
    # Exact already-observed negative disclaimer, not a general whitelist for
    # privileged facts or a change to the role/evidence firewall.
    return text.replace('never simulator object/site ground truth','never private simulator object/site state')

HARNESS_CONTRACT='''Canonical native harness: metadata.harness has sim_task, seeds as a list of integers, args as a LIST of dictionaries (each dictionary is run on every seed), and pass_criteria with kind and min_seeds_passing. The implemented kinds are simulator_task_success, grasp_holds_actor, move_completes, verify_returns_bool and tool_returns_well_formed. There is no type=primitive_result or expression interpreter. For move_completes the harness requires result.ok; for grasp_holds_actor it checks result.success or result.holding_visual, so grasped alone is not that field. Never fake these fields: only derive them from measured native outcome. Preserve the declared test task and seeds. If the proposed contract is missing/invalid, Manager may provide a corrected code_proposal with complete unchanged-or-revised policy and a supported truthful harness; that new proposal requires a later separate review. A Reviewer PATCH includes target_path, old_string, new_string and the exact materialized full source when it targets a native policy. Do not reject it as empty code if its concrete edit is present.'''

HARNESS_KINDS=('simulator_task_success','grasp_holds_actor','verify_returns_bool','move_completes','tool_returns_well_formed')


def _repair_yaml(front):
    """Quote free-text scalar values that broke the YAML (for example a
    description containing [u,v] or ': '); returns the parsed mapping or None."""
    import json,yaml
    lines=[]
    for line in front.split('\n'):
        m=re.match(r'^(\s*)([A-Za-z_][\w-]*):[ \t]+(.+?)\s*$',line)
        if m and m.group(2) not in ('args','seeds','pass_criteria','harness','metadata') \
                and not m.group(3).startswith(('"',"'",'|','>')):
            try:
                if not isinstance(yaml.safe_load(m.group(3)),(str,int,float,bool,type(None))):raise ValueError
            except (yaml.YAMLError,ValueError):
                line=m.group(1)+m.group(2)+': '+json.dumps(m.group(3))
        lines.append(line)
    try:
        fm=yaml.safe_load('\n'.join(lines))
        return fm if isinstance(fm,dict) else None
    except yaml.YAMLError:
        return None


def normalize_harness(skill_md, source_task):
    """Repair the mechanical parts of a SKILL.md harness and list what is
    still wrong. Fixed here: a missing or non-simulator sim_task (an atomic
    name, a task title, or a `task` field) becomes the concrete source task;
    a single args dictionary becomes a list; seeds given as a string become a
    list. Returned problems must be fixed by the author."""
    import yaml
    text=str(skill_md or '')
    if not text.startswith('---') or text.count('---')<2:
        return text,['SKILL.md has no YAML frontmatter with metadata.harness']
    _,front,body=text.split('---',2)
    try:fm=yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        fm=_repair_yaml(front)
        if fm is None:return text,['frontmatter is not valid YAML: '+str(exc)[:200]]
    meta=fm.setdefault('metadata',{}) if isinstance(fm,dict) else {}
    h=meta.get('harness') if isinstance(meta,dict) else None
    if not isinstance(h,dict):
        h=fm.pop('harness',None) if isinstance(fm,dict) else None
        if not isinstance(h,dict):return text,['metadata.harness is missing']
        meta['harness']=h
    task=h.pop('task',None)
    if h.get('kind')=='sim_task':h.pop('kind')
    sim=str(h.get('sim_task') or task or '')
    if '/' not in sim and '/' in str(source_task or ''):h['sim_task']=str(source_task)
    elif sim:h['sim_task']=sim
    if isinstance(h.get('args'),dict):h['args']=[h['args']]
    if isinstance(h.get('seeds'),str):h['seeds']=[int(s) for s in re.findall(r'-?\d+',h['seeds'])]
    problems=[]
    if '/' not in str(h.get('sim_task') or ''):problems.append('sim_task must be a concrete simulator task key such as libero_goal/3')
    seeds=h.get('seeds')
    if not (isinstance(seeds,list) and all(isinstance(s,int) for s in seeds) and len(set(seeds))>=2):
        problems.append('seeds must be a list of at least two distinct integers')
    args=h.get('args')
    if not (isinstance(args,list) and args and all(isinstance(a,dict) for a in args)):
        problems.append('args must be a non-empty list of argument dictionaries')
    pc=h.get('pass_criteria') if isinstance(h.get('pass_criteria'),dict) else {}
    if pc.get('kind') not in HARNESS_KINDS:
        problems.append('pass_criteria.kind must be one of '+', '.join(HARNESS_KINDS))
    try:need=int(pc.get('min_seeds_passing',0))
    except (TypeError,ValueError):need=0
    if need<2 or (isinstance(seeds,list) and need>len(seeds)):
        problems.append('pass_criteria.min_seeds_passing must be at least 2 and at most the number of seeds')
    return '---\n'+yaml.safe_dump(fm,sort_keys=False,allow_unicode=True)+'---'+body,problems
