"""grasp_top_down — grasp via a straight-DOWN (top-down) approach.

Thin strategy wrapper over the grasp_object engine: it selects the steepest
DOWNWARD GraspGen candidates for the cuRobo IK precheck. Best for flat / short
objects a vertical grasp suits. If it fails (e.g. a tall cylinder whose wrist
can't reach a straight-down pose), try grasp_diverse instead.
"""
from typing import Any


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.skills.base.grasp_object.robotwin.policy import (
        dispatch_runtime as _grasp_engine,
    )
    return _grasp_engine(state, {**args, "strategy": "top_down"})


def run(env=None, **_: Any):
    raise RuntimeError(
        "grasp_top_down runs inside the rollout tool loop; call via VLM tool dispatch.")
