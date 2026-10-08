"""Bounded public execution history; model declarations are never confirmation."""
from __future__ import annotations
import json
import math
import re

HISTORY_RULES = (
    'Earlier rounds share the current scene, but their prose and done declarations '
    'are claims, not confirmed task completion. Tool ok only reports that tool\'s '
    'contract: successful pointing/localization does not prove the requested spatial '
    'relation, an empty gripper does not prove placement, and release does not prove '
    'containment. Inspect current public observations for unresolved conditions. '
    'Do not repeat manipulation that current hold/identity evidence forbids. '
    'If an earlier round contains only a completion declaration, it adds no new '
    'observation. A task cannot become better verified by repeating that declaration.'
)

FIELDS = ('ok', 'grasped', 'holding', 'reached', 'released', 'placed',
          'gripper_opened', 'visual_verified', 'identity_verified',
          'do_not_regrasp', 'motion_ok')
CONTAINMENT_FIELDS = ('verified', 'placed', 'inside_xy', 'below_rim',
                      'object_cloud_distinct')
MAX_HISTORY_CHARS = 4096
EMPTY_HISTORY = '{"public_observations":[],"completion_declarations_not_evidence":[],"history_unavailable":true}'


def public_round_history(trace: list[dict], round_id: int) -> str:
    if not isinstance(trace, (list, tuple)):
        return EMPTY_HISTORY
    if type(round_id) is not int or not 0 <= round_id <= 10000:
        return EMPTY_HISTORY
    observations = []
    declarations = []
    failures = []
    for index, event in enumerate(trace[-2048:]):
        index += max(0,len(trace)-2048)
        if not isinstance(event, dict) or event.get('episode_round', round_id) != round_id:
            continue
        call = event.get('tool_call') or {}
        if not isinstance(call, dict):
            continue
        name = call.get('tool')
        step = event.get('step')
        step = step if type(step) is int and 0 <= step <= 1000000 else None
        if name == 'done':
            args = call.get('args')
            claimed = args.get('success') if isinstance(args, dict) else None
            declarations.append({'step': step,
                                 'claimed_success': claimed if type(claimed) is bool else None,
                                 'new_execution_evidence': False})
            declarations = declarations[-4:]
            continue
        result = event.get('result')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', name) or not isinstance(result, dict):
            continue
        public = {key: result[key] for key in FIELDS if type(result.get(key)) is bool}
        state = result.get('gripper_state')
        if isinstance(state,str) and state in ('open','held','closed_empty','ambiguous'):
            public['gripper_state'] = state
        for key in ('u','v'):
            value = result.get(key)
            if type(value) in (int,float) and math.isfinite(value) and abs(value) <= 100000:
                public[key] = value
        containment = result.get('post_release_visual_containment')
        if isinstance(containment, dict):
            public['post_release_visual_containment'] = {
                key: containment[key] for key in CONTAINMENT_FIELDS if type(containment.get(key)) is bool}
        if not public:
            continue
        # Do not copy retrieved policy source, arbitrary narrative summaries,
        # hidden adjudication labels or simulator internals into future plans.
        row = {'trace_index': index, 'step': step, 'tool': name,
               'public_return': public}
        observations.append(row)
        if any(result.get(k) is False for k in FIELDS) or result.get('do_not_regrasp') is True or any(v is False for v in public.get('post_release_visual_containment',{}).values()):
            failures.append(row)
    selected = {r['trace_index']: r for r in failures[-8:] + observations[-12:]}
    data = {'round': round_id, 'completion_declarations_not_evidence': declarations,
            'public_observations': [selected[i] for i in sorted(selected)],
            'observation_scope': 'historical typed public returns; free text omitted; re-observe current state',
            'history_truncated':len(trace)>2048 or len(selected)<len(observations)}
    important = {r['trace_index'] for r in failures[-8:]}
    while True:
        encoded=json.dumps(data,ensure_ascii=True,separators=(',',':'),allow_nan=False)
        if len(encoded)<=MAX_HISTORY_CHARS:return encoded
        rows=data['public_observations']
        if not rows:return EMPTY_HISTORY
        remove=next((i for i,r in enumerate(rows) if r['trace_index'] not in important),0)
        rows.pop(remove);data['history_truncated']=True
