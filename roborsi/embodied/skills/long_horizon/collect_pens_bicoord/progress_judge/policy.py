"""long_horizon.collect_pens_bicoord.progress_judge — VLM count judge."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


_SYSTEM = (
    "You are a vision judge. Read the provided image and count marker pens. "
    "Reply with strict JSON on the last line, no markdown:\n"
    '{"pens_in_cup": <int>, "pens_on_table": <int>, "reason": "<short>"}'
)


def run(
    scene_image: str | None = None,
    expected_total: int = 4,
    timeout_s: int = 90,
    **_: Any,
) -> dict[str, Any]:
    if not scene_image or not Path(scene_image).exists():
        return {"done": False, "score": 0.0, "reason": "no scene image"}
    prompt = (
        f"Image (use Read tool): {scene_image}\n\n"
        f"There should be {expected_total} marker pens total. Count how many are visibly inside "
        f"the cup (pen body or tip below the cup rim) and how many are still lying on the tabletop. "
        f"Pens held by a gripper count as 'on table' (not yet placed). Respond with the JSON only."
    )
    img_dir = str(Path(scene_image).parent)
    proc = subprocess.run(
        ["claude", "-p", prompt, "--bare", "--output-format", "json",
         "--append-system-prompt", _SYSTEM,
         "--allow-dangerously-skip-permissions",
         "--add-dir", img_dir],
        capture_output=True, text=True, timeout=timeout_s, check=False,
        cwd=img_dir,
    )
    counts = _parse_counts(proc.stdout)
    in_cup = int(counts.get("pens_in_cup") or 0)
    on_table = int(counts.get("pens_on_table") or 0)
    score = min(1.0, in_cup / max(1, expected_total))
    done = in_cup >= expected_total and on_table == 0
    return {
        "skill": "collect_pens_bicoord.progress_judge",
        "done": bool(done),
        "score": float(score),
        "pens_in_cup": in_cup,
        "pens_on_table": on_table,
        "reason": counts.get("reason") or "",
        "raw_tail": (proc.stdout or "")[-1000:],
    }


_JSON_OBJ = re.compile(r"\{[^{}]*\"pens_in_cup\"[^{}]*\}")


def _parse_counts(stdout: str) -> dict[str, Any]:
    if not stdout:
        return {}
    inner = stdout
    try:
        outer = json.loads(stdout.strip())
        if isinstance(outer, dict):
            inner = outer.get("result") or outer.get("text") or stdout
    except json.JSONDecodeError:
        pass
    matches = _JSON_OBJ.findall(inner)
    for cand in reversed(matches):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return {}
