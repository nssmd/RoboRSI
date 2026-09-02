"""atomic.stack_bowls_bicoord.train — build dataset + finetune."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills import run as run_skill


_SKILL_LABEL = "stack_bowls_bicoord"


def run(
    base_model: str = "act",
    steps: int = 2000,
    batch_size: int = 4,
    lr: float = 5e-5,
    dataset_name: str = "stack_bowls_bicoord_v1",
    **_: Any,
) -> dict[str, Any]:
    ds_result = run_skill(
        "lerobot_build",
        skill_label=_SKILL_LABEL,
        dataset_name=dataset_name,
    )
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
        "skill": "atomic.stack_bowls_bicoord.train",
        "dataset_root": ds_result.get("root"),
        "dataset_episodes": ds_result.get("episodes"),
        "checkpoint": ckpt_result.get("output_dir"),
        "last_log": ckpt_result.get("last_log"),
    }
