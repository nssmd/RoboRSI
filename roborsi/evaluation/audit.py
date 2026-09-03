"""Independent integrity audit for a LIBERO short-suite campaign."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SHORT_TASK = re.compile(r"^libero_(spatial|object|goal)(?:_(task|object|swap|lan))?/(\d+)$")
_TERMINAL = {"success", "failure"}
_NONTERMINAL = {"infra", "implementation_error"}
_SUMMARY_FIELDS = (
    "status",
    "tasks_total",
    "tasks_solved",
    "incomplete_tasks",
    "episode_successes",
    "episode_failures",
    "infra_count",
    "implementation_error_count",
    "unresolved_implementation_error_count",
    "subset",
    "suite",
)


def audit_libero_short_suite(
    root: Path,
    *,
    check_media: bool = False,
) -> dict[str, Any]:
    """Recompute pass@K and validate a campaign from its append-only journal."""
    root = Path(root).expanduser().resolve()
    campaign_path = root / "campaign.json"
    journal_path = root / "episodes.jsonl"
    summary_path = root / "summary.json"
    errors: list[str] = []
    warnings: list[str] = []

    campaign = _read_object(campaign_path, errors, "campaign")
    rows = _read_jsonl(journal_path, errors)
    if not campaign:
        return _report(
            root=root,
            campaign={},
            rows=rows,
            errors=errors,
            warnings=warnings,
            recomputed={},
            summary_comparison={},
        )

    task_keys = campaign.get("task_keys")
    if not isinstance(task_keys, list) or not all(isinstance(task, str) for task in task_keys):
        errors.append("campaign task_keys must be a list of strings")
        task_keys = []
    if len(task_keys) != len(set(task_keys)):
        errors.append("campaign task_keys contain duplicates")
    invalid_tasks = [task for task in task_keys if _SHORT_TASK.match(task) is None]
    if invalid_tasks:
        errors.append(f"campaign contains non-short task keys: {invalid_tasks}")

    try:
        pass_at = int(campaign["pass_at"])
        seed_start = int(campaign["seed_start"])
    except (KeyError, TypeError, ValueError):
        errors.append("campaign pass_at/seed_start are invalid")
        pass_at = 0
        seed_start = 0
    if pass_at < 1:
        errors.append("campaign pass_at must be at least one")
    expected_seeds = set(range(seed_start, seed_start + max(pass_at, 0)))
    task_set = set(task_keys)

    rows_by_pair: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    terminal_by_pair: dict[tuple[str, int], dict[str, Any]] = {}
    latest_by_pair: dict[tuple[str, int], dict[str, Any]] = {}
    media_missing: list[str] = []
    for index, row in enumerate(rows, 1):
        task_key = str(row.get("task_key", ""))
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            errors.append(f"journal row {index} has invalid seed")
            continue
        pair = (task_key, seed)
        rows_by_pair[pair].append(row)
        latest_by_pair[pair] = row

        if task_key not in task_set:
            errors.append(f"journal row {index} uses task outside manifest: {task_key}")
        if seed not in expected_seeds:
            errors.append(f"journal row {index} uses seed outside manifest: {seed}")
        if row.get("backend") != campaign.get("backend"):
            errors.append(f"journal row {index} backend differs from campaign")
        if row.get("run_mode") != "eval":
            errors.append(f"journal row {index} is not frozen eval mode")

        verdict = row.get("verdict")
        if verdict not in _TERMINAL | _NONTERMINAL:
            errors.append(f"journal row {index} has unknown verdict: {verdict!r}")
            continue
        if verdict == "success":
            if row.get("success") is not True or row.get("status") != "terminal":
                errors.append(f"journal row {index} has inconsistent success fields")
            if check_media and row.get("video_path"):
                video = Path(str(row["video_path"])).expanduser()
                if not video.is_file():
                    media_missing.append(str(video))
        elif verdict == "failure":
            if row.get("success") is not False or row.get("status") != "terminal":
                errors.append(f"journal row {index} has inconsistent failure fields")
        elif row.get("success") is not None:
            errors.append(f"journal row {index} nonterminal verdict has success label")

    for pair, pair_rows in rows_by_pair.items():
        terminal_rows = [row for row in pair_rows if row.get("verdict") in _TERMINAL]
        signatures = {(row.get("verdict"), row.get("success")) for row in terminal_rows}
        if len(signatures) > 1:
            errors.append(f"conflicting terminal verdicts for {pair[0]} seed={pair[1]}")
        if len(terminal_rows) > 1:
            errors.append(f"duplicate terminal verdicts for {pair[0]} seed={pair[1]}")
        if terminal_rows:
            terminal_by_pair[pair] = terminal_rows[-1]

    success_seed_by_task: dict[str, int] = {}
    for task_key in task_keys:
        success_seeds = sorted(
            seed
            for (task, seed), row in terminal_by_pair.items()
            if task == task_key and row.get("verdict") == "success"
        )
        if success_seeds:
            success_seed_by_task[task_key] = success_seeds[0]
    for (task_key, seed), pair_rows in rows_by_pair.items():
        success_seed = success_seed_by_task.get(task_key)
        if success_seed is not None and seed > success_seed and pair_rows:
            errors.append(
                f"success-lock violation for {task_key}: rows exist after seed {success_seed}"
            )

    per_task = []
    for task_key in task_keys:
        terminal_rows = [
            terminal_by_pair[(task_key, seed)]
            for seed in sorted(expected_seeds)
            if (task_key, seed) in terminal_by_pair
        ]
        success_seed = success_seed_by_task.get(task_key)
        solved = success_seed is not None
        complete = solved or len(terminal_rows) == pass_at
        per_task.append(
            {
                "task_key": task_key,
                "solved": solved,
                "success_seed": success_seed,
                "terminal_seeds": len(terminal_rows),
                "complete": complete,
            }
        )

    valid_rows = list(terminal_by_pair.values())
    recomputed = {
        "status": (
            "complete" if per_task and all(row["complete"] for row in per_task) else "incomplete"
        ),
        "tasks_total": len(task_keys),
        "tasks_solved": sum(row["solved"] for row in per_task),
        "incomplete_tasks": sum(not row["complete"] for row in per_task),
        "episode_successes": sum(row.get("verdict") == "success" for row in valid_rows),
        "episode_failures": sum(row.get("verdict") == "failure" for row in valid_rows),
        "infra_count": sum(row.get("verdict") == "infra" for row in rows),
        "implementation_error_count": sum(
            row.get("verdict") == "implementation_error" for row in rows
        ),
        "unresolved_implementation_error_count": sum(
            pair not in terminal_by_pair and row.get("verdict") == "implementation_error"
            for pair, row in latest_by_pair.items()
        ),
        "task_success_rate": (
            sum(row["solved"] for row in per_task) / len(per_task) if per_task else None
        ),
        "subset": _breakdown(per_task, group=1),
        "suite": _breakdown(per_task, group=2),
        "terminal_by_seed": dict(
            sorted(Counter(str(seed) for _, seed in terminal_by_pair).items())
        ),
        "media_missing": sorted(set(media_missing)),
    }
    if media_missing:
        errors.append(f"{len(set(media_missing))} referenced success videos are missing")

    summary_comparison: dict[str, Any] = {}
    if summary_path.exists():
        summary = _read_object(summary_path, errors, "summary")
        for field in _SUMMARY_FIELDS:
            matches = summary.get(field) == recomputed.get(field)
            summary_comparison[field] = {
                "matches": matches,
                "recorded": summary.get(field),
                "recomputed": recomputed.get(field),
            }
            if not matches:
                errors.append(f"summary field differs from journal: {field}")
    else:
        warnings.append("summary.json is not present; campaign may still be running")

    return _report(
        root=root,
        campaign=campaign,
        rows=rows,
        errors=errors,
        warnings=warnings,
        recomputed=recomputed,
        summary_comparison=summary_comparison,
    )


def write_audit_report(root: Path, report: dict[str, Any]) -> Path:
    path = Path(root).expanduser().resolve() / "audit.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _read_object(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    if not path.exists():
        errors.append(f"{label} file is missing: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label} file is invalid: {type(exc).__name__}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} file must contain a JSON object")
        return {}
    return value


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        errors.append(f"journal file is missing: {path}")
        return []
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"journal line {line_number} is invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"journal line {line_number} is not an object")
            continue
        rows.append(value)
    return rows


def _breakdown(
    per_task: list[dict[str, Any]],
    *,
    group: int,
) -> dict[str, dict[str, int]]:
    output: dict[str, dict[str, int]] = {}
    for row in per_task:
        match = _SHORT_TASK.match(row["task_key"])
        if match is None:
            continue
        label = match.group(group) or "base"
        bucket = output.setdefault(label, {"solved": 0, "total": 0})
        bucket["solved"] += int(row["solved"])
        bucket["total"] += 1
    return output


def _report(
    *,
    root: Path,
    campaign: dict[str, Any],
    rows: list[dict[str, Any]],
    errors: list[str],
    warnings: list[str],
    recomputed: dict[str, Any],
    summary_comparison: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "roborsi.libero_short_audit.v1",
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "integrity_status": "pass" if not errors else "fail",
        "campaign_status": recomputed.get("status", "unknown"),
        "root": str(root),
        "campaign_id": campaign.get("campaign_id"),
        "runtime": campaign.get("runtime", {}),
        "protocol": {
            "backend": campaign.get("backend"),
            "pass_at": campaign.get("pass_at"),
            "seed_start": campaign.get("seed_start"),
            "workers": campaign.get("workers"),
            "tool_budget": campaign.get("tool_budget"),
            "models": campaign.get("models"),
            "reasoning_effort": campaign.get("reasoning_effort"),
            "atomic_compound_enabled": campaign.get(
                "atomic_compound_enabled",
            ),
        },
        "journal_rows": len(rows),
        "recomputed": recomputed,
        "summary_comparison": summary_comparison,
        "errors": errors,
        "warnings": warnings,
    }
