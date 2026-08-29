from __future__ import annotations

from types import SimpleNamespace

from roborsi.embodied.agent_loop import rollout
from roborsi.embodied.agent_loop.env import Observation


class FakeEnv:
    backend_name = "libero"

    def __init__(self) -> None:
        self.predicate_calls = 0
        self.vlm_finished = False

    def take_snapshot(self) -> Observation:
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
