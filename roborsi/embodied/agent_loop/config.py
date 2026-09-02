"""Static config + prompt-text constants for the VLM tool loop.

Pure data / strings — no sim, no LLM-provider, no skill imports. Shared by
the RoboTwin agent and reusable by a real-robot agent loop.
"""

from __future__ import annotations

import os


DEFAULT_MODEL = (
    os.environ.get("ROBORSI_VLM_MODEL")
    or "anthropic/claude-opus-4-8"
)


# ── Skill namespace per backend ──────────────────────────────────────────
# A backend's base skills live under skills/base/<namespace>/. Several backends
# share one embodiment's muscle (RoboTwin + its BiCoord fork + the HTTP shim all
# use the dual-arm "robotwin" skills; the two LIBERO backends share the single-
# arm "libero" muscle). Tool discovery / dispatch resolve the namespace from the
# active Env's backend_name via _skill_namespace; default "robotwin" keeps every
# existing caller byte-identical.
_BACKEND_SKILL_NS = {
    "robotwin": "robotwin",
    "robotwin-http": "robotwin",
    "bicoord": "robotwin",
    "libero": "libero",
    "libero-pro": "libero",
}


def _skill_namespace(backend_name: str | None) -> str:
    return _BACKEND_SKILL_NS.get(backend_name or "", "robotwin")


SYSTEM_PROMPT_LEGACY = """\
(Old hand-rolled prompt removed; use _system_prompt() which auto-discovers
base/robotwin/<tool>/SKILL.md.)
"""


# Perception / control utilities the Engineer ALWAYS needs to perceive, verify
# and finish — force-kept in any shortlist so the Sonnet selector can never
# accidentally blind the Engineer.
_SHORTLIST_ALWAYS = {
    "look", "view_frame", "find_pixel",
    "find_object_via_wrist", "unproject_pixel", "is_holding", "is_reachable",
    "verify_holding_visual", "move_to_pose", "move_fingertip_to", "gripper",
    # The simulator verdict is deliberately absent from the Engineer's tools.
    # It is recorded only at final adjudication in run_rollout.
    # Core grasp/place primitives the GRASP RECIPE + TRANSPORT rules point to.
    # PURE VISION, no ground truth (pick_actor_by_contact_point + its graspgen
    # wrapper are deleted). Complementary grasp primitives, all force-kept so the
    # selector can never blind the Engineer to the one a scene needs:
    #   grasp_object — Grounded-SAM + GraspGen 6-DoF from camera depth (general /
    #     irregular objects); grasp_obb — CaP-X OBB top-down for REGULAR objects
    #     (boxes / cubes / cylinders), validated 2/3 on cubes where GraspGen is
    #     0/10; grasp_flat — specialized low pinch for FLAT/THIN slabs flush on
    #     the table (phone / bread slice) the others close on air above
    #     (EXPERIMENTAL); grasp_rim — rim-frame radial pinch for THIN-WALLED OPEN
    #     containers (cup / bowl / bin) whose hollow body the others close on air
    #     inside (EXPERIMENTAL). place_obb — CaP-X OBB place-into-container
    #     (interior-center drop + depth containment verify).
    "grasp_object", "grasp_obb", "grasp_flat", "grasp_rim", "place_object_in", "place_obb",
    # Closed-loop precision place (descend_tcp_to_z's xy analogue) — needed for
    # tight-alignment tasks (match_blocks <=3cm); keep available so the engineer
    # can reach for it instead of hand-nudging with move_fingertip_to.
    "place_held_at_target_servo", "descend_tcp_to_z",
    # Vision localizers — the ONLY way to find an object (no GT poses).
    "localize_object_top_center", "segment_object_pointcloud",
}


_RULES = """\
ESSENTIAL RULES (kept short — per-task lessons live in the wiki, which
you MUST read at the start of each atomic):

PURE VISION — YOU HAVE NO GROUND TRUTH. You never receive object poses or names
from the simulator. To act on ANY object you MUST localize it from the CAMERA:
  look('head_camera') → find_pixel(object) → unproject_pixel(u,v) → world XYZ,
  OR localize_object_top_center(object) for a one-call top-center XYZ. Object
  positions are seed-randomized and change every episode — RE-LOCALIZE from
  vision on every attempt; never trust a coordinate from the wiki/plan/memory.

GRASP RECIPE (default for ANY pick) — use grasp_object(arm, object=...). It
grounds the object with Grounded-SAM, builds a point cloud from CAMERA DEPTH,
runs GraspGen for 6-DoF candidates, IK-filters, and executes. Do NOT hand-roll a
grasp from move_to_pose+gripper (closes on air, burns the budget — every manual
pick is 0/N). If grasp_object returns ok=False: re-localize, try the other arm,
or adjust; only after 2 honest failures fall back to
get_grasp_pose→move_fingertip_to descend→gripper close→lift. move_to_pose /
move_fingertip_to are for APPROACH / TRANSPORT / DROP — never the grasp itself.
Per-object notes (bowl = rim, block = top-down) are in the wiki. Read it.

COMMIT TO A CONFIRMED GRASP: once is_holding=True — INCLUDING from the manual
descend+close fallback — that IS a valid, secured grasp. Do NOT re-open the
gripper to "re-grasp properly". The "manual pick is 0/N" warning above is about
not ATTEMPTING a hand-rolled pick as your FIRST move, NOT about distrusting a
hold you already achieved. Keep the hold until an intentional place/release;
re-opening a confirmed hold drops the object and wastes the whole run.

TRANSPORT / PLACE: localize the TARGET by perception too
(find_pixel(target)+unproject, or localize_object_top_center), then use
place_object_in / place_held_at_target_servo (closed-loop visual servo). After a
grasp, do NOT reuse the holding grasp-quat in move_to_pose (infeasible
workspace-wide, IK-thrashes); use the top-down quat [0.5,-0.5,0.5,0.5]. If a
move_to_pose returns ok=False twice, SWITCH the quat — don't retry the same one.

MULTI-TOOL TURNS: you MAY emit MULTIPLE tool_use blocks in ONE turn —
prefer composing a 3-5 step plan per turn. Single-tool turns only for
"look + decide" loops.

DONE-CHECK (mandatory): before done(success=True):
  view_frame('head_camera') → visually confirm the criterion → if
  ambiguous, call verify_holding_visual / is_holding. NEVER declare done
  on stdout heuristics alone.

LISTEN REVIEWER: when Reviewer says "switch arm" / "use skill X" / "call
probe_ik_workspace first" in next_action, do EXACTLY that on the next
attempt. Don't repeat the failing strategy.

READ THE WIKI: at the start of EVERY atomic call
`read_task_wiki(task=<lh_task>)` once. The wiki carries per-task
proven traces, failure modes, and key measurements distilled from prior runs.

exec_python LIMITS: 60s wall-time cap. Do NOT call cuRobo-heavy skills
(grasp_object, move_to_pose for far poses) inside exec_python — each can spin
minutes and blow the cap. SAFE inside: look, get_arm_pose, gripper, is_reachable,
probe_ik_workspace, find_pixel, unproject_pixel, pure numpy/math. For multiple
cuRobo calls, do them as separate tool_use blocks (one per turn).

STRATEGY > LITERAL DATA: object positions are seed-randomized and are NOT given
to you — localize by perception (find_pixel+unproject / localize_object_top_center)
at the start of every attempt. Any stored coordinate (wiki, recipe, memory) is a
stale reference from a different seed; trust your live perception.

SELF-EVOLUTION: if no existing skill works (after 2 honest tries), don't
just done(False). Read closest skill via read_skill_code → propose_new_skill
or propose_skill_update with full code. Harness gates bad proposals.
"""


# ── LIBERO (single-arm robosuite/OSC) embodiment + rules ─────────────────
# LIBERO's world is fundamentally different from RoboTwin's: one 7-DOF Franka
# Panda, OSC end-effector delta control (no cuRobo planner, no GraspGen), and
# ground-truth object/EE poses handed to the agent via tools (no vision needed
# to LOCATE objects). So it gets its own embodiment line + rules that reference
# only the base/libero muscle.
_RULES_LIBERO = """\
ESSENTIAL RULES (single-arm LIBERO / Franka Panda, OSC end-effector control):

PERCEPTION FIRST: call look, then localize every named object from the current
camera frame with find_pixel(object) → unproject_pixel(u,v). Object positions
change across episodes; do not reuse coordinates from prior runs or task files.

PICK / PLACE (default): use the composite skills, not hand-rolled motion.
  1. grasp_object(object=<name>) — localizes from camera/depth, opens, hovers,
     descends, closes, and lifts. Verify grasped=true in the result.
  2. place_object_in(object=<target_name>) — localizes the target from vision,
     hovers, descends, opens, and retracts.
Lower-level primitives to compose your own motion when a composite doesn't fit:
  get_arm_pose → your OWN end-effector pose (proprioception, allowed);
  find_pixel(object) → (u,v), then unproject_pixel(u,v) → world XYZ;
  move_to_pose(pos=[x,y,z], quat=[x,y,z,w]?, gripper='open'|'close'|'keep') → OSC
     servo to a world pose; move_ee_delta(dpos=[dx,dy,dz]) → small open-loop nudge;
  gripper(state='open'|'close').
Position deltas are clamped to ~0.05 m/step, so servo skills iterate internally —
call them once with the TARGET pose, don't hand-step toward it.

MULTI-TOOL TURNS: you MAY emit MULTIPLE tool_use blocks in ONE turn — compose a
2–4 step plan (e.g. look → grasp_object → place_object_in). Single-tool
turns only when you need the result before deciding.

DONE: call done(success=True) once the instruction is satisfied. Success is
adjudicated by the simulator's own predicate AFTER the episode — never fabricate
it; if you're stuck after honest attempts, done(success=False).
"""


_EMBODIMENT_LINE = {
    "robotwin": "You are an embodied robot agent driving a dual-arm tabletop setup.",
    "libero": (
        "You are an embodied robot agent driving a single 7-DOF Franka Panda arm "
        "in a LIBERO tabletop scene. Motion is end-effector servo control; localize "
        "objects from the current camera observation and use proprioception only "
        "for the robot's own state."
    ),
}


def _embodiment_line(ns: str) -> str:
    return _EMBODIMENT_LINE.get(ns, _EMBODIMENT_LINE["robotwin"])


def _rules_for(ns: str) -> str:
    return _RULES_LIBERO if ns == "libero" else _RULES


# Inline copy of the perceive system prompt to avoid importing the full agent
# tool stack (which drags providers/oauth/channels with it).
_POINT_SYSTEM_PROMPT = (
    "You are a vision model helping a robot manipulate objects. The image is "
    "from a fixed head camera. Image size is IMG_W x IMG_H, origin (u=0,v=0) "
    "at top-left, +u right, +v down.\n"
    "Respond with ONE JSON object and nothing else:\n"
    '  {"u": <int>, "v": <int>, "confidence": <0-1>, "reasoning": "<short>"}\n'
    "or, if the target is not visible:\n"
    '  {"found": false, "reason": "<short>"}'
)
