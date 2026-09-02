"""Shared harness-gate helper: invoke scripts/test_base_skill.py for a
single skill via --from-frontmatter and parse the verdict.

Used by:
  - scripts/apply_selfevo_proposal.py (CLI apply path)
  - roborsi/channels/agent/feishu/feishu_review.py (Feishu /approve)

Single source of truth for "what counts as a passing harness".

Verdict policy (2026-06-24): an UPDATE to an existing skill passes if it either
  (a) clears the absolute bar (pass_count >= min_required), OR
  (b) does NOT regress the last-blessed baseline — same-or-more holds AND
      same-or-fewer crashes.
(b) exists because the grasp_holds_actor gate measures SUCCESS-PATH quality and
is structurally blind to a crash-path defensive fix: a fix that turns a hard
crash into a clean ok=False leaves pass_count unchanged (often 0/5 on a hard
actor) yet drops crash_count from N to 0. The absolute bar alone would block
that safe, strictly-better change forever. The baseline lives in a JSON (not the
working tree) so it never races the campaign's concurrent commits.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
# allow either layout (scripts/ or roborsi/)
if (_REPO / "scripts/test_base_skill.py").exists():
    _HARNESS = _REPO / "scripts/test_base_skill.py"
else:
    _HARNESS = Path(__file__).resolve().parent / "test_base_skill.py"

_BASELINES = Path.home() / ".roborsi" / "gate_baselines.json"


@dataclasses.dataclass
class GateResult:
    skill: str
    verdict: str          # PASS | FAIL | SKIP | MALFORMED | ERROR
    pass_count: int | None
    total: int | None
    reason: str
    stdout_tail: str
    stderr_tail: str
    crash_count: int | None = None

    @property
    def is_blocking(self) -> bool:
        """Return True iff this verdict should HALT an apply.

        SKIP for a base/robotwin skill means the skill has no harness:
        block — that's a governance failure (skill not validated), not a
        success. The operator must explicitly --skip-harness to override."""
        return self.verdict not in ("PASS",)


def _load_baselines() -> dict:
    try:
        return json.loads(_BASELINES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_baseline(skill: str, pass_count: int, crash_count: int,
                    total: int | None) -> None:
    """Persist the last-blessed gate result for `skill` so future updates can be
    judged as no-regression. Public so an operator can SEED a baseline for a
    skill that was break-applied below the absolute bar."""
    data = _load_baselines()
    data[skill] = {"pass_count": pass_count, "crash_count": crash_count,
                   "total": total}
    _BASELINES.parent.mkdir(parents=True, exist_ok=True)
    _BASELINES.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def _invoke_harness(skill_name: str, timeout_s: int) -> tuple[dict, str, str]:
    """Run the harness subprocess for one skill; return (parsed_json, out, err)."""
    cmd = ["python3", str(_HARNESS), skill_name, "--from-frontmatter"]
    _bicoord = os.environ.get("ROBORSI_BICOORD_ROOT")
    if not _bicoord or not Path(_bicoord).is_dir():
        return (
            {"verdict": "ERROR", "reason": "set ROBORSI_BICOORD_ROOT to a valid checkout"},
            "",
            "ROBORSI_BICOORD_ROOT is unset or invalid",
        )
    env = {**os.environ,
            "ROBORSI_BICOORD_ROOT": _bicoord,
            # Cluttered-scene harnesses load assets via a path RELATIVE to the
            # BiCoord-Bench root (envs/utils/rand_create_cluttered_actor.py opens
            # "./assets/objects/objaverse/list.json"), so the harness must RUN
            # from there or it dies with FileNotFoundError → verdict=ERROR and
            # every base-skill apply is wrongly blocked. Keep _REPO on PYTHONPATH
            # so `import roborsi` still resolves from that cwd.
            "PYTHONPATH": str(_REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    res = subprocess.run(cmd, capture_output=True, text=True,
                            timeout=timeout_s, cwd=_bicoord, env=env,
                            encoding="utf-8", errors="replace")
    out = (res.stdout or "").strip()
    parsed: dict = {}
    if out:
        first = out.find("{")
        if first >= 0:
            try:
                parsed = json.loads(out[first:])
            except json.JSONDecodeError:
                parsed = {"verdict": "ERROR",
                           "reason": "could not parse harness stdout JSON"}
    return parsed, out, (res.stderr or "")


def run_gate_for(skill_name: str, timeout_s: int = 600) -> GateResult:
    parsed, out, err = _invoke_harness(skill_name, timeout_s)
    verdict = parsed.get("verdict", "ERROR")
    pc = parsed.get("pass_count")
    cc = parsed.get("crash_count")
    total = parsed.get("total")
    reason = parsed.get("reason") or err[-300:].strip()

    def _result(v: str, why: str) -> GateResult:
        return GateResult(skill=skill_name, verdict=v, pass_count=pc,
                          total=total, reason=why, stdout_tail=out[-500:],
                          stderr_tail=err[-300:], crash_count=cc)

    # Absolute-bar PASS — record the blessed result and return.
    if verdict == "PASS":
        if pc is not None:
            record_baseline(skill_name, pc, cc or 0, total)
        return _result("PASS", reason)
    # SKIP / MALFORMED / ERROR — not gradeable; pass through unchanged.
    if verdict != "FAIL":
        return _result(verdict, reason)

    # FAIL on the absolute bar — bless ONLY if it does not regress the last
    # blessed baseline: same-or-more holds AND same-or-fewer crashes. This lets
    # a crash→graceful-fail fix (pass unchanged, crashes N→0) through while still
    # blocking a genuine quality/safety regression.
    base = _load_baselines().get(skill_name)
    if base and pc is not None and cc is not None:
        bl_pass = int(base.get("pass_count", 0))
        bl_crash = int(base.get("crash_count", 0))
        if pc >= bl_pass and cc <= bl_crash:
            record_baseline(skill_name, pc, cc, total)
            return _result("PASS",
                f"no-regression vs baseline: holds {pc}>={bl_pass}, "
                f"crashes {cc}<={bl_crash} (absolute bar {parsed.get('min_required')} not met "
                f"but the change is no worse + no new crashes)")
    return _result("FAIL", reason)
