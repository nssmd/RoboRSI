"""Flexiv model registry — joint count and factory defaults per model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlexivModel:
    """Static spec for a Flexiv arm model."""

    name: str
    dof: int
    max_joint_vel: float     # rad/s, conservative default
    max_cartesian_vel: float # m/s, conservative default
    home_joints: tuple[float, ...]


_MODELS: dict[str, FlexivModel] = {
    "Rizon4": FlexivModel(
        name="Rizon4",
        dof=7,
        max_joint_vel=1.0,
        max_cartesian_vel=0.2,
        home_joints=(0.0, -0.698, 0.0, 1.571, 0.0, 0.698, 0.0),
    ),
    "Rizon4s": FlexivModel(
        name="Rizon4s",
        dof=7,
        max_joint_vel=1.0,
        max_cartesian_vel=0.2,
        home_joints=(0.0, -0.698, 0.0, 1.571, 0.0, 0.698, 0.0),
    ),
    "Rizon10": FlexivModel(
        name="Rizon10",
        dof=7,
        max_joint_vel=1.0,
        max_cartesian_vel=0.2,
        home_joints=(0.0, -0.698, 0.0, 1.571, 0.0, 0.698, 0.0),
    ),
}


def all_models() -> tuple[str, ...]:
    return tuple(_MODELS)


def get_model(name: str) -> FlexivModel:
    if name not in _MODELS:
        raise ValueError(f"Unknown Flexiv model '{name}'. Valid: {list(_MODELS)}")
    return _MODELS[name]
