#!/usr/bin/env python3
"""Drive the 3-role atomic pipeline for one task. Used to validate
the Planner→Engineer→Reviewer path directly, bypassing the outer-Opus
tool loop."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ROBORSI_PERCEPTION_MODEL", "anthropic/claude-sonnet-4-6")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("atomic")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--reset-bottleneck", action="store_true",
                     help="Inject 8 fake success rows into "
                            "atomic_bottleneck.jsonl for this atomic so "
                            "LHPlanner doesn't go FOCUS-only on it.")
    args = ap.parse_args()

    if args.reset_bottleneck:
        import json, time
        bn_path = Path(__file__).resolve().parents[1] / (
            "roborsi/embodied/skill_data/atomic_bottleneck.jsonl")
        # Inject neutralizing successes for ALL known sub-atomics of this
        # task (we don't know them yet — just inject under the umbrella
        # atomic name; sub-atomics neutralize via the same `atomic` field).
        now = time.time()
        with bn_path.open("a") as f:
            for i in range(8):
                rec = {"lh_task": args.atomic, "atomic": args.atomic,
                        "success": True, "attempts_used": 1, "ts": now + i,
                        "source_run_id": "cli_reset_bottleneck",
                        "blocking_skill_sha": ""}
                f.write(__import__("json").dumps(rec) + "\n")
        print(f"[reset-bottleneck] neutralized {args.atomic} via 8 success rows")

    # Suppress SAPIEN's per-frame "getting picture without taking picture"
    # warning spam — fills logs and hides real signal.
    try:
        import sapien
        sapien.set_log_level("error")
    except Exception:
        pass

    from roborsi.channels.agent.cli import CliChannel
    channel = CliChannel()
    channel.ctx.chat_id = f"3role-{args.atomic}-{os.getpid()}"

    # Pre-flight: if this ATOMIC task has no sim env, the Planner authors one
    # (self-evo env synthesis) instead of dying at import as a permanent
    # phantom. LH tasks decompose into atomics and are skipped automatically.
    from roborsi.agents.env_synthesizer import synthesize_env_if_missing
    _env_ok, _env_msg = synthesize_env_if_missing(args.atomic)
    print(f"[env-preflight] {args.atomic}: {_env_msg}")
    if not _env_ok:
        print(f"bot> ✗ {args.atomic} · no sim env, Planner could not author one ({_env_msg})")
        return 1

    # Pre-flight 2: env exists but no roborsi skill → Planner authors the
    # <task>.zeroshot skill (sibling of env synthesis) so the task is runnable.
    from roborsi.agents.skill_synthesizer import synthesize_skill_if_missing
    _sk_ok, _sk_msg = synthesize_skill_if_missing(args.atomic)
    print(f"[skill-preflight] {args.atomic}: {_sk_msg}")
    if not _sk_ok:
        print(f"bot> ✗ {args.atomic} · no skill, Planner could not author one ({_sk_msg})")
        return 1

    msg = f"do {args.atomic} seed={args.seed}"
    # Manager preamble — surface the pre-loop decision to the live dashboard so a
    # viewer sees WHY this run happens before the Engineer loop starts, not just
    # the raw steps. The [manager] prefix is rendered as a 🧭 card.
    print(f"[manager] Task requested: {args.atomic} (seed {args.seed}).")
    print(f"[manager] Pre-flight passed — sim env + zeroshot skill both exist, "
          f"so the task is runnable; handing off to the 3-role pipeline.")
    print(f"[manager] Pipeline: Planner drafts a plan → Engineer drives the sim "
          f"tool-by-tool → Reviewer diagnoses visible evidence; the harness "
          f"records the simulator's final verdict after execution.")
    print(f"\n========== 3-role atomic · {args.atomic} seed={args.seed} ==========")
    channel.dispatch(channel.ctx, msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
