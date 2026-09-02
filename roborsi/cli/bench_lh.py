"""``roborsi bench-lh`` — long-horizon benchmark harness.

Runs one task N times through the 3-role triangle
(Planner.decompose → LHExecutor → Reviewer.review_lh) and records per-attempt
sub-goal completion + end-to-end success + replan count into the ``benches`` table.

    roborsi bench-lh skill clean_table_bicoord --seeds 3 \\
        --tool-budget-per-atomic 18 --max-steps 4
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.store import trace_db as _td


bench_lh_app = typer.Typer(name="bench-lh",
                             help="Long-horizon benchmark (4-claim Phase 3).",
                             no_args_is_help=True)
console = Console()


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=__file__.rsplit("/roborsi/", 1)[0],
            stderr=subprocess.DEVNULL).decode().strip()
        return out or "unknown"
    except Exception:                                          # noqa: BLE001
        return "unknown"


def _attempt_one(task: str, seed: int) -> dict[str, Any]:
    """One long-horizon rollout through the 3-role triangle
    (Planner.decompose → LHExecutor → Reviewer.review_lh). Returns a result
    dict with ``success`` + ``attempts`` (per-atomic) for bench summarisation."""
    from roborsi.agents import (
        Planner, Reviewer, LHExecutor, new_workspace,
    )
    workspace = new_workspace(task)
    mission_spec = Planner().decompose(
        lh_task=task, user_msg=f"bench-lh {task} seed={seed}",
        recent_reflections="", workspace=workspace,
    )
    lh_result = LHExecutor().execute(
        mission_spec=mission_spec, workspace=workspace, seed=seed,
    )
    review = Reviewer().review_lh(workspace=workspace, lh_result=lh_result)
    return {
        "success": lh_result.success,
        "outcome": (f"{lh_result.completed_atomics}/{lh_result.total_atomics} "
                      f"atomics · {review.get('lh_verdict', '')}"),
        "completed_atomics": lh_result.completed_atomics,
        "total_atomics": lh_result.total_atomics,
        "attempts": [a.to_dict() for a in lh_result.attempts],
        "report_path": str(workspace.root),
    }


def _summarize_attempts(result: dict) -> dict[str, Any]:
    """Count sub-goal pass/fail + replans from the executor's per-atomic
    attempts. A replan is any retry beyond the first attempt at an index."""
    attempts = result.get("attempts") or []
    n_total = int(result.get("total_atomics") or 0)
    n_pass = int(result.get("completed_atomics") or 0)
    n_replan = sum(1 for a in attempts if int(a.get("attempt") or 1) > 1)
    calls = [int(a.get("tool_calls") or 0) for a in attempts]
    avg_calls = sum(calls) / len(calls) if calls else 0
    return {"subgoal_total": n_total, "subgoal_pass": n_pass,
              "replan_count": n_replan, "avg_calls_per_subgoal": avg_calls}


@bench_lh_app.command("skill")
def bench_lh_skill(
    task: str = typer.Argument(..., help="Long-horizon task name (no .execute suffix)"),
    seeds: int = typer.Option(3, "--seeds", "-n"),
    seed_start: int = typer.Option(0, "--seed-start"),
    tag: str = typer.Option("lh-default", "--tag"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Bench one long-horizon task across N seeds via the 3-role triangle."""
    sha = _git_sha()
    skill_name = f"{task}.execute"
    rows: list[dict[str, Any]] = []
    n_pass = 0
    total_subgoals = 0
    pass_subgoals = 0
    for i in range(seeds):
        seed = seed_start + i
        if not as_json:
            console.print(f"[dim]bench-lh[/dim] {task} seed={seed} ({i+1}/{seeds})…")
        t0 = time.time()
        r = _attempt_one(task, seed)
        wall = time.time() - t0
        ok = bool(r.get("success"))
        summary = _summarize_attempts(r)
        n_pass += int(ok)
        pass_subgoals += summary["subgoal_pass"]
        total_subgoals += summary["subgoal_total"]
        rows.append({
            "seed": seed, "ok": ok, "outcome": r.get("outcome"),
            "wall_s": round(wall, 1),
            **summary,
            "report_path": r.get("report_path"),
        })
    avg_calls = (sum(r["avg_calls_per_subgoal"] for r in rows) / len(rows)
                   if rows else 0.0)
    _td.record_bench(skill=skill_name, model="default",
                      seeds_passed=n_pass, seeds_total=seeds,
                      avg_tool_calls=avg_calls,
                      commit_sha=sha, tag=tag)
    subgoal_rate = pass_subgoals / max(1, total_subgoals)
    summary_out = {
        "task": task,
        "skill": skill_name,
        "n": seeds,
        "e2e_passed": n_pass,
        "e2e_rate": n_pass / seeds if seeds else 0.0,
        "subgoal_rate": subgoal_rate,
        "avg_calls_per_subgoal": avg_calls,
        "tag": tag,
        "commit_sha": sha,
        "runs": rows,
    }
    if as_json:
        sys.stdout.write(json.dumps(summary_out, ensure_ascii=False) + "\n")
        return
    _print_human(summary_out)


def _print_human(s: dict[str, Any]) -> None:
    e2e = s["e2e_rate"] * 100
    sub = s["subgoal_rate"] * 100
    console.print()
    console.print(f"[bold]{s['skill']}[/bold] · "
                    f"[dim]sha={s['commit_sha']} tag={s['tag']}[/dim]")
    console.print(f"  end-to-end:  [green]{s['e2e_passed']}[/green]/{s['n']}  "
                    f"({e2e:.0f}%)")
    console.print(f"  sub-goal:    {sub:.0f}%")
    console.print(f"  avg calls per sub-goal: {s['avg_calls_per_subgoal']:.1f}")
    t = Table(show_header=True)
    t.add_column("seed", justify="right")
    t.add_column("ok"); t.add_column("subgoals", justify="right")
    t.add_column("replans", justify="right"); t.add_column("wall (s)", justify="right")
    t.add_column("outcome")
    for r in s["runs"]:
        t.add_row(
            str(r["seed"]),
            "[green]✓" if r["ok"] else "[red]✗",
            f"{r['subgoal_pass']}/{r['subgoal_total']}",
            str(r["replan_count"]),
            f"{r['wall_s']:.0f}",
            r.get("outcome") or "")
    console.print(t)


@bench_lh_app.command("compare")
def bench_lh_compare(
    task: str = typer.Argument(...),
    before: str = typer.Option(..., "--before", help="commit sha or tag"),
    after:  str = typer.Option(..., "--after", help="commit sha or tag"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Compare two bench rows for one long-horizon task."""
    _td.init()
    import sqlite3
    c = sqlite3.connect(str(_td.db_path()))
    c.row_factory = sqlite3.Row
    skill = f"{task}.execute"
    rows = {}
    for ref in (before, after):
        r = c.execute(
            "SELECT * FROM benches WHERE skill=? "
            "AND (commit_sha=? OR tag=?) ORDER BY run_at DESC LIMIT 1",
            (skill, ref, ref)).fetchone()
        rows[ref] = dict(r) if r else None
    c.close()
    if not rows[before] or not rows[after]:
        missing = [r for r in (before, after) if not rows[r]]
        console.print(f"[red]no bench row for: {missing}")
        raise typer.Exit(2)
    def rate(r): return (r["seeds_passed"] or 0) / max(1, r["seeds_total"])
    out = {
        "task": task,
        "before": {"ref": before, "rate": rate(rows[before]),
                     "n": rows[before]["seeds_total"]},
        "after":  {"ref": after,  "rate": rate(rows[after]),
                     "n": rows[after]["seeds_total"]},
    }
    out["delta_pp"] = (out["after"]["rate"] - out["before"]["rate"]) * 100
    if as_json:
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return
    console.print(f"[bold]{task}.execute[/bold]")
    console.print(f"  before {before}: {out['before']['rate']*100:.0f}% (n={out['before']['n']})")
    console.print(f"  after  {after}: {out['after']['rate']*100:.0f}% (n={out['after']['n']})")
    console.print(f"  Δ: [{'green' if out['delta_pp']>=0 else 'red'}]"
                    f"{out['delta_pp']:+.1f}pp[/]")
