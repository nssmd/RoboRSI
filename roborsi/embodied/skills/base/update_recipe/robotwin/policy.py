"""base.robotwin.update_recipe — Engineer can rewrite this atomic's
Recipe section in plan.md mid-attempt. The Recipe is the only
plan.md section Engineer is allowed to mutate directly; Goal /
Hard rules / Done gate / Success criteria require a Reviewer-
filed plan_amend proposal.

Layout invariant: workdir is `<workspace>/<idx>_<atomic>/attempt_N/rollout`,
so plan.md lives at `workdir.parent.parent / "plan.md"`.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any


def _atomic_plan_path(workdir: Path) -> Path:
    # workdir = .../<idx>_<atomic>/attempt_N/rollout → plan.md is two levels up
    return workdir.parent.parent / "plan.md"


def _replace_recipe_section(plan_md: str, new_recipe: str) -> str:
    """Replace the body under '## Recipe' (until the next '## ' heading or
    EOF) with new_recipe. If no Recipe section exists, append one."""
    new_recipe = new_recipe.rstrip() + "\n"
    pattern = re.compile(
        r"(^##\s+Recipe[^\n]*\n)(.*?)(?=^##\s|\Z)",
        re.S | re.M,
    )
    if pattern.search(plan_md):
        return pattern.sub(lambda m: m.group(1) + new_recipe + "\n", plan_md, count=1)
    # No existing Recipe section — append at end
    suffix = "" if plan_md.endswith("\n") else "\n"
    return plan_md + suffix + "## Recipe\n" + new_recipe + "\n"


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot

    new_recipe = args.get("new_recipe") or ""
    reason = args.get("reason") or ""
    if not new_recipe.strip():
        return ({"ok": False, "reason": "new_recipe is empty"},
                _snapshot(state.env))

    plan_path = _atomic_plan_path(Path(state.workdir))
    if not plan_path.exists():
        return ({"ok": False,
                 "reason": f"plan.md not found at {plan_path}"},
                _snapshot(state.env))

    original = plan_path.read_text(encoding="utf-8")
    updated = _replace_recipe_section(original, new_recipe)
    plan_path.write_text(updated, encoding="utf-8")

    log_path = plan_path.parent / "recipe_revisions.log"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] reason: {reason}\n")
        f.write(f"  new_recipe ({len(new_recipe)} chars):\n")
        for ln in new_recipe.splitlines():
            f.write(f"    {ln}\n")
        f.write("\n")

    return ({"ok": True,
             "plan_md_path": str(plan_path),
             "reason_logged": reason,
             "note": ("Recipe section rewritten. Next attempt of this "
                       "atomic will read the new Recipe. Current attempt's "
                       "context is unchanged — finish or fail-with-reason "
                       "your current attempt, then the new Recipe applies.")},
            _snapshot(state.env))
