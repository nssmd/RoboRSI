"""grasp_diverse — grasp by trying an approach-angle-DIVERSE set of candidates.

Thin strategy wrapper over the grasp_object engine: instead of the steepest
downward candidates, it IK-prechecks a spread of approach angles (vertical →
angled → side). This surfaces the moderate / side grasps that a tall cylinder
(can, bottle, roller) actually needs and that the arm can reach, rather than
burning the whole precheck budget on unreachable straight-down poses.
"""
from typing import Any


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.skills.base.grasp_object.robotwin.policy import (
        dispatch_runtime as _grasp_engine,
    )
    # Cylinders on a table: GraspGen (object-centric, no gravity) ranks its FROM-BELOW
    # grasps highest for a standing can/bottle, so a small top-K pool can be all-from-
    # below and the engine's from_below filter leaves 0. Fetch a bigger pool (top_k=100)
    # so plenty of downward/side grasps survive the filter. Caller can override top_k.
    #
    # complete_symmetric=True: the head camera sees only the FRONT SHELL of the cylinder,
    # so GraspGen gets a half-cylinder cloud and predicts poses whose fingers close off
    # the true body — measured 0/8 capture on the standing can. Mirroring the cloud across
    # its axis into a FULL cylinder (no arm motion) fixes this: measured 4/8 vs 0/8 with
    # the same execution. Valid here because grasp_diverse targets round objects
    # (can/bottle/roller); the general grasp_object path leaves it off. Caller can override.
    return _grasp_engine(state, {"top_k": 100, "complete_symmetric": True,
                                 **args, "strategy": "diverse"})


def run(env=None, **_: Any):
    raise RuntimeError(
        "grasp_diverse runs inside the rollout tool loop; call via VLM tool dispatch.")
