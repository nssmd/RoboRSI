# Process Permissions

RoboRSI runs three roles as **persistent Claude Code processes** (Planner,
Reviewer, Manager) plus the in-process **Engineer**. The CC processes launch
with broad tools, so this file is the **binding contract** for what each may
change. It describes the existing, field-level model — nothing here invents new
behaviour; it documents how the code already gates each object.

## The model in one line

Each object has its own owner + path. Some are written **directly** (the owner
edits its own section / the framework auto-appends), some go through
**propose → approve → apply**. The **approver** of a gated change may be the
**human** or the **Manager** — currently the **Manager** (it reviews against the
7 rules + the harness gate, then applies; it escalates to the human only when
genuinely unsure).

## Permission matrix

| Object | Who changes it · how |
|---|---|
| **plan.md · initial** | **Planner** writes the whole plan (mission_spec + each `plan_<i>.md`). |
| **plan.md · Recipe** | **Engineer** edits it directly via the `update_recipe` tool. |
| **plan.md · Goal / Hard rules / Done gate / Success criteria** | **Reviewer** edits via its verdict's `plan_amend{section,new_text}`; the framework applies it (`_apply_plan_amend`). The Engineer may NOT touch these. |
| **wiki · execution traces** (Successful / Failed) | **Framework auto-writes OBSERVED FACTS** after every atomic (`append_success_trace` writes the verified success; `append_failure_trace` writes seed/outcome/tool-sequence only). |
| **wiki · Failed-run Reviewer diagnosis** (root_cause / next_action) | **Reviewer PROPOSES** (auto-queued to `wiki_review/` as a `failure_hypothesis`) → **Manager approves** (`resolve_wiki_hypothesis(approve=True)` → written into `## Manager-approved leads`). Gated: an unverified guess NEVER enters the wiki body, so it can't steer the next plan. Both Reviewer (author) and Manager (approver) must look. |
| **wiki · Key measurements** | **Reviewer PROPOSES** (`propose_measurement` → `wiki_review/` queue) → **approver applies** (`apply_measurement_proposal`). Gated. |
| **skill code** (`base/robotwin/*/policy.py`, new skills) | **Reviewer / Manager PROPOSE** (→ `skill_review/` queue) → **approver applies** (`apply_selfevo_proposal.py`, base-skill changes run the harness gate first). Gated. |

## Per-role summary

- **Engineer** (in-process): drives the sim; reads the wiki; edits its own
  plan.md **Recipe**. Does not touch other plan.md sections, the wiki, or skills.
- **Planner** (persistent CC): writes the initial plan.md from the wiki +
  baseline. Reads anything. Does not edit the wiki or skills, and does not
  propose — its product is the plan.
- **Reviewer** (persistent CC): judges from ground truth + trace. Directly owns
  the four plan.md sections (via `plan_amend` in its verdict, framework-applied).
  Proposes wiki measurements and skill new/updates (via the verdict's
  `proposal_decision` + `proposal_payload`) — these go to the queue as **pending**;
  it never applies its own proposal.
- **Manager** (persistent CC): the **approver** (for now). Reviews each pending
  proposal against the 7 rules
  (`_lib/human_review/review_selfevo_proposal/SKILL.md`), runs the harness gate
  for base-skill changes, then **applies** (`apply_selfevo_proposal.py` /
  `task_wiki.apply_measurement_proposal`). May also correct/prune the wiki and
  roll the persistent sessions. Escalates to the human only when genuinely
  unsure. (Approval authority is configurable: human or Manager — currently
  Manager.)

## Enforcement: contract, not sandbox

The CC sessions run `bypassPermissions` (so they don't hang on a TTY prompt
mid-run) — technically they *can* write anything. The read/propose boundaries
above are enforced by each session's system prompt: `persistent_agent.py`
prepends a permissions preamble, and the role prompts route every skill/wiki
change through the propose path. A session observed editing skills/wiki directly
is a contract violation to fix in the prompt, not an intended path. The only
**hard** gate is the harness gate on base-skill applies — that runs regardless
of approver. If you want a hard filesystem sandbox (read-only dirs / tool
allow-lists), that is a separate piece.

## Evaluation mode

`ROBORSI_RUN_MODE=eval` or the `roborsi eval` command freezes the released
capability set. The runtime removes proposal and skill-registration tools,
forces Planner and Reviewer to stateless calls, suppresses wiki/plan/history
write-back, separates collected episodes from training data, and rejects
proposal extraction or application. Per-run workspaces, traces, videos,
metrics, and final simulator verdicts remain writable evaluation evidence.
