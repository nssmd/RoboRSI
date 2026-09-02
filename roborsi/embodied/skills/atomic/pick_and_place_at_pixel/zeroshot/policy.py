"""atomic.pick_and_place_at_pixel.zeroshot — VLM 2-phase pick-then-place.

Two modes:
  - mode='rollout' (default): one tool_use per turn (legacy).
  - mode='codeact': VLM writes Python scripts; on success the script is
    promoted to skills/atomic/pick_and_place_at_pixel/zeroshot/programs/.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from roborsi.data.store import DataStore
from roborsi.embodied.agent_loop import get_backend
from roborsi.embodied.agent_loop.rollout import run_rollout


_SKILL = "pick_and_place_at_pixel"


def run(
    env=None,
    source_object: str = "",
    target_zone: str = "",
    task_name: str = _SKILL,
    tool_budget: int = 18,
    model: str | None = None,
    workdir: str | None = None,
    seed: int | None = None,
    backend: str = "robotwin",
    spawn_task: str | None = None,
    log: bool = True,
    mode: str | None = None,
    # Aliases the planner sometimes emits.
    object: str = "",  # noqa: A002
    target_side: str = "",
    target: str = "",
    **_: Any,
) -> dict[str, Any]:
    source_object = source_object or object
    target_zone = target_zone or target_side or target
    if not source_object or not target_zone:
        raise ValueError("source_object and target_zone are required")

    mode = (mode or os.environ.get("ROBORSI_ZEROSHOT_MODE") or "rollout").lower()

    goal = (f"Pick up the {source_object} from the table and drop it into the {target_zone}.")
    expected = (f"The {source_object} is visibly inside / on the {target_zone}, "
                "and the gripper has released (no longer holding it).")

    owns_env = False
    if env is None:
        if not spawn_task:
            raise ValueError("must pass either env (long_horizon mode) or spawn_task (standalone)")
        be = get_backend(backend)
        env = be.make_env(spawn_task, {"require_depth": True})
        env.reset(int(seed or 0))
        owns_env = True

    work = Path(workdir).expanduser() if workdir else Path(f"/tmp/roborsi-zeroshot/{_SKILL}")

    if mode == "codeact":
        from roborsi.embodied.sim.robotwin.codeact_runtime import (
            run_codeact_episode, promote_program_to_skill, discard_failed_program,
        )
        instruction = (
            f"GOAL: {goal}\n\n"
            f"CRITICAL — the source object is EXACTLY '{source_object}'. If multiple\n"
            f"colored blocks are visible (red, green, blue, yellow, magenta, cyan),\n"
            f"you MUST select the one matching the color in '{source_object}' literally.\n"
            f"Use zoom_in or label_points_grid to verify color before grasping.\n\n"
            "EXPLORATION HINTS (read tool docs in your system prompt for details):\n"
            "  - PREFERRED two-call pipeline:\n"
            f"      r = grasp_object(arm=<left|right>, object='{source_object}')\n"
            f"      if r['ok']: place_object_in(arm=<same>, target='{target_zone}')\n"
            "    Two atomic tools each handle 8-10 sub-steps internally. Done.\n"
            "  - If grasp_object returns ok=False, inspect attempts[] and switch arm,\n"
            "    or call with prefer_top_down=False.\n"
            "  - If place_object_in returns ok=False (e.g. unreachable target), try\n"
            "    the other arm. After successful place, look() + assess.\n"
            "  - You have up to 30 turns. With these tools, success should be 2-3 turns."
        )
        cresult = run_codeact_episode(
            env, seed=int(seed or 0), task_name=task_name,
            instruction=instruction, expected_on_success=expected,
            model=model, max_turns=30, workdir=work,
        )
        rollout = cresult.rollout
        rollout.meta.setdefault("collector", "zeroshot-codeact")
        rollout.meta["source_object"] = source_object
        rollout.meta["target_zone"] = target_zone
        promoted: str | None = None
        if cresult.saved_program_path:
            if rollout.success:
                promoted = promote_program_to_skill(cresult.saved_program_path, _SKILL)
            else:
                discard_failed_program(cresult.saved_program_path)
        written = DataStore().write(
            rollout, skill=task_name,
            extra_meta={
                "subskill": f"atomic.{_SKILL}.zeroshot",
                "source_object": source_object,
                "target_zone": target_zone,
                "mode": "codeact",
            },
        ) if log and rollout.success else None
        if owns_env:
            env.close()
        return {
            "skill": f"atomic.{_SKILL}.zeroshot",
            "task_name": task_name,
            "source_object": source_object,
            "target_zone": target_zone,
            "success": rollout.success,
            "outcome": rollout.outcome,
            "vlm_trace": cresult.trace,
            "final_program": cresult.saved_program_path,
            "promoted_program": promoted,
            "run_id": written.run_id if written else None,
        }

    instruction = (
        f"Pick up the {source_object} from its current location and release it on the {target_zone}. "
        f"Use look + find_pixel + move_to_pixel(grasp) for the pick, then "
        f"look + find_pixel(target) + move_to_pixel(release) for the place. "
        f"Call done(success=true) only when the {source_object} is visibly on the {target_zone}."
    )
    result = run_rollout(
        env, seed=int(seed or 0), task_name=task_name,
        instruction=instruction, expected_on_success=expected,
        model=model, tool_budget=tool_budget, workdir=work,
    )
    rollout = result.rollout
    rollout.meta.setdefault("collector", "zeroshot")
    rollout.meta["source_object"] = source_object
    rollout.meta["target_zone"] = target_zone
    written = DataStore().write(
        rollout, skill=task_name,
        extra_meta={
            "subskill": f"atomic.{_SKILL}.zeroshot",
            "source_object": source_object,
            "target_zone": target_zone,
        },
    ) if log and rollout.success else None
    if owns_env:
        env.close()
    return {
        "skill": f"atomic.{_SKILL}.zeroshot",
        "task_name": task_name,
        "source_object": source_object,
        "target_zone": target_zone,
        "success": rollout.success,
        "outcome": rollout.outcome,
        "tool_calls": rollout.meta.get("tool_calls"),
        "vlm_trace": result.trace,
        "run_id": written.run_id if written else None,
    }
