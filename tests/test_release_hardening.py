"""Release checks: hidden-state firewall, sandbox, task panel, Reviewer-gated rounds."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from roborsi.agents.roles.manager.native import assert_native_candidate  # noqa: E402
from roborsi.agents.baselines import _reject_sandbox_escape  # noqa: E402
from roborsi.evaluation.suite import _SHORT_TASK  # noqa: E402


@pytest.mark.parametrize("code", [
    "x = env.objects_dict['bowl']",
    "env.check_success()",
    "getattr(env, '_check_' + 'success')()",
    "import subprocess",
    "import urllib.request",
    "eval('1')",
])
def test_native_candidate_rejects_hidden_state_and_escapes(code):
    with pytest.raises(ValueError):
        assert_native_candidate(code)


def test_native_candidate_accepts_existing_libero_skills():
    for path in sorted((REPO / "roborsi/embodied/skills/base").glob("*/libero/policy.py")):
        assert_native_candidate(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", [
    "look.__closure__[0].cell_contents.env",
    "np.load('x.npy')",
    "getattr(result, name)",
    "vars(look)",
    "open('x')",
])
def test_capx_sandbox_rejects_escape(code):
    with pytest.raises(ValueError):
        _reject_sandbox_escape(code)


def test_capx_sandbox_allows_plain_policy():
    _reject_sandbox_escape("r = look()\nif r.get('ok'):\n    v = getattr(r, 'get')\n")


def test_short_panel_is_120_tasks_without_libero_10():
    suites = [f"libero_{b}_{d}" for b in ("goal", "spatial", "object", "10")
              for d in ("task", "object", "swap", "lan")]
    keys = [f"{s}/{i}" for s in suites for i in range(10)]
    assert sum(bool(_SHORT_TASK.match(k)) for k in keys) == 120


def test_episode_rounds_are_gated_by_reviewer_not_simulator():
    source = (REPO / "roborsi/channels/core/agent.py").read_text(encoding="utf-8")
    assert "ROBORSI_EVAL_MAX_ROUNDS" not in source
    assert "sim_predicate={" not in source
    assert 'last_round_review.get("verdict") != "continue"' in source


def test_reviewer_never_sees_simulator_verdict():
    from roborsi.channels.core.agent import _public_engineer_result

    public = _public_engineer_result({
        "success": True, "outcome": "success", "tool_calls": 7,
        "trace": [{"step": 1}],
        "rollout_meta": {"predicate_check": True, "vlm_declared": True},
    })
    assert "success" not in public and "outcome" not in public
    assert "predicate_check" not in public["rollout_meta"]
    assert public["rollout_meta"]["vlm_declared"] is True


def test_review_tools_read_only_this_episode(tmp_path):
    import json as _json

    from roborsi.agents.tools.reviewer import _dispatch, evidence_index

    rd = tmp_path / "rollout" / "round_01" / "task-0"
    rd.mkdir(parents=True)
    (rd / "trace.json").write_text(_json.dumps([
        {"step": i, "tool_call": {"tool": "look", "args": {}},
         "result": {"ok": i != 2}} for i in range(4)]))
    (rd / "tick_00000.jpg").write_bytes(b"jpg")
    (tmp_path / "plan.md").write_text("# plan")

    assert "round 1: 4 steps, 1 failed" in evidence_index(tmp_path)
    steps, _ = _dispatch(tmp_path, "read_steps", {"round": 1, "start": 1, "end": 3})
    assert [s["step"] for s in steps["steps"]] == [1, 2]
    _, image = _dispatch(tmp_path, "view_frame", {"round": 1, "index": -1})
    assert image.name == "tick_00000.jpg"
    bad, _ = _dispatch(tmp_path, "read_document", {"name": "../../trace.db"})
    assert bad["ok"] is False
    missing, _ = _dispatch(tmp_path, "read_steps", {"round": 2, "start": 0, "end": 1})
    assert missing["ok"] is False


def test_each_role_has_shared_and_own_skills():
    from roborsi.agents.roles.role_skills import ROLES, skills_for_role, with_role_skills

    for role in ROLES:
        names = [s.name for s in skills_for_role(role)]
        assert names[0] == "shared-evidence-rules"
        assert any(n.startswith(role) for n in names[1:]), (role, names)
    assert "reviewer-assess-evidence" in with_role_skills("reviewer", "base")
    assert "planner-plan-atomic" not in with_role_skills("reviewer", "base")


def test_role_sessions_are_per_simulator_task(monkeypatch, tmp_path):
    from roborsi.agents.sessions import persistent_agent as pa

    monkeypatch.setenv("ROBORSI_CURRENT_SIM_TASK", "libero_goal/3")
    assert pa._session_task("libero_pick_place") == "libero_goal/3"
    assert pa._session_task("beat_block_hammer") == "beat_block_hammer"


def test_manager_loop_runs_when_queue_has_pending(monkeypatch, tmp_path):
    import json as _json
    import time

    from roborsi.evaluation import suite

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    calls = []

    def fake_cycle():
        calls.append(1)
        for p in (tmp_path / "skill_review").glob("*.json"):
            p.write_text(_json.dumps({"status": "applied"}))

    monkeypatch.setattr(suite, "_run_manager_cycle", fake_cycle)
    loop = suite._ManagerLoop(poll_s=0.05)
    (tmp_path / "skill_review").mkdir()
    (tmp_path / "skill_review" / "p.json").write_text(_json.dumps({"status": "pending"}))
    loop.wake()
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    loop.stop()
    assert calls, "Manager cycle did not run for a pending proposal"
    assert not suite._pending_reviews()


def test_libero_plus_panel_and_breakdown(tmp_path):
    import collections
    import json as _json

    from roborsi.evaluation.panels import breakdown, load_panel
    from roborsi.evaluation.suite import _SHORT_TASK

    inst = load_panel("libero_plus_840")["instances"]
    assert len(inst) == 840
    assert set(collections.Counter(inst.values()).values()) == {120}
    assert all(_SHORT_TASK.match(k) for k in inst)
    first = next(iter(inst))
    (tmp_path / "episodes.jsonl").write_text(
        _json.dumps({"task_key": first, "verdict": "success"}) + "\n")
    out = breakdown(str(tmp_path))
    assert out["total"] == {"success": 1, "valid": 1, "planned": 840}


def test_task_local_skill_shadows_shared_only_for_its_task(monkeypatch, tmp_path):
    import roborsi.embodied.skills as skills

    def write(path, desc):
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: grip\nkind: base\ndescription: {desc}\n---\n")
        (path / "policy.py").write_text("def dispatch_runtime(state, args):\n    return {}, None\n")

    write(tmp_path / "base" / "grip" / "libero", "shared")
    write(tmp_path / "task_local" / "libero_goal__3" / "base" / "grip" / "libero", "local")
    monkeypatch.setattr(skills, "SHIPPED_ROOT", tmp_path)
    monkeypatch.setattr(skills, "user_root", lambda: tmp_path / "none")
    monkeypatch.setenv("ROBORSI_CURRENT_SIM_TASK", "libero_goal/3")
    assert skills.get_ns("grip", "libero").description == "local"
    monkeypatch.setenv("ROBORSI_CURRENT_SIM_TASK", "libero_goal/4")
    assert skills.get_ns("grip", "libero").description == "shared"
    assert all("task_local" not in s.path.relative_to(tmp_path).parts for s in skills.discover())


def test_tasks_using_skill_finds_other_tasks(monkeypatch, tmp_path):
    import json as _json

    from roborsi.agents.evolution.episode_gate import tasks_using_skill

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    for i, (task, tool) in enumerate([("libero_goal/1", "grasp_object"),
                                      ("libero_goal/2", "push_object"),
                                      ("libero_goal/3", "grasp_object"),
                                      ("libero_goal/4", "grasp_object")]):
        ws = tmp_path / "workspaces" / f"w{i}"
        rd = ws / "rollout" / "round_01" / "t"
        rd.mkdir(parents=True)
        (ws / "episode_identity.json").write_text(_json.dumps({"task_key": task}))
        (rd / "trace.json").write_text(_json.dumps([{"tool_call": {"tool": tool}}]))
    found = tasks_using_skill("grasp_object", "libero_goal/4", 5)
    assert set(found) == {"libero_goal/1", "libero_goal/3"}


def test_harness_normalizer_repairs_mechanical_errors():
    from roborsi.agents.roles.manager.patch_context import normalize_harness

    md = """---
name: rotate_attached_knob
metadata:
  harness:
    kind: sim_task
    task: turn_on_the_stove
    seeds: "17, 43, 89"
    args: {object: knob}
    pass_criteria: {kind: simulator_task_success, min_seeds_passing: 2}
---
body
"""
    fixed, problems = normalize_harness(md, "libero_goal/7")
    assert problems == []
    import yaml
    h = yaml.safe_load(fixed.split("---", 2)[1])["metadata"]["harness"]
    assert h["sim_task"] == "libero_goal/7"
    assert h["seeds"] == [17, 43, 89]
    assert h["args"] == [{"object": "knob"}]
    assert "task" not in h and "kind" not in h
    assert fixed.endswith("body\n")


def test_harness_normalizer_reports_what_the_author_must_fix():
    from roborsi.agents.roles.manager.patch_context import normalize_harness

    md = "---\nname: x\nmetadata:\n  harness:\n    sim_task: libero_goal/1\n    seeds: [3]\n    pass_criteria: {kind: looks_fine, min_seeds_passing: 1}\n---\n"
    _, problems = normalize_harness(md, "libero_goal/1")
    joined = " ".join(problems)
    assert "seeds" in joined and "args" in joined
    assert "pass_criteria.kind" in joined and "min_seeds_passing" in joined
    assert normalize_harness("no frontmatter", "libero_goal/1")[1]


def test_manager_sees_simulator_verdict_in_plain_fields():
    from roborsi.agents.roles.manager.cycle import outcome_view

    view = outcome_view({"success": True, "outcome": "vlm_declared_done"})
    assert view["simulator_success"] is True and view["engineer_declared_done"] is True
    assert outcome_view({"success": False, "outcome": "vlm_overclaimed"})["simulator_success"] is False
    real = outcome_view({"success": True, "outcome": "declared_done_unverified", "verified": False})
    assert real["simulator_success"] is None


def test_source_task_gate_compares_with_current_skill(monkeypatch):
    from roborsi.agents.evolution import episode_gate as g

    def fake(passes):
        def run(q, task, *, apply_candidate=True, min_pass=None, seeds=None):
            n = passes[apply_candidate]
            verdicts = {s: ("success" if k < n else "failure") for k, s in enumerate(seeds)}
            return g.EpisodeGateReport(True, "", {"passes": n, "verdicts": verdicts})
        return run

    monkeypatch.setattr(g, "run", fake({True: 1, False: 0}))
    assert g.source_task_gate({}, "libero_goal/5").overall_pass
    monkeypatch.setattr(g, "run", fake({True: 1, False: 2}))
    assert not g.source_task_gate({}, "libero_goal/5").overall_pass
    monkeypatch.setattr(g, "run", fake({True: 0, False: 0}))
    assert not g.source_task_gate({}, "libero_goal/5").overall_pass


def test_spread_picks_the_gpu_with_most_free_memory(monkeypatch):
    import subprocess
    from roborsi.embodied.sim.libero import runtime

    class Out:
        stdout = "0, 1000\n1, 90000\n4, 30000\n5, 89000\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Out())
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,4,5")
    monkeypatch.setenv("ROBORSI_PERCEPTION_DEVICE", "spread")
    monkeypatch.setattr(runtime, "_SPREAD_GPU", None)
    # Visible index and physical id, used for perception and for rendering.
    assert runtime.spread_gpu() in ((1, 1), (3, 5))
    monkeypatch.delenv("ROBORSI_PERCEPTION_DEVICE")
    assert runtime.spread_gpu() is None


def test_failed_skill_harness_falls_back_to_comparison_with_current_skill(monkeypatch):
    from roborsi.agents.evolution import episode_gate, validator
    from roborsi.agents.roles.manager import cycle

    class Check:
        def __init__(self, passed, verdict=None):
            self.passed, self.detail, self.extras = passed, "verdict=FAIL (0/3)", {"verdict": verdict}

    class Report:
        def __init__(self, verdict):
            self.overall_pass, self.capability, self.harness = False, Check(True), Check(False, verdict)

    md = ("---\nname: push_object\nmetadata:\n  harness:\n    sim_task: libero_goal/5\n"
          "    seeds: [0, 1, 2]\n    args: [{}]\n"
          "    pass_criteria: {kind: simulator_task_success, min_seeds_passing: 2}\n---\n")
    calls = []
    monkeypatch.setattr(episode_gate, "source_task_gate",
                        lambda q, t: calls.append(t) or episode_gate.EpisodeGateReport(True, "candidate 1, current 0"))
    monkeypatch.setattr(validator.ProposalValidator, "validate", lambda self, q: Report("FAIL"))
    out = cycle.validate_code({"name": "push_object", "skill_md": md, "source_task": "libero_goal/5"})
    assert out.overall_pass and calls == ["libero_goal/5"] and out.note.startswith("harness:")
    # A crashing or malformed harness is not rescued by the comparison.
    monkeypatch.setattr(validator.ProposalValidator, "validate", lambda self, q: Report("ERROR"))
    assert not cycle.validate_code({"name": "push_object", "skill_md": md, "source_task": "libero_goal/5"}).overall_pass


def test_engineer_giving_up_is_labelled_as_such(tmp_path):
    import json
    from types import SimpleNamespace
    from roborsi.channels.core.agent import _adjudicate_episode

    class Env:
        def check_success(self):
            return False

    eng = {"outcome": "vlm_declared_done", "rollout_meta": {"vlm_declared": False}}
    try:
        _adjudicate_episode(eng, Env(), SimpleNamespace(root=tmp_path), "t", 0)
    except Exception:
        pass  # later steps (frames, video) need a real workspace
    result = json.loads((tmp_path / "episode_result.json").read_text())
    assert result == {"success": False, "outcome": "engineer_gave_up"}


def test_manager_outcome_view_marks_old_give_ups():
    from roborsi.agents.roles.manager.cycle import outcome_view

    view = outcome_view({"success": False, "outcome": "vlm_declared_done"})
    assert view["engineer_gave_up"] and not view["engineer_declared_done"]
