"""Coherent public-sensor fixtures for authenticated pickup evidence."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from roborsi.embodied.skills.base._lib.libero import _control, _perception, instance_hold


@pytest.fixture
def authenticated_pickup(monkeypatch):
    # Only sensor/robot adapters are mocked. Reference construction and
    # association retain the production identity, generation and geometry gates.
    mask = np.zeros((32, 32), dtype=bool)
    mask[24:30, 24:30] = True
    support_mask = np.zeros_like(mask)
    support_mask[10:15, 8:13] = True
    robot_mask = np.zeros_like(mask)
    monkeypatch.setattr(_perception, "sam_mask_at_point", lambda rgb, u, v: (support_mask if support_mask[v, u] else mask).copy())
    monkeypatch.setattr(instance_hold, "robot_projection_for_frame",
                        lambda env, frame: SimpleNamespace(robot_mask=robot_mask.copy()))

    class SensorControl:
        def __init__(self, env):
            self.env = env

        def read_pose(self):
            control = getattr(self.env, "test_control", None)
            if control is not None:
                return control.read_pose()
            return np.array([0., 0., 1.]), np.array([0., 0., 0., 1.]), np.zeros(2)

    monkeypatch.setattr(_control, "LiberoControl", SensorControl)

    def make_reference(env, *, source_pixel=(26, 26), object_name="white mug"):
        u, v = source_pixel
        mask[:] = False
        mask[v-2:v+4, u-2:u+4] = True
        env.test_reset_generation = 1
        env.test_instance_visible = True

        def coherent_sensor_frame(camera="agentview"):
            position, quaternion, _ = SensorControl(env).read_pose()
            camera_to_world = np.eye(4)
            rotation = Rotation.from_quat(quaternion).as_matrix()
            camera_to_world[:3, :3] = rotation
            camera_to_world[:3, 3] = position - rotation @ np.array([0., 0., 1.])
            rgb = np.zeros((32, 32, 3), dtype=np.uint8)
            rgb[mask] = 200 if env.test_instance_visible else 0
            depth = np.ones((32, 32))
            rr, cc = np.indices(depth.shape)
            depth[mask] += np.where((rr[mask] + cc[mask]) % 2, 0.02, -0.02)
            depth[support_mask] = getattr(env, "test_support_depth", 1.0)
            return SimpleNamespace(
                valid=True, rgb=rgb, depth_m=depth,
                frame_token="unit-frame", reset_generation=env.test_reset_generation,
                calibration=SimpleNamespace(
                    image_width=32, image_height=32, camera_name=camera,
                    intrinsic=np.array([[100., 0., u + 0.5], [0., 100., v + 0.5], [0., 0., 1.]]),
                    camera_to_world=camera_to_world,
                ),
            )

        env.coherent_sensor_frame = coherent_sensor_frame
        frame = coherent_sensor_frame()
        position, quaternion, _ = SensorControl(env).read_pose()
        reference = instance_hold.build_pickup_reference(
            env, frame=frame, object_name=object_name, source_pixel=source_pixel,
            accepted_cloud=instance_hold.mask_world_cloud(frame, mask),
            grasp_position=position, grasp_quaternion=quaternion,
            object_offset_local=np.zeros(3), identity_verified=True,
        )
        assert reference is not None, "fixture must authenticate through the real pickup builder"
        return reference

    return make_reference
