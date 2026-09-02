#!/usr/bin/env python3
"""Fail if ground truth has reached agent-readable state.

The Planner/Engineer/Reviewer are camera-only by design (commit ededc01,
"remove all privileged ground truth from the RoboTwin agent"). That commit
blinded the *code paths* but left the *accumulated memory* untouched, and the
task wikis — which the Planner is told to treat as highest-trust and incorporate
verbatim — kept collecting the sim's success criterion for another ten days.
Reading the code could never have caught it; the code was correct. Only scanning
the artifacts backwards does. Hence this check.

What counts as a leak
---------------------
The success criterion is a SPEC, not an observable: no camera can measure
"success requires z > 0.82" — that is a rule someone read out of the env source.
Object state is different: a robot that lifts a bottle 8 cm and watches its mask
rise has MEASURED something, and may write it down. So this checker targets the
spec, its thresholds, its asset ids, and the sim-only APIs that expose them.

`check_success` is banned outright rather than pattern-matched. Sentences that
merely defer to it ("success is decided by the sim") are fine in substance but
indistinguishable, cheaply, from ones that state it — and every real leak found
so far shipped with a self-issued exemption ("DIAGNOSIS ONLY", "不喂 GT 位姿",
"此为感知核验非真值"). A rule that asks the author to judge their own case does
not hold. Say "the simulator decides" without naming the function.

Usage:  python scripts/check_gt_leak.py [--fix-report out.json]
Exit 0 clean, 1 on any leak.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "roborsi/embodied/skills"

sys.path.insert(0, str(REPO))
from roborsi.agents.gt_firewall import (  # noqa: E402
    GT_PATTERNS, fingerprints as _fingerprints, predicate_source as _pred_src,
)


def env_dir() -> Path | None:
    root = os.environ.get("ROBORSI_ROBOTWIN_ROOT")
    if not root:
        return None
    d = Path(root) / "envs"
    return d if d.is_dir() else None


def predicate_source(_envs, task: str) -> str | None:
    """Adapter: the firewall reads ROBORSI_ROBOTWIN_ROOT itself."""
    return _pred_src(task)


def fingerprints(pred: str) -> tuple[set[str], set[str]]:
    """Adapter over the firewall's single fingerprint set, split for reporting."""
    marks = _fingerprints(pred)
    return {m for m in marks if m[0].isdigit() and "." in m}, {m for m in marks if "_" in m}


def agent_readable(ns: str = "robotwin") -> list[Path]:
    """Files whose contents reach a blind role's context, for one namespace.

    Two surfaces, per prompt_tools: the Engineer's tool block is built from
    base/<tool>/<ns>/SKILL.md minus _ENGINEER_HIDDEN_TOOLS, and the Planner is
    handed the task wiki. `_lib/` is harness plumbing that no role sees, and a
    hidden tool's own spec is never rendered — scanning either only produces
    noise that trains people to ignore the check.

    The namespace filter keeps each backend audit focused on the skills that can
    reach that backend's Engineer prompt.
    """
    sys.path.insert(0, str(REPO))
    from roborsi.embodied.agent_loop.prompt_tools import _ENGINEER_HIDDEN_TOOLS

    out = list(SKILLS.glob("atomic/*/*/wiki.md"))
    for p in SKILLS.glob(f"base/*/{ns}/SKILL.md"):
        if p.parent.parent.name not in _ENGINEER_HIDDEN_TOOLS:
            out.append(p)
    # Solidified compounds are Engineer-callable, so their specs render too.
    for p in SKILLS.glob("atomic/*/*/SKILL.md"):
        if (p.parent / "policy.py").is_file() and \
                "def dispatch_runtime" in (p.parent / "policy.py").read_text(errors="ignore"):
            out.append(p)
    return sorted(set(out))


def task_of(path: Path) -> str:
    """skills/<tier>/<task>/<variant>/<file> -> task."""
    return path.parent.parent.name


def scan(ns: str = "robotwin") -> list[dict]:
    envs = env_dir()
    findings: list[dict] = []
    for path in agent_readable(ns):
        text = path.read_text(errors="ignore")
        lines = text.splitlines()
        pred = predicate_source(envs, task_of(path)) if envs else None
        nums, assets = fingerprints(pred) if pred else (set(), set())

        for i, line in enumerate(lines, 1):
            for pat in GT_PATTERNS:
                m = pat.search(line)
                if m:
                    findings.append({"file": str(path.relative_to(REPO)), "line": i,
                                     "kind": "sim-only vocabulary", "token": m.group(0),
                                     "text": line.strip()[:160]})
            for n in nums:
                if n in line:
                    findings.append({"file": str(path.relative_to(REPO)), "line": i,
                                     "kind": "predicate threshold", "token": n,
                                     "text": line.strip()[:160]})
            for a in assets:
                if a in line:
                    findings.append({"file": str(path.relative_to(REPO)), "line": i,
                                     "kind": "predicate asset id", "token": a,
                                     "text": line.strip()[:160]})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix-report", help="write findings as JSON here")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--ns", default="robotwin", help="skill namespace to audit")
    args = ap.parse_args()

    if env_dir() is None:
        print("ROBORSI_ROBOTWIN_ROOT unset or has no envs/ — "
              "threshold/asset checks skipped, API check still runs.",
              file=sys.stderr)

    findings = scan(args.ns)
    if args.fix_report:
        Path(args.fix_report).write_text(json.dumps(findings, indent=2, ensure_ascii=False))

    if not findings:
        print("GT leak check: clean")
        return 0

    by_file: dict[str, list[dict]] = {}
    for f in findings:
        by_file.setdefault(f["file"], []).append(f)
    print(f"GT leak check: {len(findings)} finding(s) in {len(by_file)} file(s)\n")
    if not args.quiet:
        for fn, fs in sorted(by_file.items()):
            print(f"  {fn}")
            for f in fs:
                print(f"    L{f['line']:<5} {f['kind']:<20} {f['token']}")
                print(f"          {f['text']}")
    print("\nThe success criterion is a spec, not an observation. Remove it; keep "
          "what the robot could have measured itself.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
