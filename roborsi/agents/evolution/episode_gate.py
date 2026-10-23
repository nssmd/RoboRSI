"""Episode-level simulator gate for a skill change without its own harness.

Applies the candidate in a throw-away git worktree at HEAD and runs frozen
RoboRSI episodes on the source task with development seeds that are disjoint
from the evaluation seeds. The candidate passes when enough episodes succeed.
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
DEV_SEEDS = [int(s) for s in os.environ.get("ROBORSI_GATE_SEEDS", "21,22").split(",")]
MIN_PASS = int(os.environ.get("ROBORSI_GATE_MIN_PASS", str(len(DEV_SEEDS))))


@dataclass
class EpisodeGateReport:
    overall_pass: bool
    note: str
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"overall_pass": self.overall_pass, "note": self.note,
                "details": self.details, "kind": "episode_gate"}


def _write_candidate(tree: Path, q: dict) -> None:
    name, code = str(q.get("name", "")), str(q.get("new_code", ""))
    if not name or not code:
        raise ValueError("candidate has no skill name or code")
    target = tree / "roborsi/embodied/skills/base" / name / "libero"
    target.mkdir(parents=True, exist_ok=True)
    (target / "policy.py").write_text(code, encoding="utf-8")
    if q.get("skill_md"):
        (target / "SKILL.md").write_text(str(q["skill_md"]), encoding="utf-8")
    if not (target / "SKILL.md").exists():
        raise ValueError("a new skill needs a SKILL.md")


def run(q: dict, task_key: str | None, *, apply_candidate: bool = True,
        min_pass: int | None = None) -> EpisodeGateReport:
    """Run the gate episodes; apply_candidate=False measures the current
    shared library on the same task and seeds (the baseline)."""
    need = MIN_PASS if min_pass is None else min_pass
    if not task_key:
        return EpisodeGateReport(False, "no source task to validate on")
    from roborsi.embodied.agent_loop.vlm_io import ProviderError, is_compatible_model, resolve_api_config
    # eval-suite accepts a start/count, not an arbitrary list. Never silently
    # measure different seeds from the ones advertised by this gate.
    if not DEV_SEEDS or DEV_SEEDS != list(range(DEV_SEEDS[0], DEV_SEEDS[0] + len(DEV_SEEDS))):
        raise ProviderError("ROBORSI_GATE_SEEDS must be a nonempty consecutive increasing list",
                            category="configuration")
    if not 0 <= need <= len(DEV_SEEDS):
        raise ProviderError("Gate min_pass must be between 0 and the number of seeds",
                            category="configuration")
    model = os.environ.get("ROBORSI_GATE_MODEL") or os.environ.get(
        "ROBORSI_EVAL_MODEL") or os.environ.get("ROBORSI_MANAGER_MODEL", "gpt-5.6-sol")
    if (is_compatible_model(os.environ.get("ROBORSI_ENGINEER_MODEL"))
            and not os.environ.get("ROBORSI_GATE_MODEL")):
        raise ProviderError("Set ROBORSI_GATE_MODEL explicitly for adaptive DeepSeek/Kimi runs; gate roles use APIs",
                            category="configuration")
    api_config = resolve_api_config(model).public() if is_compatible_model(model) else None
    tmp = Path(tempfile.mkdtemp(prefix="roborsi-gate-"))
    tree = tmp / "tree"
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(tree), "HEAD"],
                       cwd=R, check=True, capture_output=True)
        api_source = tree / "roborsi/embodied/agent_loop/vlm_io.py"
        if api_config and (not api_source.is_file() or
                           "def _chat_call_with_tools(" not in api_source.read_text(encoding="utf-8")):
            raise ProviderError("Episode gate uses committed HEAD; commit the API changes before running skill evolution",
                                category="configuration")
        if apply_candidate:
            _write_candidate(tree, q)
        # Eval mode needs a clean tree; commit the candidate in the throw-away worktree.
        if apply_candidate:
            subprocess.run(["git", "add", "-A"], cwd=tree, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=RoboRSI Gate", "-c", "user.email=gate@localhost",
                            "commit", "-q", "-m", "gate candidate"], cwd=tree, check=True,
                           capture_output=True)
        out = tmp / "campaign"
        env = {**os.environ, "PYTHONPATH": str(tree), "ROBORSI_RUN_MODE": "frozen",
               # Gate episodes must not touch the campaign's role sessions.
               "ROBORSI_ROLE_SESSION": "0"}
        cmd = [sys.executable, "-c",
               "import sys; from roborsi.cli.commands import app; "
               "sys.argv[0] = 'roborsi'; app()", "eval-suite",
               "--backend", os.environ.get("ROBORSI_HARNESS_BACKEND", "libero-pro"),
               "--task", task_key, "--seed-start", str(DEV_SEEDS[0]),
               "--pass-at", str(len(DEV_SEEDS)), "--workers", str(len(DEV_SEEDS)),
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
        if set(verdicts) != set(DEV_SEEDS) or proc.returncode != 0:
            return EpisodeGateReport(False, "Episode gate incomplete; not a measured skill failure",
                                     {"verdicts": verdicts, "passes": None,
                                      "observed_passes": passes, "returncode": proc.returncode,
                                      "complete": False, "expected_seeds": DEV_SEEDS,
                                      "model": model, "api_config": api_config})
        ok = passes >= need
        return EpisodeGateReport(
            ok, f"episode gate {passes}/{len(DEV_SEEDS)} on {task_key} "
                f"seeds {DEV_SEEDS} (need {need})",
            {"verdicts": verdicts, "passes": passes, "returncode": proc.returncode,
             "complete": True, "expected_seeds": DEV_SEEDS,
             "model": model, "api_config": api_config})
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)],
                       cwd=R, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


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
        if (not cand.overall_pass or not base.overall_pass
                or cand.details.get("passes") is None or base.details.get("passes") is None):
            return EpisodeGateReport(False, f"incomplete measurement on {task}", rows)
        if cand.details["passes"] < base.details["passes"]:
            return EpisodeGateReport(False, f"regression on {task}", rows)
    return EpisodeGateReport(True, f"no regression on {len(others)} other tasks", rows)
