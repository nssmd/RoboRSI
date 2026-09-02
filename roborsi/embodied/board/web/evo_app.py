"""FastAPI app for the evo self-evolution 看板 (:8787).

Serves the inline page (:mod:`board.web.page`) plus the exact routes its JS
expects — ``/data.json``, ``/sessions``, ``/frame.jpg`` (GET) and ``/message``,
``/command`` (POST) — over the migrated readers (:mod:`board.web.evo_readers`).
The route shapes are byte-identical to the old stdlib ``scripts/evo_dashboard.py``
Handler, so the page markup is unchanged.

No ``from __future__ import annotations`` — nested route handlers annotate params
with locally-imported FastAPI types; stringised annotations break resolution
(same reason as cockpit_app).
"""

import time

from . import evo_readers as E
from . import page

DEFAULT_PORT = 8787
_NO_STORE = {"Cache-Control": "no-store"}


def create_app():
    """Build the evo dashboard FastAPI app. Requires the ``[web]`` extra."""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    app = FastAPI(title="roborsi evo dashboard", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index", response_class=HTMLResponse)
    def _index() -> HTMLResponse:
        return HTMLResponse(page.HTML, headers=_NO_STORE)

    @app.get("/data.json")
    def _data(lane: str = "A", session: str = "direct") -> JSONResponse:
        return JSONResponse(E.snapshot(lane, session), headers=_NO_STORE)

    @app.get("/aspire.json")
    def _aspire() -> JSONResponse:
        from . import aspire_readers as A
        return JSONResponse(A.snapshot(), headers=_NO_STORE)

    @app.get("/aspire_frame.jpg")
    def _aspire_frame() -> Response:
        from . import aspire_readers as A
        f = A.newest_frame()
        if f and f.exists():
            return Response(f.read_bytes(), media_type="image/jpeg", headers=_NO_STORE)
        return Response(b"no frame", status_code=404, media_type="text/plain")

    @app.get("/cam")
    def _cam() -> Response:
        """Newest LIBERO head_camera ``tick_*.jpg`` across all episode workdirs.
        Reuses :func:`aspire_readers.newest_frame`. 204 when nothing is rendering
        yet (image tags treat a 204 as 'keep the last good frame')."""
        from . import aspire_readers as A
        f = A.newest_frame()
        if f and f.exists():
            return Response(f.read_bytes(), media_type="image/jpeg", headers=_NO_STORE)
        return Response(status_code=204, headers=_NO_STORE)

    @app.get("/sessions")
    def _sessions() -> JSONResponse:
        return JSONResponse({"sessions": E._list_sessions()}, headers=_NO_STORE)

    @app.get("/frame.jpg")
    def _frame(lane: str = "A") -> Response:
        f = E._newest_frame(lane)
        if f and f.exists():
            return Response(f.read_bytes(), media_type="image/jpeg", headers=_NO_STORE)
        return Response(b"no frame", status_code=404, media_type="text/plain")

    @app.post("/message")
    async def _message(req: Request) -> JSONResponse:
        body = await req.json()
        text = str(body.get("text") or "").strip()
        session = str(body.get("session") or "direct").strip() or "direct"
        if not text:
            return JSONResponse({"error": "empty text"}, status_code=400)
        return _manager_reply(text, session)

    @app.post("/command")
    async def _command(req: Request) -> JSONResponse:
        body = await req.json()
        ok, out = E._run_command(str(body.get("cmd") or ""), str(body.get("id") or ""))
        return JSONResponse({"ok": ok, "output": out}, status_code=200 if ok else 400)

    return app


def _manager_reply(text: str, session: str):
    """Post one turn to the Manager chat and record it in the convo ring.
    Errors are surfaced to the client (keep the server up) — same contract as
    the old Handler."""
    from fastapi.responses import JSONResponse
    from roborsi.agents import manager_chat

    E._convo_add(session, "you", text)
    t0 = time.time()
    try:
        reply = manager_chat.reply(text, session=session)
    except Exception as exc:                    # surface to client, keep server up
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)
    secs = time.time() - t0
    E._convo_add(session, "manager", reply, secs)
    return JSONResponse({"reply": reply, "secs": round(secs, 1)})
