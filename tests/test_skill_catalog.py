from __future__ import annotations

from collections import Counter

from roborsi.embodied.skills import discover, discover_compounds, get, get_ns
from roborsi.embodied.skills.schema import validate_catalog
from roborsi.libero.catalog import SHORT_TASK_CATALOG


def test_skill_hierarchy_covers_published_benchmarks() -> None:
    skills = discover()
    counts = Counter(skill.category for skill in skills)

    assert counts == {
        "base": 86,
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
    assert sum(
        skill.category == "atomic"
        and "long" in (skill.frontmatter["metadata"].get("tags") or [])
        for skill in skills
    ) == 10
    assert sum(
        skill.category == "base" and skill.namespace == "libero"
        for skill in skills
    ) == 35
    assert sum(
        skill.category == "base" and skill.namespace == "robotwin"
        for skill in skills
    ) == 51

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


def test_skill_catalog_schema_and_visible_boundary() -> None:
    assert validate_catalog(discover()) == []


def test_backend_qualified_base_skills_do_not_collide() -> None:
    libero = get_ns("grasp_object", "libero")
    robotwin = get_ns("grasp_object", "robotwin")

    assert libero is not None
    assert robotwin is not None
    assert libero.path != robotwin.path
    assert libero.reference == "libero/grasp_object"
    assert robotwin.reference == "robotwin/grasp_object"
    assert get("grasp_object") is None
    assert get("robotwin/grasp_object") == robotwin


def test_compounds_are_scoped_to_task_families() -> None:
    assert [skill.name for skill in discover_compounds("libero_pick_place")] == [
        "visual_pick_place"
    ]
    assert [skill.name for skill in discover_compounds("libero_long")] == [
        "place_two_in_container"
    ]
