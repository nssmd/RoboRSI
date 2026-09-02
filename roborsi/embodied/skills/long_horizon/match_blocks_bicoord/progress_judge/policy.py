"""long_horizon.match_blocks_bicoord.progress_judge — VLM count judge."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


_SYSTEM = (
    "You are a vision judge. Read the image and count colored blocks placed "
    "next to the matching-color sign. A block is 'matched' iff it sits on the "
    "table within ~10cm of the sign of the same color. "
    "Reply with strict JSON on the last line, no markdown:\n"
    '{"blocks_matched": <int>, "blocks_on_left": <int>, "reason": "<short>"}'
)


def run(
    scene_image: str | None = None,
    expected_total: int = 3,
    timeout_s: int = 90,
    **_: Any,
) -> dict[str, Any]:
    if not scene_image or not Path(scene_image).exists():
        return {"done": False, "score": 0.0, "reason": "no scene image"}
    prompt = (
        f"Image (use Read tool): {scene_image}\n\n"
        f"There should be {expected_total} colored blocks total. Count how many "
        "are correctly placed next to (within ~10cm of) the sign of the same "
        "color, and how many are still on the LEFT side of the table (not yet moved). "
        "A block held in a gripper counts as 'on left' (not yet placed). "
        "Respond with the JSON only."
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
    matched = int(counts.get("blocks_matched") or 0)
    left = int(counts.get("blocks_on_left") or 0)
    score = min(1.0, matched / max(1, expected_total))
    done = matched >= expected_total and left == 0
    return {
        "skill": "match_blocks_bicoord.progress_judge",
        "done": bool(done),
        "score": float(score),
        "blocks_matched": matched,
        "blocks_on_left": left,
        "reason": counts.get("reason") or "",
        "raw_tail": (proc.stdout or "")[-1000:],
    }


_JSON_OBJ = re.compile(r"\{[^{}]*\"blocks_matched\"[^{}]*\}")


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
