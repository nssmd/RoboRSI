"""Collision-free joint-space trajectory optimization (adapted from CaP-X's
``pyroki_snippets/_trajopt.py``).

Plans a smooth, collision-free joint path between two ``panda_hand`` poses. The
IK solver alone returns a reachable *goal* config but says nothing about the
*path* to it — so the JOINT_POSITION servo, driving straight-line in joint space
(``dq = q_goal - q_cur``), can sweep the arm through the table or into itself and
HARD-FREEZE at a high pose (measured: wedged at z≈1.37, every command a no-op).

Trajopt fixes this: it optimizes a whole trajectory that keeps every swept-arm
segment clear of the table (a ``HalfSpace``) and of itself (self-collision), so
the driven waypoints escape / avoid jams instead of plowing into them.

The start/end IK and the trajectory optimization are two separate least-squares
problems; ``solve_iks_with_collision`` is ``jdc.jit``-compiled for reuse. The
whole solve is a few tens of ms warm (vs ~1 ms for plain IK), so the client only
trajopts LONG moves — short hops stay on the fast IK path.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence

import jax
import jax.numpy as jnp
import jax_dataclasses as jdc
import jaxlie
import jaxls
import numpy as onp
import pyroki as pk


def solve_trajopt(
    robot: pk.Robot,
    robot_coll: pk.collision.RobotCollision,
    world_coll: Sequence[pk.collision.CollGeom],
    target_link_name: str,
    start_position: onp.ndarray,
    start_wxyz: onp.ndarray,
    end_position: onp.ndarray,
    end_wxyz: onp.ndarray,
    timesteps: int,
    dt: float = 0.1,
    *,
    start_cfg: onp.ndarray | None = None,
) -> onp.ndarray:
    """A ``(timesteps, num_actuated_joints)`` collision-free trajectory whose
    first waypoint hits ``(start_position, start_wxyz)`` and last hits the end
    pose. Poses are ``panda_hand`` targets IN THE PYROKI BASE FRAME (the client
    applies the world↔base transform), matching ``solve_ik``.

    The optimization graph depends only on ``timesteps`` / ``dt``, so it is JIT-
    compiled once per timestep count (via ``_jitted_optimizer``): the first call
    for a given ``timesteps`` compiles (~seconds), the rest are ~50 ms warm.
    """
    target_link_index = robot.links.names.index(target_link_name)
    if start_cfg is None:
        start_cfg, end_cfg = solve_iks_with_collision(
            robot,
            robot_coll,
            world_coll,
            jnp.array(target_link_index),
            jnp.array(start_position),
            jnp.array(start_wxyz),
            jnp.array(end_position),
            jnp.array(end_wxyz),
        )
    else:
        start_cfg = jnp.asarray(start_cfg)
        if start_cfg.shape != (robot.joints.num_actuated_joints,):
            raise ValueError("start_cfg has the wrong actuated-joint shape")
        end_cfg = solve_end_with_collision(
            robot,
            robot_coll,
            world_coll,
            jnp.array(target_link_index),
            start_cfg,
            jnp.array(end_position),
            jnp.array(end_wxyz),
        )
    optimize = _jitted_optimizer(int(timesteps), float(dt))
    traj = optimize(robot, robot_coll, world_coll, start_cfg, end_cfg)
    trajectory = onp.array(traj)
    trajectory[0] = onp.asarray(start_cfg)
    return trajectory


def solve_joint_trajopt(
    robot: pk.Robot,
    robot_coll: pk.collision.RobotCollision,
    world_coll: Sequence[pk.collision.CollGeom],
    start_cfg: onp.ndarray,
    end_cfg: onp.ndarray,
    timesteps: int,
    dt: float = 0.1,
) -> onp.ndarray:
    """Collision-aware trajectory between two exact live joint configurations."""
    expected = (robot.joints.num_actuated_joints,)
    start = onp.asarray(start_cfg, dtype=onp.float64)
    end = onp.asarray(end_cfg, dtype=onp.float64)
    if start.shape != expected or end.shape != expected:
        raise ValueError("joint trajectory endpoints have the wrong shape")
    if not onp.all(onp.isfinite(start)) or not onp.all(onp.isfinite(end)):
        raise ValueError("joint trajectory endpoints must be finite")
    optimize = _jitted_optimizer(int(timesteps), float(dt))
    trajectory = onp.asarray(
        optimize(robot, robot_coll, world_coll, jnp.asarray(start), jnp.asarray(end))
    ).copy()
    trajectory[0] = start
    trajectory[-1] = end
    return trajectory


@functools.lru_cache(maxsize=8)
def _jitted_optimizer(timesteps: int, dt: float):
    """A ``jax.jit`` closure over the (static) ``timesteps`` / ``dt`` so the
    ``jaxls`` graph is analyzed + compiled once per timestep count and cached.
    Without this the graph is re-analyzed every call (~7 s) instead of ~50 ms."""
    return jax.jit(
        lambda robot, robot_coll, world_coll, start_cfg, end_cfg: _optimize_traj(
            robot, robot_coll, world_coll, start_cfg, end_cfg, timesteps, dt))


def _optimize_traj(robot, robot_coll, world_coll, start_cfg, end_cfg,
                   timesteps: int, dt: float):
    """Optimize the swept-collision-free trajectory, initialized by a straight
    line in joint space between the collision-aware start/end configs."""
    init_traj = jnp.linspace(start_cfg, end_cfg, timesteps)
    traj_vars = robot.joint_var_cls(jnp.arange(timesteps))

    robot_b = jax.tree.map(lambda x: x[None], robot)          # batch dimension
    robot_coll_b = jax.tree.map(lambda x: x[None], robot_coll)

    factors = _base_factors(robot_b, traj_vars, timesteps)
    factors.append(
        pk.costs.self_collision_cost(
            robot_b,
            robot_coll_b,
            traj_vars,
            0.02,
            5.0,
        )
    )
    factors.extend(_world_coll_factors(robot_b, robot_coll_b, world_coll,
                                       robot.joint_var_cls, timesteps))
    factors.extend(_endpoint_factors(robot.joint_var_cls, start_cfg, end_cfg,
                                     timesteps))
    factors.extend(_smoothness_factors(robot_b, robot.joint_var_cls, timesteps, dt))

    solution = (
        jaxls.LeastSquaresProblem(factors, [traj_vars])
        .analyze()
        .solve(initial_vals=jaxls.VarValues.make((traj_vars.with_value(init_traj),)))
    )
    return solution[traj_vars]


def _base_factors(robot_b, traj_vars, timesteps):
    """Rest-pose regularization + joint-limit costs over the whole trajectory."""
    return [
        pk.costs.rest_cost(
            traj_vars,
            traj_vars.default_factory()[None],
            jnp.array([0.01])[None],
        ),
        pk.costs.limit_cost(robot_b, traj_vars, jnp.array([100.0])[None]),
    ]


def _swept_world_residual(vals, robot_b, robot_coll_b, world_coll_obj,
                          prev_vars, curr_vars):
    """Penetration residual for the arm SWEPT between consecutive waypoints vs a
    single world obstacle — penalizes any segment that plows through it."""
    coll = robot_coll_b.get_swept_capsules(robot_b, vals[prev_vars], vals[curr_vars])
    dist = pk.collision.collide(coll.reshape((-1, 1)), world_coll_obj.reshape((1, -1)))
    return (pk.collision.colldist_from_sdf(dist, 0.1) * 20.0).flatten()


def _world_coll_factors(robot_b, robot_coll_b, world_coll, joint_var_cls, timesteps):
    """One swept-collision factor per world obstacle."""
    prev_vars = joint_var_cls(jnp.arange(0, timesteps - 1))
    curr_vars = joint_var_cls(jnp.arange(1, timesteps))
    factors = []
    for obj in world_coll:
        factors.append(jaxls.Cost(
            _swept_world_residual,
            (robot_b, robot_coll_b, jax.tree.map(lambda x: x[None], obj),
             prev_vars, curr_vars),
            name="World Collision (sweep)",
        ))
    return factors


def _endpoint_factors(joint_var_cls, start_cfg, end_cfg, timesteps):
    """Pin the first two waypoints to the start config and last two to the end."""
    return [
        jaxls.Cost(
            lambda vals, var: ((vals[var] - start_cfg) * 100.0).flatten(),
            (joint_var_cls(jnp.arange(0, 2)),),
            name="start_pose_constraint",
        ),
        jaxls.Cost(
            lambda vals, var: ((vals[var] - end_cfg) * 100.0).flatten(),
            (joint_var_cls(jnp.arange(timesteps - 2, timesteps)),),
            name="end_pose_constraint",
        ),
    ]


def _smoothness_factors(robot_b, joint_var_cls, timesteps, dt):
    """Velocity / acceleration / jerk minimization for a driveable, smooth path."""
    return [
        pk.costs.smoothness_cost(
            joint_var_cls(jnp.arange(1, timesteps)),
            joint_var_cls(jnp.arange(0, timesteps - 1)),
            jnp.array([0.1])[None],
        ),
        pk.costs.five_point_velocity_cost(
            robot_b,
            joint_var_cls(jnp.arange(4, timesteps)),
            joint_var_cls(jnp.arange(3, timesteps - 1)),
            joint_var_cls(jnp.arange(1, timesteps - 3)),
            joint_var_cls(jnp.arange(0, timesteps - 4)),
            dt,
            jnp.array([10.0])[None],
        ),
        pk.costs.five_point_acceleration_cost(
            joint_var_cls(jnp.arange(2, timesteps - 2)),
            joint_var_cls(jnp.arange(4, timesteps)),
            joint_var_cls(jnp.arange(3, timesteps - 1)),
            joint_var_cls(jnp.arange(1, timesteps - 3)),
            joint_var_cls(jnp.arange(0, timesteps - 4)),
            dt,
            jnp.array([0.1])[None],
        ),
        pk.costs.five_point_jerk_cost(
            joint_var_cls(jnp.arange(6, timesteps)),
            joint_var_cls(jnp.arange(5, timesteps - 1)),
            joint_var_cls(jnp.arange(4, timesteps - 2)),
            joint_var_cls(jnp.arange(2, timesteps - 4)),
            joint_var_cls(jnp.arange(1, timesteps - 5)),
            joint_var_cls(jnp.arange(0, timesteps - 6)),
            dt,
            jnp.array([0.1])[None],
        ),
    ]


@jdc.jit
def solve_iks_with_collision(
    robot: pk.Robot,
    coll: pk.collision.RobotCollision,
    world_coll_list: Sequence[pk.collision.CollGeom],
    target_link_index: jax.Array,
    target_position_0: jax.Array,
    target_wxyz_0: jax.Array,
    target_position_1: jax.Array,
    target_wxyz_1: jax.Array,
) -> tuple[jax.Array, jax.Array]:
    """Collision-aware IK for the start and end poses jointly. Returns the two
    joint configs; both respect self-collision + every world obstacle, and are
    nudged toward each other so the connecting trajectory is short."""
    joint_var_0 = robot.joint_var_cls(0)
    joint_var_1 = robot.joint_var_cls(1)
    joint_vars = robot.joint_var_cls(jnp.arange(2))
    robot_b = jax.tree.map(lambda x: x[None], robot)
    coll_b = jax.tree.map(lambda x: x[None], coll)

    factors = [
        _endpoint_pose_cost(robot, joint_var_0, target_wxyz_0, target_position_0,
                            target_link_index),
        _endpoint_pose_cost(robot, joint_var_1, target_wxyz_1, target_position_1,
                            target_link_index),
        pk.costs.limit_cost(robot_b, joint_vars, jnp.array(100.0)),
        pk.costs.rest_cost(joint_vars, joint_vars.default_factory()[None],
                           jnp.array(0.001)),
        pk.costs.self_collision_cost(robot_b, coll_b, joint_vars, 0.02, 5.0),
    ]
    factors.extend(
        pk.costs.world_collision_cost(
            robot_b, coll_b, joint_vars,
            jax.tree.map(lambda x: x[None], world_coll), 0.05, 10.0,
        )
        for world_coll in world_coll_list
    )
    factors.append(_joint_similarity_cost(joint_var_0, joint_var_1))

    sol = jaxls.LeastSquaresProblem(factors, [joint_vars]).analyze().solve(verbose=False)
    return sol[joint_var_0], sol[joint_var_1]


@jdc.jit
def solve_end_with_collision(
    robot: pk.Robot,
    coll: pk.collision.RobotCollision,
    world_coll_list: Sequence[pk.collision.CollGeom],
    target_link_index: jax.Array,
    start_cfg: jax.Array,
    target_position: jax.Array,
    target_wxyz: jax.Array,
) -> jax.Array:
    """Solve only the endpoint, initialized and regularized from live joints."""
    joint_var = robot.joint_var_cls(0)
    joint_vars = robot.joint_var_cls(jnp.arange(1))
    robot_b = jax.tree.map(lambda x: x[None], robot)
    coll_b = jax.tree.map(lambda x: x[None], coll)
    factors = [
        _endpoint_pose_cost(
            robot,
            joint_var,
            target_wxyz,
            target_position,
            target_link_index,
        ),
        pk.costs.limit_cost(robot_b, joint_vars, jnp.array(100.0)),
        pk.costs.rest_cost(joint_var, start_cfg, jnp.array(0.05)),
        pk.costs.self_collision_cost(
            robot_b,
            coll_b,
            joint_vars,
            0.02,
            5.0,
        ),
    ]
    factors.extend(
        pk.costs.world_collision_cost(
            robot_b,
            coll_b,
            joint_vars,
            jax.tree.map(lambda x: x[None], world_coll),
            0.05,
            10.0,
        )
        for world_coll in world_coll_list
    )
    initial_values = jaxls.VarValues.make(
        (joint_var.with_value(start_cfg),)
    )
    solution = (
        jaxls.LeastSquaresProblem(factors, [joint_var])
        .analyze()
        .solve(initial_vals=initial_values, verbose=False)
    )
    return solution[joint_var]


def _endpoint_pose_cost(robot, joint_var, target_wxyz, target_position, link_index):
    """Pose cost pinning ``panda_hand`` to a target SE3 pose for one endpoint."""
    return pk.costs.pose_cost(
        robot,
        joint_var,
        jaxlie.SE3.from_rotation_and_translation(
            jaxlie.SO3(target_wxyz), target_position),
        link_index,
        jnp.array([5.0] * 3),
        jnp.array([1.0] * 3),
    )


@jaxls.Cost.create_factory(name="JointSimilarityCost")
def _joint_similarity_cost(vals, var_0, var_1):
    """Small cost encouraging the start and end configs to stay close."""
    return ((vals[var_0] - vals[var_1]) * 0.01).flatten()
