#!/usr/bin/env python3
"""Launch the RoboRSI *session cockpit* (:8795) — thin launcher.

The implementation now lives in :mod:`roborsi.embodied.board.web` (the board's
web layer). This starts ONLY the cockpit app so the standing cron can restart
:8795 independently; run ``python -m roborsi.embodied.board.web.server`` to
serve both the cockpit and the evo dashboard from one process.

Build the SPA first (``cd frontend/web && npm install && npm run build``) so the
UI is served on the same port; otherwise run the Vite dev server which proxies
``/api`` here. Optional bearer auth via ``--token`` or ``ROBORSI_WEB_TOKEN``.
"""
import argparse
import sys
from pathlib import Path

# Make the repo importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roborsi.embodied.board.web import server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="RoboRSI session cockpit web API")
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=server.DEFAULT_COCKPIT_PORT,
                        help=f"bind port (default {server.DEFAULT_COCKPIT_PORT})")
    parser.add_argument("--token", default=None,
                        help="optional bearer token (else ROBORSI_WEB_TOKEN)")
    args = parser.parse_args()
    return server.serve(host=args.host, evo_port=None, cockpit_port=args.port,
                        auth_token=args.token)


if __name__ == "__main__":
    raise SystemExit(main())
