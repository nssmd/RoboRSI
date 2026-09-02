"""HTML dashboard for live task monitoring.

Routes:
  /             — index, list of all runs (newest first)
  /run/<run_id> — single run page (auto-refreshes every 3s)
  /img/<path>   — serve a JPG file (last frame, demo image, etc.)
  /video/<run_id>?cam=head_camera  — render+serve mp4
  /status/<run_id>.json            — raw status JSON
"""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import task_runner as _tr


_INDEX_HTML = """<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>RoboRSI Run Monitor</title>
<meta http-equiv="refresh" content="5">
<style>
body{{font-family:system-ui;background:#0e1014;color:#d9dde4;padding:20px;margin:0}}
table{{border-collapse:collapse;width:100%;margin-top:10px}}
th,td{{border-bottom:1px solid #2a2f38;padding:8px 12px;text-align:left}}
th{{background:#171a20;color:#9aa0a8}}
a{{color:#7eb6ff;text-decoration:none}}a:hover{{text-decoration:underline}}
.ok{{color:#3ddc84;font-weight:bold}}.fail{{color:#ff5e5e;font-weight:bold}}
.run{{color:#ffd166}}.start{{color:#9aa0a8}}.err{{color:#e67e22}}
</style></head><body>
<h2>🤖 RoboRSI Run Monitor</h2>
<p>{summary}</p>
<table>
<tr><th>Run ID</th><th>Task</th><th>Seed</th><th>Status</th><th>Outcome</th>
    <th>Started</th><th>Summary</th></tr>
{rows}
</table>
</body></html>"""


_RUN_HTML = """<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>{run_id}</title>
<style>
body{{font-family:system-ui;background:#0e1014;color:#d9dde4;padding:20px;margin:0}}
h2{{margin-bottom:5px}}.sub{{color:#9aa0a8}}
.bar{{background:#1a1e26;border-radius:6px;height:18px;margin:10px 0;overflow:hidden}}
.bar>div{{height:100%;background:linear-gradient(90deg,#3ddc84,#7eb6ff);
        transition:width 0.5s;color:#0e1014;text-align:center;font-size:12px;
        line-height:18px;font-weight:bold}}
pre{{background:#1a1e26;padding:12px;border-radius:6px;overflow:auto;font-size:12px;
    max-height:300px;white-space:pre-wrap}}
.row{{display:flex;gap:20px;margin-top:14px;flex-wrap:wrap}}
.col{{flex:1;min-width:300px}}
img{{max-width:100%;border-radius:6px;border:1px solid #2a2f38}}
video{{width:100%;border-radius:6px}}
.tree{{font-size:13px;line-height:1.5}}
.node{{position:relative;padding:6px 0 6px 24px;border-left:1px solid #2a2f38;margin-left:8px}}
.node::before{{content:"";position:absolute;left:-1px;top:14px;width:18px;height:1px;background:#2a2f38}}
.badge{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;
       font-weight:bold;margin-right:6px;color:#0e1014}}
.badge.STEP{{background:#7eb6ff}}.badge.PROCEED{{background:#3ddc84}}
.badge.RETRY{{background:#ffd166}}.badge.REPLAN{{background:#48d1cc}}
.badge.RESET{{background:#c39bd3}}.badge.DONE{{background:#3ddc84}}
.badge.OK{{background:#3ddc84}}.badge.FAIL{{background:#ff5e5e;color:#fff}}
.tool-call{{background:#171a20;border-radius:6px;padding:8px 10px;margin:4px 0}}
.tool-call b{{color:#ffd166}}
.reasoning{{color:#a0b8b0;font-size:12px;margin:4px 0 0 0}}
a{{color:#7eb6ff}}
</style></head><body>
<h2 id="t_title">loading…</h2>
<div class="sub" id="t_meta"></div>
<div class="bar"><div id="t_bar" style="width:0%">0%</div></div>
<p id="t_summary"></p>
<div class="row">
<div class="col" style="flex:2">
<h3>Tool Calls <span class="sub" id="t_ncalls"></span></h3>
<div id="t_tree" class="tree"></div>
</div>
<div class="col">
<h3>Latest Frame</h3>
<img id="t_frame" alt="(no frame yet)" style="opacity:0.3">
<h3>Log Tail</h3>
<pre id="t_log">(none)</pre>
</div>
</div>
<div id="t_video"></div>
<p style="margin-top:30px"><a href="/">← all runs</a> · <a href="/status/{run_id}.json">raw json</a></p>
<script>
const runId = {run_id_js};
const CLS = {{success:'PROCEED',failed:'FAIL',running:'STEP',error:'FAIL',starting:'STEP'}};
function esc(s) {{return (s==null?'':String(s)).replace(/[&<>"']/g, c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
function detectDecision(text){{
  if(!text) return null;
  const m=String(text).match(/\\b(PROCEED|RETRY|REPLAN|RESET|DONE)\\b/);
  return m?m[1]:null;
}}
async function poll(){{
  try{{
    const r=await fetch('/status/'+runId+'.json'); const st=await r.json();
    if(st.err){{document.getElementById('t_title').innerText='not found'; return;}}
    document.getElementById('t_title').innerHTML=esc(st.task||'?')+' <span class="sub">seed='+esc(st.seed)+' · '+esc(runId)+'</span>';
    document.getElementById('t_meta').innerHTML=esc(st.started||'?')+' → '+esc(st.finished||'(running)')+' · status: <b class="badge '+(CLS[st.status]||'STEP')+'">'+esc(st.status)+'</b>'+(st.outcome?' · outcome: <b>'+esc(st.outcome)+'</b>':'')+(st.chat_id?' · <a href="/live/'+encodeURIComponent(st.chat_id)+'">🤖 see rollout decisions →</a>':'');
    const pct=Math.max(0,Math.min(100,parseInt(st.pct||0)));
    const bar=document.getElementById('t_bar'); bar.style.width=pct+'%'; bar.innerText=pct+'%';
    document.getElementById('t_summary').innerText=st.summary||'(running)';
    document.getElementById('t_log').innerText=st.log_tail||'(none)';
    // Build the trace: prefer final episode_summary.vlm_trace; otherwise
    // assemble live from /live_events/<chat_id> filtering by run_id.
    const ep=st.episode_summary||{{}};
    let trace=ep.vlm_trace||[];
    if((!trace.length || st.status==='running') && st.chat_id){{
      try{{
        const lr=await fetch('/live_events/'+encodeURIComponent(st.chat_id)+'?since=0');
        const ld=await lr.json();
        const live=[];
        let started=false;
        for(const e of (ld.events||[])){{
          if(e.kind==='inner_start' && e.run_id===runId){{ started=true; continue; }}
          if(e.kind==='inner_end' && e.run_id===runId){{ started=false; continue; }}
          if(!started) continue;
          if(e.kind==='inner_tool_call'){{
            live.push({{step:e.step, tool_call:{{tool:e.tool,args:e.args||{{}}}}, reasoning:e.reasoning||'', _t:e.t}});
          }} else if(e.kind==='inner_tool_result'){{
            const last=live[live.length-1];
            if(last && last.step===e.step) last.result={{ok:e.ok, preview:e.preview}};
          }}
        }}
        if(live.length) trace=live;
      }}catch(err){{console.warn('live fetch failed',err);}}
    }}
    document.getElementById('t_ncalls').innerText='('+trace.length+(st.status==='running'?' · live':'')+')';
    let html='';
    for(const t of trace){{
      const tc=t.tool_call||{{}};
      const reason=t.reasoning||'';
      const det=detectDecision(reason);
      const badge=det || ('STEP '+(t.step!=null?t.step:'?'));
      const cls=det || 'STEP';
      const res=t.result||{{}};
      let okBadge='';
      if(typeof res==='object' && 'ok' in res) okBadge=' <span class="badge '+(res.ok?'OK':'FAIL')+'">'+(res.ok?'OK':'FAIL')+'</span>';
      const argStr=JSON.stringify(tc.args||{{}}).slice(0,200);
      html+='<div class="node tool-call"><span class="badge '+cls+'">'+esc(badge)+'</span><b>'+esc(tc.tool||'?')+'</b>('+esc(argStr)+')'+okBadge;
      if(reason) html+='<div class="reasoning">💭 '+esc(reason.slice(0,400))+'</div>';
      html+='</div>';
    }}
    if(!html) html='<p class="sub">(no trace yet)</p>';
    document.getElementById('t_tree').innerHTML=html;
    const rd=ep.dir;
    if(rd){{
      const f=document.getElementById('t_frame');
      f.src='/latest_frame/'+runId+'?t='+Date.now();
      f.style.opacity=1;
    }}
    if(st.status==='success'||st.status==='failed'){{
      const v=document.getElementById('t_video');
      if(!v.innerHTML) v.innerHTML='<h3>Demo Video</h3><video controls preload="auto" src="/video/'+runId+'?cam=head_camera"></video>';
    }}
  }}catch(e){{console.error(e);}}
}}
setInterval(poll,1500); poll();
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        if p == "/" or p == "/index":
            self._html(_render_index())
        elif p.startswith("/run/"):
            self._html(_render_run(p[5:]))
        elif p.startswith("/live/"):
            self._html(_render_live(p[len("/live/"):]))
        elif p.startswith("/live_events/"):
            cid = p[len("/live_events/"):]
            since = int((q.get("since") or ["0"])[0])
            from roborsi.store import trace_db as _td
            from . import live_trace
            events = _td.list_events(cid, since_id=since)
            sess = live_trace.get_session(cid)   # in-memory for live-only flags
            self._json({
                "events": events,
                "interrupted": sess.interrupt_requested,
                "busy": sess.busy_since is not None,
                "last_camera_frame": sess.last_camera_frame,
            })
        elif p.startswith("/status/") and p.endswith(".json"):
            rid = p[len("/status/"):-len(".json")]
            st = _build_run_dict(rid)
            self._json(st or {"err": "not found"})
        elif p.startswith("/api/runs"):
            from roborsi.store import trace_db as _td
            runs = _td.list_runs(
                limit=int((q.get("limit") or ["100"])[0]),
                skill=(q.get("skill") or [None])[0],
                outcome=(q.get("outcome") or [None])[0],
                chat_id=(q.get("chat_id") or [None])[0],
                status=(q.get("status") or [None])[0])
            self._json({"runs": runs})
        elif p.startswith("/api/steps"):
            from roborsi.store import trace_db as _td
            steps = _td.list_steps(
                run_id=(q.get("run_id") or [None])[0],
                chat_id=(q.get("chat_id") or [None])[0],
                layer=(q.get("layer") or [None])[0],
                since_ts=float((q.get("since_ts") or ["0"])[0]),
                limit=int((q.get("limit") or ["1000"])[0]))
            self._json({"steps": steps})
        elif p.startswith("/api/proposals"):
            from roborsi.store import trace_db as _td
            props = _td.list_proposals(
                skill=(q.get("skill") or [None])[0],
                status=(q.get("status") or [None])[0],
                limit=int((q.get("limit") or ["100"])[0]))
            self._json({"proposals": props})
        elif p.startswith("/latest_frame/"):
            rid = p[len("/latest_frame/"):]
            st = _build_run_dict(rid) or {}
            rd = (st.get("episode_summary") or {}).get("dir")
            if rd:
                import glob
                files = sorted(glob.glob(f"{rd}/frames/head_camera/*.jpg"))
                if files:
                    self._file(Path(files[-1])); return
            self._html("no frame", status=404)
        elif p.startswith("/img"):
            path = Path(q.get("p", [""])[0])
            self._file(path)
        elif p.startswith("/file"):
            # Serve any file by absolute path (mp4, jpg, etc).
            path = Path(q.get("p", [""])[0])
            self._file(path)
        elif p.startswith("/video/"):
            rid = p[len("/video/"):]
            cam = (q.get("cam") or ["head_camera"])[0]
            mp4 = _tr.render_demo_video(rid, camera=cam)
            if mp4 and mp4.exists():
                self._file(mp4)
            else:
                self._html("<p>video not ready</p>", status=404)
        else:
            self._html("<p>not found</p>", status=404)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        if p.startswith("/interrupt/"):
            cid = p[len("/interrupt/"):]
            from . import live_trace
            sess = live_trace.get_session(cid)
            sess.request_interrupt()
            self._json({"ok": True, "msg": "interrupt requested"})
            return
        self._html("not found", status=404)

    def _html(self, body: str, status: int = 200) -> None:
        b = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def _json(self, obj) -> None:
        b = json.dumps(obj, indent=2, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def _file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._html("not found", status=404); return
        mt = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mt)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)


def _render_index() -> str:
    from roborsi.store import trace_db as _td
    runs = _td.list_runs(limit=50)
    from . import live_trace
    sessions = live_trace.list_sessions()
    live_html = ""
    if sessions:
        rows = []
        for s in sessions:
            busy = "🟢 BUSY" if s.busy_since else "⚪ idle"
            rows.append(f"<li><a href='/live/{s.chat_id}'>{s.chat_id}</a> "
                         f"— {busy} — last: {s.last_user_message[:60]}</li>")
        live_html = ("<h3>🤖 Live agent sessions</h3><ul>"
                      + "".join(rows) + "</ul>")
    if not runs:
        return _INDEX_HTML.format(summary=live_html + "no runs yet", rows="")
    rows = []
    for r in runs:
        cls = {"success": "ok", "failed": "fail", "running": "run",
                "starting": "start", "error": "err"}.get(r.get("status") or "", "")
        rows.append(
            f"<tr><td><a href='/run/{r.get('id')}'>{r.get('id')}</a></td>"
            f"<td>{r.get('task','')}</td><td>{r.get('seed','')}</td>"
            f"<td class='{cls}'>{r.get('status','')}</td>"
            f"<td>{r.get('outcome','') or ''}</td>"
            f"<td>{r.get('started_at','')}</td><td>{r.get('summary','') or ''}</td></tr>")
    return _INDEX_HTML.format(summary=live_html + f"{len(runs)} runs (sqlite)",
                                rows="\n".join(rows))


def _render_run(run_id: str) -> str:
    import json as _j
    return _RUN_HTML.format(run_id=run_id, run_id_js=_j.dumps(run_id))


def _build_run_dict(run_id: str) -> dict | None:
    """Assemble the JSON shape that /run/<id> JS expects, sourced from sqlite.

    Maps `runs` columns to the legacy status.json field names and stitches
    inner steps into `episode_summary.vlm_trace` so the existing template
    keeps rendering without changes."""
    from roborsi.store import trace_db as _td
    run = _td.get_run(run_id)
    if not run:
        return None
    out: dict = {
        "run_id": run_id,
        "task":       run.get("task"),
        "seed":       run.get("seed"),
        "status":     run.get("status"),
        "outcome":    run.get("outcome"),
        "started":    run.get("started_at"),
        "finished":   run.get("finished_at"),
        "summary":    run.get("summary"),
        "log_tail":   run.get("log_tail"),
        "chat_id":    run.get("chat_id"),
        "video_path": run.get("video_path"),
    }
    status = run.get("status") or ""
    out["pct"] = 100 if status in ("success", "failed", "error") else 50
    # Prefer the cached episode_summary blob if task_runner stored it.
    if run.get("episode_summary_json"):
        try:
            out["episode_summary"] = json.loads(run["episode_summary_json"])
        except json.JSONDecodeError:
            out["episode_summary"] = {}
    else:
        out["episode_summary"] = {}
    # Always overlay vlm_trace from the steps table — gives a live view
    # while the run is still in progress.
    inner = _td.list_steps(run_id=run_id, layer="inner")
    if inner:
        out["episode_summary"].setdefault("vlm_trace",
                                            _merge_inner_steps(inner))
    return out


def _merge_inner_steps(rows: list[dict]) -> list[dict]:
    """Pair tool-call rows with their matching tool-result rows by (idx, tool)."""
    by_idx: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in rows:
        key = (r.get("idx"), r.get("tool"))
        entry = by_idx.get(key)
        if entry is None:
            entry = {"step": r.get("idx"), "tool_call": {"tool": r.get("tool")},
                     "reasoning": ""}
            by_idx[key] = entry
            order.append(key)
        if r.get("args_json"):
            try:
                entry["tool_call"]["args"] = json.loads(r["args_json"])
            except json.JSONDecodeError:
                entry["tool_call"]["args"] = {}
        if r.get("reasoning"):
            entry["reasoning"] = r["reasoning"]
        if r.get("result_ok") is not None:
            entry["result"] = {
                "ok": bool(r["result_ok"]),
                "preview": r.get("result_preview") or "",
            }
    return [by_idx[k] for k in order]


_LIVE_HTML = """<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>Live agent — {chat_id}</title>
<style>
body{{font-family:system-ui;background:#0e1014;color:#d9dde4;padding:20px;margin:0}}
h2{{margin-bottom:5px}}.sub{{color:#9aa0a8}}
.row{{display:flex;gap:20px;margin-top:14px;flex-wrap:wrap}}
.col{{min-width:340px}}
.btn{{background:#ff5e5e;color:white;border:none;padding:10px 20px;
      border-radius:6px;font-size:14px;cursor:pointer;font-weight:bold}}
.btn:hover{{background:#ff3838}}.btn:disabled{{background:#444;cursor:not-allowed}}
.busy{{background:#3a2d10;color:#ffd166;padding:6px 10px;border-radius:6px;
       display:inline-block;margin-bottom:10px;font-size:13px}}
.idle{{background:#102a1c;color:#3ddc84;padding:6px 10px;border-radius:6px;
       display:inline-block;margin-bottom:10px;font-size:13px}}
img{{max-width:100%;border-radius:6px;border:1px solid #2a2f38}}
.tree{{font-size:13px;line-height:1.5}}
.node{{position:relative;padding:6px 0 6px 24px;border-left:1px solid #2a2f38;margin-left:8px}}
.node::before{{content:"";position:absolute;left:-1px;top:14px;width:18px;height:1px;background:#2a2f38}}
.node.root{{border-left:none;padding-left:0;margin-left:0}}
.node.root::before{{display:none}}
.badge{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:11px;
       font-weight:bold;margin-right:6px;color:#0e1014}}
.badge.PROCEED{{background:#3ddc84}}.badge.RETRY{{background:#ffd166}}
.badge.REPLAN{{background:#7eb6ff}}.badge.RESET{{background:#c39bd3}}
.badge.DONE{{background:#3ddc84}}.badge.START{{background:#48d1cc}}
.badge.STOP{{background:#ff5e5e;color:white}}
.tool-call{{background:#171a20;border-radius:6px;padding:8px 10px;margin:4px 0}}
.tool-call b{{color:#ffd166}}
.thinking{{background:#1d1623;border-radius:6px;padding:8px 10px;margin:4px 0;border-left:3px solid #c39bd3}}
.t-result{{background:#13201d;border-radius:6px;padding:6px 10px;margin:2px 0 4px 24px;font-size:12px;color:#a0b8b0}}
.task-result{{background:#1a1e26;border-radius:6px;padding:12px;margin:8px 0;border-left:4px solid #ffd166}}
.task-result.success{{border-left-color:#3ddc84}}
.task-result.failed,.task-result.error{{border-left-color:#ff5e5e}}
.task-result video{{width:100%;max-width:420px;border-radius:6px;margin-top:6px}}
pre{{background:#0a0c10;padding:6px;border-radius:3px;overflow:auto;font-size:11px;max-height:160px;color:#bbb;margin:4px 0 0 0;white-space:pre-wrap}}
.ts{{color:#666;font-size:10px;margin-right:6px}}
</style></head><body>
<h2>🤖 Live Agent View</h2>
<div class="sub">chat: <code>{chat_id}</code></div>
<div id="status_badge"></div>
<button id="interrupt_btn" class="btn">⏸ INTERRUPT</button>
<span class="sub">(stops at next checkpoint)</span>
<div class="row">
<div class="col" style="flex:2">
<h3>Agent Action Tree <span class="sub" id="evt_count"></span></h3>
<div id="tree" class="tree"></div>
</div>
<div class="col" style="flex:1">
<h3>Current Frame</h3>
<img id="frame" alt="(no frame yet)" style="opacity:0.3">
</div>
</div>
<script>
const chatId = {chat_id_js};
let since = 0;
let events = [];
const tree = document.getElementById('tree');
const statusBadge = document.getElementById('status_badge');
const btn = document.getElementById('interrupt_btn');
const frame = document.getElementById('frame');
const evtCount = document.getElementById('evt_count');
btn.onclick = async () => {{
  btn.disabled = true; btn.innerText = '⏳ requesting…';
  await fetch('/interrupt/' + chatId, {{method:'POST'}});
  setTimeout(() => {{ btn.disabled = false; btn.innerText = '⏸ INTERRUPT'; }}, 2000);
}};
function detectDecision(text) {{
  if (!text) return null;
  const m = text.match(/\b(PROCEED|RETRY|REPLAN|RESET|DONE)\b/);
  return m ? m[1] : null;
}}
function esc(s) {{
  return (s||'').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}
function renderTree() {{
  // Scope: only show events from the latest user_message onwards (current task).
  let startIdx = 0;
  for (let i = events.length - 1; i >= 0; i--) {{
    if (events[i].kind === 'user_message') {{ startIdx = i; break; }}
  }}
  const scoped = events.slice(startIdx);
  let html = '';
  let pending = 'START';
  for (const e of scoped) {{
    const ts = new Date(e.t*1000).toLocaleTimeString();
    if (e.kind === 'user_message') {{
      pending = 'START';
      html += '<div class="node root"><span class="ts">'+ts+'</span><b>👤 USER</b>: ' + esc((e.text||'').slice(0,300)) + '</div>';
    }} else if (e.kind === 'opus_thinking') {{
      const det = detectDecision(e.text);
      if (det) pending = det;
      html += '<div class="node thinking"><span class="ts">'+ts+'</span>🧠 ' + esc((e.text||'').slice(0,1500)) + '</div>';
    }} else if (e.kind === 'tool_call') {{
      const badge = '<span class="badge '+pending+'">'+pending+'</span>';
      html += '<div class="node tool-call"><span class="ts">'+ts+'</span>'+badge+'<b>'+esc(e.name)+'</b>(' + esc(JSON.stringify(e.args).slice(0,200)) + ')</div>';
      pending = 'PROCEED';
    }} else if (e.kind === 'tool_result') {{
      html += '<div class="t-result"><span class="ts">'+ts+'</span>↳ '+esc(e.name)+': <pre>'+esc((e.result_preview||'').slice(0,500))+'</pre></div>';
    }} else if (e.kind === 'inner_start') {{
      html += '<div class="node" style="margin-left:24px"><span class="ts">'+ts+'</span><span class="badge START">INNER START</span> <code>'+esc(e.skill||'?')+'</code> seed='+esc(e.seed)+' run=<a href="/run/'+encodeURIComponent(e.run_id||'')+'" target="_blank">'+esc(e.run_id||'?')+'</a></div>';
    }} else if (e.kind === 'inner_tool_call') {{
      const det = detectDecision(e.reasoning);
      const badge = det || ('STEP '+(e.step!=null?e.step:'?'));
      const cls = det || 'STEP';
      let inner = '<span class="badge '+cls+'">'+esc(badge)+'</span><b>'+esc(e.tool||'?')+'</b>(' + esc(JSON.stringify(e.args||{{}}).slice(0,160)) + ')';
      if (e.reasoning) inner += '<div class="reasoning">💭 '+esc(String(e.reasoning).slice(0,300))+'</div>';
      html += '<div class="node tool-call" style="margin-left:24px;border-left-color:#48d1cc"><span class="ts">'+ts+'</span>'+inner+'</div>';
    }} else if (e.kind === 'inner_tool_result') {{
      const okBadge = e.ok===true ? '<span class="badge OK">OK</span>' : (e.ok===false ? '<span class="badge FAIL">FAIL</span>' : '');
      html += '<div class="t-result" style="margin-left:48px"><span class="ts">'+ts+'</span>↳ '+okBadge+' '+esc(e.tool||'?')+': <pre>'+esc((e.preview||'').slice(0,400))+'</pre></div>';
    }} else if (e.kind === 'inner_end') {{
      // silent — task_result event will summarize
    }} else if (e.kind === 'task_result') {{
      const cls = e.status === 'success' ? 'success' : (e.status || '');
      const sym = e.status === 'success' ? '✓' : '✗';
      let h2 = '<h4 style="margin:0 0 4px 0">'+sym+' '+esc(e.task||'?')+' — '+esc(e.status||'?')+'</h4>';
      h2 += '<div class="sub">skill: <code>'+esc(e.skill||'?')+'</code> · outcome: <code>'+esc(e.outcome||'?')+'</code>';
      if (e.run_id) h2 += ' · <a href="/run/'+encodeURIComponent(e.run_id)+'" target="_blank">🔍 inner call tree →</a>';
      h2 += '</div>';
      h2 += '<div>'+esc(e.summary||'')+'</div>';
      if (e.video_path) h2 += '<video controls preload="metadata" src="/file?p='+encodeURIComponent(e.video_path)+'"></video>';
      html += '<div class="node task-result '+cls+'"><span class="ts">'+ts+'</span>'+h2+'</div>';
    }} else if (e.kind === 'interrupted') {{
      html += '<div class="node"><span class="ts">'+ts+'</span><span class="badge STOP">INTERRUPTED</span> '+esc(e.reason||'')+'</div>';
    }} else if (e.kind === 'done') {{
      const txt = e.final_text || '';
      html += '<div class="node"><span class="ts">'+ts+'</span><span class="badge DONE">DONE</span><div style="margin-top:6px"><pre style="background:#0a0c10;padding:10px;border-radius:6px;max-height:none;white-space:pre-wrap;color:#d9dde4;font-size:12px">'+esc(txt)+'</pre></div></div>';
    }}
  }}
  tree.innerHTML = html;
}}
async function poll() {{
  try {{
    const r = await fetch('/live_events/' + chatId + '?since=' + since);
    const d = await r.json();
    if (d.events.length) {{
      events = events.concat(d.events);
      since = d.events[d.events.length-1].idx;
      renderTree();
      evtCount.innerText = '('+events.length+')';
    }}
    if (d.busy) {{
      statusBadge.innerHTML = '<div class="busy">⚙ agent BUSY</div>';
      btn.disabled = false;
    }} else {{
      statusBadge.innerHTML = '<div class="idle">✓ agent IDLE</div>';
      btn.disabled = true;
    }}
    if (d.last_camera_frame) {{
      frame.src = '/img?p=' + encodeURIComponent(d.last_camera_frame) + '&t=' + Date.now();
      frame.style.opacity = 1;
    }}
  }} catch(err) {{ console.error(err); }}
}}
setInterval(poll, 1500);
poll();
</script>
</body></html>"""


def _render_live(chat_id: str) -> str:
    import json as _j
    return _LIVE_HTML.format(chat_id=chat_id, chat_id_js=_j.dumps(chat_id))


def serve(port: int = 8770, host: str = "0.0.0.0") -> None:
    httpd = ThreadingHTTPServer((host, port), _Handler)
    print(f"[roborsi-monitor] http://{host}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[roborsi-monitor] stopped.")
