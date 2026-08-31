"""Standalone Web console for RoboRSI evidence and local campaigns."""

from __future__ import annotations

import html
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from roborsi_libero.evidence import default_manifest_path, replay_bundle
from roborsi_libero.runs import load_campaign_payload

REQUIRED_FIELDS = {
    "metric",
    "claim_scope",
    "k",
    "solved_tasks",
    "total_tasks",
    "rate",
    "pass_curve",
    "by_suite",
}

SUITE_LABELS = {
    "libero_90": "LIBERO-90",
    "libero_goal": "Goal",
    "libero_object": "Object",
    "libero_spatial": "Spatial",
}

STATUS_LABELS = {
    "blocked": "Blocked",
    "complete": "Complete",
    "created": "Created",
    "retained": "Retained evidence",
    "running": "Running",
}


def _normalize_payload(payload: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    normalized = dict(payload)
    pass_curve = [int(value) for value in normalized.get("pass_curve") or ()]
    normalized["pass_curve"] = pass_curve
    normalized.setdefault("k", len(pass_curve))
    normalized.setdefault("source_kind", "result")
    normalized.setdefault("source_name", source_name)
    normalized.setdefault("mode", "adaptive")
    normalized.setdefault("status", "retained")
    normalized.setdefault("completed_passes", len(pass_curve))
    normalized.setdefault("completed_seeds", [])
    normalized.setdefault("release_history", [])
    normalized.setdefault("current_release_id", "")
    normalized.setdefault("protocol", {})
    normalized.setdefault("success_source", "final simulator predicate only")

    verdicts = dict(normalized.get("verdicts") or {})
    if not verdicts:
        verdicts = {
            "task_success": int(normalized.get("task_success_records", 0) or 0),
            "task_failure": int(normalized.get("task_failure_records", 0) or 0),
            "implementation_failure": int(
                normalized.get("implementation_failures", 0) or 0
            ),
            "infrastructure_excluded": int(
                normalized.get("infrastructure_excluded", 0) or 0
            ),
        }
    normalized["verdicts"] = verdicts

    efficiency = dict(normalized.get("efficiency") or {})
    normalized.setdefault(
        "total_tokens",
        int(efficiency.get("total_tokens", 0) or 0),
    )
    normalized.setdefault(
        "median_total_tokens",
        float(efficiency.get("median_total_tokens", 0.0) or 0.0),
    )
    normalized.setdefault(
        "total_vlm_calls",
        int(efficiency.get("total_vlm_calls", 0) or 0),
    )
    normalized.setdefault(
        "total_elapsed_s",
        float(efficiency.get("total_episode_elapsed_s", 0.0) or 0.0),
    )
    return normalized


def load_dashboard_payload(
    result_path: Path | None = None,
    campaign_root: Path | None = None,
) -> dict[str, Any]:
    """Load exactly one result source and normalize it for the Web console."""
    if result_path is not None and campaign_root is not None:
        raise ValueError("choose exactly one dashboard source")
    if campaign_root is not None:
        payload = load_campaign_payload(campaign_root)
    elif result_path is not None:
        source = Path(result_path).expanduser().resolve()
        if source.is_dir():
            payload = load_campaign_payload(source)
        elif source.name == "result.json" and (source.parent / "manifest.json").is_file():
            payload = load_campaign_payload(source.parent)
        else:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("dashboard result must be a JSON object")
            payload = _normalize_payload(payload, source_name=source.stem)
    else:
        payload = replay_bundle(default_manifest_path()).to_dict()
        payload.update(
            {
                "source_kind": "public_evidence",
                "source_name": "Adaptive Pass@10 evidence",
                "mode": "adaptive",
                "status": "retained",
                "completed_passes": int(payload["k"]),
                "success_source": "final simulator predicate only",
            }
        )
        payload = _normalize_payload(payload, source_name="Adaptive Pass@10 evidence")

    missing = sorted(REQUIRED_FIELDS - payload.keys())
    if missing:
        raise ValueError(f"dashboard result is missing fields: {', '.join(missing)}")
    if int(payload["total_tasks"]) <= 0:
        raise ValueError("dashboard total_tasks must be positive")
    if not isinstance(payload["by_suite"], dict):
        raise ValueError("dashboard by_suite must be an object")
    if not isinstance(payload["pass_curve"], list):
        raise ValueError("dashboard pass_curve must be a list")
    return payload


def _format_integer(value: int | float) -> str:
    return f"{int(value):,}"


def _format_tokens(value: int | float) -> str:
    amount = float(value)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return _format_integer(amount)


def _format_duration(seconds: int | float) -> str:
    value = float(seconds)
    if value >= 3600:
        return f"{value / 3600:.1f} h"
    if value >= 60:
        return f"{value / 60:.1f} min"
    return f"{value:.1f} s"


def _scope_label(claim_scope: str) -> str:
    labels = {
        "adaptive_cross_release_campaign": "Adaptive cross-release coverage",
        "adaptive_cross_release_development_coverage": "Adaptive development coverage",
        "single_release_fixed_evaluation": "Fixed release",
    }
    return labels.get(claim_scope, claim_scope.replace("_", " ").title())


def _curve_svg(pass_curve: list[int], total_tasks: int) -> str:
    width, height = 860, 260
    left, right, top, bottom = 62, 24, 24, 42
    plot_width = width - left - right
    plot_height = height - top - bottom
    if not pass_curve:
        return (
            f'<svg viewBox="0 0 {width} {height}" role="img" '
            'aria-label="No pass results have been recorded">'
            '<text x="430" y="132" text-anchor="middle" class="empty-chart">'
            "No pass results recorded yet</text></svg>"
        )

    def x(index: int) -> float:
        return left + (plot_width / max(1, len(pass_curve) - 1)) * index

    def y(value: int) -> float:
        bounded = max(0, min(total_tasks, value))
        return top + plot_height * (1 - bounded / total_tasks)

    grid = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        value = round(total_tasks * fraction)
        row_y = y(value)
        grid.append(
            f'<line x1="{left}" y1="{row_y:.1f}" x2="{width-right}" '
            f'y2="{row_y:.1f}" class="grid-line"/>'
            f'<text x="{left-12}" y="{row_y+4:.1f}" text-anchor="end" '
            f'class="axis-label">{value}</text>'
        )
    points = " ".join(
        f"{x(index):.1f},{y(int(value)):.1f}"
        for index, value in enumerate(pass_curve)
    )
    dots = "".join(
        f'<circle cx="{x(index):.1f}" cy="{y(int(value)):.1f}" r="4.5"/>'
        for index, value in enumerate(pass_curve)
    )
    labels = "".join(
        f'<text x="{x(index):.1f}" y="{height-17}" text-anchor="middle" '
        f'class="axis-label">P{index+1}</text>'
        for index in range(len(pass_curve))
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Cumulative solved tasks by pass">'
        + "".join(grid)
        + f'<polyline points="{points}" class="curve-line"/>'
        + f'<g class="curve-dots">{dots}</g>'
        + labels
        + "</svg>"
    )


def _metric(label: str, value: str, detail: str = "") -> str:
    detail_html = f"<small>{html.escape(detail)}</small>" if detail else ""
    return (
        '<article class="metric">'
        f"<span>{html.escape(label)}</span>"
        f"<strong>{html.escape(value)}</strong>"
        f"{detail_html}</article>"
    )


def _protocol_rows(payload: dict[str, Any]) -> str:
    protocol = dict(payload.get("protocol") or {})
    rows = [
        ("Mode", str(payload.get("mode") or "unspecified")),
        ("Metric", str(payload.get("metric") or "unspecified")),
        ("Claim scope", _scope_label(str(payload.get("claim_scope") or ""))),
        ("Success source", str(payload.get("success_source") or "unspecified")),
    ]
    if protocol.get("model"):
        rows.append(("Model", str(protocol["model"])))
    if protocol.get("reasoning_effort"):
        rows.append(("Reasoning", str(protocol["reasoning_effort"])))
    if protocol.get("controller"):
        rows.append(("Controller", str(protocol["controller"])))
    if int(protocol.get("image_size") or 0) > 0:
        rows.append(("Observation", f"{int(protocol['image_size'])} px"))
    if int(protocol.get("horizon") or 0) > 0:
        rows.append(("Horizon", _format_integer(protocol["horizon"])))
    return "".join(
        "<div><span>"
        + html.escape(label)
        + "</span><strong>"
        + html.escape(value)
        + "</strong></div>"
        for label, value in rows
    )


def render_dashboard_html(
    payload: dict[str, Any],
    *,
    auto_refresh: bool = False,
) -> str:
    """Render a self-contained, offline HTML dashboard."""
    missing = sorted(REQUIRED_FIELDS - payload.keys())
    if missing:
        raise ValueError(f"dashboard result is missing fields: {', '.join(missing)}")

    solved = int(payload["solved_tasks"])
    total = int(payload["total_tasks"])
    rate = float(payload["rate"])
    passes = int(payload["k"])
    completed_passes = int(payload.get("completed_passes", passes) or 0)
    excluded = int(payload.get("infrastructure_excluded", 0) or 0)
    pass_curve = [int(value) for value in payload["pass_curve"]]
    claim_scope = str(payload["claim_scope"])
    source_kind = str(payload.get("source_kind") or "result")
    source_name = str(payload.get("source_name") or "RoboRSI result")
    run_id = str(payload.get("run_id") or "")
    status = str(payload.get("status") or "retained")
    status_label = STATUS_LABELS.get(status, status.replace("_", " ").title())
    title = run_id if run_id else "Evidence Replay"
    eyebrow = run_id.upper() if run_id else source_name.upper()
    is_campaign = source_kind == "campaign"

    suites = []
    for key, row in payload["by_suite"].items():
        suite_solved = int(row["solved_tasks"])
        suite_total = int(row["total_tasks"])
        suite_rate = float(row["rate"])
        label = SUITE_LABELS.get(str(key), str(key).replace("_", " ").title())
        suites.append(
            f"""
            <article class="suite-row">
              <div>
                <strong>{html.escape(label)}</strong>
                <span>{suite_solved} / {suite_total}</span>
              </div>
              <div class="bar" aria-label="{suite_rate:.1%}">
                <i style="width:{100 * suite_rate:.3f}%"></i>
              </div>
              <b>{100 * suite_rate:.1f}%</b>
            </article>
            """
        )

    verdicts = dict(payload.get("verdicts") or {})
    verdict_values = {
        "Success": int(verdicts.get("task_success", 0) or 0),
        "Task failure": int(verdicts.get("task_failure", 0) or 0),
        "Implementation": int(verdicts.get("implementation_failure", 0) or 0),
        "Infrastructure": int(verdicts.get("infrastructure_excluded", 0) or 0),
    }
    verdict_total = max(1, sum(verdict_values.values()))
    verdict_rows = "".join(
        f"""
        <div class="verdict-row">
          <span>{html.escape(label)}</span>
          <div><i style="width:{100 * value / verdict_total:.3f}%"></i></div>
          <strong>{value}</strong>
        </div>
        """
        for label, value in verdict_values.items()
    )

    if is_campaign:
        release_history = [str(value) for value in payload.get("release_history") or ()]
        release_html = "".join(
            f"<li><span>{index:02d}</span><code>{html.escape(release)}</code></li>"
            for index, release in enumerate(release_history, 1)
        )
        if not release_html:
            release_html = (
                "<li><span>--</span><code>No release history recorded</code></li>"
            )
        release_title = "Release history"
        release_intro = "Validated capability identities observed in this campaign."
        resource_labels = ("Total tokens", "VLM calls", "Elapsed time")
        metrics = "".join(
            [
                _metric("Coverage", f"{100 * rate:.1f}%", _scope_label(claim_scope)),
                _metric("Solved tasks", f"{solved} / {total}", "task-level"),
                _metric(
                    "Completed passes",
                    f"{completed_passes} / {passes}",
                    "ordered seeds",
                ),
                _metric("Campaign status", status_label, str(payload.get("mode") or "")),
                _metric(
                    "Median tokens",
                    _format_tokens(payload.get("median_total_tokens", 0)),
                    "non-infrastructure episodes",
                ),
                _metric(
                    "Episode time",
                    _format_duration(payload.get("total_elapsed_s", 0)),
                    "summed valid episodes",
                ),
            ]
        )
        description = (
            "Local campaign status and metrics reconstructed from retained run artifacts. "
            "Reload this page while a campaign is running to read the latest state."
        )
        boundary = (
            "Campaign metrics use retained task records. Infrastructure attempts are "
            "reported separately and excluded from the task denominator."
        )
    else:
        release_title = "Evidence contents"
        release_intro = "Fields retained in the compact public replay bundle."
        release_html = "".join(
            [
                "<li><span>01</span><code>95 canonical success rows</code></li>",
                "<li><span>02</span><code>120-task catalog and ordered seeds</code></li>",
                "<li><span>03</span><code>Final verdict and efficiency fields</code></li>",
                "<li><span>04</span><code>Run-local identifiers removed</code></li>",
            ]
        )
        resource_labels = (
            "Retained-row tokens",
            "Retained-row VLM calls",
            "Retained-row time",
        )
        metrics = "".join(
            [
                _metric("Coverage", f"{100 * rate:.1f}%", _scope_label(claim_scope)),
                _metric("Solved tasks", f"{solved} / {total}", "task-level"),
                _metric("Protocol passes", str(passes), "ordered seeds"),
                _metric(
                    "Evidence rows",
                    _format_integer(payload.get("task_success_records", 0)),
                    "canonical successes",
                ),
                _metric(
                    "VLM calls",
                    _format_integer(payload.get("total_vlm_calls", 0)),
                    "retained success rows",
                ),
                _metric(
                    "Retained time",
                    _format_duration(payload.get("total_elapsed_s", 0)),
                    "retained success rows",
                ),
            ]
        )
        description = (
            "Packaged result replayed from the public evidence bundle without a model, "
            "simulator, or hidden environment state."
        )
        boundary = (
            "The compact public bundle retains one canonical success row per solved task. "
            "It verifies coverage, not total historical Token or wall-clock spend."
        )

    raw_payload = html.escape(
        json.dumps(payload, indent=2, sort_keys=True),
        quote=False,
    )
    total_tokens_label = _format_tokens(payload.get("total_tokens", 0))
    total_vlm_calls_label = _format_integer(payload.get("total_vlm_calls", 0))
    total_elapsed_label = _format_duration(payload.get("total_elapsed_s", 0))
    refresh = (
        '<meta http-equiv="refresh" content="15">'
        if auto_refresh and is_campaign and status == "running"
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh}
  <title>RoboRSI Evidence Console · {html.escape(title)}</title>
  <link rel="icon" href="data:,">
  <style>
    :root {{
      color-scheme: light;
      --ink: #14242d;
      --soft: #586b75;
      --muted: #84939a;
      --line: #d8e1e5;
      --paper: #ffffff;
      --wash: #f4f7f8;
      --blue: #007dce;
      --teal: #14877f;
      --orange: #cf6532;
      --red: #a94843;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--wash); color: var(--ink); }}
    .topbar {{ display: flex; justify-content: space-between; gap: 24px; align-items: center;
      min-height: 58px; padding: 0 28px; border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,.96); }}
    .brand {{ font: 800 14px/1 ui-monospace, monospace; }}
    .brand span {{ margin-left: 10px; color: var(--muted); font-weight: 600; }}
    .status {{ display: inline-flex; gap: 8px; align-items: center; color: var(--soft);
      font: 700 11px/1 ui-monospace, monospace; text-transform: uppercase; }}
    .status i {{ width: 8px; height: 8px; border-radius: 50%; background: var(--teal); }}
    .status-running i {{ background: var(--orange); }}
    .status-blocked i {{ background: var(--red); }}
    main {{ width: min(1180px, calc(100% - 48px)); margin: 0 auto; padding: 46px 0 72px; }}
    .page-head {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 30px;
      align-items: end; }}
    .eyebrow {{ color: var(--blue); font: 750 11px/1.3 ui-monospace, monospace; }}
    h1 {{ margin: 10px 0 0; font-size: 46px; line-height: 1.02; letter-spacing: 0;
      overflow-wrap: anywhere; }}
    .page-head p {{ max-width: 710px; margin: 16px 0 0; color: var(--soft);
      font-size: 15px; line-height: 1.65; }}
    .scope {{ max-width: 300px; padding: 10px 12px; border: 1px solid var(--line);
      border-radius: 5px; background: var(--paper); color: var(--soft);
      font: 700 10px/1.4 ui-monospace, monospace; text-align: right;
      overflow-wrap: anywhere; }}
    .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
      margin-top: 34px; border: 1px solid var(--line); background: var(--paper); }}
    .metric {{ display: flex; min-width: 0; min-height: 132px; flex-direction: column;
      padding: 20px; border-right: 1px solid var(--line); }}
    .metric:last-child {{ border-right: 0; }}
    .metric > span {{ color: var(--muted); font: 700 10px/1.3 ui-monospace, monospace;
      text-transform: uppercase; }}
    .metric > strong {{ margin-top: auto; padding-top: 22px; font-size: 25px;
      line-height: 1.05; overflow-wrap: anywhere; }}
    .metric > small {{ margin-top: 7px; color: var(--soft); font-size: 10px;
      line-height: 1.35; }}
    .dashboard-grid {{ display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(320px, .8fr);
      gap: 22px; margin-top: 22px; }}
    .panel {{ min-width: 0; padding: 26px; border: 1px solid var(--line);
      border-radius: 7px; background: var(--paper); }}
    .panel h2 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
    .panel > p {{ margin: 9px 0 0; color: var(--soft); font-size: 13px; line-height: 1.55; }}
    .curve {{ margin-top: 18px; overflow: hidden; }}
    .curve svg {{ display: block; width: 100%; min-height: 210px; }}
    .grid-line {{ stroke: #dfe6e9; stroke-width: 1; }}
    .axis-label {{ fill: #718189; font: 11px ui-monospace, monospace; }}
    .empty-chart {{ fill: #718189; font: 13px ui-monospace, monospace; }}
    .curve-line {{ fill: none; stroke: var(--blue); stroke-width: 4;
      stroke-linejoin: round; stroke-linecap: round; }}
    .curve-dots circle {{ fill: var(--paper); stroke: var(--orange); stroke-width: 3; }}
    .suite-list {{ margin-top: 18px; border-top: 1px solid var(--line); }}
    .suite-row {{ display: grid; grid-template-columns: 150px minmax(0, 1fr) 54px;
      gap: 16px; align-items: center; padding: 16px 0; border-bottom: 1px solid var(--line); }}
    .suite-row > div:first-child {{ display: flex; justify-content: space-between; gap: 12px; }}
    .suite-row span, .suite-row b {{ color: var(--soft); font: 700 11px ui-monospace, monospace; }}
    .suite-row b {{ text-align: right; }}
    .bar {{ height: 8px; overflow: hidden; background: #e8eef1; }}
    .bar i {{ display: block; height: 100%; background: var(--teal); }}
    .verdict-list {{ display: grid; gap: 15px; margin-top: 22px; }}
    .verdict-row {{ display: grid; grid-template-columns: 105px minmax(0, 1fr) 36px;
      gap: 12px; align-items: center; }}
    .verdict-row span, .verdict-row strong {{ font: 650 11px/1.2 ui-monospace, monospace; }}
    .verdict-row span {{ color: var(--soft); }}
    .verdict-row strong {{ text-align: right; }}
    .verdict-row > div {{ height: 7px; background: #e9eef0; }}
    .verdict-row i {{ display: block; height: 100%; background: var(--blue); }}
    .verdict-row:nth-child(2) i {{ background: var(--orange); }}
    .verdict-row:nth-child(3) i {{ background: var(--red); }}
    .verdict-row:nth-child(4) i {{ background: var(--muted); }}
    .protocol {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 18px; border-top: 1px solid var(--line); }}
    .protocol > div {{ min-width: 0; padding: 15px 14px 15px 0;
      border-bottom: 1px solid var(--line); }}
    .protocol span {{ display: block; color: var(--muted);
      font: 700 9px/1.3 ui-monospace, monospace; text-transform: uppercase; }}
    .protocol strong {{ display: block; margin-top: 7px; font-size: 12px;
      line-height: 1.4; overflow-wrap: anywhere; }}
    .releases {{ max-height: 265px; margin: 18px 0 0; padding: 0; overflow: auto;
      border-top: 1px solid var(--line); list-style: none; }}
    .releases li {{ display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 10px;
      padding: 12px 0; border-bottom: 1px solid var(--line); }}
    .releases span {{ color: var(--muted); font: 700 10px/1.4 ui-monospace, monospace; }}
    .releases code {{ color: var(--ink); font: 11px/1.4 ui-monospace, monospace;
      overflow-wrap: anywhere; }}
    .boundary {{ margin-top: 22px; padding: 18px 20px; border-left: 3px solid var(--orange);
      background: #fff; color: var(--soft); font-size: 13px; line-height: 1.6; }}
    details {{ margin-top: 22px; border-top: 1px solid var(--line); }}
    summary {{ padding: 18px 0; cursor: pointer; color: var(--blue);
      font: 700 12px/1.3 ui-monospace, monospace; }}
    pre {{ max-height: 480px; margin: 0; padding: 20px; overflow: auto;
      border-radius: 6px; background: #111c22; color: #dce8ed;
      font: 11px/1.55 ui-monospace, monospace; }}
    footer {{ margin-top: 28px; color: var(--muted); font: 10px/1.5 ui-monospace, monospace; }}
    @media (max-width: 980px) {{
      .metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .metric:nth-child(3) {{ border-right: 0; }}
      .metric:nth-child(-n+3) {{ border-bottom: 1px solid var(--line); }}
      .dashboard-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 640px) {{
      .topbar {{ padding: 0 16px; }}
      .brand span {{ display: none; }}
      main {{ width: min(100% - 28px, 1180px); padding-top: 30px; }}
      .page-head {{ grid-template-columns: 1fr; align-items: start; }}
      h1 {{ font-size: 34px; }}
      .scope {{ text-align: left; }}
      .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .metric:nth-child(2n) {{ border-right: 0; }}
      .metric:nth-child(-n+4) {{ border-bottom: 1px solid var(--line); }}
      .metric:nth-child(3) {{ border-right: 1px solid var(--line); }}
      .suite-row {{ grid-template-columns: 1fr 54px; }}
      .suite-row > div:nth-child(2) {{ grid-column: 1 / -1; grid-row: 2; }}
      .protocol {{ grid-template-columns: 1fr; }}
      .panel {{ padding: 20px; }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">RoboRSI <span>EVIDENCE + CAMPAIGN CONSOLE</span></div>
    <div class="status status-{html.escape(status)}"><i></i>{html.escape(status_label)}</div>
  </header>
  <main>
    <section class="page-head">
      <div>
        <div class="eyebrow">{html.escape(eyebrow)}</div>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(description)}</p>
      </div>
      <div class="scope">{html.escape(_scope_label(claim_scope))}</div>
    </section>
    <section class="metrics">{metrics}</section>
    <div class="dashboard-grid">
      <section class="panel">
        <h2>Cumulative task coverage</h2>
        <p>Each task contributes once after its first final simulator-confirmed success.</p>
        <div class="curve">{_curve_svg(pass_curve, total)}</div>
      </section>
      <section class="panel">
        <h2>Episode verdicts</h2>
        <p>Infrastructure records remain visible without entering the task denominator.</p>
        <div class="verdict-list">{verdict_rows}</div>
      </section>
      <section class="panel">
        <h2>Suite breakdown</h2>
        <div class="suite-list">{''.join(suites)}</div>
      </section>
      <section class="panel">
        <h2>Protocol</h2>
        <div class="protocol">{_protocol_rows(payload)}</div>
      </section>
      <section class="panel">
        <h2>{release_title}</h2>
        <p>{release_intro}</p>
        <ol class="releases">{release_html}</ol>
      </section>
      <section class="panel">
        <h2>Resource totals</h2>
        <div class="protocol">
          <div><span>{resource_labels[0]}</span><strong>{total_tokens_label}</strong></div>
          <div><span>{resource_labels[1]}</span><strong>{total_vlm_calls_label}</strong></div>
          <div><span>{resource_labels[2]}</span><strong>{total_elapsed_label}</strong></div>
          <div><span>Infra excluded</span><strong>{excluded}</strong></div>
        </div>
      </section>
    </div>
    <div class="boundary">{html.escape(boundary)}</div>
    <details>
      <summary>Machine-readable result</summary>
      <pre>{raw_payload}</pre>
    </details>
    <footer>Generated locally by RoboRSI · Source: {html.escape(source_name)}</footer>
  </main>
</body>
</html>
"""


def write_dashboard_html(
    output: Path,
    *,
    result_path: Path | None = None,
    campaign_root: Path | None = None,
) -> Path:
    """Write one self-contained dashboard HTML file."""
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_dashboard_html(
            load_dashboard_payload(
                result_path=result_path,
                campaign_root=campaign_root,
            )
        ),
        encoding="utf-8",
    )
    return destination


def _dashboard_handler(
    *,
    result_path: Path | None,
    campaign_root: Path | None,
) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - HTTP method contract
            path = urlsplit(self.path).path
            if path == "/healthz":
                body = b"ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            elif path in {"/", "/index.html"}:
                try:
                    body = render_dashboard_html(
                        load_dashboard_payload(
                            result_path=result_path,
                            campaign_root=campaign_root,
                        ),
                        auto_refresh=True,
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                except Exception as exc:  # noqa: BLE001
                    body = f"RoboRSI dashboard error: {type(exc).__name__}: {exc}\n".encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
            else:
                body = b"not found\n"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def serve_dashboard(
    *,
    result_path: Path | None,
    campaign_root: Path | None = None,
    host: str,
    port: int,
    open_browser: bool,
) -> str:
    """Serve a refreshable dashboard until interrupted and return its URL."""
    handler = _dashboard_handler(
        result_path=result_path,
        campaign_root=campaign_root,
    )
    try:
        server = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise RuntimeError(
            f"could not start RoboRSI Web console on {host}:{port}: {exc}"
        ) from exc
    actual_host, actual_port = server.server_address[:2]
    browser_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::"} else actual_host
    url = f"http://{browser_host}:{actual_port}/"
    print(f"RoboRSI Web console: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url
