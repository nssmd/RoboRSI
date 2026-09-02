"""long_horizon.clean_table.progress_judge — task-specific phase judging.

Reads parent SKILL.md to inject task-specific success criteria into the
VLM judge call. Generic _lib.progress_score does the actual VLM I/O.
"""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills import get as get_skill
from roborsi.embodied.skills import run as run_skill


_PARENT = "clean_table"


def run(
    phase: str,
    image_path: str,
    expected: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    if expected is None:
        fm = _parent_frontmatter(_PARENT)
        prompts = (fm.get("metadata") or {}).get("vlm_prompts") or {}
        expected = prompts.get("progress_check") or "The phase has visibly succeeded."
    return run_skill("progress_score", phase=phase, image_path=image_path, expected=expected)


def _parent_frontmatter(name: str) -> dict[str, Any]:
    sk = get_skill(name)
    if sk is None:
        raise RuntimeError(f"parent task '{name}' not found in skill catalogue")
    return sk.frontmatter or {}
