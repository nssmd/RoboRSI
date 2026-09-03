"""Planner-authored sim env synthesis.

When a campaign task has no sim env (the RoboTwin/BiCoord adapter raises
``BackendUnavailable: 'envs.<task>' not importable``) the task used to be a
permanent phantom — every run died at import. Instead, the Planner now AUTHORS
the env itself: it reads existing example envs, writes a ``Base_Task`` subclass,
and we validate it actually boots (load_actors + reset + check_success) in an
isolated subprocess before accepting it, feeding any error back for a retry.

This is the "if the Planner finds no env, it builds the env" capability — the
Manager only reviews/approves, it does not hand-author envs.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MAX_ATTEMPTS = 3


def sim_env_roots() -> list[Path]:
    """Task-env roots derived from the REGISTERED sim backends (each backend's
    own configured root), not hardcoded paths. Only roots that actually hold an
    envs/ dir are returned. env_exists / the env-source reader check ALL of
    them so a task that has an env under one backend's root is never wrongly
    re-synthesized for another."""
    from roborsi.embodied.agent_loop import list_backends, get_backend
    roots: list[Path] = []
    seen: set[Path] = set()
    for name in list_backends():
        try:
            be = get_backend(name)
        except Exception:
            continue
        resolver = getattr(be, "_root_or_raise", None)
        if resolver is None:
            continue
        try:
            root = Path(resolver())
        except Exception:
            continue
        if root not in seen and (root / "envs").is_dir():
            seen.add(root)
            roots.append(root)
    return roots


_ENV_AUTHOR_SYSTEM = (
    "You author RoboTwin / BiCoord-Bench sim task envs. An env is a Python "
    "module envs/<task>.py defining `class <task>(Base_Task)` (class name MUST "
    "equal the file/task name exactly) with:\n"
    "  - setup_demo(self, **kwags): super()._init_task_env_(**kwags)\n"
    "  - load_actors(self): spawn the scene. create_box(scene=self, pose=..., "
    "half_size=(...), color=(.5,.5,.5), name=..., is_static=True) for static "
    "target pads; create_actor(self, pose=rand_pose(xlim=[..],ylim=[..], "
    "qpos=[0.5,0.5,0.5,0.5], rotate_rand=False), modelname=\"002_bowl\"/..., "
    "model_id=N, convex=True) for graspable objects. BOWLS/CUPS MUST pass "
    "qpos=[0.5,0.5,0.5,0.5] or they spawn UNSTABLE (UnStableError). Keep objects "
    "off the midline (|x|>0.05) and not overlapping. Call "
    "self.add_prohibit_area(actor, padding=0.01) per actor.\n"
    "  - play_once(self): best-effort scripted expert (grasp_actor / "
    "move_by_displacement / place_actor + ArmTag('left'|'right')); the AGENT "
    "does NOT use this — keep it short, return self.info.\n"
    "  - check_success(self): the TASK predicate — compare actor.get_pose().p[:2] "
    "to target.get_pose().p[:2] within an eps and the gripper state; return a bool.\n"
    "Only load_actors + check_success must be CORRECT for the agent to run the "
    "task. READ 2-3 existing envs first (Read tool) to copy the exact API. "
    "Infer objects/target/success purely from the task NAME. Output ONLY the "
    "complete Python file content — no prose, no markdown fences."
)


def _env_file(task: str, root: Path) -> Path:
    return root / "envs" / f"{task}.py"


def _write_root() -> Path:
    """Root to WRITE a synthesized env into — the campaign's primary sim
    backend root (derived, not a hardcoded path)."""
    roots = sim_env_roots()
    if roots:
        return roots[0]
    from roborsi.embodied.agent_loop import get_backend
    return Path(get_backend("bicoord")._root_or_raise())


def env_exists(task: str) -> bool:
    """True iff envs/<task>.py exists under ANY registered backend's root.
    File-based (reliable): importing the module bare needs sapien/sim deps that
    fail outside a boot context, so we check the file, not the import. LH tasks
    (skills/long_horizon/<task>/) are NOT sim envs — the caller skips those."""
    return any((r / "envs" / f"{task}.py").exists() for r in sim_env_roots())


def is_long_horizon_task(task: str) -> bool:
    """LH tasks decompose into atomics and have no same-named sim env; never
    synthesize one for them."""
    return (_REPO / "roborsi" / "embodied" / "skills" / "long_horizon" / task).is_dir()


def _extract_code(text: str) -> str:
    t = (text or "").strip()
    m = re.search(r"```(?:python)?\s*\n(.*?)```", t, re.S)
    return m.group(1).strip() if m else t


def _validate_boot(task: str, root: Path) -> tuple[bool, str]:
    """Boot the just-written env in a clean subprocess: load_actors + reset +
    check_success() must run and return a bool. Returns (ok, message)."""
    probe = (
        f"import sys; sys.path.insert(0, {str(_REPO)!r})\n"
        "from roborsi.embodied.agent_loop import get_backend\n"
        "be = get_backend('bicoord')\n"
        f"with be.make_env({task!r}, {{'require_depth': True}}) as env:\n"
        "    env.reset(seed=7)\n"
        "    r = env._impl.check_success()\n"
        "    assert isinstance(r, bool), 'check_success must return bool, got '+str(type(r))\n"
        "    print('BOOT_OK')\n"
    )
    env = {**os.environ, "ROBORSI_BICOORD_ROOT": str(root),
           "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES", "1")}
    try:
        res = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                             text=True, timeout=240, env=env)
    except subprocess.TimeoutExpired:
        return False, "boot timed out (240s)"
    if "BOOT_OK" in res.stdout:
        return True, "boots"
    return False, (res.stderr or res.stdout)[-1600:]


def synthesize_env_if_missing(task: str) -> tuple[bool, str]:
    """Ensure envs/<task>.py exists and boots. If missing, the Planner authors
    it (read templates -> write -> validate -> retry on error). Returns
    (ok, message). On final failure the half-written file is removed."""
    from roborsi.agents import persistent_agent
    from roborsi.agents.atomic_backend import resolve as _resolve_atomic
    if is_long_horizon_task(task):
        return True, "long-horizon task (decomposes into atomics; no env needed)"
    # LIBERO (and any non-RoboTwin) atomics have no envs/<task>.py — their sim
    # env is the benchmark itself, addressed by the SKILL.md sim_task. Skip the
    # RoboTwin env-authoring path entirely for them.
    if not _resolve_atomic(task).needs_robotwin_env:
        return True, "non-RoboTwin backend (sim env is the benchmark; nothing to author)"
    if env_exists(task):
        return True, "exists"
    from roborsi.runtime_mode import evolution_enabled
    if not evolution_enabled():
        return False, "missing simulator environment; synthesis is disabled in eval mode"
    root = _write_root()
    prev_error = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        prompt = (
            f"Task '{task}' has NO sim env (envs/{task}.py missing) so every run "
            f"dies at import. AUTHOR it. First Read 2-3 example envs in "
            f"{root}/envs/ (place_plate_and_cup.py, stack_bowls.py, clean_table.py) "
            f"to copy the exact Base_Task API, then write a complete, STABLE "
            f"envs/{task}.py whose class is named exactly `{task}`. Infer the "
            f"objects, target and success predicate from the task name '{task}'."
        )
        if prev_error:
            prompt += (f"\n\nYour PREVIOUS attempt did NOT boot:\n{prev_error}\n"
                       f"Fix it (common cause: bowls/cups need qpos=[0.5,0.5,0.5,0.5]).")
        prompt += "\n\nOutput ONLY the complete Python file content."
        resp = persistent_agent.run("env_author", task, prompt,
                                    system_prompt=_ENV_AUTHOR_SYSTEM)
        code = _extract_code(resp)
        if "Base_Task" not in code or f"class {task}" not in code:
            prev_error = "response had no `class %s(Base_Task)`." % task
            continue
        _env_file(task, root).write_text(code, encoding="utf-8")
        ok, msg = _validate_boot(task, root)
        if ok:
            return True, f"created by Planner (attempt {attempt}/{_MAX_ATTEMPTS})"
        prev_error = msg
    p = _env_file(task, root)
    if p.exists():
        p.unlink()                      # don't leave a broken half-env on disk
    return False, f"Planner failed to author a bootable env in {_MAX_ATTEMPTS} attempts: {prev_error[-300:]}"
