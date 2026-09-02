#!/usr/bin/env python3
"""Launch the RoboRSI evo self-evolution 看板 (:8787) — thin launcher.

The implementation moved into :mod:`roborsi.embodied.board.web` (the board's
web layer): readers in ``board/web/{readers,evo_readers}.py``, the page markup in
``board/web/page.py``, the FastAPI app in ``board/web/evo_app.py``. Edit those,
NOT this file. This starts ONLY the evo app so the standing cron can restart
:8787 independently; run ``python -m roborsi.embodied.board.web.server`` to
serve both the evo dashboard and the session cockpit from one process.

    python3 scripts/evo_dashboard.py --port 8787
    # 公网: cloudflared tunnel --url http://localhost:8787
"""
import argparse
import sys
from pathlib import Path

# Make the repo importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roborsi.embodied.board.web import server  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="RoboRSI evo self-evolution dashboard")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=server.DEFAULT_EVO_PORT)
    args = ap.parse_args()
    server.serve(host=args.host, evo_port=args.port, cockpit_port=None)


if __name__ == "__main__":
    main()
