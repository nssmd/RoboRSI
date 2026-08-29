"""Shared driver for LIBERO atomic skills.

Every LIBERO atomic differs only in its SKILL.md prompts, default task, and tool
budget — the execution is identical: get the backend, reset the task, and run the
universal ``run_rollout`` loop with success adjudicated by LIBERO's own
``check_success`` predicate (``use_sim_predicate=True``) computed AFTER the
episode (the VLM never sees it and cannot self-report success). So the atomics
share this instead of copy-pasting a ``run()`` body.

``LiberoProEnv`` has no ``run_rollout`` (that's a LIBERO-adapter convenience);
we reset and call ``run_rollout`` directly, mirroring what
``LIBEROEnv.run_rollout`` does internally.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop import get_backend
from roborsi.embodied.agent_loop.rollout import run_rollout
from roborsi.embodied.agent_loop.vlm_io import (
    reset_usage_metrics,
    usage_metrics_snapshot,
)
from roborsi.embodied.sim.libero.run_records import (
    EpisodeIdentity,
    classify_infrastructure_exception,
    episode_workdir,
)
from roborsi.embodied.skills import get as get_skill


class EpisodeInfrastructureError(RuntimeError):
    def __init__(
        self,
        *,
        category: str,
        detail: str,
        preview_path: str | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        super().__init__(detail)
        self.category = category
        self.detail = detail
        self.preview_path = preview_path
        self.usage = dict(usage or {})


_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "vlm_calls",
    "unmetered_vlm_calls",
)


def _empty_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE_KEYS}


def _normalized_usage(value: dict[str, Any] | None) -> dict[str, int]:
    source = dict(value or {})
    return {key: int(source.get(key, 0) or 0) for key in _USAGE_KEYS}


def _sum_usage(*values: dict[str, Any]) -> dict[str, int]:
    return {
        key: sum(int(value.get(key, 0) or 0) for value in values)
        for key in _USAGE_KEYS
    }


def _code_backed_trace_metrics(
    task: str,
    trace: list[dict[str, Any]],
) -> dict[str, Any]:
    from roborsi.embodied.skills import discover_compounds

    names = {skill.name for skill in discover_compounds(task)}
    calls = [
        str(tool_call.get("tool"))
        for event in trace
        if isinstance(event, dict)
        for tool_call in [event.get("tool_call")]
        if isinstance(tool_call, dict)
        if str(tool_call.get("tool")) in names
    ]
    return {
        "code_backed_hit": bool(calls),
        "code_backed_call_count": len(calls),
        "code_backed_tools": sorted(set(calls)),
    }


def _classify_infrastructure_exception(exc: Exception) -> str:
    return classify_infrastructure_exception(exc)


def prompts_for(skill: str) -> tuple[str, str]:
    """(instruction guidance, expected-on-success) from the atomic's SKILL.md."""
    sk = get_skill(skill)
    if sk is None:
        raise ValueError(f"task SKILL.md not found for '{skill}'")
    fm = sk.frontmatter or {}
    meta = fm.get("metadata") or {}
    prompts = meta.get("vlm_prompts") or {}
    instruction = (
        prompts.get("instruction")
        or fm.get("description")
        or f"Complete the task '{skill}'."
    )
    expected = (
        prompts.get("expected_on_success")
        or "The task is complete per the simulator predicate."
    )
    return str(instruction).strip(), str(expected).strip()


def _code_fingerprint() -> str:
    release_id = os.environ.get("ROBORSI_RELEASE_ID", "").strip()
    return f"release:{release_id or 'unbound'}"


def _config_fingerprint(payload: dict[str, Any]) -> str:
    episode_meta = dict(payload.get("episode_meta") or {})
    fields = (
        ("task", payload.get("task")),
        ("seed", payload.get("seed")),
        ("budget", payload.get("tool_budget")),
        ("model", payload.get("model")),
        ("backend", payload.get("backend")),
        ("run", episode_meta.get("run_id")),
        ("shard", episode_meta.get("shard")),
        ("attempt", episode_meta.get("attempt")),
        ("roles", os.environ.get("ROBORSI_LIBERO_ROLES", "0")),
        (
            "runtime_task_authoritative",
            episode_meta.get("runtime_task_authoritative", False),
        ),
    )

    def clean(value: Any) -> str:
        return str(value).replace("|", "/").replace("\n", " ")

    return "config:" + "|".join(
        f"{name}={clean(value)}"
        for name, value in fields
    )


def _role_orchestrated_instruction(
    *,
    skill: str,
    task: str,
    runtime_instruction: str,
    guidance: str,
    expected: str,
    model: str | None,
    workdir: Path,
) -> tuple[Any, str]:
    """Run the real roborsi Planner before the in-process Engineer loop."""
    from roborsi.agents import Planner
    from roborsi.agents.workspace import Workspace

    role_root = workdir / "roles"
    role_root.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(
        task=skill,
        run_id=workdir.name,
        root=role_root,
    )
    user_msg = f"LIBERO benchmark task: {runtime_instruction}"
    mission_spec = Planner(model=model).plan(
        task=skill,
        user_msg=user_msg,
        recent_reflections="",
        workspace=workspace,
        ns="libero",
    )
    plan_md = workspace.read_plan()
    criteria = "; ".join(mission_spec.get("success_criteria", [])) or expected
    instruction = (
        f"LIBERO task: {runtime_instruction}\n\n"
        f"{guidance}\n\n"
        f"PLAN (from roborsi Planner):\n{plan_md}\n\n"
        f"SUCCESS CRITERIA: {criteria}"
    )
    return workspace, instruction


def _review_role_episode(
    *,
    workspace: Any,
    result: Any,
    model: str | None,
) -> dict[str, Any]:
    """Run the independent Reviewer after the simulator has adjudicated."""
    from roborsi.agents import Reviewer

    trace = list(result.trace or [])
    rollout_meta = dict(result.rollout.meta or {})
    vlm_declared = bool(rollout_meta.get("vlm_declared", False))
    completion_candidate = bool(
        rollout_meta.get("tool_completion_candidate", False)
    )
    visible_success = vlm_declared or completion_candidate
    if vlm_declared:
        visible_outcome = "engineer_declared_done"
    elif completion_candidate:
        visible_outcome = "engineer_completion_candidate"
    else:
        visible_outcome = "engineer_not_done"
    for hidden_key in (
        "predicate_check",
        "category",
        "episode_identity",
        "demo_video",
        "rollout_video",
        "preview_video",
        "trajectory_path",
        "media_error",
    ):
        rollout_meta.pop(hidden_key, None)
    engineer_result = {
        "success": visible_success,
        "outcome": visible_outcome,
        "tool_calls": len(trace),
        "trace": trace,
        "rollout_meta": rollout_meta,
    }
    workspace.write_summary(
        "\n".join(
            [
                f"# Engineer Summary - {workspace.task}",
                "",
                f"Outcome: `{visible_outcome}`",
                f"Engineer declared completion: `{visible_success}`",
                f"Tool calls: `{len(trace)}`",
            ]
        )
        + "\n"
    )
    return Reviewer(model=model).review(
        workspace=workspace,
        engineer_result=engineer_result,
        run_id=None,
        ns="libero",
        posthoc_behavior_review=False,
    )

def run_libero_atomic(
    skill: str,
    *,
    task: str,
    episodes: int = 1,
    seed_start: int = 0,
    tool_budget: int = 30,
    model: str | None = None,
    workdir: str | None = None,
    backend: str = "libero",
) -> dict[str, Any]:
    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"{backend} unavailable: {reason}")
    work = Path(workdir).expanduser() if workdir else Path("/tmp/roborsi")
    safe_task = str(task).replace("/", "__")
    run_id = (
        f"atomic-{safe_task}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    )
    shard = 0

    eps_out: list[dict[str, Any]] = []
    for i in range(episodes):
        seed = seed_start + i
        identity = EpisodeIdentity(
            run_id=run_id,
            task_key=task,
            seed=int(seed),
            shard=shard,
            attempt=1,
        )
        seed_workdir = episode_workdir(work, identity)
        eps_out.append(
            run_libero_episode(
                skill,
                task=task,
                seed=seed,
                tool_budget=tool_budget,
                model=model,
                workdir=seed_workdir,
                backend=backend,
                episode_meta={
                    "run_id": run_id,
                    "task_key": task,
                    "seed": int(seed),
                    "shard": shard,
                    "attempt": 1,
                    "media_root": str(work / "media"),
                },
            ),
        )
    successes = sum(1 for e in eps_out if e["success"])
    return {
        "skill": f"atomic.{skill}.zeroshot",
        "task": task,
        "backend": backend,
        "episodes": eps_out,
        "total": len(eps_out),
        "successes": successes,
        "success_rate": (successes / len(eps_out)) if eps_out else 0.0,
    }


def run_libero_episode(
    skill: str,
    *,
    task: str,
    seed: int,
    tool_budget: int,
    model: str | None,
    workdir: Path,
    episode_meta: dict[str, Any],
    backend: str = "libero",
) -> dict[str, Any]:
    guidance, expected = prompts_for(skill)
    be = get_backend(backend)
    ok, reason = be.available()
    if not ok:
        raise RuntimeError(f"{backend} unavailable: {reason}")
    identity = EpisodeIdentity(
        run_id=str(episode_meta.get("run_id") or "legacy-run"),
        task_key=str(episode_meta.get("task_key") or task),
        seed=int(episode_meta.get("seed", seed)),
        shard=int(episode_meta.get("shard", 0)),
        attempt=int(episode_meta.get("attempt", 1)),
    )
    media_root = Path(episode_meta.get("media_root")) if episode_meta.get("media_root") else (
        workdir / "videos"
    )
    roles_enabled = os.environ.get("ROBORSI_LIBERO_ROLES", "0") == "1"
    role_workspace = None
    planner_usage = _empty_usage()
    planner_time_s = 0.0
    with be.make_env(task) as env:
        env.reset(seed)
        runtime_task_authoritative = bool(
            episode_meta.get("runtime_task_authoritative", False)
        )
        if runtime_task_authoritative:
            expected = "The visible LIBERO task instruction is completed in the scene."
        instruction = f"LIBERO task: {env.instruction}\n\n{guidance}"
        rollout_workdir = workdir
        if roles_enabled:
            reset_usage_metrics()
            planner_started = time.monotonic()
            try:
                role_workspace, instruction = _role_orchestrated_instruction(
                    skill=skill,
                    task=task,
                    runtime_instruction=env.instruction,
                    guidance=guidance,
                    expected=expected,
                    model=model,
                    workdir=workdir,
                )
            finally:
                planner_usage = _normalized_usage(usage_metrics_snapshot())
                planner_time_s = time.monotonic() - planner_started
            rollout_workdir = role_workspace.root / "executor"
        try:
            res = run_rollout(
                env,
                seed=seed,
                task_name=skill,
                instruction=instruction,
                expected_on_success=expected,
                model=model,
                tool_budget=tool_budget,
                workdir=rollout_workdir,
                use_sim_predicate=True,
                episode_meta=episode_meta,
                include_skill_task_truth=not runtime_task_authoritative,
            )
        except Exception as exc:  # noqa: BLE001
            category = _classify_infrastructure_exception(exc)
            detail = f"{type(exc).__name__}: {exc}"
            preview_path = None
            finalize_preview = getattr(env, "finalize_preview", None)
            if callable(finalize_preview):
                try:
                    preview = finalize_preview(
                        identity=identity,
                        category=category,
                        media_root=media_root,
                    )
                    preview_path = str(preview) if preview is not None else None
                except Exception as preview_exc:  # noqa: BLE001
                    detail = (
                        f"{detail} | preview_finalize_error:"
                        f"{type(preview_exc).__name__}: {preview_exc}"
                    )
            raise EpisodeInfrastructureError(
                category=category,
                detail=detail,
                preview_path=preview_path,
                usage=usage_metrics_snapshot(),
            ) from exc
    meta = dict(res.rollout.meta or {})
    trace = list(res.trace or [])
    meta.update(_code_backed_trace_metrics(skill, trace))
    meta["efficiency_schema"] = "roborsi.efficiency.v2"
    if roles_enabled and role_workspace is not None:
        engineer_usage = _normalized_usage(meta)
        reviewer_usage = _empty_usage()
        reviewer_time_s = 0.0
        meta["role_mode"] = "planner_engineer_reviewer"
        meta["role_workspace"] = str(role_workspace.root)
        reset_usage_metrics()
        reviewer_started = time.monotonic()
        try:
            review = _review_role_episode(
                workspace=role_workspace,
                result=res,
                model=model,
            )
            meta["review_verdict"] = str(review.get("verdict") or "")
            meta["review_proposal_decision"] = str(
                review.get("proposal_decision") or "NO_PROPOSAL"
            )
        except Exception as exc:  # Reviewer failure cannot rewrite simulator truth.
            meta["review_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            reviewer_usage = _normalized_usage(usage_metrics_snapshot())
            reviewer_time_s = time.monotonic() - reviewer_started
        meta["role_usage"] = {
            "planner": planner_usage,
            "engineer": engineer_usage,
            "reviewer": reviewer_usage,
        }
        meta.update(_sum_usage(planner_usage, engineer_usage, reviewer_usage))
        meta["planner_time_s"] = round(planner_time_s, 3)
        meta["reviewer_time_s"] = round(reviewer_time_s, 3)
        meta["orchestration_time_s"] = round(
            planner_time_s + reviewer_time_s,
            3,
        )
    meta.setdefault("vlm_declared", bool(meta.get("vlm_declared", False)))
    meta.setdefault("code_fingerprint", _code_fingerprint())
    meta.setdefault(
        "config_fingerprint",
        _config_fingerprint(
            {
                "task": task,
                "seed": seed,
                "tool_budget": tool_budget,
                "model": model,
                "backend": backend,
                "episode_meta": episode_meta,
            }
        ),
    )
    return {
        "seed": seed,
        "success": res.success,
        "outcome": res.outcome,
        "steps": len(res.trace),
        "meta": meta,
    }
