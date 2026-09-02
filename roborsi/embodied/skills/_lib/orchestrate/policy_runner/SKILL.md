---
name: policy_runner
kind: lifecycle
lifecycle: orchestrate
version: 0.1.0
description: Shared utilities to load a LeRobot-trained policy and roll it out on a live SimEnv. Used by both _lib.evaluation.success_rate (multi-seed) and the LH triangle (LHExecutor, single phase on shared env).
metadata:
  tags: [orchestrate, policy, inference]
---

# policy_runner

Two callables:

- `load_policy(checkpoint)` — read `train_config.json`, load `LeRobotDatasetMetadata`, instantiate via `make_policy(cfg, ds_meta=...)`, send to CUDA, eval mode.
- `rollout_one(policy, env, seed, max_steps, action_type)` — reset to `seed`, step until done or budget; return `{success, outcome, steps}`.
- `policy_forward(policy, obs, action_type)` — translate `SimObservation` to LeRobot batch (image float [0,1] CHW + state), call `select_action`, return numpy.

Shared between eval and long_horizon execution so the policy code path is one place, not duplicated.
