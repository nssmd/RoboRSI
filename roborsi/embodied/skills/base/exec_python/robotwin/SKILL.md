---
name: exec_python
kind: base
robot: robotwin
category: control
version: 0.1.0
description: |
  CaP-X-inspired Code-as-Policy escape hatch: instead of unrolling a
  multi-step plan into N separate tool_calls (each costing a round-trip),
  emit ONE Python snippet that calls the same tool dispatchers as
  functions. Every other registered base skill is callable as a Python
  function from inside the snippet:
      r = localize_object_top_center(object='red block')
      for arm in ('left', 'right'):
          chk = is_reachable(arm=arm, x=r['xyz'][0], y=r['xyz'][1], z=r['xyz'][2])
          if chk.get('reachable'):
              break
      grasp_then_lift_graspgen(arm=arm, object='hammer', extra_queries=[...])

  The snippet runs in a sandboxed namespace with:
    - all base skills exposed as functions returning their result dict
    - numpy as np
    - the standard math module
    - a `state` dict for intermediate values (preserved across calls)
    - print() captured to stdout, returned to VLM
  Returns: { ok, stdout, stderr, returned_values_dict, last_image_path }.

  Use when:
    - the plan needs loops, conditions, or arithmetic on tool outputs
    - you'd otherwise need 5+ tool_use blocks in one turn
    - you want to factor reusable inline helpers (define `def my_helper(): ...`
      inside the snippet)

  Do NOT use for: single tool calls, anything that doesn't compose results.
args:
  code: { type: string, required: true, description: "Python source. Functions for every base skill are pre-imported. Use return_dict={} for values you want surfaced." }
  description: { type: string, required: false, description: "1-line intent (logged in trace)." }
returns:
  ok: bool
  stdout: str
  stderr: str
  returned_values_dict: dict
  exception: str?
  n_skill_calls: int
when_to_use: |
  Whenever the next 3+ tool_calls form a loop / branch / arithmetic over
  prior results. Saves turns, lets VLM express richer policies.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"code": "print('harness exec_python ok')"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---
