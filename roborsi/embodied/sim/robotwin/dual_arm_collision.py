"""dual_arm_collision — per-plan dual-arm sphere collision check using the
BiCoord-shipped `collision_aloha_*.yml` sphere data.

cuRobo's left/right planners are configured with `extra_links: null` and
therefore have ZERO knowledge of the other arm. plan_path returning
Success only means the planned arm doesn't self-collide; it could still
sweep through the other arm or its held object.

This module provides `check_pair_collision(impl, holding_arm,
container_arm, candidate_qpos, held_object_spheres)`:

  1. Snapshots both arms' current qpos.
  2. Sets the holding arm to the candidate end qpos (kinematic-only,
     no scene.step — link world poses update immediately).
  3. Reads each link's world pose from both arm articulations.
  4. Transforms every collision sphere center to the world frame.
  5. Pairwise distance check across holding-arm spheres vs container-arm
     spheres (+ held-object spheres attached to container-arm last link).
  6. Restores both arms' qpos.

Returns (collides: bool, min_clearance: float, closest_pair: tuple|None).
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import numpy as np
import yaml

_BICOORD_ROOT = Path(
    os.environ.get(
        "ROBORSI_BICOORD_ROOT",
        str(Path.home() / "BiCoord-Bench"),
    )
)
_COLLISION_YML = (
    _BICOORD_ROOT
    / "assets/embodiments/aloha-agilex/collision_aloha_left.yml"
)

# Held-object sphere offsets (attached to the *_link8 fingertip frame).
# Numbers tuned for the silver bowl (~6cm radius, 4cm wall) and red block
# (~4cm cube).
_BOWL_ATTACH_SPHERES = [
    {"center": [0.08, 0.0, 0.0], "radius": 0.06},  # ~bowl ball
]
_BLOCK_ATTACH_SPHERES = [
    {"center": [0.07, 0.0, 0.0], "radius": 0.03},  # ~block ball
]


def _load_sphere_data() -> dict[str, list[dict]]:
    with _COLLISION_YML.open() as f:
        d = yaml.safe_load(f)
    return d.get("collision_spheres", {}) or {}


_SPHERE_CACHE: dict[str, list[dict]] | None = None


def _sphere_data() -> dict[str, list[dict]]:
    global _SPHERE_CACHE
    if _SPHERE_CACHE is None:
        _SPHERE_CACHE = _load_sphere_data()
    return _SPHERE_CACHE


def _link_world_spheres(link, local_spheres: list[dict]) -> list[tuple[np.ndarray, float]]:
    """Transform link-local sphere centers to world frame."""
    if not local_spheres:
        return []
    pose = link.get_pose()
    T = pose.to_transformation_matrix()  # 4x4
    R = T[:3, :3]
    p = T[:3, 3]
    out: list[tuple[np.ndarray, float]] = []
    for s in local_spheres:
        c_local = np.array(s["center"], dtype=np.float64)
        c_world = R @ c_local + p
        out.append((c_world, float(s["radius"])))
    return out


def _arm_link_prefix(arm: str) -> str:
    return "fl_" if arm == "left" else "fr_"


def _collect_arm_spheres(entity, arm: str,
                          attach_spheres_on_link8: list[dict] | None = None
                          ) -> list[tuple[np.ndarray, float, str]]:
    """Walk an arm entity's links and emit (center_world, radius, link_name)
    tuples for every collision sphere defined for that arm's links."""
    prefix = _arm_link_prefix(arm)
    sphere_data = _sphere_data()
    out: list[tuple[np.ndarray, float, str]] = []
    for link in entity.get_links():
        name = link.get_name()
        if not name.startswith(prefix) and not name.startswith(arm + "_camera"):
            continue
        for c, r in _link_world_spheres(link, sphere_data.get(name, [])):
            out.append((c, r, name))
        if (attach_spheres_on_link8 and name == f"{prefix}link8"):
            for c, r in _link_world_spheres(link, attach_spheres_on_link8):
                out.append((c, r, f"{name}+attach"))
    return out


def _get_arm_entity(impl, arm: str):
    """Return the SAPIEN articulation entity for the given arm."""
    if hasattr(impl.robot, "left_entity") and arm == "left":
        return impl.robot.left_entity
    if hasattr(impl.robot, "right_entity") and arm == "right":
        return impl.robot.right_entity
    raise RuntimeError(f"impl.robot has no {arm}_entity attribute")


def check_pair_collision(impl, *, holding_arm: str, container_arm: str,
                          candidate_qpos: np.ndarray | list[float],
                          holding_attach: list[dict] | None = None,
                          container_attach: list[dict] | None = None,
                          clearance_threshold: float = -0.005,
                          ) -> dict[str, Any]:
    """Check whether moving `holding_arm` to `candidate_qpos` causes the
    holding arm (incl. held object spheres) to collide with the container
    arm (incl. its held object spheres).

    `candidate_qpos` must match the holding-arm articulation's qpos
    dimensionality (BiCoord aloha left/right entities are separate
    articulations, each with 8 joints incl. 2 finger joints).

    `clearance_threshold` < 0 means accept up to that much penetration.
    Default -0.005 = 5mm tolerance for sphere-approximation slack.

    Returns dict:
      ok: True if check ran without error
      collides: True if min_clearance < clearance_threshold
      min_clearance: smallest (d - (r1+r2)) across all pairs
      closest_pair: ((link_a, link_b), distance) for the worst pair
    """
    # Arg validation FIRST — fail fast on bad inputs without touching sim.
    if holding_arm not in ("left", "right"):
        return {"ok": False, "reason": f"holding_arm must be left/right, got {holding_arm!r}",
                 "collides": False, "min_clearance": float("inf")}
    if container_arm not in ("left", "right"):
        return {"ok": False, "reason": f"container_arm must be left/right, got {container_arm!r}",
                 "collides": False, "min_clearance": float("inf")}
    if holding_arm == container_arm:
        return {"ok": False, "reason": "holding_arm and container_arm must differ",
                 "collides": False, "min_clearance": float("inf")}

    try:
        holding_entity = _get_arm_entity(impl, holding_arm)
        container_entity = _get_arm_entity(impl, container_arm)
    except RuntimeError as e:
        return {"ok": False, "reason": str(e),
                "collides": False, "min_clearance": float("inf")}

    # Snapshot both arms' qpos.
    h_qpos_orig = np.array(holding_entity.get_qpos(), copy=True)
    c_qpos_orig = np.array(container_entity.get_qpos(), copy=True)
    cand = np.asarray(candidate_qpos, dtype=np.float64)
    if cand.shape != h_qpos_orig.shape:
        return {"ok": False,
                 "reason": f"candidate_qpos shape {cand.shape} != "
                            f"holding-arm qpos shape {h_qpos_orig.shape}",
                 "collides": False, "min_clearance": float("inf")}

    try:
        holding_entity.set_qpos(cand)
        h_spheres = _collect_arm_spheres(holding_entity, holding_arm,
                                          attach_spheres_on_link8=holding_attach)
        c_spheres = _collect_arm_spheres(container_entity, container_arm,
                                          attach_spheres_on_link8=container_attach)
    finally:
        holding_entity.set_qpos(h_qpos_orig)
        container_entity.set_qpos(c_qpos_orig)

    if not h_spheres or not c_spheres:
        import sys
        link_names_h = sorted({l.get_name() for l in holding_entity.get_links()})
        link_names_c = sorted({l.get_name() for l in container_entity.get_links()})
        print(f"[dual_arm_collision] no spheres parsed: "
              f"holding={holding_arm} ({len(h_spheres)} spheres, links={link_names_h[:5]}), "
              f"container={container_arm} ({len(c_spheres)} spheres, links={link_names_c[:5]})",
              file=sys.stderr, flush=True)
        return {"ok": False,
                 "reason": (f"no spheres parsed: h={len(h_spheres)} c={len(c_spheres)}. "
                              f"link prefixes maybe wrong. holding links: {link_names_h[:5]}; "
                              f"container links: {link_names_c[:5]}"),
                 "collides": False, "min_clearance": float("inf"),
                 "h_sphere_count": len(h_spheres),
                 "c_sphere_count": len(c_spheres),
                 "h_link_names": link_names_h,
                 "c_link_names": link_names_c}

    min_clearance = math.inf
    worst_pair: tuple[str, str] | None = None
    for hc, hr, hn in h_spheres:
        for cc, cr, cn in c_spheres:
            d = float(np.linalg.norm(hc - cc))
            clearance = d - (hr + cr)
            if clearance < min_clearance:
                min_clearance = clearance
                worst_pair = (hn, cn)

    return {"ok": True,
             "collides": min_clearance < clearance_threshold,
             "min_clearance": min_clearance,
             "closest_pair": worst_pair,
             "h_sphere_count": len(h_spheres),
             "c_sphere_count": len(c_spheres)}


def check_trajectory_collision(impl, *, holding_arm: str, container_arm: str,
                                qpos_trajectory: np.ndarray | list,
                                holding_attach: list[dict] | None = None,
                                container_attach: list[dict] | None = None,
                                clearance_threshold: float = -0.005,
                                stride: int = 4,
                                ) -> dict[str, Any]:
    """Check collision at multiple waypoints along a planned trajectory
    (swept-volume approximation). Stops at the first colliding waypoint.

    cuRobo's plan_path returns `result["position"]` shaped (N, dof).
    Sample every `stride`-th waypoint to keep cost bounded while
    catching mid-flight collisions that the end-pose-only check misses.

    Returns same shape as `check_pair_collision` plus:
      colliding_step: int | None — which waypoint triggered the rejection
      n_checked: int
    """
    traj = np.asarray(qpos_trajectory, dtype=np.float64)
    if traj.ndim != 2:
        return {"ok": False, "reason": f"trajectory must be 2D, got {traj.shape}",
                 "collides": False, "min_clearance": float("inf")}
    n = traj.shape[0]
    if n == 0:
        return {"ok": False, "reason": "empty trajectory",
                 "collides": False, "min_clearance": float("inf")}

    # Sample waypoints: always include start + end, then every `stride`.
    indices = sorted(set([0, n - 1] + list(range(0, n, stride))))
    min_clearance = math.inf
    worst_pair = None
    colliding_step = None
    for i in indices:
        res = check_pair_collision(
            impl, holding_arm=holding_arm, container_arm=container_arm,
            candidate_qpos=traj[i],
            holding_attach=holding_attach,
            container_attach=container_attach,
            clearance_threshold=clearance_threshold,
        )
        if not res.get("ok"):
            return res  # bubble up parse error
        c = res.get("min_clearance", math.inf)
        if c < min_clearance:
            min_clearance = c
            worst_pair = res.get("closest_pair")
        if res.get("collides"):
            colliding_step = i
            break

    return {"ok": True,
             "collides": colliding_step is not None,
             "min_clearance": min_clearance,
             "closest_pair": worst_pair,
             "colliding_step": colliding_step,
             "n_checked": len(indices),
             "n_total": n}


def held_object_spheres(kind: str) -> list[dict]:
    """Return canonical sphere set for an attached object."""
    if kind == "bowl":
        return list(_BOWL_ATTACH_SPHERES)
    if kind == "block":
        return list(_BLOCK_ATTACH_SPHERES)
    return []
