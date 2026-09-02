#!/usr/bin/env python3
"""Live HTML dashboard for a RoboRSI 3-role run.

Tails a run's stdout log and serves a self-refreshing web page so a user can
WATCH the agent work in real time (Manager decision -> Planner plan -> Engineer
steps + thinking -> Reviewer verdict) instead of waiting for the final result.
Token usage is shown in a side panel, not dumped to the user.

Usage:  python scripts/live_dashboard.py --log /tmp/pb/bell.log --port 8799
Then expose with:  cloudflared tunnel --url http://localhost:8799
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_PATH = "/tmp/pb/run.log"

PAGE = r"""<!doctype html><html><head><meta charset="utf-8">
<title>RoboRSI · live run</title>
<style>
 :root{--bg:#0d1117;--card:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
       --ok:#3fb950;--bad:#f85149;--acc:#58a6ff;--think:#d2a8ff;--plan:#f0c674}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
   font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
 header{position:sticky;top:0;background:#010409;border-bottom:1px solid var(--bd);
   padding:10px 18px;display:flex;align-items:center;gap:14px;z-index:5}
 header h1{font-size:15px;margin:0;letter-spacing:.02em}
 #status{font-size:12px;color:var(--mut)} #status b{color:var(--acc)}
 .wrap{display:flex;gap:16px;padding:16px;max-width:1200px;margin:0 auto}
 #feed{flex:1;min-width:0} #side{width:250px;flex:none}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:8px;
   padding:10px 14px;margin:0 0 10px}
 .card .h{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
   color:var(--mut);margin-bottom:4px}
 .phase{border-left:3px solid var(--acc)} .phase.plan{border-left-color:var(--plan)}
 .goal{color:var(--plan);font-weight:600}
 .sg{color:var(--mut);margin:2px 0 2px 14px;font-size:13px}
 .step{border-left:3px solid #30475e}
 .step .tool{color:var(--acc);font-family:ui-monospace,Menlo,monospace;font-size:13px}
 .step .res{font-size:12px;color:var(--mut);margin-top:3px}
 .ok{color:var(--ok)} .bad{color:var(--bad)}
 .think{border-left:3px solid var(--think);background:#1c162b}
 .think .t{color:var(--think)}
 .final{font-size:16px;font-weight:700;padding:14px}
 .final.ok{border:1px solid var(--ok)} .final.bad{border:1px solid var(--bad)}
 #side .card{position:sticky;top:64px}
 .tok{display:flex;justify-content:space-between;font-size:13px;margin:3px 0}
 .tok b{color:var(--acc);font-variant-numeric:tabular-nums}
 .sel{font-size:12px;color:var(--mut);font-family:ui-monospace,monospace;word-break:break-word}
 .dim{color:var(--mut);font-size:12px}
</style></head><body>
<header><h1>🤖 RoboRSI · live run</h1>
  <span id="status">connecting…</span></header>
<div class="wrap">
 <div id="feed"></div>
 <div id="side">
  <div class="card"><div class="h">Tokens</div>
    <div class="tok"><span>prompt</span><b id="tp">0</b></div>
    <div class="tok"><span>completion</span><b id="tc">0</b></div>
    <div class="tok"><span>total</span><b id="tt">0</b></div>
    <div class="tok"><span>VLM calls</span><b id="tn">0</b></div>
    <div class="h" style="margin-top:12px">Skills shortlisted</div>
    <div class="sel" id="skills">—</div>
  </div>
 </div>
</div>
<script>
let off=0, tp=0,tc=0,tt=0,tn=0;
const feed=document.getElementById('feed');
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function add(html){const d=document.createElement('div');d.innerHTML=html;
  feed.appendChild(d.firstElementChild);window.scrollTo(0,document.body.scrollHeight)}
function render(line){
  let m;
  if(m=line.match(/^\[3role\] 🧠 Planner planning (.+?) \.\.\./))
    return add(`<div class="card phase plan"><div class="h">🧠 Planner</div><div>planning <b>${esc(m[1])}</b>…</div></div>`);
  if(m=line.match(/^\[3role\] 🧠 Planner goal: (.+)/))
    return add(`<div class="card phase plan"><div class="h">Goal</div><div class="goal">${esc(m[1])}</div></div>`);
  if(m=line.match(/^\[3role\]\s+(\d+)\. (.+)/))
    return add(`<div class="card sg">${m[1]}. ${esc(m[2])}</div>`);
  if(m=line.match(/^\[3role\] 🔍 Reviewer (.+)/))
    return add(`<div class="card phase"><div class="h">🔍 Reviewer</div><div>${esc(m[1])}</div></div>`);
  if(m=line.match(/^\[manager\] (.+)/))
    return add(`<div class="card phase"><div class="h">🧭 Manager</div><div>${esc(m[1])}</div></div>`);
  if(m=line.match(/^\[rollout\] step=(\d+) → (.+)/))
    return add(`<div class="card step"><div class="h">step ${m[1]}</div><div class="tool">→ ${esc(m[2])}</div></div>`);
  if(m=line.match(/^\[rollout\] step=(\d+) 💭 (.+)/))
    return add(`<div class="card think"><div class="h">💭 step ${m[1]} thinking</div><div class="t">${esc(m[2])}</div></div>`);
  if(m=line.match(/^\[rollout\] step=(\d+) tool=(\S+) dispatched in ([\d.]+)s ok=(\w+)/)){
    const good=m[4]==='True';
    return add(`<div class="card step"><div class="res">${esc(m[2])} · ${m[3]}s · <span class="${good?'ok':'bad'}">ok=${m[4]}</span></div></div>`);
  }
  if(m=line.match(/^\[skill-selector\].*picked \d+\): (\[.*\])/)){
    document.getElementById('skills').textContent=m[1];return}
  if(m=line.match(/^\[tokens\] (\{.*\})/)){
    try{const j=JSON.parse(m[1]);tp+=j.prompt||0;tc+=j.completion||0;tt+=j.total||0;tn+=1;
      document.getElementById('tp').textContent=tp.toLocaleString();
      document.getElementById('tc').textContent=tc.toLocaleString();
      document.getElementById('tt').textContent=tt.toLocaleString();
      document.getElementById('tn').textContent=tn}catch(e){}
    return}
  if(m=line.match(/^bot> (✓|✗) (.+)/)){
    const good=m[1]==='✓';
    return add(`<div class="card final ${good?'ok':'bad'}">${m[1]} ${esc(m[2])}</div>`);
  }
  if(/^====+ 3-role/.test(line))
    return add(`<div class="card dim">${esc(line.replace(/=/g,'').trim())}</div>`);
}
async function poll(){
  try{
    const r=await fetch('/events?since='+off);const j=await r.json();
    off=j.offset;
    for(const ln of j.lines) render(ln);
    document.getElementById('status').innerHTML=j.running?
      'status: <b>running</b> · live':'status: finished';
  }catch(e){document.getElementById('status').textContent='status: disconnected'}
  setTimeout(poll, 1000);
}
poll();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/events"):
            since = 0
            if "since=" in self.path:
                try:
                    since = int(self.path.split("since=")[1].split("&")[0])
                except ValueError:
                    since = 0
            lines: list[str] = []
            offset = since
            if os.path.exists(LOG_PATH):
                size = os.path.getsize(LOG_PATH)
                if since > size:      # log rotated/truncated → restart
                    since = 0
                with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(since)
                    chunk = f.read()
                    offset = f.tell()
                # keep only complete lines
                if chunk and not chunk.endswith("\n"):
                    cut = chunk.rfind("\n")
                    if cut >= 0:
                        offset = since + len(chunk[:cut + 1].encode("utf-8"))
                        chunk = chunk[:cut + 1]
                    else:
                        chunk = ""
                        offset = since
                lines = [ln for ln in chunk.split("\n") if ln.strip()]
            running = os.path.exists(LOG_PATH + ".running")
            out = json.dumps({"offset": offset, "lines": lines,
                              "running": running}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        self.send_response(404)
        self.end_headers()


def main() -> int:
    global LOG_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    LOG_PATH = args.log
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    bound = srv.server_address[1]
    print(f"[dashboard] PORT={bound}", flush=True)
    print(f"[dashboard] serving {LOG_PATH} at http://127.0.0.1:{bound}",
          flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
