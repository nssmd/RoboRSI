"""Flat-development treatment; role collaboration and code learning stay enabled."""
import os

FLAT_RULES = '''FLAT DEVELOPMENT CONDITION: operate with a goal and current public
observations. Do not create an ordered task/subskill tree, subgoal checklist,
recursive refinement agenda, or progress cursor. Select the next direct public
action from the current observation. The plan tool is unavailable. This replaces
only hierarchy-specific planning instructions; all perception, motion, tool
contracts, evidence and adjudication rules still apply. Planner, Engineer,
Reviewer and Manager remain separate roles. Flat reusable code is allowed and
must pass the ordinary independent review and simulator publication gates.'''


def flat_enabled():
    return os.environ.get('ROBORSI_REFINEMENT_MODE', 'hierarchical') == 'flat'


def flat_plan(planner, *, task, user_msg, recent_reflections, workspace, ns='robotwin'):
    from roborsi.agents import persistent_agent
    from roborsi.agents.planner import _skill_catalog, _extract_json_and_md
    from roborsi.agents.gt_firewall import redact
    from roborsi.agents.task_wiki import read_wiki
    catalog = _skill_catalog(task, user_msg, ns)
    catalog = "\n".join(line for line in catalog.splitlines() if line.strip().split(":", 1)[0] != "plan")
    wiki, _ = redact(task, read_wiki(task))
    reflections, _ = redact(task, recent_reflections)
    prompt = (FLAT_RULES + '\nYou are the Planner role. Describe the task objective, '
              'observable completion criteria and risks. Do not output action '
              'sequences, sub_goals or a task tree. Return one fenced JSON block '
              'with goal:string, success_criteria:list[string], '
              'candidate_skills:list[string], risks:list[string].')
    from roborsi.agents.planner_contract import namespace_contract
    prompt += '\n' + namespace_contract(ns) if ns == 'libero' else ''
    content = persistent_agent.run_role(
        'planner', task,
        'Current task instruction:\n' + user_msg + '\nAvailable public skills:\n' + catalog
        + '\nPublic experience:\n' + wiki + '\nRecent reflections:\n' + reflections,
        system_prompt=prompt, model=planner.model)
    parsed, _ = _extract_json_and_md(content)
    spec = {'goal': str(parsed.get('goal') or user_msg or task), 'sub_goals': [],
            'success_criteria': parsed.get('success_criteria', []),
            'candidate_skills': parsed.get('candidate_skills', []),
            'risks': parsed.get('risks', []), 'expected_steps': 12,
            'refinement_mode': 'flat'}
    for key in ['success_criteria', 'candidate_skills', 'risks']:
        value = spec[key]
        spec[key] = [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
    # Never persist model-returned hierarchical Markdown or subgoal arrays.
    brief = ('# Flat task brief\n\nGoal: ' + spec['goal'] + '\n\nObservable criteria: '
             + '; '.join(spec['success_criteria']) + '\n\nRisks: '
             + '; '.join(spec['risks']) + '\n\n' + FLAT_RULES + '\n')
    workspace.write_plan(brief)
    return spec
