"""_lib.judging.vlm_judge_claude — spawn a Claude CLI subprocess as VLM judge.

Replaces sim-privileged `check_success` with image-grounded VLM judgement that
transfers to real hardware. Caller hands us a success criterion + image paths;
we run `claude -p --bare --output-format json` with a prompt that instructs the
subprocess to Read each image and reply ONLY with strict JSON.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


_SYSTEM_PROMPT = (
    "You are a vision-grounded success judge for a robot manipulation task. "
    "You receive a success criterion and one or more image file paths. "
    "Use the Read tool to view each image, then decide whether the criterion is met "
    "based ONLY on what is visible. Reply with a single JSON object on the LAST line "
    "of your response, no markdown fences, exactly the schema:\n"
    '{"success": <bool>, "reason": "<one short sentence>"}'
)


def run(
    criterion: str,
    images: list[str] | None = None,
    context: str = "",
    model: str = "",
    timeout_s: int = 90,
    **_: Any,
) -> dict[str, Any]:
    if not criterion:
        raise ValueError("vlm_judge_claude requires 'criterion'")
    images = images or []
    valid_imgs = [str(Path(p)) for p in images if p and Path(p).exists()]
    if not valid_imgs:
        return {"success": False, "reason": "no images provided to judge", "raw": ""}

    user_prompt = _build_user_prompt(criterion, valid_imgs, context)
    image_dirs = sorted({str(Path(p).parent) for p in valid_imgs})
    cmd = [
        "claude", "-p", user_prompt,
        "--bare",
        "--output-format", "json",
        "--append-system-prompt", _SYSTEM_PROMPT,
        "--allow-dangerously-skip-permissions",
    ]
    for d in image_dirs:
        cmd += ["--add-dir", d]
    if model:
        cmd += ["--model", model]

    # NOTE: explicit encoding="utf-8" + errors="replace" is REQUIRED.
    # claude CLI emits UTF-8 (em-dashes, arrows, etc.); without this Python
    # falls back to locale (ASCII in headless containers) and raises
    # UnicodeDecodeError on byte 0xe2 etc., crashing the entire judge and
    # marking the atomic failed even when it succeeded.
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
        cwd=image_dirs[0],
    )
    raw = (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
    verdict = _parse_verdict(proc.stdout)
    verdict["raw"] = raw[-2000:]
    verdict["exit_code"] = proc.returncode
    return verdict


def _build_user_prompt(criterion: str, images: list[str], context: str) -> str:
    bullets = "\n".join(f"- {p}" for p in images)
    ctx_block = f"\n\nExtra context: {context.strip()}" if context.strip() else ""
    return (
        f"Success criterion: {criterion.strip()}\n\n"
        f"Image files to inspect (use Read on each):\n{bullets}{ctx_block}\n\n"
        "Reply with a single JSON object on the last line: "
        '{"success": <bool>, "reason": "<one short sentence>"}.'
    )


def _parse_verdict(stdout: str) -> dict[str, Any]:
    if not stdout:
        return {"success": False, "reason": "empty subprocess stdout"}
    payload = _peel_outer_json(stdout)
    inner_text = ""
    if isinstance(payload, dict):
        inner_text = payload.get("result") or payload.get("response") or payload.get("text") or ""
    if not inner_text:
        inner_text = stdout
    obj = _last_json_object(inner_text)
    if obj is None:
        return {"success": False, "reason": f"no JSON in judge output: {inner_text[:200]}"}
    success = bool(obj.get("success"))
    reason = str(obj.get("reason") or "").strip() or "(no reason)"
    return {"success": success, "reason": reason}


def _peel_outer_json(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


_JSON_OBJ = re.compile(r"\{[^{}]*\"success\"[^{}]*\}")


def _last_json_object(text: str) -> dict[str, Any] | None:
    matches = _JSON_OBJ.findall(text)
    for cand in reversed(matches):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None
