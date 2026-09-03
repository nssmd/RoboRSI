"""``roborsi bench`` — run a skill N times and record success rate.

Used by all three claim test suites (self-evolution, data flywheel,
long-horizon) to produce a consistent ``benches`` row per measurement.

    roborsi bench skill click_bell.zeroshot --seeds 5 --json
    roborsi bench compare click_bell.zeroshot --before SHA1 --after SHA2
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from roborsi.store import trace_db as _td


bench_app = typer.Typer(name="bench",
                         help="Benchmark a skill (success rate, tool calls).",
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


def _task_from_skill(skill: str) -> str:
    """`click_bell.zeroshot` → `click_bell`."""
    return skill.rsplit(".", 1)[0] if "." in skill else skill


def _summarize_run(run: dict[str, Any]) -> tuple[bool | None, int]:
    """Return (verdict, tool_calls); verdict is None for infra/error rows."""
    status = run.get("status")
    verdict = True if status == "success" else False if status == "failed" else None
    n_calls = 0
    blob = run.get("episode_summary_json")
    if blob:
        try:
            ep = json.loads(blob)
            n_calls = int(ep.get("tool_calls") or 0)
        except (json.JSONDecodeError, ValueError):
            pass
    return verdict, n_calls


@bench_app.command("skill")
def bench_skill(
    skill: str = typer.Argument(..., help="Skill name (e.g. click_bell.zeroshot)"),
    seeds: int = typer.Option(5, "--seeds", "-n", help="How many seeds to run."),
    seed_start: int = typer.Option(0, "--seed-start",
                                     help="First seed index (seeds run [start, start+N))."),
    tool_budget: int = typer.Option(12, "--tool-budget"),
    model: str = typer.Option("", "--model",
                                help="Override DEFAULT_MODEL during this bench."),
    as_json: bool = typer.Option(False, "--json"),
    chat_id: str = typer.Option("bench", "--chat-id",
                                  help="Tag this bench's events under a chat_id."),
    run_mode: str = typer.Option(
        "eval", "--mode", help="Run mode: eval (frozen) or evolve."
    ),
) -> None:
    """Run a skill `seeds` times, record one `benches` row, print summary."""
    from roborsi.channels.agent.feishu.task_runner import (
        run_task_sync,
    )
    task = _task_from_skill(skill)
    from roborsi.runtime_mode import parse_mode
    try:
        parsed_mode = parse_mode(run_mode)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--mode") from exc
    sha = _git_sha()
    if model:
        # The rollout runtime reads DEFAULT_MODEL from env via its module
        # constant — override before importing run paths.
        import os
        os.environ["ROBORSI_DEFAULT_MODEL"] = model
    results: list[dict[str, Any]] = []
    n_pass = 0
    n_fail = 0
    n_infra = 0
    n_verdict = 0
    total_calls = 0
    for i in range(seeds):
        seed = seed_start + i
        if not as_json:
            console.print(f"[dim]bench[/dim] {skill} seed={seed} "
                            f"({i+1}/{seeds})…")
        run = run_task_sync(task=task, seed=seed, episodes=1,
                              tool_budget=tool_budget, skill_name=skill,
                              chat_id=chat_id,
                              run_mode=parsed_mode.value)
        verdict, n_calls = _summarize_run(run)
        if verdict is None:
            n_infra += 1
            verdict_label = "infra"
        else:
            n_verdict += 1
            n_pass += int(verdict)
            n_fail += int(not verdict)
            total_calls += n_calls
            verdict_label = "success" if verdict else "failure"
        results.append({"seed": seed, "ok": verdict, "verdict": verdict_label,
                          "tool_calls": n_calls,
                          "run_id": run.get("run_id"),
                          "outcome": run.get("outcome"),
                          "status": run.get("status")})
    avg_calls = (total_calls / n_verdict) if n_verdict else 0.0
    _td.record_bench(skill=skill, model=model or "default",
                      seeds_passed=n_pass, seeds_total=n_verdict,
                      avg_tool_calls=avg_calls, commit_sha=sha,
                      run_mode=parsed_mode.value)
    summary = {
        "skill": skill,
        "model": model or "default",
        "run_mode": parsed_mode.value,
        "n": seeds,
        "requested_seeds": seeds,
        "verdict_count": n_verdict,
        "seeds_passed": n_pass,
        "seeds_failed": n_fail,
        "infra_count": n_infra,
        "success_rate": n_pass / n_verdict if n_verdict else None,
        "avg_tool_calls": avg_calls,
        "commit_sha": sha,
        "runs": results,
    }
    if as_json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return
    _print_human(summary)


def _print_human(summary: dict[str, Any]) -> None:
    rate = summary["success_rate"]
    bar = ("█" * int((rate or 0.0) * 20)).ljust(20)
    console.print()
    console.print(f"[bold]{summary['skill']}[/bold]  "
                    f"model=[cyan]{summary['model']}[/cyan]  "
                    f"sha=[dim]{summary['commit_sha']}[/dim]")
    rate_text = f"{rate * 100:.0f}%" if rate is not None else "n/a"
    console.print(
        f"  success: [green]{summary['seeds_passed']}[/green]/"
        f"{summary['verdict_count']}  |{bar}|  "
        f"[bold]{rate_text}[/bold]  "
        f"failed={summary['seeds_failed']} infra={summary['infra_count']}"
    )
    console.print(f"  avg tool calls: {summary['avg_tool_calls']:.1f}")
    t = Table(show_header=True)
    t.add_column("seed", justify="right")
    t.add_column("ok"); t.add_column("calls", justify="right")
    t.add_column("outcome"); t.add_column("run_id")
    for r in summary["runs"]:
        verdict = {
            "success": "[green]✓",
            "failure": "[red]✗",
            "infra": "[yellow]!",
        }[r["verdict"]]
        t.add_row(str(r["seed"]),
                    verdict,
                    str(r["tool_calls"]),
                    r.get("outcome") or "",
                    r.get("run_id") or "")
    console.print(t)


@bench_app.command("compare")
def bench_compare(
    skill: str = typer.Argument(...),
    before: str = typer.Option(..., "--before", help="commit sha"),
    after:  str = typer.Option(..., "--after",  help="commit sha"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Compare the most-recent `benches` rows for two commit shas."""
    _td.init()
    import sqlite3
    c = sqlite3.connect(str(_td.db_path()))
    c.row_factory = sqlite3.Row
    rows = {}
    for sha in (before, after):
        r = c.execute(
            "SELECT * FROM benches WHERE skill=? AND commit_sha=? "
            "ORDER BY run_at DESC LIMIT 1", (skill, sha)).fetchone()
        rows[sha] = dict(r) if r else None
    c.close()
    if not rows[before] or not rows[after]:
        missing = [s for s in (before, after) if not rows[s]]
        console.print(f"[red]no bench row for sha(s): {missing}")
        raise typer.Exit(2)
    def rate(r): return (r["seeds_passed"] or 0) / max(1, r["seeds_total"])
    out = {
        "skill": skill,
        "before": {"sha": before, "rate": rate(rows[before]),
                     "n": rows[before]["seeds_total"]},
        "after":  {"sha": after,  "rate": rate(rows[after]),
                     "n": rows[after]["seeds_total"]},
    }
    out["delta_pp"] = (out["after"]["rate"] - out["before"]["rate"]) * 100
    if as_json:
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return
    console.print(f"[bold]{skill}[/bold]")
    console.print(f"  before {before}: {out['before']['rate']*100:.0f}% "
                    f"(n={out['before']['n']})")
    console.print(f"  after  {after}: {out['after']['rate']*100:.0f}% "
                    f"(n={out['after']['n']})")
    console.print(f"  Δ: [{'green' if out['delta_pp']>=0 else 'red'}]"
                    f"{out['delta_pp']:+.1f}pp[/]")


@bench_app.command("history")
def bench_history(
    skill: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show recent `benches` rows for a skill."""
    _td.init()
    import sqlite3
    c = sqlite3.connect(str(_td.db_path()))
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM benches WHERE skill=? ORDER BY run_at DESC LIMIT ?",
        (skill, limit)).fetchall()]
    c.close()
    if as_json:
        sys.stdout.write(json.dumps({"skill": skill, "rows": rows},
                                       ensure_ascii=False) + "\n")
        return
    t = Table(show_header=True, title=skill)
    t.add_column("run_at"); t.add_column("mode"); t.add_column("model")
    t.add_column("sha")
    t.add_column("n", justify="right"); t.add_column("rate", justify="right")
    t.add_column("avg calls", justify="right")
    for r in rows:
        rate = (r["seeds_passed"] or 0) / max(1, r["seeds_total"])
        t.add_row(r["run_at"], r.get("run_mode") or "evolve",
                    r["model"] or "", r["commit_sha"] or "",
                    str(r["seeds_total"]),
                    f"{rate*100:.0f}%",
                    f"{r['avg_tool_calls']:.1f}" if r["avg_tool_calls"] is not None else "")
    console.print(t)
