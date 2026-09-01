"""Code-backed placement of two objects into one LIBERO container."""

from __future__ import annotations

from typing import Any


def _tool(state: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    from roborsi.embodied.agent_loop.rollout import _dispatch_tool

    result, _ = _dispatch_tool(state, name, args)
    return result


def dispatch_runtime(state: Any, args: dict[str, Any]):
    objects = (
        str(args.get("object_a") or "").strip(),
        str(args.get("object_b") or "").strip(),
    )
    container = str(args.get("container") or "").strip()
    trace: list[dict[str, Any]] = []

    def finish(ok: bool, reason: str):
        return (
            {
                "ok": ok,
                "completed": sum(
                    row.get("phase") == "place" and row.get("released") is True
                    for row in trace
                ),
                "reason": reason,
                "trace": trace,
            },
            state.env.take_snapshot(),
        )

    if not all(objects) or not container:
        return finish(False, "object_a, object_b, and container are required")

    for object_name in objects:
        _tool(state, "look", {"camera": "head"})
        located = _tool(state, "find_by_pointing", {"object": object_name})
        trace.append({"phase": "locate", "object": object_name, **located})
        if not located.get("ok"):
            return finish(False, f"could not locate {object_name}")

        grasped = _tool(
            state,
            "grasp_object",
            {
                "object": object_name,
                "pixel": [int(located["u"]), int(located["v"])],
            },
        )
        trace.append({"phase": "grasp", "object": object_name, **grasped})
        if not grasped.get("grasped"):
            return finish(False, f"could not grasp {object_name}")

        placed = _tool(state, "place_object_in", {"object": container})
        trace.append({"phase": "place", "object": object_name, **placed})
        if not placed.get("released"):
            return finish(False, f"could not place {object_name}")

    return finish(True, f"placed both objects in {container}")
