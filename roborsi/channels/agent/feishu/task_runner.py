"""Inline task runner. Spawns NO subprocess — runs the skill in the calling
thread, blocks until done. All run state is persisted to the sqlite trace
store (`roborsi.store.trace_db`). On-disk artefacts (frames, demo mp4)
still live under ``$ROBORSI_RUNS_DIR/<run_id>/`` so the HTML monitor can
serve them with ``/file?p=...``.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any


RUNS_DIR = Path(os.environ.get("ROBORSI_RUNS_DIR", "/tmp/roborsi_runs"))


def runs_dir() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def run_task_sync(task: str, seed: int = 0, episodes: int = 1,
                   tool_budget: int = 12,
                   skill_name: str | None = None,
                   chat_id: str | None = None,
                   run_mode: str | None = None) -> dict[str, Any]:
    """Run one task under an explicit or inherited RoboRSI run mode."""
    from roborsi.runtime_mode import current_mode, use_run_mode
    selected = run_mode or current_mode()
    with use_run_mode(selected):
        return _run_task_sync_impl(
            task=task,
            seed=seed,
            episodes=episodes,
            tool_budget=tool_budget,
            skill_name=skill_name,
            chat_id=chat_id,
        )


def _run_task_sync_impl(task: str, seed: int = 0, episodes: int = 1,
                        tool_budget: int = 12,
                        skill_name: str | None = None,
                        chat_id: str | None = None) -> dict[str, Any]:
    """Run a roborsi atomic task INLINE in the calling thread.
    BLOCKS until done. Persists progress to sqlite (`runs` + `steps` +
    `events` tables) so the HTML monitor follows in real time.
    `skill_name` defaults to `<task>.zeroshot` — pass explicit name to use a
    different executor (e.g. `<task>.execute`).
    `chat_id` is recorded so `/run/<id>` can link back to `/live/<chat_id>`."""
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    runs_dir().mkdir(parents=True, exist_ok=True)
    skill_name = skill_name or f"{task}.zeroshot"
    from roborsi.store import trace_db as _td
    _td.insert_run(run_id, task=task, skill=skill_name, seed=seed,
                    chat_id=chat_id)
    # Wire inner-trace live events to the chat session so /live/<chat_id>
    # streams per-substep progress in real time, not only on completion.
    from . import live_trace
    sess = live_trace.get_session(chat_id) if chat_id else None
    live_trace.set_inner_target(sess)
    live_trace.set_inner_run_id(run_id)
    if sess:
        sess.append("inner_start", run_id=run_id, task=task,
                     skill=skill_name, seed=seed)
    t0 = time.time()
    try:
        from roborsi.embodied.skills import run as run_skill
        result = run_skill(skill_name, episodes=episodes,
                            seed_start=seed, tool_budget=tool_budget)
        # Two return shapes: atomic .zeroshot returns {episodes: [...]};
        # long_horizon .execute returns {trace: [...], success, outcome, ...}.
        # Flatten LH return into an eps-shaped dict so the rest of the
        # status pipeline (and the agent's view) stays uniform.
        if "episodes" in result:
            eps = (result.get("episodes") or [{}])[0]
        elif "trace" in result:
            trace = result.get("trace") or []
            atomics = [{
                "atomic": s.get("atomic"),
                "success": bool(s.get("atomic_success")),
                "outcome": (s.get("atomic_result") or {}).get("outcome"),
                "tool_calls": (s.get("atomic_result") or {}).get("tool_calls"),
                "wall_time_s": s.get("wall_time_s"),
                "error": s.get("atomic_error"),
            } for s in trace]
            eps = {
                "success": bool(result.get("success")),
                "outcome": result.get("outcome"),
                "tool_calls": sum((a.get("tool_calls") or 0) for a in atomics),
                "vlm_trace": trace,
                "n_atomics": len(atomics),
                "n_success": sum(1 for a in atomics if a["success"]),
                "atomics": atomics,
                "report_path": result.get("report_path"),
            }
        else:
            eps = {}
        from roborsi.runtime_mode import current_mode
        eps["run_mode"] = current_mode().value
        ok = bool(eps.get("success"))
        outcome = eps.get("outcome", "")
        summary = (f"✓ success ({outcome}, {eps.get('tool_calls','?')} tool calls)"
                    if ok else
                    f"✗ {outcome} after {eps.get('tool_calls','?')} tool calls")
        _td.update_run(run_id, status="success" if ok else "failed",
                        outcome=outcome, summary=summary,
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        wallclock_s=time.time() - t0,
                        episode_summary=eps)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _td.update_run(run_id, status="error",
                        summary=f"{type(e).__name__}: {e}",
                        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                        wallclock_s=time.time() - t0)
    finally:
        live_trace.set_inner_target(None)
        live_trace.set_inner_run_id(None)
        if sess:
            sess.append("inner_end", run_id=run_id)
    return _result_dict(run_id)


def _result_dict(run_id: str) -> dict[str, Any]:
    """Return the run row in the shape callers expect (legacy `run_id` +
    parsed `episode_summary`)."""
    from roborsi.store import trace_db as _td
    import json as _j
    row = _td.get_run(run_id) or {"id": run_id, "status": "error"}
    row["run_id"] = row.get("id")
    if row.get("episode_summary_json"):
        try:
            row["episode_summary"] = _j.loads(row["episode_summary_json"])
        except _j.JSONDecodeError:
            row["episode_summary"] = {}
    return row


def spawn_task(task: str, seed: int = 0, episodes: int = 1,
                tool_budget: int = 12, on_complete=None,
                run_mode: str | None = None) -> str:
    """Synchronous shim for the legacy callback-based API."""
    st = run_task_sync(
        task, seed, episodes, tool_budget, run_mode=run_mode
    )
    if on_complete:
        on_complete(st.get("run_id"))
    return st.get("run_id", "")


def render_demo_video(run_id: str, camera: str = "head_camera") -> Path | None:
    """Render frames under <data_dir>/frames/<camera>/*.jpg to mp4.
    Returns mp4 path, or None on failure."""
    from roborsi.store import trace_db as _td
    run = _td.get_run(run_id) or {}
    import json as _j
    ep = {}
    if run.get("episode_summary_json"):
        try:
            ep = _j.loads(run["episode_summary_json"])
        except _j.JSONDecodeError:
            pass
    run_dir = ep.get("dir")
    if not run_dir:
        return None
    frames_dir = Path(run_dir) / "frames" / camera
    if not frames_dir.exists():
        return None
    import glob
    files = sorted(glob.glob(str(frames_dir / "*.jpg")))
    if not files:
        return None
    out = runs_dir() / run_id / f"demo_{camera}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    import imageio.v2 as iio
    with iio.get_writer(str(out), fps=30, codec="libx264", quality=8) as w:
        for f in files:
            w.append_data(iio.imread(f))
    return out
