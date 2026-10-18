"""Episode-level simulator gate for a skill change without its own harness.

Applies the candidate in a throw-away git worktree at HEAD and runs frozen
RoboRSI episodes on the source task with development seeds that are disjoint
from the evaluation seeds. The candidate passes when it succeeds at least once and does at least as well as the current skill (source_task_gate).
Nothing is published here; the Manager applies the change afterwards.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

R = Path(__file__).resolve().parents[3]
DEV_SEEDS = [int(s) for s in os.environ.get("ROBORSI_GATE_SEEDS", "21,22,23").split(",")]
MIN_PASS = int(os.environ.get("ROBORSI_GATE_MIN_PASS", str(len(DEV_SEEDS))))


@dataclass
class EpisodeGateReport:
    overall_pass: bool
    note: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"overall_pass": self.overall_pass, "note": self.note,
                "details": self.details, "kind": "episode_gate"}


def _write_candidate(tree: Path, q: dict, task_key: str | None = None) -> None:
    name, code = str(q.get("name", "")), str(q.get("new_code", ""))
    if not name or not code:
        raise ValueError("candidate has no skill name or code")
    target = tree / "roborsi/embodied/skills/base" / name / "libero"
    if task_key:
        # Episodes of this task load its task-local revision first; put the
        # candidate there, or the gate would run the old revision.
        local = (tree / "roborsi/embodied/skills/task_local" / task_key.replace("/", "__")
                 / "base" / name / "libero")
        if local.exists():
            target = local
    target.mkdir(parents=True, exist_ok=True)
    (target / "policy.py").write_text(code, encoding="utf-8")
    if q.get("skill_md"):
        (target / "SKILL.md").write_text(str(q["skill_md"]), encoding="utf-8")
    if not (target / "SKILL.md").exists():
        raise ValueError("a new skill needs a SKILL.md")


def run(q: dict, task_key: str | None, *, apply_candidate: bool = True,
        min_pass: int | None = None,
        seeds: list[int] | None = None) -> EpisodeGateReport:
    """Run the gate episodes; apply_candidate=False measures the current
    shared library on the same task and seeds (the baseline)."""
    seeds = list(seeds or DEV_SEEDS)
    need = MIN_PASS if min_pass is None else min_pass
    if not task_key:
        return EpisodeGateReport(False, "no source task to validate on")
    tmp = Path(tempfile.mkdtemp(prefix="roborsi-gate-"))
    tree = tmp / "tree"
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(tree), "HEAD"],
                       cwd=R, check=True, capture_output=True)
        if apply_candidate:
            _write_candidate(tree, q, task_key)
        # Eval mode needs a clean tree; commit the candidate in the throw-away worktree.
        if apply_candidate:
            subprocess.run(["git", "add", "-A"], cwd=tree, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=RoboRSI Gate", "-c", "user.email=gate@localhost",
                            "commit", "-q", "-m", "gate candidate"], cwd=tree, check=True,
                           capture_output=True)
        out = tmp / "campaign"
        model = os.environ.get("ROBORSI_EVAL_MODEL") or os.environ.get(
            "ROBORSI_MANAGER_MODEL", "gpt-5.6-sol")
        env = {**os.environ, "PYTHONPATH": str(tree), "ROBORSI_RUN_MODE": "frozen",
               # Gate episodes must not touch the campaign's role sessions.
               "ROBORSI_ROLE_SESSION": "0"}
        cmd = [sys.executable, "-c",
               "import sys; from roborsi.cli.commands import app; "
               "sys.argv[0] = 'roborsi'; app()", "eval-suite",
               "--backend", os.environ.get("ROBORSI_HARNESS_BACKEND", "libero-pro"),
               "--task", task_key, "--seed-start", str(seeds[0]),
               "--pass-at", str(len(seeds)), "--workers", str(len(seeds)),
               "--tool-budget", os.environ.get("ROBORSI_GATE_TOOL_BUDGET", "80"),
               "--run-mode", "frozen", "--run-all-seeds", "--planner-model", model,
               "--engineer-model", model, "--reviewer-model", model,
               "--out", str(out)]
        proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True,
                              text=True, timeout=int(os.environ.get(
                                  "ROBORSI_GATE_TIMEOUT_S", "7200")))
        rows = []
        journal = out / "episodes.jsonl"
        if journal.exists():
            rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        verdicts = {}
        for row in rows:
            if row.get("verdict") in ("success", "failure"):
                verdicts[int(row["seed"])] = row["verdict"]
        passes = sum(v == "success" for v in verdicts.values())
        ok = passes >= need and len(verdicts) == len(seeds)
        return EpisodeGateReport(
            ok, f"episode gate {passes}/{len(seeds)} on {task_key} "
                f"seeds {seeds} (need {need})",
            {"verdicts": verdicts, "passes": passes, "returncode": proc.returncode})
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                       cwd=R, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def source_task_gate(q: dict, task_key: str | None) -> EpisodeGateReport:
    """Gate for installing a skill change on its source task: on the same
    development seeds the candidate must succeed at least once and do at
    least as well as the current skill. A real fix is not blocked by seeds
    the task fails for other reasons; a change that makes things worse is."""
    seeds = DEV_SEEDS
    cand = run(q, task_key, min_pass=0, seeds=seeds)
    base = run(q, task_key, apply_candidate=False, min_pass=0, seeds=seeds)
    c, b = cand.details.get("passes"), base.details.get("passes")
    rows = {"candidate": c, "baseline": b, "seeds": seeds,
            "candidate_verdicts": cand.details.get("verdicts"),
            "baseline_verdicts": base.details.get("verdicts")}
    complete = (len(cand.details.get("verdicts") or {}) == len(seeds)
                and len(base.details.get("verdicts") or {}) == len(seeds))
    ok = complete and c is not None and b is not None and c >= max(1, b)
    return EpisodeGateReport(ok, f"source-task gate on {task_key} seeds {seeds}: "
                                 f"candidate {c}, current {b}", rows)


def tasks_using_skill(skill: str, exclude: str | None, limit: int) -> list[str]:
    """Other tasks whose recorded episodes called ``skill``, most recent first."""
    import glob
    from roborsi.embodied.paths import home

    found: list[str] = []
    for ws in sorted(glob.glob(str(home() / "workspaces" / "*")), reverse=True):
        ident = Path(ws) / "episode_identity.json"
        if not ident.exists():
            continue
        try:
            key = json.loads(ident.read_text()).get("task_key")
        except ValueError:
            continue
        if not key or key == exclude or key in found:
            continue
        for trace in glob.glob(f"{ws}/rollout/round_*/*/trace.json"):
            try:
                steps = json.loads(Path(trace).read_text())
            except ValueError:
                continue
            if any((s.get("tool_call") or {}).get("tool") == skill for s in steps):
                found.append(key)
                break
        if len(found) >= limit:
            break
    return found


def cross_task_gate(q: dict, source_task: str | None) -> EpisodeGateReport:
    """Promotion check for a shared-skill change: on other tasks that use the
    skill, the candidate must do at least as well as the current shared skill
    on the same development seeds."""
    n = int(os.environ.get("ROBORSI_GLOBAL_GATE_TASKS", "2"))
    others = tasks_using_skill(str(q.get("name", "")), source_task, n)
    if len(others) < n:
        return EpisodeGateReport(False, f"only {len(others)} other tasks used this skill (need {n})",
                                 {"tasks": others})
    rows = {}
    for task in others:
        cand = run(q, task, min_pass=0)
        base = run(q, task, apply_candidate=False, min_pass=0)
        rows[task] = {"candidate": cand.details.get("passes"), "baseline": base.details.get("passes")}
        if cand.details.get("passes") is None or base.details.get("passes") is None \
                or cand.details["passes"] < base.details["passes"]:
            return EpisodeGateReport(False, f"regression on {task}", rows)
    return EpisodeGateReport(True, f"no regression on {len(others)} other tasks", rows)
