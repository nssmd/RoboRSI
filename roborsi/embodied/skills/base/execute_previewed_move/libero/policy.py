"""Consume a state-bound preview token and execute its exact LIBERO move."""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def _execute_move(state, args: dict[str, Any]):
    from roborsi.embodied.skills.base.move_to_pose.libero.policy import (
        dispatch_runtime,
    )

    return dispatch_runtime(state, args)


def _attach_fresh_head(state) -> None:
    from roborsi.embodied.skills.base.look.libero.policy import dispatch_runtime

    dispatch_runtime(state, {"camera": "head"})


def dispatch_runtime(state, args: dict[str, Any]):
    preview_id = str(args.get("preview_id") or "").strip()
    previews = getattr(state, "_libero_move_previews", None)
    preview = previews.get(preview_id) if isinstance(previews, dict) else None
    if not isinstance(preview, dict):
        return (
            {"ok": False, "reason": "unknown, stale, or consumed preview_id"},
            state.env.take_snapshot(),
        )
    if int(preview.get("generation", -1)) != int(state.env.orbit_generation()):
        previews.pop(preview_id, None)
        return (
            {"ok": False, "reason": "preview invalidated by a newer observation or action"},
            state.env.take_snapshot(),
        )
    control = LiberoControl(state.env)
    ee_pos, _, _ = control.read_pose()
    preview_ee = np.asarray(preview.get("ee_pos"), dtype=float)
    if preview_ee.shape != (3,) or float(np.linalg.norm(ee_pos - preview_ee)) > 0.015:
        previews.pop(preview_id, None)
        return (
            {"ok": False, "reason": "robot moved after preview"},
            state.env.take_snapshot(),
        )
    move_args = dict(preview.get("args") or {})
    trajectory = preview.get("trajectory")
    if not isinstance(trajectory, list):
        previews.pop(preview_id, None)
        return (
            {"ok": False, "reason": "preview has no committed trajectory"},
            state.env.take_snapshot(),
        )
    move_args["_planned_trajectory"] = trajectory
    previews.pop(preview_id, None)
    result, observation = _execute_move(state, move_args)
    _attach_fresh_head(state)
    output = dict(result)
    output["preview_id"] = preview_id
    output["preview_consumed"] = True
    return output, observation
