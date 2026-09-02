---
name: harness_standard
kind: meta_skill
description: Specification of what makes a base skill "harness-validated". Every base/robotwin skill must carry a harness_args field in its SKILL.md frontmatter that gives the smallest invocation exercising its core path, and must satisfy the pass criteria for its skill_kind. Used by review_base_skill_harness as the grading rubric.
version: 1
metadata:
  tags: [harness, base-skill, governance, meta, validation]
---

# Why

A base skill is infrastructure — many atomics depend on it. A subtle bug
(wrong quat order, wrong link name, leftover hardcode) ships silently until
an orchestrator burns ~3 hours of GPU producing failures whose root cause is
the base skill. Every base skill must therefore carry a self-test
specification and PASS it before landing.

# Required frontmatter fields

```yaml
---
name: <skill_name>
kind: base_skill
category: base/robotwin
description: <one line>
version: <int>
metadata:
  tags: [...]
  skill_kind: grasp | place | verify | detect | move | introspect | other
  harness:
    sim_task: <bicoord env name, e.g. handover_block_with_bowls>
    args:
      - <primary args dict>
    extra_args:                     # optional — alternative configurations
      - <args dict>
      - <args dict>
    seeds: [<int>, <int>, ...]      # at least 3 seeds; default [0, 1, 2]
    pass_criteria:                  # one of the schemes below
      kind: <see "Pass criteria schemes">
      ...
    skip_harness: false             # set true ONLY for skills that can't run
                                    # standalone (require multi-arm coordination,
                                    # human-in-loop, network call). Skip MUST
                                    # come with a one-line reason.
---
```

`skill_kind` drives which pass criteria scheme applies. New schemes can be
added here as needed.

# Pass criteria schemes

## `grasp_holds_actor`
For `pick_*`, `grasp_*` skills.

```yaml
pass_criteria:
  kind: grasp_holds_actor
  actor_attr: cup_2          # the env._impl attribute name passed in args
  min_seeds_passing: 2       # of N seeds, at least this many must pass
```

Passes when, for ≥ `min_seeds_passing` of the `seeds`, the returned dict has
`holding_visual=True` AND `verify_source="sim_ground_truth"` AND post-call
sim ground truth confirms `actor_attr` in gripper-link contact list.

## `place_actor_at_target`
For `place_*` skills.

```yaml
pass_criteria:
  kind: place_actor_at_target
  actor_attr: cup_2
  target_attr: target_2
  min_seeds_passing: 2
```

Passes when post-call `getattr(env._impl, actor_attr).get_pose().p` is within
the sim's own `eps` of `target_attr.get_functional_point(0)` AND
`actor_attr` no longer in any gripper-link contact list. (No eps tuned by us
— pull from the env's `check_success` constants.)

## `verify_returns_bool`
For `verify_*` skills (verify_holding, verify_contact, verify_pick_complete).

```yaml
pass_criteria:
  kind: verify_returns_bool
  setup: hold_actor           # the setup hook to call before this verify
  setup_args: {...}
  expected: true              # what verify should report after the setup
  min_seeds_passing: 2
```

Passes when, with `setup` having been executed (e.g. `pick_actor_by_contact_point`
already lifted the bowl), the verify returns a bool matching `expected` for
≥ `min_seeds_passing` of seeds. Distinguishes the verify's own correctness
from the upstream grasp's correctness.

## `tool_returns_well_formed`
For `detect`, `introspect`, `read_*`, `look`, `find_pixel`, `find_object_via_wrist`,
`describe_scene_actors`, `get_arm_pose`, `list_contacts`.

```yaml
pass_criteria:
  kind: tool_returns_well_formed
  required_keys: [count, actors]    # keys that MUST appear in returned dict
  optional_keys: [note]
  min_seeds_passing: 3
```

Passes when, for ≥ `min_seeds_passing` of seeds, the call returns `ok=True` AND
returned dict contains every key in `required_keys`.

## `move_completes`
For `move_*`, `gripper`, `home` skills.

```yaml
pass_criteria:
  kind: move_completes
  args:
    arm: right
    x: 0.20
    y: -0.10
    z: 1.00
  min_seeds_passing: 2
```

Passes when, for ≥ `min_seeds_passing`, the call returns `ok=True` AND
sim's post-call EE pose (from `impl.robot.get_*_ee_pose()`) is within
2 cm of the requested xyz (2 cm is a fixed transport tolerance derived
from typical cuRobo trajectory residual; this is the ONLY tunable in the
whole standard, exposed as `move_tolerance_m` per call).

## `skip_harness`
For skills the harness cannot validate standalone — must list reason.

```yaml
metadata:
  harness:
    skip_harness: true
    skip_reason: "Requires both arms coordinated via play_once-style move()."
```

# Args validity

Every `args` entry in `harness.args` and `extra_args` must:
1. Be a JSON-serializable dict.
2. Pass directly to the skill's `dispatch_runtime(state, args)` — no helper
   re-shape.
3. Cover the most common production usage (e.g. for `pick_actor_by_contact_point`,
   the args dict for `cup_2`, `contact_point_id=0` — the expert's first move).

# Forbidden in pass criteria

- **No hardcoded thresholds in our test harness**. If a criterion needs a number,
  read it from the sim's own `check_success` or from `actor.config`.
- **No VLM in the harness pass-criteria**. Sim GT only — the harness exists to
  validate the skill independent of perception.

# Workflow

A reviewer (`review_base_skill_harness`) reads the skill's frontmatter,
constructs the `scripts/test_base_skill.py` invocation, runs it, parses the
JSON output, and APPROVES iff `min_seeds_passing` is met.

If a skill has `skip_harness: true`, the reviewer accepts only with explicit
human OK in the proposal review note.

# Schema migration for existing 56 base skills

Existing base/robotwin skills predate this standard. Migrate by adding the
`harness:` block to each SKILL.md (or marking `skip_harness: true` with
reason). Skills that don't get migrated are flagged in the periodic audit
report as `untested`.
