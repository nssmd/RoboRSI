"""Local HTML review server for VLM-authored skill proposals.

Run via:  roborsi-sim skill review-server [--port 8765]
Then open  http://localhost:8765  in a browser (or share the URL via
Feishu/Slack/etc) — every pending proposal renders with code highlighting
and Approve/Reject buttons that flip the queue file inline.
"""
from __future__ import annotations

import html
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


_QUEUE_DIR = Path.home() / ".roborsi" / "skill_review"


_INDEX_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>RoboRSI — Skill Review</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
         margin: 0; padding: 24px; background: #f6f7fb; color: #222; }
  h1 { font-size: 20px; margin: 0 0 16px; }
  .empty { padding: 32px; background: #fff; border-radius: 8px;
           text-align: center; color: #999; }
  .card { background: #fff; border-radius: 10px; padding: 18px;
          margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
  .meta { color: #666; font-size: 13px; margin-bottom: 8px; }
  .name { color: #1a73e8; font-weight: 600; font-size: 16px; }
  .doc { font-size: 14px; margin: 8px 0 12px; padding: 10px;
         background: #f0f6ff; border-left: 3px solid #1a73e8; border-radius: 3px; }
  pre.code { background: #1e1e1e; color: #d4d4d4; padding: 14px; border-radius: 6px;
             overflow-x: auto; font-size: 13px; line-height: 1.5;
             font-family: 'SFMono-Regular', 'Menlo', monospace; }
  .keyword { color: #569cd6; } .string { color: #ce9178; } .comment { color: #6a9955; }
  .actions { margin-top: 14px; display: flex; gap: 8px; align-items: center; }
  .btn { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer;
         font-size: 14px; font-weight: 500; }
  .btn-approve { background: #1e8e3e; color: white; }
  .btn-reject { background: #d93025; color: white; }
  .note-input { flex: 1; padding: 8px 10px; border: 1px solid #ddd;
                border-radius: 6px; font-size: 14px; }
  .test-args { font-family: monospace; font-size: 12px; color: #555;
               background: #f5f5f5; padding: 4px 8px; border-radius: 3px; }
  .test-result { font-family: monospace; font-size: 12px; color: #1a4;
               background: #f0fff4; padding: 6px 10px; border-radius: 3px;
               margin: 6px 0; word-break: break-all; }
  .demo-imgs { display: flex; gap: 12px; margin: 12px 0; }
  .demo-imgs figure { margin: 0; flex: 1; max-width: 280px; }
  .demo-imgs img { width: 100%; border: 1px solid #ddd; border-radius: 4px;
                   display: block; }
  .demo-imgs figcaption { font-size: 12px; color: #666; text-align: center;
                          padding-top: 4px; }
  .audit-block { margin: 12px 0; padding: 12px; background: #fafbfc;
                 border-radius: 6px; border-left: 3px solid #5b6;
                 font-size: 13px; }
  .audit-verdict { font-weight: 600; font-size: 14px; margin-bottom: 8px; }
  .audit-verdict.approve { color: #1e8e3e; }
  .audit-verdict.request_changes { color: #b8860b; }
  .audit-verdict.reject { color: #d93025; }
  .audit-row { display: flex; gap: 12px; margin: 4px 0;
               font-family: 'SFMono-Regular', monospace; font-size: 12px; }
  .audit-row .dim { width: 110px; color: #555; }
  .audit-row .score { width: 30px; text-align: right; font-weight: 600; }
  .audit-row .concerns { flex: 1; color: #444; }
  .audit-summary { font-style: italic; color: #555; margin: 8px 0; }
  .audit-fixes { margin-top: 8px; padding-left: 18px; }
  .audit-fixes li { margin: 2px 0; }
  .btn-audit { background: #5b6; color: white; }
  .reload { background: #f1f3f4; color: #444; padding: 6px 12px;
            border-radius: 6px; text-decoration: none; font-size: 13px; }
</style>
</head><body>
<h1>RoboRSI — Pending Skill Proposals
  <a class="reload" href="/" style="float:right;">↻ refresh</a>
</h1>
{cards}
</body></html>"""


_CARD_TEMPLATE = """<div class="card">
  <div class="meta">
    <span class="name">{name}</span>
    &middot; task: <code>{task}</code>
    &middot; submitted: {ts}
    &middot; id: <code>{id}</code>
  </div>
  <div class="doc">{doc}</div>
  {test_args_html}
  {test_result_html}
  {demo_images_html}
  <pre class="code"><code>{code}</code></pre>
  {audit_html}
  <form method="POST" action="/audit" class="actions" style="margin-bottom:6px;">
    <input type="hidden" name="id" value="{id}">
    <button class="btn btn-audit" type="submit">🤖 AI Audit</button>
    <span style="color:#888;font-size:12px;">Calls VLM to review safety / correctness / robustness</span>
  </form>
  <form method="POST" action="/decide" class="actions">
    <input type="hidden" name="id" value="{id}">
    <input class="note-input" name="note" placeholder="optional reviewer note (or rejection reason)">
    <button class="btn btn-approve" name="action" value="approve">✓ Approve</button>
    <button class="btn btn-reject" name="action" value="reject">✗ Reject</button>
  </form>
</div>"""


class _ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass    # silence access logs

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._render_index()
        elif path == "/health":
            self._send(200, "ok", "text/plain")
        elif path.startswith("/img/"):
            self._serve_image(path[len("/img/"):])
        else:
            self._send(404, "not found", "text/plain")

    def _serve_image(self, raw_path: str):
        """Serve a registered demo image. Path is the abs filesystem path
        (URL-encoded). Restrict to /tmp/skill_review_imgs/ for safety."""
        from urllib.parse import unquote
        full = unquote(raw_path)
        # Whitelist parent dir to avoid path traversal.
        allowed = Path("/tmp/skill_review_imgs").resolve()
        try:
            target = Path(full).resolve()
        except OSError:
            self._send(400, "bad path", "text/plain"); return
        if not str(target).startswith(str(allowed)) or not target.exists():
            self._send(404, "not found", "text/plain"); return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/decide":
            self._handle_decide()
            return
        if path == "/audit":
            self._handle_audit()
            return
        self._send(404, "not found", "text/plain")

    def _handle_decide(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        form = parse_qs(body)
        proposal_id = form.get("id", [""])[0]
        action = form.get("action", [""])[0]
        note = form.get("note", [""])[0]
        if action not in ("approve", "reject") or not proposal_id:
            self._send(400, "bad request", "text/plain"); return
        from roborsi.embodied.skills._lib.human_review.skill_review import (
            approve, reject,
        )
        ok = (approve(proposal_id, note) if action == "approve"
              else reject(proposal_id, note or "rejected via web UI"))
        if ok:
            self.send_response(303); self.send_header("Location", "/"); self.end_headers()
        else:
            self._send(404, f"proposal {proposal_id} not found", "text/plain")

    def _handle_audit(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        form = parse_qs(body)
        proposal_id = form.get("id", [""])[0]
        if not proposal_id:
            self._send(400, "missing id", "text/plain"); return
        # Load proposal from queue.
        p = _QUEUE_DIR / f"{proposal_id}.json"
        if not p.exists():
            self._send(404, f"proposal {proposal_id} not found", "text/plain"); return
        data = json.loads(p.read_text())
        from roborsi.embodied.skills._lib.human_review.skill_audit import (
            audit_skill,
        )
        audit = audit_skill(
            name=data.get("name", "?"),
            code=data.get("code", ""),
            docstring=data.get("docstring", ""),
            task_name=data.get("task_name", "shared"),
            test_result_preview=data.get("test_result_preview"),
            test_image_paths=data.get("test_images"),
        )
        # Persist audit alongside proposal so a refresh shows it.
        data["audit"] = audit
        p.write_text(json.dumps(data, indent=2))
        # Redirect back to index to render the updated card.
        self.send_response(303); self.send_header("Location", "/"); self.end_headers()

    def _render_index(self):
        from roborsi.embodied.skills._lib.human_review.skill_review import (
            list_pending,
        )
        pend = list_pending()
        if not pend:
            cards = '<div class="empty">No pending proposals — VLM hasn\'t asked for a new skill recently.</div>'
        else:
            cards = "\n".join(self._render_card(p) for p in pend)
        page = _INDEX_HTML.replace("{cards}", cards)
        self._send(200, page, "text/html; charset=utf-8")

    def _render_card(self, p: dict) -> str:
        from urllib.parse import quote
        ta = p.get("test_call_args")
        ta_html = (f'<div class="test-args">test_call_args: '
                    f'{html.escape(json.dumps(ta))}</div>' if ta else "")
        tr = p.get("test_result_preview")
        tr_html = (f'<div class="test-result">test result: '
                    f'{html.escape(tr[:300])}</div>' if tr else "")
        imgs = p.get("test_images") or {}
        if imgs:
            tiles = []
            for label in ("before", "after", "after_fail"):
                pth = imgs.get(label)
                if pth:
                    src = f"/img/{quote(pth, safe='')}"
                    tiles.append(
                        f'<figure><img src="{src}" alt="{label}">'
                        f'<figcaption>{html.escape(label)}</figcaption></figure>'
                    )
            imgs_html = (f'<div class="demo-imgs">{"".join(tiles)}</div>'
                          if tiles else "")
        else:
            imgs_html = ""
        audit_html = self._render_audit(p.get("audit"))
        return _CARD_TEMPLATE.format(
            id=html.escape(p.get("id", "")),
            name=html.escape(p.get("name", "?")),
            task=html.escape(str(p.get("task_name", "shared"))),
            ts=html.escape(p.get("submitted_at", "")),
            doc=html.escape(p.get("docstring", "")),
            code=html.escape(p.get("code", "")),
            test_args_html=ta_html,
            test_result_html=tr_html,
            demo_images_html=imgs_html,
            audit_html=audit_html,
        )

    def _render_audit(self, audit) -> str:
        if not audit or not isinstance(audit, dict):
            return ""
        verdict = (audit.get("verdict") or "REQUEST_CHANGES").upper()
        verdict_cls = verdict.lower().replace("_", "_")
        rows = []
        for dim in ("safety", "correctness", "robustness", "code_quality"):
            d = audit.get(dim, {}) or {}
            score = d.get("score")
            score_str = "—" if score is None else str(score)
            concerns = d.get("concerns") or []
            concerns_html = (html.escape(" / ".join(concerns)) if concerns
                              else "<em style='color:#999'>none</em>")
            rows.append(
                f'<div class="audit-row">'
                f'<span class="dim">{html.escape(dim)}</span>'
                f'<span class="score">{html.escape(score_str)}</span>'
                f'<span class="concerns">{concerns_html}</span>'
                f'</div>'
            )
        fixes = audit.get("specific_fixes") or []
        fixes_html = ""
        if fixes:
            items = "".join(f'<li>{html.escape(str(f))}</li>' for f in fixes)
            fixes_html = f'<ul class="audit-fixes">{items}</ul>'
        match_doc = audit.get("matches_docstring")
        match_label = ('matches docstring: '
                        + ('✓' if match_doc else '✗' if match_doc is False
                           else '?'))
        return (
            f'<div class="audit-block">'
            f'<div class="audit-verdict {verdict_cls}">🤖 AI Audit: {html.escape(verdict)} '
            f'<span style="font-weight:400;font-size:12px;color:#888;">'
            f'({html.escape(match_label)} · model: '
            f'{html.escape((audit.get("model") or "?").split("/")[-1][:30])} · '
            f'{html.escape(audit.get("audited_at", ""))})</span></div>'
            f'<div class="audit-summary">{html.escape(audit.get("summary", ""))}</div>'
            f'{"".join(rows)}'
            f'{fixes_html}'
            f'</div>'
        )

    def _send(self, status, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8765, host: str = "127.0.0.1") -> None:
    """Start the review server (foreground; ctrl-C to stop)."""
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), _ReviewHandler)
    print(f"[skill review] http://{host}:{port}/  (queue: {_QUEUE_DIR})")
    print("[skill review] open in browser; Approve/Reject buttons flip the queue.")
    print("[skill review] Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[skill review] stopped.")
