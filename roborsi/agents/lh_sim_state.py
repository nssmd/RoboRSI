"""Sim-state utilities for the LH executor — snapshot / restore / ground-truth.

These are pure SAPIEN scene-state helpers (no LH control flow): they read the
authoritative ground truth for the Reviewer, capture multi-camera review
frames, and snapshot/restore the full scene (actor poses + velocities +
articulation qpos/qvel + gripper drive targets) so a stuck atomic can rollback
to a prior end-state and re-run. Split out of lh_executor.py to keep that
module's orchestration logic focused (and under the 1000-line file cap).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def snapshot_review_frames(env, out_dir: Path) -> list[tuple[str, Path]]:
    """Grab MULTIPLE camera RGBs (head + both wrist cams) for the Reviewer to
    judge from independently. Per 2026-06-16 (ENPIRE-style multi-camera
    verification): a single head view often mis-judges (phantom done).
    Capturing head + left_wrist + right_wrist lets the Reviewer check the
    success predicate from each view and AND the verdicts. Returns list of
    (camera_name, path); empty if obs unreadable."""
    out: list[tuple[str, Path]] = []
    try:
        from roborsi.embodied.agent_loop.rollout import _snapshot as _sim_snapshot
        from roborsi.embodied.sim.robotwin.robotwin_agent import _write_jpg
        obs = _sim_snapshot(env)
        for cam in ("head_camera", "left_camera", "right_camera"):
            img = obs.images.get(cam)
            if img is None:
                continue
            p = out_dir / f"review_gt_{cam}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            _write_jpg(p, img)
            out.append((cam, p))
    except Exception:
        pass
    return out


def ground_truth_state(env) -> str:
    """Authoritative sim state at this moment — gripper vals + actor
    z's + sim's check_success predicate. Injected into Reviewer
    context so it doesn't rubber-stamp Engineer's claimed-done
    when sim disagrees. V7 review caught: Engineer printed
    'held: True' but sim said success=False; Reviewer believed
    the stdout. With this block in the prompt, Reviewer is forced
    to reconcile claim vs reality."""
    impl = getattr(env, "_impl", None)
    if impl is None:
        return "[no env]"
    lines = ["=== AUTHORITATIVE SIM GROUND TRUTH (post-attempt) ==="]
    try:
        lv = float(impl.robot.get_left_gripper_val())
        rv = float(impl.robot.get_right_gripper_val())
        lines.append(f"left_gripper_val={lv:.3f}  right_gripper_val={rv:.3f}")
        lines.append("(val<0.005 = closed/maybe-holding; val>0.6 = open/empty)")
    except Exception as e:
        lines.append(f"gripper read failed: {type(e).__name__}: {e}")
    try:
        check = getattr(impl, "check_success", None) or \
                getattr(impl, "_check_success", None)
        if check is not None:
            lines.append(f"sim.check_task_success() = {bool(check())}")
            lines.append("(this is the FULL-LH predicate; intermediate "
                          "atomics expected False; True means whole LH done)")
    except Exception as e:
        lines.append(f"check_success failed: {type(e).__name__}: {e}")
    try:
        for a in (impl.scene.get_all_actors() or []):
            try:
                name = a.get_name() or "?"
                p = a.get_pose().p
                lines.append(f"  actor {name:22s} xyz=({float(p[0]):+.3f},"
                              f"{float(p[1]):+.3f},{float(p[2]):+.3f})")
            except Exception:
                continue
    except Exception:
        pass
    lines.append("=== END GROUND TRUTH ===")
    lines.append("If Engineer claimed success but actor z's / gripper vals "
                  "above are inconsistent with the success criteria — "
                  "verdict MUST be `retry`, not `done`.")
    return "\n".join(lines)


def sim_check_success(env, settle_ticks: int = 0) -> bool:
    """The sim's own full-LH check_success predicate (True iff the whole
    task is genuinely complete). Used as a hard backstop on atomic success
    so a Reviewer `done` verdict cannot turn a sim-False state into a
    recorded LH completion. Intermediate atomics legitimately read False.

    ``settle_ticks`` steps physics first so a just-released object / still-
    opening gripper reaches REST before the predicate is read — many
    RoboTwin predicates require grippers OPEN + settled poses, so reading
    immediately after the final tool call can false-negative a genuine
    completion. This is accurate terminal-state measurement (the episode
    has already ended), not a mid-loop success grab. Never raises."""
    impl = getattr(env, "_impl", None)
    if impl is None:
        return False
    if settle_ticks:
        try:
            for _ in range(settle_ticks):
                impl.scene.step()
        except Exception:
            pass
    check = getattr(impl, "check_success", None) or \
            getattr(impl, "_check_success", None)
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return False


def snapshot_scene(env) -> Any:
    """Full SAPIEN state snapshot — actor poses + velocities + every
    articulation's qpos + qvel + root pose. The plain `pack_poses`
    only stored entity poses, so a robot arm in mid-motion or an
    actor with linear velocity stayed broken on restore. V6 review
    2026-06-10 caught this: cup_2 stayed off-world across attempts
    because pack_poses didn't restore the velocity that flung it.
    """
    import numpy as np
    impl = getattr(env, "_impl", None)
    scene = getattr(impl, "scene", None) if impl else None
    if scene is None:
        return None
    snap: dict[str, Any] = {}
    # Actors: pose + linear_velocity + angular_velocity.
    actors_state = []
    for actor in (scene.get_all_actors() or []):
        try:
            p = actor.get_pose()
            rec = {
                "name": actor.get_name(),
                "pose_p": list(p.p), "pose_q": list(p.q),
                "lvel": (np.array(actor.get_linear_velocity(), copy=True).tolist()
                         if hasattr(actor, "get_linear_velocity") else None),
                "avel": (np.array(actor.get_angular_velocity(), copy=True).tolist()
                         if hasattr(actor, "get_angular_velocity") else None),
            }
            actors_state.append(rec)
        except Exception:
            continue
    snap["actors"] = actors_state
    # Articulations: root pose + qpos + qvel + per-active-joint drive
    # targets (so a closed gripper holding an actor stays closed and
    # holding after restore — qpos alone sets the joint angle but the
    # drive target is what keeps the gripper pressing on the held
    # object; without it, the gripper relaxes within a few sim steps
    # and the held actor falls. V36 bug 2026-06-14: atomic_2 always
    # started with left gripper open after restore.)
    arts_state = []
    for art in (scene.get_all_articulations() or []):
        try:
            drives = []
            for j in (art.get_active_joints() or []):
                drives.append({
                    "target": (j.get_drive_target().tolist()
                                if hasattr(j.get_drive_target(), "tolist")
                                else list(j.get_drive_target())),
                    "vtarget": (j.get_drive_velocity_target().tolist()
                                 if hasattr(j.get_drive_velocity_target(),
                                            "tolist")
                                 else list(j.get_drive_velocity_target())),
                })
            rec = {
                "name": art.get_name() if hasattr(art, "get_name") else "",
                "root_p": list(art.get_root_pose().p),
                "root_q": list(art.get_root_pose().q),
                "qpos": np.array(art.get_qpos(), copy=True).tolist(),
                "qvel": (np.array(art.get_qvel(), copy=True).tolist()
                         if hasattr(art, "get_qvel") else None),
                "drives": drives,
            }
            arts_state.append(rec)
        except Exception:
            continue
    snap["articulations"] = arts_state
    return snap


def restore_scene(env, snapshot) -> bool:
    import numpy as np
    if not snapshot:
        return False
    impl = getattr(env, "_impl", None)
    scene = getattr(impl, "scene", None) if impl else None
    if scene is None:
        return False
    # Restore actors by name match.
    actors_by_name = {a.get_name(): a for a in (scene.get_all_actors() or [])
                      if hasattr(a, "get_name")}
    for rec in snapshot.get("actors", []):
        actor = actors_by_name.get(rec.get("name"))
        if actor is None:
            continue
        try:
            from sapien.core import Pose
            actor.set_pose(Pose(rec["pose_p"], rec["pose_q"]))
            if rec.get("lvel") is not None and hasattr(actor, "set_linear_velocity"):
                actor.set_linear_velocity(np.array(rec["lvel"]))
            if rec.get("avel") is not None and hasattr(actor, "set_angular_velocity"):
                actor.set_angular_velocity(np.array(rec["avel"]))
            # Wake a body that fell asleep off-scene after a fling — a
            # sleeping rigid body IGNORES set_pose and stays stuck, so
            # every retry kept seeing the bowl at x=-9.21 and the atomic
            # could never restart (V75 atomic_0). Waking forces the new
            # pose to register on the next step.
            for comp in (actor.get_components()
                         if hasattr(actor, "get_components") else []):
                if hasattr(comp, "wake_up"):
                    comp.wake_up()
        except Exception:
            continue
    # Restore articulations (robot arm joints + root frame).
    arts_by_name = {a.get_name(): a for a in (scene.get_all_articulations() or [])
                     if hasattr(a, "get_name")}
    for rec in snapshot.get("articulations", []):
        art = arts_by_name.get(rec.get("name"))
        if art is None:
            # Fallback: only one articulation, use it.
            arts = scene.get_all_articulations() or []
            if len(arts) == 1:
                art = arts[0]
            else:
                continue
        try:
            from sapien.core import Pose
            art.set_root_pose(Pose(rec["root_p"], rec["root_q"]))
            art.set_qpos(np.array(rec["qpos"]))
            if rec.get("qvel") is not None and hasattr(art, "set_qvel"):
                art.set_qvel(np.array(rec["qvel"]))
            # Re-apply drive targets so closed gripper keeps gripping.
            drives = rec.get("drives") or []
            joints = art.get_active_joints() or []
            for j, d in zip(joints, drives):
                try:
                    j.set_drive_target(np.array(d["target"]))
                    j.set_drive_velocity_target(np.array(d["vtarget"]))
                except Exception:
                    continue
        except Exception:
            continue
    if hasattr(scene, "step"):
        # Several steps so a restored actor (esp. one teleported back
        # from off-scene) settles onto the table instead of a single
        # step leaving it mid-air or its pose write not yet applied.
        for _ in range(8):
            scene.step()
    return True
