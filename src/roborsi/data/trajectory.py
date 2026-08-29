"""Trajectory writer for roborsi LIBERO rollouts.

Layout (per-episode):

    <store-root>/<skill>/<run_id>/
        ├── meta.json            # skill, seed, plan trace, judge scores, outcome
        ├── episode.parquet      # frame-level observation.state + action + timestamp
        └── frames/
            ├── <cam>/0000.jpg
            └── <cam>/0001.jpg

LeRobot-alignment: column names ``observation.state``, ``action``,
``timestamp``, ``episode_index``, ``frame_index`` match LeRobot v0.5 so a
downstream trainer can concat without reshaping.

Image encoding: JPEG per frame per cam, not mp4 — simpler for the MVP,
swap to mp4 later when we measure actual disk pain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roborsi.embodied.agent_loop.env import Rollout


@dataclass
class WrittenEpisode:
    run_id: str
    dir: Path
    skill: str
    frames: int
    success: bool
    meta_path: Path
    parquet_path: Path | None


def _write_frames(rollout: Rollout, out_dir: Path) -> int:
    import cv2
    frames_root = out_dir / "frames"
    count = 0
    for idx, step in enumerate(rollout.steps):
        for cam, img in step.obs.images.items():
            if img is None:
                continue
            cam_dir = frames_root / cam
            cam_dir.mkdir(parents=True, exist_ok=True)
            path = cam_dir / f"{idx:06d}.jpg"
            # Simulator tensors are HWC RGB; cv2 writes BGR.
            to_write = img[..., ::-1] if hasattr(img, "shape") and img.ndim == 3 else img
            cv2.imwrite(str(path), to_write)
            count += 1
    return count


def _write_parquet(rollout: Rollout, out_path: Path) -> Path | None:
    import pandas as pd

    rows: list[dict[str, Any]] = []
    for i, step in enumerate(rollout.steps):
        rows.append({
            "episode_index": 0,
            "frame_index": i,
            "timestamp": step.obs.timestamp,
            "observation.state": _serialise(step.obs.state),
            "action": _serialise(step.action),
            "reward": step.reward,
            "done": step.done,
            "source": step.info.get("source"),
            "physics_tick": step.info.get("tick"),
            "action_type": step.info.get("action_type"),
        })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df.to_parquet(out_path, index=False)
    return out_path


def _serialise(value: Any) -> Any:
    """Narrow ndarray / tensor types into python lists for parquet."""
    if value is None:
        return None
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def write_rollout(
    rollout: Rollout,
    *,
    skill: str,
    run_id: str,
    store_root: Path,
    plan_trace: list[dict[str, Any]] | None = None,
    judge_scores: list[dict[str, Any]] | None = None,
    extra_meta: dict[str, Any] | None = None,
) -> WrittenEpisode:
    out_dir = store_root / skill / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = _write_frames(rollout, out_dir)
    parquet_path = _write_parquet(rollout, out_dir / "episode.parquet")
    meta_path = out_dir / "meta.json"
    meta = {
        "skill": skill,
        "run_id": run_id,
        "task": rollout.task,
        "seed": rollout.seed,
        "backend": rollout.meta.get("backend", ""),
        "success": rollout.success,
        "outcome": rollout.outcome,
        "length": rollout.length,
        "frames_written": frames,
        "plan_trace": plan_trace or [],
        "judge_scores": judge_scores or [],
        "rollout_meta": rollout.meta,
    }
    if extra_meta:
        meta.update(extra_meta)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return WrittenEpisode(
        run_id=run_id,
        dir=out_dir,
        skill=skill,
        frames=frames,
        success=rollout.success,
        meta_path=meta_path,
        parquet_path=parquet_path,
    )
