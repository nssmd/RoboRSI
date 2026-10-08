"""Planner instructions and contracts for the actual runtime tool namespace."""
import json


LIBERO_CONTRACT = '''LIBERO TOOL CONTRACT:
This scene uses one Franka arm. Reference only the exact AVAILABLE SKILLS and
their declared argument names, types, required fields and return contracts.
Do not add arm selectors or import parameter conventions from another robot.
Tool return fields are evidence only for what their descriptions establish:
motion completion, proprioceptive holding, visual identity and final task
completion are different claims. Do not silently treat one as another.
Use the current task instruction to choose the object and destination. Localize
them from current images; old plans, pixels and coordinates are not scene facts.
Prefer available dedicated grasp and placement skills. Follow their current
contracts and the task-routing block for support surfaces versus receptacles.
Choose a shape-appropriate dedicated grasp before considering manual recovery;
a manual gripper close is a fallback only after a grasp skill returns ok=False.
Do not invent a fixed transport quaternion or hand-written grasp sequence from
another embodiment. If a tool is unavailable, do not put it in the plan.
'''


def namespace_contract(ns):
    return LIBERO_CONTRACT if ns == 'libero' else ''


def system_for_namespace(base, ns):
    if ns != 'libero':
        return base
    marker = 'exec_python discipline:'
    assert base.count(marker) == 1, 'Unexpected Planner prompt shape'
    # Keep the existing role, output format, hierarchy and compound interface.
    # Replace only robot/tool-specific examples inappropriate for this namespace.
    header = base.split(marker, 1)[0].replace(
        'loop (find_pixel, gripper, move_to_pose, ...).',
        'loop through the registered runtime tools.')
    return header + LIBERO_CONTRACT


def render_contract(function, returns=None):
    record = {'description': function.get('description', ''),
              'parameters': function.get('parameters', {})}
    if returns is not None:
        record['returns'] = returns
    return '  ' + function['name'] + ': ' + json.dumps(record, ensure_ascii=False, sort_keys=True)
