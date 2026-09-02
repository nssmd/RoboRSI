---
name: clean_table
kind: long_horizon
domain: manipulation
version: 0.1.0
description: Clear a tabletop of arbitrary objects by composing atomic skills. Decomposed at runtime by VLM, judged step-by-step, logged for post-training.
metadata:
  tags: [long_horizon, multi-step, sim, robotwin]
  candidate_atomics: [beat_block_hammer]   # 增长中；plan 阶段 VLM 从这选
  vlm_prompts:
    instruction: "Clear the table — every object should end up in its tray or off the workspace."
    decompose_hint: |
      Break the instruction into a sequence of available atomic tasks.
      For each: name the atomic, the target object, and a one-sentence rationale.
      Stop when the table is empty.
    progress_check: |
      Given a before-and-after pair around one atomic, decide whether that
      atomic actually moved the world toward "all objects cleared".
      Done iff the target object is no longer on the workspace surface.
  posttrain:
    reward_recipe: "sparse phase reward (1 if progress_judge.done else 0) + small step penalty"
---

# clean_table (long_horizon)

每个 long_horizon task 自带 task 特定的 progress_judge / posttrain，且只跟此 task 相关——不是平台共享的全局 skill。
共性的判定/训练逻辑在 `_lib/`（progress_score / pi0_posttrain），子 skill 只是把任务特定的 prompts / 判定标准注入。规划与执行统一走 3-role 三角（LHPlanner → LHExecutor → LHReviewer）。

## task 特化

| sub-skill | 它知道的 task 特定信息 |
|---|---|
| `progress_judge/` | `metadata.vlm_prompts.progress_check`，每 atomic 跑前/跑后的图像对 |
| `posttrain/`      | `metadata.posttrain.reward_recipe` |

## 执行循环

```
1. LHPlanner（读 frontmatter + wiki）→ ordered atomics 序列
2. LHExecutor：for atomic in 序列:
     run atomic 的 active_executor
     per-atomic Reviewer → done / retry / replan
3. LHReviewer → 总体 verdict；posttrain/(读 frontmatter) → 联合 RL
```

## 现状

`candidate_atomics` 只有 1 个（beat_block_hammer）。这个 long_horizon **暂时无法真跑**——它需要 ≥ 2 个 atomic 才有意义。骨架在这是为了让"加新 atomic 后立刻可串起来"成立。
