"""roborsi.embodied.sim.robotwin.gripper_geom — measured aloha-agilex
TCP offset in EE-flange local frame. Replaces the hardcoded 0.18 magic
number that was both wrong sign and wrong magnitude.

Measured from arx5_description_isaac.urdf + link7.STL mesh:
  fl_link6 = EE flange (matches `impl.robot.get_left_ee_pose()`)
  joint origin fl_link7 in fl_link6 frame: (0.0846, +0.0245, 0) at joint=0 (closed)
  joint origin fl_link8 in fl_link6 frame: (0.0846, -0.0245, 0)
  link7.STL mesh max X = +0.071  → fingertip extends +0.071 m past joint origin
  → TCP (closed-finger midpoint) in EE local = (0.0846 + 0.071, 0, 0) = (0.1556, 0, 0)

Also exposes plan_and_predict_ee: cuRobo's plan_path returns status=Success
even when the optimized plan terminus only approximates the target. We
forward-kinematic the plan's last joint state to predict the actual EE
pose — letting callers reject plans whose predicted EE drifts > tolerance
BEFORE physical execution (preventing the violent partial-plan motion
that sometimes knocks objects off the table).
"""
from __future__ import annotations

import numpy as np


# Measured (NOT estimated): from URDF joint origin + mesh extent.
ALOHA_TCP_IN_EE_LOCAL = np.array([0.1556, 0.0, 0.0])


def aloha_tcp_in_ee_local(impl, arm: str = "left") -> np.ndarray:
    return ALOHA_TCP_IN_EE_LOCAL.copy()


def warmup_planner(impl, arm: str = "left") -> bool:
    """Reset cuRobo's warm-start to a near-HOME state before a grasp's plans.

    motion_gen's warm-start DRIFTS across prior tool calls, so the first
    plan_path of a grasp tool that does NOT warm up spins non-deterministically
    to the wall-time cap — the 300s deadlocks. grasp_object already fixes this
    by warming up once before its grasp; this shared helper lets EVERY grasp
    tool do the same in one line. From a warmed (near-HOME) planner, plan_path
    is nearly deterministic and fast-fails on infeasible targets instead of
    hanging. Returns False (no-op) if the planner exposes no motion_gen.

    Call ONCE at the start of a grasp (warmup builds the graph, ~seconds) —
    NOT before every plan_path."""
    planner = (getattr(impl.robot, "left_planner", None) if arm == "left"
               else getattr(impl.robot, "right_planner", None))
    mg = getattr(planner, "motion_gen", None) if planner is not None else None
    if mg is None:
        return False
    mg.warmup(enable_graph=True, warmup_js_trajopt=False, parallel_finetune=True)
    return True


def ik_feasible(impl, arm: str, flange_pose7, tol_m: float = 0.03):
    """Real cuRobo IK feasibility for a FLANGE target [x,y,z,qw,qx,qy,qz],
    checked from a NEUTRAL (mid-joint-limit) home qpos so reachability is
    config-INDEPENDENT — otherwise cuRobo mis-refuses physically-reachable
    targets just because the arm's CURRENT qpos sits on the wrong kinematic
    branch (same trick base.robotwin.is_reachable uses). Use AFTER warmup +
    the cheap _arm_reach_ok gate, BEFORE the expensive from-current-qpos plan,
    so IK-INFEASIBLE grasp candidates are PRUNED instead of planned + executed
    into a missed/empty grasp. Saves/restores qpos. Returns (ok, gap_m)."""
    art = impl.scene.get_all_articulations()[0]
    saved = np.array(art.get_qpos(), copy=True)
    try:
        qmin, qmax = art.get_qlimit()[:, 0], art.get_qlimit()[:, 1]
        qmin = np.where(np.isfinite(qmin), qmin, saved)
        qmax = np.where(np.isfinite(qmax), qmax, saved)
        art.set_qpos(0.5 * (qmin + qmax))            # neutral home pose
        res = plan_and_predict_ee(impl, list(flange_pose7), arm=arm, tol_m=tol_m)
    finally:
        art.set_qpos(saved)
    return bool(res.get("ok")), res.get("gap_m")


def flange_from_tcp(impl, tcp_world: np.ndarray, R_ee: np.ndarray,
                    arm: str = "left") -> np.ndarray:
    """Given desired TCP world pose, compute the EE flange world position
    cuRobo should plan to: flange = TCP - R_ee @ TCP_local.
    R_ee must be the aloha fl_link6 rotation (R_aloha from graspgen_infer),
    NOT the raw GraspGen rotation_matrix_world."""
    return np.asarray(tcp_world) - R_ee @ ALOHA_TCP_IN_EE_LOCAL


def tcp_from_flange(impl, flange_world: np.ndarray, R_ee: np.ndarray,
                    arm: str = "left") -> np.ndarray:
    return np.asarray(flange_world) + R_ee @ ALOHA_TCP_IN_EE_LOCAL


def fk_ee_from_qpos(impl, qpos: np.ndarray, arm: str = "left") -> tuple[np.ndarray, np.ndarray]:
    """Forward-kinematic the EE link given a target full-robot qpos.
    Sapien set_qpos teleports the kinematic state without stepping physics,
    so we save/restore the current qpos. Returns (xyz, quat_wxyz) of fl_link6
    (or fr_link6) at the supplied qpos.

    NOTE: this MUTATES articulation state momentarily (within one call) and
    restores it. Sapien's get_qpos may return a numpy VIEW into internal
    state — we deep-copy to ensure the restore uses the pre-mutation values.
    Don't call from inside a physics step / multi-thread."""
    art = impl.scene.get_all_articulations()[0]
    by_name = {l.get_name(): l for l in art.get_links()}
    ee_link = by_name["fl_link6" if arm == "left" else "fr_link6"]
    saved = np.array(art.get_qpos(), copy=True)  # explicit deep copy
    try:
        art.set_qpos(np.asarray(qpos))
        p = ee_link.get_pose()
        return np.array(p.p), np.array(p.q)
    finally:
        art.set_qpos(saved)


def plan_and_predict_ee(impl, target_pose7: list, arm: str = "left",
                        tol_m: float = 0.02) -> dict:
    """Plan a path with cuRobo, then FK the plan terminus to verify the
    EE will actually reach the target. target_pose7 = [x,y,z, qw,qx,qy,qz]
    (wxyz — RoboTwin/sapien convention)."""
    plan_fn = impl.robot.left_plan_path if arm == "left" else impl.robot.right_plan_path
    try:
        plan = plan_fn(list(target_pose7))
    except Exception as e:  # noqa: BLE001 — cuRobo can throw various
        return {"ok": False, "status": f"exception: {type(e).__name__}",
                "plan": None, "ee_predicted": None, "gap_m": None,
                "reason": f"plan_path threw: {e}"}
    if plan.get("status") != "Success":
        return {"ok": False, "status": plan.get("status"), "plan": plan,
                "ee_predicted": None, "gap_m": None,
                "reason": f"cuRobo plan status = {plan.get('status')}"}

    qpos_terminal = np.asarray(plan["position"][-1])
    # plan["position"] is for the active arm joints only; pad to full qpos
    art = impl.scene.get_all_articulations()[0]
    full_qpos = np.array(art.get_qpos())
    # determine which qpos indices belong to this arm by joint names
    active_joint_names = (impl.robot.left_planner.active_joints_name if arm == "left"
                          else impl.robot.right_planner.active_joints_name)
    all_joint_names = [j.get_name() for j in art.get_active_joints()]
    name_to_idx = {n: i for i, n in enumerate(all_joint_names)}
    arm_indices = [name_to_idx[n] for n in active_joint_names if n in name_to_idx]
    if len(arm_indices) != len(qpos_terminal):
        return {"ok": False, "status": "Success", "plan": plan,
                "ee_predicted": None, "gap_m": None,
                "reason": (f"qpos length mismatch: plan has {len(qpos_terminal)} "
                           f"but {len(arm_indices)} arm joints found")}
    full_qpos[arm_indices] = qpos_terminal
    ee_xyz, _ = fk_ee_from_qpos(impl, full_qpos, arm)
    target_xyz = np.array(target_pose7[:3])
    gap = float(np.linalg.norm(ee_xyz - target_xyz))
    ok = gap < tol_m
    return {"ok": ok, "status": "Success", "plan": plan,
            "ee_predicted": ee_xyz.tolist(), "gap_m": round(gap, 4),
            "reason": ("predicted EE within tol" if ok else
                       f"cuRobo lied: predicted EE {ee_xyz.round(3).tolist()} "
                       f"vs target {target_xyz.round(3).tolist()} = {gap*100:.1f}cm gap")}
