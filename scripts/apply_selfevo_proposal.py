#!/usr/bin/env python3
"""Apply ONE pending proposal from ~/.roborsi/skill_review/.

For kind=new (propose_new_skill): writes SKILL.md + policy.py under
    roborsi/embodied/skills/<category>/<name>/ , then `git add` + commit.

For kind=update (propose_skill_update): writes new_code to the discovered
    policy.py of the named skill, then `git add` + commit.

Marks the file's status to 'applied' and mirrors to sqlite proposals row.

Usage:
    python3 scripts/apply_selfevo_proposal.py <proposal_id>
    python3 scripts/apply_selfevo_proposal.py --reject <proposal_id>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roborsi.store import trace_db as _td


REPO = Path(__file__).resolve().parents[1]
QUEUE = Path.home() / ".roborsi" / "skill_review"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO,
                            capture_output=True, text=True, check=False)


def _find_file(proposal_id: str) -> Path | None:
    for fp in QUEUE.glob(f"{proposal_id}*.json"):
        return fp
    for fp in QUEUE.glob("*.json"):
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if d.get("id") == proposal_id:
            return fp
    return None


def _apply_new(data: dict) -> tuple[list[str], str]:
    name = data["name"]
    category = data.get("category") or "base"
    # skill-first layout: base skills live at base/<name>/<embodiment>/.
    # category like "base/robotwin" or "base/libero" → base/<name>/<ns>.
    _segs = category.split("/")
    base = REPO / "roborsi" / "embodied" / "skills"
    if len(_segs) >= 2 and _segs[0] == "base":
        base = base / "base" / name / _segs[1]
    else:
        for seg in _segs:
            base = base / seg
        base = base / name
    base.mkdir(parents=True, exist_ok=True)
    skill_md = data.get("skill_md") or ""
    code = data.get("code") or ""
    files: list[str] = []
    if skill_md:
        p = base / "SKILL.md"
        _backup(p)
        p.write_text(skill_md, encoding="utf-8")
        files.append(str(p.relative_to(REPO)))
    if code:
        p = base / "policy.py"
        _backup(p)
        p.write_text(code, encoding="utf-8")
        files.append(str(p.relative_to(REPO)))
    msg = (f"selfevo: add {category} skill `{name}` (proposal {data['id']})\n\n"
            f"{(data.get('rationale') or '')[:300]}")
    return files, msg


# Pre-write backups so a gate BLOCK can restore the working tree even when the
# skill files are UNTRACKED (in which case `git checkout -- <file>` is a silent
# no-op and the unvalidated code would otherwise be left on disk — which once
# left a gate-failed move_dual_arm revision live for the running daemons).
_BACKUPS: dict[str, str | None] = {}


def _backup(path) -> None:
    _BACKUPS[str(path)] = (path.read_text(encoding="utf-8")
                           if path.exists() else None)


def _restore_backups() -> None:
    for fp, original in _BACKUPS.items():
        p = Path(fp)
        if original is None:        # file was newly created → remove it
            if p.exists():
                p.unlink()
        else:                       # file was overwritten → put bytes back
            p.write_text(original, encoding="utf-8")


def _apply_update(data: dict) -> tuple[list[str], str]:
    # find the skill's directory
    from roborsi.embodied.skills import get as get_skill
    sk = get_skill(data["name"])
    if sk is None:
        raise FileNotFoundError(f"skill {data['name']!r} not registered")
    new_code = data.get("new_code") or ""
    if not new_code:
        raise ValueError("proposal has empty new_code")
    # Heuristic: if new_code starts with YAML frontmatter (---) it is a
    # SKILL.md replacement. Otherwise it targets policy.py.
    if new_code.lstrip().startswith("---"):
        target = sk.path                                # SKILL.md path
    else:
        target = sk.path.parent / "policy.py"
        if not target.exists():
            raise FileNotFoundError(f"{target} does not exist")
    _backup(target)
    target.write_text(new_code, encoding="utf-8")
    msg = (f"selfevo: update {data['name']} (proposal {data['id']})\n\n"
            f"{(data.get('rationale') or '')[:300]}")
    return [str(target.relative_to(REPO))], msg


def main() -> int:
    from roborsi.runtime_mode import EvolutionDisabledError, require_evolution
    try:
        require_evolution("applying or resolving a self-evolution proposal")
    except EvolutionDisabledError as exc:
        print(f"[apply] {exc}", file=sys.stderr)
        return 4
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("proposal_id")
    ap.add_argument("--reject", action="store_true",
                      help="Mark proposal rejected instead of applying.")
    ap.add_argument("--skip-harness", action="store_true",
                      help="(Operator override) Skip harness gate for "
                           "base/robotwin skill changes. Use only when you "
                           "have manually validated the change.")
    args = ap.parse_args()
    fp = _find_file(args.proposal_id)
    if fp is None:
        print(f"[apply] proposal {args.proposal_id!r} not found in queue",
              file=sys.stderr)
        return 2
    data = json.loads(fp.read_text(encoding="utf-8"))
    pid = data["id"]

    if args.reject:
        data["status"] = "rejected"
        archive = QUEUE / "rejected"
        archive.mkdir(parents=True, exist_ok=True)
        new_fp = archive / fp.name
        new_fp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        fp.unlink()
        _td.update_proposal_status(pid, "rejected", applied_by="claude")
        print(f"[apply] {pid} rejected → {new_fp.relative_to(QUEUE)}")
        return 0

    try:
        if data.get("kind") == "new":
            files, msg = _apply_new(data)
        elif data.get("kind") == "update":
            files, msg = _apply_update(data)
        else:
            print(f"[apply] unknown kind {data.get('kind')!r}", file=sys.stderr)
            return 2
    except Exception as e:  # noqa: BLE001
        print(f"[apply] FAIL: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # Harness gate: if any written file lives under base/robotwin/, run the
    # base-skill harness for that skill before committing. Honors
    # --skip-harness (operator override). Same gate as Feishu /approve.
    base_skill_names = set()
    for f in files:
        m = __import__("re").match(
            r"roborsi/embodied/skills/base/([^/]+)/(?:robotwin|libero)/", f)
        if m:
            base_skill_names.add(m.group(1))
    if base_skill_names and not getattr(args, "skip_harness", False):
        from scripts_lib_harness_gate import run_gate_for, GateResult
        for skill in base_skill_names:
            gr = run_gate_for(skill)
            print(f"[apply][harness {skill}] verdict={gr.verdict} "
                  f"({gr.pass_count}/{gr.total}) {gr.reason[:100]}")
            if gr.verdict not in ("PASS",):
                print(f"[apply] HARNESS GATE BLOCKED — not committing. "
                      f"Override with --skip-harness if you accept the risk.")
                # Restore the pre-apply working tree. Use the content backups
                # (git checkout is a silent no-op for UNTRACKED skill files).
                _restore_backups()
                _git("checkout", "--", *files)
                return 3

    print(f"[apply] wrote files: {files}")
    for f in files:
        _git("add", f)
    cm = _git("commit", "-m", msg)
    if cm.returncode != 0:
        print(f"[apply] git commit failed: {cm.stderr[:200]}")
        return 1
    print(f"[apply] committed:\n{cm.stdout.strip()[:300]}")
    data["status"] = "applied"
    data["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["applied_by"] = "claude"
    archive = QUEUE / "applied"
    archive.mkdir(parents=True, exist_ok=True)
    new_fp = archive / fp.name
    new_fp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    fp.unlink()
    _td.update_proposal_status(pid, "applied", applied_by="claude",
                                  note=f"files: {files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
