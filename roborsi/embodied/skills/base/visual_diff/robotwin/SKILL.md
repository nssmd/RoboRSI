---
name: visual_diff
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: |
  Before/after frame diff for plan-react-replan. Captures a frame as
  "before", optionally executes an action via a follow-up call, then
  captures "after" and asks the VLM: "what changed? did the intended
  action happen?". Composes a side-by-side panel (before | after | pixel
  diff heatmap) so the VLM has all three signals in one image. Replaces
  "VLM stares at after-frame alone trying to infer if it matches intent".
args:
  mode: { type: string, required: true, enum: [snapshot, diff],
          description: "snapshot = save current frame as the 'before' anchor and return; diff = capture 'after' now, build panel, ask VLM." }
  expected_change: { type: string, required: false, description: "(diff mode) one-line description of the intended change, e.g. 'hammer head should now be on top of the red block'. Used in the VLM prompt." }
  camera: { type: string, required: false, description: "Camera (default head_camera)." }
  anchor_id: { type: string, required: false, description: "Tag for the before-frame so multiple diffs can coexist (default 'last')." }
returns:
  ok: bool
  changed: bool                  # (diff mode) VLM verdict
  matches_expectation: bool       # (diff mode)
  vlm_reason: str
  panel_path: str                # (diff mode) saved before|after|heatmap panel
when_to_use: |
  After any non-trivial action whose success is hard to read from the
  after-frame alone — pick/place, tap, push. Call visual_diff(mode=snapshot)
  BEFORE the action, then visual_diff(mode=diff, expected_change=...)
  AFTER. Lets the VLM see the delta directly, not infer it.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"mode": "snapshot", "camera": "head_camera"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---
