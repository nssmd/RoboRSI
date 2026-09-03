"""Planner role — Opus LLM call that writes plan.md before execution.

Inputs: user_msg, task_name, recent reflections.
Output: mission_spec dict + plan.md written into workspace.

The mission_spec is what Engineer reads to decide what to do; plan.md is
the human-readable artifact and also the document Engineer mutates as
sub-goals get completed.
"""
from __future__ import annotations

import json
import re
from typing import Any

from roborsi.agents.workspace import Workspace


def _task_success_source(task: str) -> str:
    """PURE-VISION: disabled. Reading the sim task's check_success() source
    reveals ground-truth object variables/thresholds — a camera-only robot (and
    therefore the Planner, which must stay blind to GT) can't have it. The
    Planner now infers success from the task name + user instruction + wiki
    strategy only. Kept as a no-op stub so callers don't break."""
    return ""


def _skill_catalog(task: str, user_msg: str, ns: str = "robotwin") -> str:
    """Render the SAME base-skill shortlist the Engineer will be offered, as
    `name: description` lines. The Planner otherwise only knows the few skills
    hardcoded in its prompt, so it invents move_to_pose sequences for maneuvers
    a specialized skill already covers (dump_bin_bigbin: the stale plan scripted
    a hand-rolled tilt and never named tip_pour). Aligning the plan's vocabulary
    with the real execution tool surface is what makes the Engineer reach for
    the right skill instead of blind-trying primitives. Empty on any error →
    Planner falls back to its prior behaviour, never breaks planning.

    `ns` selects the backend's skill namespace. Non-robotwin backends (libero)
    have a small namespace-scoped muscle and the Sonnet shortlist only knows the
    robotwin registry, so for them we list all of base/<ns>/ directly."""
    from roborsi.embodied.skills import discover, discover_ns
    if ns != "robotwin":
        # Only list skills the Engineer can actually call: in LIBERO perception
        # mode the GT-pose readers (describe_scene / get_object_pose) are hidden
        # from the Engineer, so the Planner must NOT plan with them either — else
        # it drafts a describe_scene-first recipe the Engineer can't follow and
        # the Reviewer misreads the failure as "prompt non-adherence".
        from roborsi.embodied.agent_loop.prompt_tools import _hidden_tools
        hidden = _hidden_tools(ns)
        return "\n".join(
            f"  {s.name}: {(s.description or '').splitlines()[0].strip()[:90]}"
            for s in sorted(discover_ns(ns), key=lambda x: x.name)
            if s.name not in hidden
        )
    from roborsi.embodied.agent_loop.prompt_tools import _maybe_shortlist_skills
    names = _maybe_shortlist_skills(user_msg or task, task, 0)
    if not names:
        return ""
    skills = {s.name: s for s in discover()}
    lines = []
    for n in sorted(names):
        s = skills.get(n)
        if s is None:
            continue
        desc = (s.description or "").splitlines()[0].strip()[:90]
        lines.append(f"  {n}: {desc}")
    return "\n".join(lines)


_SYSTEM_PROMPT = """You are PLANNER for a robot manipulation atomic task.

Your job is to produce ONE document (plan.md) that the Engineer will
follow. The Engineer is a separate LLM agent that will drive the sim
loop (find_pixel, gripper, move_to_pose, ...). The Reviewer is a third
LLM agent that reads plan.md + execution trace + outcome and decides
whether to propose a skill fix.

You DO NOT execute anything. You only write the plan.

OUTPUT FORMAT — emit a single fenced JSON block followed by the full
plan.md markdown. Nothing else. Schema:

```json
{
  "goal": "one-line statement of what success looks like",
  "sub_goals": ["ordered list of concrete sub-steps"],
  "success_criteria": ["bullet conditions that must all hold to call done(True) — when a SIM SUCCESS PREDICATE block is provided, EACH criterion must translate one of its checks into a concrete observable end-state (e.g. 'contents now inside the bin, source container empty', NOT 'container grasped/lifted'); the final action step must be the one that satisfies the LAST predicate condition"],
  "candidate_skills": ["base skill names you expect Engineer to use"],
  "expected_steps": 9,
  "risks": ["known failure modes the Engineer should watch for"]
}
```

```markdown
# Plan: <task_name>

## Goal
...

## Sub-goals
1. ...

## Success criteria
- ...

## Candidate skills
- `name` — why it fits

## Expected n_steps
N

## Risks
- ...
```

Be concrete. Reference specific tool names. Keep the plan ≤ 60 lines.

exec_python discipline: write Sub-goals as NUMBERED TOOL STEPS. Do NOT tell the
Engineer to "call exec_python ONCE with the block below / transcribe verbatim"
and then give PROSE — a promised-but-absent code block leaves the Engineer
nothing to run and it burns the budget hand-improvising (the place_burger_fries
0/N failure mode). Either give numbered tool steps directly (preferred), or, if
you truly want one exec_python call, emit the ACTUAL fenced ```python code.

SKILL SELECTION (critical). An `AVAILABLE SKILLS` catalog (`name: description`)
is provided below the task — it is the ACTUAL shortlist the Engineer will be
offered. Your `candidate_skills` MUST be names from that catalog, and your
Sub-goals / plan steps MUST call those skills by name. NEVER script a
manipulation out of repeated `move_to_pose` / `move_fingertip_to` when a
specialized skill in the catalog covers it. Concretely: for a pour / dump /
tip-out / empty-container task, call `tip_pour` (it searches a reachable
past-horizontal tilt AND a high tilted hold itself — do NOT hand-roll the tilt
with move_to_pose wrist-rolls, which IK-thrash: dump_bin_bigbin burned 30+
move_to_pose calls that way). move_to_pose / move_fingertip_to are for APPROACH
and TRANSPORT only, never for a maneuver a catalog skill performs.

PURE VISION (critical): the Engineer has NO ground truth — no object poses, no
names from the sim. Every object must be LOCALIZED from the camera
(find_pixel + unproject_pixel, or localize_object_top_center) before it can be
grasped or used as a target. Your plan's sub-goals MUST start with a perception
step for each object; never assume a coordinate is known.

GRASP POLICY. For ANY pick/grasp sub-goal, plan a DEDICATED vision grasp skill,
never a hand-rolled one. All are PURE VISION (Grounded-SAM + camera depth):
  • grasp_obb(arm, object=..., u, v) — CaP-X OBB top-down for REGULAR shapes
    (boxes / cubes / cylinders); PREFER it for clean box/cylinder objects.
  • grasp_object(arm, object=...) — GraspGen 6-DoF for general / irregular shapes.
  • grasp_flat(arm, object=...) — specialized low pinch for FLAT/THIN slabs the
    others close on air above (EXPERIMENTAL).
(The ground-truth contact-point grasp pick_actor_by_contact_point and its
graspgen wrapper, and describe_scene_actors, are DELETED — NEVER plan them;
localize from vision with look + find_pixel / localize_object_top_center.)
Do NOT plan a grasp out of move_fingertip_to + close_gripper: that path is
low-precision, closes on air, and burns the step budget. move_fingertip_to /
move_to_pose are for APPROACH, TRANSPORT, and DROP only — never for the grasp
itself. A manual close is allowed ONLY as a fallback AFTER a grasp skill returns
ok=False. For TRANSPORT after a grasp: never reuse the holding grasp-quat in
move_to_pose (infeasible workspace-wide → IK thrash); plan place_obb (into a
container) / place_object_in / place_held_at_target_servo (after perceiving the
target) or a top-down quat [0.5,-0.5,0.5,0.5] for carry+place.
"""


def _extract_json_and_md(reply: str) -> tuple[dict, str]:
    """Parse Planner's two fenced blocks. Tolerant of missing fences."""
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", reply, re.S)
    md_match = re.search(r"```markdown\s*(.*?)```", reply, re.S)
    spec = json.loads(json_match.group(1)) if json_match else {}
    plan_md = md_match.group(1).strip() if md_match else reply.strip()
    return spec, plan_md


# ──────────────────────────────────────────────────────────────────────────
# Long-horizon decomposition — Planner.decompose (LH front-end)
# ──────────────────────────────────────────────────────────────────────────
#
# The LH front-end reuses the SAME Planner class (session-per-task, same
# persistent_agent.run_role dispatch) — there is no separate LHPlanner. The LH
# system prompt + block parser + fallback synth live here as module helpers.


_LH_SYSTEM_PROMPT = """You are LH PLANNER for a long-horizon robot
manipulation task. Your job is to decompose the LH task into ordered
ATOMIC sub-tasks and write a COMPLETE EXECUTION PLAN for each in
plan_<i>.md. The Engineer LLM downstream reads plan_<i>.md DIRECTLY
as its instruction — there is no separate static recipe. You own
the recipe. Make it concrete and scene-aware.

You DO NOT execute anything. The downstream LHExecutor will call an
Engineer LLM on each atomic with plan_<i>.md as instruction. A
Reviewer judges each atomic via the AUTHORITATIVE SIM GROUND TRUTH
(actor xyz, gripper vals, head_camera image).

OUTPUT — emit a single fenced JSON block, then for EACH atomic emit
a fenced markdown block tagged with its index. Schema:

```json
{
  "lh_task": "<task name>",
  "overall_goal": "one line summary",
  "ordered_atomics": [
    {"index": 0,
     "atomic": "<registered atomic name>",
     "args": {},
     "why": "what this step contributes to the LH goal",
     "success_criteria": ["bullets that must hold before next atomic"],
     "candidate_skills": ["base skills this atomic likely needs"]
    },
    ...
  ],
  "expected_total_steps": 30,
  "risks": ["whole-LH risks"]
}
```

Then for each atomic emit a FULL execution plan. This is the Engineer's
ONLY instruction — include everything Engineer needs to drive the sim:

```plan_<index>.md
# Atomic <index>: <atomic_name>

## Goal
One paragraph describing ONLY what the original task spec says this
atomic must achieve. State the physical-world predicate (e.g. "the
bowl is held by some gripper at z > 0.85, 10cm above the table").

DO NOT add constraints the task didn't specify. Do NOT write "use the
right arm" or "with the silver gripper" unless the LH task description
literally said so. Arm choice / gripper choice / approach direction
belong in Recipe, not Goal.

## Recipe (concrete, executable step-by-step — Engineer may REWRITE)
Number each step. Each step names the tool, its key args, and the
expected outcome. Concrete values (e.g. arm="left", actor_name="cup")
ARE OK here — Engineer can adapt during execution if a hardcoded
choice doesn't work (e.g. "left arm can't reach → rewrite step 3
with arm=right"). Recipe is the only section Engineer can amend
directly.

Example:
  1. look(camera="head_camera"), then find_pixel(object="silver bowl")
     → (u, v). Cache the pixel.
  2. is_reachable(arm="left", x=BOWL.x, y=BOWL.y, z=BOWL.z+0.02)
     AND is_reachable(arm="right", ...). Pick CHOSEN_ARM on the object's
     side (object x>0 → right, x<0 → left).
  3. If the OTHER arm holds something near workspace center, call
     park_arm(arm=<other_arm>) first.
  4. grasp_obb(arm=CHOSEN_ARM, object="silver bowl", u=u, v=v) for a
     regular shape (else grasp_object). Verify is_holding +
     verify_holding_visual.
  5. If not held after 2 tries: re-localize (find_pixel) and try the
     OTHER arm; never hand-roll the grasp from move_fingertip_to.
  ...

## exec_python discipline
Write the Recipe as NUMBERED TOOL STEPS (preferred). Do NOT tell the
Engineer to "call exec_python with the block below / transcribe verbatim"
and then give PROSE instead of code — a promised-but-absent code block
leaves the Engineer nothing to run and it burns the budget improvising
by hand. If you genuinely want one exec_python call, you MUST emit the
ACTUAL fenced ```python code; otherwise just give the numbered steps.

## Hard rules (failures here override Engineer's done call)
- PROHIBITED: calling done(success=True) without view_frame +
  verify_holding_visual.
- (anything that, if violated, makes the success claim invalid.
  These are immutable by Engineer; only a Reviewer-proposed amend
  can change them.)

## Done gate (mandatory checks BEFORE done(success=True))
1. view_frame(camera="head_camera")
2. verify_holding_visual(arm=CHOSEN_ARM, object="silver bowl")
   → require holding_visual=True AND confidence>=0.7
3. The visual evidence in the image must show the bowl elevated
   clearly above the table.

## Success criteria (Reviewer judges with sim GT)
- bullets the Reviewer checks against actor xyz + image
```

Constraints:
- Atomics MUST use names from the registered list (`<atomic>.zeroshot`).
- Each plan_<i>.md MUST contain all five sections above (Goal,
  Recipe, Hard rules, Done gate, Success criteria).
- No length cap — be thorough. Engineer needs this to be complete.
- Goal stays narrow (only task-given facts). Recipe is where you
  apply scene knowledge to make execution concrete.

CROSS-ARM SAFETY (V8 incident — block + bowl dropped during right-arm
cross-midline grasp because left arm was still holding block):

  When atomic_N+1's chosen arm path crosses workspace midline AND
  atomic_N's arm is holding an actor near center, the FIRST recipe
  step of atomic_N+1 MUST be:

      "Before any grasp: call park_arm(arm=<prior_arm>) to move
       the just-finished arm + held object to the back corner so the
       active arm has a clear corridor."

  Default park pose: (±0.38, -0.40, 1.05), grip preserved.

WORKSPACE / IK NOTES (2026-06-11 measured):
- Right arm + top-down quat [0.5,-0.5,0.5,0.5] has fingertip-z floor
  ≈ 0.870; reaching below requires lateral approach or other arm.
- Table baseline z ≈ 0.72. Typical bowl rim z ≈ 0.79. So top-down
  right-arm grasp of a bowl on the left half of the table is
  geometrically infeasible — plan to use LEFT arm or lateral approach.

PRESERVE GRIP across atomics (per 2026-06-15 user feedback after
V44-V47 atomic_2 grip-slip cascades):
- When atomic K starts with an arm holding an object from atomic K-1,
  the plan_K Hard rules MUST include: "DO NOT open <arm> gripper
  unless atomic K is explicitly a release/place of that object."
- The plan_K Recipe must avoid chained manual fingertip moves on the
  holding arm — single-shot move_to_pose keeps grip via smooth motion.
- NEVER write Hard rules like "drop the held bowl on the table first"
  as a workaround — once dropped, the LH is broken.
- NO SIM CHEATING in any Hard rule or Recipe step: never reference
  `use_attach=True`, force-grip, teleport, or any physics override.
  The agent must achieve the task under real physics.

STRATEGY, NOT DATA in Recipe steps (per 2026-06-15 user feedback):
- Actors are seed-randomized within spawn ranges. NEVER hardcode actor
  xyz / specific coordinates in any plan Recipe step.
- Write Recipe as STRATEGY referring to placeholders:
  GOOD: "1. look + find_pixel(object='red block') → (u,v).
        2. grasp_obb(arm='left', object='red block', u=u, v=v)"
  BAD:  "1. grasp_obb(arm='left', object='red block',
        x=-0.168, y=-0.148, z=0.756)  # hardcoded seed-specific xyz"
- The only fixed values you may hardcode are kinematic constants (e.g.
  right-arm top-down floor ≈0.870, table baseline ≈0.72) — those are
  embodiment facts, not scene state.
- Engineer must always LOCALIZE from live vision (look + find_pixel /
  localize_object_top_center) at the start of each atomic. Recipe steps
  must encode that perception read explicitly as step 1.
"""


def _extract_blocks(reply: str) -> tuple[dict, dict[int, str]]:
    """Pull JSON spec + each plan_<i>.md from the LH decomposition reply."""
    spec_m = re.search(r"```json\s*(\{.*?\})\s*```", reply, re.S)
    spec: dict = json.loads(spec_m.group(1)) if spec_m else {}
    plans: dict[int, str] = {}
    for m in re.finditer(r"```plan_(\d+)\.md\s*(.*?)```", reply, re.S):
        plans[int(m.group(1))] = m.group(2).strip()
    return spec, plans


def _fallback_plan_md(entry: dict) -> str:
    """Synthesize a minimal plan_<i>.md if the LLM forgot to emit a fenced
    markdown for this atomic."""
    atomic = entry.get("atomic", "?")
    goal = entry.get("why", atomic)
    criteria = entry.get("success_criteria", [])
    skills = entry.get("candidate_skills", [])
    lines = [f"# Atomic {entry.get('index','?')}: {atomic}",
             "", "## Goal", goal,
             "", "## Success criteria"]
    lines += [f"- {c}" for c in (criteria or ["(unspecified)"])]
    lines += ["", "## Skills to consider"]
    lines += [f"- `{s}`" for s in skills]
    return "\n".join(lines) + "\n"


def persistent_plan_path(task: str):
    """Path to the task's persistent plan.md inside its skill dir.

    This plan survives across runs and ships with the skill (git + cold
    start). It is the document the Planner refines each run and the
    Reviewer amends; per-run workspaces get a copy for local editing.
    """
    from roborsi.agents.task_wiki import _task_skill_dir
    return _task_skill_dir(task) / "plan.md"


class Planner:
    """Single-shot Opus call that produces a mission_spec + plan.md."""

    DEFAULT_MODEL = "claude-opus-4-8"

    def __init__(self, model: str | None = None) -> None:
        self.model = model or self.DEFAULT_MODEL

    def plan(self, *, task: str, user_msg: str,
             recent_reflections: str, workspace: Workspace,
             ns: str = "robotwin") -> dict[str, Any]:
        """Call Opus, write plan.md, return mission_spec. `ns` = the active
        backend's skill namespace (drives which base skills the plan can name)."""
        # Lazy import — keeps agents package importable without sim deps.
        from roborsi.agents.plan_archive import (
            get_recent_plans, format_for_planner,
        )
        from roborsi.agents.gt_firewall import redact
        recent_plans = get_recent_plans(task, n=3)
        prior_plans_block = format_for_planner(recent_plans)
        prior_plans_block, _ = redact(task, prior_plans_block)
        # Persistent plan.md (carries improvements from prior runs). Read it
        # BEFORE the Opus call so the Planner refines it rather than starting
        # from scratch each run.
        plan_file = persistent_plan_path(task)
        persistent_plan = (
            plan_file.read_text(encoding="utf-8") if plan_file.exists() else "")
        persistent_plan, _ = redact(task, persistent_plan)
        persistent_block = (
            "=== CURRENT PERSISTENT PLAN (refine this — it carries "
            "improvements from prior runs; keep what works, only change what "
            f"failed) ===\n{persistent_plan}\n\n" if persistent_plan.strip()
            else "")
        # Task wiki: successful tool sequences to REUSE + failed-attempt
        # Reviewer diagnoses (root_cause + next_action) to ADDRESS. This is the
        # self-evo memory that lets the plan adapt across rounds instead of
        # repeating the same failed approach.
        from roborsi.agents.task_wiki import read_wiki
        wiki_md = read_wiki(task)
        wiki_block = (
            "=== TASK WIKI ===\n"
            "How to use each section (trust order):\n"
            "1. '## Manager-approved leads' — HUMAN-SIGNED-OFF, highest trust (a "
            "Manager verified each lead this task). Your plan MUST incorporate "
            "EVERY approved lead as concrete, ordered sub-goals, using the EXACT "
            "skills/params it names. Do NOT substitute your own approach for an "
            "approved lead (e.g. if a lead says the fallback is grasp_by_keypoint, "
            "the plan's fallback IS grasp_by_keypoint, not get_grasp_pose).\n"
            "2. '## Successful execution traces' / '## Key measurements' — trusted; "
            "REUSE the tool sequences / facts.\n"
            "3. '## Failed execution traces' — OBSERVED FACTS ONLY (the Reviewer's "
            "diagnosis is gated to review, not shown here); use them only to avoid "
            "repeating a dead-end sequence.\n"
            f"{wiki_md}\n\n"
            if wiki_md.strip() else "")
        success_src = _task_success_source(task)
        success_block = (
            "=== SIM SUCCESS PREDICATE (the task's own check_success() source — "
            "your success_criteria MUST cover EVERY condition here; done(True) is "
            "only legitimate when ALL of them physically hold, NOT when the object "
            f"is merely grasped/lifted) ===\n{success_src}\n\n"
            if success_src else "")
        catalog = _skill_catalog(task, user_msg, ns)
        catalog_block = (
            "=== AVAILABLE SKILLS (the Engineer's actual shortlist — pick "
            "candidate_skills from these EXACT names; prefer a specialized skill "
            f"over hand-rolled move_to_pose) ===\n{catalog}\n\n"
            if catalog else "")
        # Self-evolution: once the task has enough Sim successes and no compound
        # yet, encourage the Planner to solidify the winning recipe as a compound
        # policy proposal (opt-in — empty otherwise). Manager reviews it.
        from roborsi.agents import compound_proposal
        from roborsi.runtime_mode import evolution_enabled
        compound_block = (
            compound_proposal.encourage_block(task, wiki_md)
            if evolution_enabled()
            else ""
        )
        user_block = (
            f"=== ATOMIC TASK ===\n{task}\n\n"
            f"=== USER REQUEST ===\n{user_msg}\n\n"
            f"{success_block}"
            f"{catalog_block}"
            f"{persistent_block}"
            f"{wiki_block}"
            f"{compound_block}"
            f"{prior_plans_block + chr(10) + chr(10) if prior_plans_block else ''}"
            f"=== RECENT REFLECTIONS (last 5 turns, JSONL) ===\n"
            f"{recent_reflections}\n"
        )
        # Planner runs as a PERSISTENT (role=planner, task) session — a real
        # cc/codex process resumed every run — so it accumulates cross-run
        # planning memory. ROBORSI_ROLE_SESSION=0 falls back to the stateless
        # one-shot. See agents/persistent_agent.run_role.
        from roborsi.agents import persistent_agent
        content = persistent_agent.run_role(
            "planner", task, user_block,
            system_prompt=_SYSTEM_PROMPT, model=self.model)
        # Queue any compound-policy proposal, then strip it so it never lands in
        # plan.md (no-op unless opt-in and a valid block is present).
        if evolution_enabled():
            compound_proposal.capture(task, workspace.run_id, wiki_md, content)
        content = compound_proposal.strip(content)
        spec, plan_md = _extract_json_and_md(content)
        # Always write plan.md even if json parse failed — Engineer can
        # still read the raw markdown.
        if not plan_md:
            plan_md = f"# Plan: {task}\n\n(planner output empty)\n"
        # Persistent skill-dir plan.md is a READ-ONLY SEED (read above as the
        # refinement base). It is NEVER written here — an unproven refinement
        # must not overwrite the seed. Persistent is updated ONLY via a
        # success-gated + Manager-reviewed promotion (see
        # task_wiki.resolve_plan_promotion). Only the per-run workspace copy is
        # written each run; the Engineer/Reviewer read that ephemeral copy.
        workspace.write_plan(plan_md)
        # Fill in defaults so Engineer never sees a KeyError.
        spec.setdefault("goal", task)
        spec.setdefault("sub_goals", [])
        spec.setdefault("success_criteria", [])
        spec.setdefault("candidate_skills", [])
        spec.setdefault("expected_steps", 12)
        spec.setdefault("risks", [])
        return spec

    def decompose(self, *, lh_task: str, user_msg: str,
                  recent_reflections: str,
                  workspace: Workspace) -> dict[str, Any]:
        """Long-horizon front-end: one persistent-session turn decomposes the
        LH task into ordered atomics, writes lh_plan.md (overall) + per-atomic
        plan_<i>.md into the workspace, and returns the structured mission_spec.

        Same Planner class + same persistent_agent.run_role dispatch as .plan();
        only the system prompt (LH decomposition) and the block parser differ.
        There is no separate LHPlanner."""
        from roborsi.embodied.skills import discover
        from roborsi.agents.plan_archive import (
            get_recent_plans, format_for_planner,
        )
        from roborsi.agents.atomic_bottleneck import (
            get_bottleneck_atomics,
            format_for_planner as format_bottlenecks_for_planner,
        )
        prior_block = format_for_planner(get_recent_plans(lh_task, n=3))
        bottleneck_block = format_bottlenecks_for_planner(get_bottleneck_atomics())
        # Build the canonical list of atomics the LH plan is allowed to use. If
        # it picks anything outside this set, LHExecutor will fail to find the
        # .zeroshot child. Anti-hallucination: feed the registry-grounded list
        # directly into the prompt.
        registered = sorted({
            sk.name.removesuffix(".zeroshot")
            for sk in discover() if sk.name.endswith(".zeroshot")
        })
        atomic_index = "\n".join(f"  - {n}" for n in registered)
        # Wiki: accumulated knowledge for this task. Read-only here; gives the
        # Planner success-trace patterns + Reviewer-approved measurements.
        from roborsi.agents.task_wiki import read_wiki
        wiki_md = read_wiki(lh_task)
        wiki_block = (
            f"=== TASK WIKI (~/.roborsi/wiki/{lh_task}.md) ===\n"
            f"{wiki_md}\n"
            f"(Use this to inform plan_K recipe steps. Do NOT paste "
            f"literal xyz; cite measurements where relevant.)\n"
        )
        user_block = (
            f"=== LONG-HORIZON TASK ===\n{lh_task}\n\n"
            f"=== USER REQUEST ===\n{user_msg}\n\n"
            f"=== REGISTERED ATOMICS (use ONLY these names) ===\n"
            f"{atomic_index}\n\n"
            f"{wiki_block}\n"
            f"{bottleneck_block + chr(10) + chr(10) if bottleneck_block else ''}"
            f"{prior_block + chr(10) + chr(10) if prior_block else ''}"
            f"=== RECENT REFLECTIONS ===\n{recent_reflections}\n"
        )
        # Same persistent (role=planner, task) session dispatch as .plan().
        # ROBORSI_ROLE_SESSION=0 falls back to the stateless one-shot.
        from roborsi.agents import persistent_agent
        content = persistent_agent.run_role(
            "planner", lh_task, user_block,
            system_prompt=_LH_SYSTEM_PROMPT, model=self.model)
        spec, plans = _extract_blocks(content)
        # Defaults so downstream code never KeyErrors.
        spec.setdefault("lh_task", lh_task)
        spec.setdefault("overall_goal", lh_task)
        spec.setdefault("ordered_atomics", [])
        spec.setdefault("expected_total_steps", 30)
        spec.setdefault("risks", [])

        # Filter out atomics whose .zeroshot is not registered. Otherwise
        # LHExecutor's run_skill call would crash mid-run. Log dropped entries
        # to the workspace for the operator's review.
        kept: list[dict] = []
        dropped: list[str] = []
        for entry in spec["ordered_atomics"]:
            name = entry.get("atomic", "")
            if name in registered:
                kept.append(entry)
            else:
                dropped.append(name)
        if dropped:
            (workspace.root / "lh_plan_warnings.md").write_text(
                "# LH decomposition produced invalid atomic names; dropped\n\n"
                + "\n".join(f"- `{n}`" for n in dropped)
                + "\n\n(Either rename to a registered atomic OR author the new "
                  "atomic via propose_new_skill before re-running.)\n",
                encoding="utf-8")
        spec["ordered_atomics"] = kept
        # Re-index so downstream sub-dirs are 00, 01, ... contiguous.
        for i, entry in enumerate(kept):
            entry["index"] = i

        # Write lh_plan.md (overall) — full markdown reply for human reading.
        # workspace.write_plan writes plan.md by default; LH wants lh_plan.md.
        (workspace.root / "lh_plan.md").write_text(content, encoding="utf-8")
        if (workspace.root / "plan.md").exists():
            (workspace.root / "plan.md").unlink()

        # Write per-atomic plan_<i>.md into the sub-dirs the LHExecutor uses
        # as workspaces for each sub-attempt.
        for entry in spec["ordered_atomics"]:
            idx = int(entry.get("index", 0))
            atomic = entry.get("atomic", f"step_{idx}")
            sub = workspace.root / f"{idx:02d}_{atomic}"
            sub.mkdir(parents=True, exist_ok=True)
            plan_md = plans.get(idx) or _fallback_plan_md(entry)
            (sub / "plan.md").write_text(plan_md, encoding="utf-8")

        return spec
