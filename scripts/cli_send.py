#!/usr/bin/env python3
"""Send a single custom message through CliChannel with a persistent
history file. Mirrors cli_task.py but lets the caller
pass the message verbatim (instead of a hardcoded prompt template).

    python3 scripts/cli_send.py --history-file PATH --chat-id ID \\
        "<message body>"
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("message", help="User message (verbatim)")
    ap.add_argument("--history-file", required=False)
    ap.add_argument("--chat-id",
                      default=os.environ.get("ROBORSI_CHAT_ID")
                      or f"cli-send-{os.getpid()}-{int(time.time())}")
    args = ap.parse_args()
    queue_dir = Path.home() / ".roborsi" / "skill_review"
    queue_dir.mkdir(parents=True, exist_ok=True)
    channel = CliChannel()
    channel.ctx.chat_id = args.chat_id
    history_path = Path(args.history_file) if args.history_file else None
    if history_path and history_path.exists():
        channel._history = json.loads(history_path.read_text(encoding="utf-8"))
        print(f"[cli_send] loaded {len(channel._history)} msg history",
              flush=True)
    print(f"\n========== send  chat_id={args.chat_id}  "
          f"history_in={len(channel._history)} ==========", flush=True)
    existing = {p.name for p in queue_dir.glob("*.json")}
    t0 = time.time()
    try:
        channel.dispatch(channel.ctx, args.message)
    except KeyboardInterrupt:
        return 130
    wall = time.time() - t0
    if history_path:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            json.dumps(channel._history, ensure_ascii=False, indent=2,
                         default=str),
            encoding="utf-8")
        print(f"[cli_send] saved {len(channel._history)} msg history",
              flush=True)
    new_files = sorted(p for p in queue_dir.glob("*.json")
                        if p.name not in existing)
    print(f"\n[turn done] {wall:.1f}s · {len(new_files)} new proposal(s)",
          flush=True)
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
