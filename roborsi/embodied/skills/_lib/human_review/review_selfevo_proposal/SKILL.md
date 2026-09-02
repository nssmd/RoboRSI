---
name: review_selfevo_proposal
kind: meta_skill
description: Decision protocol for reviewing self-evolution proposals written by a sim-agent. Used by a human (or surrogate AI reviewer) to APPLY / REJECT / ESCALATE proposals queued in ~/.roborsi/skill_review/. Codifies the rules I (Claude) developed while reviewing the handover_block_bicoord auto-iteration runs.
version: 1
metadata:
  tags: [self-evolution, review, governance, meta]
  inputs:
    - proposal_id  # the basename (no .json) of a pending file in ~/.roborsi/skill_review/
  outputs:
    - decision     # APPLY | REJECT | ESCALATE
    - action_taken # "git commit <sha>" | "marked rejected" | "surfaced to human <reason>"
---

# Overview

When a sim-agent (`bot_agent.py`) self-diagnoses a failed long-horizon run, it can submit proposals via `propose_skill_update` / `propose_new_skill`. Proposals land as `~/.roborsi/skill_review/<id>.json`. **Auto-applying is dangerous**: agents have produced partial-patches that broke skills, empty stubs, and duplicates of already-merged fixes. This skill is the playbook for a human-equivalent reviewer to triage each pending proposal cleanly.

# Prerequisites

- `~/.roborsi/skill_review/` exists and is readable.
- Read access to the roborsi repo + git history (`git log`, `git show`).
- `scripts/apply_selfevo_proposal.py` works (moves applied/rejected JSON into archive subdirs).

# Phases

## 1. Enumerate truly-pending proposals

```bash
ls ~/.roborsi/skill_review/*.json 2>/dev/null
```
Only files directly under the queue root are pending — `applied/` and `rejected/` are archived.

## 2. For each proposal: extract structural signals

```python
import json
d = json.load(open(path))
kind       = d["kind"]                      # "new" or "update"
name       = d["name"]                       # skill name (e.g. pick_bowl_bicoord.zeroshot)
rationale  = (d.get("rationale") or "").strip()
code       = d.get("new_code") or d.get("code") or ""
code_head  = code[:200]
```

## 3. Apply the decision tree (in order — first match wins)

| # | Condition | Decision |
|---|-----------|----------|
| 1 | `code == ""` or `len(code) < 200` | **REJECT** (stub) |
| 2 | `rationale == ""` | **REJECT** (no evidence) |
| 3 | `"PARTIAL PATCH" in code_head` or `"PATCH" in code[:120]` | **REJECT** — apply_script treats new_code as full file replacement; partial patches would destroy the skill. Splice manually if the patch idea is valid. |
| 4 | `kind == "new"` AND category starts with `base/robotwin/` | **HARNESS GATE FIRST** (rule 4a). Then **ESCALATE** to user with harness result. |
| 4a | `kind == "update"` AND target file is `base/robotwin/*/policy.py` (a BASE SKILL update) | **HARNESS GATE** — run `review_base_skill_harness` on the candidate. APPROVE proceeds; REJECT or SKIP halts apply. |
| 5 | Rationale lacks ALL of: ("SYSTEMATIC" / "verdict"), ("view_frame" or "frame"), ("inner_trace" or "vlm_trace" or "tool_calls") | **REJECT** — proposal isn't grounded in the diagnostic chain (see `bot_agent` system prompt CONTRADICTION SIGNAL protocol) |
| 6 | `git log --grep="<skill_name>"` shows a recent commit that already changed this skill in the same direction | **REJECT** as duplicate |
| 7 | otherwise | **APPLY** via `python3 scripts/apply_selfevo_proposal.py <pid>` |

## 4. Execute the decision

```bash
# APPLY
python3 scripts/apply_selfevo_proposal.py <pid>
# REJECT
python3 scripts/apply_selfevo_proposal.py --reject <pid>
# ESCALATE: do NOT touch the queue; report verbatim to the human:
#   "PROPOSAL <pid>  kind=<kind>  name=<name>
#    rationale: <first 300 chars>
#    code_len: <N>
#    Needs human decision because <rule-4 reason>."
```

## 5. After all decisions, snapshot the round

Log to `/tmp/agent_loop/<task>/review_decisions.log`:
```
<timestamp>  <pid>  <decision>  <one-line reason>
```

# Success criteria

- Every file directly under `~/.roborsi/skill_review/` after one review pass either: (a) moved to `applied/`, (b) moved to `rejected/`, or (c) explicitly flagged for human escalation in the report.
- No partial-patch ever applied (this destroyed skills on 2026-05-30).
- No duplicate of an already-committed fix applied.

# Failure modes

- **Partial-patch apply**: agent writes "# PARTIAL PATCH" fragments and the apply script treats them as full files. Mitigated by Rule 3.
- **Duplicate spam**: agent re-proposes a fix that's already in HEAD because it can't see git log. Mitigated by Rule 6.
- **Empty proposals**: agent submits with no rationale or code (tool-use bug under load). Rules 1+2.
- **Drive-by base skill**: agent proposes a new base skill without engineering review. Rule 4 forces escalation.
- **Reviewer reads wrong queue**: forgetting that `applied/` and `rejected/` contain archived files — only the queue root is live. Always `ls *.json` not `ls -R`.

# Related

- Proposal submission tools: `propose_skill_update`, `propose_new_skill` in `bot_agent.py`.
- Apply script: `scripts/apply_selfevo_proposal.py` (now moves files into archive subdirs).
- Diagnostic tools the agent's rationale should cite: `get_failure_patterns`, `get_inner_trace`, `view_frame`, `get_sim_debug`, `read_file`.
