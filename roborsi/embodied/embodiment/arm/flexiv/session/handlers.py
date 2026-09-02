"""Action handlers — map protocol actions to RdkAdapter calls.

Each handler takes the sidecar ``Session`` and ``params`` dict, performs
the action, and returns a dict to be wrapped in a ``Response``. Handlers
are synchronous from the asyncio loop's perspective; long-running moves
loop on ``adapter.busy()`` with ``asyncio.sleep`` between polls.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from roborsi.embodied.embodiment.arm.flexiv.session.rdk_adapter import RdkAdapter

Handler = Callable[[RdkAdapter, dict[str, Any]], Awaitable[dict[str, Any]]]


_POLL_INTERVAL = 0.05  # s


async def _wait_until_idle(adapter: RdkAdapter, timeout: float) -> None:
    """Poll ``busy()`` until it clears or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout
    while adapter.busy():
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(f"Motion did not complete within {timeout}s")
        await asyncio.sleep(_POLL_INTERVAL)


async def _wait_pose_reached(
    adapter: RdkAdapter, target_xyz: list[float], timeout: float, tol: float = 0.003
) -> None:
    """Wait until TCP position (x,y,z) is within ``tol`` metres of ``target_xyz``.

    ``busy()`` can stay True forever in Cartesian servoing modes, so we
    watch pose convergence instead. Returns silently on match; raises
    TimeoutError otherwise.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    tol2 = tol * tol
    while True:
        st = adapter.read_state()
        pose = st["tcp_pose"]
        dx = pose[0] - target_xyz[0]
        dy = pose[1] - target_xyz[1]
        dz = pose[2] - target_xyz[2]
        if dx * dx + dy * dy + dz * dz < tol2:
            return
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(
                f"TCP did not reach {target_xyz} within {timeout}s "
                f"(last {pose[:3]}, err={(dx*dx+dy*dy+dz*dz)**0.5:.4f} m)"
            )
        await asyncio.sleep(_POLL_INTERVAL)


async def handle_state(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    return adapter.read_state()


async def _wait_joint_reached(
    adapter: RdkAdapter, target_q: list[float], timeout: float, tol: float = 0.01
) -> None:
    """Wait until joint positions are within ``tol`` rad of target.

    ``busy()`` stays True in NRT_JOINT_POSITION mode even after the target is
    reached (PID keeps servoing), so we check joint convergence directly.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    tgt = list(target_q)
    while True:
        st = adapter.read_state()
        q = st["q"]
        err = max(abs(a - b) for a, b in zip(q, tgt))
        if err < tol:
            return
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError(
                f"Joints did not converge within {timeout}s (max err={err:.4f} rad)"
            )
        await asyncio.sleep(_POLL_INTERVAL)


async def handle_move_joint(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    q = params.get("q")
    if not isinstance(q, list) or not q:
        raise ValueError("move_joint requires non-empty 'q' list")
    vel = float(params.get("vel", 0.5))
    acc = float(params.get("acc", 1.0))
    timeout = float(params.get("timeout", 30.0))
    adapter.move_joint(q, max_vel=vel, max_acc=acc)
    await _wait_joint_reached(adapter, q, timeout)
    return {"q": q, "mode": adapter.current_mode()}


async def handle_move_tcp(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    pose = params.get("pose")
    if not isinstance(pose, list) or len(pose) != 7:
        raise ValueError(
            "move_tcp requires 'pose' as [x,y,z,qx,qy,qz,qw] (standard order)"
        )
    vel = float(params.get("vel", 0.1))
    timeout = float(params.get("timeout", 30.0))
    adapter.move_tcp(pose, max_linear_vel=vel)
    await _wait_pose_reached(adapter, pose[:3], timeout)
    return {"pose": pose, "mode": adapter.current_mode()}


async def handle_move_delta(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    """Relative TCP move: current pose + (dx, dy, dz), orientation preserved."""
    dx = float(params.get("dx", 0.0))
    dy = float(params.get("dy", 0.0))
    dz = float(params.get("dz", 0.0))
    vel = float(params.get("vel", 0.05))
    timeout = float(params.get("timeout", 20.0))

    st = adapter.read_state()
    cx, cy, cz, qx, qy, qz, qw = st["tcp_pose"]
    target = [cx + dx, cy + dy, cz + dz, qx, qy, qz, qw]
    adapter.move_tcp(target, max_linear_vel=vel)
    await _wait_pose_reached(adapter, target[:3], timeout)
    return {"from": st["tcp_pose"], "to": target, "mode": adapter.current_mode()}


async def handle_contact_descend(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    """Descend straight down until |Fz| exceeds ``force_thresh`` or ``dz_max`` reached.

    Uses ext_wrench[2] (world Z) as a contact sensor — ideal when the table
    height is unknown. Stops the motion at contact and returns the z reached.
    """
    dz_max = float(params.get("dz_max", 0.20))
    force_thresh = float(params.get("force_thresh", 5.0))
    vel = float(params.get("vel", 0.02))
    timeout = float(params.get("timeout", 30.0))

    st = adapter.read_state()
    start_pose = list(st["tcp_pose"])
    start_fz = float(st["ext_wrench"][2])
    cx, cy, cz, qx, qy, qz, qw = start_pose
    target = [cx, cy, cz - dz_max, qx, qy, qz, qw]
    adapter.move_tcp(target, max_linear_vel=vel)

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        st = adapter.read_state()
        pose = st["tcp_pose"]
        fz = float(st["ext_wrench"][2])
        # Contact if |fz - start_fz| exceeds threshold (ignore baseline gravity/offset).
        if abs(fz - start_fz) >= force_thresh:
            adapter.stop()
            return {
                "contact": True,
                "z_at_contact": pose[2],
                "fz": fz,
                "dz_travelled": start_pose[2] - pose[2],
            }
        if pose[2] <= target[2] + 0.001:
            return {"contact": False, "z_at_bottom": pose[2], "fz": fz, "dz_travelled": start_pose[2] - pose[2]}
        if asyncio.get_event_loop().time() > deadline:
            adapter.stop()
            raise TimeoutError(f"contact_descend timed out after {timeout}s at z={pose[2]:.4f}")
        await asyncio.sleep(0.03)


async def handle_grasp(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    """Macro: open → descend dz_down → close to grip_width → lift dz_up.

    All motion is relative to the current pose. Intended for Rollout-style
    policies that first hover above the target, then call ``grasp`` for the
    final pinch. Each sub-step has its own timeout + pose-convergence check.
    """
    dz_down = float(params.get("dz_down", 0.08))
    dz_up = float(params.get("dz_up", 0.10))
    grip_width = float(params.get("grip_width", 0.0))
    grip_force = float(params.get("grip_force", 100.0))
    open_width = float(params.get("open_width", 0.085))
    vel = float(params.get("vel", 0.05))
    timeout = float(params.get("timeout", 25.0))

    # Open first (non-blocking wrt motion planner).
    adapter.gripper_move(open_width, velocity=0.1, force=grip_force)
    start = adapter.read_state()["tcp_pose"]

    # Descend.
    down_target = [start[0], start[1], start[2] - dz_down, start[3], start[4], start[5], start[6]]
    adapter.move_tcp(down_target, max_linear_vel=vel)
    await _wait_pose_reached(adapter, down_target[:3], timeout)

    # Close.
    adapter.gripper_move(grip_width, velocity=0.1, force=grip_force)
    await asyncio.sleep(0.8)  # let gripper settle before lifting

    # Lift.
    up_target = [start[0], start[1], start[2] - dz_down + dz_up, start[3], start[4], start[5], start[6]]
    adapter.move_tcp(up_target, max_linear_vel=vel)
    await _wait_pose_reached(adapter, up_target[:3], timeout)

    return {
        "start_pose": start,
        "down_pose": down_target,
        "up_pose": up_target,
        "gripper": adapter.gripper_state(),
    }


async def handle_move_home(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    home = params.get("q")
    if not isinstance(home, list) or not home:
        raise ValueError("move_home requires 'q' from the caller (sidecar has no preset)")
    vel = float(params.get("vel", 0.3))
    acc = float(params.get("acc", 0.5))
    timeout = float(params.get("timeout", 30.0))
    adapter.move_joint(home, max_vel=vel, max_acc=acc)
    await _wait_joint_reached(adapter, home, timeout)
    return {"q": home, "mode": adapter.current_mode()}


async def handle_stop(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    adapter.stop()
    return {"mode": adapter.current_mode()}


async def handle_gripper_open(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    width = float(params.get("width", 0.085))
    adapter.gripper_move(width)
    return adapter.gripper_state()


async def handle_gripper_close(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    adapter.gripper_move(0.0)
    return adapter.gripper_state()


async def handle_gripper_width(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    width = params.get("value")
    if width is None:
        raise ValueError("gripper_width requires 'value'")
    adapter.gripper_move(float(width))
    return adapter.gripper_state()


# TODO(hardware): Flexiv Primitives manual lists the full catalogue; for
# the MVP we expose the adapter directly and let the caller pass a name.
_PRIMITIVE_CATALOGUE = [
    "Home",
    "MoveL",
    "MoveJ",
    "MovePTP",
    "Grasp",
    "Release",
    "Contact",
]


async def handle_primitive_list(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    return {"primitives": list(_PRIMITIVE_CATALOGUE)}


async def handle_primitive_run(adapter: RdkAdapter, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("primitive_run requires 'name'")
    prim_params = params.get("params", {}) or {}
    timeout = float(params.get("timeout", 60.0))
    adapter.run_primitive(name, prim_params)
    await _wait_until_idle(adapter, timeout)
    return {"name": name, "state": adapter.primitive_state()}


HANDLERS: dict[str, Handler] = {
    "state": handle_state,
    "move_joint": handle_move_joint,
    "move_tcp": handle_move_tcp,
    "move_delta": handle_move_delta,
    "contact_descend": handle_contact_descend,
    "move_home": handle_move_home,
    "grasp": handle_grasp,
    "stop": handle_stop,
    "gripper_open": handle_gripper_open,
    "gripper_close": handle_gripper_close,
    "gripper_width": handle_gripper_width,
    "primitive_list": handle_primitive_list,
    "primitive_run": handle_primitive_run,
}
