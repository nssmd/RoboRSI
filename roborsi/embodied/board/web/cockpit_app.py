"""FastAPI server for the RoboRSI session cockpit (read-only viewer).

Endpoints (all under ``/api``)::

    GET  /api/sessions                  → [{key, role, task, thread_id, ...}]
    GET  /api/sessions/{key}/turns      → {role, task, thread_id, turns:[...]}
    GET  /api/tasks                     → per-task run tally
    GET  /api/tasks/{task}/progress     → runs / verified successes for a task
    GET  /api/tasks/{task}/evolution    → wiki leads + hypothesis funnel + trend
    GET  /api/evolution                 → global skill self-evolution overview
    GET  /api/manager                   → Manager-led orchestration overview
    GET  /api/campaign                  → both lanes + campaign.log digest
    GET  /api/events?since=<offset>     → poll fallback for new campaign lines
    WS   /api/stream                    → live push of new campaign.log lines

When ``frontend/web/dist`` exists it is mounted at ``/`` so a single port serves
both the API and the built SPA. ``fastapi`` / ``uvicorn`` are imported lazily so
importing this module never hard-requires the web extra.

Deliberately NO ``from __future__ import annotations`` — the nested route
handlers annotate params with the locally-imported ``WebSocket`` / ``Query``
types, and stringised annotations would break FastAPI's resolution (same reason
as the argus-skill webapi).
"""

import asyncio
import os
from pathlib import Path

from . import readers as data

# frontend/web/dist lives at the repo root (two levels above this file's parent).
WEB_DIST = data.REPO_ROOT / "frontend" / "web" / "dist"

DEFAULT_PORT = 8795
_CAMPAIGN_POLL_SECONDS = 2.0


def create_app(*, auth_token: str | None = None):
    """Build the FastAPI app. Requires the ``[web]`` extra (fastapi)."""
    from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware

    token = auth_token if auth_token is not None else os.environ.get("ROBORSI_WEB_TOKEN")
    app = FastAPI(title="roborsi session cockpit", version="0.1.0")

    # Localhost dev only: allow the Vite dev server + same-origin. Not a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173", "http://127.0.0.1:5173",
            f"http://localhost:{DEFAULT_PORT}", f"http://127.0.0.1:{DEFAULT_PORT}",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _require_auth(authorization: str | None = Header(default=None)) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    _register_read_routes(app, Depends, HTTPException, Query, _require_auth)
    _register_stream(app, WebSocket, WebSocketDisconnect, Query, token)
    _mount_static(app)
    return app


def _register_read_routes(app, Depends, HTTPException, Query, require_auth) -> None:
    dep = [Depends(require_auth)]

    @app.get("/api/sessions", dependencies=dep)
    def _sessions() -> dict:
        return {"sessions": data.list_sessions()}

    @app.get("/api/sessions/{key:path}/turns", dependencies=dep)
    def _turns(key: str) -> dict:
        result = data.session_turns(key)
        if not result["found"]:
            raise HTTPException(status_code=404, detail=f"unknown session: {key}")
        return result

    @app.get("/api/tasks", dependencies=dep)
    def _tasks(limit: int = Query(40, ge=1, le=500)) -> dict:
        return {"tasks": data.task_overview(limit=limit)}

    @app.get("/api/tasks/{task}/progress", dependencies=dep)
    def _progress(task: str, limit: int = Query(50, ge=1, le=500)) -> dict:
        return data.task_progress(task, limit=limit)

    @app.get("/api/campaign", dependencies=dep)
    def _campaign() -> dict:
        return data.campaign_status()

    @app.get("/api/evolution", dependencies=dep)
    def _evolution() -> dict:
        return data.evolution_overview()

    @app.get("/api/manager", dependencies=dep)
    def _manager() -> dict:
        return data.manager_overview()

    @app.get("/api/tasks/{task}/evolution", dependencies=dep)
    def _task_evolution(task: str) -> dict:
        return data.task_evolution(task)

    @app.get("/api/events", dependencies=dep)
    def _events(since: int = Query(0, ge=0), tail: int = Query(200, ge=1, le=2000)) -> dict:
        return data.campaign_log_lines(offset=since, tail=tail)


def _register_stream(app, WebSocket, WebSocketDisconnect, Query, token) -> None:
    @app.websocket("/api/stream")
    async def _stream(ws: WebSocket, token_q: str | None = Query(default=None, alias="token")) -> None:
        await ws.accept()
        if token and token_q != token:
            await ws.close(code=4401, reason="unauthorized")
            return
        await _pump_campaign(ws, WebSocketDisconnect)

    async def _pump_campaign(ws, WebSocketDisconnect) -> None:
        """Push new campaign.log lines as they appear, then poll forever."""
        seed = data.campaign_log_lines(offset=0, tail=60)
        await ws.send_json({"type": "seed", **seed})
        offset = seed["next_offset"]
        try:
            while True:
                await asyncio.sleep(_CAMPAIGN_POLL_SECONDS)
                update = data.campaign_log_lines(offset=offset, tail=2000)
                if update["next_offset"] > offset:
                    await ws.send_json({"type": "append", **update})
                    offset = update["next_offset"]
        except WebSocketDisconnect:
            return


def _mount_static(app) -> None:
    """Mount the built SPA at ``/`` when present (API routes win — declared first)."""
    if not WEB_DIST.is_dir():
        return
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
