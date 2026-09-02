#!/usr/bin/env python3
"""Push every pending proposal in ~/.roborsi/skill_review/ to a Feishu
chat as a review card. Deduplicates via /tmp/agent_loop/feishu_notified.json.

Usage:
    python scripts/feishu_notify_review.py --chat-id <id>

If --chat-id is omitted, falls back to env var ROBORSI_REVIEW_CHAT_ID.

Designed to be run after a bench / review round, or from a cron.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chat-id",
                      default=os.environ.get("ROBORSI_REVIEW_CHAT_ID"))
    args = ap.parse_args()
    if not args.chat_id:
        print("[feishu-notify] no chat_id (set --chat-id or "
              "ROBORSI_REVIEW_CHAT_ID); skipping", file=sys.stderr)
        return 0
    from roborsi.channels.agent.feishu.feishu_review import notify_pending
    n = notify_pending(args.chat_id)
    print(f"[feishu-notify] pushed {n} new pending proposal card(s) to "
          f"{args.chat_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
