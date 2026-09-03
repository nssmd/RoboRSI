"""One authoritative atomic evaluation path.

Both ``roborsi eval`` and ``roborsi bench skill`` call this module so they
cannot silently measure different execution stacks.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import evals_root, home
from roborsi.runtime_mode import RunMode, parse_mode, use_run_mode

_INFRA_EXCEPTION_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectTimeout",
    "ConnectionError",
    "ReadError",
    "ReadTimeout",
    "RemoteProtocolError",
    "TimeoutError",
}
_INFRA_MESSAGE_MARKERS = (
    "backend unavailable",
    "connection refused",
    "connection reset",
    "cuda out of memory",
    "egl",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "model not found",
    "no space left on device",
    "provider unavailable",
    "rate limit",
    "resource temporarily unavailable",
    "timed out",
    "timeout",
    "transport",
)


def classify_attempt_exception(exc: Exception) -> str:
    """Separate infrastructure interruption from implementation defects."""
    if type(exc).__name__ in _INFRA_EXCEPTION_NAMES:
        return "infra"
    message = str(exc).lower()
    if any(marker in message for marker in _INFRA_MESSAGE_MARKERS):
        return "infra"
    return "implementation_error"


def run_atomic_attempt(
    *,
    task: str,
    seed: int,
    mode: str | RunMode = RunMode.EVAL,
    tool_budget: int = 40,
    backend: str | None = None,
    sim_task: str | None = None,
    planner_model: str | None = None,
    engineer_model: str | None = None,
    reviewer_model: str | None = None,
    reasoning_effort: str | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Run one Planner -> Engineer -> Reviewer attempt and classify its result."""
    from roborsi.channels.agent.feishu.live_trace import get_session
    from roborsi.channels.core.agent import _run_atomic_3role

    parsed_mode = parse_mode(mode)
    chat_id = chat_id or (
        f"{parsed_mode.value}-{task.replace('/', '-')}-{seed}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    sess = get_session(chat_id)
    sess.set_busy(True)
    capability_text = (
        "the frozen released capability set"
        if parsed_mode is RunMode.EVAL
        else "evolution mode"
    )
    request = (
        f"Evaluate atomic task {task} at seed={seed} using "
        f"{capability_text}."
    )
    if sim_task:
        request += f" Simulator task key: {sim_task}."
    sess.last_user_message = request

    with use_run_mode(parsed_mode), _reasoning_effort(reasoning_effort):
        try:
            details = _run_atomic_3role(
                text=request,
                atomic=task,
                seed=seed,
                sess=sess,
                target_chat_id=chat_id,
                channel=None,
                ctx=None,
                tool_budget=tool_budget,
                backend_name=backend,
                sim_task=sim_task,
                planner_model=planner_model,
                engineer_model=engineer_model,
                reviewer_model=reviewer_model,
                return_details=True,
            )
            if not isinstance(details, dict):
                raise TypeError("atomic runner returned a non-dict result")
            details["verdict"] = "success" if details["success"] else "failure"
            details["status"] = "terminal"
            details["reasoning_effort"] = reasoning_effort
            sess.append(
                "done",
                final_text=details["text"],
                run_mode=parsed_mode.value,
            )
            return details
        except Exception as exc:
            verdict = classify_attempt_exception(exc)
            error = f"{type(exc).__name__}: {exc}"
            row = {
                "text": error,
                "run_id": None,
                "workspace": None,
                "task": task,
                "backend": backend,
                "sim_task": sim_task,
                "seed": seed,
                "run_mode": parsed_mode.value,
                "success": None,
                "verdict": verdict,
                "status": "incomplete",
                "outcome": verdict,
                "tool_calls": 0,
                "reviewer_verdict": None,
                "proposal_decision": "NO_PROPOSAL",
                "reasoning_effort": reasoning_effort,
                "video_path": None,
                "error": error,
            }
            sess.append(
                "eval_attempt_error",
                seed=seed,
                error=error,
                category=verdict,
                run_mode=parsed_mode.value,
            )
            sess.append("done", final_text=error, run_mode=parsed_mode.value)
            return row
        finally:
            sess.set_busy(False)


def run_atomic_campaign(
    *,
    task: str,
    seeds: int,
    seed_start: int = 0,
    mode: str | RunMode = RunMode.EVAL,
    tool_budget: int = 40,
    backend: str | None = None,
    sim_task: str | None = None,
    planner_model: str | None = None,
    engineer_model: str | None = None,
    reviewer_model: str | None = None,
    reasoning_effort: str | None = None,
    persist_manifest: bool = True,
    progress=None,
) -> dict[str, Any]:
    """Run a sequential atomic campaign and return one canonical summary."""
    if seeds < 1:
        raise ValueError("seeds must be >= 1")
    parsed_mode = parse_mode(mode)
    started_at = datetime.now(timezone.utc)
    campaign_id = (
        f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"{_slug(task)}-{uuid.uuid4().hex[:8]}"
    )
    rows: list[dict[str, Any]] = []
    for offset in range(seeds):
        seed = seed_start + offset
        if progress is not None:
            progress(task, seed, offset + 1, seeds)
        rows.append(run_atomic_attempt(
            task=task,
            seed=seed,
            mode=parsed_mode,
            tool_budget=tool_budget,
            backend=backend,
            sim_task=sim_task,
            planner_model=planner_model,
            engineer_model=engineer_model,
            reviewer_model=reviewer_model,
            reasoning_effort=reasoning_effort,
        ))

    summary = summarize_campaign(
        campaign_id=campaign_id,
        task=task,
        rows=rows,
        seeds=seeds,
        seed_start=seed_start,
        mode=parsed_mode,
        tool_budget=tool_budget,
        backend=backend,
        sim_task=sim_task,
        reasoning_effort=reasoning_effort,
        started_at=started_at,
    )
    if persist_manifest:
        manifest_root = (
            evals_root() if parsed_mode is RunMode.EVAL
            else home() / "campaigns" / "evolve"
        )
        manifest_dir = manifest_root / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{campaign_id}.json"
        summary["manifest_path"] = str(manifest_path)
        manifest_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return summary


def summarize_campaign(
    *,
    campaign_id: str,
    task: str,
    rows: list[dict[str, Any]],
    seeds: int,
    seed_start: int,
    mode: RunMode,
    tool_budget: int,
    backend: str | None,
    sim_task: str | None,
    reasoning_effort: str | None,
    started_at: datetime,
) -> dict[str, Any]:
    passed = sum(1 for row in rows if row["verdict"] == "success")
    failed = sum(1 for row in rows if row["verdict"] == "failure")
    infra = sum(1 for row in rows if row["verdict"] == "infra")
    implementation_errors = sum(
        1 for row in rows if row["verdict"] == "implementation_error"
    )
    verdict_count = passed + failed
    finished_at = datetime.now(timezone.utc)
    sha, dirty = _git_state()
    return {
        "campaign_id": campaign_id,
        "run_mode": mode.value,
        "frozen": mode is RunMode.EVAL,
        "status": (
            "complete"
            if infra == 0 and implementation_errors == 0
            else "incomplete"
        ),
        "task": task,
        "backend_override": backend,
        "sim_task_override": sim_task,
        "reasoning_effort": reasoning_effort,
        "tool_budget": tool_budget,
        "seeds": seeds,
        "seed_start": seed_start,
        "requested_seeds": seeds,
        "verdict_count": verdict_count,
        "seeds_passed": passed,
        "seeds_failed": failed,
        "infra_count": infra,
        "implementation_error_count": implementation_errors,
        "success_rate": passed / verdict_count if verdict_count else None,
        "commit_sha": sha,
        "git_dirty": dirty,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "wallclock_s": (finished_at - started_at).total_seconds(),
        "runs": rows,
    }


def campaign_exit_code(summary: dict[str, Any]) -> int:
    return 0 if summary.get("status") == "complete" else 2


def _git_state() -> tuple[str, bool]:
    repo = Path(__file__).resolve().parents[2]
    sha_proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return sha_proc.stdout.strip() or "unknown", bool(status_proc.stdout.strip())


@contextmanager
def _reasoning_effort(value: str | None):
    name = "ROBORSI_REASONING_EFFORT"
    previous = os.environ.get(name)
    if value:
        os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "task"
