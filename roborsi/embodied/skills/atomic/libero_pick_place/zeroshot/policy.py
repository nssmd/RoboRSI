"""atomic.libero_pick_place.zeroshot — VLM + base/libero tools.

Drives the universal ``run_rollout`` loop against a LIBERO task with the
camera-grounded single-arm muscle. The final simulator verdict is computed
after the episode (``use_sim_predicate=True``) and is not exposed to the VLM.

LiberoProEnv has no ``run_rollout`` (that's a RoboTwin-adapter convenience);
we reset the env and call ``run_rollout`` directly, mirroring what
``RoboTwinEnv.run_rollout`` does internally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop import get_backend
from roborsi.embodied.agent_loop.rollout import run_rollout
from roborsi.embodied.skills import get as get_skill

_SKILL = "libero_pick_place"
_DEFAULT_BACKEND = "libero"
_DEFAULT_TASK = "libero_object/0"


def run(
    episodes: int = 1,
    seed_start: int = 0,
    tool_budget: int = 30,
    model: str | None = None,
    workdir: str | None = None,
    backend: str = _DEFAULT_BACKEND,
    task: str = _DEFAULT_TASK,
    **_: Any,
) -> dict[str, Any]:
    guidance, expected = _prompts_for(_SKILL)
    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"{backend} unavailable: {reason}")
    work = Path(workdir).expanduser() if workdir else Path("/tmp/roborsi-libero")

    eps_out: list[dict[str, Any]] = []
    with be.make_env(task) as env:
        for i in range(episodes):
            seed = seed_start + i
            env.reset(seed)
            # The VLM gets the specific LIBERO instruction PLUS the how-to guidance.
            instruction = f"LIBERO task: {env.instruction}\n\n{guidance}"
            res = run_rollout(
                env,
                seed=seed,
                task_name=_SKILL,
                instruction=instruction,
                expected_on_success=expected,
                model=model,
                tool_budget=tool_budget,
                workdir=work,
                use_sim_predicate=True,
            )
            eps_out.append({
                "seed": seed,
                "success": res.success,
                "outcome": res.outcome,
                "steps": len(res.trace),
            })

    successes = sum(1 for e in eps_out if e["success"])
    return {
        "skill": f"atomic.{_SKILL}.zeroshot",
        "task": task,
        "backend": backend,
        "episodes": eps_out,
        "total": len(eps_out),
        "successes": successes,
        "success_rate": (successes / len(eps_out)) if eps_out else 0.0,
    }


def _prompts_for(task: str) -> tuple[str, str]:
    sk = get_skill(task)
    if sk is None:
        raise ValueError(f"task SKILL.md not found for '{task}'")
    fm = sk.frontmatter or {}
    meta = fm.get("metadata") or {}
    prompts = meta.get("vlm_prompts") or {}
    instruction = (
        prompts.get("instruction")
        or fm.get("description")
        or f"Complete the task '{task}'."
    )
    expected = (
        prompts.get("expected_on_success")
        or "The task is visibly complete."
    )
    return str(instruction).strip(), str(expected).strip()
