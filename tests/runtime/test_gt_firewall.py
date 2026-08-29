from __future__ import annotations

import ast
from pathlib import Path


def test_visible_libero_policies_do_not_read_hidden_success_or_object_state() -> None:
    root = Path(__file__).resolve().parents[2] / "src/roborsi/embodied/skills"
    policy_files = [
        *root.glob("base/*/libero/policy.py"),
        *root.glob("base/_lib/libero/*.py"),
        *root.glob("atomic/libero_*/*/policy.py"),
    ]
    assert policy_files
    forbidden_attrs = {"check_success", "region_box", "parsed_problem", "goal_state"}
    forbidden_strings = {"check_task", "sim_ground_truth", "object_pose"}
    for path in policy_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_attrs, f"{node.attr} in {path}:{node.lineno}"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                lowered = node.value.lower()
                for forbidden in forbidden_strings:
                    assert forbidden not in lowered, f"{forbidden} in {path}:{node.lineno}"
