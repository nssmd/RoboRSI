"""Canonical LIBERO short task catalog."""

from __future__ import annotations

SHORT_SUITES: tuple[tuple[str, int], ...] = (
    ("libero_spatial", 10),
    ("libero_object", 10),
    ("libero_goal", 10),
    ("libero_90", 90),
)

SHORT_TASK_CATALOG: tuple[str, ...] = tuple(
    f"{suite}/{task_id}"
    for suite, count in SHORT_SUITES
    for task_id in range(count)
)


def validate_short_catalog(tasks: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    catalog = tuple(str(task) for task in tasks)
    if len(catalog) != len(set(catalog)):
        raise ValueError("task catalog contains duplicates")
    if len(catalog) == 120 and set(catalog) != set(SHORT_TASK_CATALOG):
        missing = sorted(set(SHORT_TASK_CATALOG) - set(catalog))
        extra = sorted(set(catalog) - set(SHORT_TASK_CATALOG))
        raise ValueError(f"invalid 120-task LIBERO short catalog: missing={missing} extra={extra}")
    if not catalog:
        raise ValueError("task catalog must not be empty")
    return catalog


def suite_for(task_key: str) -> str:
    return str(task_key).split("/", 1)[0]
