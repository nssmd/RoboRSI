"""base.robotwin.probe_ik_workspace - IK/plan reachability probe (no sim
mutation). For a fixed target (x, y), grid over TCP z and one-or-more wrist
orientations (named approaches AND/OR an explicit custom quat); report which
combinations the arm's plan_path actually accepts - the REALIZED descent
floor.

Per user 2026-06-12: Engineer / Reviewer kept reporting 'all IK fails' after
only testing top-down - this gives a real workspace measurement first.

Two ways to specify the wrist orientation(s):
  * quat: a single explicit wxyz wrist quaternion (e.g. a task place quat
    that is NOT one of the canned approaches). Probed as label 'custom'.
    The flange offset is derived from the quat itself
    (outward = R(quat) @ EE-local +X), so any orientation can be probed.
  * approaches: a list (or literal-string of a list) of NAMED approaches
    from _APPROACH_QUATS. Default (only when no quat given): all named.

Unlike is_reachable (which IK-checks a HOVER pose at z+hover_m and so
over-reports the reachable floor), this probes plan_path at the EXACT target
TCP z across the z-range.
"""
from __future__ import annotations

import ast
import math
from typing import Any

import numpy as np


_APPROACH_QUATS = {
    "top_down":     [0.5, -0.5, 0.5, 0.5],
    "lateral_-x":   [0.0, 0.7071068, 0.7071068, 0.0],
    "lateral_+x":   [0.7071068, 0.0, 0.0, 0.7071068],
    "lateral_-y":   [0.5, 0.5, 0.5, 0.5],
    "lateral_+y":   [0.5, -0.5, 0.5, -0.5],
    "tilt_30_-x":   [0.2588, -0.6597, 0.6597, 0.2588],
    "tilt_30_+x":   [0.6597, -0.2588, 0.2588, 0.6597],
}

_APPROACH_OUTWARD = {
    "top_down":     (0.0, 0.0, -1.0),
    "lateral_-x":   (-1.0, 0.0, 0.0),
    "lateral_+x":   (+1.0, 0.0, 0.0),
    "lateral_-y":   (0.0, -1.0, 0.0),
    "lateral_+y":   (0.0, +1.0, 0.0),
    "tilt_30_-x":   (-math.cos(math.radians(30)), 0.0, -math.sin(math.radians(30))),
    "tilt_30_+x":   (+math.cos(math.radians(30)), 0.0, -math.sin(math.radians(30))),
}

_TCP_OFFSET = 0.1556  # ALOHA_TCP_IN_EE_LOCAL[0]


def _quat_wxyz_to_R(q) -> np.ndarray:
    w, x, y, z = (float(v) for v in q)
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if n < 1e-9:
        return np.eye(3)
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
        [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
    ])


def _outward_for_quat(quat) -> tuple[float, float, float]:
    """Gripper approach (outward) axis in world = R(quat) @ EE-local +X.
    Reproduces the hardcoded _APPROACH_OUTWARD for the canned approaches."""
    v = _quat_wxyz_to_R(quat) @ np.array([1.0, 0.0, 0.0])
    return float(v[0]), float(v[1]), float(v[2])


def _flange_xyz(tcp_x: float, tcp_y: float, tcp_z: float,
                outward: tuple[float, float, float]) -> tuple[float, float, float]:
    ux, uy, uz = outward
    return (tcp_x - ux * _TCP_OFFSET,
            tcp_y - uy * _TCP_OFFSET,
            tcp_z - uz * _TCP_OFFSET)


def _coerce_list(val):
    """Accept a real list/tuple, or a literal-string of one; else None."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            val = ast.literal_eval(val)
        except (ValueError, SyntaxError):
            return None
    return list(val) if isinstance(val, (list, tuple)) else None


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot

    arm = str(args.get("arm", "")).lower()
    if arm not in ("left", "right"):
        return ({"ok": False, "reason": "arm must be 'left' or 'right'"},
                _snapshot(state.env))
    x = args.get("x"); y = args.get("y")
    if x is None or y is None:
        return ({"ok": False, "reason": "x, y required (target TCP world coords)"},
                _snapshot(state.env))
    x = float(x); y = float(y)
    z_min = float(args.get("z_min", 0.73))
    z_max = float(args.get("z_max", 0.95))
    z_step = float(args.get("z_step", 0.02))

    # Build {label: (quat, outward)} to probe.
    probes: dict[str, tuple[list, tuple]] = {}
    # (1) explicit custom quat - e.g. a task place quat not in the canned set.
    custom_quat = _coerce_list(args.get("quat"))
    if custom_quat is not None and len(custom_quat) == 4:
        q = [float(v) for v in custom_quat]
        probes["custom"] = (q, _outward_for_quat(q))
    # (2) named approaches (default ALL only when no custom quat was given).
    raw_appr = args.get("approaches")
    if raw_appr is not None or not probes:
        names = _coerce_list(raw_appr)
        if names is None and raw_appr is None:
            names = list(_APPROACH_QUATS.keys())
        names = [a for a in (names or []) if a in _APPROACH_QUATS]
        for a in names:
            probes[a] = (_APPROACH_QUATS[a], _APPROACH_OUTWARD[a])

    if not probes:
        return ({"ok": False,
                 "reason": ("nothing to probe: pass quat=[w,x,y,z] for a custom "
                            "orientation, or approaches=[...] (a real list or a "
                            f"literal-string) from {sorted(_APPROACH_QUATS)}")},
                _snapshot(state.env))

    impl = state.env._impl
    plan_fn = (impl.robot.left_plan_path if arm == "left"
               else impl.robot.right_plan_path)

    zs = []
    z = z_min
    while z <= z_max + 1e-9:
        zs.append(round(z, 4))
        z += z_step

    per_approach: dict[str, list[float]] = {}
    best: dict[str, Any] = {"approach": None, "lowest_feasible_z": None}
    for label, (quat, outward) in probes.items():
        feasible = []
        for zt in zs:
            fx, fy, fz = _flange_xyz(x, y, zt, outward)
            pose = [fx, fy, fz, *quat]
            res = plan_fn(pose)
            if res.get("status") == "Success":
                feasible.append(zt)
        per_approach[label] = feasible
        if feasible:
            lo = min(feasible)
            if (best["lowest_feasible_z"] is None
                    or lo < best["lowest_feasible_z"]):
                best = {"approach": label, "lowest_feasible_z": lo}

    n_feasible = sum(len(v) for v in per_approach.values())
    n_total = len(probes) * len(zs)
    summary = (f"arm={arm} target=({x:.3f},{y:.3f}) z in [{z_min},{z_max}]: "
               f"{n_feasible}/{n_total} probes feasible - "
               f"best={best['approach']} (lowest z={best['lowest_feasible_z']})")
    return ({"ok": True,
             "arm": arm, "target_xy": [x, y],
             "z_range": [z_min, z_max, z_step],
             "per_approach": per_approach,
             "best": best,
             "summary": summary,
             "note": ("Probes plan_path at the EXACT target TCP z (the REALIZED "
                      "descent floor), unlike is_reachable which checks a hover "
                      "pose. lowest_feasible_z is the lowest TCP z plan_path "
                      "accepts for each orientation; pass quat=<place quat> to "
                      "probe a custom wrist orientation.")},
            _snapshot(state.env))
