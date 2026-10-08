"""Baseline agent orchestrations for controlled comparisons against RoboRSI.

Three ablation agent modes share the exact evaluation protocol of the full
Planner → Engineer → Reviewer triangle — the same base-skill tool surface
(``_build_tool_specs`` / plugin dispatch), the same tool budget, the same
model transport (``vlm_io``), the same seeds, and the same invisible final
simulator predicate — while changing ONLY the role orchestration:

  maestro : one VLM session plans AND executes (no separate Planner or
            Reviewer). It may retry freely until the tool budget runs out.
            Implemented as a single ``run_rollout`` episode — the identical
            tool loop the RoboRSI Engineer drives — with an orchestrator
            instruction instead of a Planner-authored plan.md.
  openeta : ETA-style observe→think→act interaction loop. Every cycle the
            harness force-dispatches ``look()`` (charged against the SAME
            budget), then the VLM must answer with exactly ONE action tool
            call. No plan document, no Reviewer.
  capx    : CaP/CaP-X-style code generation. The VLM writes one policy
            snippet (up to 3 repair rounds on runtime errors); the code may
            only call the literal public base-skill API (same sandboxed
            builtins as ``base.exec_python``) and every skill call is charged
            against the shared tool budget. No step-wise interaction.

Integrity invariants shared with the roborsi mode: success is adjudicated by
``env.check_success()`` AFTER the episode (never visible to the agent), no
proposals are ever produced (``proposal_decision`` is always NO_PROPOSAL),
and eval mode's no-write-back guarantees apply unchanged — none of these
orchestrations touch the task wiki, skill history, or proposal queue.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Baseline orchestrations selectable via ``--agent-mode`` (the full triangle
#: itself is selected as ``"roborsi"`` and never routes through this module).
BASELINE_MODES = ("maestro", "openeta", "capx")

#: Harness/meta tools that are NOT part of the literal public skill API a
#: capx policy snippet may call.
_CAPX_META_TOOLS = {
    "done",
    "read_skill_code",
    "list_base_skills",
    "propose_new_skill",
    "propose_skill_update",
}

_CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


class ToolBudgetExhausted(RuntimeError):
    """Raised inside a capx policy when the shared tool budget is spent."""


def _eval_discipline() -> str:
    from roborsi.runtime_mode import evolution_enabled

    if evolution_enabled():
        return (
            "DISCIPLINE: use the existing skill surface; do not assume any "
            "helper that is not in the tool list."
        )
    return (
        "EVALUATION DISCIPLINE: the released capability set is frozen. Do "
        "not write code, register helpers, propose changes, or persist "
        "lessons. Use existing skills only and report an honest failure if "
        "they are insufficient."
    )


def run_baseline_atomic(
    *,
    agent_mode: str,
    text: str,
    atomic: str,
    seed: int,
    sess: Any,
    tool_budget: int = 40,
    backend_name: str | None = None,
    sim_task: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run one baseline attempt and return the same details dict shape as
    ``_run_atomic_3role(return_details=True)`` (plus ``agent_mode``)."""
    import time as _t

    from roborsi.agents import new_workspace
    from roborsi.agents.atomic_backend import resolve as _resolve_atomic
    from roborsi.agents.engineer import (
        Engineer,
        _configure_perception_backend,
        _summarize_trace,
    )
    from roborsi.embodied.agent_loop import get_backend
    from roborsi.embodied.agent_loop.config import _skill_namespace
    from roborsi.embodied.agent_loop.vlm_io import capture_usage, merge_usage
    from roborsi.runtime_mode import current_mode

    if agent_mode not in BASELINE_MODES:
        raise ValueError(
            f"unknown baseline agent_mode {agent_mode!r}; "
            f"expected one of {BASELINE_MODES}"
        )
    run_mode = current_mode().value
    resolved_model = model or Engineer.DEFAULT_MODEL
    run_started_at = _t.time()
    sess.append("baseline_start", agent_mode=agent_mode, atomic=atomic,
                seed=seed, run_mode=run_mode)
    workspace = new_workspace(atomic)
    sess.append("baseline_workspace", path=str(workspace.root))

    ab = _resolve_atomic(atomic)
    resolved_backend = backend_name or ab.backend_name
    resolved_sim_task = sim_task or ab.sim_task
    ns = _skill_namespace(resolved_backend)
    _configure_perception_backend(ns, resolved_model)

    backend = get_backend(resolved_backend)
    backend_ok, backend_reason = backend.available()
    if not backend_ok:
        raise RuntimeError(
            f"backend '{resolved_backend}' unavailable: {backend_reason}"
        )
    print(f"[baseline:{agent_mode}] 🎛️  backend={resolved_backend} "
          f"sim_task={resolved_sim_task}", flush=True)
    with backend.make_env(
        resolved_sim_task,
        {"require_depth": True},
    ) as active_env:
        initial_obs = active_env.reset(seed)
        task_instruction = str(
            getattr(active_env, "instruction", "")
            or (getattr(initial_obs, "extras", {}) or {}).get("instruction", "")
            or ""
        ).strip()
        sess.append(
            "baseline_environment_ready",
            agent_mode=agent_mode,
            backend=resolved_backend,
            sim_task=resolved_sim_task,
            has_task_instruction=bool(task_instruction),
        )
        with capture_usage() as agent_usage:
            remote_loop = getattr(active_env, "run_baseline_loop", None)
            if callable(remote_loop):
                result = remote_loop(
                    agent_mode=agent_mode, atomic=atomic, seed=seed,
                    tool_budget=tool_budget, model=resolved_model,
                    workspace=workspace, task_instruction=task_instruction, ns=ns,
                )
            elif agent_mode == "maestro":
                result = _run_maestro(
                    active_env, atomic=atomic, seed=seed,
                    tool_budget=tool_budget, model=resolved_model,
                    workspace=workspace, task_instruction=task_instruction,
                )
            elif agent_mode == "openeta":
                result = _run_openeta(
                    active_env, atomic=atomic, seed=seed,
                    tool_budget=tool_budget, model=resolved_model,
                    workspace=workspace, task_instruction=task_instruction,
                    ns=ns,
                )
            else:
                result = _run_capx(
                    active_env, atomic=atomic, seed=seed,
                    tool_budget=tool_budget, model=resolved_model,
                    workspace=workspace, task_instruction=task_instruction,
                    ns=ns,
                )

    total_wallclock_s = _t.time() - run_started_at
    usage = {"agent": agent_usage.to_dict(), "total": merge_usage(agent_usage)}
    sess.append("baseline_executed", agent_mode=agent_mode,
                success=result["success"], outcome=result["outcome"],
                tool_calls=result["tool_calls"], t=total_wallclock_s)

    # Same trace.db bookkeeping as the roborsi path — campaigns and the
    # cockpit read runs the same way regardless of orchestration.
    from roborsi.store import trace_db as _td
    _td.insert_run(workspace.run_id, task=atomic, seed=seed,
                   model=resolved_model, run_mode=run_mode)
    _td.update_run(
        workspace.run_id,
        status="success" if result["success"] else "failed",
        outcome=result["outcome"],
        video_path=result.get("video_path"),
        finished_at=_t.strftime("%Y-%m-%d %H:%M:%S"),
        wallclock_s=total_wallclock_s,
        episode_summary={
            "agent_mode": agent_mode,
            "tool_calls": result["tool_calls"],
            "run_mode": run_mode,
            "task_instruction": task_instruction,
            "usage": usage,
            "timing": result.get("timing") or {},
            "total_wallclock_s": total_wallclock_s,
        },
    )
    workspace.write_summary(
        f"# Baseline Summary · {atomic} (seed={seed})\n\n"
        f"**Agent mode**: `{agent_mode}` (no Planner / no Reviewer)\n"
        f"**Run mode**: `{run_mode}`\n"
        f"**Outcome**: `{result['outcome']}`\n"
        f"**Tool calls**: {result['tool_calls']} / budget {tool_budget}\n\n"
        "## Trace (first 12 steps)\n"
        "```\n"
        f"{_summarize_trace(result.get('trace') or [])}\n"
        "```\n"
    )

    badge = "✓" if result["success"] else "✗"
    reply = "\n".join([
        f"{badge} **{atomic}** seed={seed} · {result['outcome']} "
        f"({result['tool_calls']} tool calls)",
        f"Agent mode: `{agent_mode}` · Mode: `{run_mode}`",
        "",
        f"Workspace: `{workspace.root}`",
    ])
    return {
        "text": reply,
        "run_id": workspace.run_id,
        "workspace": str(workspace.root),
        "task": atomic,
        "backend": resolved_backend,
        "sim_task": resolved_sim_task,
        "seed": seed,
        "run_mode": run_mode,
        "agent_mode": agent_mode,
        "success": bool(result["success"]),
        "outcome": result["outcome"],
        "tool_calls": result["tool_calls"],
        "reviewer_verdict": None,
        "proposal_decision": "NO_PROPOSAL",
        "usage": usage,
        "timing": result.get("timing") or {},
        "models": {"agent": resolved_model},
        "total_wallclock_s": total_wallclock_s,
        "task_instruction": task_instruction,
        "video_path": result.get("video_path"),
    }


# ────────────────────────────────────────────────────────────────────────
# maestro — single orchestrator session over the shared tool loop
# ────────────────────────────────────────────────────────────────────────


def _run_maestro(env: Any, *, atomic: str, seed: int, tool_budget: int,
                 model: str, workspace: Any,
                 task_instruction: str) -> dict[str, Any]:
    from roborsi.agents.engineer import _summarize_tool_timing
    from roborsi.embodied.agent_loop.rollout import run_rollout

    visible = (task_instruction or "").strip()
    instruction = (
        (f"TASK INSTRUCTION:\n{visible}\n\n" if visible else "")
        + f"GOAL: complete the atomic task '{atomic}' so the instruction "
          "above is visibly satisfied.\n\n"
        "ROLE: You are MAESTRO — the single orchestrator. There is no "
        "separate planner or reviewer: you plan, execute, and verify "
        "yourself within this one session. Think through a short plan "
        "first, then drive the tools. When an action fails, diagnose from "
        "the latest image and retry a DIFFERENT approach; keep going until "
        "you succeed or the tool budget runs out.\n\n"
        + _eval_discipline()
    )
    m_result = run_rollout(
        env, seed=seed, task_name=atomic,
        instruction=instruction,
        expected_on_success=(
            visible or "the task instruction is visibly completed"
        ),
        model=model, tool_budget=tool_budget,
        workdir=workspace.root / "rollout",
        # Final verdict is the sim's OWN predicate, never the VLM's done().
        use_sim_predicate=True,
    )
    meta = dict(m_result.rollout.meta)
    return {
        "success": bool(m_result.success),
        "outcome": m_result.outcome,
        "tool_calls": len(m_result.trace),
        "trace": m_result.trace,
        "timing": _summarize_tool_timing(m_result.trace),
        "video_path": meta.get("demo_video"),
        "rollout_meta": meta,
    }


# ────────────────────────────────────────────────────────────────────────
# openeta — forced observe → think → act cycles
# ────────────────────────────────────────────────────────────────────────


def _run_openeta(env: Any, *, atomic: str, seed: int, tool_budget: int,
                 model: str, workspace: Any, task_instruction: str,
                 ns: str) -> dict[str, Any]:
    import time as _t

    from roborsi.agents.engineer import _summarize_tool_timing
    from roborsi.embodied.agent_loop.messages import (
        _append_image,
        _assistant_tool_calls_msg,
        _initial_messages,
    )
    from roborsi.embodied.agent_loop.prompt_tools import _build_tool_specs
    from roborsi.embodied.agent_loop.rollout import (
        DispatchContext,
        _dispatch_with_timeout,
        _tool_timing_phase,
    )
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools

    workdir = workspace.root / "rollout" / f"{atomic}-{seed}"
    workdir.mkdir(parents=True, exist_ok=True)
    state = DispatchContext(env=env, workdir=workdir, last_image_path=None,
                            ns=ns, task=atomic)
    state._tool_handlers = env.tool_handlers()
    tools = _build_tool_specs(ns=ns, task=atomic)
    visible = (task_instruction or "").strip()
    instruction = (
        (f"TASK INSTRUCTION:\n{visible}\n\n" if visible else "")
        + f"GOAL: complete the atomic task '{atomic}'.\n\n"
        "PROTOCOL (observe → think → act): each cycle the harness calls "
        "look() FOR you and attaches the fresh observation (this counts "
        "against the shared tool budget). You then THINK about the scene "
        "and reply with EXACTLY ONE tool call — the act phase. There is no "
        "plan document and no reviewer. Call done(success=...) when "
        "finished or hopeless.\n\n"
        + _eval_discipline()
    )
    convo = _initial_messages(
        instruction,
        visible or "the task instruction is visibly completed",
        task_name=atomic, ns=ns,
    )

    trace: list[dict[str, Any]] = []
    success = False
    outcome = "budget_exceeded"
    step_idx = 0
    while len(trace) < tool_budget:
        # 1. OBSERVE — harness-forced look(), charged to the shared budget.
        _t0 = _t.time()
        look_result, _ = _dispatch_with_timeout(
            state, {"tool": "look", "args": {}})
        trace.append({
            "step": step_idx, "tool_call": {"tool": "look", "args": {}},
            "forced_observation": True, "result": look_result,
            "wallclock_s": round(_t.time() - _t0, 6),
            "timing_phase": "perception",
        })
        convo.append({"role": "user", "content": (
            f"OBSERVE (cycle {step_idx}, {len(trace)}/{tool_budget} tool "
            "calls used): a fresh look() observation is attached. THINK "
            "about the current scene state and your progress, then ACT: "
            "reply with EXACTLY ONE tool call. Call done(success=...) if "
            "the task is finished or clearly hopeless."
        )})
        if (state.last_image_path is not None
                and state.last_image_path != state._attached_image_path):
            convo = _append_image(convo, state.last_image_path)
            state._attached_image_path = state.last_image_path
        if len(trace) >= tool_budget:
            break

        # 2. THINK + ACT — the VLM answers with exactly one tool call.
        msg = _call_vlm_tools(model, convo, tools)
        tool_calls = list(getattr(msg, "tool_calls", None) or [])
        if not tool_calls:
            convo.append({"role": "assistant",
                          "content": getattr(msg, "content", "") or " "})
            convo.append({"role": "user", "content": (
                "You must reply with exactly one tool_use block (the ACT "
                "phase). Choose one action now, or call done(success=...) "
                "if finished."
            )})
            msg = _call_vlm_tools(model, convo, tools)
            tool_calls = list(getattr(msg, "tool_calls", None) or [])
            if not tool_calls:
                outcome = "vlm_no_tool_call"
                break
        tc = tool_calls[0]      # observe→think→act: ONE action per cycle
        convo.append(_assistant_tool_calls_msg(msg, [tc]))
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        print(f"[openeta] cycle={step_idx} → {name}", flush=True)
        if name == "done":
            success = bool(args.get("success", False))
            outcome = "vlm_declared_done"
            trace.append({"step": step_idx,
                          "tool_call": {"tool": name, "args": args},
                          "result": {"acknowledged": True},
                          "wallclock_s": 0.0, "timing_phase": "other"})
            convo.append({"role": "tool", "tool_call_id": tc.id,
                          "name": name,
                          "content": json.dumps({"acknowledged": True},
                                                 ensure_ascii=False)})
            break
        _t0 = _t.time()
        result, _ = _dispatch_with_timeout(state, {"tool": name, "args": args})
        trace.append({
            "step": step_idx, "tool_call": {"tool": name, "args": args},
            "result": result,
            "wallclock_s": round(_t.time() - _t0, 6),
            "timing_phase": _tool_timing_phase(name),
        })
        convo.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                      "content": json.dumps(result, ensure_ascii=False,
                                             default=str)})
        step_idx += 1

    # Final, invisible simulator predicate — same adjudication as run_rollout.
    vlm_declared = success
    real_success = env.check_success()
    if vlm_declared and not real_success:
        outcome = "vlm_overclaimed"
        success = False
    if real_success and not vlm_declared:
        outcome = "predicate_passed_without_done"
        success = True
    _persist_trace(workdir, trace)
    return {
        "success": bool(success),
        "outcome": outcome,
        "tool_calls": len(trace),
        "trace": trace,
        "timing": _summarize_tool_timing(trace),
        "video_path": None,
    }


# ────────────────────────────────────────────────────────────────────────
# capx — one-shot code-as-policy over the literal public skill API
# ────────────────────────────────────────────────────────────────────────


def _run_capx(env: Any, *, atomic: str, seed: int, tool_budget: int,
              model: str, workspace: Any, task_instruction: str, ns: str,
              max_repairs: int = 3) -> dict[str, Any]:
    import io
    import math
    import time as _t
    import traceback
    from contextlib import redirect_stderr, redirect_stdout

    import numpy as np

    from roborsi.agents.engineer import _summarize_tool_timing
    from roborsi.embodied.agent_loop.messages import _append_image
    from roborsi.embodied.agent_loop.prompt_tools import (
        _build_tool_specs,
        _build_tools_block,
        _system_prompt,
    )
    from roborsi.embodied.agent_loop.rollout import (
        DispatchContext,
        _dispatch_with_timeout,
        _tool_timing_phase,
    )
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_no_tools
    # Reuse the exec sandbox's capability limits: whitelisted builtins and a
    # restricted importer — no file / network / process access from policy code.
    from roborsi.embodied.skills.base.exec_python.robotwin.policy import (
        _safe_builtins,
    )

    workdir = workspace.root / "rollout" / f"{atomic}-{seed}"
    workdir.mkdir(parents=True, exist_ok=True)
    state = DispatchContext(env=env, workdir=workdir, last_image_path=None,
                            ns=ns, task=atomic)
    state._tool_handlers = env.tool_handlers()

    trace: list[dict[str, Any]] = []
    calls = {"n": 0}

    def _bind(name: str):
        def fn(**kwargs: Any) -> dict[str, Any]:
            if calls["n"] >= tool_budget:
                raise ToolBudgetExhausted(
                    f"tool budget of {tool_budget} skill calls exhausted"
                )
            calls["n"] += 1
            _t0 = _t.time()
            result, _ = _dispatch_with_timeout(
                state, {"tool": name, "args": kwargs})
            trace.append({
                "step": calls["n"] - 1,
                "tool_call": {"tool": name, "args": kwargs},
                "result": result,
                "wallclock_s": round(_t.time() - _t0, 6),
                "timing_phase": _tool_timing_phase(name),
            })
            return result
        fn.__name__ = name
        fn.__doc__ = f"Literal public skill {name!r}; returns its result dict."
        return fn

    public_names = [
        spec["function"]["name"]
        for spec in _build_tool_specs(ns=ns, task=atomic)
        if spec["function"]["name"] not in _CAPX_META_TOOLS
    ]
    bound: dict[str, Any] = {name: _bind(name) for name in public_names}
    return_dict: dict[str, Any] = {}
    bound.update({
        "np": np, "numpy": np, "math": math,
        "return_dict": return_dict,
        "__builtins__": _safe_builtins(),
    })

    # One initial observation for code generation (charged to the budget,
    # like every other skill call).
    if "look" in bound:
        try:
            bound["look"]()
        except Exception:
            pass

    visible = (task_instruction or "").strip()
    system = (
        _system_prompt(ns=ns)
        + "\n\nROLE: You are a CODE-AS-POLICY agent. You do NOT interact "
        "step by step. You write ONE Python policy snippet; the harness "
        "executes it and the simulator alone judges the final state.\n\n"
        + _eval_discipline()
    )
    user_text = (
        (f"TASK INSTRUCTION:\n{visible}\n\n" if visible else "")
        + f"GOAL: complete the atomic task '{atomic}'.\n\n"
        "PUBLIC SKILL API (each is pre-bound as a module-level Python "
        "function returning that skill's result dict, e.g. "
        "r = grasp_object(object_query='red block')):\n"
        f"{_build_tools_block(ns=ns)}\n\n"
        "CONTRACT:\n"
        "- Emit exactly ONE fenced ```python code block containing the full "
        "policy (straight-line code with light control flow is fine).\n"
        "- Call ONLY the listed skill functions plus math/numpy; there is a "
        f"shared budget of {tool_budget} skill calls TOTAL (all repair "
        "rounds included) — exceeding it aborts the policy.\n"
        "- Check each result dict's 'ok'/'reason' fields defensively.\n"
        "- Do NOT call done(); there is no done. The simulator judges the "
        "final scene state after your code finishes.\n"
        "- You may stash values in return_dict for your own debugging.\n"
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ]
    if state.last_image_path is not None:
        messages = _append_image(messages, state.last_image_path)

    exec_error: str | None = None
    executed = False
    for round_idx in range(1 + max_repairs):
        reply = _call_vlm_no_tools(model, messages)
        messages.append({"role": "assistant", "content": reply or " "})
        code = _extract_code(reply or "")
        if not code:
            exec_error = "no fenced ```python block in the model reply"
            messages.append({"role": "user", "content": (
                "No fenced ```python block found. Emit exactly one fenced "
                "python block containing the full policy."
            )})
            continue
        (workdir / f"policy_round{round_idx}.py").write_text(
            code, encoding="utf-8")
        if executed:
            # Re-run the repaired policy from the same initial state.
            env.reset(seed)
        executed = True
        exec_error = None
        stdout = io.StringIO()
        budget_dead = False
        with redirect_stdout(stdout), redirect_stderr(stdout):
            try:
                exec(compile(code, "<capx-policy>", "exec"), bound, bound)
            except ToolBudgetExhausted as exc:
                exec_error = f"ToolBudgetExhausted: {exc}"
                budget_dead = True
            except Exception as exc:
                exec_error = (
                    f"{type(exc).__name__}: {exc}\n"
                    f"{traceback.format_exc(limit=5)}"
                )
        if exec_error is None:
            break
        if budget_dead or calls["n"] >= tool_budget:
            break       # nothing left to repair with
        print(f"[capx] round {round_idx} failed: "
              f"{exec_error.splitlines()[0][:160]}", flush=True)
        messages.append({"role": "user", "content": (
            f"Your policy raised an error (repair round "
            f"{round_idx + 1}/{max_repairs}):\n{exec_error[:2000]}\n\n"
            f"stdout tail:\n{stdout.getvalue()[-1500:]}\n\n"
            f"Budget remaining: {tool_budget - calls['n']} skill calls. "
            "The environment is reset to the initial state before your "
            "corrected policy re-runs. Emit ONE corrected full-replacement "
            "```python block."
        )})

    # Final, invisible simulator predicate — the only success authority.
    success = bool(env.check_success())
    if success:
        outcome = "capx_predicate_passed"
    elif exec_error is not None:
        outcome = "capx_exec_error"
    elif not executed:
        outcome = "capx_no_code"
    else:
        outcome = "capx_predicate_failed"
    _persist_trace(workdir, trace)
    return {
        "success": success,
        "outcome": outcome,
        "tool_calls": calls["n"],
        "trace": trace,
        "timing": _summarize_tool_timing(trace),
        "video_path": None,
        "exec_error": exec_error,
    }


def _extract_code(reply: str) -> str:
    for match in _CODE_BLOCK.finditer(reply):
        block = match.group(1).strip()
        if block:
            return block
    return ""


def _persist_trace(workdir: Any, trace: list[dict[str, Any]]) -> None:
    try:
        (workdir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:      # pragma: no cover — best-effort debugging aid
        try:
            (workdir / "trace_error.txt").write_text(
                f"{type(exc).__name__}: {exc}", encoding="utf-8")
        except Exception:
            pass
