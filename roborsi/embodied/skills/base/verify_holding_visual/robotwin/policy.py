"""base.robotwin.verify_holding_visual — visual-grounded grasp verification.

Implementation lives in robotwin_tools._do_verify_holding_visual; this
file exists so skill discovery finds the SKILL.md and the auto-prompt
includes it. The actual VLM dispatch happens inside the runtime so the
wrist image and VLM call share state with the running episode.
"""

from __future__ import annotations

from typing import Any


def run(env, arm: str, object: str, **_: Any) -> dict[str, Any]:
    """Out-of-band invocation (rarely used). The codeact / rollout loop
    dispatches via _do_verify_holding_visual which has direct access to
    runtime state; this wrapper exists for symmetry with other base
    skills if someone calls run_skill directly."""
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("verify_holding_visual requires an active RoboTwinEnv")
    if arm not in {"left", "right"}:
        raise ValueError(f"arm must be 'left'|'right', got {arm!r}")
    raise RuntimeError(
        "verify_holding_visual must be invoked from inside the rollout/codeact "
        "tool loop — direct run_skill invocation is not supported."
    )
