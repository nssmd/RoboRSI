"""base.robotwin.register_skill — VLM-authored skill creation.

VLM defines a new helper Python function; we validate, parse, save it
to the per-task function library, and add it to the in-memory namespace
so subsequent exec_python calls can use it as if it were a base skill.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path
from typing import Any


# In-memory cache of VLM-authored helpers, keyed by name → callable.
# Picked up by exec_python's namespace builder.
_RUNTIME_REGISTRY: dict[str, Any] = {}


def get_runtime_registered() -> dict[str, Any]:
    """exec_python uses this to bind VLM-authored helpers into snippets."""
    from roborsi.runtime_mode import evolution_enabled
    if not evolution_enabled():
        return {}
    return dict(_RUNTIME_REGISTRY)


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.runtime_mode import evolution_enabled
    if not evolution_enabled():
        return (
            {"ok": False, "reason": "register_skill is disabled in eval mode"},
            _snapshot(state.env),
        )
    from roborsi.embodied.sim.robotwin.robotwin_agent import _ensure_registry
    name = (args.get("name") or "").strip()
    code = args.get("code") or ""
    docstring = (args.get("docstring") or "").strip()
    test_kwargs = args.get("test_call_args")

    if not name or not code or not docstring:
        return ({"ok": False,
                 "reason": "name, code, docstring all required"},
                _snapshot(state.env))
    if not name.replace("_", "").isalnum() or not name.islower():
        return ({"ok": False,
                 "reason": f"name {name!r} must be snake_case (lowercase + underscores)"},
                _snapshot(state.env))

    # Collision: with existing base skill plugins or legacy handlers.
    from roborsi.embodied.skills import discover as all_skills
    existing_skills = {sk.name for sk in all_skills()}
    legacy = set(_ensure_registry().keys())
    if name in existing_skills or name in legacy:
        return ({"ok": False,
                 "reason": f"name {name!r} collides with existing base skill"},
                _snapshot(state.env))
    if name in _RUNTIME_REGISTRY:
        # Allow REPLACE — VLM iterating on a helper.
        pass

    # Parse: must contain a top-level def with matching name.
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return ({"ok": False,
                 "reason": f"SyntaxError in code: {e}"}, _snapshot(state.env))
    target_def = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            target_def = node
            break
    if target_def is None:
        return ({"ok": False,
                 "reason": (f"no top-level `def {name}(...)` found in code; "
                            f"saw {[n.name for n in tree.body if isinstance(n, ast.FunctionDef)]}")},
                _snapshot(state.env))

    # Compile + bind into a namespace shared with exec_python.
    from roborsi.embodied.skills.base.exec_python.robotwin.policy import (
        _safe_builtins, _BLOCKLIST,
    )
    from roborsi.embodied.agent_loop.prompt_tools import _try_load_plugin_dispatcher

    def _make_skill_fn(skill_name: str):
        def fn(**kwargs):
            handler = _try_load_plugin_dispatcher(skill_name)
            if handler is None:
                handler = _ensure_registry().get(skill_name)
            if handler is None:
                raise RuntimeError(f"skill {skill_name!r} has no dispatcher")
            result, _ = handler(state, kwargs)
            return result
        fn.__name__ = skill_name
        return fn

    bound: dict[str, Any] = {"__builtins__": _safe_builtins()}
    # Bind every base skill so the VLM's new function can call them.
    for sk in all_skills():
        if "base" not in sk.path.parent.parts: continue
        if "robotwin" not in sk.path.parent.parts: continue
        if sk.name in _BLOCKLIST: continue
        bound[sk.name] = _make_skill_fn(sk.name)
    for legacy_name in legacy:
        if legacy_name in _BLOCKLIST or legacy_name in bound: continue
        bound[legacy_name] = _make_skill_fn(legacy_name)
    # Existing VLM-registered helpers can call each other too.
    for k, v in _RUNTIME_REGISTRY.items():
        bound.setdefault(k, v)

    try:
        exec(compile(code, f"<vlm-skill:{name}>", "exec"), bound, bound)
    except Exception as e:
        return ({"ok": False,
                 "reason": f"exec of definition raised: {type(e).__name__}: {e}"},
                _snapshot(state.env))
    fn = bound.get(name)
    if not callable(fn):
        return ({"ok": False,
                 "reason": f"after exec, {name!r} is not callable"},
                _snapshot(state.env))

    # Optional test invocation. Capture before/after head_camera images
    # so human reviewer can visually verify what the skill DID in sim.
    test_invoke = None
    test_images: dict[str, str] = {}
    if test_kwargs is not None:
        if not isinstance(test_kwargs, dict):
            return ({"ok": False,
                     "reason": "test_call_args must be a dict"},
                    _snapshot(state.env))
        test_images["before"] = _capture_head_camera_jpg(state, "before")
        try:
            tr = fn(**test_kwargs)
            test_invoke = {"ok": True, "result_preview": str(tr)[:300]}
        except Exception as e:
            test_invoke = {"ok": False,
                           "exception": f"{type(e).__name__}: {e}"}
            test_images["after"] = _capture_head_camera_jpg(state, "after_fail")
            return ({"ok": False,
                     "reason": f"test invocation failed: {test_invoke['exception']}",
                     "test_invoke": test_invoke,
                     "test_images": test_images},
                    _snapshot(state.env))
        test_images["after"] = _capture_head_camera_jpg(state, "after")

    # HUMAN APPROVAL GATE — VLM-authored skills are NEW behavior; human
    # eyeballs the code briefly before activation. Modes (env-controlled):
    #   default interactive (TTY) / queue (non-TTY) / auto_approve / reject_all
    impl = getattr(state.env, "_impl", None)
    task_name = getattr(impl, "task_name", None)
    from roborsi.embodied.skills._lib.human_review.skill_review import (
        review_proposal,
    )
    verdict = review_proposal(
        name=name, code=code, docstring=docstring,
        test_call_args=test_kwargs, task_name=task_name,
        test_images=test_images,
        test_result_preview=(test_invoke.get("result_preview") if test_invoke else None),
    )
    if not verdict.approved:
        return ({"ok": False,
                 "reason": f"human {verdict.mode} REJECTED: {verdict.reviewer_note}",
                 "review_mode": verdict.mode,
                 "review_elapsed_s": round(verdict.elapsed_s, 1),
                 "test_invoke": test_invoke,
                 "test_images": test_images,
                 "note": ("Skill NOT registered. Address the reviewer's "
                          "concern in your next iteration before re-submitting "
                          "register_skill().")},
                _snapshot(state.env))

    # Register live + persist to per-task library.
    _RUNTIME_REGISTRY[name] = fn
    _persist_to_library(name, code, docstring, state)

    return ({"ok": True,
             "name": name,
             "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "review_mode": verdict.mode,
             "review_note": verdict.reviewer_note,
             "review_elapsed_s": round(verdict.elapsed_s, 1),
             "test_invoke": test_invoke,
             "test_images": test_images,
             "n_chars": len(code),
             "note": (
                 f"Skill {name!r} approved by human ({verdict.mode}) "
                 "and registered. Call it from exec_python like any base "
                 "skill: `r = " + name + "(...)`. Persisted to the per-task "
                 "function library; successful future trials will increment "
                 "its occurrence count for cross-task auto-promotion.")},
            _snapshot(state.env))


def _capture_head_camera_jpg(state, suffix: str) -> str:
    """Save head_camera RGB to /tmp/skill_review_imgs/ and return path."""
    import cv2
    import numpy as np
    impl = getattr(state.env, "_impl", None)
    if impl is None:
        return ""
    impl._update_render(); impl.cameras.update_picture()
    rgb = impl.cameras.get_rgb().get("head_camera", {}).get("rgb")
    if rgb is None:
        return ""
    if rgb.dtype != np.uint8:
        rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
                if rgb.max() <= 1 else rgb.astype(np.uint8))
    out_dir = Path("/tmp/skill_review_imgs"); out_dir.mkdir(exist_ok=True)
    p = out_dir / f"{int(time.time() * 1000)}-{suffix}.jpg"
    cv2.imwrite(str(p), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return str(p)


def _persist_to_library(name: str, code: str, docstring: str, state):
    """Save VLM-authored skill to <task>/_function_library.json. We don't
    know the active task name at base-skill level, so try to read it from
    state.env._impl.task_name (RoboTwin sets this), fallback to 'shared'."""
    impl = getattr(state.env, "_impl", None)
    task_name = getattr(impl, "task_name", None) or "shared"
    from roborsi.embodied.skills._lib.library.skill_library import (
        _function_library_path,
    )
    import json
    p = _function_library_path(task_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
        except Exception:
            existing = {}
    by_name = {f["name"]: f for f in existing.get("functions", [])}
    if name in by_name:
        # Update existing entry without bumping occurrences (occurrence
        # bumps come from successful trial completion only).
        by_name[name]["code"] = code
        by_name[name]["docstring"] = docstring
        by_name[name]["author"] = "vlm_register"
    else:
        by_name[name] = {"name": name, "code": code, "docstring": docstring,
                          "occurrences": 1, "author": "vlm_register"}
    p.write_text(json.dumps({"functions": list(by_name.values())}, indent=2))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
