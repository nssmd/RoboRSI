"""Resumable task-level pass@K evaluation for LIBERO short suites."""

from __future__ import annotations

import concurrent.futures as cf
import importlib.util
import json
import multiprocessing as mp
import os
import platform
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import evals_root
from roborsi.evaluation.atomic import (
    classify_attempt_exception,
    run_atomic_attempt,
)

_SHORT_TASK = re.compile(
    r"^libero_(spatial|object|goal)(?:_(task|object|swap|lan))?/(\d+)$"
)


def select_libero_short_tasks(
    backend_name: str,
    requested: list[str] | None = None,
) -> list[str]:
    """Return a stable LIBERO short task list, excluding LIBERO-10/long."""
    from roborsi.embodied.agent_loop import get_backend

    backend = get_backend(backend_name)
    ok, reason = backend.available()
    if not ok:
        raise RuntimeError(f"backend '{backend_name}' unavailable: {reason}")
    available = sorted(task for task in backend.list_tasks() if _SHORT_TASK.match(task))
    if requested:
        requested_clean = [str(task).strip() for task in requested if str(task).strip()]
        unknown = [task for task in requested_clean if task not in available]
        if unknown:
            raise ValueError(f"unknown LIBERO short task(s): {', '.join(unknown)}")
        return requested_clean
    return available


def run_libero_short_suite(
    *,
    backend: str = "libero-pro",
    atomic: str = "libero_pick_place",
    seeds: int = 5,
    seed_start: int = 0,
    workers: int = 4,
    tool_budget: int = 40,
    tasks: list[str] | None = None,
    out_dir: Path | None = None,
    infra_retries: int = 2,
    planner_model: str | None = None,
    engineer_model: str | None = None,
    reviewer_model: str | None = None,
    progress=None,
) -> dict[str, Any]:
    """Run task-level pass@K with exact journal resume and success protection."""
    if seeds < 1:
        raise ValueError("seeds must be >= 1")
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if infra_retries < 0:
        raise ValueError("infra_retries must be >= 0")

    task_keys = select_libero_short_tasks(backend, tasks)
    if not task_keys:
        raise RuntimeError(f"backend '{backend}' exposes no LIBERO short tasks")
    planner_model, engineer_model, reviewer_model = _effective_models(
        planner_model,
        engineer_model,
        reviewer_model,
    )
    runtime = _runtime_fingerprint(backend)
    if runtime.get("roborsi_dirty"):
        raise RuntimeError(
            "eval-suite requires a clean RoboRSI worktree so one campaign "
            "cannot mix different source revisions"
        )

    started_at = datetime.now(timezone.utc)
    generated_id = (
        f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-"
        f"libero-short-pass{seeds}-{uuid.uuid4().hex[:8]}"
    )
    root = Path(out_dir).expanduser().resolve() if out_dir else (
        evals_root() / "suites" / generated_id
    )
    root.mkdir(parents=True, exist_ok=True)
    campaign_path = root / "campaign.json"
    journal = root / "episodes.jsonl"
    campaign = _load_or_create_campaign(
        path=campaign_path,
        generated_id=generated_id,
        backend=backend,
        atomic=atomic,
        task_keys=task_keys,
        seeds=seeds,
        seed_start=seed_start,
        workers=workers,
        tool_budget=tool_budget,
        infra_retries=infra_retries,
        planner_model=planner_model,
        engineer_model=engineer_model,
        reviewer_model=reviewer_model,
        journal=journal,
        created_at=started_at,
        runtime=runtime,
    )
    campaign_id = str(campaign["campaign_id"])
    existing = _load_journal(journal)
    terminal_by_key = {
        (row["task_key"], int(row["seed"])): row
        for row in existing
        if row.get("verdict") in {"success", "failure"}
    }
    attempts_by_key: dict[tuple[str, int], int] = {}
    for row in existing:
        key = (str(row.get("task_key")), int(row.get("seed", -1)))
        attempts_by_key[key] = max(
            attempts_by_key.get(key, 0),
            int(row.get("attempt", 1)),
        )
    solved = {
        str(row["task_key"])
        for row in terminal_by_key.values()
        if row.get("verdict") == "success"
    }
    rows = list(existing)

    executor: cf.Executor
    if workers == 1:
        executor = cf.ThreadPoolExecutor(max_workers=1)
    else:
        executor = cf.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
        )
    try:
        for seed in range(seed_start, seed_start + seeds):
            pending = [
                task_key
                for task_key in task_keys
                if task_key not in solved
                and (task_key, seed) not in terminal_by_key
            ]
            futures = {}
            for task_key in pending:
                key = (task_key, seed)
                payload = {
                    "task_key": task_key,
                    "atomic": atomic,
                    "backend": backend,
                    "seed": seed,
                    "tool_budget": tool_budget,
                    "infra_retries": infra_retries,
                    "attempt_start": attempts_by_key.get(key, 0) + 1,
                    "planner_model": planner_model,
                    "engineer_model": engineer_model,
                    "reviewer_model": reviewer_model,
                }
                futures[executor.submit(_run_suite_attempt, payload)] = task_key

            completed = 0
            for future in cf.as_completed(futures):
                task_key = futures[future]
                try:
                    attempt_rows = future.result()
                except Exception as exc:
                    attempt_rows = [_parent_worker_error(task_key, seed, exc)]
                for row in attempt_rows:
                    rows.append(row)
                    _append_journal(journal, row)
                    key = (task_key, seed)
                    attempts_by_key[key] = max(
                        attempts_by_key.get(key, 0),
                        int(row.get("attempt", 1)),
                    )
                    if row["verdict"] in {"success", "failure"}:
                        terminal_by_key[key] = row
                    if row["verdict"] == "success":
                        solved.add(task_key)
                completed += 1
                if progress is not None:
                    progress(
                        task_key,
                        seed,
                        completed,
                        len(pending),
                        attempt_rows[-1],
                        len(solved),
                        len(task_keys),
                    )
    finally:
        executor.shutdown(wait=True)

    summary = _summarize_suite(
        campaign_id=campaign_id,
        root=root,
        backend=backend,
        atomic=atomic,
        task_keys=task_keys,
        rows=rows,
        seeds=seeds,
        seed_start=seed_start,
        workers=workers,
        tool_budget=tool_budget,
        started_at=started_at,
        journal=journal,
        campaign=campaign,
    )
    summary_path = root / "summary.json"
    summary["summary_path"] = str(summary_path)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return summary


def suite_exit_code(summary: dict[str, Any]) -> int:
    return 0 if summary.get("status") == "complete" else 2


def _run_suite_attempt(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    max_attempts = int(payload["infra_retries"]) + 1
    for offset in range(max_attempts):
        attempt = int(payload["attempt_start"]) + offset
        row = run_atomic_attempt(
            task=payload["atomic"],
            seed=int(payload["seed"]),
            mode="eval",
            tool_budget=int(payload["tool_budget"]),
            backend=payload["backend"],
            sim_task=payload["task_key"],
            planner_model=payload.get("planner_model"),
            engineer_model=payload.get("engineer_model"),
            reviewer_model=payload.get("reviewer_model"),
        )
        row["task_key"] = payload["task_key"]
        row["attempt"] = attempt
        rows.append(row)
        if row["verdict"] != "infra":
            break
    return rows


def _parent_worker_error(task_key: str, seed: int, exc: Exception) -> dict[str, Any]:
    verdict = classify_attempt_exception(exc)
    return {
        "task": "libero_pick_place",
        "task_key": task_key,
        "sim_task": task_key,
        "backend": None,
        "seed": seed,
        "attempt": 1,
        "run_mode": "eval",
        "success": None,
        "verdict": verdict,
        "status": "incomplete",
        "outcome": verdict,
        "tool_calls": 0,
        "error": f"{type(exc).__name__}: {exc}",
    }


def _load_journal(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"journal parse failed at line {line_no}: {path}") from exc
        if not isinstance(row, dict) or "task_key" not in row or "seed" not in row:
            raise ValueError(f"journal row {line_no} is missing task_key/seed")
        rows.append(row)
    _assert_no_terminal_conflicts(rows)
    return rows


def _load_or_create_campaign(
    *,
    path: Path,
    generated_id: str,
    backend: str,
    atomic: str,
    task_keys: list[str],
    seeds: int,
    seed_start: int,
    workers: int,
    tool_budget: int,
    infra_retries: int,
    planner_model: str | None,
    engineer_model: str | None,
    reviewer_model: str | None,
    journal: Path,
    created_at: datetime,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    requested = {
        "schema": "roborsi.libero_short_campaign.v1",
        "backend": backend,
        "atomic": atomic,
        "task_keys": task_keys,
        "pass_at": seeds,
        "seed_start": seed_start,
        "workers": workers,
        "tool_budget": tool_budget,
        "infra_retries": infra_retries,
        "models": {
            "planner": planner_model,
            "engineer": engineer_model,
            "reviewer": reviewer_model,
        },
        "runtime": runtime,
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"campaign manifest is not valid JSON: {path}") from exc
        mismatches = [
            key
            for key, value in requested.items()
            if existing.get(key) != value
        ]
        if mismatches:
            raise ValueError(
                "resume configuration differs from campaign manifest: "
                + ", ".join(mismatches)
            )
        if not existing.get("campaign_id"):
            raise ValueError(f"campaign manifest has no campaign_id: {path}")
        return existing
    if journal.exists() and journal.stat().st_size:
        raise ValueError(
            f"cannot resume journal without its exact campaign manifest: {journal}"
        )
    campaign = {
        **requested,
        "campaign_id": generated_id,
        "created_at": created_at.isoformat(),
    }
    path.write_text(
        json.dumps(campaign, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return campaign


def _assert_no_terminal_conflicts(rows: list[dict[str, Any]]) -> None:
    terminal: dict[tuple[str, int], tuple[str, bool]] = {}
    for row in rows:
        if row.get("verdict") not in {"success", "failure"}:
            continue
        key = (str(row["task_key"]), int(row["seed"]))
        signature = (str(row["verdict"]), bool(row.get("success")))
        prior = terminal.get(key)
        if prior is not None and prior != signature:
            raise ValueError(
                f"journal conflict for task={key[0]} seed={key[1]}: "
                f"{prior} versus {signature}"
            )
        terminal[key] = signature


def _append_journal(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _summarize_suite(
    *,
    campaign_id: str,
    root: Path,
    backend: str,
    atomic: str,
    task_keys: list[str],
    rows: list[dict[str, Any]],
    seeds: int,
    seed_start: int,
    workers: int,
    tool_budget: int,
    started_at: datetime,
    journal: Path,
    campaign: dict[str, Any],
) -> dict[str, Any]:
    terminal: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["task_key"]), int(row["seed"]))
        if row["verdict"] in {"success", "failure"}:
            terminal[key] = row

    per_task = []
    solved_tasks = 0
    incomplete_tasks = 0
    for task_key in task_keys:
        task_rows = [
            terminal[(task_key, seed)]
            for seed in range(seed_start, seed_start + seeds)
            if (task_key, seed) in terminal
        ]
        success_seed = next(
            (int(row["seed"]) for row in task_rows if row["verdict"] == "success"),
            None,
        )
        solved = success_seed is not None
        solved_tasks += int(solved)
        complete = solved or len(task_rows) == seeds
        if not complete:
            incomplete_tasks += 1
        per_task.append({
            "task_key": task_key,
            "solved": solved,
            "success_seed": success_seed,
            "terminal_seeds": len(task_rows),
            "complete": complete,
        })

    valid_rows = list(terminal.values())
    infra = sum(1 for row in rows if row["verdict"] == "infra")
    implementation_errors = sum(
        1 for row in rows if row["verdict"] == "implementation_error"
    )
    latest_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        latest_by_key[(str(row["task_key"]), int(row["seed"]))] = row
    unresolved_implementation_errors = sum(
        1
        for key, row in latest_by_key.items()
        if key not in terminal and row["verdict"] == "implementation_error"
    )
    finished_at = datetime.now(timezone.utc)
    return {
        "schema": "roborsi.libero_short_eval.v1",
        "campaign_id": campaign_id,
        "status": "complete" if incomplete_tasks == 0 else "incomplete",
        "run_mode": "eval",
        "frozen": True,
        "backend": backend,
        "atomic": atomic,
        "pass_at": seeds,
        "seed_start": seed_start,
        "workers": workers,
        "tool_budget": tool_budget,
        "tasks_total": len(task_keys),
        "tasks_solved": solved_tasks,
        "task_success_rate": solved_tasks / len(task_keys),
        "incomplete_tasks": incomplete_tasks,
        "episode_successes": sum(
            1 for row in valid_rows if row["verdict"] == "success"
        ),
        "episode_failures": sum(
            1 for row in valid_rows if row["verdict"] == "failure"
        ),
        "infra_count": infra,
        "implementation_error_count": implementation_errors,
        "unresolved_implementation_error_count": (
            unresolved_implementation_errors
        ),
        "subset": _breakdown(per_task, index=1),
        "suite": _breakdown(per_task, index=2),
        "per_task": per_task,
        "runtime": _runtime_fingerprint(backend),
        "root": str(root),
        "campaign_manifest_path": str(root / "campaign.json"),
        "journal_path": str(journal),
        "campaign_created_at": campaign["created_at"],
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "wallclock_s": (finished_at - started_at).total_seconds(),
    }


def _breakdown(per_task: list[dict[str, Any]], *, index: int) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for row in per_task:
        match = _SHORT_TASK.match(row["task_key"])
        if match is None:
            continue
        label = match.group(index) or "base"
        bucket = out.setdefault(label, {"solved": 0, "total": 0})
        bucket["total"] += 1
        bucket["solved"] += int(row["solved"])
    return out


def _runtime_fingerprint(backend: str) -> dict[str, Any]:
    repo = Path(__file__).resolve().parents[2]
    result = {
        "roborsi_commit": _git_rev(repo),
        "roborsi_dirty": _git_dirty(repo),
        "backend": backend,
        "libero_suites": os.environ.get("ROBORSI_LIBERO_SUITES", ""),
        "libero_initdir": os.environ.get("ROBORSI_LIBERO_INITDIR", ""),
        "perception_model": os.environ.get("ROBORSI_PERCEPTION_MODEL", ""),
        "python": platform.python_version(),
        "platform": sys.platform,
    }
    spec = importlib.util.find_spec("libero")
    if spec is not None and spec.origin:
        path = Path(spec.origin).resolve()
        result["libero_module"] = str(path)
        result["libero_commit"] = _nearest_git_rev(path)
        git_root = _nearest_git_root(path)
        result["libero_dirty"] = _git_dirty(git_root) if git_root else None
    return result


def _nearest_git_rev(path: Path) -> str | None:
    root = _nearest_git_root(path)
    return _git_rev(root) if root else None


def _nearest_git_root(path: Path) -> Path | None:
    return next(
        (parent for parent in (path, *path.parents) if (parent / ".git").exists()),
        None,
    )


def _git_rev(root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None


def _git_dirty(root: Path) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(proc.stdout.strip())


def _effective_models(
    planner_model: str | None,
    engineer_model: str | None,
    reviewer_model: str | None,
) -> tuple[str, str, str]:
    from roborsi.agents import Engineer, Planner, Reviewer

    return (
        planner_model or Planner.DEFAULT_MODEL,
        engineer_model or Engineer.DEFAULT_MODEL,
        reviewer_model or Reviewer.DEFAULT_MODEL,
    )
