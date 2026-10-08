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
