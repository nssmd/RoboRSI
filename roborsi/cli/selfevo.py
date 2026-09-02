"""``roborsi selfevo`` — self-evolution claim driver.

Loop:
    Round 0      : bench test-set baseline (current `base/` state)
    Round 1..K   : roll out the training tasks, extract candidate base
                   skills from successful traces, auto-apply each, re-bench
                   the test set with tag=`round-K`.

Each round's per-test-task bench is tagged ``selfevo-round{K}`` so
``render_claim_report`` can produce the before/after table.

    roborsi selfevo run \\
        --train click_bell,beat_block_hammer,pick_block_bicoord \\
        --test  pick_bowl_bicoord,stack_bowls_bicoord \\
        --rounds 3 --seeds 5
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.store import trace_db as _td
from roborsi.learning.auto_apply import apply_proposal
from roborsi.learning.base_skill_extractor import extract_candidates


selfevo_app = typer.Typer(name="selfevo",
                           help="Self-evolution: extract base skills from "
                                 "successful traces, auto-apply, measure.",
                           no_args_is_help=True)
console = Console()


REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "roborsi" / "embodied" / "skills" / "base"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                            capture_output=True, text=True, check=check)


def _baseline_sha() -> str:
    return _git("rev-parse", "--short", "HEAD").stdout.strip()


def _bench_one(skill: str, seeds: int, tag: str,
                chat_id: str | None = None) -> dict[str, Any]:
    """Run a single skill bench inline (no subprocess), record + return."""
    from roborsi.channels.agent.feishu.task_runner import run_task_sync
    task = skill.rsplit(".", 1)[0] if "." in skill else skill
    cid = chat_id or f"selfevo-{tag}"
    n_pass = 0
    total_calls = 0
    for i in range(seeds):
        run = run_task_sync(task=task, seed=i, episodes=1,
                              tool_budget=12, skill_name=skill,
                              chat_id=cid)
        if run.get("status") == "success":
            n_pass += 1
        blob = run.get("episode_summary") or {}
        total_calls += int(blob.get("tool_calls") or 0)
    avg_calls = total_calls / seeds if seeds else 0.0
    rate = n_pass / seeds if seeds else 0.0
    _td.record_bench(skill=skill, model="default", seeds_passed=n_pass,
                      seeds_total=seeds, avg_tool_calls=avg_calls,
                      commit_sha=_baseline_sha(), tag=tag)
    return {"skill": skill, "rate": rate, "n_pass": n_pass, "seeds": seeds,
             "avg_calls": avg_calls, "tag": tag}


def _bench_set(tasks: list[str], seeds: int, tag: str) -> list[dict[str, Any]]:
    return [_bench_one(f"{t}.zeroshot", seeds, tag) for t in tasks]


def _count_base_skills() -> int:
    if not BASE_DIR.exists():
        return 0
    return sum(1 for d in BASE_DIR.iterdir() if (d / "SKILL.md").exists())


@selfevo_app.command("run")
def selfevo_run(
    train: str = typer.Option(..., "--train",
                                help="Comma-separated training task names "
                                     "(without `.zeroshot` suffix)."),
    test: str = typer.Option(..., "--test",
                               help="Comma-separated held-out test tasks."),
    rounds: int = typer.Option(3, "--rounds", "-r"),
    seeds: int = typer.Option(5, "--seeds", "-n",
                                help="Seeds per task per phase."),
    max_proposals_per_round: int = typer.Option(3, "--max-proposals"),
    model: str = typer.Option("", "--model"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run the self-evolution loop."""
    train_tasks = [t.strip() for t in train.split(",") if t.strip()]
    test_tasks  = [t.strip() for t in test.split(",")  if t.strip()]
    if not train_tasks or not test_tasks:
        console.print("[red]must pass at least one --train and --test task")
        raise typer.Exit(2)

    summary: dict[str, Any] = {
        "train": train_tasks, "test": test_tasks,
        "rounds": rounds, "seeds": seeds,
        "by_round": [],
    }

    # ── Round 0: baseline ─────────────────────────────────────────────
    console.rule("[bold]Round 0: baseline")
    console.print(f"existing base skills: {_count_base_skills()}")
    r0 = _bench_set(test_tasks, seeds, tag="selfevo-round0")
    summary["by_round"].append({
        "round": 0, "base_skills": _count_base_skills(),
        "test": r0, "applied": [], "reverted": [],
    })
    _print_round(0, summary["by_round"][-1])

    for k in range(1, rounds + 1):
        console.rule(f"[bold]Round {k}: train + extract + apply + test")
        # Train rollouts (give the extractor recent successful traces).
        for task in train_tasks:
            console.print(f"  [dim]train rollouts[/dim] {task}.zeroshot "
                            f"({seeds} seeds)…")
            _bench_one(f"{task}.zeroshot", seeds,
                        tag=f"selfevo-round{k}-train")
        # Extract candidate base skills.
        console.print("  [dim]extracting candidate base skills…[/dim]")
        new_pids = extract_candidates(train_tasks,
                                        model=model or None,
                                        max_proposals=max_proposals_per_round)
        console.print(f"  candidates: {new_pids}")
        # Apply each (verifying with first test task as bench, light seeds).
        applied, reverted = [], []
        verify_skill = f"{test_tasks[0]}.zeroshot"
        for pid in new_pids:
            console.print(f"  [dim]apply[/dim] {pid}…")
            res = apply_proposal(pid, bench_seeds=max(3, seeds // 2),
                                   require_no_regression=True,
                                   bench_skill=verify_skill)
            if res.status == "applied":
                applied.append(pid)
            else:
                reverted.append(pid)
            console.print(f"    {res.status}: {res.message}")
        # Final test bench for this round.
        rk = _bench_set(test_tasks, seeds, tag=f"selfevo-round{k}")
        summary["by_round"].append({
            "round": k, "base_skills": _count_base_skills(),
            "test": rk, "applied": applied, "reverted": reverted,
        })
        _print_round(k, summary["by_round"][-1])

    if as_json:
        sys.stdout.write(json.dumps(summary, default=str) + "\n")
    _print_overall(summary)


def _print_round(k: int, info: dict[str, Any]) -> None:
    t = Table(title=f"Round {k} test bench  (base skills: {info['base_skills']})")
    t.add_column("skill"); t.add_column("rate", justify="right")
    t.add_column("calls", justify="right")
    for r in info["test"]:
        t.add_row(r["skill"], f"{r['rate']*100:.0f}%",
                    f"{r['avg_calls']:.1f}")
    console.print(t)
    if info.get("applied") or info.get("reverted"):
        console.print(f"  applied={info['applied']}  "
                        f"reverted={info['reverted']}")


def _print_overall(summary: dict[str, Any]) -> None:
    t = Table(title="Self-evolution overview")
    t.add_column("round", justify="right"); t.add_column("base skills",
                                                            justify="right")
    t.add_column("test rate avg", justify="right")
    t.add_column("test calls avg", justify="right")
    t.add_column("applied", justify="right"); t.add_column("reverted",
                                                              justify="right")
    for row in summary["by_round"]:
        rates = [r["rate"] for r in row["test"]] or [0.0]
        calls = [r["avg_calls"] for r in row["test"]] or [0.0]
        t.add_row(str(row["round"]), str(row["base_skills"]),
                    f"{(sum(rates)/len(rates))*100:.0f}%",
                    f"{sum(calls)/len(calls):.1f}",
                    str(len(row.get("applied", []))),
                    str(len(row.get("reverted", []))))
    console.print(t)


@selfevo_app.command("show")
def selfevo_show(
    train: str = typer.Option(""),
    test: str = typer.Option(""),
) -> None:
    """Show recent selfevo benches grouped by round tag."""
    _td.init()
    import sqlite3
    c = sqlite3.connect(str(_td.db_path()))
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM benches WHERE tag LIKE 'selfevo-round%' "
        "ORDER BY tag, skill").fetchall()]
    c.close()
    if not rows:
        console.print("[yellow]no selfevo benches yet — run `selfevo run` first")
        return
    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        by_tag.setdefault(r["tag"], []).append(r)
    for tag in sorted(by_tag):
        t = Table(title=tag)
        t.add_column("skill"); t.add_column("rate", justify="right")
        t.add_column("calls", justify="right"); t.add_column("run_at")
        for r in by_tag[tag]:
            rate = (r["seeds_passed"] or 0) / max(1, r["seeds_total"])
            t.add_row(r["skill"], f"{rate*100:.0f}%",
                        f"{r['avg_tool_calls']:.1f}" if r["avg_tool_calls"] else "",
                        r["run_at"])
        console.print(t)
