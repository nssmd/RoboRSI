#!/usr/bin/env python3
"""Base-skill harness — exercise a single base skill (single mode) OR every
base/robotwin skill that declares a `harness:` block in its SKILL.md
(batch mode). Reports per-call ok/success/holding_visual + verdict.

Per EDIT.md §5: every base skill must be validated here before landing.
Per harness_standard/SKILL.md: pass criteria + frontmatter spec.

Modes:
    # Single skill, explicit args (legacy):
    python scripts/test_base_skill.py <skill_name> <sim_task> --seed N \\
        --args '{...}' [--extra-args '{...}']

    # Single skill, args from SKILL.md frontmatter:
    python scripts/test_base_skill.py <skill_name> --from-frontmatter

    # Batch: every base/robotwin/*/SKILL.md with a harness: block:
    python scripts/test_base_skill.py --batch

    # Batch but only a filter:
    python scripts/test_base_skill.py --batch --filter "pick_*"

Output:
    Single mode → stdout, exit 0 on PASS.
    Batch mode → JSON report at ~/.roborsi/harness_reports/<ts>.json,
                 summary table to stdout. Exit 0 iff zero FAIL/ERROR.

Must run inside the RoboTwin conda env with
    ROBORSI_BICOORD_ROOT=/path/to/BiCoord-Bench
and cwd at the BiCoord-Bench root (relative asset paths).
"""
from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import importlib
import json
import os
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


_REPO = Path(__file__).resolve().parents[1]
_BASE_DIR = _REPO / "roborsi/embodied/skills/base"
_REPORT_DIR = Path.home() / ".roborsi" / "harness_reports"


@dataclasses.dataclass
class _State:
    env: object
    workdir: Path
    last_image_path: Path | None = None


def _load_skill_dispatch(skill_name: str):
    """Return a callable (state, args) → (result, obs). Three paths tried
    in order:
      1. policy.dispatch_runtime(state, args) — newest plugin convention
      2. robotwin_agent._dispatch via registered _do_<name> handler
      3. policy.run(env, **args) — legacy run() interface, no rollout wiring

    A skill only callable via run_skill from outside the sim loop still
    gets exercised here, which catches real bugs that wouldn't otherwise
    surface in either of the other two paths."""
    mod_path = f"roborsi.embodied.skills.base.{skill_name}.robotwin.policy"
    mod = None
    try:
        mod = importlib.import_module(mod_path)
    except ImportError:
        pass
    if mod is not None:
        dr = getattr(mod, "dispatch_runtime", None)
        if dr is not None:
            return dr
    from roborsi.embodied.sim.robotwin.robotwin_agent import _dispatch, _ensure_registry, _snapshot
    if skill_name in _ensure_registry():
        def _router(state, args):
            return _dispatch(state, {"tool": skill_name, "args": args})
        return _router
    if mod is not None and callable(getattr(mod, "run", None)):
        run_fn = mod.run
        def _legacy(state, args):
            result = run_fn(env=state.env, **(args or {}))
            return (result or {"ok": False, "reason": "run() returned None"},
                    _snapshot(state.env))
        return _legacy
    def _missing(state, args):
        return ({"ok": False,
                  "reason": f"no dispatch_runtime / _do_{skill_name} / run() entry"},
                _snapshot(state.env))
    return _missing


def _load_frontmatter(skill_name: str) -> dict:
    """Read SKILL.md YAML frontmatter. Returns {} if missing."""
    import re
    skill_md = _BASE_DIR / skill_name / "robotwin" / "SKILL.md"
    if not skill_md.exists():
        return {}
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        import yaml
        return yaml.safe_load(m.group(1)) or {}
    except ImportError:
        return {}


def _boot_env(sim_task: str, seed: int):
    from roborsi.embodied.agent_loop import get_backend
    be = get_backend("bicoord")
    env = be.make_env(sim_task, {"require_depth": True})
    env.reset(seed=seed)
    return env


def _run_one(dispatch, env, args: dict, label: str, *,
              state: _State | None = None) -> tuple[dict, _State]:
    """Returns (result_dict, state_used). Pass an existing state to share
    workdir + last_image_path across calls (needed for setup→target
    chains where the target reads state from setup, e.g. zoom_in needing
    look's recent image)."""
    if state is None:
        state = _State(env=env, workdir=Path("/tmp/base_skill_harness"))
        state.workdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    crashed = False
    try:
        result, _obs = dispatch(state, args)
    except Exception as exc:  # noqa: BLE001 — the harness must GRADE a crashing
        # skill, not die WITH it. A raised exception = the skill crashed on this
        # seed (e.g. BiCoord asserts target_pose None when no IK-feasible grasp).
        # Record it as a crashed, non-passing seed so the gate can (a) finish
        # grading the other seeds and (b) see crash_count DROP when a defensive
        # fix converts the hard crash into a clean ok=False. This is observation,
        # not error-swallowing: the crash is surfaced verbatim in `reason`.
        crashed = True
        result = {"ok": False, "success": False,
                   "reason": f"CRASH {type(exc).__name__}: {exc}"}
    wall = time.time() - t0
    res = {
        "label": label,
        "args": args,
        "wall_s": round(wall, 2),
        "ok": result.get("ok"),
        "success": result.get("success"),
        "holding_visual": result.get("holding_visual"),
        "verify_source": result.get("verify_source") or result.get("source"),
        "reason": (result.get("reason") or "")[:160],
        "crashed": crashed,
        "extras": {k: v for k, v in result.items()
                    if k not in {"ok", "success", "holding_visual",
                                 "verify_source", "source", "reason"}},
    }
    return res, state


def _grade(harness: dict, results: list[dict]) -> dict:
    """Apply pass criteria from harness_standard. Returns
    {verdict, pass_count, fail_count, reason}."""
    pc = harness.get("pass_criteria") or {}
    kind = pc.get("kind", "ok_true")
    min_pass = int(pc.get("min_seeds_passing", 1))

    def _seed_passed(r: dict) -> bool:
        if kind == "grasp_holds_actor":
            # Pass if the skill reports holding (regardless of which
            # verify path produced the signal). Older skills that don't
            # pass actor_name to verify_holding_visual fall to pixel
            # heuristic which doesn't tag verify_source — accepting
            # success=True OR holding_visual=True covers both.
            return bool(r.get("holding_visual")) or bool(r.get("success"))
        if kind in ("verify_returns_bool", "move_completes"):
            return bool(r.get("ok"))
        if kind == "tool_returns_well_formed":
            req = set(pc.get("required_keys") or [])
            extras = set(r.get("extras") or {})
            # Some legacy skills don't return ok=True explicitly but DO
            # return data (rgb, shape, joint_state). Accept "ok present
            # AND required_keys met" OR "required_keys met and at least
            # one non-trivial extra key returned" — the latter handles
            # capture_image / read_joint_state which omit ok.
            keys_ok = req.issubset(extras | {"ok", "success",
                                                "holding_visual", "reason"})
            has_payload = len(extras) > 0
            return keys_ok and (bool(r.get("ok")) or has_payload)
        return bool(r.get("ok"))

    passes = sum(1 for r in results if _seed_passed(r))
    crashes = sum(1 for r in results if r.get("crashed"))
    verdict = "PASS" if passes >= min_pass else "FAIL"
    return {"verdict": verdict, "kind": kind,
             "pass_count": passes, "total": len(results),
             "crash_count": crashes,
             "min_required": min_pass,
             "reason": ("" if verdict == "PASS"
                         else f"only {passes}/{len(results)} seeds passed (need {min_pass})")}


def _run_skill_from_frontmatter(skill_name: str) -> dict:
    fm = _load_frontmatter(skill_name)
    harness = ((fm.get("metadata") or {}).get("harness")) or fm.get("harness")
    if not harness:
        return {"skill": skill_name, "verdict": "SKIP",
                 "reason": "no harness: block in SKILL.md"}
    if harness.get("skip_harness"):
        return {"skill": skill_name, "verdict": "SKIP",
                 "reason": harness.get("skip_reason") or "skip_harness=true"}
    sim_task = harness.get("sim_task")
    args_list = harness.get("args") or []
    extra = harness.get("extra_args") or []
    seeds = harness.get("seeds") or [0, 1, 2]
    # Optional setup hook: run another base skill first (e.g. pick before
    # place). Spec: {skill: <name>, args: <dict>}. Setup result discarded;
    # only matters that it leaves the env in the prerequisite state.
    setup = harness.get("setup") or {}
    if not sim_task or not args_list:
        return {"skill": skill_name, "verdict": "MALFORMED",
                 "reason": "harness missing sim_task or args"}
    dispatch = _load_skill_dispatch(skill_name)
    setup_dispatch = (_load_skill_dispatch(setup["skill"])
                        if setup.get("skill") else None)
    all_results: list[dict] = []
    for seed in seeds:
        env = _boot_env(sim_task, int(seed))
        shared_state: _State | None = None
        if setup_dispatch is not None:
            _, shared_state = _run_one(setup_dispatch, env,
                                          setup.get("args") or {},
                                          f"setup-seed{seed}")
        for i, a in enumerate(args_list):
            r, shared_state = _run_one(dispatch, env, a,
                                          f"seed{seed}-args{i}",
                                          state=shared_state)
            all_results.append(r)
        for i, a in enumerate(extra):
            env.reset(seed=int(seed))
            shared_state = None
            if setup_dispatch is not None:
                _, shared_state = _run_one(setup_dispatch, env,
                                              setup.get("args") or {},
                                              f"setup-seed{seed}-extra")
            r, shared_state = _run_one(dispatch, env, a,
                                          f"seed{seed}-extra{i}",
                                          state=shared_state)
            all_results.append(r)
    grade = _grade(harness, all_results)
    return {"skill": skill_name, "sim_task": sim_task, "seeds": seeds,
             "results": all_results, **grade}


def _batch(filter_glob: str | None) -> dict:
    skills = sorted(d.name for d in _BASE_DIR.iterdir()
                    if d.is_dir() and (d / "robotwin" / "SKILL.md").exists())
    if filter_glob:
        skills = [s for s in skills if fnmatch.fnmatch(s, filter_glob)]
    report = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
               "total": len(skills), "by_skill": {}}
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "MALFORMED": 0, "ERROR": 0}
    for s in skills:
        print(f"[harness] {s} ...", flush=True)
        try:
            r = _run_skill_from_frontmatter(s)
        except Exception as e:                                  # noqa: BLE001
            r = {"skill": s, "verdict": "ERROR",
                  "reason": f"{type(e).__name__}: {e}"}
        report["by_skill"][s] = r
        v = r.get("verdict", "ERROR")
        counts[v] = counts.get(v, 0) + 1
        print(f"           → {v}  {r.get('reason','')[:100]}", flush=True)
    report["counts"] = counts
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fp = _REPORT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    fp.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    print(f"\n[batch] report → {fp}")
    print(f"[batch] {counts}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skill_name", nargs="?")
    ap.add_argument("sim_task", nargs="?")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--args")
    ap.add_argument("--extra-args", action="append", default=[])
    ap.add_argument("--reset-between", action="store_true")
    ap.add_argument("--from-frontmatter", action="store_true")
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--filter", help="batch mode skill name glob")
    args = ap.parse_args()

    if args.batch:
        rep = _batch(args.filter)
        return 0 if rep["counts"]["FAIL"] == 0 and rep["counts"]["ERROR"] == 0 else 1

    if args.from_frontmatter:
        if not args.skill_name:
            ap.error("skill_name required for --from-frontmatter")
        r = _run_skill_from_frontmatter(args.skill_name)
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        return 0 if r.get("verdict") == "PASS" else 1

    if not args.skill_name or not args.sim_task or not args.args:
        ap.error("legacy mode requires skill_name, sim_task, --args")
    primary = json.loads(args.args)
    extras = [json.loads(s) for s in args.extra_args]
    dispatch = _load_skill_dispatch(args.skill_name)
    env = _boot_env(args.sim_task, args.seed)
    r0, st = _run_one(dispatch, env, primary, "primary")
    results = [r0]
    for i, extra in enumerate(extras, 1):
        if args.reset_between:
            env.reset(seed=args.seed)
            st = None
        r, st = _run_one(dispatch, env, extra, f"extra-{i}", state=st)
        results.append(r)
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    any_held = any(r.get("holding_visual") for r in results)
    any_ok = any(r.get("ok") for r in results)
    print(f"\nverdict: {'PASS' if (any_held or any_ok) else 'FAIL'}")
    return 0 if any_held or any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
