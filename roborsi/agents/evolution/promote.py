"""Background promotion of a task-scoped skill change to the shared skill.

    python -m roborsi.agents.evolution.promote <job.json>

Runs the cross-task gate (other tasks that use the skill must do at least as
well as with the current shared skill) and, if it passes, installs the change
globally. Results go to manager_events.jsonl."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from roborsi.embodied.paths import home


def _log(kind: str, **kw) -> None:
    with (home() / "manager_events.jsonl").open("a") as f:
        f.write(json.dumps({"utc": time.time(), "kind": kind, **kw}) + "\n")


def main() -> int:
    job = Path(sys.argv[1])
    data = json.loads(job.read_text())
    q, source = data["proposal"], data.get("source_task")
    from roborsi.agents.evolution.episode_gate import EpisodeGateReport, cross_task_gate
    if q.get("kind") == "new":
        # A new skill exists only for its source task, so no other task can
        # have used it yet; promotion just makes it available everywhere.
        report = EpisodeGateReport(True, "new skill: made available to all tasks")
    else:
        report = cross_task_gate(q, source)
    _log("cross_task_gate", proposal=job.name, report=report.to_dict())
    if not report.overall_pass:
        return 0
    repo = Path(__file__).resolve().parents[3]
    r = subprocess.run([os.environ.get("ROBORSI_APPLY_PYTHON", sys.executable), "-m",
                        "roborsi.agents.evolution.apply_proposal", q["id"], "--skip-harness",
                        "--scope", "global"], cwd=repo, capture_output=True, text=True, timeout=180)
    _log("global_promotion", proposal=job.name, returncode=r.returncode,
         output=(r.stdout + r.stderr)[-1000:])
    if r.returncode == 0 and source:
        # The source task's task-local copy would otherwise keep shadowing the
        # shared skill and every later global fix.
        import shutil
        local = (repo / "roborsi/embodied/skills/task_local" / str(source).replace("/", "__")
                 / "base" / str(q.get("name", "")) / "libero")
        if q.get("name") and local.exists():
            shutil.rmtree(local)
            subprocess.run(["git", "add", "-A", str(local.parent.parent.parent)], cwd=repo)
            subprocess.run(["git", "-c", "user.name=RoboRSI Manager", "-c", "user.email=manager@localhost",
                            "commit", "-qm", f"selfevo: drop task-local {q.get('name')} after global promotion"],
                           cwd=repo, capture_output=True)
            _log("task_local_removed", proposal=job.name, path=str(local))
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
