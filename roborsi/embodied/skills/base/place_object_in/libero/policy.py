"""place_object_in — camera/depth composite place for LIBERO.

The target comes from a provided pixel/position or is localized from the
current camera frame by its public language description. The policy estimates
the drop point and rim/surface height from the segmented depth cloud, then
hovers, descends, releases, and retracts.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def _bad_uv(uv) -> bool:
    """None, or the (128,128) 'found nothing' sentinel on a 256px LIBERO frame."""
    return uv is None or (abs(int(uv[0]) - 128) <= 2 and abs(int(uv[1]) - 128) <= 2)


def _clear_localize(state, ctrl, name: str):
    """Localize a place target with the head view cleared (mirror of the grasp
    self-correct): try; if nothing / sentinel, side-step the arm and retry once.
    Pure vision, no ground truth."""
    from roborsi.embodied.skills.base._lib.libero._perception import localize_precise
    loc = localize_precise(state, name)
    if not _bad_uv(loc):
        return loc
    ee, _, _ = ctrl.read_pose()
    ctrl.servo_to([float(ee[0]) - 0.06, float(ee[1]) + 0.08, float(ee[2])],
                  gripper="close", max_iters=40)
    loc = localize_precise(state, name)
    return None if _bad_uv(loc) else loc


def _drop_from_memory(state, target_name: str, z_offset: float):
    from roborsi.embodied.skills.base._lib.libero._perception import recall_world

    world = recall_world(state, target_name)
    if world is None:
        return None
    object_height = float(getattr(state, "_held_object_height", 0.08))
    object_height = float(np.clip(object_height, 0.02, 0.16))
    grasp_z_offset = float(getattr(state, "_held_grasp_z_offset", 0.0))
    grasp_z_offset = float(np.clip(grasp_z_offset, -0.04, 0.04))
    return np.asarray([
        world[0],
        world[1],
        world[2] + object_height / 2.0 + z_offset + grasp_z_offset,
    ], dtype=float)


def _servo_to_drop(ctrl, drop: np.ndarray, hover: float):
    current, _, _ = ctrl.read_pose()
    travel_z = max(float(current[2]) + 0.06, float(drop[2]) + hover)
    lift_reached, _ = ctrl.servo_to(
        [float(current[0]), float(current[1]), travel_z],
        gripper="close",
        max_iters=100,
    )
    hover_reached, _ = ctrl.servo_to(
        [float(drop[0]), float(drop[1]), travel_z],
        gripper="close",
        max_iters=140,
    )
    descent_reached = True
    for z in np.linspace(travel_z, float(drop[2]), 5)[1:]:
        reached, _ = ctrl.servo_to(
            [float(drop[0]), float(drop[1]), float(z)],
            gripper="close",
            max_iters=100,
        )
        descent_reached = descent_reached and reached
        if not reached:
            break
    return lift_reached, hover_reached, descent_reached


def dispatch_runtime(state, args: dict[str, Any]):
    env = state.env
    ctrl = LiberoControl(env)
    hover = float(args.get("hover", 0.12))
    z_offset = float(args.get("z_offset", 0.03))   # release just above the rim, not +6cm (dropped-too-high → object bounces out / lands beside)

    pos = args.get("pos")
    pixel = args.get("pixel")
    target_name = str(args.get("object") or "").strip()
    from roborsi.embodied.skills.base._lib.libero._perception import (
        recall_pixel,
        remember_pixel,
    )

    if isinstance(pixel, (list, tuple)) and len(pixel) == 2:
        pixel = list(remember_pixel(state, target_name, pixel))

    # A bare object=<name> is localized from the current camera observation.
    if pixel is None and pos is None and target_name:
        from roborsi.embodied.skills.base._lib.libero._perception import (
            _fix_on,
            _place_fix_on,
            localize_precise,
            retreat_from_head_view,
        )
        if _fix_on():
            loc = recall_pixel(state, target_name)
            if loc is None:
                # The held object / arm can occlude the target. Clear the head
                # view only when no earlier visual target can be reused.
                if _place_fix_on():
                    retreat_from_head_view(env, ctrl)
                else:
                    ee, _, _ = ctrl.read_pose()
                    ctrl.servo_to(
                        [float(ee[0]), float(ee[1]), float(ee[2]) + 0.18],
                        gripper="close",
                        max_iters=50,
                    )
                loc = _clear_localize(state, ctrl, target_name)
        else:
            loc = localize_precise(state, target_name)
            if loc is None:
                ee, _, _ = ctrl.read_pose()
                ctrl.servo_to([float(ee[0]), float(ee[1]), 0.38], gripper="close", max_iters=45)
                loc = localize_precise(state, target_name)
        if loc is None:
            return ({"ok": False, "reason": f"could not perceive container '{target_name}' by vision — look() + find_pixel(container)"},
                    env.take_snapshot())
        pixel = [int(loc[0]), int(loc[1])]

    if isinstance(pos, (list, tuple)) and len(pos) == 3:
        drop = np.asarray(pos, dtype=float)                 # explicit release point
    elif isinstance(pixel, (list, tuple)) and len(pixel) == 2:
        drop = _drop_from_memory(state, target_name, z_offset)
        if drop is None:
            from roborsi.embodied.skills.base._lib.libero._perception import (
                _place_fix_on,
                object_cloud,
                retreat_from_head_view,
            )

            if _place_fix_on():
                retreat_from_head_view(env, ctrl)
            cloud = object_cloud(env, int(pixel[0]), int(pixel[1]), z_band=0.18)
            if cloud is None or len(cloud) < 20:
                return ({"ok": False, "reason": "could not perceive the container at that pixel — re-find_pixel the container"},
                        env.take_snapshot())
            cx, cy = float(np.median(cloud[:, 0])), float(np.median(cloud[:, 1]))
            rim_z = float(np.percentile(cloud[:, 2], 85))
            drop = np.array([cx, cy, rim_z + z_offset])
    else:
        return ({"ok": False, "reason": "give pos=[x,y,z] or object=<name>"},
                env.take_snapshot())

    lift_reached, hover_reached, drop_reached = _servo_to_drop(
        ctrl,
        drop,
        hover,
    )
    release_ee, _, _ = ctrl.read_pose()
    error = np.asarray(release_ee, dtype=float) - drop
    horizontal_error = float(np.linalg.norm(error[:2]))
    vertical_error = abs(float(error[2]))
    if (
        not hover_reached
        or horizontal_error > 0.025
        or vertical_error > 0.03
    ):
        # The arm WEDGED (kinematic limit) short of the target — releasing here
        # drops the object far from the goal and falsely reports success (the
        # "released but arm never moved, dropped at the pickup spot" failure).
        return ({"ok": False, "released": False,
                 "reason": "could not servo over the drop point (arm wedged short of the target) — "
                           "retry from a clearer approach",
                 "ee_pos": [round(float(v), 4) for v in release_ee],
                 "target_position": [round(float(v), 4) for v in drop],
                 "horizontal_error": round(horizontal_error, 4),
                 "vertical_error": round(vertical_error, 4),
                 "lift_reached": bool(lift_reached),
                 "hover_reached": bool(hover_reached),
                 "drop_reached": bool(drop_reached)},
                env.take_snapshot())
    ctrl.set_gripper(close=False)                            # release
    ctrl.servo_to([drop[0], drop[1], drop[2] + hover], gripper="open", max_iters=60)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": True, "released": True,
             "target_pixel": list(pixel) if pixel is not None else None,
             "target_position": [round(float(v), 4) for v in drop],
             "release_ee_pos": [round(float(v), 4) for v in release_ee],
             "horizontal_error": round(horizontal_error, 4),
             "vertical_error": round(vertical_error, 4),
             "lift_reached": bool(lift_reached),
             "hover_reached": bool(hover_reached),
             "drop_reached": bool(drop_reached),
             "ee_pos": [round(float(v), 4) for v in ee]},
            env.take_snapshot())
