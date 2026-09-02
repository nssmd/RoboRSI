---
name: recall_past_success
kind: base
robot: robotwin
category: perception
version: 0.1.0
description: Look up past SUCCESSFUL trace records for an atomic skill from RAG history. Returns the tool sequence(s) that worked before. Use when stuck — cheaper and less confusing than always injecting full traces into the prompt.
args:
  atomic: { type: string, required: true, description: "the atomic skill name we're currently doing (e.g. 'pick_block_bicoord')" }
  k: { type: int, default: 1, description: "number of past traces to return" }
returns:
  ok: bool
  count: int
  traces: list
  note: str
when_to_use: |
  Whenever you don't immediately know which tool sequence will work. Call
  recall_past_success(atomic='<the atomic skill>') to see if a past run
  solved this. If `count > 0`, mimic the tool sequence structure (adapt args
  to current scene). If `count == 0`, you're exploring fresh — succeed and
  your trace will be persisted for future calls.

  Cheap to call: only loads from disk, no VLM/network round-trip.
metadata:
  harness:
    sim_task: handover_block_with_bowls
    seeds: [0]
    args:
      - {"atomic": "pick_bowl_bicoord"}
    pass_criteria:
      kind: tool_returns_well_formed
      required_keys: ["ok"]
      min_seeds_passing: 1
---

# recall_past_success · RoboTwin

The on-demand RAG tool. Replaces the previous "always inject traces into
prompt" pattern, which made prompts huge and confused tool_use parsing.

VLM owns when to consult history. Typical use:
  1. look() → see scene
  2. (Pause to think.) Have I done this before?
  3. recall_past_success(atomic='pick_block_bicoord', k=1) → returns 1 trace
  4. Mimic structure with current pixel coords from get_object_bbox.
