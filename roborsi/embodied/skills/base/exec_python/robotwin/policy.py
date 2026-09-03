"""base.robotwin.exec_python — Code-as-Policy escape hatch.

VLM emits a Python snippet; the snippet has every other base skill
auto-bound as a Python function returning the skill's result dict.
Output captured. Sandbox uses exec() with a controlled globals dict
(no import of OS/subprocess/etc.) — same model as CaP-X.
"""
from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

import numpy as np


# Names of base skills to expose. Excludes itself + heavy/destructive ones
# the VLM shouldn't invoke from inside a snippet.
_BLOCKLIST = {"exec_python", "plan", "done", "execute_with_pi05",
              # PURE-VISION: never let a VLM snippet reach privileged sim
              # ground truth. These skills read object world poses / asset
              # contact points / sim contacts (a camera-only robot can't).
              # They are deleted from the tool surface; blocklist them here so
              # exec_python can't call them either. exec_python's namespace
              # never binds `state`/`_impl` directly (skills are closures), so
              # blocking these severs the only path to object GT.
              "describe_scene_actors", "resolve_actor_by_query",
              "pick_actor_by_contact_point", "grasp_two_via_contact",
              "list_contacts", "verify_contact_pair", "solve_pour_dock",
              "place_held_flat", "grasp_then_lift_graspgen", "grasp_then_lift",
              "press_button_at_xyz", "push_toggle_lateral", "tap_held_on_target"}


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot

    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return ({"ok": False, "reason": "code (str) required"},
                _snapshot(state.env))
    description = (args.get("description") or "").strip()

    # Build the in-snippet namespace: bind every base/robotwin skill that
    # has a plugin dispatcher AND every legacy _do_<name> handler in
    # robotwin_agent as a Python function returning its result.
    from roborsi.embodied.agent_loop.prompt_tools import _try_load_plugin_dispatcher
    from roborsi.embodied.sim.robotwin.robotwin_agent import _ensure_registry
    from roborsi.embodied.skills import discover as all_skills

    call_counter = {"n": 0}
    return_dict: dict[str, Any] = {}

    def _make_handler_fn(name: str, handler):
        def fn(**kwargs):
            call_counter["n"] += 1
            result, _ = handler(state, kwargs)
            return result
        fn.__name__ = name
        return fn

    def _make_skill_fn(name: str):
        def fn(**kwargs):
            handler = _try_load_plugin_dispatcher(name)
            if handler is None:
                # Fallback to legacy _do_<name> in robotwin_tools.
                handler = _ensure_registry().get(name)
            if handler is None:
                raise RuntimeError(f"skill {name!r} has no dispatcher")
            call_counter["n"] += 1
            result, _ = handler(state, kwargs)
            return result
        fn.__name__ = name
        fn.__doc__ = f"Auto-bound skill {name!r}; returns its result dict."
        return fn

    bound: dict[str, Any] = {
        "np": np, "numpy": np, "math": __import__("math"),
        "return_dict": return_dict,
        "state_scratch": _scratch_for_state(state),
        "__builtins__": _safe_builtins(),
    }
    n_bound = 0
    # 1) plugin-based base skills (skills/base/robotwin/<name>/policy.py)
    for sk in all_skills():
        if "base" not in sk.path.parent.parts:
            continue
        if "robotwin" not in sk.path.parent.parts:
            continue
        name = sk.name
        if name in _BLOCKLIST:
            continue
        bound[name] = _make_skill_fn(name)
        n_bound += 1
    # 2) legacy _do_<name> handlers (look, gripper, move_to_pose, etc.)
    for legacy_name, handler in _ensure_registry().items():
        if legacy_name in _BLOCKLIST or legacy_name in bound:
            continue
        bound[legacy_name] = _make_handler_fn(legacy_name, handler)
        n_bound += 1
    # 3) VLM-authored helpers from register_skill
    try:
        from roborsi.embodied.skills.base.register_skill.robotwin.policy import (
            get_runtime_registered,
        )
        for vname, vfn in get_runtime_registered().items():
            if vname in _BLOCKLIST or vname in bound:
                continue
            bound[vname] = vfn
            n_bound += 1
    except ImportError:
        pass

    stdout = io.StringIO()
    stderr = io.StringIO()
    exc_text = None
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            exec(compile(code, "<vlm-snippet>", "exec"), bound, bound)
        except Exception as e:
            exc_text = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    out_stdout = stdout.getvalue()
    out_stderr = stderr.getvalue()
    result = {
        "ok": exc_text is None,
        "description": description,
        "stdout": out_stdout[-4000:],
        "stderr": out_stderr[-2000:],
        "returned_values_dict": _jsonable(return_dict),
        "exception": exc_text,
        "n_skill_calls": call_counter["n"],
        "n_skills_bound": n_bound,
        "note": ("Code-as-Policy result. stdout shows everything printed; "
                  "returned_values_dict surfaces values you put in "
                  "return_dict[...]. n_skill_calls counts how many base "
                  "skills the snippet invoked. exception is None on success."),
    }
    return (result, _snapshot(state.env))


_SHARED_SCRATCH: dict[str, Any] = {}


def _scratch_for_state(state: Any) -> dict[str, Any]:
    """Keep eval scratch episode-local while preserving evolve semantics."""
    from roborsi.runtime_mode import is_eval_mode
    if not is_eval_mode():
        return _SHARED_SCRATCH
    scratch = getattr(state, "_roborsi_eval_scratch", None)
    if scratch is None:
        scratch = {}
        setattr(state, "_roborsi_eval_scratch", scratch)
    return scratch


def _safe_builtins() -> dict[str, Any]:
    """Whitelist of builtins for the sandbox. Allows defensive coding
    (try/except, import of safe modules) but no file/network/process."""
    import builtins
    SAFE = ("abs", "all", "any", "bool", "dict", "enumerate", "filter",
            "float", "int", "isinstance", "issubclass", "len", "list", "map",
            "max", "min", "next", "print", "range", "repr", "reversed",
            "round", "set", "slice", "sorted", "str", "sum", "tuple",
            "type", "zip", "True", "False", "None",
            # Object introspection — pure, no I/O; VLM snippets constantly
            # need these to read sim actor attributes (hasattr/getattr were
            # missing → "NameError: 'hasattr' not defined" on pick_diverse_bottles).
            "getattr", "hasattr", "setattr", "callable", "vars", "dir",
            "hash", "format", "chr", "ord", "divmod", "pow", "bytes",
            "frozenset", "bytearray",
            # Exceptions: VLM needs these for try/except defensive code.
            "Exception", "ValueError", "KeyError", "TypeError",
            "IndexError", "AttributeError", "RuntimeError", "AssertionError",
            "ZeroDivisionError", "NameError", "ArithmeticError", "StopIteration")
    out = {k: getattr(builtins, k) for k in SAFE if hasattr(builtins, k)}
    # Restricted importer: only allow safe std-lib + scientific modules.
    SAFE_IMPORTS = {"math", "numpy", "json", "re", "time", "itertools",
                     "functools", "collections", "operator", "traceback",
                     "copy", "dataclasses"}
    real_import = builtins.__import__
    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root in SAFE_IMPORTS:
            return real_import(name, globals, locals, fromlist, level)
        raise ImportError(f"sandboxed: import of {name!r} not allowed; "
                           f"only {sorted(SAFE_IMPORTS)} permitted.")
    out["__import__"] = safe_import
    return out


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively convert dict values to JSON-serializable forms."""
    import json
    def coerce(v):
        if isinstance(v, np.ndarray):
            return v.tolist()
        if isinstance(v, (np.floating, np.integer)):
            return v.item()
        if isinstance(v, dict):
            return {str(k): coerce(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [coerce(x) for x in v]
        try:
            json.dumps(v); return v
        except (TypeError, ValueError):
            return str(v)
    return {str(k): coerce(v) for k, v in d.items()}


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
