#!/usr/bin/env python3
"""Freeze a task's proven recipe into code so the Engineer stops re-deriving it.

The Engineer is a per-step VLM loop: every tool call costs a model round-trip
(place_shoe: 36 calls for 48 tools). Most of that is spent rediscovering a
sequence the task has already solved several times. Measured over the wins,
repeated successes of one task share 56% of their action sequence on average,
and every task has a skeleton — usually the canonical
`localize -> grasp_obb -> descend_tcp_to_z -> gripper -> park_arm`.

So the sequence is worth freezing. The COORDINATES are not: a compound that
hardcodes where the bowl was on seed 3 does not generalise, and baking measured
poses into a skill is how privileged information gets laundered into code. This
generates a macro that replays the *shape* of the recipe and re-derives every
argument from perception at run time.

Skeleton extraction is deterministic (longest common subsequence over the
traces). Only the code-writing step calls a model, and its output goes through
the existing Manager gate + harness like any other proposal.

    python scripts/solidify_skill.py --list
    python scripts/solidify_skill.py --task place_a2b_right
"""

from __future__ import annotations

import argparse
import ast
import collections
import difflib
import glob
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOGS = Path(os.environ.get(
    "ROBORSI_LOG_DIR", Path.home() / ".roborsi" / "logs"
)).expanduser()
REVIEW = Path.home() / ".roborsi" / "skill_review"

MIN_WINS = 3
MIN_SIM = 0.60

OUTCOMES = "vlm_declared_done|vlm_overclaimed|predicate_passed_without_done|budget_exceeded"
VERDICT = re.compile(
    rf"^bot> (✓|✗) \*\*[a-z_0-9]+\*\* seed=\d+ · (?:{OUTCOMES})"
    rf"|^bot> (✓|✗) [a-z_0-9]+: (?:✓ success \((?:{OUTCOMES})|✗ (?:{OUTCOMES}))", re.M)
CALL = re.compile(r"step=(\d+) → ([a-z_]+)\((.*)$")
DISPATCH = re.compile(r"step=\d+ tool=([a-z_]+) dispatched")

# Perception chatter varies run to run and carries no ordering information; the
# skeleton lives in the actions.
NOISE = {"look", "view_frame", "zoom_in", "read_task_wiki", "recall_past_success",
         "exec_python", "get_arm_pose", "is_holding", "read_joint_state", "plan"}


def wins_for(task: str) -> list[dict]:
    """Sim-adjudicated wins for one task: tool sequence plus the call lines."""
    out = []
    for f in glob.glob(str(LOGS / "loop_3role*/shard*" / f"{task}_seed*.log")):
        txt = Path(f).read_text(errors="ignore")
        marks = [m.group(1) or m.group(2) for m in VERDICT.finditer(txt)]
        if not marks or marks[-1] != "✓":
            continue
        seq = [t for t in DISPATCH.findall(txt) if t not in NOISE]
        calls = [f"{m.group(2)}({m.group(3)[:110]}" for m in CALL.finditer(txt)
                 if m.group(2) not in NOISE]
        if seq:
            out.append({"file": os.path.basename(f), "seq": seq, "calls": calls})
    return out


def skeleton(seqs: list[list[str]]) -> list[str]:
    """Longest common subsequence across every winning run, pairwise-folded."""
    def lcs(a: list[str], b: list[str]) -> list[str]:
        sm = difflib.SequenceMatcher(None, a, b)
        return [x for blk in sm.get_matching_blocks() for x in a[blk.a:blk.a + blk.size]]
    cur = seqs[0]
    for s in seqs[1:]:
        cur = lcs(cur, s)
        if not cur:
            break
    return cur


def similarity(seqs: list[list[str]]) -> float:
    pairs = [difflib.SequenceMatcher(None, a, b).ratio()
             for i, a in enumerate(seqs) for b in seqs[i + 1:]]
    return sum(pairs) / len(pairs) if pairs else 0.0


def candidates() -> list[dict]:
    tasks = {os.path.basename(f).rsplit("_seed", 1)[0]
             for f in glob.glob(str(LOGS / "loop_3role*/shard*" / "*_seed*.log"))}
    out = []
    for t in sorted(tasks):
        w = wins_for(t)
        if len(w) < 2:
            continue
        seqs = [x["seq"] for x in w]
        sk = skeleton(seqs)
        out.append({"task": t, "wins": len(w), "sim": similarity(seqs),
                    "skeleton": sk,
                    "ready": len(w) >= MIN_WINS and similarity(seqs) >= MIN_SIM and len(sk) >= 3})
    return out


PROMPT = """You are writing ONE solidified compound policy for the RoboTwin task
`{task}`, so the Engineer can run its proven recipe in a single tool call
instead of {n_steps} separate VLM round-trips.

The task has been solved {n_wins} times under the simulator's own predicate.
Those runs agree on {sim:.0%} of their action sequence. This is their common
skeleton, in order:

    {skeleton}

Here are the actual calls from the winning runs, with their arguments:

{samples}

=== EXISTING base/robotwin SKILLS you must compose (do not reimplement) ===
{catalog}

=== HARD RULES ===
- Freeze the SEQUENCE, never the COORDINATES. Every pixel, pose and height must
  be re-derived from perception at run time. A hardcoded pose from a winning
  seed is not a skill — it fails the moment the object moves, and it smuggles a
  measurement from one episode into every future one.
- PURE VISION. No simulator reads: no env.*, no actor poses, no check_success,
  no contact points, no asset ids. Cameras, depth and the arm's own joint state
  only.
- Verify by measurement, not assertion: after the grasp, lift and re-check that
  the object came with it; return ok=False when it did not.
- Fail loudly and early. If perception cannot find the object, return a clear
  reason so the Engineer falls back to driving the tools itself — a compound
  that limps on is worse than one that declines.
- Under ~150 lines, at most 3 levels of indentation, no try/except that swallows.
- Signature: `def dispatch_runtime(state, args: dict) -> tuple[dict, object]`,
  and import only `_dispatch_tool` from
  `roborsi.embodied.agent_loop.rollout`.
- Call `_dispatch_tool` with literal public base-skill names. Never read,
  alias, return, or reflect on `state`; never use `state.env`, `_snapshot`,
  dynamic imports, files, processes, or networks.
- Return the Observation from the final `_dispatch_tool` call:
  `result, obs = _dispatch_tool(state, "look", {{...}}); return result, obs`.

Answer in exactly these delimited sections, nothing else — no JSON, source code
does not survive being escaped into a JSON string.

===NAME===
<snake_case compound name, e.g. pick_place>
===DESCRIPTION===
<one line for the tool catalog>
===CODE===
<full policy.py source, verbatim>
===SKILL_MD===
<full SKILL.md: YAML frontmatter with name/kind/category/domain/version/args/metadata plus a harness: block>
===RATIONALE===
<why this skeleton is the recipe, citing the winning runs>
"""


def catalog() -> str:
    base = REPO / "roborsi/embodied/skills/base"
    lines = []
    for p in sorted(base.glob("*/robotwin/SKILL.md")):
        m = re.search(r"^description:\s*(.+)$", p.read_text(errors="ignore")[:400], re.M)
        lines.append(f"  {p.parent.parent.name}: {(m.group(1) if m else '')[:88]}")
    return "\n".join(lines)


def parse_sections(text: str) -> dict:
    parts = re.split(r"^===([A-Z_]+)===\s*$", text, flags=re.M)
    return {parts[i].strip().lower(): parts[i + 1].strip()
            for i in range(1, len(parts) - 1, 2)}


def author(task: str, info: dict, wins: list[dict], model: str) -> dict:
    import anthropic

    samples = "\n\n".join(
        f"--- {w['file']}\n" + "\n".join("    " + c for c in w["calls"][:22])
        for w in wins[:3])
    prompt = PROMPT.format(
        task=task, n_steps=max(len(w["seq"]) for w in wins), n_wins=len(wins),
        sim=info["sim"], skeleton=" → ".join(info["skeleton"]),
        samples=samples, catalog=catalog())

    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        raise RuntimeError("set ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN")
    client_args = {"api_key": api_key, "max_retries": 8}
    if os.environ.get("ANTHROPIC_BASE_URL"):
        client_args["base_url"] = os.environ["ANTHROPIC_BASE_URL"]
    client = anthropic.Anthropic(**client_args)
    text = ""
    with client.messages.stream(model=model, max_tokens=32000,
                                messages=[{"role": "user", "content": prompt}]) as st:
        for chunk in st.text_stream:
            text += chunk
        resp = st.get_final_message()

    got = parse_sections(text)
    missing = {"name", "description", "code", "skill_md", "rationale"} - set(got)
    if missing:
        raise RuntimeError(f"author omitted {sorted(missing)} "
                           f"(stop={resp.stop_reason}, blocks={[b.type for b in resp.content]})")
    ast.parse(got["code"])
    return got


def file_proposal(task: str, info: dict, got: dict) -> Path:
    """Queue it for the Manager gate; nothing lands without harness approval."""
    REVIEW.mkdir(parents=True, exist_ok=True)
    pid = f"{int(time.time())}-new-compound-{task}-{got['name']}-{uuid.uuid4().hex[:6]}"
    payload = {
        "id": pid, "kind": "new", "name": got["name"],
        "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S"), "status": "pending",
        "submitted_by": f"solidifier[{task}]",
        "category": f"atomic/{task}",
        "target_path": f"roborsi/embodied/skills/atomic/{task}/{got['name']}/policy.py",
        "description": got["description"], "code": got["code"],
        "skill_md": got["skill_md"],
        "rationale": (f"[{info['wins']} 次判据认可成功, 序列相似度 {info['sim']:.0%}] "
                      + got["rationale"]),
    }
    p = REVIEW / f"{pid}.json"
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all-ready", action="store_true", help="solidify every eligible task")
    ap.add_argument("--model", default="claude-opus-5")
    args = ap.parse_args()

    cands = candidates()
    if args.list or not (args.task or args.all_ready):
        print(f"{'任务':24s} {'成功':>4s} {'相似度':>7s} {'可固化':>6s}  骨架")
        for c in sorted(cands, key=lambda x: -x["sim"]):
            print(f"{c['task']:24s} {c['wins']:4d} {c['sim']:7.0%} "
                  f"{'✓' if c['ready'] else '':>6s}  {'→'.join(c['skeleton'][:7])}")
        print(f"\n达标 (≥{MIN_WINS} 次成功且相似度 ≥{MIN_SIM:.0%}): "
              f"{sum(1 for c in cands if c['ready'])}")
        return 0

    todo = [c for c in cands if c["ready"]] if args.all_ready \
        else [c for c in cands if c["task"] == args.task]
    if not todo:
        print("没有达标的任务")
        return 1
    for c in todo:
        got = author(c["task"], c, wins_for(c["task"]), args.model)
        p = file_proposal(c["task"], c, got)
        print(f"{c['task']}: 已提交 {p.name} — {got['name']}")
    print("等待 Manager 闸门与 harness 裁决。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
