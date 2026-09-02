"""rl.pi0_posttrain — SKELETON.

Deliberately unimplemented. RL-in-sim is a project unto itself: PPO /
GRPO choice, parallel rollout buffer, reward shaping. Putting real code
here now would be premature — we land it after finetune + eval are
proven end-to-end on at least one task.
"""

from __future__ import annotations

from typing import Any


def run(**params: Any) -> dict[str, Any]:
    raise NotImplementedError(
        "pi0_posttrain is a skeleton. Wire algorithm + parallel envs + "
        "reward source before removing this guard."
    )
