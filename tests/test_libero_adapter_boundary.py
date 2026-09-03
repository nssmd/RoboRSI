from __future__ import annotations

from types import SimpleNamespace

from roborsi.embodied.sim.libero.adapter import (
    LiberoProEnv,
    _visible_raw_obs,
)


def test_visible_raw_obs_drops_object_state() -> None:
    visible = _visible_raw_obs({
        "robot0_eef_pos": [0.1, 0.2, 0.3],
        "robot0_gripper_qpos": [0.01, -0.01],
        "agentview_image": [[[0, 0, 0]]],
        "agentview_depth": [[0.5]],
        "milk_1_pos": [0.4, 0.2, 0.1],
        "milk_1_quat": [1.0, 0.0, 0.0, 0.0],
    })

    assert "robot0_eef_pos" in visible
    assert "agentview_image" in visible
    assert "milk_1_pos" not in visible
    assert "milk_1_quat" not in visible


def test_step_does_not_query_or_expose_task_success() -> None:
    class Wrapped:
        def __init__(self):
            self.env = SimpleNamespace(
                done=False,
                sim=SimpleNamespace(_render_context_offscreen=None),
            )
            self.check_calls = 0

        def step(self, _action):
            return (
                {
                    "robot0_eef_pos": [0.1, 0.2, 0.3],
                    "milk_1_pos": [0.4, 0.2, 0.1],
                },
                1.0,
                False,
                {"success": True, "task_success": True, "public": "kept"},
            )

        def check_success(self):
            self.check_calls += 1
            raise AssertionError("step must not query the final predicate")

    wrapped = Wrapped()
    env = LiberoProEnv(
        env=wrapped,
        task="libero_object/0",
        init_states=[None],
        instruction="pick the object",
        settle_steps=0,
    )

    step = env.step([0.0] * 8)

    assert wrapped.check_calls == 0
    assert step.done is False
    assert step.reward == 0.0
    assert step.info == {"public": "kept"}
    assert "milk_1_pos" not in env.raw_obs()
