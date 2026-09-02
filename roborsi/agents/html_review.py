"""HTML proposal review surfaces.

Two pages:
  • index.html — lists all pending proposals with status badges
    (harness PASS/FAIL · similarity PASS/FAIL · awaiting human).
    Operator clicks to drill into a per-proposal diff page.
  • per-proposal page — already authored by reviewer._render_html_diff;
    we don't duplicate that here.

Both land under ~/.roborsi/proposal_html/. The same dir is also
serveable via the existing python -m http.server cloudflare tunnel
the project already uses for web/.
"""
from __future__ import annotations

import html
import json
from pathlib import Path


_PROPOSAL_DIR = Path.home() / ".roborsi" / "skill_review"
_HTML_DIR = Path.home() / ".roborsi" / "proposal_html"


def _load_pending() -> list[dict]:
    """Read every JSON in skill_review/ root (not the applied/rejected
    subdirs). Sort newest first."""
    if not _PROPOSAL_DIR.exists():
        return []
    rows: list[dict] = []
    for f in _PROPOSAL_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        d["_path"] = str(f)
        d["_mtime"] = f.stat().st_mtime
        rows.append(d)
    rows.sort(key=lambda d: -d["_mtime"])
    return rows


def _badge(label: str, kind: str) -> str:
    """kind: 'ok' | 'fail' | 'wait' | 'neutral'"""
    color = {"ok": "#2c7a3f", "fail": "#a13d3d",
              "wait": "#8a6800", "neutral": "#666"}[kind]
    return (f'<span style="display:inline-block;font-size:11px;'
            f'background:{color};color:#fff;padding:2px 8px;'
            f'border-radius:10px;margin-right:6px">'
            f'{html.escape(label)}</span>')


def _row_html(proposal: dict) -> str:
    pid = proposal.get("id", "?")
    name = proposal.get("name", "?")
    kind = proposal.get("kind", "?")
    rationale = (proposal.get("rationale") or "")[:200]
    vr = proposal.get("validation_report") or {}
    harness = (vr.get("harness") or {})
    similarity = (vr.get("similarity") or {})

    # Status badges
    badges: list[str] = []
    if vr:
        h_pass = harness.get("passed")
        s_pass = similarity.get("passed")
        badges.append(_badge(
            "harness PASS" if h_pass else "harness FAIL",
            "ok" if h_pass else "fail"))
        badges.append(_badge(
            "similarity OK" if s_pass else "DUPLICATE",
            "ok" if s_pass else "fail"))
        if vr.get("overall_pass"):
            badges.append(_badge("auto-eligible", "ok"))
    else:
        badges.append(_badge("awaiting validation", "wait"))

    per_html = _HTML_DIR / f"{pid}.html"
    if per_html.exists():
        link = f'<a href="{html.escape(pid)}.html" style="color:#0a66c2;text-decoration:none">{html.escape(name)}</a>'
    else:
        link = html.escape(name)

    detail_lines: list[str] = []
    if vr:
        if harness.get("detail"):
            detail_lines.append(
                f"<b>harness:</b> {html.escape(harness['detail'][:200])}")
        if similarity.get("detail"):
            detail_lines.append(
                f"<b>similarity:</b> {html.escape(similarity['detail'][:200])}")
        if vr.get("note"):
            detail_lines.append(f"<i>{html.escape(vr['note'])}</i>")
    detail_block = "<br>".join(detail_lines)

    return f"""
<tr>
  <td style="padding:10px;vertical-align:top;width:46%">
    <div style="font-family:monospace;font-size:13px;color:#222"><b>{link}</b>
      <span style="color:#888;font-size:11px">· {html.escape(kind)}</span></div>
    <div style="font-family:monospace;font-size:11px;color:#999;margin-top:2px">{html.escape(pid)}</div>
    <div style="font-size:12px;color:#555;margin-top:6px;line-height:1.45">{html.escape(rationale)}</div>
  </td>
  <td style="padding:10px;vertical-align:top">
    <div style="margin-bottom:6px">{''.join(badges)}</div>
    <div style="font-size:11.5px;color:#555;line-height:1.6">{detail_block}</div>
    <div style="font-family:monospace;font-size:10.5px;color:#888;margin-top:8px">
      approve: <code>python3 scripts/apply_selfevo_proposal.py {html.escape(pid)} --skip-harness</code><br>
      reject:  <code>python3 scripts/apply_selfevo_proposal.py --reject {html.escape(pid)}</code>
    </div>
  </td>
</tr>"""


def build_index_page() -> Path:
    """Regenerate ~/.roborsi/proposal_html/index.html from current
    skill_review/ + validation reports. Returns the file path."""
    _HTML_DIR.mkdir(parents=True, exist_ok=True)
    rows = _load_pending()
    body_rows = "\n".join(_row_html(p) for p in rows) or """
<tr><td colspan="2" style="padding:24px;text-align:center;color:#888">
  No pending proposals.
</td></tr>"""
    title = f"Skill Review · {len(rows)} pending"
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body{{font-family:-apple-system,sans-serif;max-width:1180px;margin:24px auto;padding:0 24px;color:#222;background:#fafafa}}
  h1{{font-size:24px;border-bottom:1px solid #ddd;padding-bottom:10px;margin-bottom:18px}}
  .meta{{color:#888;font-size:12px;margin-bottom:18px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e0e0e0;border-radius:6px;overflow:hidden}}
  tr{{border-bottom:1px solid #eee}}
  tr:last-child{{border-bottom:none}}
  tr:hover{{background:#fafafa}}
  code{{background:#f3f3f3;padding:1px 4px;border-radius:3px;font-size:11px}}
</style></head><body>
<h1>🛠 Skill Review · {len(rows)} pending</h1>
<div class="meta">
  Source: <code>~/.roborsi/skill_review/*.json</code> ·
  Index regenerated each time a Reviewer produces a new proposal ·
  Click skill name for full diff page.
</div>
<table>
<thead>
  <tr style="background:#f0f0f0">
    <th style="padding:10px;text-align:left;font-size:11px;letter-spacing:.1em;color:#555">PROPOSAL</th>
    <th style="padding:10px;text-align:left;font-size:11px;letter-spacing:.1em;color:#555">STATUS · ACTIONS</th>
  </tr>
</thead>
<tbody>{body_rows}</tbody>
</table>
</body></html>
"""
    out = _HTML_DIR / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    path = build_index_page()
    print(path)
