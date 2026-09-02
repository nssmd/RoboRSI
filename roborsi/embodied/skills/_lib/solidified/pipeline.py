"""Solidified pick-and-place primitives shared by atomic compound policies.

A *compound policy* lives at ``atomic/<task>/<name>/policy.py`` and codifies a
task's PROVEN winning recipe as a single Engineer-callable tool — so the
Engineer issues one tool call instead of hand-driving the ~16 base-skill steps
the rollout loop needs otherwise. These primitives are the reusable middle
layer: each wraps the base skills (perceive / grasp / place / verify) that recur
across every solved pick-place task (place_a2b_left, move_can_pot,
place_mouse_pad …), adding code-level gating, a fallback ladder, and hard caps.

They compose exclusively through ``rollout._dispatch_tool`` — the very seam the
rollout loop dispatches through — so they stay backend-agnostic and never touch
sim internals. Import is function-local (matching the base-skill policies) so a
compound's ``policy.py`` can be ``exec_module``'d by the tool-spec builder
without dragging sim in at discovery time.

Contract: every primitive takes the live ``DispatchContext`` ``state`` and
returns a plain ``dict`` starting with ``ok``. They deliberately do NOT return
an ``Observation`` — the compound's ``dispatch_runtime`` takes the ONE final
snapshot the rollout loop consumes.
"""

from __future__ import annotations

from typing import Any


def _tool(state: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call one base skill by name and return only its result dict (the
    Observation is dropped — compounds snapshot once at the end)."""
    from roborsi.embodied.agent_loop.rollout import _dispatch_tool
    result, _obs = _dispatch_tool(state, name, args)
    return result


def look(state: Any, camera: str = "head_camera") -> dict[str, Any]:
    """Refresh the head camera — perception skills need a current frame."""
    return _tool(state, "look", {"camera": camera})


def perceive(state: Any, obj: str, *, location: str | None = None) -> dict[str, Any]:
    """``find_pixel`` → ``unproject_pixel`` for ``obj``.

    Returns ``{ok, u, v, xyz, confidence, object}``. ``ok`` is False (with a
    reason) if the object cannot be located; ``xyz`` may be None if the pixel
    unprojects onto no depth."""
    args: dict[str, Any] = {"object": obj}
    if location:
        args["location"] = location
    px = _tool(state, "find_pixel", args)
    if not px.get("ok"):
        return {"ok": False, "object": obj,
                "reason": f"find_pixel failed for {obj!r}: {px.get('reason')}"}
    u, v = int(px["u"]), int(px["v"])
    unp = _tool(state, "unproject_pixel", {"u": u, "v": v})
    return {"ok": True, "object": obj, "u": u, "v": v,
            "xyz": unp.get("xyz") if unp.get("ok") else None,
            "confidence": px.get("confidence")}


def is_holding(state: Any, arm: str) -> bool:
    """Authoritative proprioceptive grip check (reads the finger joints)."""
    return bool(_tool(state, "is_holding", {"arm": arm}).get("holding"))


# Grasp skills tried in order; each is verified proprioceptively after. The
# first is the general engine; the two specialised strategies cover the flat
# (top_down) and tall/cylindrical (diverse) failure modes when it whiffs.
_GRASP_LADDER = ("grasp_object", "grasp_top_down", "grasp_diverse")


def grasp_with_fallback(state: Any, arm: str, obj: str,
                        u: int, v: int) -> dict[str, Any]:
    """Grasp ``obj`` at pixel ``(u, v)``, climbing the strategy ladder until the
    grip is confirmed held. ``(u, v)`` disambiguates which instance when the
    name grounds to several regions (the approved place_a2b lead)."""
    attempts: list[dict[str, Any]] = []
    for skill in _GRASP_LADDER:
        r = _tool(state, skill, {"arm": arm, "object": obj, "u": u, "v": v})
        held = is_holding(state, arm)
        attempts.append({"skill": skill, "ok": r.get("ok"), "held": held,
                         "reason": r.get("reason")})
        if r.get("ok") and held:
            return {"ok": True, "held": True, "via": skill, "attempts": attempts}
    return {"ok": False, "held": False, "via": None, "attempts": attempts,
            "reason": f"all grasp strategies failed for {obj!r} with {arm} arm"}


def place(state: Any, arm: str, *, held_object: str, target: str,
          mode: str = "beside", offset_m: float = 0.08,
          drop_height_m: float = 0.03) -> dict[str, Any]:
    """Set the held object down relative to ``target``.

    ``mode='beside'`` → ``place_beside`` (on the arm's side of ``target``);
    ``mode='in'`` → ``place_object_in`` (into a container). Release is confirmed
    proprioceptively (``is_holding`` must go False)."""
    if mode == "in":
        r = _tool(state, "place_object_in",
                  {"arm": arm, "target": target, "drop_height_m": drop_height_m})
    else:
        r = _tool(state, "place_beside",
                  {"arm": arm, "target": target, "held_object": held_object,
                   "offset_m": offset_m, "drop_height_m": drop_height_m})
    released = not is_holding(state, arm)
    return {"ok": bool(r.get("ok")) and released, "released": released,
            "mode": mode, "reason": r.get("reason"), "detail": r}


def _unproject(state: Any, u: int, v: int, name: str) -> dict[str, Any]:
    unp = _tool(state, "unproject_pixel", {"u": u, "v": v})
    return {"ok": True, "object": name, "u": u, "v": v,
            "xyz": unp.get("xyz") if unp.get("ok") else None}


def coincide(a: dict[str, Any], b: dict[str, Any], *, min_sep_px: int = 20) -> bool:
    """True if two perceive() hits ground to essentially the same pixel — the
    signature of a generic-label collision (both 'object A'/'object B' landing on
    one block) that the disambiguate step exists to break."""
    if not (a.get("ok") and b.get("ok")):
        return False
    return (abs(a["u"] - b["u"]) + abs(a["v"] - b["v"])) < min_sep_px


def disambiguate_two(state: Any, name_a: str, name_b: str) -> tuple[dict, dict]:
    """Manager-approved multi-same-object guard (place_a2b / pick_dual / blocks):
    generic labels can ground to the SAME region. Take ``detect_object``'s two
    most separated centroids, assign left→A / right→B by x, unproject each.
    Returns two perceive-shaped dicts (``ok=False`` if <2 objects found)."""
    det = _tool(state, "detect_object", {"object": "object on the table", "top_k": 6})
    cents = [d.get("centroid") for d in (det.get("detections") or []) if d.get("centroid")]
    if len(cents) < 2:
        fail = {"ok": False, "reason": "detect_object found <2 distinct objects"}
        return fail, dict(fail)
    cents.sort(key=lambda c: c[0])   # by x (pixel column)
    left, right = cents[0], cents[-1]
    return (_unproject(state, int(left[0]), int(left[1]), name_a),
            _unproject(state, int(right[0]), int(right[1]), name_b))
