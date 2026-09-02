"""roborsi.embodied.sim.robotwin.codeact_runtime — Rollout-style code-as-action.

Instead of VLM emitting one tool_use block per turn (our default), here VLM
writes a Python script that calls base skills as top-level functions. We exec
the script in a sandbox where each base skill is a Python function with the
same signature as its SKILL.md args.

If the atomic_judge later marks the run a success, the script is moved from
/tmp into `skills/atomic/<atomic>/zeroshot/programs/<run_id>.py` as a
permanent policy reference. Failed scripts are deleted.

This is closer to Rollout's plan-react-replan loop:
  - VLM writes code
  - Code runs (multiple tool calls, conditionals, retries inside)
  - VLM sees stdout + final image
  - Either done or rewrite + retry
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop.env import Observation, Rollout, Step


SYSTEM_PROMPT_CODEACT_HEADER = """\
You are an embodied robot agent driving a dual-arm tabletop sim. You write
Python scripts that call base-skill functions to accomplish a goal. The
sandbox executes your script and shows you the stdout + the final
head-camera frame.

MANDATORY GRASP PROTOCOL (read this FIRST, applies to every pick):
  get_grasp_pose RETURNS a dict — it does NOT move the arm. You MUST execute
  the returned pose via move_to_pose. Complete recipe for picking ANY object:

    look(camera='head_camera')
    p   = find_pixel(object='red cube', location='center of red cube')
    xyz = unproject_pixel(camera='head_camera', u=p['u'], v=p['v'])['xyz']
    r   = get_grasp_pose(object='red cube', z_min=xyz[2]-0.005,
                         z_max=xyz[2]+0.04, half_window_px=30)
    pose = r['grasp_pose']  # [x, y, z, qx, qy, qz, qw]
    gripper(arm='left', action='open')
    move_to_pose(arm='left', x=pose[0], y=pose[1], z=pose[2]+0.10, quat=pose[3:])  # hover
    move_to_pose(arm='left', x=pose[0], y=pose[1], z=pose[2],     quat=pose[3:])  # descend
    gripper(arm='left', action='close')
    move_to_pose(arm='left', x=pose[0], y=pose[1], z=pose[2]+0.20, quat=pose[3:])  # lift
    v = verify_holding_visual(arm='left', object='red cube')
    if v['holding_visual']:
        done(success=True, reason='Left gripper visibly holds the red cube')

  Calling get_grasp_pose then done() WITHOUT the move_to_pose / gripper
  steps is a HARD FAILURE — the arm never moved. Skip this protocol ONLY
  for tap/press/release actions (no grasp needed).

REFLECTION DISCIPLINE (do this every turn before writing the next script):
  1. Describe what you SEE in the latest image (gripper position, object
     location, what changed since last turn).
  2. Compare to your INTENT for the previous script — did it work? What
     went wrong?
  3. Pick the next strategy based on the diagnosis, NOT habit. If the same
     approach failed twice, switch tactics (different orientation, tool,
     or z-offset).
  4. Then write the next script.

WORKFLOW per turn:
  - You receive: GOAL, scene image, and (optionally) past stdout/error.
  - You emit: a single ```python``` code block.
  - The script runs to completion in a persistent Python namespace —
    variables you assign (e.g. `bx, by, bz = unproject_pixel(...)["xyz"]`)
    SURVIVE into the next turn's script. The env state (gripper, held
    objects, scene) also persists.
  - You see: stdout (your prints), exception (if any), the latest image.
  - You decide: call done(success=True/False), or write a new script.

KEY RULES:
  - Use Python freely: variables, conditionals, loops, list comprehensions.
  - print() any debug info you want to see; it shows in next turn's stdout.
  - When the goal is met, end the script with done(success=True, reason="...").
  - Tool-budget per turn: keep scripts to <=30 base-skill calls.

CRITICAL — DO NOT GIVE UP EARLY:
  - You have MULTIPLE turns. After a failed attempt, the next turn lets
    you see the new image + write a NEW script with a different approach.
  - DO NOT call done(success=False) after one failed attempt.
  - On grasp failure: READ THE TOOL CATALOG BELOW. Each tool's `when_to_use`
    section tells you what failure modes to expect and which tool to switch
    to. Don't blindly retry the same tool — switch tactics based on the doc.

  done(success: bool, reason: str = "") -> ENDS the episode (sentinel).

  author_base_skill(name, description, code, when_to_use="", args_schema=None) ->
    {ok, skill_path, ...}
    SELF-AUTHOR a new base skill when an existing tool doesn't fit. `code`
    is a Python function body (NO `def` line) that operates on `state`
    (with state.env, state.workdir) and returns a dict. After this call
    the skill is on disk under skills/base/robotwin/<name>/ and available
    by name in this episode AND all future episodes. Use sparingly — only
    when you need a capability the existing 23 tools cannot compose.

AVAILABLE TOOLS (each is a Python function; signatures + `when_to_use` from SKILL.md):

"""


def _build_codeact_tool_catalog() -> str:
    """Pull SKILL.md frontmatter for every wired base/robotwin tool and emit
    a catalog string with signature + description + when_to_use. The VLM
    reads this to make tactical decisions itself, instead of us hardcoding
    'try X first, fall back to Y' rules in atomic prompts.
    """
    from roborsi.embodied.skills import discover
    from roborsi.embodied.sim.robotwin.robotwin_agent import _ensure_registry
    from roborsi.embodied.agent_loop.prompt_tools import _try_load_plugin_dispatcher
    legacy = set(_ensure_registry().keys())
    rows: list[tuple[str, str]] = []
    for sk in discover():
        parts = sk.path.parent.parts
        if "base" not in parts or "robotwin" not in parts:
            continue
        if sk.name not in legacy and _try_load_plugin_dispatcher(sk.name) is None:
            continue
        fm = sk.frontmatter or {}
        desc = (fm.get("description") or "").strip()
        when = fm.get("when_to_use") or ""
        if isinstance(when, str):
            when = when.strip()
        args = fm.get("args") or {}
        arg_names = ", ".join(args.keys()) if isinstance(args, dict) else ""
        sig = f"  {sk.name}({arg_names})"
        block = sig + "\n    DESCRIPTION: " + desc.replace("\n", " ")
        if when:
            indented = "\n      ".join(when.splitlines())
            block += "\n    WHEN TO USE:\n      " + indented
        rows.append((sk.name, block))
    rows.sort(key=lambda kv: kv[0])
    return "\n\n".join(b for _, b in rows)


def _system_prompt_codeact() -> str:
    return (
        SYSTEM_PROMPT_CODEACT_HEADER
        + _build_codeact_tool_catalog()
        + "\n\nGoal-specific instructions follow.\n"
    )


# Eagerly build once at import; downstream code reads SYSTEM_PROMPT_CODEACT.
SYSTEM_PROMPT_CODEACT = _system_prompt_codeact()


def _do_author_base_skill(
    name: str, description: str, code: str, when_to_use: str,
    args_schema: dict[str, Any] | None, tool_ns: dict[str, Any],
    persistent_ns: dict[str, Any],
) -> dict[str, Any]:
    """Persist a new base skill (SKILL.md + policy.py) and add it to the
    running episode's namespace. Called by the codeact `author_base_skill`
    meta-tool the VLM can invoke when an existing tool is missing.

    The user-written `code` should define a Python function `<name>(state, **kwargs)`
    that returns a result dict. We wrap it as `dispatch_runtime(state, args)`
    in the persisted policy.py.
    """
    safe_name = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if not safe_name or not safe_name[0].isalpha():
        return {"ok": False, "reason": f"invalid name {name!r}"}
    repo_root = Path(__file__).resolve().parents[3]
    skill_dir = repo_root / "embodied" / "skills" / "base" / safe_name / "robotwin"
    skill_dir.mkdir(parents=True, exist_ok=True)

    args_block = ""
    if args_schema and isinstance(args_schema, dict):
        lines = ["args:"]
        for k, v in args_schema.items():
            t = (v.get("type") if isinstance(v, dict) else "string") or "string"
            d = (v.get("description") if isinstance(v, dict) else "") or ""
            lines.append(f"  {k}: {{ type: {t}, description: \"{d}\" }}")
        args_block = "\n".join(lines)
    skill_md = (
        f"---\nname: {safe_name}\nkind: base\nrobot: robotwin\nversion: 0.1.0\n"
        f"description: {description.strip()}\n"
        + (args_block + "\n" if args_block else "")
        + (f"when_to_use: |\n  {when_to_use.strip().replace(chr(10), chr(10) + '  ')}\n"
           if when_to_use else "")
        + "---\n\n"
        f"# {safe_name} · RoboTwin (VLM-authored)\n\n"
        "Authored by the codeact VLM during a live episode via author_base_skill.\n"
    )
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    policy_py = (
        '"""base.' + safe_name + '.robotwin — VLM-authored base skill (plugin path)."""\n\n'
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n\n"
        "def dispatch_runtime(state: Any, args: dict[str, Any]):\n"
        "    from roborsi.embodied.agent_loop.rollout import _snapshot\n"
        "    return _impl(state, args), _snapshot(state.env)\n\n\n"
        "def _impl(state: Any, args: dict[str, Any]) -> dict[str, Any]:\n"
        + "    " + code.strip().replace("\n", "\n    ") + "\n\n\n"
        "def run(env, **kwargs):\n"
        "    raise RuntimeError('VLM-authored skill; invoke via codeact tool dispatch.')\n"
    )
    (skill_dir / "policy.py").write_text(policy_py, encoding="utf-8")

    # Make available to the running episode immediately. We exec the user
    # code so they can call <name>(state, **kwargs) inside their next script
    # via tool_ns.
    try:
        local_ns: dict[str, Any] = {}
        exec(compile(code, f"<author_base_skill:{safe_name}>", "exec"), local_ns)
        fn = local_ns.get("_impl") or local_ns.get(safe_name)
        if fn is None:
            return {"ok": False, "reason": f"code did not define _impl or {safe_name}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"compile failed: {type(exc).__name__}: {exc}"}

    # Wrap so callers don't need to pass `state`.
    from roborsi.embodied.agent_loop.prompt_tools import _PLUGIN_CACHE
    _PLUGIN_CACHE.pop(safe_name, None)  # invalidate cache so next call reloads from disk

    return {
        "ok": True,
        "name": safe_name,
        "skill_path": str(skill_dir / "SKILL.md"),
        "policy_path": str(skill_dir / "policy.py"),
        "note": "Skill is now in the catalog. Call it via _impl(state, **kwargs) "
                "this turn, or refer to it by name in the next turn (it loads "
                "via the plugin dispatch path).",
    }


@dataclass
class CodeactResult:
    rollout: Rollout
    success: bool
    outcome: str
    trace: list[dict[str, Any]]
    saved_program_path: str | None


def run_codeact_episode(
    env: Any,
    *,
    seed: int,
    task_name: str,
    instruction: str,
    expected_on_success: str,
    model: str | None = None,
    max_turns: int = 6,
    max_calls_per_script: int = 30,
    workdir: Path | None = None,
) -> CodeactResult:
    """Drive one episode in code-as-action mode.

    Returns a CodeactResult with the rollout + the saved-program path (if
    atomic_judge later confirms success — the caller is responsible for moving
    it to its permanent home).
    """
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.messages import _convert_messages_to_anthropic
    from roborsi.embodied.agent_loop.vlm_io import _anthropic_call_with_tools
    from roborsi.embodied.agent_loop.rollout import DispatchContext as _State, _snapshot, _dispatch
    from roborsi.embodied.sim.robotwin.robotwin_agent import _write_jpg
    workdir = (workdir or Path("/tmp/roborsi-codeact")) / f"{task_name}-{seed}"
    workdir.mkdir(parents=True, exist_ok=True)
    rollout = Rollout(task=task_name, seed=seed)
    trace: list[dict[str, Any]] = []
    state = _State(env=env, workdir=workdir, last_image_path=None)

    # Initial frame.
    obs = _snapshot(env)
    rollout.steps.append(Step(obs=obs, action=obs.state, info={"phase": "reset"}))
    head = obs.images.get("head_camera")
    init_path = workdir / "initial.jpg"
    if head is not None:
        _write_jpg(init_path, head)
        state.last_image_path = init_path

    # Build tool namespace (each function calls _dispatch + returns the result dict).
    # Positional args are mapped to SKILL.md `args` order so VLM can call
    # `move_to_pose('left', bx, by, bz, quat=...)` natively.
    from roborsi.embodied.skills import discover as _discover_skills
    arg_order: dict[str, list[str]] = {}
    for sk in _discover_skills():
        if "base" in sk.path.parent.parts and "robotwin" in sk.path.parent.parts:
            args_block = (sk.frontmatter or {}).get("args") or {}
            if isinstance(args_block, dict):
                arg_order[sk.name] = list(args_block.keys())

    def make_tool(name: str):
        order = arg_order.get(name, [])
        def _f(*args: Any, **kwargs: Any) -> dict[str, Any]:
            merged = dict(kwargs)
            for i, val in enumerate(args):
                if i >= len(order):
                    raise TypeError(
                        f"{name}() got {len(args)} positional args but only "
                        f"{len(order)} are defined in SKILL.md (args: {order})"
                    )
                key = order[i]
                if key in merged:
                    raise TypeError(f"{name}() got multiple values for '{key}'")
                merged[key] = val
            result, _obs = _dispatch(state, {"tool": name, "args": merged})
            return result
        _f.__name__ = name
        return _f

    base_skills = [
        "look", "find_pixel", "zoom_in", "label_points_grid", "get_object_bbox",
        "unproject_pixel", "move_to_pixel", "move_to_pose", "move_fingertip_to",
        "gripper", "is_holding", "verify_holding_visual",
        "get_arm_pose", "get_grasp_pose", "scan_wrist", "estimate_feature_point",
        "measure_distance", "measure_vector", "measure_relative_rotation",
        "rotate_vector", "recall_past_success",
        "grasp_object", "is_reachable", "place_object_in",
    ]
    tool_ns: dict[str, Any] = {name: make_tool(name) for name in base_skills}

    # Special done() that flips a flag and raises a sentinel
    class _DoneSignal(Exception):
        def __init__(self, success: bool, reason: str = ""):
            self.success = success
            self.reason = reason

    def done(success: bool, reason: str = ""):
        raise _DoneSignal(bool(success), str(reason))
    tool_ns["done"] = done

    def author_base_skill(name: str, description: str, code: str,
                          when_to_use: str = "",
                          args_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist a new base skill the VLM just authored.

        Writes skills/base/robotwin/<name>/{SKILL.md, policy.py}. The next
        time _dispatch is asked for this name, it'll auto-load via plugin
        path. Within the CURRENT episode, also evals the code and adds the
        function to tool_ns so subsequent turns can call it.
        """
        return _do_author_base_skill(name, description, code, when_to_use,
                                     args_schema, tool_ns, persistent_ns)
    tool_ns["author_base_skill"] = author_base_skill

    convo = [
        {"role": "user", "content": [
            {"type": "text", "text": (
                f"Task: {instruction}\n\n"
                f"Success criterion: {expected_on_success}\n\n"
                "Write a Python script using the available functions. Begin."
            )},
        ]},
    ]
    if init_path.exists():
        convo[0]["content"].append({"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{_b64_image(init_path)}"
        }})

    # Hook scene.step for dense per-tick capture.
    impl = env._impl
    scene = impl.scene
    original_step = scene.step
    subsample_every = 5
    tick_counter = {"n": 0}
    def _hooked_step():
        from roborsi.embodied.sim.robotwin.adapter import _to_sim_obs
        result = original_step()
        tick_counter["n"] += 1
        if tick_counter["n"] % subsample_every == 0:
            sim_obs = _to_sim_obs(impl.get_obs())
            rollout.steps.append(Step(
                obs=sim_obs, action=sim_obs.state,
                info={"tick": tick_counter["n"], "source": "scene_step"},
            ))
        return result
    scene.step = _hooked_step

    success = False
    outcome = "budget_exceeded"
    final_program: str | None = None
    saved_program_path: str | None = None
    persistent_ns: dict[str, Any] = dict(tool_ns)

    try:
        for turn_idx in range(max_turns):
            # Refresh image attachment if a tool produced one and we haven't shown it
            if state.last_image_path is not None and state.last_image_path.exists():
                # Replace last user content image — append at last user turn
                if convo[-1]["role"] == "user":
                    if isinstance(convo[-1]["content"], list):
                        convo[-1]["content"].append({"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{_b64_image(state.last_image_path)}"
                        }})
                state.last_image_path = None

            # Call VLM (no tools= — code-as-action means we want plain text + code).
            full_system = SYSTEM_PROMPT_CODEACT + "\n\n" + instruction
            response_text = _vlm_complete_text(model or DEFAULT_MODEL, full_system, convo)
            trace.append({"turn": turn_idx, "vlm_response": response_text[:600]})
            convo.append({"role": "assistant", "content": response_text})

            code = _extract_code_block(response_text)
            if not code:
                outcome = "vlm_no_code"
                break

            # Persist the program to /tmp.
            program_path = workdir / f"turn_{turn_idx:02d}.py"
            program_path.write_text(code, encoding="utf-8")
            final_program = str(program_path)

            # Execute in sandbox. We REUSE one namespace across turns so
            # variables (e.g. `bx, by, bz` from earlier unproject) persist —
            # this matches Rollout's IPython-kernel semantics.
            stdout_buf = io.StringIO()
            err_text = ""
            done_signal: _DoneSignal | None = None
            try:
                with contextlib.redirect_stdout(stdout_buf):
                    exec(compile(code, program_path.name, "exec"), persistent_ns)
            except _DoneSignal as ds:
                done_signal = ds
            except Exception as exc:
                err_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

            stdout = stdout_buf.getvalue()
            trace[-1]["stdout"] = stdout[-4000:]
            trace[-1]["error"] = err_text[-2000:]
            trace[-1]["program_path"] = str(program_path)
            trace[-1]["done_signal"] = (
                {"success": done_signal.success, "reason": done_signal.reason}
                if done_signal else None
            )

            # If VLM declared done, stop.
            if done_signal is not None:
                success = bool(done_signal.success)
                outcome = "vlm_declared_done"
                break

            # Otherwise, build the next user turn with stdout + new image.
            obs = _snapshot(env)
            head = obs.images.get("head_camera")
            new_img: Path | None = None
            if head is not None:
                new_img = workdir / f"after_turn_{turn_idx:02d}.jpg"
                _write_jpg(new_img, head)
                state.last_image_path = new_img

            user_content: list[dict[str, Any]] = [{"type": "text", "text": (
                f"Script turn {turn_idx} executed.\n"
                f"STDOUT (last 800):\n{stdout[-800:]}\n\n"
                + (f"ERROR:\n{err_text[-800:]}\n\n" if err_text else "")
                + "Write the next script (or call done()). Image of current scene attached."
            )}]
            if new_img is not None and new_img.exists():
                user_content.append({"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{_b64_image(new_img)}"
                }})
            convo.append({"role": "user", "content": user_content})
    finally:
        scene.step = original_step

    rollout.success = success
    rollout.outcome = outcome
    rollout.meta = {
        "backend": "robotwin",
        "collector": "codeact",
        "model": model or DEFAULT_MODEL,
        "turns": len(trace),
        "vlm_declared": success,
        "physics_ticks": tick_counter["n"],
        "subsample_every": subsample_every,
        "final_program": final_program,
    }

    # Persist the trace + final program (if any) for offline debugging.
    try:
        (workdir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass

    return CodeactResult(rollout=rollout, success=success, outcome=outcome,
                         trace=trace, saved_program_path=final_program)


def promote_program_to_skill(program_path: str, atomic_name: str) -> str | None:
    """Move a successful program from /tmp to skills/atomic/<atomic>/zeroshot/programs/.

    Called by the LH triangle (LHExecutor) after atomic_judge confirms success.
    Returns the new path, or None if anything fails.
    """
    if not program_path:
        return None
    src = Path(program_path)
    if not src.exists():
        return None
    skills_root = Path(__file__).resolve().parents[3] / "skills" / "atomic" / atomic_name / "zeroshot" / "programs"
    skills_root.mkdir(parents=True, exist_ok=True)
    rid = datetime.now().strftime("%Y%m%d-%H%M%S-") + src.stem
    dst = skills_root / f"{rid}.py"
    shutil.copyfile(src, dst)
    return str(dst)


def discard_failed_program(program_path: str) -> None:
    if program_path:
        try:
            Path(program_path).unlink()
        except (FileNotFoundError, OSError):
            pass


def _extract_code_block(text: str) -> str | None:
    """Pull the first ```python ... ``` block (or unfenced fallback)."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, flags=re.DOTALL)
    if m:
        return m.group(1)
    return None


def _b64_image(path: Path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode()


def _vlm_complete_text(model: str, system: str, convo: list[dict[str, Any]]) -> str:
    """Free-form text completion (no tools). Used by codeact mode.

    Provider selection:
      ROBORSI_VLM_PROVIDER=openai  → OpenAI Chat Completions (GPT-5/4o)
      ROBORSI_VLM_PROVIDER=anthropic (default) → Claude Messages API
    """
    import os
    provider = os.environ.get("ROBORSI_VLM_PROVIDER", "anthropic").lower()
    if provider == "openai":
        from roborsi.embodied.agent_loop.vlm_io import _vlm_complete_openai
        return _vlm_complete_openai(model, system, convo)
    return _vlm_complete_anthropic(model, system, convo)


def _vlm_complete_anthropic(model: str, system: str, convo: list[dict[str, Any]]) -> str:
    from roborsi.embodied.agent_loop.vlm_io import _anthropic_client

    client = _anthropic_client()
    model_id = model.split("/", 1)[1] if "/" in model else model
    from roborsi.embodied.agent_loop.messages import _convert_messages_to_anthropic
    resp = client.messages.create(
        model=model_id,
        system=system,
        messages=_convert_messages_to_anthropic(convo),
        max_tokens=2048,
        temperature=1.0,
        extra_body={"output_config": {"effort": "medium"}},
    )
    text = ""
    for blk in resp.content or []:
        if getattr(blk, "type", None) == "text":
            text += blk.text
    return text
