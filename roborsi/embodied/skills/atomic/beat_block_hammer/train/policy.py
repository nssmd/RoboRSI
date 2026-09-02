"""atomic.beat_block_hammer.train — build dataset + finetune."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills import run as run_skill


_SKILL_LABEL = "beat_block_hammer"


def run(
    base_model: str = "pi0.5",
    steps: int = 20000,
    batch_size: int = 8,
    lr: float = 2.5e-5,
    dataset_name: str = "beat_block_hammer_v1",
    **_: Any,
) -> dict[str, Any]:
    ds_result = run_skill(
        "lerobot_build",
        skill_label=_SKILL_LABEL,
        dataset_name=dataset_name,
    )
    # LeRobot expects namespace/name repo_id format. Use 'local/<name>' to keep
    # everything offline; pi0_finetune uses --dataset.root to point at disk.
    repo_id = dataset_name if "/" in dataset_name else f"local/{dataset_name}"
    ckpt_result = run_skill(
        "pi0_finetune",
        dataset=repo_id,
        base_model=base_model,
        steps=steps,
        batch_size=batch_size,
        lr=lr,
    )
    return {
        "skill": "atomic.beat_block_hammer.train",
        "dataset_root": ds_result.get("root"),
        "dataset_episodes": ds_result.get("episodes"),
        "checkpoint": ckpt_result.get("output_dir"),
        "last_log": ckpt_result.get("last_log"),
    }
