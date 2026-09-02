from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.runtime


class FakeEnv:
    backend_name = "libero"

    def __init__(self) -> None:
        self.predicate_calls = 0
        self.vlm_finished = False

    def take_snapshot(self):
        from roborsi.embodied.agent_loop.env import Observation

        return Observation(images={}, state=[0.0], timestamp=0.0)

    def hook_physics_step(self, callback):
        return lambda: None

    def tool_handlers(self):
        return {}

    def check_success(self) -> bool:
        assert self.vlm_finished, "predicate became visible before the tool loop ended"
        self.predicate_calls += 1
        return True


def test_simulator_predicate_runs_once_after_visible_tool_loop(monkeypatch, tmp_path) -> None:
    from roborsi.embodied.agent_loop import rollout

    env = FakeEnv()

    def fake_vlm(model, messages, tools):
        env.vlm_finished = True
        return SimpleNamespace(
            content="visible task appears complete",
            tool_calls=[
                SimpleNamespace(
                    id="done-1",
                    function=SimpleNamespace(name="done", arguments='{"success": true}'),
                )
            ],
        )

    monkeypatch.setattr(rollout, "_call_vlm_tools", fake_vlm)
    monkeypatch.setenv("ROBORSI_COLLECT", "0")
    monkeypatch.setenv("ROBORSI_SELFEVO_FREEZE", "1")

    result = rollout.run_rollout(
        env,
        seed=0,
        task_name="libero_pick_place",
        instruction="place the object in the container",
        expected_on_success="the object is visibly in the container",
        model="responses/gpt-5.6-sol",
        tool_budget=2,
        workdir=tmp_path,
        use_sim_predicate=True,
    )

    assert result.success is True
    assert env.predicate_calls == 1
    assert result.rollout.meta["predicate_check"] is True


def test_top_down_step_requires_ordered_skill_sequence(monkeypatch, tmp_path) -> None:
    from roborsi.embodied.agent_loop import rollout

    env = FakeEnv()
    turns = iter(
        [
            SimpleNamespace(
                content="",
                tool_calls=[
                    SimpleNamespace(
                        id="look-1",
                        function=SimpleNamespace(name="look", arguments="{}"),
                    ),
                    SimpleNamespace(
                        id="grasp-1",
                        function=SimpleNamespace(
                            name="grasp_object",
                            arguments='{"object": "visible object"}',
                        ),
                    ),
                ],
            ),
            SimpleNamespace(
                content="",
                tool_calls=[
                    SimpleNamespace(
                        id="done-1",
                        function=SimpleNamespace(name="done", arguments='{"success": true}'),
                    )
                ],
            ),
        ]
    )
    def fake_vlm(*args, **kwargs):
        response = next(turns)
        if any(call.function.name == "done" for call in response.tool_calls):
            env.vlm_finished = True
        return response

    monkeypatch.setattr(rollout, "_call_vlm_tools", fake_vlm)
    monkeypatch.setattr(
        env,
        "tool_handlers",
        lambda: {
            "look": lambda state, args: ({"ok": True}, state.env.take_snapshot()),
            "grasp_object": lambda state, args: (
                {"ok": True, "grasped": True},
                state.env.take_snapshot(),
            ),
        },
    )
    monkeypatch.setenv("ROBORSI_COLLECT", "0")
    monkeypatch.setenv("ROBORSI_SELFEVO_FREEZE", "1")

    result = rollout.run_rollout(
        env,
        seed=0,
        task_name="libero_pick_place",
        instruction="pick the visible object",
        expected_on_success="the object is visibly held",
        model="responses/gpt-5.6-sol",
        tool_budget=2,
        workdir=tmp_path,
        top_down_plan={
            "steps": [
                {
                    "id": "locate-and-grasp",
                    "goal": "locate then grasp",
                    "skills": ["look", "grasp_object"],
                }
            ]
        },
    )

    assert result.trace[0].get("plan_step_status") is None
    assert result.trace[1]["plan_step_status"] == "completed_visible"
    assert result.rollout.meta["completed_plan_steps"] == ["locate-and-grasp"]
