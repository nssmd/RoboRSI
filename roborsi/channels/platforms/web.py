"""Web adapter: chat and live trace on one page.

Telegram covers mobile better than a dashboard will, so this is not the mobile
story — it is the surface you open without installing anything, and the only
one that can show a rollout unfolding next to the conversation that started it.

Before this, those were two pages: chat on 8770, trace on the Feishu
status_server. Watching your own run meant opening a second tab and pasting a
chat_id. The trace was never Feishu-specific — `live_trace.LiveSession` is
keyed by chat_id and knows nothing about Lark — so it is pulled in here
directly.

Stdlib only. Adding a framework to serve one page would be a poor trade in a
repo whose deployment target is a GPU box behind a tunnel.

    GET  /                  the page
    POST /api/send          a turn, answered synchronously
    GET  /api/events        SSE: replies, files, and live trace, interleaved
    POST /api/interrupt     stop the current run
    GET  /api/frame/<chat>  latest simulator frame
    GET  /api/media/<id>    a file the agent sent, by id (Range-capable)
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import subprocess
import tempfile
import threading
import uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..core.ports import Conversation, ManagerEntry
from ..core.registry import PlatformEntry, registry

POLL_S = 0.4          # how often a tailer asks LiveSession for new events
KEEPALIVE_S = 20      # proxies drop an idle SSE stream well before a rollout ends
IDLE_EXIT_S = 60      # tailer gives up only after this long with no events AND no viewer


class WebAdapter:
    name = "web"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._host = cfg.get("host") or os.environ.get("ROBORSI_WEB_HOST", "127.0.0.1")
        self._port = int(cfg.get("port") or os.environ.get("ROBORSI_WEB_PORT", "8770"))
        # The Manager can run skills and read the repo, so a reachable page is a
        # reachable shell. Binding anywhere but loopback mints a token if none
        # was supplied: putting this behind a tunnel must not be one flag away
        # from serving an open shell to the internet.
        self._token = cfg.get("token") or os.environ.get("ROBORSI_WEB_TOKEN", "")
        if not self._token and self._host not in ("127.0.0.1", "localhost", "::1"):
            self._token = uuid.uuid4().hex[:16]
        self._server: ThreadingHTTPServer | None = None
        self._subs: list[tuple[str, queue.Queue]] = []
        self._lock = threading.Lock()
        self._tailers: dict[str, threading.Thread] = {}
        # Files are served by opaque id, never by path. Only what the agent
        # actually sent is reachable, so there is no traversal surface and the
        # page never has to show a filesystem path to a user.
        self._media: dict[str, Path] = {}
        # Rollout demos are written by cv2 with the "mp4v" fourcc, i.e. MPEG-4
        # Part 2 — which no browser decodes. The file plays fine in VLC and
        # shows as a black rectangle in Chrome, with a working duration bar,
        # so it reads as a broken player rather than an unsupported codec.
        # Transcode to H.264 on first request and keep the result.
        self._h264: dict[str, Path] = {}
        # chat_id -> the rollout workdir currently producing tick_*.jpg. The
        # agent announces it via the 3role_workspace event, which saves hunting
        # for it by run_id the way the old status page had to.
        self._workdirs: dict[str, Path] = {}
        self._chains: dict[str, list[dict]] = {}

    # ---- outbound -------------------------------------------------------
    def send_text(self, conv: Conversation, text: str) -> None:
        self._push(conv.chat_id, {"type": "text", "text": text})

    def send_card(self, conv: Conversation, card: dict[str, Any]) -> None:
        self._push(conv.chat_id, {"type": "card", "card": card})

    def send_file(self, conv: Conversation, path: Path) -> str | None:
        p = Path(path)
        media_id = uuid.uuid4().hex[:12]
        self._media[media_id] = p
        kind = ("video" if p.suffix.lower() in (".mp4", ".mov", ".webm")
                else "image" if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif")
                else "file")
        self._push(conv.chat_id, {"type": "media", "id": media_id,
                                  "name": p.name, "kind": kind,
                                  "size": p.stat().st_size if p.is_file() else 0})
        return f"/api/media/{media_id}"

    def on_event(self, conv: Conversation, event: dict[str, Any]) -> None:
        self._push(conv.chat_id, {"type": "trace", "event": event})

    def _push(self, chat_id: str, payload: dict) -> None:
        with self._lock:
            subs = [(c, q) for c, q in self._subs if c == chat_id]
        for _, q in subs:
            # Drop for a subscriber that stopped draining rather than grow
            # without bound — a backgrounded tab must not leak memory.
            if q.qsize() < 512:
                q.put(payload)

    # ---- live trace -----------------------------------------------------
    def _ensure_tailer(self, chat_id: str) -> None:
        """One thread per chat forwards LiveSession events into every SSE stream.

        Polling rather than a callback: LiveSession has no subscribe hook, and
        adding one would mean touching the agent loop's hot path to serve a
        display. 0.4 s is well under human latency and costs an index compare.
        """
        if chat_id in self._tailers:
            return

        def tail() -> None:
            from roborsi.channels.agent.feishu.live_trace import get_session
            sess = get_session(chat_id)
            cursor = 0
            idle = 0.0
            while True:
                events = sess.get_since(cursor)
                if events:
                    cursor += len(events)
                    idle = 0.0
                    for ev in events:
                        # Absorbed regardless of who is watching: /live must
                        # work when opened part-way through a run.
                        self._absorb(chat_id, ev)
                        self._push(chat_id, {"type": "trace", "event": ev})
                else:
                    idle += POLL_S
                    with self._lock:
                        watched = any(c == chat_id for c, _ in self._subs)
                    if not watched and idle > IDLE_EXIT_S:
                        break
                time.sleep(POLL_S)
            self._tailers.pop(chat_id, None)

        t = threading.Thread(target=tail, name=f"trace-{chat_id}", daemon=True)
        self._tailers[chat_id] = t
        t.start()

    def _absorb(self, chat_id: str, ev: dict) -> None:
        """Pick the two things the live view needs out of the event stream."""
        kind = str(ev.get("kind") or "")
        if kind in ("3role_workspace", "lh3role_workspace") and ev.get("path"):
            self._workdirs[chat_id] = Path(str(ev["path"]))
        elif kind == "tool_call":
            chain = self._chains.setdefault(chat_id, [])
            chain.append({"n": len(chain) + 1,
                          "tool": str(ev.get("tool") or ev.get("name") or "?"),
                          "args": ev.get("args") or {},
                          "t": ev.get("t")})
        elif kind == "tool_result":
            chain = self._chains.get(chat_id) or []
            if chain:
                chain[-1]["ok"] = ev.get("ok", True)
        elif kind in ("3role_start", "lh3role_start", "user_message"):
            self._chains[chat_id] = []     # a new turn starts a new chain

    def _latest_frame(self, chat_id: str) -> Path | None:
        """Newest simulator frame for this chat, or None while nothing runs."""
        sess_path = None
        from roborsi.channels.agent.feishu.live_trace import get_session
        cam = getattr(get_session(chat_id), "last_camera_frame", None)
        if cam and Path(cam).is_file():
            sess_path = Path(cam)
        wd = self._workdirs.get(chat_id)
        if wd and wd.is_dir():
            # The rollout drops tick_%05d.jpg as it steps physics; the highest
            # name is the newest without stat-ing every file.
            ticks = sorted(wd.rglob("tick_*.jpg"))
            if ticks:
                return ticks[-1]
        return sess_path

    # ---- inbound --------------------------------------------------------
    def run(self, manager: ManagerEntry) -> None:
        adapter = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            extra_cookie = ""

            def log_message(self, *a):  # noqa: N802 — quiet by default
                pass

            def end_headers(self):  # noqa: N802
                # Set once, on the request that carried a valid ?k=. Every route
                # ends its headers here, so no route can forget to hand it back.
                if self.extra_cookie:
                    self.send_header("Set-Cookie", self.extra_cookie)
                    self.extra_cookie = ""
                super().end_headers()

            def _json(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if not adapter._authed(self):
                    return
                p = self.path.split("?")[0]
                if p == "/api/events":
                    return adapter._serve_events(self)
                if p.startswith("/api/frame/"):
                    return adapter._serve_frame(self, p[len("/api/frame/"):])
                if p.startswith("/api/media/"):
                    return adapter._serve_media(self, p[len("/api/media/"):])
                if p == "/api/chain":
                    from urllib.parse import parse_qs, urlparse as _u
                    cid = (parse_qs(_u(self.path).query).get("chat_id") or ["web-1"])[0]
                    return self._json({"chain": adapter._chains.get(cid, [])})
                if p == "/live":
                    body = LIVE_PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                if not adapter._authed(self):
                    return
                p = self.path.split("?")[0]
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                chat_id = str(data.get("chat_id") or "web-1")

                if p == "/api/interrupt":
                    from roborsi.channels.agent.feishu.live_trace import get_session
                    get_session(chat_id).request_interrupt()
                    return self._json({"ok": True})

                if p != "/api/send":
                    return self.send_error(404)

                conv = Conversation(chat_id=chat_id, platform="web")
                adapter._ensure_tailer(chat_id)
                reply = manager.handle(conv, str(data.get("text") or ""))
                self._json({"text": reply.text,
                            "files": [str(f) for f in reply.files]})

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        where = f"http://{self._host}:{self._port}"
        print(f"[web] {where}" + (f"/?k={self._token}" if self._token else ""),
              flush=True)
        self._server.serve_forever()

    # ---- access ---------------------------------------------------------
    def _authed(self, h: BaseHTTPRequestHandler) -> bool:
        """Gate every route on the shared token; sends the failure itself.

        The token arrives once in the URL and is then kept in a cookie, so the
        page's own fetches — SSE, media, frames — need no rewriting, and the
        link a user pastes to a phone still works.
        """
        if not self._token:
            return True
        from urllib.parse import parse_qs, urlparse
        given = (parse_qs(urlparse(h.path).query).get("k") or [""])[0]
        if hmac.compare_digest(given, self._token):
            h.extra_cookie = f"rh={self._token}; Path=/; Max-Age=604800; SameSite=Lax"
            return True
        cookie = h.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "rh" and hmac.compare_digest(value, self._token):
                return True
        h.send_response(401)
        body = "需要访问令牌:在链接后加 ?k=<token>\n".encode()
        h.send_header("Content-Type", "text/plain; charset=utf-8")
        # HTTP/1.1 keep-alive: without a length the client waits for a body that
        # never ends, so a rejection would hang instead of failing.
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Connection", "close")
        h.end_headers()
        h.wfile.write(body)
        h.close_connection = True
        return False

    def _serve_events(self, h: BaseHTTPRequestHandler) -> None:
        from urllib.parse import parse_qs, urlparse
        chat_id = (parse_qs(urlparse(h.path).query).get("chat_id") or ["web-1"])[0]
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append((chat_id, q))
        self._ensure_tailer(chat_id)

        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "keep-alive")
        h.end_headers()
        try:
            while True:
                try:
                    item = q.get(timeout=KEEPALIVE_S)
                    h.wfile.write(f"data: {json.dumps(item)}\n\n".encode())
                except queue.Empty:
                    h.wfile.write(b": keepalive\n\n")
                h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with self._lock:
                self._subs = [(c, qq) for c, qq in self._subs if qq is not q]

    def _serve_media(self, h: BaseHTTPRequestHandler, media_id: str) -> None:
        """Serve a sent file by id, with range support so video can seek.

        Without Range, browsers will still play an mp4 but cannot scrub it, and
        Safari refuses to start at all.
        """
        path = self._media.get(media_id)
        if path is None or not path.is_file():
            return h.send_error(404)
        if path.suffix.lower() in (".mp4", ".mov"):
            path = self._playable(media_id, path)
        ctype = {".mp4": "video/mp4", ".mov": "video/quicktime",
                 ".webm": "video/webm", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif"}.get(path.suffix.lower(),
                                          "application/octet-stream")
        total = path.stat().st_size
        rng = h.headers.get("Range") or ""
        start, end = 0, total - 1
        if rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = int(b) if b else total - 1
            end = min(end, total - 1)

        length = end - start + 1
        h.send_response(206 if rng else 200)
        h.send_header("Content-Type", ctype)
        h.send_header("Accept-Ranges", "bytes")
        h.send_header("Content-Length", str(length))
        if rng:
            h.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        h.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                h.wfile.write(chunk)
                remaining -= len(chunk)

    def _playable(self, media_id: str, path: Path) -> Path:
        """Return a browser-decodable copy, transcoding once if needed."""
        cached = self._h264.get(media_id)
        if cached is not None and cached.is_file():
            return cached
        if _codec(path) in ("h264", "vp8", "vp9", "av1"):
            self._h264[media_id] = path
            return path
        out = Path(tempfile.gettempdir()) / f"rh-h264-{media_id}.mp4"
        if not out.is_file():
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
                 "-movflags", "+faststart", str(out)],
                check=False, timeout=300)
        # A failed transcode falls back to the original: a black player is
        # still better than a 500.
        self._h264[media_id] = out if out.is_file() else path
        return self._h264[media_id]

    def _serve_frame(self, h: BaseHTTPRequestHandler, chat_id: str) -> None:
        """Latest simulator frame, so the page can show what the robot sees."""
        path = self._latest_frame(chat_id)
        if path is None:
            return h.send_error(404)
        data = path.read_bytes()
        h.send_response(200)
        h.send_header("Content-Type", "image/jpeg")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


PAGE = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>RoboRSI</title>
<style>
:root{--bg:#0a0b0d;--panel:#101216;--line:#23272f;--fg:#e8eaed;--dim:#8b93a1;
      --me:#1d4e46;--signal:#ff8c42;--ok:#4ec9b0}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
  font:15px/1.6 ui-monospace,"SF Mono",Menlo,monospace}
#app{display:flex;flex-direction:column;height:100dvh;max-width:1100px;margin:0 auto}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;
  justify-content:space-between;align-items:center;gap:10px;font-size:12px;color:var(--dim);
  padding-top:max(12px,env(safe-area-inset-top))}
header b{color:var(--fg)}
#stop{background:transparent;border:1px solid var(--signal);color:var(--signal);
  padding:5px 11px;border-radius:6px;font:500 11px/1 inherit;cursor:pointer;display:none}
#stop.on{display:block}
#split{flex:1;display:flex;min-height:0}
#log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;
  -webkit-overflow-scrolling:touch}
#trace{width:42%;border-left:1px solid var(--line);overflow-y:auto;padding:12px 14px;
  font-size:12px;display:flex;flex-direction:column;gap:5px;background:#0c0e11}
#trace .hd{color:var(--dim);letter-spacing:.14em;text-transform:uppercase;
  font-size:10px;margin-bottom:6px;display:flex;justify-content:space-between}
.msg{max-width:86%;padding:9px 13px;border-radius:12px;background:var(--panel);
  border:1px solid var(--line);white-space:pre-wrap;word-break:break-word}
.msg.me{align-self:flex-end;background:var(--me);border-color:#2a6b60}
.ev{border-left:2px solid var(--line);padding:2px 0 2px 10px;color:var(--dim);
  white-space:pre-wrap;word-break:break-word}
.ev.tool{border-left-color:var(--signal);color:var(--fg)}
.ev.think{font-style:italic}
.ev.done{border-left-color:var(--ok);color:var(--ok)}
.ev .k{color:var(--signal);font-size:10.5px;letter-spacing:.08em}
.media{align-self:flex-start;width:100%;max-width:520px;background:var(--panel);
  border:1px solid var(--line);border-radius:12px;overflow:hidden}
.media video,.media img{display:block;width:100%;max-height:52vh;background:#000}
.media .cap{padding:7px 12px;font-size:11.5px;color:var(--dim);display:flex;
  justify-content:space-between;gap:12px;align-items:center}
.media .cap a{color:var(--ok);text-decoration:none}
.media.doc{padding:11px 14px}
.media.doc a{color:var(--ok);text-decoration:none;word-break:break-all}
form{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--line);
  padding-bottom:max(12px,env(safe-area-inset-bottom))}
input{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--fg);
  padding:11px 13px;border-radius:9px;font:inherit;min-width:0}
input:focus{outline:none;border-color:var(--dim)}
button.send{background:var(--signal);color:#12130f;border:0;padding:11px 18px;
  border-radius:9px;font:600 14px/1 inherit;cursor:pointer}
button.send:disabled{opacity:.5}
#tabs{display:none;gap:6px}
#tabs button{background:var(--panel);border:1px solid var(--line);color:var(--dim);
  padding:5px 12px;border-radius:6px;font:500 11px/1 inherit;cursor:pointer}
#tabs button.on{background:var(--signal);border-color:var(--signal);color:#12130f}
/* On a phone there is no room for two columns; tab between them instead. */
@media(max-width:760px){
  #trace{position:absolute;inset:0;width:100%;border-left:0;display:none;
    padding-top:max(12px,env(safe-area-inset-top))}
  #split{position:relative}
  body.show-trace #trace{display:flex}
  body.show-trace #log{display:none}
  #tabs{display:flex}
  .msg{max-width:94%}
}
</style></head><body><div id="app">
<header>
  <b>RoboRSI</b>
  <a id="livelink" href="#" target="_blank"
     style="color:var(--ok);text-decoration:none;display:none">📹 实时画面 →</a>
  <div id="tabs"><button id="tb_chat" class="on">对话</button><button id="tb_trace">执行</button></div>
  <span style="display:flex;gap:10px;align-items:center">
    <button id="stop">■ 中断</button><span id="st">连接中…</span>
  </span>
</header>
<div id="split">
  <div id="log"></div>
  <div id="trace"><div class="hd"><span>实时执行</span><span id="n">0</span></div></div>
</div>
<form id="f"><input id="i" placeholder="发消息给 Manager…" autocomplete="off"><button class="send" id="b">发送</button></form>
</div><script>
const log=document.getElementById('log'),trace=document.getElementById('trace'),
      st=document.getElementById('st'),stop=document.getElementById('stop'),
      cnt=document.getElementById('n');
const chat='web-'+Math.random().toString(36).slice(2,8);
let nev=0;
function add(t,cls){const d=document.createElement('div');d.className='msg '+(cls||'');
  d.textContent=t;log.appendChild(d);log.scrollTop=log.scrollHeight}
function fmt(n){return n>1048576?(n/1048576).toFixed(1)+' MB':Math.round(n/1024)+' KB'}
function media(m){
  const url='/api/media/'+m.id, d=document.createElement('div');
  if(m.kind==='video'){
    d.className='media';
    // Muted + playsinline so iOS autoplays without a tap; loop because a demo
    // clip is a few seconds and users want to re-watch the grasp.
    d.innerHTML='<video src="'+url+'" controls autoplay muted loop playsinline preload="auto"></video>'+
      '<div class="cap"><span>'+m.name+'</span><a href="'+url+'" download>下载 '+fmt(m.size)+'</a></div>';
    const v=d.querySelector('video');
    v.addEventListener('loadedmetadata',()=>{try{v.currentTime=0.1}catch(e){}},{once:true});
    v.addEventListener('canplay',()=>{v.play().catch(()=>{})},{once:true});
  }else if(m.kind==='image'){
    d.className='media';
    d.innerHTML='<img src="'+url+'" alt="'+m.name+'">'+
      '<div class="cap"><span>'+m.name+'</span><a href="'+url+'" download>下载 '+fmt(m.size)+'</a></div>';
  }else{
    d.className='media doc';
    d.innerHTML='<a href="'+url+'" download>📎 '+m.name+'</a> <span style="color:var(--dim)">'+fmt(m.size)+'</span>';
  }
  log.appendChild(d);log.scrollTop=log.scrollHeight;
}
// Event kinds the agent emits; anything unknown still renders, just unstyled.
const live=document.getElementById('livelink');
live.href='/live?chat_id='+encodeURIComponent(chat);
const CLS={thinking:'think',tool_call:'tool',tool_result:'',done:'done',
           '3role_start':'tool','3role_planned':'','3role_executed':'',
           '3role_reviewed':'done',task_result:'done',sim_progress:''};
function ev(e){
  const k=e.kind||'';
  if(k==='3role_start'||k==='lh3role_start'||k==='tool_call')live.style.display='block';
  if(k==='done')live.style.display='none';
  const d=document.createElement('div');
  d.className='ev '+(CLS[e.kind]!==undefined?CLS[e.kind]:'');
  const {kind,ts,idx,t,run_id,chat_id,...rest}=e;
  const body=Object.entries(rest).map(([k,v])=>{
    let s=typeof v==='string'?v:JSON.stringify(v);
    if(s&&s.length>200)s=s.slice(0,200)+'…';
    return k==='tool'||k==='args'?s:k+': '+s}).join(' ');
  d.innerHTML='<span class="k">'+kind+'</span>'+(body?'\\n'+body.replace(/</g,'&lt;'):'');
  trace.appendChild(d);trace.scrollTop=trace.scrollHeight;cnt.textContent=++nev;
}
const es=new EventSource('/api/events?chat_id='+chat);
es.onopen=()=>st.textContent='已连接 · '+chat;
es.onerror=()=>st.textContent='连接断开';
es.onmessage=m=>{const d=JSON.parse(m.data);
  if(d.type==='text')add(d.text);
  else if(d.type==='media')media(d);
  else if(d.type==='trace')ev(d.event||{});};
document.getElementById('f').onsubmit=async e=>{e.preventDefault();
  const i=document.getElementById('i'),b=document.getElementById('b');
  const t=i.value.trim(); if(!t)return;
  add(t,'me'); i.value=''; b.disabled=true; stop.classList.add('on');
  try{const r=await fetch('/api/send',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({chat_id:chat,text:t})});
      const j=await r.json(); if(j.text)add(j.text);
  }catch(err){add('发送失败: '+err)}
  finally{b.disabled=false;stop.classList.remove('on');i.focus()}};
stop.onclick=()=>fetch('/api/interrupt',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:chat})});
const tc=document.getElementById('tb_chat'),tt=document.getElementById('tb_trace');
tc.onclick=()=>{document.body.classList.remove('show-trace');tc.className='on';tt.className=''};
tt.onclick=()=>{document.body.classList.add('show-trace');tt.className='on';tc.className=''};
</script></body></html>"""


def _codec(path: Path) -> str:
    """Video codec name, or "" when ffprobe is unavailable or fails."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=20)
    return r.stdout.strip()


LIVE_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>RoboRSI · 实时</title>
<style>
:root{--bg:#0a0b0d;--panel:#101216;--line:#23272f;--fg:#e8eaed;--dim:#8b93a1;
      --signal:#ff8c42;--ok:#4ec9b0}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
  font:14px/1.6 ui-monospace,"SF Mono",Menlo,monospace}
#w{display:flex;flex-direction:column;height:100dvh;max-width:1200px;margin:0 auto}
header{padding:12px 16px;border-bottom:1px solid var(--line);display:flex;
  justify-content:space-between;align-items:center;font-size:12px;color:var(--dim);
  padding-top:max(12px,env(safe-area-inset-top))}
header b{color:var(--fg)} header a{color:var(--ok);text-decoration:none}
#body{flex:1;display:flex;min-height:0}
#cam{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:16px;gap:10px;background:#08090b}
#cam img{max-width:100%;max-height:74vh;border:1px solid var(--line);border-radius:8px;
  background:#000;image-rendering:auto}
#cammeta{font-size:11.5px;color:var(--dim);display:flex;gap:14px;align-items:center}
.dot{width:7px;height:7px;border-radius:50%;background:var(--dim);display:inline-block}
.dot.on{background:var(--ok);box-shadow:0 0 0 3px rgba(78,201,176,.18)}
#chain{width:400px;border-left:1px solid var(--line);overflow-y:auto;padding:14px}
#chain h3{margin:0 0 12px;font:500 11px/1 inherit;letter-spacing:.16em;
  text-transform:uppercase;color:var(--dim);display:flex;justify-content:space-between}
.step{display:grid;grid-template-columns:26px 1fr;gap:10px;padding:7px 0;
  border-bottom:1px solid rgba(35,39,47,.6)}
.step:last-child{border-bottom:0}
.step .n{color:var(--dim);font-size:11px;text-align:right;padding-top:1px}
.step .t{color:var(--fg)}
.step .a{color:var(--dim);font-size:11.5px;word-break:break-all}
.step.bad .t{color:var(--signal)}
.step.bad .t::after{content:" ✗";font-size:10px}
.step.cur{background:rgba(255,140,66,.08);margin:0 -14px;padding-left:14px;padding-right:14px}
@media(max-width:820px){
  #body{flex-direction:column}
  #chain{width:100%;border-left:0;border-top:1px solid var(--line);max-height:44vh}
  #cam img{max-height:40vh}
}
</style></head><body><div id="w">
<header><b>实时相机</b>
  <span id="cid" style="color:var(--dim)"></span>
  <a href="/" id="back">← 回到对话</a></header>
<div id="body">
  <div id="cam">
    <img id="f" alt="等待仿真画面…">
    <div id="cammeta"><span><i class="dot" id="d"></i> <span id="fps">未开始</span></span>
      <span id="ts"></span></div>
  </div>
  <div id="chain"><h3><span>工具调用链</span><span id="n">0</span></h3><div id="steps"></div></div>
</div></div><script>
const q=new URLSearchParams(location.search), chat=q.get('chat_id')||'web-1';
document.getElementById('cid').textContent=chat;
document.getElementById('back').href='/?chat_id='+encodeURIComponent(chat);
const img=document.getElementById('f'),dot=document.getElementById('d'),
      fps=document.getElementById('fps'),ts=document.getElementById('ts');
let live=0, misses=0;
// Poll the frame rather than stream MJPEG: the rollout writes one jpg every 20
// physics ticks, so a socket held open would idle most of the time, and a
// dropped poll costs one frame instead of the whole feed.
function tick(){
  const u='/api/frame/'+encodeURIComponent(chat)+'?t='+Date.now();
  const probe=new Image();
  probe.onload=()=>{img.src=u;live++;misses=0;dot.classList.add('on');
    fps.textContent='接收中';ts.textContent=new Date().toLocaleTimeString()};
  probe.onerror=()=>{if(++misses>3){dot.classList.remove('on');
    fps.textContent=live?'已结束':'等待仿真开始'}};
  probe.src=u;
}
setInterval(tick,500); tick();

const steps=document.getElementById('steps'),cnt=document.getElementById('n');
async function chain(){
  try{
    const r=await fetch('/api/chain?chat_id='+encodeURIComponent(chat));
    const {chain}=await r.json();
    cnt.textContent=chain.length;
    steps.innerHTML=chain.map((c,i)=>{
      let a=JSON.stringify(c.args||{}); if(a==='{}')a='';
      if(a.length>90)a=a.slice(0,90)+'…';
      const cur=i===chain.length-1?' cur':'', bad=c.ok===false?' bad':'';
      return '<div class="step'+cur+bad+'"><div class="n">'+c.n+'</div><div>'+
        '<div class="t">'+c.tool+'</div>'+(a?'<div class="a">'+
        a.replace(/</g,'&lt;')+'</div>':'')+'</div></div>';
    }).join('');
    steps.scrollTop=steps.scrollHeight;
  }catch(e){}
}
setInterval(chain,1000); chain();
</script></body></html>"""


registry.register(PlatformEntry(
    name="web",
    label="网页 (对话 + 实时执行)",
    build=WebAdapter,
    supports_cards=True,
    supports_files=True,
))
