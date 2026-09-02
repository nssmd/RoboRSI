---
name: stack_bowls_bicoord.train
kind: atomic_subskill
parent: stack_bowls_bicoord
phase: train
version: 0.1.0
description: Build a LeRobot v3 dataset from stack_bowls_bicoord successful trajectories, then finetune ACT.
metadata:
  tags: [training, sim, bicoord, act]
  uses_lib: [_lib.dataset.lerobot_build, _lib.training.pi0_finetune]
params:
  base_model:    { type: string, default: act }
  steps:         { type: int,    default: 2000 }
  batch_size:    { type: int,    default: 4 }
  lr:            { type: float,  default: 5e-5 }
  dataset_name:  { type: string, default: stack_bowls_bicoord_v1 }
returns:
  dataset_root: "str"
  checkpoint:   "str"
---

# stack_bowls_bicoord / train

Same shape as click_bell.train. ACT default.
