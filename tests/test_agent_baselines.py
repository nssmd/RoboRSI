"""Minimal unit tests for the ICLR baseline agent modes (maestro / openeta /
capx) — mock provider, no simulator.

Each baseline test asserts the three shared protocol invariants:
  1. no proposal is ever produced (proposal_decision == NO_PROPOSAL),
  2. the tool budget is enforced,
  3. the episode row carries agent_mode (so episodes.jsonl records it).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from roborsi.agents.workspace import Workspace
from roborsi.embodied.agent_loop.env import Observation


class _FakeEnv:
    backend_name = "libero-pro"

    def __init__(self, instruction: str = "put the block in the basket",
                 success: bool = False):
        self.instruction = instruction
        self._success = success
        self.reset_calls = 0

    def reset(self, _seed):
        self.reset_calls += 1
        return SimpleNamespace(extras={"instruction": self.instruction})

    def take_snapshot(self):
        return Observation()

    def check_success(self):
        return self._success

    def hook_physics_step(self, _cb):
        return lambda: None

    def tool_handlers(self):
        def _ok(_state, _args):
            return ({"ok": True}, Observation())

        return {"noop_action": _ok, "look": _ok}

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _FakeBackend:
    def __init__(self, env: _FakeEnv):
        self.env = env

    def available(self):
        return True, ""

    def make_env(self, _task, _config):
        return self.env


class _Sess:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def append(self, kind, **payload):
        self.events.append((kind, payload))


def _tool_msg(name: str, args: dict | None = None):
    return SimpleNamespace(
        content="thinking",
        tool_calls=[SimpleNamespace(
            id="tc-1",
            function=SimpleNamespace(
                name=name,
                arguments=json.dumps(args or {}),
            ),
        )],
    )


@pytest.fixture()
def baseline_ctx(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Common scaffolding: fake backend/env, tmp workspace, no trace.db."""
    import roborsi.agents as agents
    from roborsi.store import trace_db

    env = _FakeEnv()
    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace.db"))
    monkeypatch.setenv("ROBORSI_PERCEPTION_MODEL", "test/model")
    monkeypatch.setenv("ROBORSI_VLM_PROVIDER", "litellm")
    monkeypatch.setattr(trace_db, "_INITIALISED", False)
    monkeypatch.setattr(trace_db, "insert_run", lambda *a, **k: None)
    monkeypatch.setattr(trace_db, "update_run", lambda *a, **k: None)
    workspace = Workspace(task="demo", run_id="baseline-r0", root=tmp_path)
    monkeypatch.setattr(agents, "new_workspace", lambda _task: workspace)
    monkeypatch.setattr(
        "roborsi.agents.atomic_backend.resolve",
        lambda _task: SimpleNamespace(
            backend_name="libero-pro", sim_task="libero_object/0"
        ),
    )
    monkeypatch.setattr(
        "roborsi.embodied.agent_loop.get_backend",
        lambda _name: _FakeBackend(env),
    )
    return SimpleNamespace(env=env, workspace=workspace, sess=_Sess())


def test_maestro_single_session_respects_budget_and_never_proposes(
    baseline_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roborsi.agents.baselines import run_baseline_atomic
    from roborsi.embodied.agent_loop import rollout
    from roborsi.runtime_mode import use_run_mode

    # Mock provider: the orchestrator VLM keeps acting, never calls done().
    monkeypatch.setattr(
        rollout, "_call_vlm_tools",
        lambda _model, _convo, _tools: _tool_msg("noop_action"),
    )

    with use_run_mode("eval"):
        details = run_baseline_atomic(
            agent_mode="maestro",
            text="evaluate demo",
            atomic="demo",
            seed=0,
            sess=baseline_ctx.sess,
            tool_budget=3,
            model="test/model",
        )

    assert details["agent_mode"] == "maestro"
    assert details["proposal_decision"] == "NO_PROPOSAL"
    assert details["reviewer_verdict"] is None
    assert details["tool_calls"] == 3          # budget, not one call more
    assert details["success"] is False
    assert details["outcome"] == "budget_exceeded"
    assert details["run_mode"] == "eval"


def test_openeta_forces_observation_each_cycle_and_respects_budget(
    baseline_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roborsi.agents.baselines import _run_openeta
    from roborsi.agents.baselines import run_baseline_atomic
    from roborsi.embodied.agent_loop import vlm_io
    from roborsi.runtime_mode import use_run_mode

    monkeypatch.setattr(
        vlm_io, "_call_vlm_tools",
        lambda _model, _convo, _tools: _tool_msg("noop_action"),
    )

    with use_run_mode("eval"):
        details = run_baseline_atomic(
            agent_mode="openeta",
            text="evaluate demo",
            atomic="demo",
            seed=0,
            sess=baseline_ctx.sess,
            tool_budget=5,
            model="test/model",
        )

    assert details["agent_mode"] == "openeta"
    assert details["proposal_decision"] == "NO_PROPOSAL"
    assert details["tool_calls"] == 5          # forced looks + actions == budget
    assert details["success"] is False

    # The forced observe phase leads every cycle and is charged to the budget.
    with use_run_mode("eval"):
        result = _run_openeta(
            baseline_ctx.env, atomic="demo", seed=0, tool_budget=5,
            model="test/model", workspace=baseline_ctx.workspace,
            task_instruction="put the block in the basket", ns="libero",
        )
    looks = [step for step in result["trace"] if step.get("forced_observation")]
    assert result["trace"][0]["tool_call"]["tool"] == "look"
    assert result["trace"][0]["forced_observation"] is True
    assert len(looks) == 3 and result["tool_calls"] == 5


def test_openeta_done_overclaim_is_overruled_by_sim_predicate(
    baseline_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roborsi.agents.baselines import _run_openeta
    from roborsi.embodied.agent_loop import vlm_io
    from roborsi.runtime_mode import use_run_mode

    monkeypatch.setattr(
        vlm_io, "_call_vlm_tools",
        lambda _model, _convo, _tools: _tool_msg("done", {"success": True}),
    )

    with use_run_mode("eval"):
        result = _run_openeta(
            baseline_ctx.env, atomic="demo", seed=0, tool_budget=10,
            model="test/model", workspace=baseline_ctx.workspace,
            task_instruction="put the block in the basket", ns="libero",
        )

    assert result["success"] is False
    assert result["outcome"] == "vlm_overclaimed"


def test_capx_budget_caps_generated_code_and_charges_all_skill_calls(
    baseline_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roborsi.agents.baselines import run_baseline_atomic
    from roborsi.embodied.agent_loop import vlm_io
    from roborsi.runtime_mode import use_run_mode

    code_reply = (
        "Here is the policy.\n"
        "```python\n"
        "for _ in range(100):\n"
        "    look()\n"
        "```\n"
    )
    monkeypatch.setattr(vlm_io, "_call_vlm_no_tools", lambda _model, _msgs: code_reply)

    with use_run_mode("eval"):
        details = run_baseline_atomic(
            agent_mode="capx",
            text="evaluate demo",
            atomic="demo",
            seed=0,
            sess=baseline_ctx.sess,
            tool_budget=4,
            model="test/model",
        )

    assert details["agent_mode"] == "capx"
    assert details["proposal_decision"] == "NO_PROPOSAL"
    assert details["tool_calls"] == 4          # budget exhausts the loop
    assert details["success"] is False
    assert details["outcome"] == "capx_exec_error"
    # Budget exhaustion is terminal: no repair round re-ran the policy.
    assert baseline_ctx.env.reset_calls == 1   # the initial reset only


def test_capx_repair_round_then_sim_predicate_decides(
    baseline_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roborsi.agents.baselines import run_baseline_atomic
    from roborsi.embodied.agent_loop import vlm_io
    from roborsi.runtime_mode import use_run_mode

    baseline_ctx.env._success = True
    replies = iter([
        "no code here, just prose",
        "```python\nr = look()\nassert r.get('ok')\n```",
    ])
    monkeypatch.setattr(
        vlm_io, "_call_vlm_no_tools", lambda _model, _msgs: next(replies))

    with use_run_mode("eval"):
        details = run_baseline_atomic(
            agent_mode="capx",
            text="evaluate demo",
            atomic="demo",
            seed=0,
            sess=baseline_ctx.sess,
            tool_budget=10,
            model="test/model",
        )

    assert details["success"] is True
    assert details["outcome"] == "capx_predicate_passed"
    assert details["agent_mode"] == "capx"
    assert details["tool_calls"] == 2          # initial look + policy look


def test_run_atomic_attempt_routes_and_stamps_agent_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import roborsi.agents.baselines as baselines
    from roborsi.evaluation.atomic import run_atomic_attempt

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace.db"))
    seen: dict[str, object] = {}

    def fake_baseline(**kwargs):
        seen.update(kwargs)
        return {
            "text": "ok",
            "run_id": "r0",
            "workspace": "/tmp/w",
            "task": kwargs["atomic"],
            "backend": "libero-pro",
            "sim_task": "libero_object/0",
            "seed": kwargs["seed"],
            "run_mode": "eval",
            "agent_mode": kwargs["agent_mode"],
            "success": True,
            "outcome": "capx_predicate_passed",
            "tool_calls": 2,
            "reviewer_verdict": None,
            "proposal_decision": "NO_PROPOSAL",
            "video_path": None,
        }

    monkeypatch.setattr(baselines, "run_baseline_atomic", fake_baseline)
    row = run_atomic_attempt(
        task="demo", seed=3, mode="eval", tool_budget=7, agent_mode="capx",
    )

    assert seen["agent_mode"] == "capx"
    assert seen["tool_budget"] == 7
    assert row["agent_mode"] == "capx"
    assert row["verdict"] == "success"
    assert row["status"] == "terminal"

    with pytest.raises(ValueError, match="agent_mode"):
        run_atomic_attempt(task="demo", seed=0, agent_mode="bogus")


def test_run_atomic_attempt_error_row_keeps_agent_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import roborsi.agents.baselines as baselines
    from roborsi.evaluation.atomic import run_atomic_attempt

    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path))
    monkeypatch.setenv("ROBORSI_TRACE_DB", str(tmp_path / "trace.db"))

    def broken(**_kwargs):
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(baselines, "run_baseline_atomic", broken)
    row = run_atomic_attempt(
        task="demo", seed=0, mode="eval", agent_mode="maestro",
    )

    assert row["agent_mode"] == "maestro"
    assert row["verdict"] == "infra"
    assert row["proposal_decision"] == "NO_PROPOSAL"


def test_suite_threads_agent_mode_into_payload_journal_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from roborsi.evaluation import suite

    monkeypatch.setattr(
        suite,
        "select_libero_short_tasks",
        lambda _backend, _requested=None: ["libero_goal/0"],
    )
    monkeypatch.setattr(
        suite,
        "_runtime_fingerprint",
        lambda _backend: {"roborsi_commit": "test", "backend": "libero-pro"},
    )
    seen_modes: list[str] = []

    def fake_attempt(payload: dict) -> list[dict]:
        seen_modes.append(str(payload.get("agent_mode")))
        return [{
            "task": "libero_pick_place",
            "task_key": payload["task_key"],
            "sim_task": payload["task_key"],
            "backend": "libero-pro",
            "seed": payload["seed"],
            "run_mode": "eval",
            "agent_mode": payload["agent_mode"],
            "success": True,
            "verdict": "success",
            "status": "terminal",
            "outcome": "capx_predicate_passed",
            "tool_calls": 2,
            "attempt": payload["attempt_start"],
        }]

    monkeypatch.setattr(suite, "_run_suite_attempt", fake_attempt)
    summary = suite.run_libero_short_suite(
        seeds=1,
        workers=1,
        out_dir=tmp_path,
        agent_mode="openeta",
    )

    assert seen_modes == ["openeta"]
    assert summary["agent_mode"] == "openeta"
    manifest = json.loads((tmp_path / "campaign.json").read_text())
    assert manifest["agent_mode"] == "openeta"
    journal_rows = [
        json.loads(line)
        for line in (tmp_path / "episodes.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [row["agent_mode"] for row in journal_rows] == ["openeta"]
    # Resuming the campaign under a different orchestration must refuse.
    with pytest.raises(ValueError, match="agent_mode"):
        suite.run_libero_short_suite(
            seeds=1,
            workers=1,
            out_dir=tmp_path,
            agent_mode="roborsi",
        )


def test_eval_suite_cli_exposes_agent_mode() -> None:
    from roborsi.cli.commands import app

    result = CliRunner().invoke(app, ["eval-suite", "--help"])
    assert result.exit_code == 0, result.output
    assert "--agent-mode" in result.output


def test_suite_pass_through_of_run_suite_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_suite_attempt forwards agent_mode into run_atomic_attempt."""
    from roborsi.evaluation import suite

    captured: dict[str, object] = {}

    def fake_run_atomic_attempt(**kwargs):
        captured.update(kwargs)
        return {
            "verdict": "success",
            "success": True,
            "agent_mode": kwargs["agent_mode"],
        }

    monkeypatch.setattr(suite, "run_atomic_attempt", fake_run_atomic_attempt)
    rows = suite._run_suite_attempt({
        "task_key": "libero_goal/0",
        "atomic": "libero_pick_place",
        "backend": "libero-pro",
        "seed": 1,
        "tool_budget": 9,
        "infra_retries": 0,
        "attempt_start": 1,
        "atomic_compound_enabled": True,
        "agent_mode": "maestro",
    })

    assert captured["agent_mode"] == "maestro"
    assert captured["tool_budget"] == 9
    assert rows[0]["agent_mode"] == "maestro"
