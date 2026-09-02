#!/usr/bin/env python3
"""Run ONE turn (one seed) of a task through CliChannel, with optional
persistent history. Designed for the human-in-the-loop pattern:
    1. invoke this script → 1 turn runs → exits, dumps history to file
    2. human reviews proposals, applies via apply_selfevo_proposal.py
    3. invoke again with same --history-file → agent sees full history
       (with the just-applied fix already in repo on disk)
    4. repeat per seed

Code changes between turns are picked up because each turn is a fresh
process (plugin module cache is fresh each time).

    python3 scripts/cli_task.py <task> --seed <n> [--history-file PATH] \\
        [--tool-budget 24]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roborsi.channels.agent.cli import CliChannel
from roborsi.store import trace_db as _td


PROMPT_TMPL = (
    "请执行 `{task}.zeroshot` 任务，使用 seed_start={seed}, episodes=1, "
    "tool_budget={tool_budget}。\n"
    "\n"
    "流程：\n"
    "  1. 调 run_skill(name='{task}.zeroshot', args={{'seed_start': {seed}, 'tool_budget': {tool_budget}}}).\n"
    "  2. 失败时：read_skill_code → 回顾本会话之前 seed 的失败（在你的对话历史里） →\n"
    "     如果有可复用改进，**必须**调 propose_new_skill / propose_skill_update 工具。\n"
    "     **不要只在 chat 里写 diff** —— 写在工具调用里才能进入审核队列被 apply。\n"
    "     多个独立改进就分别提，不要合并。\n"
    "  3. 一句话报结果。\n"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("task")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--tool-budget", type=int, default=24)
    ap.add_argument("--history-file",
                      help="JSON file: load before turn, save after.")
    ap.add_argument("--chat-id",
                      default=os.environ.get("ROBORSI_CHAT_ID")
                      or f"cli-{os.getpid()}-{int(time.time())}")
    args = ap.parse_args()

    queue_dir = Path.home() / ".roborsi" / "skill_review"
    queue_dir.mkdir(parents=True, exist_ok=True)

    channel = CliChannel()
    channel.ctx.chat_id = args.chat_id

    # Load prior history if requested.
    history_path = Path(args.history_file) if args.history_file else None
    if history_path and history_path.exists():
        channel._history = json.loads(history_path.read_text(encoding="utf-8"))
        print(f"[cli_task] loaded {len(channel._history)} msg history from "
              f"{history_path}", flush=True)
    print(f"\n========== {args.task} seed={args.seed}  "
          f"chat_id={args.chat_id}  history_in={len(channel._history)} ==========",
          flush=True)

    existing_files = {p.name for p in queue_dir.glob("*.json")}
    t0 = time.time()
    msg = PROMPT_TMPL.format(task=args.task, seed=args.seed,
                                tool_budget=args.tool_budget)
    try:
        channel.dispatch(channel.ctx, msg)
    except KeyboardInterrupt:
        print("\n[cli_task] interrupted")
        return 130
    wall = time.time() - t0

    # Save updated history.
    if history_path:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            json.dumps(channel._history, ensure_ascii=False, indent=2,
                         default=str),
            encoding="utf-8")
        print(f"[cli_task] saved {len(channel._history)} msg history to "
              f"{history_path}", flush=True)

    # New proposals from this turn.
    new_files = sorted(p for p in queue_dir.glob("*.json")
                        if p.name not in existing_files)
    print(f"\n[turn done] {wall:.1f}s · {len(new_files)} new proposal(s) · "
          f"history_out={len(channel._history)}", flush=True)
    for fp in new_files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        print(f"  PROPOSAL  id={data.get('id')}  kind={data.get('kind')}  "
              f"name={data.get('name')}  category={data.get('category','-')}")
        print(f"  rationale: {(data.get('rationale') or '')[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
