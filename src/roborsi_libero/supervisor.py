"""Sequential seed supervisor with parallel workers and exact resume."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from roborsi_libero.catalog import SHORT_TASK_CATALOG, suite_for
from roborsi_libero.config import load_config
from roborsi_libero.launcher import build_worker_commands
from roborsi_libero.protocol import CampaignState, EpisodeVerdict, schedule_round

INFRASTRUCTURE = {
    "provider_failure",
    "transport_failure",
    "image_failure",
    "resource_failure",
    "interrupted",
}


def _source_records(campaign_root: Path):
    from roborsi.embodied.sim.libero.run_records import load_records, merge_records

    rows = []
    for path in sorted((campaign_root / "journals").glob("*.episodes.jsonl")):
        rows.extend(load_records(path))
    merged, conflicts = merge_records(rows)
    if conflicts:
        raise ValueError(f"campaign journal conflicts: {sorted(conflicts)}")
    return list(merged.values())


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _existing_progress(campaign_root: Path) -> tuple[list[int], str]:
    path = campaign_root / "state.json"
    if not path.is_file():
        return [], "running"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], "running"
    completed = sorted({int(seed) for seed in row.get("completed_seeds") or []})
    status = str(row.get("status") or "running")
    return completed, status if status in {"running", "complete", "blocked"} else "running"


def refresh_campaign_state(campaign_root: Path | str) -> CampaignState:
    root = Path(campaign_root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    state = CampaignState.new(mode=manifest["mode"], release_id=manifest["release_id"])
    completed, status = _existing_progress(root)
    state.completed_seeds = completed
    state.status = status
    for row in sorted(
        _source_records(root),
        key=lambda value: (
            str(value.recorded_at or ""),
            value.identity.task_key,
            value.identity.seed,
            value.identity.attempt,
        ),
    ):
        release_id = str(row.code_fingerprint or "").removeprefix("release:")
        release_id = release_id or manifest["release_id"]
        state.begin_release(release_id)
        state.record(
            EpisodeVerdict(
                task_key=row.identity.task_key,
                seed=int(row.identity.seed),
                attempt=int(row.identity.attempt),
                category=row.category,
                simulator_verdict=(
                    row.category if row.category in {"task_success", "task_failure"} else None
                ),
                release_id=release_id,
            )
        )
    _atomic_json(root / "state.json", state.to_dict())
    return state


def summarize_campaign(campaign_root: Path | str) -> dict[str, Any]:
    root = Path(campaign_root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    state = refresh_campaign_state(root)
    rows = _source_records(root)
    solved = state.solved_tasks
    by_suite: dict[str, dict[str, int | float]] = {}
    for suite in sorted({suite_for(task) for task in SHORT_TASK_CATALOG}):
        tasks = {task for task in SHORT_TASK_CATALOG if suite_for(task) == suite}
        count = len(tasks & solved)
        by_suite[suite] = {
            "solved_tasks": count,
            "total_tasks": len(tasks),
            "rate": count / len(tasks),
        }
    pass_curve = []
    cumulative: set[str] = set()
    for seed in manifest["seeds"]:
        cumulative.update(
            row.identity.task_key
            for row in rows
            if row.category == "task_success" and int(row.identity.seed) == int(seed)
        )
        pass_curve.append(len(cumulative))
    verdicts = Counter(row.category for row in rows)
    metered = [row for row in rows if row.category not in INFRASTRUCTURE]
    token_values = [int(row.total_tokens) for row in metered]
    summary = {
        "schema": "roborsi.libero_short_campaign_summary.v1",
        "run_id": manifest["run_id"],
        "mode": manifest["mode"],
        "metric": manifest["metric"],
        "claim_scope": manifest["claim_scope"],
        "status": state.status,
        "completed_seeds": state.completed_seeds,
        "solved_tasks": len(solved),
        "solved_task_keys": sorted(solved),
        "total_tasks": len(SHORT_TASK_CATALOG),
        "rate": len(solved) / len(SHORT_TASK_CATALOG),
        "pass_curve": pass_curve,
        "by_suite": by_suite,
        "release_history": state.release_history,
        "verdicts": {
            "task_success": verdicts["task_success"],
            "task_failure": verdicts["task_failure"],
            "implementation_failure": verdicts["implementation_failure"],
            "infrastructure_excluded": sum(verdicts[name] for name in INFRASTRUCTURE),
        },
        "efficiency": {
            "total_tokens": sum(token_values),
            "median_total_tokens": float(statistics.median(token_values)) if token_values else 0.0,
            "total_vlm_calls": sum(int(row.vlm_calls) for row in metered),
            "total_episode_elapsed_s": sum(float(row.elapsed_s) for row in metered),
            "scope": "all non-infrastructure campaign episode records",
        },
        "success_source": "final simulator predicate only",
    }
    _atomic_json(root / "result.json", summary)
    return summary


def _run_workers(config, commands) -> list[int]:
    processes = []
    base_env = os.environ.copy()
    base_env.update(config.runtime_environment(base_env))
    for command in commands:
        env = dict(base_env)
        env.update(command.env)
        command.log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = command.log_path.open("ab")
        process = subprocess.Popen(command.argv, env=env, stdout=stream, stderr=subprocess.STDOUT)
        stream.close()
        processes.append(process)
    return [process.wait() for process in processes]


def run_supervisor(campaign_root: Path | str, *, max_stalled_retries: int = 6) -> dict[str, Any]:
    root = Path(campaign_root).resolve()
    config = load_config(root / "config.resolved.yaml")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    state = refresh_campaign_state(root)
    if state.status == "complete":
        return summarize_campaign(root)
    state.status = "running"
    _atomic_json(root / "state.json", state.to_dict())

    for seed in manifest["seeds"]:
        if seed in state.completed_seeds:
            continue
        stalled = 0
        while True:
            state = refresh_campaign_state(root)
            pending = schedule_round(state, seed=seed)
            if not pending:
                state.completed_seeds = sorted({*state.completed_seeds, seed})
                _atomic_json(root / "state.json", state.to_dict())
                break
            commands = build_worker_commands(
                config,
                campaign_root=root,
                task_keys=pending,
                seed=seed,
                release_id=state.current_release_id,
            )
            return_codes = _run_workers(config, commands)
            refreshed = refresh_campaign_state(root)
            remaining = schedule_round(refreshed, seed=seed)
            if len(remaining) >= len(pending) or any(code != 0 for code in return_codes):
                stalled += 1
            else:
                stalled = 0
            if stalled >= max_stalled_retries:
                refreshed.status = "blocked"
                _atomic_json(root / "state.json", refreshed.to_dict())
                return summarize_campaign(root)
        if manifest["mode"] == "adaptive":
            try:
                from roborsi_libero.evolution import process_pending_proposals

                release_id = process_pending_proposals(root, seed=seed)
                if release_id:
                    state = refresh_campaign_state(root)
                    state.begin_release(release_id)
                    _atomic_json(root / "state.json", state.to_dict())
            except Exception as exc:  # noqa: BLE001
                with (root / "proposal_errors.log").open("a", encoding="utf-8") as stream:
                    stream.write(f"seed={seed} {type(exc).__name__}: {exc}\n")
    state = refresh_campaign_state(root)
    state.status = "complete"
    _atomic_json(root / "state.json", state.to_dict())
    return summarize_campaign(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    args = parser.parse_args()
    result = run_supervisor(args.campaign)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
