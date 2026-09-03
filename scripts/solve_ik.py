"""Seeded PyRoKi inverse kinematics for branch-continuous Panda motion."""

from __future__ import annotations

import jax
import jax.numpy as jnp
import jax_dataclasses as jdc
import jaxlie
import jaxls
import numpy as onp
import pyroki as pk


def solve_ik(
    robot: pk.Robot,
    target_link_name: str,
    target_wxyz: onp.ndarray,
    target_position: onp.ndarray,
    *,
    initial_cfg: onp.ndarray | None = None,
) -> onp.ndarray:
    """Solve IK while staying on the live redundant-joint branch."""
    assert target_position.shape == (3,) and target_wxyz.shape == (4,)
    if initial_cfg is None:
        initial_cfg = onp.asarray(
            robot.joint_var_cls(0).default_factory(),
            dtype=onp.float64,
        )
    else:
        initial_cfg = onp.asarray(initial_cfg, dtype=onp.float64)
    assert initial_cfg.shape == (robot.joints.num_actuated_joints,)
    target_link_index = robot.links.names.index(target_link_name)
    cfg = _solve_ik_jax(
        robot,
        jnp.array(target_link_index),
        jnp.array(target_wxyz),
        jnp.array(target_position),
        jnp.array(initial_cfg),
    )
    assert cfg.shape == (robot.joints.num_actuated_joints,)
    return onp.array(cfg)


@jdc.jit
def _solve_ik_jax(
    robot: pk.Robot,
    target_link_index: jax.Array,
    target_wxyz: jax.Array,
    target_position: jax.Array,
    initial_cfg: jax.Array,
) -> jax.Array:
    joint_var = robot.joint_var_cls(0)
    variables = [joint_var]
    costs = [
        pk.costs.pose_cost_analytic_jac(
            robot,
            joint_var,
            jaxlie.SE3.from_rotation_and_translation(
                jaxlie.SO3(target_wxyz), target_position
            ),
            target_link_index,
            pos_weight=50.0,
            ori_weight=10.0,
        ),
        pk.costs.limit_constraint(robot, joint_var),
        pk.costs.rest_cost(joint_var, initial_cfg, jnp.array(0.05)),
    ]
    initial_values = jaxls.VarValues.make(
        (joint_var.with_value(initial_cfg),)
    )
    solution = (
        jaxls.LeastSquaresProblem(costs=costs, variables=variables)
        .analyze()
        .solve(
            initial_vals=initial_values,
            verbose=False,
            linear_solver="dense_cholesky",
            trust_region=jaxls.TrustRegionConfig(lambda_initial=1.0),
        )
    )
    return solution[joint_var]
