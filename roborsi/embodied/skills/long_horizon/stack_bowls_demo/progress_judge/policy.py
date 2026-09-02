"""long_horizon.stack_bowls_demo.progress_judge — wrap _lib.judging.progress_score."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills import get as get_skill
from roborsi.embodied.skills import run as run_skill


def run(phase: str, image_path: str, expected: str | None = None, **_: Any) -> dict[str, Any]:
    if expected is None:
        sk = get_skill("stack_bowls_demo")
        meta = (sk.frontmatter or {}).get("metadata") if sk else {}
        prompts = (meta or {}).get("vlm_prompts") or {}
        expected = prompts.get("progress_check") or "The phase has visibly succeeded."
    return run_skill("progress_score", phase=phase, image_path=image_path, expected=expected)
