"""base.robotwin.visual_diff — before/after frame diff for replan.

Two modes:
  snapshot: capture current camera frame, cache as "before" anchor.
  diff:     capture current frame as "after", build side-by-side panel
            (before | after | abs-diff heatmap), ask VLM: did the
            intended change happen?
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import numpy as np


_ANCHOR_CACHE: dict[str, dict[str, Any]] = {}


def dispatch_runtime(state, args: dict[str, Any]):
    import cv2
    from roborsi.embodied.agent_loop.rollout import _snapshot

    mode = str(args.get("mode", "")).lower()
    if mode not in {"snapshot", "diff"}:
        return ({"ok": False, "reason": "mode must be 'snapshot' or 'diff'"},
                _snapshot(state.env))
    cam = str(args.get("camera", "head_camera"))
    anchor_id = str(args.get("anchor_id", "last"))

    impl = state.env._impl
    impl._update_render(); impl.cameras.update_picture()
    rgb_dict = impl.cameras.get_rgb().get(cam)
    if rgb_dict is None:
        return ({"ok": False, "reason": f"camera {cam!r} not available"},
                _snapshot(state.env))
    rgb = rgb_dict["rgb"]
    if rgb.dtype != np.uint8:
        rgb = ((rgb * 255).clip(0, 255).astype(np.uint8)
               if rgb.max() <= 1 else rgb.astype(np.uint8))

    workdir = Path(getattr(state, "workdir", "/tmp/visual-diff"))
    workdir.mkdir(parents=True, exist_ok=True)

    if mode == "snapshot":
        anchor_path = workdir / f"diff_before_{anchor_id}.jpg"
        cv2.imwrite(str(anchor_path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        _ANCHOR_CACHE[anchor_id] = {"rgb": rgb.copy(), "camera": cam,
                                      "path": str(anchor_path)}
        return ({"ok": True, "mode": "snapshot", "anchor_id": anchor_id,
                 "camera": cam, "anchor_path": str(anchor_path),
                 "note": "Before-frame cached. Run mode='diff' after the action."},
                _snapshot(state.env))

    # mode == "diff"
    anchor = _ANCHOR_CACHE.get(anchor_id)
    if anchor is None:
        return ({"ok": False,
                 "reason": f"no anchor for id {anchor_id!r}; call mode=snapshot first"},
                _snapshot(state.env))
    if anchor["camera"] != cam:
        return ({"ok": False,
                 "reason": (f"anchor camera {anchor['camera']!r} ≠ current {cam!r}. "
                            "Diff must use the same camera as the snapshot.")},
                _snapshot(state.env))
    before = anchor["rgb"]
    after = rgb
    # Pixel-wise abs diff → grayscale heatmap → color map.
    diff = np.abs(before.astype(np.int16) - after.astype(np.int16)).sum(axis=-1)
    diff_norm = np.clip(diff * (255.0 / max(diff.max(), 1)), 0, 255).astype(np.uint8)
    heatmap = cv2.applyColorMap(diff_norm, cv2.COLORMAP_HOT)
    before_bgr = cv2.cvtColor(before, cv2.COLOR_RGB2BGR)
    after_bgr = cv2.cvtColor(after, cv2.COLOR_RGB2BGR)
    panel = np.concatenate([before_bgr, after_bgr, heatmap], axis=1)
    cv2.putText(panel, "BEFORE", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    cv2.putText(panel, "AFTER", (before.shape[1] + 8, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    cv2.putText(panel, "DIFF (hot)", (2 * before.shape[1] + 8, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    panel_path = workdir / f"diff_panel_{anchor_id}_{len(list(workdir.glob('diff_panel_*.jpg'))):03d}.jpg"
    cv2.imwrite(str(panel_path), panel)

    expected = (args.get("expected_change") or "").strip()
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image
    model = os.environ.get("ROBORSI_PERCEPTION_MODEL", DEFAULT_MODEL)
    system = (
        "You see a 3-panel image: BEFORE | AFTER | DIFF heatmap (hot = "
        "changed pixels). Compare BEFORE and AFTER. Report on TWO things:\n"
        "  CHANGED: did something visibly change at all? (yes/no)\n"
        f"  MATCH: did the EXPECTED change happen? Expected: {expected!r}\n"
        "Reply on TWO lines exactly:\n"
        "  CHANGED: YES or NO\n"
        "  MATCH: YES or NO\n"
        "Then a one-line reason."
    )
    user = "Did the expected change happen? Inspect the 3-panel image."
    raw = _call_vlm_image(model, system, user, panel_path).strip()
    m_chg = re.search(r"CHANGED\s*:\s*(YES|NO)", raw, re.IGNORECASE)
    m_match = re.search(r"MATCH\s*:\s*(YES|NO)", raw, re.IGNORECASE)
    changed = bool(m_chg and m_chg.group(1).upper() == "YES")
    matches = bool(m_match and m_match.group(1).upper() == "YES")
    return ({"ok": True, "mode": "diff", "anchor_id": anchor_id,
             "changed": changed, "matches_expectation": matches,
             "vlm_reason": raw[:240],
             "panel_path": str(panel_path),
             "n_changed_pixels": int((diff > 30).sum()),
             "note": ("3-panel BEFORE|AFTER|DIFF asked to VLM. Use "
                      "matches_expectation to gate replan: False → "
                      "the action did not achieve the intent; retry or "
                      "switch strategy.")},
            _snapshot(state.env))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
