---
name: register_skill
kind: base
robot: robotwin
category: meta
version: 0.1.0
description: |
  VLM-authored skill creation. When the existing toolbox can't accomplish
  what you need, define a new helper function with Python code and
  register it. After register_skill returns ok=True the function is:
    - Available in the namespace of subsequent exec_python(code=...)
      calls (call it like any other base skill)
    - Persisted to the skill library; shows up in future trials' system
      prompt as a PROMOTED FUNCTION
  This is the CaP-X "skill library evolution" mechanism, but PROACTIVE —
  you decide when to invent, you don't wait for occurrence-counting.
args:
  name: { type: string, required: true,
          description: "snake_case unique name; cannot collide with existing base skills." }
  code: { type: string, required: true,
          description: "Full Python source of a single top-level def. May call any other base skill function in its body. Avoid imports outside the sandbox whitelist." }
  docstring: { type: string, required: true,
               description: "1-2 sentence description: what the function does + when to use it." }
  test_call_args: { type: dict, required: false,
                     description: "Optional kwargs to test-invoke the function once with after registration. If the call raises, the registration is rolled back." }
returns:
  ok: bool
  name: str
  registered_at: str
  test_invoke: dict?      # if test_call_args was passed
  reason: str?            # on failure: parse error / collision / test failure
when_to_use: |
  Whenever you find yourself writing the same multi-step pattern twice in
  exec_python, OR when no existing base skill does what you need (e.g.
  "find object by color filter + cluster + return centroid").

  DO NOT register trivial wrappers (1-2 lines) — just inline them. Register
  things 5-15 lines long that capture a reusable concept.
metadata:
  harness:
    skip_harness: true
    skip_reason: "meta — registers new skills; side-effects"
---


# register_skill · RoboTwin

## Example

```python
register_skill(
    name="find_yellow_handle_xyz",
    docstring="Locate the yellow plastic handle of a tool via color HSV "
              "filter + bbox-narrow point cloud + centroid.",
    code='''
def find_yellow_handle_xyz(camera="head_camera", bbox_pad_px=30):
    """Locate yellow handle XYZ via color HSV + bbox cloud centroid."""
    coarse = find_pixel(object="hammer handle")
    if not coarse.get("ok"):
        return {"ok": False, "reason": "find_pixel failed"}
    u, v = int(coarse["u"]), int(coarse["v"])
    seg = segment_object_pointcloud(
        object="hammer", ee_xyz=None, vlm_verify=False)
    if not seg.get("ok"):
        return {"ok": False, "reason": "segment failed"}
    import numpy as np
    cloud = np.asarray(seg["xyz"])
    centroid = cloud.mean(axis=0)
    return {"ok": True, "xyz": centroid.tolist(),
            "n_points": int(len(cloud))}
''',
    test_call_args={"camera": "head_camera"},
)
```

## Why proactive skill creation matters

CaP-X / Rollout evolve skills passively (count occurrences across trials,
promote at threshold). That's a slow flywheel.

This skill makes evolution PROACTIVE: when the VLM **realizes mid-task**
that a multi-step idiom keeps repeating or that no existing skill quite
fits, it stops, defines the helper, and uses it.

The skill is persisted to `~/.roborsi/data/<task>/_function_library.json`
with `occurrences=1`. Future trials that reach success will increment the
count; ≥2 promotes it into the system prompt's PROMOTED FUNCTIONS section
of every subsequent run.

## Sandbox constraints (same as exec_python)

- ONE top-level `def name(...): ...` per call (multiple defs allowed but
  only the one matching `name` is registered).
- May call any registered base skill (auto-imported into the function body
  at execution time via exec_python's binding mechanism).
- May import `math`, `numpy`, `json`, `re`, `time`, `itertools`,
  `functools`, `collections`, `operator`, `traceback`, `copy`, `dataclasses`.
- May NOT do file I/O, subprocess, network, eval/exec.

## Failure modes

- name collision with existing base skill → `ok=False reason="collision: 'X'"`
- code is not a valid Python `def` block → parse error returned
- if test_call_args provided and the test raised → registration rolled back
