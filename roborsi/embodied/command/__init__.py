"""Command package — manifest + params -> lerobot CLI argv."""

from roborsi.embodied.command.builder import CommandBuilder
from roborsi.embodied.command.helpers import (
    ActionError,
    dataset_path,
    group_arms,
    logs_dir,
    resolve_action_arms,
    resolve_cameras,
    validate_dataset_name,
)

__all__ = [
    "ActionError",
    "CommandBuilder",
    "dataset_path",
    "group_arms",
    "logs_dir",
    "resolve_action_arms",
    "resolve_cameras",
    "validate_dataset_name",
]
