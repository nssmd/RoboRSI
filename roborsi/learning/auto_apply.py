"""Apply an agent-generated proposal to the repo and verify it.

Lifecycle:
  1. Read proposal row from sqlite (status='pending').
  2. Materialise diff to a tmp .patch file.
  3. ``git apply --check`` — abort if it doesn't apply cleanly.
  4. ``git apply``.
  5. Verify: run the supplied bench (``roborsi bench skill <name>
     --seeds N``). The proposal's `skill` field selects what to bench;
     callers can override.
  6. If post-rate < pre-rate (or post-rate < min_rate): ``git checkout
     -- <touched-files>`` and mark proposal ``status='reverted'``.
     Otherwise ``git add + git commit``, mark ``status='applied'``.

No backward-compat shims. Failures raise; caller decides.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roborsi.store import trace_db as _td


REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str, check: bool = True, cwd: Path | None = None
          ) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True, text=True,
        check=check)


def _diff_touched_files(diff_text: str) -> list[str]:
    """Pull '+++ b/<path>' file list out of a unified diff."""
    out: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/"):].strip()
            if path and path != "/dev/null":
                out.append(path)
    return out


@dataclass
class ApplyResult:
    proposal_id: str
    status: str                      # 'applied' | 'reverted' | 'check_failed'
    pre_rate: float | None = None
    post_rate: float | None = None
    touched: list[str] = None        # type: ignore[assignment]
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status,
            "pre_rate": self.pre_rate,
            "post_rate": self.post_rate,
            "touched": self.touched or [],
            "message": self.message,
        }


def apply_proposal(
    proposal_id: str,
    bench_seeds: int = 5,
    min_rate: float | None = None,
    require_no_regression: bool = True,
    bench_skill: str | None = None,
) -> ApplyResult:
    """Apply one proposal and verify.

    Args:
      proposal_id:           id from proposals table.
      bench_seeds:           seeds to use in the verification bench.
      min_rate:              if set, post-rate must be >= this; else
                             only "no regression vs pre-rate" is enforced.
      require_no_regression: enforce post_rate >= pre_rate.
      bench_skill:           bench this skill name instead of the
                             proposal's stored `skill` field.
    """
    from roborsi.runtime_mode import require_evolution
    require_evolution("applying an extracted skill proposal")
    rows = _td.list_proposals(limit=1000)
    proposal = next((r for r in rows if r["id"] == proposal_id), None)
    if proposal is None:
        raise KeyError(f"proposal {proposal_id!r} not found")
    diff_text = (proposal.get("diff") or "").strip()
    if not diff_text:
        raise ValueError(f"proposal {proposal_id!r} has empty diff")
    skill_to_bench = bench_skill or proposal.get("skill") or ""
    if not skill_to_bench:
        raise ValueError(f"proposal {proposal_id!r} has no skill to bench")

    # 1. pre-bench (current HEAD) — record rate first.
    pre_rate, _ = _quick_bench(skill_to_bench, bench_seeds,
                                  tag=f"pre-{proposal_id}")

    # 2. git apply --check; bail without touching tree if it won't apply.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch",
                                       encoding="utf-8",
                                       delete=False) as f:
        f.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")
        patch_path = Path(f.name)
    try:
        check = _git("apply", "--check", str(patch_path), check=False)
        if check.returncode != 0:
            _td.update_proposal_status(proposal_id, "check_failed",
                                          note=check.stderr[:200])
            return ApplyResult(proposal_id=proposal_id,
                                status="check_failed", pre_rate=pre_rate,
                                touched=_diff_touched_files(diff_text),
                                message=check.stderr.strip()[:200])
        _git("apply", str(patch_path))
    finally:
        patch_path.unlink(missing_ok=True)

    touched = _diff_touched_files(diff_text)

    # 3. post-bench (working tree has the patch).
    post_rate, _ = _quick_bench(skill_to_bench, bench_seeds,
                                  tag=f"post-{proposal_id}")

    # 4. accept or revert.
    accept = True
    if min_rate is not None and post_rate < min_rate:
        accept = False
    if require_no_regression and pre_rate is not None and post_rate < pre_rate:
        accept = False

    if accept:
        for fp in touched:
            _git("add", fp, check=False)
        msg = (f"selfevo: apply proposal {proposal_id} "
               f"(rate {pre_rate*100:.0f}%→{post_rate*100:.0f}%)")
        _git("commit", "-m", msg)
        _td.update_proposal_status(proposal_id, "applied",
                                     applied_by="auto_apply",
                                     note=f"{pre_rate:.2f}→{post_rate:.2f}")
        return ApplyResult(proposal_id=proposal_id, status="applied",
                            pre_rate=pre_rate, post_rate=post_rate,
                            touched=touched, message=msg)

    for fp in touched:
        _git("checkout", "--", fp, check=False)
    _td.update_proposal_status(proposal_id, "reverted",
                                 applied_by="auto_apply",
                                 note=f"regression {pre_rate:.2f}→{post_rate:.2f}")
    return ApplyResult(proposal_id=proposal_id, status="reverted",
                        pre_rate=pre_rate, post_rate=post_rate,
                        touched=touched,
                        message=f"reverted: {pre_rate:.2f}→{post_rate:.2f}")


def _quick_bench(skill: str, seeds: int, tag: str = "") -> tuple[float, int]:
    """Run a verification bench. Returns (rate, n_pass). Uses the same
    code path as ``roborsi bench skill`` but writes to a tagged chat_id
    so the events don't pollute interactive monitor sessions."""
    from roborsi.channels.agent.feishu.task_runner import run_task_sync

    task = skill.rsplit(".", 1)[0] if "." in skill else skill
    chat_id = f"auto_apply-{tag or int(time.time())}"
    n_pass = 0
    for i in range(seeds):
        run = run_task_sync(task=task, seed=i, episodes=1,
                              tool_budget=12, skill_name=skill,
                              chat_id=chat_id)
        if run.get("status") == "success":
            n_pass += 1
    return (n_pass / seeds if seeds else 0.0), n_pass
