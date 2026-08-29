"""Code-backed one-object LIBERO pick and place from current vision."""

from __future__ import annotations

import re
from typing import Any


def _tool(state: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    from roborsi.embodied.agent_loop.rollout import _dispatch_tool

    result, _ = _dispatch_tool(state, name, args)
    return result


def _point(state: Any, description: str, explicit: Any) -> dict[str, Any]:
    if isinstance(explicit, (list, tuple)) and len(explicit) == 2:
        try:
            return {"ok": True, "u": int(explicit[0]), "v": int(explicit[1])}
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "reason": "pixel must contain two integers"}
    return _tool(state, "find_by_pointing", {"object": description})


def _visible_target_query(target: str, placement: str) -> str:
    raw = str(target or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)+_[0-9]+", raw):
        return str(target or "").strip()
    words = raw.split("_")[:-1]
    preferred = (
        {"plate", "stove", "pad", "stand", "scale", "rack", "table"}
        if placement == "surface"
        else {"basket", "bowl", "bin", "drawer", "cabinet", "container"}
    )
    noun = next((word for word in reversed(words) if word in preferred), None)
    return noun or " ".join(words)


def dispatch_runtime(state: Any, args: dict[str, Any]):
    source = str(args.get("source") or "").strip()
    target = str(args.get("target") or "").strip()
    placement = str(args.get("placement") or "").strip().lower()
    target_query = _visible_target_query(target, placement)
    trace: list[dict[str, Any]] = []

    def finish(
        ok: bool,
        reason: str,
        *,
        phase: str | None = None,
        **extra: Any,
    ):
        result = {
            "ok": ok,
            "source": source,
            "target": target,
            "placement": placement,
            "reason": reason,
            "trace": trace,
            **extra,
        }
        if phase is not None:
            result["failed_phase"] = phase
        return result, state.env.take_snapshot()

    if not source or not target or placement != "surface":
        return finish(
            False,
            "source, target, and placement=surface are required; "
            "container mode is not validated",
            phase="arguments",
        )

    _tool(state, "look", {"camera": "head"})
    source_point = _point(state, source, args.get("source_pixel"))
    trace.append({"phase": "locate_source", **source_point})
    if not source_point.get("ok"):
        return finish(False, "source localization failed", phase="locate_source")

    grasp_args: dict[str, Any] = {
        "object": source,
        "pixel": [int(source_point["u"]), int(source_point["v"])],
    }
    for key in ("hover", "grasp_z_offset"):
        if key in args:
            grasp_args[key] = args[key]
    grasp = _tool(state, "grasp_object", grasp_args)
    trace.append(
        {
            "phase": "grasp",
            "ok": grasp.get("ok"),
            "grasped": grasp.get("grasped"),
            "holding": grasp.get("holding"),
            "visual_verified": grasp.get("visual_verified"),
            "identity_verified": grasp.get("identity_verified"),
            "do_not_regrasp": grasp.get("do_not_regrasp"),
            "reason": grasp.get("reason"),
        }
    )
    if not (
        grasp.get("ok")
        and grasp.get("grasped")
        and grasp.get("holding")
        and grasp.get("visual_verified") is True
    ):
        return finish(
            False,
            f"grasp failed: {grasp.get('reason')}",
            phase="grasp",
            holding=bool(grasp.get("holding")),
        )
    if grasp.get("identity_verified") is not True:
        return finish(
            False,
            "physical hold is real but source identity is not verified",
            phase="grasp_identity",
            holding=True,
            do_not_regrasp=True,
        )

    target_point = _point(state, target_query, args.get("target_pixel"))
    trace.append(
        {
            "phase": "locate_target",
            "requested_target": target,
            "target_query": target_query,
            **target_point,
        }
    )
    if not target_point.get("ok"):
        return finish(
            False,
            "target localization failed while preserving the hold",
            phase="locate_target",
            holding=True,
        )

    target_pixel = [int(target_point["u"]), int(target_point["v"])]
    place_args: dict[str, Any] = {
        "target": target_query,
        "pixel": target_pixel,
    }
    for key in ("release_clearance", "place_hover", "pos_tol"):
        if key in args:
            mapped = "hover" if key == "place_hover" else key
            place_args[mapped] = args[key]
    placed = _tool(state, "place_on_surface", place_args)
    trace.append(
        {
            "phase": "place",
            "tool": "place_on_surface",
            "ok": placed.get("ok"),
            "released": placed.get("released"),
            "gripper_opened": placed.get("gripper_opened"),
            "reason": placed.get("reason"),
        }
    )
    if not (placed.get("ok") and placed.get("released")):
        return finish(
            False,
            f"placement failed: {placed.get('reason')}",
            phase="place",
            grasped=True,
            holding=True,
        )

    _tool(state, "look", {"camera": "head"})
    return finish(
        True,
        "object released at the requested destination; awaiting simulator verdict",
        grasped=True,
        placed=True,
        released=True,
    )


def run(env: Any, **_: Any):
    raise RuntimeError("visual_pick_place is a compound tool for VLM dispatch")
