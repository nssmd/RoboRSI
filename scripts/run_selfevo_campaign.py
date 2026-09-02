#!/usr/bin/env python3
"""One-shot self-evolution campaign through the outer bot_agent loop.

For each of N atomic tasks × M seeds:
  - hand the cli channel a user message asking the agent to run the task
    AND, if it finds repeated subroutines worth extracting, to call
    `propose_new_skill` to add a base skill.
  - The bot_agent's own loop owns the run_skill → failure reflection →
    read_skill_code → propose_skill_update/propose_new_skill flow.

No new CLI, no rigid round protocol — just batch-feed messages through the
existing channel + agent. After it finishes, the sqlite `proposals` table
contains whatever the agent chose to propose, and a human (or auto_apply)
decides what to land.

    python3 scripts/run_selfevo_campaign.py [--seeds 5] [--tasks ...]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roborsi.channels.agent.cli import CliChannel
from roborsi.channels.core.agent import handle_user_message
from roborsi.store import trace_db as _td


DEFAULT_TASKS = [
    "click_bell",
    "beat_block_hammer",
    "click_alarmclock",
    "turn_switch",
    "press_stapler",
    "grab_roller",
]


def _per_attempt_message(task: str, seed: int) -> str:
    return (
        f"请执行 `{task}.zeroshot` 任务，使用 seed_start={seed}，episodes=1。\n"
        f"\n"
        f"过程要求：\n"
        f"  1. 直接调 run_skill(name='{task}.zeroshot', args={{'seed_start': {seed}}}) 运行。\n"
        f"  2. 失败时：read_skill_code 看实现 → 分析根因 → 如果发现这次失败的修法\n"
        f"     是几个 step 的组合（这几个 step 在别的任务里也常出现），"
        f"     调 propose_new_skill 提交一个新的 base skill；\n"
        f"     如果是该 skill 自己的实现 bug，调 propose_skill_update 提交修复。\n"
        f"  3. 一句话总结结果。\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=",".join(DEFAULT_TASKS))
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--seed-start", type=int, default=0)
    args = ap.parse_args()
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    channel = CliChannel()
    ctx = channel.ctx
    monitor = "http://localhost:8770"
    print(f"[campaign] chat_id={ctx.chat_id}")
    print(f"[campaign] tasks={tasks}  seeds={args.seeds}")
    print(f"[campaign] monitor: {monitor}/live/{ctx.chat_id}")
    print()
    t0 = time.time()
    n_done = 0
    n_total = len(tasks) * args.seeds
    for task in tasks:
        for i in range(args.seeds):
            seed = args.seed_start + i
            n_done += 1
            print(f"\n========== [{n_done}/{n_total}] {task} seed={seed} ==========",
                    flush=True)
            msg = _per_attempt_message(task, seed)
            try:
                handle_user_message(msg, channel=channel, ctx=ctx)
            except KeyboardInterrupt:
                print("\n[campaign] interrupted by user; quitting")
                return 130
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                print(f"[campaign] attempt error: {type(e).__name__}: {e}")
            # Snapshot the campaign's proposal queue so far.
            props = _td.list_proposals(limit=200)
            new = [p for p in props if (p.get("created_at") or "") >= time.strftime(
                "%Y-%m-%d", time.localtime(t0))]
            pending = [p for p in new if p.get("status") == "pending"]
            print(f"[campaign] proposals so far: {len(new)} total · "
                    f"{len(pending)} pending review", flush=True)
    elapsed = time.time() - t0
    print(f"\n========== campaign done in {elapsed/60:.1f} min ==========")
    print("Run:  python3 -c \"from roborsi.store import trace_db as td; "
            "[print(p) for p in td.list_proposals(status='pending', limit=50)]\"")
    print("to inspect pending proposals, then have me (Claude) auto-apply the good ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
