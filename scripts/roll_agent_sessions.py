#!/usr/bin/env python3
"""roll_agent_sessions — the Manager's periodic cleanup for the persistent
Planner / Reviewer sessions.

A persistent (role, task) Claude session accumulates context every run; left
unbounded it bloats. This rolls it: ask the session to SUMMARIZE its durable
lessons, archive that summary to ~/.roborsi/agent_memory/<role>_<task>.md,
then forget the live session id so the next run starts a FRESH session — which
persistent_agent seeds with the archived summary. So memory is compacted but
preserved (the agent wrote the summary itself).

The Manager runs this after a few iterations (see MANAGER.md). The user decides
when — it is not automatic.

Usage:  python3 scripts/roll_agent_sessions.py --role reviewer --task handover_block_bicoord
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roborsi.agents import persistent_agent

_SUMMARIZE = (
    "Compact your own memory. Summarize the DURABLE lessons you have learned "
    "across ALL your prior turns on this task — what reliably works, what fails "
    "and the root cause, the validated parameters/pipeline, and any open "
    "problems — as a tight bulleted list (aim for <= 15 bullets). A FRESH copy "
    "of you will be seeded with ONLY this summary, so include everything that "
    "fresh-you would need and nothing transient. Output ONLY the bullets."
)
_SUMMARY_SYS = (
    "You are compacting your own session memory into a durable summary for a "
    "fresh successor session. Be concrete and lossless on the lessons; drop "
    "transient chatter."
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=["planner", "reviewer"])
    ap.add_argument("--task", required=True)
    args = ap.parse_args()

    if persistent_agent.session_id(args.role, args.task) is None:
        print(f"no live session for {args.role}:{args.task} — nothing to roll")
        return 0

    summary = persistent_agent.run(args.role, args.task, _SUMMARIZE,
                                   system_prompt=_SUMMARY_SYS)
    mem = persistent_agent.memory_file(args.role, args.task)
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_text(summary, encoding="utf-8")
    persistent_agent.clear(args.role, args.task)
    print(f"rolled {args.role}:{args.task}\n  summary -> {mem}\n"
          f"  live session cleared; next run starts fresh, seeded with the summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
