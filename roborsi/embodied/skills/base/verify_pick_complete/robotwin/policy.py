"""base.robotwin.verify_pick_complete — combined done-gate for pick atomics.

Eliminates the dominant 'vlm_overclaim' failure mode where the VLM calls
done(success=True) without both proprioceptive and visual evidence. This
tool runs both checks in a single dispatch and returns a single ok bool.
"""

from __future__ import annotations

from typing import Any


def _geometric_holding(state, arm: str) -> tuple[bool, float | None]:
    """Holding from the gripper's real finger separation (proprioception).

    Reuses is_holding — the single source of truth — which reads the achieved
    finger joint qpos (get_gripper_val reads 0 even when holding) plus command
    intent. Returns (holding, finger_opening_rad).
    """
    from roborsi.embodied.sim.robotwin.robotwin_tools import _do_is_holding
    res, _ = _do_is_holding(state, {"arm": arm})
    return bool(res.get("holding")), res.get("finger_opening")


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.embodied.skills import run as run_skill

    arm = str(args.get("arm", "right")).lower()
    obj = str(args.get("object", "")).strip()
    min_conf = float(args.get("min_visual_confidence", 0.7))

    reasons: list[str] = []

    # 1. Geometric check.
    geom_ok, width = _geometric_holding(state, arm)
    if not geom_ok:
        reasons.append(f"geometric: fingers not wedged on an object (finger_opening={width})")

    # 2. Visual check via verify_holding_visual (best-effort).
    visual_ok: bool | None = None
    visual_conf: float | None = None
    try:
        vres = run_skill("verify_holding_visual", env=state.env, arm=arm, object=obj) or {}
        visual_ok = bool(vres.get("holding_visual"))
        visual_conf = vres.get("confidence")
        try:
            visual_conf = float(visual_conf) if visual_conf is not None else None
        except Exception:
            visual_conf = None
        if not visual_ok:
            reasons.append(f"visual: holding_visual=False ({vres.get('reason','')})")
        elif visual_conf is not None and visual_conf < min_conf:
            reasons.append(f"visual: confidence {visual_conf} < {min_conf}")
            visual_ok = False
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"visual: error {type(exc).__name__}: {exc}")
        visual_ok = None

    ok = bool(geom_ok and visual_ok is True)
    out = {
        "ok": ok,
        "geometric": geom_ok,
        "gripper_width": width,
        "visual": visual_ok,
        "visual_confidence": visual_conf,
        "reason": "; ".join(reasons) if reasons else "all gates passed",
        "note": ("Use ok=True as the ONLY precondition for done(success=True). "
                 "If ok=False, retry the grasp; do NOT overclaim done."),
    }
    return (out, _snapshot(state.env))


def run(env=None, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Call via rollout tool dispatch (dispatch_runtime).")
