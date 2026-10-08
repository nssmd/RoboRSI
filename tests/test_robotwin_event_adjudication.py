from types import SimpleNamespace

import pytest

from roborsi.embodied.sim.robotwin import adapter


class Scene:
    def __init__(self):
        self.ticks = 0

    def step(self):
        self.ticks += 1
        return f"physics-{self.ticks}"


class Impl:
    def __init__(self):
        self.scene = Scene()
        self.checks = 0
        self.eval_success = False
        self.closed = False

    def check_success(self):
        self.checks += 1
        return self.scene.ticks == 2

    def get_obs(self):
        return {"visible": "camera and proprioception only"}

    def close_env(self):
        self.closed = True


def make_env(task="click_bell"):
    env = adapter.RoboTwinEnv.__new__(adapter.RoboTwinEnv)
    env.task = task
    env._impl = Impl()
    env._last_obs = env._impl.get_obs()
    env._clear_event_adjudication()
    return env


@pytest.mark.parametrize("task", [
    "beat_block_hammer", "click_alarmclock", "click_bell", "handover_mic",
    "place_can_basket", "place_object_basket", "press_stapler",
])
def test_transient_event_is_preserved_only_for_final_adjudication(monkeypatch, task):
    monkeypatch.setenv("ROBORSI_ROBOTWIN_EVENT_ADJUDICATION", "1")
    env = make_env(task)
    before = env._impl.get_obs()
    env._install_event_adjudication()
    assert [env._impl.scene.step() for _ in range(3)] == ["physics-1", "physics-2", "physics-3"]
    assert env._impl.check_success() is False  # contact has ended
    assert env.check_success() is True        # official event was observed
    assert env._event_record["ticks"] == 3
    assert env._impl.eval_success is False    # no early stop / feedback injection
    assert env._impl.get_obs() == before
    assert env._last_obs == before


@pytest.mark.parametrize("task,enabled", [("click_bell", "0"), ("stack_blocks_three", "1")])
def test_other_adjudication_modes_keep_final_state_semantics(monkeypatch, task, enabled):
    monkeypatch.setenv("ROBORSI_ROBOTWIN_EVENT_ADJUDICATION", enabled)
    env = make_env(task)
    env._install_event_adjudication()
    for _ in range(3):
        env._impl.scene.step()
    assert env._impl.checks == 0
    assert env.check_success() is False


def test_reset_does_not_carry_events_or_count_scene_initialization(monkeypatch):
    monkeypatch.setenv("ROBORSI_ROBOTWIN_EVENT_ADJUDICATION", "1")
    monkeypatch.setattr(adapter, "_to_sim_obs", lambda value: value)
    env = make_env()
    env._install_event_adjudication()
    old = env._impl
    for _ in range(3):
        old.scene.step()
    assert env.check_success() is True
    new = Impl()
    new.scene.step()
    new.scene.step()  # initialization contact must not become an episode success
    env._init_impl = lambda seed: new
    assert env.reset(10) == new.get_obs()
    assert env._event_record == {"success": False, "ticks": 0}
    prior_checks = old.checks
    old.scene.step()
    assert old.checks == prior_checks
    new.scene.step()
    assert env.check_success() is False


def test_video_hook_and_event_hook_do_not_duplicate_physics(monkeypatch):
    monkeypatch.setenv("ROBORSI_ROBOTWIN_EVENT_ADJUDICATION", "1")
    env = make_env()
    env._install_event_adjudication()
    frames = []
    unhook_video = env.hook_physics_step(lambda: frames.append(env._impl.scene.ticks))
    for _ in range(3):
        env._impl.scene.step()
    assert frames == [1, 2, 3]
    unhook_video()
    impl = env._impl
    env.close()
    assert impl.closed and env._impl is None
    prior = impl.checks
    impl.scene.step()
    assert impl.checks == prior
    assert env.check_success() is None


def test_predicate_errors_are_not_silently_counted_as_failures(monkeypatch):
    monkeypatch.setenv("ROBORSI_ROBOTWIN_EVENT_ADJUDICATION", "1")
    env = make_env()

    def broken():
        raise RuntimeError("invalid simulator state")

    env._impl.check_success = broken
    env._install_event_adjudication()
    with pytest.raises(RuntimeError, match="invalid simulator state"):
        env._impl.scene.step()
    assert env._event_record["success"] is False
