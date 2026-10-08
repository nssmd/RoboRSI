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
import re
import sys
import time
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
# allow either layout (scripts/ or roborsi/)
if (_REPO / "scripts/test_base_skill.py").exists():
    _HARNESS = _REPO / "scripts/test_base_skill.py"
else:
    _HARNESS = Path(__file__).resolve().parent / "test_base_skill.py"

_BASELINES = __import__("roborsi.embodied.paths", fromlist=["home"]).home() / "gate_baselines.json"


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


def _parse_report(out: str, skill_name: str) -> dict:
    """Accept exactly one complete final report for the requested skill."""
    reports = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^[ \t]*\{", out):
        start = out.index("{", match.start())
        try:
            value, end = decoder.raw_decode(out, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "verdict" in value and "skill" in value:
            reports.append((value, end))
    if len(reports) != 1:
        return {"verdict": "ERROR", "reason": "missing or ambiguous final harness report"}
    value, end = reports[0]
    if value["skill"] != skill_name or out[end:].strip():
        return {"verdict": "ERROR", "reason": "harness report identity or final boundary mismatch"}
    return value


def _harness_environment() -> tuple[dict, Path]:
    env = dict(os.environ)
    compat = Path.home() / ".roborsi-ops/supervision-20260914/rt-compat-warp121"
    if env.get("ROBORSI_HARNESS_NAMESPACE", "robotwin") == "libero":
        # RT's binary wheels target Python3.10; LIBERO's existing env is3.12.
        paths = [x for x in env.get("PYTHONPATH", "").split(os.pathsep)
                 if x and Path(x).resolve() != compat.resolve()]
        env["PYTHONPATH"] = os.pathsep.join([str(_REPO), *paths])
        cwd = _REPO
    else:
        bicoord = env.get("ROBORSI_BICOORD_ROOT")
        if not bicoord or not Path(bicoord).is_dir():
            raise ValueError("set ROBORSI_BICOORD_ROOT to a valid checkout")
        cwd = Path(bicoord)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(compat), str(_REPO), env.get("PYTHONPATH", "")])
    model_root = Path.home() / "roborsi-lab/models"
    for key, folder in (("ROBORSI_POINT_SAM_MODEL", "facebook-sam-vit-base"),
                        ("ROBORSI_GROUNDING_DINO_MODEL", "grounding-dino-tiny")):
        if not env.get(key) and (model_root / folder).is_dir():
            env[key] = str(model_root / folder)
    return env, cwd


def _invoke_harness(skill_name: str, timeout_s: int) -> tuple[dict, str, str]:
    """Preserve complete process evidence; parse only the final skill report."""
    artifact = _BASELINES.parent / "harness_invocations" / str(time.time_ns())
    artifact.mkdir(parents=True, exist_ok=False)
    out, err, parsed, rc = "", "", {}, None
    try:
        env, cwd = _harness_environment()
        default_python = (sys.executable if env.get("ROBORSI_HARNESS_NAMESPACE") == "libero"
                          else str(Path.home() / "miniconda3/envs/RoboTwin/bin/python"))
        cmd = [env.get("ROBORSI_HARNESS_PYTHON") or default_python,
               str(_HARNESS), skill_name, "--from-frontmatter"]
        (artifact / "invocation.json").write_text(json.dumps({
            "skill": skill_name, "cmd": cmd, "cwd": str(cwd),
            "namespace": env.get("ROBORSI_HARNESS_NAMESPACE", "robotwin"),
            "pythonpath": env.get("PYTHONPATH"), "timeout_s": timeout_s}, indent=2))
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_s, cwd=cwd, env=env,
                             encoding="utf-8", errors="replace")
        out, err, rc = res.stdout or "", res.stderr or "", res.returncode
        parsed = _parse_report(out, skill_name)
        if rc != 0 and not (rc == 1 and parsed.get("verdict") == "FAIL"):
            parsed = {"verdict": "ERROR", "reason": f"harness exited {rc}",
                      "reported_result": parsed}
    except subprocess.TimeoutExpired as exc:
        out, err = exc.stdout or "", exc.stderr or ""
        if isinstance(out, bytes): out = out.decode("utf-8", errors="replace")
        if isinstance(err, bytes): err = err.decode("utf-8", errors="replace")
        parsed = {"verdict": "ERROR", "reason": f"harness timed out after {timeout_s}s"}
    except (OSError, ValueError) as exc:
        err = str(exc)
        parsed = {"verdict": "ERROR", "reason": str(exc)}
    (artifact / "stdout.log").write_text(out)
    (artifact / "stderr.log").write_text(err)
    (artifact / "report.json").write_text(json.dumps(
        {"skill": skill_name, "returncode": rc, "parsed": parsed}, indent=2))
    parsed["artifact_dir"] = str(artifact)
    return parsed, out, err


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

    # Full-task gates are absolute; legacy no-regression never grants publication.
    if parsed.get("kind") == "simulator_task_success":
        if verdict == "PASS" and not (
            type(pc) is int and type(total) is int and type(cc) is int
            and type(parsed.get("min_required")) is int
            and 0 < parsed["min_required"] <= total
            and parsed["min_required"] <= pc <= total and cc == 0
        ):
            return _result("ERROR", "inconsistent full-task success report")
        return _result(verdict, reason)

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
