"""dataset.lerobot_build — DataStore → LeRobot v3.0 dataset.

LeRobot 0.5+ uses v3.0 schema:
  meta/info.json
  meta/tasks.parquet        (NOT .jsonl)
  meta/episodes/chunk-000/file-000.parquet   (per-episode metadata)
  data/chunk-000/file-000.parquet            (concatenated frames)
  videos/<cam>/chunk-000/file-000.mp4        (we still skip; frames are jpg)

For training small bootstrap data we keep videos as per-frame jpg in
``videos/<cam>/episode_<i>/`` for inspection, but lerobot will skip them
unless the policy expects video features.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from roborsi.data.store import DataStore


def _default_out_root() -> Path:
    from roborsi.embodied.paths import datasets_root
    return datasets_root()


def run(
    skill_label: str,
    dataset_name: str,
    fps: int = 30,
    robot_type: str = "aloha-agilex",
    splits: dict[str, float] | None = None,
    out_root: str | None = None,
    include_failed: bool = False,
    **_: Any,
) -> dict[str, Any]:
    if not skill_label or not dataset_name:
        raise ValueError("lerobot_build requires skill_label and dataset_name")
    store = DataStore()
    episodes = [e for e in store.list(skill_label) if include_failed or e.success]
    if not episodes:
        raise RuntimeError(
            f"no episodes found for skill='{skill_label}' "
            f"(set include_failed=true to keep failures)"
        )
    root = (Path(out_root).expanduser() if out_root else _default_out_root()).resolve()
    ds_root = root / dataset_name
    if ds_root.exists():
        raise RuntimeError(f"dataset '{dataset_name}' already exists at {ds_root}")
    (ds_root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (ds_root / "data" / "chunk-000").mkdir(parents=True)
    (ds_root / "videos").mkdir(parents=True)

    cam_names: set[str] = set()
    frames_total = 0
    episode_rows: list[dict[str, Any]] = []
    all_frame_rows: list[dict[str, Any]] = []

    # First pass: discover camera names so we know the encoded video keys.
    discovered_cams: set[str] = set()
    for ep in episodes:
        frames_src = ep.dir / "frames"
        if frames_src.exists():
            for cam_dir in frames_src.iterdir():
                if cam_dir.is_dir():
                    discovered_cams.add(cam_dir.name)
    head_cam = "head_camera" if "head_camera" in discovered_cams else next(iter(sorted(discovered_cams)), None)

    for ep_idx, ep in enumerate(episodes):
        n_frames, cams, frame_rows = _import_episode(ep.dir, ds_root, ep_idx, head_cam)
        cam_names.update(cams)
        episode_rows.append({
            "episode_index": ep_idx,
            "tasks": [skill_label],
            "length": n_frames,
            "dataset_from_index": frames_total,
            "dataset_to_index": frames_total + n_frames,
            "data/chunk_index": 0,
            "data/file_index": 0,
        })
        # Episode-level video file paths (one mp4 per camera per episode).
        for cam in cam_names:
            episode_rows[-1][f"videos/observation.images.{cam}/chunk_index"] = 0
            episode_rows[-1][f"videos/observation.images.{cam}/file_index"] = ep_idx
            episode_rows[-1][f"videos/observation.images.{cam}/from_timestamp"] = 0.0
            episode_rows[-1][f"videos/observation.images.{cam}/to_timestamp"] = float(n_frames) / float(fps)
        all_frame_rows.extend(frame_rows)
        frames_total += n_frames

    _write_data_parquet(all_frame_rows, ds_root / "data" / "chunk-000" / "file-000.parquet")
    _write_episodes_parquet(episode_rows, ds_root / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
    _write_tasks_parquet([skill_label], ds_root / "meta" / "tasks.parquet")
    _write_stats_json(all_frame_rows, ds_root / "meta" / "stats.json", head_cam)

    info = {
        "codebase_version": "v3.0",
        "robot_type": robot_type,
        "fps": fps,
        "total_episodes": len(episodes),
        "total_frames": frames_total,
        "total_tasks": 1,
        "total_chunks": 1,
        "chunks_size": 1000,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "splits": splits or {"train": f"0:{len(episodes)}"},
        "features": _features_schema(cam_names),
    }
    (ds_root / "meta" / "info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "skill": "lerobot_build",
        "dataset": dataset_name,
        "root": str(ds_root),
        "episodes": len(episodes),
        "frames": frames_total,
        "cameras": sorted(cam_names),
    }


def _import_episode(
    src_dir: Path, dst_root: Path, idx: int, head_cam: str | None,
) -> tuple[int, list[str], list[dict[str, Any]]]:
    """Parse one DataStore episode → (frames_count, cam_names, frame_rows).

    Embeds head_camera frames as raw bytes in parquet rows (image dtype).
    """
    import numpy as np
    import pandas as pd
    src_parquet = src_dir / "episode.parquet"
    if not src_parquet.exists():
        return 0, [], []
    df = pd.read_parquet(src_parquet)
    df["episode_index"] = idx
    df["task_index"] = 0
    df = df[df["observation.state"].apply(lambda v: v is not None)].reset_index(drop=True)
    if len(df) == 0:
        return 0, [], []

    state_lists = [np.asarray(s, dtype=np.float32).flatten().tolist() for s in df["observation.state"]]
    action_lists = []
    for i, a in enumerate(df["action"]):
        if a is None:
            action_lists.append(state_lists[i])
        else:
            action_lists.append(np.asarray(a, dtype=np.float32).flatten().tolist())
    df["observation.state"] = state_lists
    df["action"] = action_lists
    df["timestamp"] = df["timestamp"].astype("float32")
    df["frame_index"] = list(range(len(df)))

    keep = ["episode_index", "frame_index", "timestamp", "observation.state", "action", "task_index"]
    df = df[keep]

    cam_names: list[str] = []
    frames_src = src_dir / "frames"
    head_bytes: list[dict[str, Any]] = []
    if frames_src.exists():
        for cam_dir in sorted(p for p in frames_src.iterdir() if p.is_dir()):
            cam_names.append(cam_dir.name)
        if head_cam and (frames_src / head_cam).exists():
            jpgs = sorted((frames_src / head_cam).glob("*.jpg"))
            for i in range(len(df)):
                # Episode parquet row count may differ from saved frame count
                # (DataStore.write today writes start+end frames). Fall back
                # to last available frame.
                if i < len(jpgs):
                    img_path = jpgs[i]
                else:
                    img_path = jpgs[-1] if jpgs else None
                if img_path is None:
                    head_bytes.append({"bytes": b"", "path": None})
                else:
                    head_bytes.append({"bytes": img_path.read_bytes(), "path": None})
    if head_bytes:
        df[f"observation.images.{head_cam}"] = head_bytes
    return len(df), cam_names, df.to_dict(orient="records")


def _write_data_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    import pandas as pd
    if not rows:
        return
    df = pd.DataFrame(rows)
    df["index"] = range(len(df))
    df.to_parquet(path, index=False)


def _write_episodes_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)


def _write_tasks_parquet(tasks: list[str], path: Path) -> None:
    import pandas as pd
    df = pd.DataFrame({"task_index": list(range(len(tasks))), "task": tasks})
    df.to_parquet(path, index=False)


def _write_stats_json(rows: list[dict[str, Any]], path: Path, head_cam: str | None) -> None:
    """Compute min / max / mean / std for vector features. Required by LeRobot."""
    import json
    import numpy as np
    if not rows:
        path.write_text("{}", encoding="utf-8")
        return
    stats: dict[str, dict[str, list[float]]] = {}
    for key in ("observation.state", "action"):
        arr = np.asarray([r[key] for r in rows if r.get(key) is not None], dtype=np.float32)
        if arr.size == 0:
            continue
        stats[key] = {
            "min": arr.min(axis=0).tolist(),
            "max": arr.max(axis=0).tolist(),
            "mean": arr.mean(axis=0).tolist(),
            "std": (arr.std(axis=0) + 1e-6).tolist(),
            "count": [int(arr.shape[0])],
        }
    # Image stats: pi0/ACT expects per-channel min/max/mean/std for image features.
    # We use generic 0..1 normalisation since we have no precomputed image stats.
    if head_cam:
        key = f"observation.images.{head_cam}"
        zeros = [0.0, 0.0, 0.0]
        ones = [1.0, 1.0, 1.0]
        halves = [0.5, 0.5, 0.5]
        quarters = [0.25, 0.25, 0.25]
        stats[key] = {
            "min": [[[v] for v in zeros]],
            "max": [[[v] for v in ones]],
            "mean": [[[v] for v in halves]],
            "std": [[[v] for v in quarters]],
            "count": [int(len(rows))],
        }
    path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def _features_schema(cam_names: set[str]) -> dict[str, Any]:
    """Schema reflecting parquet columns + per-row image bytes for head_camera.

    Using ``image`` dtype (bytes embedded in parquet) instead of ``video``
    (mp4 + torchcodec). Image is heavier on disk but avoids torchcodec ABI
    mismatch with our torch 2.4.1+cu118 install.
    """
    feats: dict[str, Any] = {
        "observation.state": {"dtype": "float32", "shape": [14], "names": None},
        "action": {"dtype": "float32", "shape": [14], "names": None},
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
    }
    if "head_camera" in cam_names:
        feats["observation.images.head_camera"] = {
            "dtype": "image",
            "shape": [3, 240, 320],
            "names": ["channel", "height", "width"],
        }
    return feats
