from __future__ import annotations

from roborsi.libero.catalog import SHORT_TASK_CATALOG
from roborsi.libero.protocol import CampaignState, EpisodeVerdict, schedule_round


def test_short_catalog_contains_exact_official_120_tasks() -> None:
    assert len(SHORT_TASK_CATALOG) == 120
    assert len(set(SHORT_TASK_CATALOG)) == 120
    assert SHORT_TASK_CATALOG[:3] == (
        "libero_spatial/0",
        "libero_spatial/1",
        "libero_spatial/2",
    )
    assert SHORT_TASK_CATALOG[-1] == "libero_90/89"


def test_adaptive_schedule_protects_success_and_retries_prior_failure_next_seed() -> None:
    state = CampaignState.new(mode="adaptive", release_id="r1")
    seed0 = schedule_round(state, seed=0)
    assert len(seed0) == 120

    state.record(
        EpisodeVerdict(
            task_key="libero_spatial/0",
            seed=0,
            category="task_success",
            simulator_verdict="task_success",
            release_id="r1",
        )
    )
    state.record(
        EpisodeVerdict(
            task_key="libero_spatial/1",
            seed=0,
            category="task_failure",
            simulator_verdict="task_failure",
            release_id="r1",
        )
    )

    assert "libero_spatial/0" not in schedule_round(state, seed=1)
    assert "libero_spatial/1" in schedule_round(state, seed=1)
    assert "libero_spatial/0" not in schedule_round(state, seed=0)
    assert "libero_spatial/1" not in schedule_round(state, seed=0)


def test_infrastructure_record_is_retained_but_does_not_consume_task_seed() -> None:
    state = CampaignState.new(mode="adaptive", release_id="r1")
    state.record(
        EpisodeVerdict(
            task_key="libero_goal/0",
            seed=0,
            category="provider_failure",
            simulator_verdict=None,
            release_id="r1",
        )
    )

    assert "libero_goal/0" in schedule_round(state, seed=0)
    assert state.infrastructure_excluded == 1


def test_fixed_mode_freezes_self_evolution_and_release_identity() -> None:
    state = CampaignState.new(mode="fixed", release_id="release-fixed")

    assert state.selfevo_frozen is True
    assert state.release_history == ["release-fixed"]
    state.begin_release("release-other")
    assert state.release_history == ["release-fixed"]


def test_episode_verdict_rejects_unknown_category() -> None:
    import pytest

    with pytest.raises(ValueError, match="category"):
        EpisodeVerdict(
            task_key="libero_goal/0",
            seed=0,
            category="unknown",  # type: ignore[arg-type]
            simulator_verdict=None,
            release_id="r1",
        )
