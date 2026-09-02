"""atomic.pick_and_place_at_pixel.judge — VLM judge per pick-and-place attempt."""

from __future__ import annotations

from typing import Any

from roborsi.embodied.skills import run as run_skill


def run(
    source_object: str = "",
    target_zone: str = "",
    pre_image: str | None = None,
    post_image: str | None = None,
    context: str = "",
    **_: Any,
) -> dict[str, Any]:
    if not source_object or not target_zone:
        return {"success": False, "reason": "missing source_object or target_zone"}
    if not post_image:
        return {"success": False, "reason": "no post_image to judge"}

    criterion = (
        f"The {source_object} has been moved onto / into the {target_zone}, "
        f"the gripper is no longer holding it (released), and the {source_object} "
        f"is clearly resting on/in the {target_zone} (not on the floor or somewhere else)."
    )
    images = [p for p in [pre_image, post_image] if p]
    judged = run_skill(
        "vlm_judge_claude",
        criterion=criterion,
        images=images,
        context=f"atomic=pick_and_place_at_pixel; source={source_object}; target={target_zone}; {context}".strip("; "),
    )
    return {
        "skill": "atomic.pick_and_place_at_pixel.judge",
        "source_object": source_object,
        "target_zone": target_zone,
        "success": bool(judged.get("success")),
        "reason": judged.get("reason"),
        "raw": judged.get("raw", ""),
    }
