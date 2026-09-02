"""home — retract the end-effector straight UP to clear the head-camera view,
PRESERVING the grip (a held object must survive a 'clear the view' move).

Two former bugs this fixes: (1) it opened the gripper, so homing after a grasp
DROPPED the held object mid pick-and-place; (2) ``clearance_z`` was an ABSOLUTE
z≈0.28 that sits BELOW the ~0.9 table (the object frame), so 'retract up' drove
the arm DOWN into the table. We now retract up RELATIVE to the current height
and hold whatever the gripper has."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills.base._lib.libero._control import LiberoControl


def dispatch_runtime(state, args: dict[str, Any]):
    lift = float(args.get("lift", 0.18))                 # retract UP this much (m)
    ctrl = LiberoControl(state.env)
    cur, _, _ = ctrl.read_pose()
    ctrl.servo_to([cur[0], cur[1], float(cur[2]) + lift], gripper="keep", max_iters=60)
    ee, _, _ = ctrl.read_pose()
    return ({"ok": True, "ee_pos": [round(float(v), 4) for v in ee]},
            state.env.take_snapshot())
