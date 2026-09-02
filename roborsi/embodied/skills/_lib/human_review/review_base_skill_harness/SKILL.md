---
name: review_base_skill_harness
kind: meta_skill
description: Procedure for harness-reviewing a base skill (new or updated). Loads its SKILL.md frontmatter, runs scripts/test_base_skill.py with --from-frontmatter, parses the JSON verdict, decides APPROVE / REJECT / SKIP per harness_standard. Every base skill proposal goes through this — apply step is BLOCKED until this returns APPROVE.
version: 1
metadata:
  tags: [harness, review, base-skill, governance, process]
  inputs:
    - skill_name             # e.g. pick_actor_by_contact_point
    - source                 # "existing" | "proposal:<pid>"
  outputs:
    - decision               # APPROVE | REJECT | SKIP_WITH_REASON | MALFORMED
    - report_path            # ~/.roborsi/harness_reports/<ts>.json
    - error_lines            # if FAIL/ERROR/MALFORMED
---

# When to invoke

- Before applying a `kind=new` proposal with `category=base/robotwin/*`.
- Before applying a `kind=update` proposal that touches any
  `base/robotwin/*/policy.py`.
- During the periodic full audit (`scripts/test_base_skill.py --batch`).
- After ANY edit to `_do_verify_holding_visual`, `_do_move_to_pose`,
  `_do_gripper`, or other shared helpers in `rollout_runtime.py` (they're
  consumed by many base skills, breakage cascades).

# Inputs the proposal must carry

The proposal JSON (or the existing SKILL.md being updated) MUST contain a
`harness:` block in the SKILL.md frontmatter, conforming to
`harness_standard/SKILL.md`. A proposal without it is REJECT with reason
"missing harness specification — see harness_standard".

# Procedure

## 1. Load the harness spec

```python
run_python(code='''
from scripts.test_base_skill import _load_frontmatter
fm = _load_frontmatter("<skill_name>")
print((fm.get("metadata") or {}).get("harness") or fm.get("harness"))
''')
```

Verify the spec parses, has `sim_task` + non-empty `args`, and the
`pass_criteria.kind` is one of the schemes in `harness_standard`.

## 2. Run the harness

Terminal:
```bash
ROBORSI_BICOORD_ROOT=$ROBORSI_BICOORD_ROOT \
  python scripts/test_base_skill.py <skill_name> --from-frontmatter
```

Or programmatically:
```python
run_python(code='''
import subprocess
r = subprocess.run(
    ["python3", "scripts/test_base_skill.py", "<skill_name>", "--from-frontmatter"],
    env={**os.environ,
         "ROBORSI_BICOORD_ROOT": "$ROBORSI_BICOORD_ROOT"},
    capture_output=True, text=True, timeout=600)
print(r.stdout[-3000:])
print("---stderr---", r.stderr[-500:])
print("returncode:", r.returncode)
''')
```

Each call takes ~10-30s × (seeds × args) — total 30s-2min per skill.

## 3. Parse the verdict

JSON output ends with `"verdict": "PASS|FAIL|SKIP|MALFORMED|ERROR"`.

| verdict | meaning | action |
|---|---|---|
| PASS | `pass_count >= min_seeds_passing` | **APPROVE** — proceed with apply |
| FAIL | not enough seeds passed | **REJECT** — return failing-seed details to proposer for fix |
| SKIP | `skip_harness: true` or no harness block | **SKIP_WITH_REASON** — needs explicit human OK (do not auto-apply) |
| MALFORMED | spec missing required fields | **REJECT** — proposer must add fields per harness_standard |
| ERROR | exception during run (import error, sim crash) | **REJECT** — proposer or framework bug; surface traceback |

## 4. Write the decision + propagate

If APPROVE: include the harness JSON path in the apply commit message:
```
selfevo: apply <skill_name>  (harness PASS 5/5 @ ~/.roborsi/harness_reports/<ts>.json)
```

If REJECT: write the failing-seed details into the proposal's status file at
`~/.roborsi/skill_review/<pid>.json` (status="rejected", note=verdict
+ first failing-seed reason). Move to `rejected/`.

If SKIP: surface to user with the skip_reason verbatim. Do NOT auto-apply.

# Cross-references for the reviewer

- `harness_standard/SKILL.md` — pass criteria schemes.
- `review_selfevo_proposal/SKILL.md` — the broader review tree this hooks
  into (sections 4 & 6 require a harness check for base skill proposals).
- `EDIT.md §5` — the underlying "validate before landing" principle.
- `scripts/test_base_skill.py` — the harness runner.

# Failure modes of this skill itself

- Forgetting to clear `__pycache__` after editing shared helpers — old
  bytecode masks the fix. Add `find . -name "*.pyc" -delete` before runs
  when you've just edited `rollout_runtime.py` or `_lib/evaluation/*`.
- Running outside the RoboTwin conda env / wrong cwd — sim fails to boot.
  The harness will report ERROR; check stderr for `ModuleNotFoundError:
  sapien` or relative-path FileNotFoundError.
- Treating SKIP as PASS — never. SKIP means "we didn't actually test it";
  must surface to human for explicit acknowledgement.
