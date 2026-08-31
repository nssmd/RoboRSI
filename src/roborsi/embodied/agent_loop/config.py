"""LIBERO-only runtime prompt and model constants."""

from __future__ import annotations

import os

DEFAULT_MODEL = os.environ.get("ROBORSI_VLM_MODEL") or "responses/gpt-5.6-sol"


def _skill_namespace(backend_name: str | None) -> str:
    if backend_name not in {"libero", "libero-pro"}:
        raise ValueError(f"unsupported public backend: {backend_name}")
    return "libero"


_SHORTLIST_ALWAYS = {
    "look",
    "find_pixel",
    "find_by_pointing",
    "unproject_pixel",
    "get_arm_pose",
    "is_holding",
    "is_reachable",
    "grasp_object",
    "place_object_in",
    "place_on_surface",
    "place_beside",
    "move_to_pose",
    "move_ee_delta",
    "gripper",
}


_RULES_LIBERO = """\
ESSENTIAL RULES (single-arm LIBERO / Franka Panda, JOINT_POSITION):

PURE VISION: object and target locations come only from current camera RGB and
depth. Use look, find_pixel or find_by_pointing, then unproject_pixel before
acting. Re-localize after scene motion. Never invent or reuse coordinates from
another episode. get_arm_pose reports only robot proprioception.

LOCALIZATION: preserve the exact task wording for fine-grained identity and
spatial relations. If a detector returns alternatives, inspect the current
image. Detector rank alone is not identity evidence.

PICK: use grasp_object and require grasped=true before placement. A manual
gripper close is not proof of a grasp.

DIRECT MANIPULATION: keep the task verb. Use pull_drawer to open a drawer,
close_drawer to close one, open_hinged_door for a hinged fixture, and
push_object for push or slide tasks. Do not convert these tasks to generic
pick-and-place.

PLACEMENT: use place_on_surface for exposed supports, place_object_in for a
container or cavity, place_beside for a relation, and
place_held_at_target_servo only for a pose produced by current perception.

VISUAL HOLD: physical hold and fine-grained identity are separate. Respect
holding, visual_verified, identity_verified, and do_not_regrasp. Never open or
re-grasp while a possible object is held merely to gather more evidence.

MOTION: move_to_pose and move_ee_delta plan joint-position motion internally.
Use one bounded target command instead of hand-stepping an unchanged request.

DONE: call done(success=True) only after the visible instruction is satisfied.
The host evaluates the simulator predicate after the episode. That predicate is
not a tool and is never visible during planning or execution.
"""

_RULES = _RULES_LIBERO
SYSTEM_PROMPT_LEGACY = "Use _system_prompt() to build the live LIBERO prompt."

_EMBODIMENT = (
    "You are an embodied robot agent driving a single 7-DOF Franka Panda arm "
    "in a LIBERO tabletop scene. Use current RGB/RGB-D and robot "
    "proprioception only."
)


def _embodiment_line(ns: str) -> str:
    if ns != "libero":
        raise ValueError(f"unsupported public skill namespace: {ns}")
    return _EMBODIMENT


def _rules_for(ns: str) -> str:
    if ns != "libero":
        raise ValueError(f"unsupported public skill namespace: {ns}")
    return _RULES_LIBERO


_POINT_SYSTEM_PROMPT = (
    "You locate a manipulation target in a fixed head-camera image. The image "
    "size is IMG_W x IMG_H with origin at the top-left. Return one JSON object: "
    '{"u": <int>, "v": <int>, "confidence": <0-1>, "basis": "<short>"}. '
    'If absent, return {"found": false, "reason": "<short>"}.'
)
