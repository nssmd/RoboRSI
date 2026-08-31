"""Universal VLM tool-loop driver — backend-agnostic ``run_rollout``.

Drives one episode against ANY ``Env`` (LIBERO sim, LIBERO, future real
robot): the VLM decides a tool call, we dispatch it, snapshot the result, loop
until ``done`` or budget. The loop touches the environment ONLY through the
``Env`` seam methods (``take_snapshot`` / ``check_success`` / ``hook_physics_step``
/ ``tool_handlers``) — no ``env._impl`` / sim internals leak in here — so the
same logic serves every backend. The ``_do_<name>`` tool implementations that a
backend registers via ``tool_handlers()`` are what stay backend-specific.

Wires a VLM against a fixed set of *real-world-realistic* tools (look /
find_pixel / move_to_pixel / done, plus base skills), capturing one
(frame + tool_call + result) tuple per step into a Rollout so the data is dense.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from roborsi.embodied.agent_loop.config import DEFAULT_MODEL, _skill_namespace
from roborsi.embodied.agent_loop.env import Observation, Rollout, Step
from roborsi.embodied.agent_loop.messages import (
    _append_image,
    _assistant_tool_calls_msg,
    _initial_messages,
    _summarize_old_trace,
)
from roborsi.embodied.agent_loop.prompt_tools import (
    _build_status_check_prompt,
    _build_tool_specs,
    _dispatch_meta_tool,
    _maybe_shortlist_skills,
    _try_load_compound_dispatcher,
    _try_load_plugin_dispatcher,
)
from roborsi.embodied.agent_loop.vlm_io import (
    _call_vlm_no_tools,
    _call_vlm_tools,
    reset_usage_metrics,
    usage_metrics_snapshot,
)
from roborsi.embodied.sim.libero.run_records import EpisodeIdentity


def _trajectory_capture_config() -> tuple[int, int]:
    def positive_int(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, str(default))))
        except ValueError:
            return default

    return (
        positive_int("ROBORSI_TRAJECTORY_SUBSAMPLE", 20),
        positive_int("ROBORSI_TRAJECTORY_MAX_CAPTURES", 400),
    )


def _fmt_args(args: dict[str, Any]) -> str:
    """Compact one-line rendering of tool-call args for the CLI, so a run shows
    WHAT the agent is doing each step (e.g. press_button_at_xyz(x=0.1, y=-0.05,
    z=0.29)) instead of just the arg key names. Floats round to 3 dp; long
    strings / lists are truncated."""
    def _v(x: Any) -> str:
        if isinstance(x, float):
            return f"{x:.3f}"
        if isinstance(x, (list, tuple)):
            body = ", ".join(_v(e) for e in list(x)[:4])
            return f"[{body}{', ...' if len(x) > 4 else ''}]"
        s = str(x)
        return s if len(s) <= 40 else s[:37] + "..."
    return ", ".join(f"{k}={_v(v)}" for k, v in list(args.items())[:6])


_IMAGE_TOOLS = {
    "look",
    "observe_orbit",
    "mark_orbit_point",
    "preview_move_to_pose",
    "find_pixel",
    "find_by_detector",
    "find_by_pointing",
    "zoom_in",
}
_ACTION_TOOLS = {
    "descend_tcp_to_z",
    "move_ee_delta",
    "move_to_pose",
    "move_fingertip_to",
    "move_to_pixel",
    "gripper",
    "set_gripper",
    "home",
    "grasp_then_lift",
    "grasp_then_lift_graspgen",
    "grasp_object",
    "pick_actor_by_contact_point",
    "place_object_in",
    "place_on_surface",
    "place_beside",
    "place_held_at_target_servo",
    "pull_drawer",
    "close_drawer",
    "open_hinged_door",
    "push_object",
    "recover_joint_posture",
    "tap_held_on_target",
    "execute_with_pi05",
    "execute_previewed_move",
}
_PERCEPTION_TOOLS = _IMAGE_TOOLS | {
    "unproject_pixel",
    "get_arm_pose",
    "is_holding",
    "is_reachable",
    "measure_distance",
    "get_grasp_pose",
    "verify_pick_complete",
    "detect_object",
    "get_object_bbox",
}
_RECOVERY_TOOLS = {
    "home",
    "recover_joint_posture",
    "plan",
    "list_base_skills",
    "read_skill_code",
    "propose_new_skill",
    "done",
}
_RECOVERY_REVIEW_FAILURE_THRESHOLD = 2
_RECOVERY_REVIEW_MAX_CALLS = 3
_SEMANTIC_ACTION_SUCCESS_FIELDS = {
    "grasp_object": ("grasped",),
    "grasp_then_lift": ("grasped",),
    "grasp_then_lift_graspgen": ("grasped",),
    "pick_actor_by_contact_point": ("grasped",),
    "place_object_in": ("placed",),
    "place_on_surface": ("placed",),
    "place_beside": ("placed",),
    "place_held_at_target_servo": ("placed",),
}


def _action_result_is_failure(tool_name: str, result: object) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("ok") is False:
        return True
    for result_field in _SEMANTIC_ACTION_SUCCESS_FIELDS.get(str(tool_name), ()):
        if result_field in result and result.get(result_field) is not True:
            return True
    if "reached" in result and result.get("reached") is False:
        return True
    return False


def _recovery_review_due(*, action_failures: int, reviews_used: int) -> bool:
    return (
        int(action_failures) >= _RECOVERY_REVIEW_FAILURE_THRESHOLD
        and int(reviews_used) < _RECOVERY_REVIEW_MAX_CALLS
    )


def _build_recovery_review_messages(
    instruction: str,
    trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    visible_events = []
    allowed_result_fields = (
        "ok",
        "reason",
        "ee_pos",
        "position_error",
        "moved_distance",
        "grasped",
        "holding",
        "released",
        "reachable",
        "failed_phase",
        "joint_error_max",
        "gripper_state",
    )
    for event in trace[-12:]:
        tool_call = event.get("tool_call") or {}
        tool = str(tool_call.get("tool") or "")
        if not tool:
            continue
        result = event.get("result") or {}
        visible_events.append(
            {
                "step": event.get("step"),
                "tool": tool,
                "args": tool_call.get("args") or {},
                "result": {
                    key: result[key]
                    for key in allowed_result_fields
                    if key in result
                },
            }
        )
    return [
        {
            "role": "system",
            "content": (
                "You are the independent roborsi Recovery Reviewer. "
                "Use only the visible task, recent tool calls, tool returns, "
                "and attached current image. Identify one causal failure "
                "pattern and propose one materially different recovery plan. "
                "Do not declare task completion and do not emit tool calls. "
                "If repeated motion calls make no progress and the gripper is "
                "empty, consider recover_joint_posture before more Cartesian "
                "retries. "
                "Be concrete and stay under 180 words."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Visible task and plan:\n{instruction[:6000]}\n\n"
                "Recent visible trace:\n"
                + json.dumps(visible_events, ensure_ascii=False, default=str)[:10000]
            ),
        },
    ]


def _request_recovery_review(
    model: str,
    instruction: str,
    trace: list[dict[str, Any]],
    image_path: Path | None,
) -> str:
    messages = _build_recovery_review_messages(instruction, trace)
    if image_path is not None and image_path.is_file():
        messages = _append_image(messages, image_path)
    return _call_vlm_no_tools(model, messages).strip()[:4000]


def _is_action_tool(
    tool_name: str,
    compound_tool_names: set[str] | frozenset[str] = frozenset(),
) -> bool:
    return tool_name in _ACTION_TOOLS or tool_name in compound_tool_names


def _is_strong_surface_completion(
    tool_name: str,
    result: object,
) -> bool:
    if tool_name != "place_on_surface" or not isinstance(result, dict):
        return False
    if not all(
        result.get(field) is True
        for field in (
            "ok",
            "reached",
            "released",
            "gripper_opened",
            "source_clear_verified",
            "gripper_hold_continuity",
        )
    ):
        return False
    if result.get("object_release_verified") is False:
        return False
    try:
        error = float(result.get("pre_release_error"))
    except (TypeError, ValueError, OverflowError):
        return False
    return 0.0 <= error <= 0.015


def _auto_complete_strong_place_enabled() -> bool:
    return os.environ.get(
        "ROBORSI_AUTO_COMPLETE_STRONG_PLACE",
        "0",
    ) == "1"


def _task_completion_latch_enabled() -> bool:
    """Task completion is host-adjudicated only; action evidence never latches it."""
    return False


def _is_code_backed_completion(tool_name: str, result: object) -> bool:
    if tool_name != "visual_pick_place" or not isinstance(result, dict):
        return False
    if not all(
        result.get(field) is True
        for field in ("ok", "grasped", "placed", "released")
    ):
        return False
    phases = {
        str(row.get("phase")): row
        for row in result.get("trace") or ()
        if isinstance(row, dict)
    }
    grasp = phases.get("grasp") or {}
    place = phases.get("place") or {}
    return bool(
        grasp.get("holding") is True
        and grasp.get("identity_verified") is True
        and place.get("ok") is True
        and place.get("released") is True
    )


def partition_same_turn_image_calls(tool_calls):
    accepted = []
    rejected = []
    image_pending = False
    for call in tool_calls:
        name = getattr(getattr(call, "function", None), "name", "")
        if name in _IMAGE_TOOLS:
            if image_pending:
                rejected.append({"tool_call": call, "reason": "image_already_pending"})
                continue
            image_pending = True
        accepted.append(call)
    return accepted, rejected


@dataclass
class RolloutResult:
    rollout: Rollout
    success: bool
    outcome: str
    trace: list[dict[str, Any]]
    # Final visible conversation retained for trace inspection.
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DispatchContext:
    """Per-episode dispatch state. ``env`` is any backend ``Env``; the loop
    reaches the backend only through its seam methods. The trailing fields are
    dynamic runtime state (contamination guard, per-call timeout history,
    memoized tool registry) — declared here so they survive as real fields."""

    env: Any
    workdir: Path
    last_image_path: Path | None
    ns: str = "libero"
    # Running atomic task name — scopes opt-in solidified compounds
    # (atomic/<task>/<name>/policy.py) to their own task at dispatch time.
    task: str = ""
    _sim_contaminated: bool = False
    _timeout_history: dict[str, int] = field(default_factory=dict)
    _tool_handlers: dict[str, Callable] | None = None
    _attached_image_path: Path | None = None
    _allowed_tools: set[str] | None = None


# ────────────────────────────────────────────────────────────────────────
# Top-level entry: drive one episode to completion.
# ────────────────────────────────────────────────────────────────────────


def run_rollout(
    env: Any,                                           # Env with reset() already called
    *,
    seed: int,
    task_name: str,
    instruction: str,
    expected_on_success: str,
    model: str | None = None,
    tool_budget: int = 25,
    workdir: Path | None = None,
    use_sim_predicate: bool = True,
    restrict_to_names: set[str] | None = None,
    prior_messages: list[dict[str, Any]] | None = None,
    episode_meta: dict[str, Any] | None = None,
    include_skill_task_truth: bool = True,
) -> RolloutResult:
    """Success adjudication is the SIM predicate by DEFAULT (use_sim_predicate=True):
    the final verdict is env.check_success(), computed AFTER the VLM loop ends —
    the Engineer NEVER sees it during the episode and cannot self-report success.
    If the VLM declared done(success=True) but the sim predicate disagrees →
    vlm_overclaimed (success=False); if the predicate passes without a done →
    predicate_passed_without_done (success=True).

    `restrict_to_names`: optional set of base skill names. When provided,
    only those skills appear in the inner VLM's tool list for the entire
    episode. Engineer (agents/engineer.py) uses this with the SkillSelector
    top-K when total skill count exceeds SKILL_LIST_SOFT_CAP.

    `prior_messages` is an API guard. Public short evaluation always starts a
    fresh episode conversation and rejects carried context."""
    import time as _time

    episode_started = _time.monotonic()
    reset_usage_metrics()
    if workdir is None:
        workdir = Path("/tmp/roborsi-zeroshot") / f"{task_name}-{seed}"
    else:
        workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    rollout = Rollout(task=task_name, seed=seed)
    trace: list[dict[str, Any]] = []
    # Resolve the active backend's skill namespace once per episode.
    ns = _skill_namespace(getattr(env, "backend_name", None))
    state = DispatchContext(env=env, workdir=workdir, last_image_path=None,
                            ns=ns, task=task_name)
    # Memoize the backend's tool registry once per episode (was the old
    # module-global _ensure_registry cache). Env-agnostic: each backend
    # supplies its own _do_* map via tool_handlers().
    state._tool_handlers = env.tool_handlers()

    # Initial frame.
    obs = env.take_snapshot()
    rollout.steps.append(
        Step(
            obs=obs,
            action=obs.state,
            info={
                "phase": "reset",
                "source": "reset",
                "action_type": "state_proxy",
            },
        )
    )

    # Hook the backend's physics loop so each tick during tool execution lands a
    # (state, action=next-state qpos) tuple. Same data shape as expert_replay
    # so downstream lerobot_build can ingest both. Backends without a steppable
    # physics loop (real robot, robosuite) inherit a no-op hook and capture
    # nothing mid-tool.
    # Each capture renders every camera and depth map. Sparse capture keeps
    # playback smooth without letting a long motion spend the tool budget on
    # rendering. Past the cap, physics continues while frame capture stops.
    subsample_every, max_captures = _trajectory_capture_config()
    tick_counter = {"n": 0, "captured": 0}

    def _on_tick(controller_action=None) -> None:
        tick_counter["n"] += 1
        if tick_counter["n"] % subsample_every != 0:
            return
        if tick_counter["captured"] >= max_captures:
            return
        tick_counter["captured"] += 1
        sim_obs = env.take_snapshot()
        rollout.steps.append(Step(
            obs=sim_obs,
            action=(
                controller_action
                if controller_action is not None
                else sim_obs.state
            ),
            info={
                "tick": tick_counter["n"],
                "source": "scene_step",
                "action_type": (
                    "controller"
                    if controller_action is not None
                    else "state_proxy"
                ),
            },
        ))
        # Also dump a head_camera RGB frame for the demo renderer. Cheap:
        # one ~25KB jpg every 5 ticks gives us smooth-ish playback later.
        head_rgb = sim_obs.images.get("head_camera")
        if head_rgb is not None:
            import cv2 as _cv2
            tick_path = workdir / f"tick_{tick_counter['n']:05d}.jpg"
            _cv2.imwrite(str(tick_path), _cv2.cvtColor(head_rgb, _cv2.COLOR_RGB2BGR))

    unhook = env.hook_physics_step(_on_tick)

    if prior_messages is not None:
        raise ValueError("public LIBERO short episodes do not accept carried conversations")
    if restrict_to_names is None:
        restrict_to_names = _maybe_shortlist_skills(instruction, task_name, seed, ns=ns)
    convo = _initial_messages(
        instruction,
        expected_on_success,
        task_name=task_name,
        restrict_to_names=restrict_to_names,
        ns=ns,
        include_skill_task_truth=include_skill_task_truth,
    )
    tools = _build_tool_specs(ns=ns, task=task_name)
    state._allowed_tools = {
        str(row["function"]["name"])
        for row in tools
    }
    compound_tool_names: set[str] = set()
    if (
        os.environ.get("ROBORSI_ATOMIC_COMPOUND") == "1"
        and os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") == "0"
    ):
        from roborsi.embodied.skills import discover_compounds

        compound_tool_names = {
            skill.name for skill in discover_compounds(task_name)
        }
    success = False
    completion_candidate = False
    outcome = "budget_exceeded"
    reflect_every = 8  # inject reflection prompt every N steps without done
    # Compact older trace into a synthetic summary before the conversation
    # exceeds the bounded context budget.
    summarize_at_msgs = 30
    import time as _t
    phase_seconds = {
        "vlm": 0.0,
        "perception": 0.0,
        "action": 0.0,
        "recovery": 0.0,
    }
    action_failures_since_review = 0
    recovery_reviewer_calls = 0
    recovery_reviewer_errors = 0
    for step_idx in range(tool_budget):
        _t0 = _t.time()
        # Summarize old trace if conversation got too long.
        if len(convo) > summarize_at_msgs:
            convo = _summarize_old_trace(convo, keep_recent=12)
            print(f"[zeroshot] step={step_idx} convo summarized to "
                   f"{len(convo)} msgs", flush=True)
        print(f"[zeroshot] step={step_idx} convo_msgs={len(convo)} tools={len(tools)}", flush=True)
        # Embed the newest view into the convo ONCE, but keep last_image_path
        # pointing at it so crop tools (zoom_in, find_pixel-on-view) can still
        # read the file this turn. Nulling it here used to make zoom_in fail
        # every call ("no recent image") — the VLM sees an image it can never
        # zoom into. Re-embed guard = the path we last attached.
        if (state.last_image_path is not None
                and state.last_image_path != state._attached_image_path):
            convo = _append_image(convo, state.last_image_path)
            state._attached_image_path = state.last_image_path

        # Force a reflection turn periodically — don't let VLM grind 25 attempts
        # without stepping back. Zero-shot loop pattern: "describe what happened,
        # identify cause, plan next steps".
        if step_idx > 0 and step_idx % reflect_every == 0:
            convo.append({"role": "user", "content": (
                f"REFLECTION CHECKPOINT (step {step_idx}/{tool_budget}). Stop and analyse:\n"
                "  1. What's the current state of the scene + arms? (look at the latest image)\n"
                "  2. What have you tried? Which tool calls failed and why?\n"
                "  3. What's the failure pattern? (e.g. 'fingers close above the cube' / "
                "'gripper grabs bowl rim instead of cube')\n"
                "  4. What's the next plan? Try a DIFFERENT strategy than what already failed.\n"
                "  5. Issue your next tool call(s) — you may emit MULTIPLE tool_use blocks "
                "in one assistant turn to compose a multi-step plan (e.g. unproject + "
                "move_to_pose hover + move_to_pose descend + gripper close in one turn)."
            )})

        print(f"[zeroshot] step={step_idx} calling _call_vlm_tools...", flush=True)
        msg = _call_vlm_tools(model or DEFAULT_MODEL, convo, tools)
        vlm_elapsed = _t.time() - _t0
        phase_seconds["vlm"] += vlm_elapsed
        print(f"[zeroshot] step={step_idx} got response in {vlm_elapsed:.1f}s "
              f"tool_calls={len(getattr(msg,'tool_calls',None) or [])}", flush=True)
        tool_calls = list(getattr(msg, "tool_calls", None) or [])
        if not tool_calls:
            # VLM produced text instead of a tool call. Retry up to 2 times by
            # nudging it to act, then give up. Without this, GPT-5.4 sometimes
            # returns a "let me think" text and we abandon the whole atomic.
            for retry_idx in range(2):
                trace.append({"step": step_idx, "tool_call": None,
                              "raw": (getattr(msg, "content", "") or "")[:200],
                              "no_tool_retry": retry_idx})
                convo.append({"role": "assistant",
                              "content": getattr(msg, "content", "") or " "})
                convo.append({"role": "user", "content": (
                    "You returned no tool_use blocks. You MUST call at least "
                    "one tool per turn — write text-only responses are not "
                    "valid here. Look at the latest image, decide on the next "
                    "action, and emit at least one tool_use block now. If you "
                    "believe the goal is met, call done(success=True). If "
                    "stuck, call look() and reassess."
                )})
                retry_started = _t.time()
                msg = _call_vlm_tools(model or DEFAULT_MODEL, convo, tools)
                phase_seconds["vlm"] += _t.time() - retry_started
                tool_calls = list(getattr(msg, "tool_calls", None) or [])
                if tool_calls:
                    break
            if not tool_calls:
                trace.append({"step": step_idx, "tool_call": None,
                              "raw": (getattr(msg, "content", "") or "")[:200],
                              "no_tool_retry": "exhausted"})
                convo.append({"role": "assistant",
                              "content": getattr(msg, "content", "") or " "})
                outcome = "vlm_no_tool_call"
                break

        # Append the assistant's tool_calls turn to the convo (raw, so litellm
        # can match tool_call_id on the next user message).
        convo.append(_assistant_tool_calls_msg(msg, tool_calls))
        accepted_calls, rejected_calls = partition_same_turn_image_calls(tool_calls)
        for rejected in rejected_calls:
            tc = rejected["tool_call"]
            name = tc.function.name
            reason = rejected["reason"]
            trace.append(
                {
                    "step": step_idx,
                    "tool_call": {"tool": name, "args": {}},
                    "tool_call_id": tc.id,
                    "result": {"ok": False, "reason": reason},
                }
            )
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps({"ok": False, "reason": reason}, ensure_ascii=False),
                }
            )

        # Dispatch each tool call (Responses / litellm may emit multiple in one
        # turn; we run them in order).
        any_done = False
        turn_action_failures = 0
        for tc in accepted_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            print(f"[zeroshot] step={step_idx} -> {name}({_fmt_args(args)})",
                  flush=True)
            _t_dispatch = _t.time()
            trace.append({"step": step_idx, "tool_call": {"tool": name, "args": args},
                          "tool_call_id": tc.id})
            if name == "done":
                success = bool(args.get("success", False))
                outcome = "vlm_declared_done"
                convo.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                              "content": json.dumps({"acknowledged": True}, ensure_ascii=False)})
                any_done = True
                break
            # exec_python lets Engineer write arbitrary sim loops; without
            # a tight cap it can bypass the tool budget. Hard-cap it so the
            # Engineer returns to bounded atomic tools.
            # Once exec_python (or anything) times out, the worker thread
            # cannot be killed (Python GIL limit) — it's still holding
            # sim. Mark the state contaminated so subsequent dispatches
            # fail fast and Engineer surrenders the attempt; next attempt
            # gets a clean restore_scene.
            if state._sim_contaminated:
                result = ({"ok": False, "success": False,
                            "reason": ("This attempt's simulator state is "
                                          "contaminated: a prior exec_python "
                                          "/ long tool call timed out and its "
                                          "worker thread is still holding the "
                                          "sim. All subsequent tool calls in "
                                          "this attempt will return ok=False. "
                                          "Call done(success=False) now; the "
                                          "next attempt will restore a clean "
                                          "sim and you can try a different "
                                          "approach.")},
                           Observation())
                after_obs = result[1]
                result = result[0]
                # Synthesize an "instant dispatch" log line.
                print(f"[zeroshot] step={step_idx} tool={name} "
                       f"dispatched in 0.0s ok=False (contaminated)",
                       flush=True)
            else:
                # Per-tool wall-time cap. SIGALRM-backed (main thread).
                # exec_python = 60s (Engineer code, should be quick).
                # Other cuRobo-heavy tools = 600s. Raised from 300s: under
                # heavy GPU contention (a co-tenant training job pinning the
                # GPU to 100%) SAPIEN physics stepping slows ~30-90x, so a
                # single pick_actor (which internally tries 2 candidate grasps
                # at ~90s exec each) legitimately needs 180-300s and the old
                # 300s cap killed grasps that WOULD have completed (measured on
                # lift_pot). 600s lets contention-slowed-but-valid plans finish;
                # a true infinite hang still bails, just later.
                tool_timeout = 60.0 if name == "exec_python" else 600.0
                result, after_obs = _dispatch_with_timeout(
                    state, {"tool": name, "args": args}, timeout_s=tool_timeout)
                # Contamination check: if this call timed out AND the
                # reason marker mentions wall-time, mark state dirty.
                if (isinstance(result, dict)
                        and "TIMEOUT" in str(result.get("reason", ""))[:20]):
                    state._sim_contaminated = True
            dispatch_elapsed = _t.time() - _t_dispatch
            if name in _PERCEPTION_TOOLS:
                phase_seconds["perception"] += dispatch_elapsed
            elif _is_action_tool(name, compound_tool_names):
                phase_seconds["action"] += dispatch_elapsed
            else:
                phase_seconds["recovery"] += dispatch_elapsed
            print(f"[zeroshot] step={step_idx} tool={name} "
                  f"dispatched in {dispatch_elapsed:.1f}s ok={result.get('ok')}", flush=True)
            if _is_action_tool(name, compound_tool_names):
                if _action_result_is_failure(name, result):
                    turn_action_failures += 1
            trace[-1]["result"] = result
            trace[-1]["tick_end"] = tick_counter["n"]
            rollout.steps.append(Step(
                obs=after_obs,
                action=after_obs.state,
                info={
                    "step": step_idx,
                    "tool": name,
                    "source": "tool_boundary",
                    "action_type": "state_proxy",
                },
            ))
            convo.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                          "content": json.dumps(result, ensure_ascii=False)})
            code_completion = _is_code_backed_completion(name, result)
            surface_completion = bool(
                _auto_complete_strong_place_enabled()
                and _is_strong_surface_completion(name, result)
            )
            if (
                _task_completion_latch_enabled()
                and use_sim_predicate
                and ns == "libero"
                and (code_completion or surface_completion)
            ):
                completion_candidate = True
                outcome = (
                    "code_backed_completion_candidate"
                    if code_completion
                    else "tool_completion_candidate"
                )
                any_done = True
                print(
                    "[zeroshot] verified tool completion latched; "
                    "ending tool loop for simulator adjudication",
                    flush=True,
                )
                break
        if any_done:
            break

        if turn_action_failures:
            action_failures_since_review += turn_action_failures
        if _recovery_review_due(
            action_failures=action_failures_since_review,
            reviews_used=recovery_reviewer_calls,
        ):
            review_started = _t.time()
            try:
                advice = _request_recovery_review(
                    model=model or DEFAULT_MODEL,
                    instruction=instruction,
                    trace=trace,
                    image_path=state.last_image_path,
                )
                if advice:
                    recovery_reviewer_calls += 1
                    trace.append(
                        {
                            "step": step_idx,
                            "event": "recovery_review",
                            "result": {"advice": advice},
                        }
                    )
                    convo.append(
                        {
                            "role": "user",
                            "content": (
                                "INDEPENDENT RECOVERY REVIEW (advisory, not a "
                                "task verdict):\n" + advice
                            ),
                        }
                    )
                    print(
                        f"[reviewer-recovery] step={step_idx} "
                        f"{advice[:600]}",
                        flush=True,
                    )
            except Exception as exc:  # noqa: BLE001
                recovery_reviewer_errors += 1
                trace.append(
                    {
                        "step": step_idx,
                        "event": "recovery_review_error",
                        "result": {
                            "error": f"{type(exc).__name__}: {exc}"[:1000]
                        },
                    }
                )
                print(
                    f"[reviewer-recovery] step={step_idx} error="
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            finally:
                phase_seconds["recovery"] += _t.time() - review_started
                action_failures_since_review = 0

        # CaP-X-inspired forced reflection after each turn: don't let VLM
        # drift between turns without explicitly classifying what to do
        # next. Replaces "VLM freelance" with a 3-way structured decision.
        # Skip when the turn was pure-observation (look/find_pixel/etc.) —
        # only inject after ACTION-class tools, where state actually changed.
        had_action = any(
            _is_action_tool(tc.function.name, compound_tool_names)
            for tc in accepted_calls
        )
        if had_action and step_idx < tool_budget - 1:
            convo.append({"role": "user", "content": _build_status_check_prompt()})
    unhook()

    # Persist trace for offline debugging — external executor executor doesn't save it.
    try:
        (workdir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        (workdir / "trace_error.txt").write_text(f"{type(exc).__name__}: {exc}")

    vlm_declared = success
    adjudication_claim = vlm_declared or completion_candidate
    success, outcome, real_success = adjudicate(
        env,
        vlm_declared=adjudication_claim,
        outcome=outcome,
        use_sim_predicate=use_sim_predicate,
    )

    # NOTE: zeroshot trace persistence is now ONLY done by the LH triangle
    # (LHExecutor) AFTER atomic_judge runs and returns success. We used to
    # persist here on vlm_declared=True, but VLM frequently overclaims (declares
    # done(success=true) even when the grasp missed), polluting the RAG corpus
    # with false positives. The judge-gated persistence in LHExecutor is the
    # source of truth.
    rollout.success = success
    rollout.outcome = outcome
    # Keep demo videos for every verdict category; retention is handled outside
    # the evaluator.
    meta = dict(episode_meta or {})
    identity = EpisodeIdentity(
        run_id=str(meta.get("run_id") or "legacy-run"),
        task_key=str(meta.get("task_key") or task_name),
        seed=int(meta.get("seed", seed)),
        shard=int(meta.get("shard", 0)),
        attempt=int(meta.get("attempt", 1)),
    )
    category = "task_success" if success else "task_failure"
    media_root = Path(meta.get("media_root")) if meta.get("media_root") else (workdir / "videos")
    rollout_video = None
    preview_video = None
    media_errors: list[str] = []
    try:
        rollout_video = _finalize_demo_video(
            workdir,
            identity=identity,
            category=category,
            media_root=media_root,
        )
    except Exception as exc:  # noqa: BLE001
        media_errors.append(f"{type(exc).__name__}: {exc}")
    finalize_preview = getattr(env, "finalize_preview", None)
    if callable(finalize_preview):
        try:
            preview_video = finalize_preview(
                identity=identity,
                category=category,
                media_root=media_root,
            )
        except Exception as exc:  # noqa: BLE001
            media_errors.append(f"preview_finalize_error:{type(exc).__name__}: {exc}")
    usage = usage_metrics_snapshot()
    rollout.meta = {
        "backend": env.backend_name,
        "collector": "rollout_vlm",
        "model": model or DEFAULT_MODEL,
        "elapsed_s": round(_time.monotonic() - episode_started, 3),
        "tool_calls": len(trace),
        **usage,
        "vlm_time_s": round(phase_seconds["vlm"], 3),
        "perception_time_s": round(phase_seconds["perception"], 3),
        "action_time_s": round(phase_seconds["action"], 3),
        "recovery_time_s": round(phase_seconds["recovery"], 3),
        "recovery_reviewer_calls": recovery_reviewer_calls,
        "recovery_reviewer_errors": recovery_reviewer_errors,
        "vlm_declared": vlm_declared,
        "tool_completion_candidate": completion_candidate,
        "predicate_check": real_success,
        "physics_ticks": tick_counter["n"],
        "subsample_every": subsample_every,
        "demo_video": str(rollout_video) if rollout_video else None,
        "rollout_video": str(rollout_video) if rollout_video else None,
        "preview_video": str(preview_video) if preview_video else None,
        "category": category,
        "episode_identity": identity.to_dict(),
    }
    if media_errors:
        rollout.meta["media_error"] = " | ".join(media_errors)
    trajectory_path = _persist_episode(
        rollout,
        task_name,
        seed,
        success,
        trace,
        episode_meta=meta,
    )
    rollout.meta["trajectory_path"] = trajectory_path
    return RolloutResult(rollout=rollout, success=success, outcome=outcome,
                         trace=trace, messages=convo)


def _persist_episode(
    rollout,
    task_name: str,
    seed: int,
    success: bool,
    trace,
    *,
    episode_meta: dict[str, Any] | None = None,
) -> str | None:
    """Write the episode to the DataStore so it can become training data.

    The loop has always been building `rollout.steps` — an (obs, action, reward,
    done) tuple per physics tick and per tool call, exactly the columns
    `_write_parquet` needs for a LeRobot dataset. Nothing ever called the store,
    so five hundred episodes of zero-shot collection left only mp4s behind, and
    an mp4 cannot train a policy.

    Successes only, and gated by the sim predicate rather than the VLM's claim:
    distilling overclaimed rollouts teaches a policy to stop early, which is the
    exact failure the predicate exists to catch. Set ROBORSI_COLLECT=0 to skip.
    """
    import os
    if os.environ.get("ROBORSI_COLLECT", "1") == "0" or not success:
        return None
    if not rollout.steps:
        return None
    from roborsi.data.store import DataStore
    episode_meta = dict(episode_meta or {})
    extra_meta = {
        "seed": seed,
        "collector": "rollout_vlm",
        "predicate_gated": True,
        "task_key": episode_meta.get("task_key"),
        "run_id": episode_meta.get("run_id"),
        "shard": episode_meta.get("shard"),
        "attempt": episode_meta.get("attempt"),
    }
    written = DataStore().write(
        rollout, skill=task_name, plan_trace=trace,
        extra_meta=extra_meta,
    )
    print(f"[collect] {task_name} seed={seed} -> {written.dir} "
          f"({rollout.length} steps)", flush=True)
    if getattr(written, "parquet_path", None):
        return str(written.parquet_path)
    return str(written.dir)


def adjudicate(
    env: Any,
    *,
    vlm_declared: bool,
    outcome: str,
    use_sim_predicate: bool = True,
) -> tuple[bool, str, bool | None]:
    if not use_sim_predicate:
        return vlm_declared, outcome, None
    real_success = bool(env.check_success())
    if vlm_declared and not real_success:
        return False, "vlm_overclaimed", real_success
    if real_success and not vlm_declared:
        return True, "predicate_passed_without_done", real_success
    return vlm_declared, outcome, real_success


_MEDIA_DIR = {
    "task_success": "success",
    "task_failure": "failure",
    "provider_failure": "infrastructure",
    "transport_failure": "infrastructure",
    "image_failure": "infrastructure",
    "resource_failure": "infrastructure",
    "interrupted": "infrastructure",
}


def _finalize_demo_video(
    workdir: Path,
    *,
    identity: EpisodeIdentity,
    category: str,
    media_root: Path,
) -> Path | None:
    import glob

    import cv2 as _cv2

    frames = sorted(glob.glob(str(workdir / "tick_*.jpg")))
    if not frames:
        return None
    out_dir = Path(media_root) / _MEDIA_DIR.get(category, "infrastructure")
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_key = identity.key.replace("/", "__").replace(":", "__")
    out = out_dir / f"{safe_key}.mp4"
    tmp_out = out.with_name(f".{out.name}.{os.getpid()}.tmp.mp4")
    if tmp_out.exists():
        tmp_out.unlink()
    first = _cv2.imread(frames[0])
    if first is None:
        raise OSError(f"cannot read first frame: {frames[0]}")
    h, w = first.shape[:2]
    writer = None
    try:
        writer = _cv2.VideoWriter(str(tmp_out), _cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (w, h))
        try:
            if not bool(getattr(writer, "isOpened", lambda: True)()):
                raise OSError(f"video writer did not open: {tmp_out}")
            for f in frames:
                img = _cv2.imread(f)
                if img is None:
                    raise OSError(f"cannot read frame: {f}")
                writer.write(img)
        finally:
            if writer is not None:
                writer.release()
        if (not tmp_out.exists()) or tmp_out.stat().st_size <= 0:
            raise OSError(f"encoded video missing/empty: {tmp_out}")
        cap = _cv2.VideoCapture(str(tmp_out))
        try:
            if not bool(getattr(cap, "isOpened", lambda: False)()):
                raise OSError(f"encoded video not decodable: {tmp_out}")
            ok, frame = cap.read()
            if not ok or frame is None:
                raise OSError(f"encoded video has no readable frames: {tmp_out}")
        finally:
            cap.release()
        os.replace(tmp_out, out)
    except BaseException:
        if tmp_out.exists():
            tmp_out.unlink()
        raise
    return out


# ────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ────────────────────────────────────────────────────────────────────────


def _dispatch_with_timeout(state: DispatchContext, call: dict[str, Any],
                            timeout_s: float = 300.0
                            ) -> tuple[dict[str, Any], Observation]:
    """Run _dispatch with a wall-time cap via ThreadPoolExecutor.

    Main thread does future.result(timeout) which is pure Python wait —
    main always responsive regardless of worker state. Worker may leak
    (Python can't kill threads holding GIL in C extensions) but the
    contamination guard upstream sees the TIMEOUT marker and refuses
    subsequent calls in this attempt, letting Engineer cleanly bail
    to next attempt (restore_scene gives a fresh sim).

    Process signals are not reliable here because a C extension may hold the
    GIL. A worker thread keeps the main control path responsive long enough to
    record the timeout and stop issuing commands in the contaminated attempt.

    Also tracks repeated identical timeouts for an even louder warning.
    """
    import concurrent.futures as _cf
    import json as _json

    name = call.get("tool", "?")
    args = call.get("args") or {}
    key = name + "|" + _json.dumps(args, sort_keys=True, default=str)[:300]

    pool = _cf.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_dispatch, state, call)
    try:
        result = future.result(timeout=timeout_s)
        pool.shutdown(wait=False)
        return result
    except _cf.TimeoutError:
        state._timeout_history[key] = state._timeout_history.get(key, 0) + 1
        repeat = state._timeout_history[key]
        repeat_warning = ""
        if repeat > 1:
            repeat_warning = (
                f" This exact call has timed out {repeat} times. "
                f"Stop retrying it and use a different actor, arm, or skill.")
        print(f"[zeroshot] TIMEOUT after {timeout_s:.0f}s on tool={name} "
              f"args={args} repeat={repeat}; worker thread leaked, "
              f"contamination guard will refuse subsequent calls. "
              f"Returning ok=False to Engineer.", flush=True)
        # CRITICAL: do NOT snapshot here. Leaked worker is still holding
        # sim; take_snapshot would deadlock waiting for sim access.
        # Return dummy obs; contamination guard upstream marks state
        # dirty so subsequent calls fail fast until next attempt's
        # restore_scene.
        return ({"ok": False, "success": False,
                 "reason": (f"TIMEOUT: '{name}' exceeded "
                              f"{timeout_s:.0f}s wall-time. cuRobo IK stuck on "
                              f"infeasible pose. Worker thread cannot be killed "
                              f"(Python C-ext limit); sim CONTAMINATED for this "
                              f"attempt.{repeat_warning}\n"
                              f"Next: call done(success=False) now; "
                              f"subsequent calls in this attempt refuse anyway. "
                              f"Next attempt restore_scene gives clean sim. "
                              f"Try DIFFERENT arm / skill on retry.")},
                Observation())


def _dispatch(
    state: DispatchContext,
    call: dict[str, Any],
    *,
    internal: bool = False,
) -> tuple[dict[str, Any], Observation]:
    """Route tool name → handler. Three paths, in order:

    1. Meta tools: read_skill_code / list_base_skills / propose_new_skill /
       propose_skill_update — codebase introspection + skill self-evolution.
       No env interaction.
    2. Backend tools: a `_do_<name>(state, args)` handler the backend supplies
       via env.tool_handlers() (memoized on state._tool_handlers).
    3. Plugin: `skills/base/<backend>/<name>/policy.py` exporting
       `dispatch_runtime(state, args) -> (result_dict, Observation)`.

    Plugin path means Tier 2 can author a new base skill end-to-end (SKILL.md +
    policy.py) without touching this file.
    """
    name = call.get("tool")
    args = call.get("args") or {}
    internal_bypass = bool(internal and state.ns != "libero")
    if (
        not internal_bypass
        and state._allowed_tools is not None
        and name not in state._allowed_tools
    ):
        return (
            {"ok": False, "reason": f"tool '{name}' is not allowed"},
            state.env.take_snapshot(),
        )
    meta_result = _dispatch_meta_tool(
        name,
        args,
        ns=state.ns,
        task=state.task,
    )
    if meta_result is not None:
        return (meta_result, state.env.take_snapshot())
    if state._tool_handlers is None:
        state._tool_handlers = state.env.tool_handlers()
    handler = state._tool_handlers.get(name)
    if handler is None:
        handler = _try_load_plugin_dispatcher(name, state.ns)
    # Opt-in atomic-scoped compound (atomic/<task>/<name>/policy.py), resolved
    # only after base tools miss and only for the running task.
    if handler is None and state.task and \
            os.environ.get("ROBORSI_ATOMIC_COMPOUND") == "1" and \
            os.environ.get("ROBORSI_SELFEVO_FREEZE", "0") == "0":
        handler = _try_load_compound_dispatcher(name, state.task)
    if handler is None:
        return ({"ok": False, "reason": f"unknown tool '{name}'"}, state.env.take_snapshot())
    return handler(state, args)


def _dispatch_tool(state: "DispatchContext", tool_name: str,
                    args: dict[str, Any] | None = None
                    ) -> tuple[dict[str, Any], Observation]:
    """Thin wrapper: call another base tool by name from inside a
    dispatch_runtime. Useful for composing base skills (e.g.
    `press_button_at_xyz` composes `move_fingertip_to`, `gripper`,
    `check_task_success`)."""
    return _dispatch(
        state,
        {"tool": tool_name, "args": args or {}},
        internal=True,
    )


# ────────────────────────────────────────────────────────────────────────
# Backend-agnostic snapshot / success helpers (thin wrappers over the Env
# seam, kept as module functions for the many `_do_*` handlers that call
# `_snapshot(state.env)` directly).
# ────────────────────────────────────────────────────────────────────────


def _snapshot(env: Any) -> Observation:
    """Fresh observation via the backend's seam method."""
    return env.take_snapshot()


def _check_success(env: Any) -> bool | None:
    """Ground-truth task predicate via the backend's seam method."""
    return env.check_success()
