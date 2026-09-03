"""Universal VLM tool-loop driver — backend-agnostic ``run_rollout``.

Drives one episode against ANY ``Env`` (RoboTwin sim, RoboCasa, future real
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
from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
from roborsi.runtime_mode import current_mode, is_eval_mode


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
            return f"[{body}{', …' if len(x) > 4 else ''}]"
        s = str(x)
        return s if len(s) <= 40 else s[:37] + "…"
    return ", ".join(f"{k}={_v(v)}" for k, v in list(args.items())[:6])


@dataclass
class RolloutResult:
    rollout: Rollout
    success: bool
    outcome: str
    trace: list[dict[str, Any]]
    # Final conversation list. Caller may pass this back as `prior_messages`
    # to a subsequent run_rollout call to chain a multi-atomic LH
    # session under one sustained LLM context.
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
    ns: str = "robotwin"
    # Running atomic task name — scopes opt-in solidified compounds
    # (atomic/<task>/<name>/policy.py) to their own task at dispatch time.
    task: str = ""
    _sim_contaminated: bool = False
    _timeout_history: dict[str, int] = field(default_factory=dict)
    _tool_handlers: dict[str, Callable] | None = None
    _attached_image_path: Path | None = None
    _perception_cache: dict[str, dict[str, Any]] = field(default_factory=dict)


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
) -> RolloutResult:
    """Success adjudication is the SIM predicate by DEFAULT (use_sim_predicate=True):
    the final verdict is env.check_success(), computed AFTER the VLM loop ends —
    the Engineer NEVER sees it during the episode and cannot self-report success.
    If the VLM declared done(success=True) but the sim predicate disagrees →
    vlm_overclaimed (success=False); if the predicate passes without a done →
    predicate_passed_without_done (success=True).

    This is correct for STANDALONE atomics that match a task one-to-one, but
    WRONG for atomic sub-episodes of a long-horizon task (the sim's
    check_success encodes the FULL task predicate, never True mid-handover
    — caused every pick_bowl/pick_block to be falsely flagged
    vlm_overclaimed for the first half of 2026-06-04). LH sub-atomic callers
    (lh_executor) MUST pass use_sim_predicate=False and let the per-atomic
    progress_judge adjudicate instead.

    `restrict_to_names`: optional set of base skill names. When provided,
    only those skills appear in the inner VLM's tool list for the entire
    episode. Engineer (agents/engineer.py) uses this with the SkillSelector
    top-K when total skill count exceeds SKILL_LIST_SOFT_CAP.

    `prior_messages`: optional. When None (atomic case), the loop builds
    a fresh [system, user(instruction)] convo. When provided (LH multi-
    atomic case), the loop APPENDS one new user turn carrying the new
    instruction to the existing convo and continues — sustaining the
    LLM's context across atomics + retries. The final messages list is
    returned in RolloutResult.messages for the caller to feed into the
    next call."""
    workdir = (workdir or Path("/tmp/roborsi-zeroshot")) / f"{task_name}-{seed}"
    workdir.mkdir(parents=True, exist_ok=True)
    rollout = Rollout(task=task_name, seed=seed)
    trace: list[dict[str, Any]] = []
    # Resolve the active backend's skill namespace ONCE — drives which
    # base/<ns>/ skills are discovered as tools and dispatched. Default
    # "robotwin" keeps every RoboTwin/BiCoord run byte-identical.
    ns = _skill_namespace(getattr(env, "backend_name", None))
    state = DispatchContext(env=env, workdir=workdir, last_image_path=None,
                            ns=ns, task=task_name)
    # Memoize the backend's tool registry once per episode (was the old
    # module-global _ensure_registry cache). Env-agnostic: each backend
    # supplies its own _do_* map via tool_handlers().
    state._tool_handlers = env.tool_handlers()

    # Initial frame.
    obs = env.take_snapshot()
    rollout.steps.append(Step(obs=obs, action=obs.state, info={"phase": "reset"}))

    # Hook the backend's physics loop so each tick during tool execution lands a
    # (state, action=next-state qpos) tuple. Same data shape as expert_replay
    # so downstream lerobot_build can ingest both. Backends without a steppable
    # physics loop (real robot, robosuite) inherit a no-op hook and capture
    # nothing mid-tool.
    # Per-tick capture cost = a FULL env.take_snapshot() (RoboTwin get_obs
    # renders every camera + depth). At subsample 5 a long trajectory (the
    # lift_pot pot-handle grasp cuRobo-plans hundreds of scene.step()s) did
    # hundreds of full renders → impl.move 60-83s, and pick_actor's several
    # moves blew the 300s per-tool WALL-CAP → false "cuRobo hang" (root-caused
    # 2026-07-03 via py-spy: cuRobo plan was 0.1s, the time was all in per-tick
    # rendering). 20 (4x sparser) keeps demo playback smooth; MAX_CAPTURES caps
    # total renders so an unusually long trajectory can never O(N)-blow the
    # tool budget (past the cap we still step physics, just skip the render).
    subsample_every = 20
    MAX_CAPTURES = 400
    tick_counter = {"n": 0, "captured": 0}

    def _on_tick() -> None:
        tick_counter["n"] += 1
        if tick_counter["n"] % subsample_every != 0:
            return
        if tick_counter["captured"] >= MAX_CAPTURES:
            return
        tick_counter["captured"] += 1
        sim_obs = env.take_snapshot()
        rollout.steps.append(Step(
            obs=sim_obs, action=sim_obs.state,
            info={"tick": tick_counter["n"], "source": "scene_step"},
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
        # LH path: continue an existing sustained conversation by adding
        # a new user turn carrying THIS atomic's instruction. Don't
        # rebuild system prompt — keep the original (and its tool list).
        convo = list(prior_messages)
        convo.append({"role": "user", "content": (
            f"Next sub-task in this long-horizon session.\n"
            f"Task: {instruction}\n"
            f"Success criterion: {expected_on_success}\n\n"
            f"Issue exactly one tool call, in JSON, no prose."
        )})
    else:
        # Fresh episode: two-stage skill selection. A Sonnet SkillSelector
        # shortlists the relevant skills first (when restrict isn't already
        # pinned) so the Engineer sees a focused, fully-described tool set.
        if restrict_to_names is None:
            restrict_to_names = _maybe_shortlist_skills(
                instruction, task_name, seed, ns=ns)
        convo = _initial_messages(instruction, expected_on_success,
                                     task_name=task_name,
                                     restrict_to_names=restrict_to_names,
                                     ns=ns)
    tools = _build_tool_specs(ns=ns, task=task_name)
    success = False
    outcome = "budget_exceeded"
    # Clear the module-global active plan so is_revision is per-episode correct
    # (a single-process runner would otherwise leak the prior episode's plan).
    from roborsi.embodied.skills.base.plan.robotwin.policy import (
        _reset_active_plan,
    )
    _reset_active_plan()
    REFLECT_EVERY = 8  # inject reflection prompt every N steps without done
    # Trace summarization threshold: when convo exceeds this many turns,
    # compact older trace into a synthetic "previously tried X failed Y"
    # message so Engineer stays within ~20k tokens. Per 2026-06-15 user
    # request "trace 累计加一下达到一定 token 就总结".
    SUMMARIZE_AT_MSGS = 30
    import time as _t
    for step_idx in range(tool_budget):
        _t0 = _t.time()
        # Summarize old trace if conversation got too long.
        if len(convo) > SUMMARIZE_AT_MSGS:
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
        if step_idx > 0 and step_idx % REFLECT_EVERY == 0:
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
        print(f"[zeroshot] step={step_idx} got response in {_t.time()-_t0:.1f}s "
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
                msg = _call_vlm_tools(model or DEFAULT_MODEL, convo, tools)
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

        # Dispatch each tool call (Anthropic / litellm may emit multiple in one
        # turn; we run them in order).
        any_done = False
        # Capture VLM reasoning text emitted before this turn's tool calls.
        # For Anthropic via litellm, msg.content is a string; for OpenAI it's
        # also a string. Either way we strip and truncate for trace+overlay.
        reasoning_raw = getattr(msg, "content", None)
        if isinstance(reasoning_raw, list):
            reasoning_raw = " ".join(
                getattr(b, "text", "") if hasattr(b, "text") else str(b.get("text", "") if isinstance(b, dict) else "")
                for b in reasoning_raw
            )
        reasoning_text = (str(reasoning_raw or "")).strip()
        # Surface the agent's reasoning to the CLI. It was captured for the trace
        # but never printed, so a run showed only "calling..." / "dispatching
        # tool=X" and the user watched a silent spinner with no view of WHY the
        # agent chose each action. Print it (trimmed) so the thinking is visible.
        if reasoning_text:
            print(f"[zeroshot] step={step_idx} 💭 "
                  f"{reasoning_text[:600]}", flush=True)
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            print(f"[zeroshot] step={step_idx} → {name}({_fmt_args(args)})",
                  flush=True)
            _t_dispatch = _t.time()
            trace.append({"step": step_idx, "tool_call": {"tool": name, "args": args},
                          "tool_call_id": tc.id,
                          "reasoning": reasoning_text[:600]})
            try:
                from roborsi.channels.agent.feishu import live_trace as _lt
                _lt.emit_inner("inner_tool_call", step=step_idx, tool=name,
                                args=args, reasoning=reasoning_text[:600])
            except Exception:
                pass
            if name == "done":
                success = bool(args.get("success", False))
                outcome = "vlm_declared_done"
                trace[-1]["result"] = {"acknowledged": True}
                trace[-1]["wallclock_s"] = 0.0
                trace[-1]["timing_phase"] = "other"
                convo.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                              "content": json.dumps({"acknowledged": True}, ensure_ascii=False)})
                any_done = True
                break
            # exec_python lets Engineer write arbitrary sim loops; without
            # a tight cap it can run 10+ min and bypass tool budget
            # (V43/V44 hang root cause). Hard-cap at 60s to force Engineer
            # back to atomic tools.
            # cuRobo-heavy skills now run on SIGALRM-protected dispatch
            # (V52 #1) so cap can be aggressive — 90s is plenty for any
            # real plan_path; longer means the IK is infeasible.
            # Once exec_python (or anything) times out, the worker thread
            # cannot be killed (Python GIL limit) — it's still holding
            # sim. Mark the state contaminated so subsequent dispatches
            # fail fast and Engineer surrenders the attempt; next attempt
            # gets a clean restore_scene.
            if state._sim_contaminated:
                result = ({"ok": False, "success": False,
                            "reason": ("⚠ This attempt's sim state is "
                                          "CONTAMINATED — a prior exec_python "
                                          "/ long tool call timed out and its "
                                          "worker thread is still holding the "
                                          "sim. All subsequent tool calls in "
                                          "this attempt will return ok=False. "
                                          "Call done(success=False) now — the "
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
            dispatch_wallclock_s = _t.time() - _t_dispatch
            print(f"[zeroshot] step={step_idx} tool={name} "
                  f"dispatched in {dispatch_wallclock_s:.1f}s ok={result.get('ok')}", flush=True)
            trace[-1]["result"] = result
            trace[-1]["tick_end"] = tick_counter["n"]
            trace[-1]["wallclock_s"] = round(dispatch_wallclock_s, 6)
            trace[-1]["timing_phase"] = _tool_timing_phase(name)
            try:
                from roborsi.channels.agent.feishu import live_trace as _lt
                ok = result.get("ok") if isinstance(result, dict) else None
                _lt.emit_inner("inner_tool_result", step=step_idx, tool=name,
                                ok=ok, preview=json.dumps(result, ensure_ascii=False)[:400])
            except Exception:
                pass
            rollout.steps.append(Step(
                obs=after_obs, action=after_obs.state,
                info={"step": step_idx, "tool": name, "source": "tool_boundary"},
            ))
            convo.append({"role": "tool", "tool_call_id": tc.id, "name": name,
                          "content": json.dumps(result, ensure_ascii=False)})
        if any_done:
            break

        # CaP-X-inspired forced reflection after each turn: don't let VLM
        # drift between turns without explicitly classifying what to do
        # next. Replaces "VLM freelance" with a 3-way structured decision.
        # Skip when the turn was pure-observation (look/find_pixel/etc.) —
        # only inject after ACTION-class tools, where state actually changed.
        ACTION_TOOLS = {
            "move_to_pose", "move_fingertip_to", "move_to_pixel",
            "gripper", "set_gripper", "home",
            "grasp_then_lift", "grasp_then_lift_graspgen", "grasp_object",
            "pick_actor_by_contact_point", "place_object_in",
            "place_on_surface",
            "tap_held_on_target", "execute_with_pi05",
        }
        had_action = any(tc.function.name in ACTION_TOOLS for tc in tool_calls)
        if had_action and step_idx < tool_budget - 1:
            convo.append({"role": "user", "content": _build_status_check_prompt()})
    unhook()

    # Persist trace for offline debugging — long_horizon executor doesn't save it.
    try:
        (workdir / "trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as exc:
        (workdir / "trace_error.txt").write_text(f"{type(exc).__name__}: {exc}")

    vlm_declared = success
    real_success = env.check_success() if use_sim_predicate else None
    if use_sim_predicate:
        if vlm_declared and not real_success:
            outcome = "vlm_overclaimed"
            success = False
        if real_success and not vlm_declared:
            outcome = "predicate_passed_without_done"
            success = True

    # NOTE: zeroshot trace persistence is now ONLY done by the LH triangle
    # (LHExecutor) AFTER atomic_judge runs and returns success. We used to
    # persist here on vlm_declared=True, but VLM frequently overclaims (declares
    # done(success=true) even when the grasp missed), polluting the RAG corpus
    # with false positives. The judge-gated persistence in LHExecutor is the
    # source of truth.
    rollout.success = success
    rollout.outcome = outcome
    # Evolve mode keeps only simulator-confirmed success demos. Frozen eval
    # preserves both verdict classes and their source frames as evidence.
    _demo_video = _finalize_demo_video(workdir, task_name, seed, success)
    rollout.meta = {
        "backend": env.backend_name,
        "collector": "rollout_vlm",
        "run_mode": current_mode().value,
        "model": model or DEFAULT_MODEL,
        "tool_calls": len(trace),
        "vlm_declared": vlm_declared,
        "predicate_check": real_success,
        "physics_ticks": tick_counter["n"],
        "subsample_every": subsample_every,
        "demo_video": str(_demo_video) if _demo_video else None,
    }
    return RolloutResult(rollout=rollout, success=success, outcome=outcome,
                         trace=trace, messages=convo)


def _finalize_demo_video(workdir: Path, task_name: str, seed: int,
                         success: bool):
    """Assemble per-tick head-camera frames into an evaluation/demo video.

    Evolve mode retains only simulator-confirmed success videos. Frozen eval
    retains success and failure videos plus the original frames so each verdict
    remains inspectable. Never raises into the caller.
    """
    import glob
    import time as _time
    frames = sorted(glob.glob(str(workdir / "tick_*.jpg")))
    frozen_eval = is_eval_mode()
    if not frames:
        return None
    if not success and not frozen_eval:
        for f in frames:
            try:
                os.remove(f)
            except OSError:
                pass
        return None
    # This file lives at roborsi/embodied/agent_loop/rollout.py, so the repo
    # root is parents[3] (agent_loop → embodied → roborsi → repo).
    artifact_group = "evals" if frozen_eval else "demos/auto"
    demos_dir = Path(__file__).resolve().parents[3] / "artifacts" / artifact_group
    demos_dir.mkdir(parents=True, exist_ok=True)
    verdict = "success" if success else "failure"
    out = demos_dir / (
        f"{task_name}-seed{seed}-{verdict}-{_time.strftime('%Y%m%d-%H%M%S')}.mp4"
    )

    if not _encode_h264(frames, out):
        _encode_mpeg4(frames, out)

    if not frozen_eval:
        for f in frames:        # transient per-tick jpgs — keep only the mp4
            try:
                os.remove(f)
            except OSError:
                pass
    return out if out.is_file() else None


def _ffmpeg_bin() -> str | None:
    """Locate an ffmpeg binary, PATH or not.

    The GPU box that produces these demos has no system ffmpeg, but several
    Python envs there ship `imageio_ffmpeg`, which bundles a static build.
    Relying on PATH alone would silently drop every run back to the cv2 encoder
    on exactly the machine whose output matters most.
    """
    import shutil as _shutil
    exe = _shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _encode_h264(frames: list[str], out: Path) -> bool:
    """Encode the frames to H.264 with ffmpeg. False if it is unavailable.

    cv2.VideoWriter cannot produce H.264 here: this OpenCV build offers only the
    `h264_v4l2m2m` hardware encoder, which has no device on a headless box, so
    every H.264 fourcc fails to open and the writer silently falls back to
    `mp4v` — MPEG-4 Part 2. That plays in VLC and shows as a black rectangle in
    every browser, which reads as a broken player rather than a codec the page
    cannot decode. Demos exist to be watched, so encode what browsers decode.

    `+faststart` moves the index to the front so a clip streams rather than
    having to download fully before the first frame appears.
    """
    import subprocess as _sp
    import tempfile as _tf

    exe = _ffmpeg_bin()
    if exe is None:
        return False
    # A concat list avoids assuming the jpgs are contiguously numbered — the
    # tick counter skips whenever the capture cap is hit.
    with _tf.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        for f in frames:
            fh.write(f"file '{f}'\nduration 0.1\n")
        fh.write(f"file '{frames[-1]}'\n")
        listing = fh.name
    try:
        r = _sp.run([exe, "-y", "-loglevel", "error", "-f", "concat",
                     "-safe", "0", "-i", listing, "-c:v", "libx264",
                     "-pix_fmt", "yuv420p", "-crf", "24", "-r", "10",
                     "-movflags", "+faststart", str(out)],
                    capture_output=True, text=True, timeout=600)
    finally:
        os.remove(listing)
    if r.returncode != 0:
        print(f"[demo] ffmpeg failed, falling back to mpeg4: "
              f"{r.stderr.strip()[:200]}", flush=True)
        return False
    return out.is_file()


def _encode_mpeg4(frames: list[str], out: Path) -> None:
    """Last-resort cv2 encode. Produces a file no browser can play."""
    import cv2 as _cv2
    first = _cv2.imread(frames[0])
    if first is None:
        return
    h, w = first.shape[:2]
    writer = _cv2.VideoWriter(str(out), _cv2.VideoWriter_fourcc(*"mp4v"),
                              10.0, (w, h))
    for f in frames:
        img = _cv2.imread(f)
        if img is not None:
            writer.write(img)
    writer.release()


# ────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ────────────────────────────────────────────────────────────────────────


def _dispatch_with_timeout(state: DispatchContext, call: dict[str, Any],
                            timeout_s: float = 300.0
                            ) -> tuple[dict[str, Any], Observation]:
    """Run _dispatch with a wall-time cap via ThreadPoolExecutor.

    MuJoCo EGL contexts are thread-affine. LIBERO tools therefore execute on
    the environment owner thread; their servo loops are already iteration
    bounded. Other backends retain the worker-thread timeout path below.

    Main thread does future.result(timeout) which is pure Python wait —
    main always responsive regardless of worker state. Worker may leak
    (Python can't kill threads holding GIL in C extensions) but the
    contamination guard upstream sees the TIMEOUT marker and refuses
    subsequent calls in this attempt, letting Engineer cleanly bail
    to next attempt (restore_scene gives a fresh sim).

    Per 2026-06-15: tried SIGALRM as an "improvement" (V52-V56). It
    was WORSE — SIGALRM can't be delivered while a C extension holds
    GIL, so cuRobo hangs in main thread became 30+ min unrecoverable
    freezes. ThreadPoolExecutor is the industry standard for
    Python+C-extension timeouts. Reverted.

    Also tracks repeated identical timeouts for an even louder warning.
    """
    import concurrent.futures as _cf
    import contextvars as _contextvars
    import json as _json

    name = call.get("tool", "?")
    args = call.get("args") or {}
    key = name + "|" + _json.dumps(args, sort_keys=True, default=str)[:300]

    backend_name = str(
        getattr(getattr(state, "env", None), "backend_name", "")
    )
    if backend_name.startswith("libero"):
        return _dispatch(state, call)

    pool = _cf.ThreadPoolExecutor(max_workers=1)
    dispatch_context = _contextvars.copy_context()
    future = pool.submit(dispatch_context.run, _dispatch, state, call)
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
                f" ‼ YOU HAVE NOW TIMED OUT ON THIS EXACT CALL "
                f"{repeat} TIMES. STOP retrying it. The args are "
                f"geometrically infeasible — DIFFERENT actor / arm / "
                f"skill needed.")
        print(f"[zeroshot] TIMEOUT after {timeout_s:.0f}s on tool={name} "
              f"args={args} repeat={repeat} — worker thread leaked, "
              f"contamination guard will refuse subsequent calls. "
              f"Returning ok=False to Engineer.", flush=True)
        # CRITICAL: do NOT snapshot here. Leaked worker is still holding
        # sim; take_snapshot would deadlock waiting for sim access.
        # Return dummy obs; contamination guard upstream marks state
        # dirty so subsequent calls fail fast until next attempt's
        # restore_scene.
        return ({"ok": False, "success": False,
                 "reason": (f"⚠ TIMEOUT — \'{name}\' exceeded "
                              f"{timeout_s:.0f}s wall-time. cuRobo IK stuck on "
                              f"infeasible pose. Worker thread cannot be killed "
                              f"(Python C-ext limit); sim CONTAMINATED for this "
                              f"attempt.{repeat_warning}\n"
                              f"NEXT: call done(success=False) NOW — "
                              f"subsequent calls in this attempt refuse anyway. "
                              f"Next attempt restore_scene gives clean sim. "
                              f"Try DIFFERENT arm / skill on retry.")},
                Observation())


def _dispatch(state: DispatchContext, call: dict[str, Any]) -> tuple[dict[str, Any], Observation]:
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
    meta_result = _dispatch_meta_tool(name, args, ns=state.ns)
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
            os.environ.get("ROBORSI_ATOMIC_COMPOUND") == "1":
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
    a simulator verdict during the episode)."""
    return _dispatch(state, {"tool": tool_name, "args": args or {}})


def _tool_timing_phase(tool_name: str) -> str:
    recovery = {
        "home",
        "park_arm",
        "reset_failure",
        "reset_success",
    }
    action = {
        "descend_tcp_to_z",
        "execute_previewed_move",
        "execute_with_pi05",
        "grasp_diverse",
        "grasp_flat",
        "grasp_object",
        "grasp_obb",
        "grasp_rim",
        "grasp_top_down",
        "gripper",
        "move_dual_arm",
        "move_ee_delta",
        "move_fingertip_to",
        "move_to_pixel",
        "move_to_pose",
        "place_beside",
        "place_held_at_target_servo",
        "place_held_in_held_container",
        "place_object_in",
        "place_on_surface",
        "place_obb",
        "set_gripper",
        "tip_pour",
    }
    if tool_name in recovery:
        return "recovery"
    if tool_name in action:
        return "action"
    if tool_name == "done":
        return "other"
    return "perception"


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
