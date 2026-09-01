from __future__ import annotations

from collections import Counter

from roborsi.embodied.skills import discover, discover_compounds
from roborsi.libero.catalog import SHORT_TASK_CATALOG


def test_skill_hierarchy_covers_published_benchmarks() -> None:
    skills = discover()
    counts = Counter(skill.category for skill in skills)

    assert counts == {
        "base": 35,
        "atomic": 182,
        "task_families": 11,
        "compound": 2,
        "executors": 2,
    }

    atomics = [skill for skill in skills if skill.category == "atomic"]
    libero = [
        skill
        for skill in atomics
        if "libero" in skill.frontmatter["metadata"]["backends"]
    ]
    robotwin = [
        skill
        for skill in atomics
        if "robotwin" in skill.frontmatter["metadata"]["backends"]
    ]
    assert len(libero) == 130
    assert len(robotwin) == 52

    libero_keys = {
        skill.frontmatter["metadata"]["benchmark"]["task_key"]
        for skill in libero
    }
    assert libero_keys == set(SHORT_TASK_CATALOG) | {
        f"libero_10/{task_id}" for task_id in range(10)
    }


def test_atomic_skill_schema_and_parent_links() -> None:
    skills = discover()
    names = {skill.name for skill in skills}

    for skill in skills:
        if skill.category != "atomic":
            continue
        frontmatter = skill.frontmatter
        metadata = frontmatter["metadata"]
        assert frontmatter["kind"] == "atomic"
        assert frontmatter["parent"] in names
        assert frontmatter["domain"]
        assert metadata["backends"]
        assert metadata["runtime_status"]
        assert metadata["benchmark"]["task_key"]
        assert metadata["vlm_prompts"]["instruction"]
        assert metadata["vlm_prompts"]["expected_on_success"]


def test_atomic_prompts_do_not_embed_simulator_truth() -> None:
    forbidden = (
        "check_success",
        "success_predicate",
        "goal_state",
        "object_pose",
        "region_box",
        "sim_ground_truth",
    )

    for skill in discover():
        if skill.category != "atomic":
            continue
        text = skill.path.read_text(encoding="utf-8").lower()
        assert not any(term in text for term in forbidden)


def test_compounds_are_scoped_to_task_families() -> None:
    assert [skill.name for skill in discover_compounds("libero_pick_place")] == [
        "visual_pick_place"
    ]
    assert [skill.name for skill in discover_compounds("libero_long")] == [
        "place_two_in_container"
    ]
