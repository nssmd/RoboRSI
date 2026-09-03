"""Prompt + tool-spec builders and meta-tool dispatch for the VLM loop.

Auto-discovers base/robotwin/<tool>/SKILL.md to build the tools prompt block
and the OpenAI/Anthropic tool specs, composes the system prompt, runs the
two-stage skill shortlist, and handles the codebase-introspection /
skill-proposal meta tools. Skill / agent / plugin imports are kept lazy
(function-local) so this module has no module-load coupling to sim.
"""

from __future__ import annotations

from typing import Any

from roborsi.embodied.agent_loop.config import (
    _SHORTLIST_ALWAYS,
    _embodiment_line,
    _rules_for,
)

# ---- Plugin dispatcher cache (skills-only; no sim). Shared by prompt/tool
#      builders here and the sim dispatch path in robotwin_agent. ----
_PLUGIN_CACHE: dict[str, Any] = {}
# Atomic-scoped compound policies, keyed by (task, name). Opt-in; see
# _try_load_compound_dispatcher.
_COMPOUND_CACHE: dict[tuple[str, str], Any] = {}


def atomic_compounds_enabled() -> bool:
    """Return whether released atomic compounds belong to the runtime surface."""
    import os

    return os.environ.get("ROBORSI_ATOMIC_COMPOUND", "1") != "0"


# ---- Tools that exist for INTERNAL base-skill composition only and must NEVER
#      appear in the Engineer's (VLM's) tool surface. Success is adjudicated only
#      after the episode by the simulator/harness. ----
_ENGINEER_HIDDEN_TOOLS: set[str] = {
    "check_task_success",
    # PURE-VISION: object-ground-truth tools. Deleted from the repo; also
    # hidden here so a stale plugin/registry entry can never resurface them in
    # the Engineer's tool surface. A camera-only robot can't read object world
    # poses / asset contact points / sim contacts.
    "describe_scene_actors", "resolve_actor_by_query",
    "pick_actor_by_contact_point", "grasp_two_via_contact",
    "list_contacts", "verify_contact_pair", "solve_pour_dock",
    "place_held_flat", "grasp_then_lift_graspgen", "grasp_then_lift",
    "press_button_at_xyz", "push_toggle_lateral", "tap_held_on_target",
}


def _hidden_tools(ns: str) -> set[str]:
    """Tools to keep off the Engineer's surface."""
    hidden = set(_ENGINEER_HIDDEN_TOOLS)
    if ns == "libero":
        hidden |= {"describe_scene", "get_object_pose"}
    return hidden


def _load_dispatch_runtime(policy_path, mod_name: str):
    """exec a skill's policy.py and return its ``dispatch_runtime`` (or None).

    Shared by the base-plugin and atomic-compound loaders. Registers the module
    in sys.modules BEFORE exec so any @dataclass (or other construct that does
    sys.modules[cls.__module__] introspection during class-body execution)
    resolves — without this, Python 3.10 dataclasses raise
    `NoneType has no attribute '__dict__'` and the whole dispatch path crashes."""
    if not policy_path.exists():
        return None
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(mod_name, policy_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, "dispatch_runtime", None)


def _try_load_plugin_dispatcher(name: str, ns: str = "robotwin"):
    """Find skills/base/<ns>/<name>/policy.py:dispatch_runtime, cached.

    `ns` is the active backend's skill namespace (config._skill_namespace);
    default "robotwin" keeps every existing caller unchanged. The cache is keyed
    by (ns, name) because a robotwin `set_gripper` and a libero `set_gripper` are
    different files."""
    cache_key = (ns, name)
    if cache_key in _PLUGIN_CACHE:
        return _PLUGIN_CACHE[cache_key]
    from roborsi.embodied.skills import get_ns
    sk = get_ns(name, ns)
    handler = None if sk is None else _load_dispatch_runtime(
        sk.path.parent / "policy.py", f"_baseplugin_{ns}_{name}")
    _PLUGIN_CACHE[cache_key] = handler
    return handler


def _try_load_compound_dispatcher(name: str, task: str):
    """Find skills/atomic/<task>/<name>/policy.py:dispatch_runtime, cached.

    Atomic-scoped solidified compounds (opt-in via ROBORSI_ATOMIC_COMPOUND):
    an Engineer-callable macro that codifies THIS task's proven recipe in code.
    Resolved only after the base-plugin path misses, and only for the running
    task — so a compound never leaks onto another task's tool surface."""
    cache_key = (task, name)
    if cache_key in _COMPOUND_CACHE:
        return _COMPOUND_CACHE[cache_key]
    from roborsi.embodied.skills import discover_compounds
    sk = next((s for s in discover_compounds(task) if s.name == name), None)
    handler = None if sk is None else _load_dispatch_runtime(
        sk.path.parent / "policy.py", f"_compound_{task}_{name}")
    _COMPOUND_CACHE[cache_key] = handler
    return handler


def _legacy_tool_names(ns: str = "robotwin") -> set[str]:
    """The set of skill names wired via a legacy `_do_<name>` handler.

    Read off the robotwin_tools module (the sim tool layer) via an
    `import ... as` reference — a name-list only, no sim objects — so this
    general prompt layer can decide which discovered skills are wired
    without pulling any sim runtime types. Only the robotwin namespace has a
    native `_do_*` layer; other backends (libero) rely solely on the plugin
    `dispatch_runtime` path, so this returns an empty set for them."""
    if ns != "robotwin":
        return set()
    import roborsi.embodied.sim.robotwin.robotwin_tools as _rt
    return {n[4:] for n in vars(_rt) if n.startswith("_do_")}


def _build_tools_block(restrict_to_names: set[str] | None = None,
                        ns: str = "robotwin") -> str:
    """Auto-discover base/<ns>/<tool>/SKILL.md and produce the prompt
    section listing every tool the VLM can call. Adding a new tool = adding a
    SKILL.md; no manual prompt edits.

    `restrict_to_names`: if provided, only include skills whose name is in
    the set. SkillSelector (agents/skill_selector.py) uses this to narrow
    the inner VLM's choice when the registry exceeds SKILL_LIST_SOFT_CAP."""
    from roborsi.embodied.skills import discover_ns
    # A skill is "wired" if either:
    #   1. this module defines _do_<name> (legacy path), OR
    #   2. its policy.py exports `dispatch_runtime` (plugin path).
    wired_legacy = _legacy_tool_names(ns)
    from roborsi.runtime_mode import evolution_enabled
    rows: list[tuple[str, str]] = []
    for sk in discover_ns(ns):
        if sk.name not in wired_legacy and _try_load_plugin_dispatcher(sk.name, ns) is None:
            continue
        if restrict_to_names is not None and sk.name not in restrict_to_names:
            continue
        if sk.name in _hidden_tools(ns):
            continue
        if not evolution_enabled() and sk.name == "register_skill":
            continue
        fm = sk.frontmatter or {}
        desc = str(fm.get("description") or "").strip().replace("\n", " ")
        when = ""
        when_not = ""
        # `when_to_use` may be a YAML literal block string in frontmatter.
        if isinstance(fm.get("when_to_use"), str):
            when = fm["when_to_use"].strip().replace("\n", " ")
        # `when_NOT_to_use` (Auto-Robotist style negative rules) — same shape.
        # Critical for routing: prevents VLM from defaulting to a generic
        # skill when a specialized one applies (e.g. don't grasp_then_lift
        # a precise contact when pick_actor_by_contact_point fits).
        if isinstance(fm.get("when_NOT_to_use"), str):
            when_not = fm["when_NOT_to_use"].strip().replace("\n", " ")
        args_block = fm.get("args") or {}
        if isinstance(args_block, dict):
            arg_names = ", ".join(args_block.keys())
        else:
            arg_names = ""
        sig = f"  {sk.name}({arg_names})" if arg_names else f"  {sk.name}()"
        line = f"{sig}\n    → {desc}"
        if when:
            line += f"\n    When to use: {when}"
        if when_not:
            line += f"\n    When NOT to use: {when_not}"
        rows.append((sk.name, line))
    rows.sort(key=lambda kv: kv[0])
    if not rows:
        return "  (no tools discovered — base/robotwin/<tool>/SKILL.md missing?)"
    return "\n".join(line for _, line in rows)


def _maybe_shortlist_skills(instruction: str, task_name: str,
                            seed: int, ns: str = "robotwin") -> set[str] | None:
    """Two-stage skill selection (user-requested): a cheap Sonnet SkillSelector
    narrows the 70+ base-skill registry to the relevant top-K, so the Engineer
    (Opus) sees a focused, fully-described shortlist and actually surfaces
    SPECIALIZED skills it would otherwise skip among the crowd (e.g.
    pick_actor_by_contact_point for a precise grasp instead of defaulting
    to grasp_then_lift).
    Returns the name set (unioned with always-keep utilities), or None to fall
    back to the full list (registry small, or any error — never breaks a run).

    Only the robotwin namespace has a large enough registry (and the
    robotwin-specific always-keep set) to need this; other backends (libero,
    ~8 skills) fall straight through to the full list."""
    if ns != "robotwin":
        return None
    try:
        from roborsi.agents.engineer import _build_skill_index, _count_active_skills
        from roborsi.agents.skill_history import get_success_counts
        from roborsi.agents.skill_selector import SkillSelector
        # Always run the Sonnet selector — it surfaces specialized skills the
        # Engineer skips among the crowd even at ~44 active (verified: it picks
        # pick_actor_by_contact_point + grasp_then_lift_graspgen for a bowl). Only
        # bypass when the registry is tiny (nothing to narrow).
        if _count_active_skills(ns) < 12:
            return None
        picked = SkillSelector().pick(
            plan_md=instruction, recent_results=[],
            skill_index=_build_skill_index(ns),
            scene_hint=f"task={task_name} seed={seed}",
            success_counts=get_success_counts(task_name))
        if not picked:
            return None
        shortlist = set(picked) | _SHORTLIST_ALWAYS
        print(f"[skill-selector] {task_name}: shortlisted {len(shortlist)} "
              f"skills (picked {len(picked)}): {sorted(picked)[:12]}")
        return shortlist
    except Exception as exc:
        print(f"[skill-selector] fallback to full tool list "
              f"({type(exc).__name__}: {exc})")
        return None


def _system_prompt(restrict_to_names: set[str] | None = None,
                    ns: str = "robotwin") -> str:
    """Compose the live SYSTEM_PROMPT from auto-discovered tools + static rules.

    `restrict_to_names`: when provided, the tools block only lists named
    base/<ns> skills (used by Engineer after SkillSelector top-K).
    `ns`: the active backend's skill namespace — selects the embodiment line
    (dual-arm robotwin vs single-arm libero) and the matching rules block.

    Note (2026-06-15 user request "prompt 不要膨胀"): all per-task lessons
    (BOWL/BLOCK protocols, PRESERVE GRIP rules, IK measurements, etc.)
    are now in the per-task wiki. Engineer is told to read it. This
    system prompt only carries the essentials that apply across all
    tasks.
    """
    prompt = (
        _embodiment_line(ns) + "\n\n"
        "TOOLS: full schemas are provided via the `tools` parameter — browse "
        "them there.\n\n"
        + _rules_for(ns)
    )
    from roborsi.runtime_mode import evaluation_prompt, is_eval_mode
    if is_eval_mode():
        prompt += "\n\n" + evaluation_prompt()
    return prompt


# Kept for backwards compat; live callers should use _system_prompt() each
# episode so SKILL.md edits show up without a process restart.
SYSTEM_PROMPT = _system_prompt()


def _spec_from_skill(sk, type_map: dict[str, str]) -> dict[str, Any]:
    """One OpenAI/Anthropic tool spec from a skill's SKILL.md frontmatter.
    Shared by the base-tool and atomic-compound spec builders."""
    fm = sk.frontmatter or {}
    desc = (fm.get("description") or "").strip()
    if isinstance(fm.get("when_to_use"), str):
        desc = (desc + "\n\nWhen to use: " + fm["when_to_use"].strip()).strip()
    if isinstance(fm.get("when_NOT_to_use"), str):
        desc = (desc + "\n\nWhen NOT to use: " + fm["when_NOT_to_use"].strip()).strip()
    properties: dict[str, Any] = {}
    required: list[str] = []
    # Some legacy SKILL.md files use `params:` instead of `args:`. Read both.
    args = fm.get("args") or fm.get("params") or {}
    for arg_name, meta in args.items() if isinstance(args, dict) else ():
        # Skip internal-only params the VLM should never set / malformed entries.
        if arg_name in {"env", "task_name", "log", "workdir", "model"} \
                or not isinstance(meta, dict):
            continue
        prop: dict[str, Any] = {"type": type_map.get(str(meta.get("type", "string")), "string")}
        if meta.get("description"):
            prop["description"] = str(meta["description"])
        if "default" in meta:
            prop["default"] = meta["default"]
        if "enum" in meta:
            prop["enum"] = meta["enum"]
        properties[arg_name] = prop
        if meta.get("required"):
            required.append(arg_name)
    return {"type": "function", "function": {
        "name": sk.name, "description": desc,
        "parameters": {"type": "object", "properties": properties,
                       "required": required}}}


def _compound_specs(task: str, type_map: dict[str, str]) -> list[dict[str, Any]]:
    """Tool specs for this task's released, solidified compound policies."""
    if not atomic_compounds_enabled() or not task:
        return []
    from roborsi.embodied.skills import discover_compounds
    return [_spec_from_skill(sk, type_map) for sk in discover_compounds(task)
            if _try_load_compound_dispatcher(sk.name, task) is not None]


def _build_tool_specs(ns: str = "robotwin", task: str = "") -> list[dict[str, Any]]:
    """Auto-generate OpenAI/Anthropic-style tool specs from each base/<ns>/<tool>/SKILL.md.

    The VLM gets a typed, schema-validated tool surface (no more parsing free-form
    JSON from text). Adding a tool = adding a SKILL.md. `ns` selects the backend's
    skill namespace (default robotwin). `task` (when ROBORSI_ATOMIC_COMPOUND=1)
    appends that atomic task's solidified compound macros.
    """
    from roborsi.embodied.skills import discover_ns
    wired_legacy = _legacy_tool_names(ns)
    type_map = {"int": "integer", "float": "number", "list": "array",
                "object": "object", "bool": "boolean", "string": "string"}
    specs: list[dict[str, Any]] = []
    from roborsi.runtime_mode import evolution_enabled
    for sk in discover_ns(ns):
        if sk.name not in wired_legacy and _try_load_plugin_dispatcher(sk.name, ns) is None:
            continue
        if sk.name in _hidden_tools(ns):
            continue
        if not evolution_enabled() and sk.name == "register_skill":
            continue
        specs.append(_spec_from_skill(sk, type_map))
    specs.sort(key=lambda s: s["function"]["name"])
    # Opt-in atomic compounds for THIS task, appended after the sorted base
    # tools so the task-specific macros stand out.
    specs.extend(_compound_specs(task, type_map))
    specs.append({
        "type": "function",
        "function": {
            "name": "done",
            "description": "End the episode with your verdict. Call when the success criterion is visibly met OR when further attempts are clearly hopeless.",
            "parameters": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "true iff the success criterion is visibly met in the latest image"},
                    "reason": {"type": "string"},
                },
                "required": ["success"],
            },
        },
    })
    # Meta tools — same set Reviewer has. Engineer can read other base
    # skills, list them, and propose new/updated skills when a missing
    # primitive blocks progress (e.g. "no skill can grasp a bowl rim
    # at z<0.84 — I'll propose a new bowl-rim grasp skill with tilted-EE
    # side grasp"). Proposals go to skill_review/ queue for Claude approval.
    specs.append({"type": "function", "function": {
        "name": "read_skill_code",
        "description": ("Return the policy.py source of an existing "
                         "skill so you can model a new skill on its "
                         "structure or understand why a current skill "
                         "fails. First 8000 chars."),
        "parameters": {"type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"]},
    }})
    specs.append({"type": "function", "function": {
        "name": "list_base_skills",
        "description": ("List all registered base/robotwin skills with "
                         "one-line descriptions. Use to discover which "
                         "skills exist before proposing a new one."),
        "parameters": {"type": "object", "properties": {}},
    }})
    if evolution_enabled():
        specs.append({"type": "function", "function": {
            "name": "propose_new_skill",
            "description": ("Propose a brand-new base skill. `code` MUST be "
                             "complete valid Python (full policy.py drop-in). "
                             "Generated code may only compose literal public "
                             "skills through `_dispatch_tool`; it cannot read "
                             "`state.env` or backend internals. "
                             "`skill_md` MUST include YAML frontmatter with a "
                             "`harness:` block (sim_task + args + pass_criteria). "
                             "Goes to skill_review/ for Claude 3-gate approval; "
                             "applied next LH run if approved."),
            "parameters": {"type": "object",
                "properties": {"name": {"type": "string"},
                                "category": {"type": "string"},
                                "description": {"type": "string"},
                                "code": {"type": "string"},
                                "skill_md": {"type": "string"},
                                "rationale": {"type": "string"}},
                "required": ["name", "description", "code",
                              "skill_md", "rationale"]},
        }})
        specs.append({"type": "function", "function": {
            "name": "propose_skill_update",
            "description": ("Propose updating an existing skill. `new_code` "
                             "MUST be complete valid Python (full policy.py "
                             "replacement, NOT a diff or TODO) and may only "
                             "compose literal public skills through "
                             "`_dispatch_tool`; no `state.env`. Goes to "
                             "skill_review/ for Claude approval."),
            "parameters": {"type": "object",
                "properties": {"name": {"type": "string"},
                                "new_code": {"type": "string"},
                                "skill_md": {"type": "string"},
                                "rationale": {"type": "string"}},
                "required": ["name", "new_code", "rationale"]},
        }})
    return specs


def _build_status_check_prompt() -> str:
    """STATUS CHECK with the active plan's current substep success_evidence
    surfaced — VLM has an OBJECTIVE target, not a subjective vibe check."""
    base = (
        "STATUS CHECK (after the action above). Reply with EXACTLY ONE of "
        "these next-step categories AS THE VERY FIRST WORD of your next "
        "message, then issue the corresponding tool_calls:\n"
        "  PROCEED   - last action OK, advance to next substep\n"
        "  REPLAN    - plan itself wrong; emit plan() with revised substeps\n"
        "  RETRY     - recoverable; try the substep's FALLBACK or DIFFERENT params\n"
        "              (NEVER retry identical args)\n"
        "  DONE      - goal achieved or unreachable; call done(success=T|F)\n"
    )
    try:
        from roborsi.embodied.skills.base.plan.robotwin.policy import (
            get_active_plan,
        )
        plan = get_active_plan()
    except Exception:
        plan = {}
    if not plan or not plan.get("substeps"):
        return base + "\n(No active plan recorded - emit plan() first if you haven't.)"
    cursor = int(plan.get("cursor", 0))
    substeps = plan["substeps"]
    if cursor >= len(substeps):
        return base + "\n(All substeps completed per cursor - likely DONE.)"
    cur = substeps[cursor]
    retry_n = plan.get("retry_counts", [0] * len(substeps))[cursor]
    extra = (
        f"\nCurrent substep: [{cursor}] '{cur['name']}' "
        f"(progress contribution: {cur.get('progress_pct', '?')}%)\n"
        f"  Primary strategy: {cur.get('primary', '')}\n"
        f"  SUCCESS EVIDENCE TO LOOK FOR: {cur.get('success_evidence') or '(none specified)'}\n"
        f"  Fallback: {cur.get('fallback') or '(none specified)'}\n"
        f"  Retries used on this substep: {retry_n}\n"
        "Verify the action result against SUCCESS EVIDENCE before PROCEEDing."
    )
    from roborsi.runtime_mode import evolution_enabled
    if evolution_enabled():
        extra += (
            "\nIf no existing skill can do what you need, you may call "
            "register_skill(name, code, docstring) to define a new helper, "
            "then use it from exec_python."
        )
    return base + extra


def _dispatch_meta_tool(
    name: str,
    args: dict[str, Any],
    *,
    ns: str = "robotwin",
) -> dict[str, Any] | None:
    """Handle the codebase-introspection + skill-proposal meta tools.
    Returns a result dict if the tool is meta, None otherwise so callers
    fall through to the regular skill dispatchers."""
    if name == "read_skill_code":
        from roborsi.embodied.skills import get_ns
        requested = args.get("name", "")
        if requested in _hidden_tools(ns):
            return {
                "ok": False,
                "reason": f"skill '{requested}' is not visible to the Engineer",
            }
        sk = get_ns(requested, ns)
        if sk is None:
            return {
                "ok": False,
                "reason": (
                    f"public base skill '{requested}' not found in namespace '{ns}'"
                ),
            }
        py = sk.path.parent / "policy.py"
        if not py.exists():
            return {"ok": True, "note": f"'{sk.name}' has no policy.py"}
        return {"ok": True, "name": sk.name,
                 "policy_py": py.read_text(encoding="utf-8")[:8000]}
    if name == "list_base_skills":
        from roborsi.embodied.skills import discover_ns
        from roborsi.runtime_mode import evolution_enabled
        rows = []
        for s in sorted(discover_ns(ns), key=lambda x: x.name):
            if s.name in _hidden_tools(ns):
                continue
            if not evolution_enabled() and s.name == "register_skill":
                continue
            desc = (s.description or "")[:90]
            rows.append({"name": s.name, "description": desc})
        return {"ok": True, "count": len(rows), "skills": rows[:120]}
    if name in ("propose_new_skill", "propose_skill_update"):
        from roborsi.runtime_mode import evolution_enabled
        if not evolution_enabled():
            return {
                "ok": False,
                "reason": "skill proposals are disabled in eval mode",
            }
        from roborsi.channels.core.agent import _enqueue_proposal
        kind = "new" if name == "propose_new_skill" else "update"
        pid_or_msg = _enqueue_proposal(kind=kind, **args)
        return {"ok": True, "proposal": pid_or_msg}
    return None
