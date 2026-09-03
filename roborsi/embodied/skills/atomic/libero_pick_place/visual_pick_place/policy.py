"""Code-backed one-object LIBERO pick and place from current vision."""

from __future__ import annotations

import re
from typing import Any

from roborsi.embodied.agent_loop.env import Observation


def _tool(
    state: Any,
    name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], Observation]:
    from roborsi.embodied.agent_loop.rollout import _dispatch_tool

    return _dispatch_tool(state, name, args)


def _explicit_point(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return {"ok": True, "u": int(value[0]), "v": int(value[1])}
    except (TypeError, ValueError, OverflowError):
        return {"ok": False, "reason": "pixel must contain two integers"}


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
    observation = Observation()

    def call(name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        nonlocal observation
        result, observation = _tool(state, name, tool_args)
        return result

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
        return result, observation

    if not source or not target or placement not in {"surface", "container"}:
        return finish(
            False,
            "source, target, and placement=surface|container are required",
            phase="arguments",
        )

    call("look", {"camera": "head"})
    source_point = _explicit_point(args.get("source_pixel"))
    if source_point is None:
        source_point = call("find_by_pointing", {"object": source})
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
    grasp = call("grasp_object", grasp_args)
    trace.append({
        "phase": "grasp",
        "ok": grasp.get("ok"),
        "grasped": grasp.get("grasped"),
        "holding": grasp.get("holding"),
        "visual_verified": grasp.get("visual_verified"),
        "identity_verified": grasp.get("identity_verified"),
        "do_not_regrasp": grasp.get("do_not_regrasp"),
        "reason": grasp.get("reason"),
    })
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

    target_point = _explicit_point(args.get("target_pixel"))
    if target_point is None:
        target_point = call("find_by_pointing", {"object": target_query})
    trace.append({
        "phase": "locate_target",
        "requested_target": target,
        "target_query": target_query,
        **target_point,
    })
    if not target_point.get("ok"):
        return finish(
            False,
            "target localization failed while preserving the hold",
            phase="locate_target",
            holding=True,
        )

    target_pixel = [int(target_point["u"]), int(target_point["v"])]
    if placement == "surface":
        place_tool = "place_on_surface"
        place_args: dict[str, Any] = {
            "target": target_query,
            "pixel": target_pixel,
        }
        for key in ("release_clearance", "place_hover", "pos_tol"):
            if key in args:
                place_args["hover" if key == "place_hover" else key] = args[key]
    else:
        place_tool = "place_object_in"
        place_args = {
            "object": target_query,
            "pixel": target_pixel,
        }
        for key in ("z_offset", "place_hover"):
            if key in args:
                place_args["hover" if key == "place_hover" else key] = args[key]
    placed = call(place_tool, place_args)
    trace.append({
        "phase": "place",
        "tool": place_tool,
        "ok": placed.get("ok"),
        "released": placed.get("released"),
        "gripper_opened": placed.get("gripper_opened"),
        "reason": placed.get("reason"),
    })
    if not (placed.get("ok") and placed.get("released")):
        return finish(
            False,
            f"placement failed: {placed.get('reason')}",
            phase="place",
            grasped=True,
            holding=True,
        )

    call("look", {"camera": "head"})
    return finish(
        True,
        "object released at the requested destination; awaiting simulator verdict",
        grasped=True,
        placed=True,
        released=True,
    )


def run(env: Any, **_: Any):
    raise RuntimeError("visual_pick_place is a compound tool for VLM dispatch")
