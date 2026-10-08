"""Bounded repair routing for concrete rejected proposals; never publishes code."""
from pathlib import Path


def build_repair_request(proposal, review, proposal_name):
    if proposal.get('kind') not in ('new', 'update'):
        return None
    if review.get('decision') not in ('reject', 'defer'):
        return None
    if isinstance(review.get('code_proposal'), dict):
        return None
    if not str(review.get('reason') or '').strip():
        return None
    if proposal.get('submitted_by') == 'Manager development':
        return None
    try:
        if int(proposal.get('repair_depth', 0)) >= 1:
            return None
    except (TypeError, ValueError):
        return None
    code = proposal.get('new_code') or proposal.get('code')
    if not isinstance(code, str) or not code.strip():
        return None
    root = Path(str(proposal.get('repair_root') or proposal_name)).name
    request = dict(proposal)
    for key in list(request):
        if key.startswith('manager_'):
            request.pop(key)
    request.update(kind='code_revision_request', status='pending', new_code=code,
                   repair_root=root, repair_depth=1,
                   parent_proposal=proposal_name,
                   revision_request='Produce one concrete changed implementation addressing the independent review. '
                                    'Preserve existing validation criteria and declared development seeds. '
                                    'If public capabilities are insufficient, record the precise blocker. '
                                    'Do not publish; send changed code for separate review and simulator validation.')
    # Preserve declared development identities even when carried only in SKILL.md.
    import yaml
    try:
        fm = yaml.safe_load(str(proposal.get('skill_md', '')).split('---', 2)[1])
        harness = (fm.get('metadata') or {}).get('harness') or {}
    except (IndexError, AttributeError, TypeError, yaml.YAMLError):
        harness = {}
    if not isinstance(harness, dict):
        harness = {}
    seeds = harness.get('seeds')
    if 'development_seeds' not in request and isinstance(seeds, list) and seeds and all(type(s) is int for s in seeds):
        request['development_seeds'] = list(seeds)
    if 'development_task' not in request and isinstance(harness.get('sim_task'), str):
        request['development_task'] = harness['sim_task']
    if 'development_pass_criteria' not in request and isinstance(harness.get('pass_criteria'), dict):
        request['development_pass_criteria'] = dict(harness['pass_criteria'])
    return request
