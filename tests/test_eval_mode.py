from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from roborsi.agents.workspace import Workspace
from roborsi.cli.commands import app
from roborsi.runtime_mode import (
    EvolutionDisabledError,
    RunMode,
    current_mode,
    evolution_enabled,
    use_run_mode,
)


def _fake_backend(instruction: str = "perform the visible task"):
    class Env:
        backend_name = "libero-pro"

        def __init__(self):
            self.instruction = instruction

        def reset(self, _seed):
            return SimpleNamespace(extras={"instruction": instruction})

        def close(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    class Backend:
        def available(self):
            return True, ""

        def make_env(self, _task, _config):
            return Env()

    return Backend()


def test_run_mode_context_is_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBORSI_RUN_MODE", raising=False)
    assert current_mode() is RunMode.EVOLVE
    with use_run_mode("eval"):
        assert current_mode() is RunMode.EVAL
        assert not evolution_enabled()
    assert current_mode() is RunMode.EVOLVE


def test_eval_tool_surface_removes_mutating_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roborsi.embodied import skills
    from roborsi.embodied.agent_loop import prompt_tools

    def fake_skill(name: str):
        return SimpleNamespace(
            name=name,
            description=name,
            path=Path(f"/tmp/base/{name}/robotwin/SKILL.md"),
            frontmatter={
                "name": name,
                "description": name,
                "args": {},
            },
        )

    available = [fake_skill("look"), fake_skill("register_skill")]
    monkeypatch.setattr(skills, "discover_ns", lambda _ns: available)
    monkeypatch.setattr(
        prompt_tools, "_legacy_tool_names", lambda _ns="robotwin": {"look", "register_skill"}
    )
    monkeypatch.setattr(prompt_tools, "_compound_specs", lambda _task, _types: [])

    with use_run_mode("eval"):
        names = {
            row["function"]["name"]
            for row in prompt_tools._build_tool_specs(task="demo")
        }
        tools_block = prompt_tools._build_tools_block()
    assert "look" in names
    assert "register_skill" not in names
    assert "propose_new_skill" not in names
    assert "propose_skill_update" not in names
    assert "register_skill" not in tools_block

    with use_run_mode("evolve"):
        evolve_names = {
            row["function"]["name"]
            for row in prompt_tools._build_tool_specs(task="demo")
        }
    assert {"register_skill", "propose_new_skill", "propose_skill_update"} <= evolve_names


def test_engineer_code_introspection_is_limited_to_public_base_skills() -> None:
    from roborsi.embodied.agent_loop import prompt_tools

    hidden = prompt_tools._dispatch_meta_tool(
        "read_skill_code",
        {"name": "check_task_success"},
        ns="robotwin",
    )
    atomic = prompt_tools._dispatch_meta_tool(
        "read_skill_code",
        {"name": "libero_pick_place.zeroshot"},
        ns="libero",
    )
    assert hidden["ok"] is False
    assert "not visible" in hidden["reason"]
    assert atomic["ok"] is False
    assert "public base skill" in atomic["reason"]


def test_libero_detector_does_not_enumerate_simulator_object_names() -> None:
    from roborsi.embodied.skills.base._lib.libero import _perception

    source = Path(_perception.__file__).read_text(encoding="utf-8")
    assert "scene_object_names" not in source
    assert "scene_candidates" not in source
    assert "env.raw_obs()" not in source


def test_eval_outer_tool_surface_removes_unsafe_entrypoints() -> None:
    from roborsi.channels.core import agent as core_agent

    names = {
        row["function"]["name"]
        for row in core_agent._outer_tool_specs(can_evolve=False)
    }
    assert "run_skill" in names
    assert "run_python" not in names
    assert "propose_new_skill" not in names
    assert "propose_skill_update" not in names
    assert "get_lh_report" not in names


def test_eval_data_store_is_separate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.data.store import DataStore

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    with use_run_mode("eval"):
        assert DataStore().root == (tmp_path / "evals").resolve()
        assert DataStore(root=tmp_path / "data").root == (tmp_path / "evals").resolve()
        assert DataStore(root=tmp_path / "data" / "demo").root == (
            tmp_path / "evals" / "demo"
        ).resolve()
    with use_run_mode("evolve"):
        assert DataStore().root == (tmp_path / "data").resolve()


def test_eval_reads_released_recipes_without_writing_training_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.embodied.skills._lib.library import skill_library

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    for run_id in ("r0", "r1"):
        run_dir = tmp_path / "data" / "demo" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(
            json.dumps({"success": True}),
            encoding="utf-8",
        )
        (run_dir / "trace.json").write_text(
            json.dumps([
                {"tool_call": {"tool": "look", "args": {}}},
                {"tool_call": {"tool": "grasp_object", "args": {}}},
                {"tool_call": {"tool": "done", "args": {"success": True}}},
            ]),
            encoding="utf-8",
        )

    with use_run_mode("eval"):
        recipes = skill_library.get_proven_recipes("demo")
        with pytest.raises(EvolutionDisabledError):
            skill_library.promote_functions_from_code(
                "demo",
                "def helper():\n    return 1\n",
            )

    assert [recipe.tool_sequence for recipe in recipes] == [
        ["grasp_object", "done"]
    ]
    assert not (tmp_path / "evals" / "demo" / "_function_library.json").exists()


def test_eval_does_not_write_task_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.agents import task_wiki

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    monkeypatch.setattr(task_wiki, "_task_skill_dir", lambda _task: skill_dir)

    with use_run_mode("eval"):
        assert task_wiki.read_wiki("demo") == ""
        task_wiki.append_success_trace(
            task="demo",
            atomic="demo",
            seed=0,
            run_id="r0",
            tool_events=[],
            tool_calls_total=0,
        )
        with pytest.raises(EvolutionDisabledError):
            task_wiki.propose_measurement(
                task="demo",
                measurement_md="x",
                rationale="x",
                source_run_id="r0",
                reviewer="reviewer",
            )

    assert not (skill_dir / "wiki.md").exists()


def test_reviewer_history_uses_ground_truth_firewall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.agents import reviewer, task_wiki

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "wiki.md").write_text(
        "# Wiki\n\n"
        "## Manager-approved leads\n"
        "- ground truth says use a hidden threshold\n"
        "- use camera depth and a top-down grasp\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(task_wiki, "_task_skill_dir", lambda _task: skill_dir)

    block = reviewer._task_history_block("demo")
    assert "ground truth" not in block
    assert "use camera depth" in block


def test_eval_reviewer_suppresses_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.agents import persistent_agent
    from roborsi.agents import reviewer as reviewer_module

    workspace = Workspace(task="demo", run_id="r0", root=tmp_path)
    workspace.write_plan("# plan\n")
    workspace.write_summary("# summary\n")
    monkeypatch.setattr(reviewer_module, "_task_history_block", lambda _task: "(none)")
    monkeypatch.setattr(
        persistent_agent,
        "run_role",
        lambda *args, **kwargs: json.dumps({
            "verdict": "continue",
            "root_cause": "missing primitive",
            "next_action": "report the frozen limitation",
            "proposal_decision": "NEW_SKILL",
            "proposal_payload": {"name": "new_primitive"},
            "review_md": "diagnosis",
        }),
    )

    with use_run_mode("eval"):
        review = reviewer_module.Reviewer().review(
            workspace=workspace,
            engineer_result={
                "success": False,
                "outcome": "failed",
                "tool_calls": 1,
                "trace": [],
            },
        )

    assert review["proposal_decision"] == "NO_PROPOSAL"
    assert review["proposal_payload"] == {}
    assert review["proposal_suppressed"] is True
    assert "Filter mode**: `disabled`" in workspace.review_path.read_text()
    assert not workspace.proposal_link_path.exists()


def test_reviewer_does_not_receive_final_simulator_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from roborsi.agents import persistent_agent
    from roborsi.agents import reviewer as reviewer_module

    workspace = Workspace(task="demo", run_id="r0", root=tmp_path)
    workspace.write_plan("# plan\n")
    workspace.write_summary(
        "# Engineer Summary\n\n**Agent completion claim**: `True`\n"
    )
    seen: dict[str, str] = {}

    def fake_role(_role, _task, user_block, **_kwargs):
        seen["prompt"] = user_block
        return json.dumps({
            "verdict": "done",
            "root_cause": "",
            "next_action": "",
            "proposal_decision": "NO_PROPOSAL",
            "review_md": "visible evidence only",
        })

    monkeypatch.setattr(persistent_agent, "run_role", fake_role)
    monkeypatch.setattr(reviewer_module, "_task_history_block", lambda _task: "(none)")
    monkeypatch.setattr(reviewer_module, "_gate_log_for_run", lambda _run: [])

    reviewer_module.Reviewer(allow_evolution=False).review(
        workspace=workspace,
        engineer_result={
            "success": True,
            "outcome": "predicate_passed_without_done",
            "tool_calls": 1,
            "trace": [{"tool_call": {"tool": "look", "args": {}}}],
            "rollout_meta": {
                "vlm_declared": True,
                "predicate_check": True,
            },
        },
        run_id="r0",
    )

    assert "agent_completion_claim=True" in seen["prompt"]
    assert "predicate_check" not in seen["prompt"]
    assert "predicate_passed_without_done" not in seen["prompt"]
    assert "success=True" not in seen["prompt"]


def test_eval_uses_stateless_role_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roborsi.agents import persistent_agent
    from roborsi.embodied.agent_loop import vlm_io

    monkeypatch.setattr(
        persistent_agent,
        "run",
        lambda *args, **kwargs: pytest.fail("persistent session used during eval"),
    )
    monkeypatch.setattr(
        vlm_io,
        "_call_vlm_tools",
        lambda *args, **kwargs: SimpleNamespace(content="stateless"),
    )

    with use_run_mode("eval"):
        out = persistent_agent.run_role(
            "planner",
            "demo",
            "task",
            system_prompt="system",
            model="anthropic/test",
        )
    assert out == "stateless"


def test_trace_db_records_and_filters_run_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.store import trace_db

    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace.db"))
    monkeypatch.setattr(trace_db, "_INITIALISED", False)
    with use_run_mode("eval"):
        trace_db.insert_run("eval-run", task="demo")
        trace_db.record_bench(
            "demo.zeroshot",
            "test",
            seeds_passed=1,
            seeds_total=1,
        )
    with use_run_mode("evolve"):
        trace_db.insert_run("evolve-run", task="demo")

    assert trace_db.get_run("eval-run")["run_mode"] == "eval"
    assert [row["id"] for row in trace_db.list_runs(run_mode="eval")] == ["eval-run"]
    assert [row["id"] for row in trace_db.list_runs(run_mode="evolve")] == ["evolve-run"]
    conn = trace_db._conn()
    try:
        bench_mode = conn.execute(
            "SELECT run_mode FROM benches WHERE skill = ?",
            ("demo.zeroshot",),
        ).fetchone()["run_mode"]
    finally:
        conn.close()
    assert bench_mode == "eval"


def test_eval_blocks_training_and_proposal_registry_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.store import trace_db

    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace.db"))
    monkeypatch.setattr(trace_db, "_INITIALISED", False)
    with use_run_mode("eval"):
        with pytest.raises(EvolutionDisabledError):
            trace_db.record_proposal("demo", "new")
        with pytest.raises(EvolutionDisabledError):
            trace_db.update_proposal_status("missing", "rejected")
        with pytest.raises(EvolutionDisabledError):
            trace_db.record_vla_episode("r0", "demo", True)


def test_eval_exec_python_scratch_is_episode_local() -> None:
    from roborsi.embodied.skills.base.exec_python.robotwin import policy

    first = SimpleNamespace()
    second = SimpleNamespace()
    with use_run_mode("eval"):
        first_scratch = policy._scratch_for_state(first)
        first_scratch["seen"] = True
        assert policy._scratch_for_state(first)["seen"] is True
        assert policy._scratch_for_state(second) == {}


def test_eval_mode_propagates_into_tool_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roborsi.embodied.agent_loop import rollout

    monkeypatch.setattr(
        rollout,
        "_dispatch",
        lambda _state, _call: ({"mode": current_mode().value}, SimpleNamespace()),
    )
    state = SimpleNamespace(_timeout_history={})
    with use_run_mode("eval"):
        result, _ = rollout._dispatch_with_timeout(
            state,
            {"tool": "look", "args": {}},
            timeout_s=1.0,
        )
    assert result["mode"] == "eval"


def test_libero_tool_dispatch_stays_on_environment_owner_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from roborsi.embodied.agent_loop import rollout

    owner_thread = threading.get_ident()
    monkeypatch.setattr(
        rollout,
        "_dispatch",
        lambda _state, _call: (
            {
                "thread": threading.get_ident(),
                "mode": current_mode().value,
            },
            SimpleNamespace(),
        ),
    )
    state = SimpleNamespace(
        env=SimpleNamespace(backend_name="libero-pro"),
        _timeout_history={},
    )

    with use_run_mode("eval"):
        result, _ = rollout._dispatch_with_timeout(
            state,
            {"tool": "grasp_object", "args": {}},
            timeout_s=1.0,
        )

    assert result == {"thread": owner_thread, "mode": "eval"}


def test_usage_metrics_count_each_provider_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import litellm

    from roborsi.embodied.agent_loop import vlm_io

    calls = 0

    def fake_completion(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("transient timeout")
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=11,
                completion_tokens=7,
                total_tokens=18,
            ),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                )
            ],
        )

    def immediate_retry(fn):
        try:
            return fn()
        except TimeoutError:
            return fn()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(vlm_io, "_retry_litellm", immediate_retry)

    with vlm_io.capture_usage() as usage:
        assert vlm_io._call_vlm("test/model", []) == "ok"

    assert usage.vlm_calls == 2
    assert usage.metered_calls == 1
    assert usage.unmetered_calls == 1
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 7
    assert usage.total_tokens == 18


def test_tool_timing_is_grouped_for_efficiency_reports() -> None:
    from roborsi.agents.engineer import _summarize_tool_timing

    timing = _summarize_tool_timing([
        {"timing_phase": "perception", "wallclock_s": 1.25},
        {"timing_phase": "action", "wallclock_s": 2.5},
        {"timing_phase": "recovery", "wallclock_s": 0.75},
        {"timing_phase": "other", "wallclock_s": 0.1},
    ])

    assert timing == {
        "perception_s": 1.25,
        "action_s": 2.5,
        "recovery_s": 0.75,
        "other_s": 0.1,
        "tool_total_s": 4.6,
    }


def test_atomic_eval_runner_skips_writeback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import roborsi.agents as agents
    from roborsi.channels.core import agent as core_agent
    from roborsi.store import trace_db

    workspace = Workspace(task="demo", run_id="eval-r0", root=tmp_path)

    class Planner:
        def plan(self, **_kwargs):
            workspace.write_plan("# frozen plan\n")
            return {"goal": "demo", "success_criteria": ["visible success"]}

    class Engineer:
        def execute(self, **_kwargs):
            return {
                "success": True,
                "outcome": "predicate_passed",
                "tool_calls": 1,
                "trace": [{"tool_call": {"tool": "look", "args": {}}}],
                "rollout_meta": {"model": "test", "predicate_check": True},
            }

    class Reviewer:
        def __init__(self, **_kwargs):
            pass

        def review(self, **_kwargs):
            workspace.write_review("# review\n")
            return {
                "verdict": "done",
                "proposal_decision": "NO_PROPOSAL",
                "next_action": "",
            }

    monkeypatch.setattr(agents, "Planner", Planner)
    monkeypatch.setattr(agents, "Engineer", Engineer)
    monkeypatch.setattr(agents, "Reviewer", Reviewer)
    monkeypatch.setattr(agents, "new_workspace", lambda _task: workspace)
    monkeypatch.setattr(
        "roborsi.agents.atomic_backend.resolve",
        lambda _task: SimpleNamespace(backend_name="robotwin", sim_task="demo"),
    )
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.get_backend",
        lambda _name: _fake_backend(),
    )
    monkeypatch.setattr(trace_db, "insert_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(trace_db, "update_run", lambda *args, **kwargs: None)

    class Session:
        def append(self, *_args, **_kwargs):
            return None

    with use_run_mode("eval"):
        result = core_agent._run_atomic_3role(
            text="evaluate demo",
            atomic="demo",
            seed=0,
            sess=Session(),
            target_chat_id="eval-test",
            channel=None,
            ctx=None,
            return_details=True,
        )

    assert isinstance(result, dict)
    assert result["run_mode"] == "eval"
    assert result["proposal_decision"] == "NO_PROPOSAL"


def test_atomic_eval_preserves_sim_verdict_when_reviewer_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import roborsi.agents as agents
    from roborsi.channels.core import agent as core_agent
    from roborsi.store import trace_db

    workspace = Workspace(task="demo", run_id="eval-reviewer-error", root=tmp_path)

    class Planner:
        def plan(self, **_kwargs):
            workspace.write_plan("# frozen plan\n")
            return {"goal": "demo", "success_criteria": ["visible success"]}

    class Engineer:
        def execute(self, **_kwargs):
            return {
                "success": True,
                "outcome": "predicate_passed",
                "tool_calls": 1,
                "trace": [{"tool_call": {"tool": "look", "args": {}}}],
                "rollout_meta": {"model": "test", "predicate_check": True},
            }

    class Reviewer:
        def __init__(self, **_kwargs):
            pass

        def review(self, **_kwargs):
            raise RuntimeError("review provider unavailable")

    monkeypatch.setattr(agents, "Planner", Planner)
    monkeypatch.setattr(agents, "Engineer", Engineer)
    monkeypatch.setattr(agents, "Reviewer", Reviewer)
    monkeypatch.setattr(agents, "new_workspace", lambda _task: workspace)
    monkeypatch.setattr(
        "roborsi.agents.atomic_backend.resolve",
        lambda _task: SimpleNamespace(backend_name="robotwin", sim_task="demo"),
    )
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.get_backend",
        lambda _name: _fake_backend(),
    )
    monkeypatch.setattr(trace_db, "insert_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(trace_db, "update_run", lambda *args, **kwargs: None)

    class Session:
        def append(self, *_args, **_kwargs):
            return None

    with use_run_mode("eval"):
        result = core_agent._run_atomic_3role(
            text="evaluate demo",
            atomic="demo",
            seed=0,
            sess=Session(),
            target_chat_id="eval-test",
            channel=None,
            ctx=None,
            return_details=True,
        )

    assert isinstance(result, dict)
    assert result["success"] is True
    assert result["reviewer_verdict"] == "unavailable"
    assert result["reviewer_error"] == "RuntimeError: review provider unavailable"
    assert "NO_PROPOSAL" in workspace.review_path.read_text()


def test_eval_cli_runs_all_requested_seeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.channels.core import agent as core_agent

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    seen: list[tuple[int, RunMode]] = []

    def fake_run(**kwargs):
        seed = kwargs["seed"]
        seen.append((seed, current_mode()))
        return {
            "text": "done",
            "run_id": f"run-{seed}",
            "workspace": f"/tmp/run-{seed}",
            "task": kwargs["atomic"],
            "backend": kwargs.get("backend_name") or "robotwin",
            "sim_task": kwargs.get("sim_task") or kwargs["atomic"],
            "seed": seed,
            "run_mode": "eval",
            "success": seed % 2 == 0,
            "outcome": "done",
            "tool_calls": 1,
            "reviewer_verdict": "done",
            "proposal_decision": "NO_PROPOSAL",
            "video_path": None,
        }

    monkeypatch.setattr(core_agent, "_run_atomic_3role", fake_run)
    result = CliRunner().invoke(
        app,
        ["eval", "demo", "--seeds", "2", "--seed-start", "4", "--json"],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["run_mode"] == "eval"
    assert summary["frozen"] is True
    assert summary["seeds_passed"] == 1
    assert seen == [(4, RunMode.EVAL), (5, RunMode.EVAL)]
    assert Path(summary["manifest_path"]).exists()


def test_web_cli_is_exposed() -> None:
    result = CliRunner().invoke(app, ["web", "--help"])
    assert result.exit_code == 0, result.output
    assert "--evo-port" in result.output
    assert "--cockpit-port" in result.output


def test_eval_cli_excludes_infra_from_success_denominator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from roborsi.channels.core import agent as core_agent

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))

    def fake_run(**kwargs):
        seed = kwargs["seed"]
        if seed == 0:
            raise RuntimeError("backend unavailable")
        return {
            "text": "done",
            "run_id": f"run-{seed}",
            "workspace": f"/tmp/run-{seed}",
            "task": kwargs["atomic"],
            "backend": "libero",
            "sim_task": "libero_object/0",
            "seed": seed,
            "run_mode": "eval",
            "success": True,
            "outcome": "predicate_passed",
            "tool_calls": 1,
            "reviewer_verdict": "done",
            "proposal_decision": "NO_PROPOSAL",
            "video_path": None,
        }

    monkeypatch.setattr(core_agent, "_run_atomic_3role", fake_run)
    result = CliRunner().invoke(
        app,
        ["eval", "demo", "--seeds", "2", "--json"],
    )

    assert result.exit_code == 2
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["requested_seeds"] == 2
    assert summary["verdict_count"] == 1
    assert summary["seeds_passed"] == 1
    assert summary["infra_count"] == 1
    assert summary["success_rate"] == 1.0
    assert Path(summary["manifest_path"]).exists()
