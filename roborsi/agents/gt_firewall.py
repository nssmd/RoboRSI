"""The ground-truth firewall: one definition of what a blind role may read.

The Planner/Engineer/Reviewer are camera-only. Commit ededc01 enforced that in
the *code* — deleting privileged tools, stubbing the predicate reader — but the
task wikis kept accumulating the sim's success criterion for another ten days,
written by a Manager that could `cat` the env source and believed a label
("DIAGNOSIS ONLY", "不喂 GT 位姿") made it safe. Fourteen solved tasks turned out
to have been solved while knowing the criterion.

Two lessons are encoded here:

1. **Guard the read, not the write.** Any number of actors can append to a wiki;
   only `read_wiki` feeds it to a role. Filtering at the choke point holds even
   when an upstream writer is careless, which is the case that actually happened.

2. **A criterion is a spec, not an observation.** No camera measures "success
   requires z > 0.82" — that is a rule, read out of the env. Object state is
   different: a robot that lifts a bottle and watches its mask rise has MEASURED
   something and may write it down. This module drops the former and keeps the
   latter, so genuine experience still accumulates.

The vocabulary ban is deliberately coarse. A scrub of `check_success` alone was
tried first and the leaks reappeared as "seed-7 ground truth: shoe spawns at
random yaw" — same information, no shared token. Innocent phrasings get caught
too; rewriting one costs seconds, and a missed leak costs the experiment.
"""

from __future__ import annotations

import os
import re

# Sim-only accessors, plus the words used to restate what they return.
GT_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"check_success", r"check_task_success", r"get_functional_point",
        r"check_actors_contact", r"get_contact_point",
        r"get_gripper_actor_contact_position", r"stage_success_tag",
        r"is_left_gripper_open\(\)", r"is_right_gripper_open\(\)",
        r"get_actor_pose", r"start_height", r"object_start_height", r"table_z_bias",
        r"\bGT\b", r"ground[- ]truth", r"真值", r"by construction",
        r"envs/[a-z_]+\.py",
    ]
]


def predicate_source(task: str) -> str | None:
    """The task's own check_success body, for fingerprinting. Manager-side only."""
    root = os.environ.get("ROBORSI_ROBOTWIN_ROOT")
    if not root:
        return None
    path = os.path.join(root, "envs", f"{task}.py")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="ignore") as fh:
        src = fh.read()
    m = re.search(r"def check_success\(self.*?(?=\n    def |\nclass |\Z)", src, re.S)
    return m.group(0) if m else None


def fingerprints(pred: str) -> set[str]:
    """Values a blind planner could not have guessed: 3+ decimals and asset ids.

    Two-decimal tolerances (0.02, 0.15) are excluded on purpose — they collide
    with numbers an agent picks by itself, and a filter that eats innocent text
    gets disabled.
    """
    nums = set(re.findall(r"\b\d\.\d{3,}\b", pred))
    assets = set(re.findall(r"[\"']([0-9]{3}_[a-z_]+)[\"']", pred))
    return nums | assets


def _entries(md: str) -> list[list[str]]:
    """Split markdown into bullet entries: a '- ' line plus its continuation."""
    blocks: list[list[str]] = []
    for line in md.splitlines(keepends=True):
        if line.startswith("- ") or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return blocks


def leaks(text: str, marks: set[str]) -> str | None:
    """The first privileged token in `text`, or None."""
    for m in marks:
        if m in text:
            return m
    for pat in GT_PATTERNS:
        found = pat.search(text)
        if found:
            return found.group(0)
    return None


def redact(task: str, md: str) -> tuple[str, list[str]]:
    """Drop every entry carrying privileged content. Returns (clean_md, dropped).

    Whole entries go, not the offending clause: a paragraph written by someone
    who had read the env is downstream of it throughout, and no clause-level
    edit separates the safe sentences reliably.
    """
    pred = predicate_source(task)
    marks = fingerprints(pred) if pred else set()

    kept, dropped = [], []
    for block in _entries(md):
        text = "".join(block)
        hit = leaks(text, marks)
        if hit is None:
            kept.append(text)
        else:
            dropped.append(hit)
    return "".join(kept), dropped
