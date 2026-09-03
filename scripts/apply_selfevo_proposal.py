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
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from roborsi.store import trace_db as _td


REPO = Path(__file__).resolve().parents[1]
QUEUE = Path.home() / ".roborsi" / "skill_review"
_SKILL_SEGMENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_BASE_NAMESPACES = {"libero", "robotwin"}


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


def _validated_segment(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _SKILL_SEGMENT.fullmatch(text):
        raise ValueError(f"unsafe {label}: {text!r}")
    return text


def _proposal_layout(data: dict) -> tuple[str, str, str]:
    """Return ``(kind, owner, namespace)`` for a proposal category."""
    category = str(data.get("category") or "base/robotwin").strip()
    parts = category.split("/")
    if len(parts) != 2:
        raise ValueError(f"unsupported proposal category: {category!r}")
    tier, owner = parts
    if tier == "base":
        if owner not in _BASE_NAMESPACES:
            raise ValueError(f"unsupported base namespace: {owner!r}")
        return tier, owner, owner
    if tier == "atomic":
        task = _validated_segment(owner, "atomic task")
        from roborsi.agents.atomic_backend import resolve
        from roborsi.embodied.agent_loop.config import _skill_namespace

        return tier, task, _skill_namespace(resolve(task).backend_name)
    raise ValueError(f"unsupported proposal category: {category!r}")


def _candidate_code(data: dict) -> str:
    if data.get("kind") == "new":
        return str(data.get("code") or data.get("new_code") or "")
    if data.get("kind") == "update":
        code = str(data.get("new_code") or "")
        return "" if code.lstrip().startswith("---") else code
    return ""


def _assert_candidate_safe(data: dict) -> None:
    code = _candidate_code(data)
    name = _validated_segment(data.get("name"), "skill name")
    _tier, _owner, namespace = _proposal_layout(data)
    from roborsi.agents.proposal_safety import (
        assert_safe_candidate,
        assert_safe_skill_text,
    )

    if code:
        assert_safe_candidate(
            code,
            namespace=namespace,
            candidate_name=name,
        )
    skill_text = str(data.get("skill_md") or "")
    if data.get("kind") == "update":
        update = str(data.get("new_code") or "")
        if update.lstrip().startswith("---"):
            skill_text = update
    if skill_text:
        assert_safe_skill_text(skill_text)


def _apply_new(data: dict) -> tuple[list[str], str]:
    name = _validated_segment(data.get("name"), "skill name")
    tier, owner, _namespace = _proposal_layout(data)
    category = f"{tier}/{owner}"
    # skill-first layout: base skills live at base/<name>/<embodiment>/.
    # category like "base/robotwin" or "base/libero" → base/<name>/<ns>.
    base = REPO / "roborsi" / "embodied" / "skills"
    if tier == "base":
        base = base / "base" / name / owner
    else:
        base = base / "atomic" / owner / name
    base.mkdir(parents=True, exist_ok=True)
    skill_md = data.get("skill_md") or ""
    code = data.get("code") or data.get("new_code") or ""
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
    from roborsi.embodied.skills import discover_compounds, get, get_ns

    name = _validated_segment(data.get("name"), "skill name")
    tier, owner, _namespace = _proposal_layout(data)
    if tier == "base":
        sk = get_ns(name, owner)
    else:
        sk = next(
            (item for item in discover_compounds(owner) if item.name == name),
            None,
        )
    if sk is None and not data.get("category"):
        sk = get(name)
    if sk is None:
        raise FileNotFoundError(f"skill {name!r} not registered in {tier}/{owner}")
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
    msg = (f"selfevo: update {name} (proposal {data['id']})\n\n"
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
        _td.update_proposal_status(pid, "rejected", applied_by="operator")
        print(f"[apply] {pid} rejected → {new_fp.relative_to(QUEUE)}")
        return 0

    _BACKUPS.clear()
    try:
        _assert_candidate_safe(data)
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
    base_skills: set[tuple[str, str]] = set()
    for f in files:
        m = re.match(
            r"roborsi/embodied/skills/base/([^/]+)/(robotwin|libero)/",
            f,
        )
        if m:
            base_skills.add((m.group(1), m.group(2)))
    if base_skills and not getattr(args, "skip_harness", False):
        from scripts_lib_harness_gate import run_gate_for
        for skill, namespace in base_skills:
            if namespace != "robotwin":
                print(
                    "[apply] HARNESS GATE BLOCKED — no automatic LIBERO "
                    "per-skill harness is configured. Run matched simulator "
                    "validation, then use --skip-harness for operator apply."
                )
                _restore_backups()
                return 3
            gr = run_gate_for(skill)
            print(f"[apply][harness {skill}] verdict={gr.verdict} "
                  f"({gr.pass_count}/{gr.total}) {gr.reason[:100]}")
            if gr.verdict not in ("PASS",):
                print(f"[apply] HARNESS GATE BLOCKED — not committing. "
                      f"Override with --skip-harness if you accept the risk.")
                # Restore the pre-apply working tree. Use the content backups
                # (git checkout is a silent no-op for UNTRACKED skill files).
                _restore_backups()
                return 3

    print(f"[apply] wrote files: {files}")
    for f in files:
        _git("add", f)
    cm = _git("commit", "--only", "-m", msg, "--", *files)
    if cm.returncode != 0:
        print(f"[apply] git commit failed: {cm.stderr[:200]}")
        return 1
    print(f"[apply] committed:\n{cm.stdout.strip()[:300]}")
    data["status"] = "applied"
    data["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["applied_by"] = "operator"
    archive = QUEUE / "applied"
    archive.mkdir(parents=True, exist_ok=True)
    new_fp = archive / fp.name
    new_fp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    fp.unlink()
    _td.update_proposal_status(pid, "applied", applied_by="operator",
                                  note=f"files: {files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
