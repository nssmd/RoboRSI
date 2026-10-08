"""Native Manager proposals retain the historical code-update path and gate."""
import ast
import hashlib
import re
from pathlib import Path
from roborsi.agents.gt_firewall import GT_PATTERNS

def attach_failure_source(proposal, repo):
    """Attach current public implementation to a Reviewer's named tool fault.

    The diagnosis remains a hypothesis. Never replace a submitted candidate,
    its source, task identity, or existing validation contract.
    """
    q = dict(proposal)
    if q.get('kind') != 'failure_hypothesis' or q.get('native_source'):
        return q
    match = re.match(r'^\[TOOL_BUG ([a-z][a-z0-9_]{0,47}) \d+/\d+\]',
                     str(q.get('root_cause') or ''))
    if not match:
        return q
    root = Path(repo).resolve()
    source = root / 'roborsi/embodied/skills/base' / match[1] / 'libero/policy.py'
    if not source.is_file() or not source.resolve().is_relative_to(root):
        return q
    document = source.with_name('SKILL.md')
    if not document.is_file() or not document.resolve().is_relative_to(root):
        return q
    content = source.read_text(encoding='utf-8')
    q.update(development_mode='native', native_source=content,
             native_source_path=str(source.relative_to(root)),
             native_source_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest())
    q.setdefault('skill_md', document.read_text(encoding='utf-8'))
    return q


def unified_review_system(proposal, base_system, harness_contract, flat_rules=''):
    """One review path for lessons, plans, native changes and compounds."""
    system = request_system(proposal, base_system) + '\n' + harness_contract
    if proposal.get('kind') == 'failure_hypothesis':
        system += '''
This is a Reviewer diagnosis, not an already-authored repair. A TOOL_BUG label
is a hypothesis, not proof of an implementation defect. Evaluate the preserved
public execution evidence independently. Any native_source supplied here is the
current skill implementation, not a proposed replacement. You may diagnose a
planning, perception, parameter, physical execution or implementation issue;
do not force every failure into a code-bug explanation. You may author a useful
same-name native update or a new skill. Code you author is queued for separate
Manager review and the existing simulator publication gate. Keep lesson approval
separate from the decision to develop code. Do not demand that unwritten code
already have a successful simulation result.
'''
    if flat_rules:
        system += '\n' + flat_rules
    return system


def request_system(proposal, base_system):
    if proposal.get('kind') != 'code_revision_request':
        return base_system
    from manager_patch_review import patch_prompt
    return base_system + '''

CURRENT STAGE: MANAGER AUTHORS A CANDIDATE; THIS IS NOT CANDIDATE APPROVAL.
native_source and skill_md are the CURRENT implementation and its current
documentation, supplied for editing. They are not an already-repaired candidate.
revision_request asks YOU, the Manager, to produce code_proposal with a complete
changed native implementation and the complete declared functional harness.
Absence of an edit or harness in the CURRENT implementation is an authoring task,
not a reason to reject an allegedly submitted unchanged candidate. Likewise,
the first simulator trial is how an unvalidated repair hypothesis is tested;
do not require a successful trial that has not yet been authorized or run.
Assess the public failure evidence and causal uncertainty. If it supports a
concrete testable repair, AUTHOR it without claiming it works. Preserve every
declared task, seed and pass criterion, native interfaces and physical interlocks.
If evidence does not support any concrete repair, explain precisely what is
missing; do not invent evidence or force a candidate.
Use decision=defer for the design request when returning a new code_proposal:
no existing candidate is being approved here. Your code is queued for a later,
separate actual Manager review, then the unchanged simulator gate, then publication.
No separate Developer exists, and no code is self-approved in this response.
''' + patch_prompt(proposal)

def assert_native_candidate(code):
    tree=ast.parse(code)
    # Reuse the established no-private-task-access rules; native sensor/control
    # imports and same-name updates are no longer rejected as compositions.
    for n in ast.walk(tree):
        token = n.id if isinstance(n,ast.Name) else n.attr if isinstance(n,ast.Attribute) else None
        if token and any(p.search(token) for p in GT_PATTERNS):
            raise ValueError('Native proposal reads forbidden task state: '+token)
    return tree
