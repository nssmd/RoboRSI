"""Immutable public contracts for coherent LIBERO robot sensor frames.

The adapter is the producer. Skill-side perception and robot rendering may
consume these records, but must reject records with ``valid`` false or any
reset / observation / frame-token mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


def _immutable_array(
    value: Any,
    *,
    dtype: Any,
    shape: tuple[int, ...] | None = None,
) -> np.ndarray:
    array = np.array(value, dtype=dtype, copy=True, order="C")
    if shape is not None and array.shape != shape:
        raise ValueError(
            f"expected array shape {shape}, received {array.shape}"
        )
    array.setflags(write=False)
    return array


def _finite_array(
    value: Any,
    *,
    shape: tuple[int, ...],
) -> np.ndarray:
    array = _immutable_array(value, dtype=float, shape=shape)
    if not np.all(np.isfinite(array)):
        raise ValueError("sensor contract array contains non-finite values")
    return array


@dataclass(frozen=True)
class CameraCalibration:
    camera_name: str
    image_height: int
    image_width: int
    intrinsic: np.ndarray
    camera_to_world: np.ndarray
    near_m: float
    far_m: float
    row_order: str
    pixel_order: str
    depth_convention: str


@dataclass(frozen=True)
class RobotJointState:
    observation_generation: int
    arm_joint_names: tuple[str, ...]
    arm_qpos: tuple[float, ...]
    gripper_joint_names: tuple[str, ...]
    gripper_qpos: tuple[float, ...]
    base_position_world: np.ndarray
    base_quaternion_wxyz: np.ndarray


@dataclass(frozen=True)
class KnownRobotGeometry:
    reset_generation: int
    model_identity: str
    robot_model_xml: str
    root_body: str
    arm_joint_names: tuple[str, ...]
    gripper_joint_names: tuple[str, ...]
    visual_geom_names: tuple[str, ...]
    contact_geom_names: tuple[str, ...]


@dataclass(frozen=True)
class CoherentSensorFrame:
    frame_token: str
    reset_generation: int
    observation_generation: int
    observation_timestamp: float | None
    captured_at: float
    rgb: np.ndarray | None
    depth_m: np.ndarray | None
    calibration: CameraCalibration
    robot_state: RobotJointState
    valid: bool
    invalid_reason: str | None


def make_camera_calibration(
    *,
    camera_name: str,
    image_height: int,
    image_width: int,
    intrinsic: Any,
    camera_to_world: Any,
    near_m: float,
    far_m: float,
    row_order: str,
    pixel_order: str,
    depth_convention: str,
) -> CameraCalibration:
    if image_height <= 0 or image_width <= 0:
        raise ValueError("camera dimensions must be positive")
    near = float(near_m)
    far = float(far_m)
    if not np.isfinite(near) or not np.isfinite(far) or near <= 0 or far <= near:
        raise ValueError("camera near/far planes are invalid")
    return CameraCalibration(
        camera_name=str(camera_name),
        image_height=int(image_height),
        image_width=int(image_width),
        intrinsic=_finite_array(intrinsic, shape=(3, 3)),
        camera_to_world=_finite_array(camera_to_world, shape=(4, 4)),
        near_m=near,
        far_m=far,
        row_order=str(row_order),
        pixel_order=str(pixel_order),
        depth_convention=str(depth_convention),
    )


def make_robot_joint_state(
    *,
    observation_generation: int,
    arm_joint_names: tuple[str, ...],
    arm_qpos: tuple[float, ...],
    gripper_joint_names: tuple[str, ...],
    gripper_qpos: tuple[float, ...],
    base_position_world: Any,
    base_quaternion_wxyz: Any,
) -> RobotJointState:
    arm_names = tuple(str(name) for name in arm_joint_names)
    hand_names = tuple(str(name) for name in gripper_joint_names)
    arm_values = tuple(float(value) for value in arm_qpos)
    hand_values = tuple(float(value) for value in gripper_qpos)
    if len(arm_names) != len(arm_values):
        raise ValueError("arm joint names and qpos lengths differ")
    if len(hand_names) != len(hand_values):
        raise ValueError("gripper joint names and qpos lengths differ")
    if not arm_names:
        raise ValueError("robot arm joint mapping is empty")
    if not np.all(np.isfinite(np.asarray(arm_values, dtype=float))):
        raise ValueError("arm qpos contains non-finite values")
    if not np.all(np.isfinite(np.asarray(hand_values, dtype=float))):
        raise ValueError("gripper qpos contains non-finite values")
    quat = _finite_array(base_quaternion_wxyz, shape=(4,))
    if float(np.linalg.norm(quat)) <= 0.0:
        raise ValueError("robot base quaternion is invalid")
    return RobotJointState(
        observation_generation=int(observation_generation),
        arm_joint_names=arm_names,
        arm_qpos=arm_values,
        gripper_joint_names=hand_names,
        gripper_qpos=hand_values,
        base_position_world=_finite_array(
            base_position_world,
            shape=(3,),
        ),
        base_quaternion_wxyz=quat,
    )


def make_known_robot_geometry(
    *,
    reset_generation: int,
    model_identity: str,
    robot_model_xml: str,
    root_body: str,
    arm_joint_names: tuple[str, ...],
    gripper_joint_names: tuple[str, ...],
    visual_geom_names: tuple[str, ...],
    contact_geom_names: tuple[str, ...],
) -> KnownRobotGeometry:
    xml = str(robot_model_xml)
    if not xml.strip() or "<mujoco" not in xml:
        raise ValueError("robot_model.get_xml() returned invalid MJCF")
    arm_names = tuple(str(name) for name in arm_joint_names)
    if not arm_names:
        raise ValueError("known robot geometry has no arm joints")
    return KnownRobotGeometry(
        reset_generation=int(reset_generation),
        model_identity=str(model_identity),
        robot_model_xml=xml,
        root_body=str(root_body),
        arm_joint_names=arm_names,
        gripper_joint_names=tuple(
            str(name) for name in gripper_joint_names
        ),
        visual_geom_names=tuple(str(name) for name in visual_geom_names),
        contact_geom_names=tuple(str(name) for name in contact_geom_names),
    )


def make_coherent_sensor_frame(
    *,
    frame_token: str,
    reset_generation: int,
    observation_generation: int,
    observation_timestamp: float | None,
    captured_at: float,
    rgb: Any,
    depth_m: Any,
    calibration: CameraCalibration,
    robot_state: RobotJointState,
) -> CoherentSensorFrame:
    reasons: list[str] = []
    rgb_copy = None
    depth_copy = None
    expected_hw = (
        calibration.image_height,
        calibration.image_width,
    )
    if rgb is None:
        reasons.append("rgb_unavailable")
    else:
        try:
            rgb_copy = _immutable_array(rgb, dtype=np.uint8)
            if (
                rgb_copy.ndim != 3
                or rgb_copy.shape[:2] != expected_hw
                or rgb_copy.shape[2] != 3
            ):
                reasons.append("rgb_shape_mismatch")
        except (TypeError, ValueError, OverflowError):
            rgb_copy = None
            reasons.append("rgb_invalid")
    if depth_m is None:
        reasons.append("depth_unavailable")
    else:
        try:
            depth_copy = _immutable_array(depth_m, dtype=float)
            if depth_copy.ndim == 3 and depth_copy.shape[2] == 1:
                depth_copy = _immutable_array(
                    depth_copy[..., 0],
                    dtype=float,
                )
            if depth_copy.ndim != 2 or depth_copy.shape != expected_hw:
                reasons.append("depth_shape_mismatch")
            elif not np.all(np.isfinite(depth_copy)):
                reasons.append("depth_nonfinite")
            elif np.any(depth_copy <= 0.0):
                reasons.append("depth_nonpositive")
        except (TypeError, ValueError, OverflowError):
            depth_copy = None
            reasons.append("depth_invalid")
    if robot_state.observation_generation != int(observation_generation):
        reasons.append("robot_state_generation_mismatch")
    timestamp = (
        None
        if observation_timestamp is None
        else float(observation_timestamp)
    )
    captured = float(captured_at)
    if timestamp is not None and not np.isfinite(timestamp):
        reasons.append("observation_timestamp_invalid")
    if not np.isfinite(captured):
        reasons.append("capture_timestamp_invalid")
    return CoherentSensorFrame(
        frame_token=str(frame_token),
        reset_generation=int(reset_generation),
        observation_generation=int(observation_generation),
        observation_timestamp=timestamp,
        captured_at=captured,
        rgb=rgb_copy,
        depth_m=depth_copy,
        calibration=calibration,
        robot_state=robot_state,
        valid=not reasons,
        invalid_reason=",".join(reasons) if reasons else None,
    )
