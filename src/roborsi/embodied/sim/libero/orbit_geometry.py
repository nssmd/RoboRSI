"""Pure geometry and visualization helpers for LIBERO orbit observations."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class OrbitFrame:
    name: str
    rgb: np.ndarray
    depth_m: np.ndarray
    camera_position_world: np.ndarray
    camera_to_world_rotation: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float

    def ray_at(self, u: int, v: int) -> tuple[np.ndarray, np.ndarray]:
        height, width = self.depth_m.shape
        if not (0 <= int(u) < width and 0 <= int(v) < height):
            raise ValueError("orbit pixel is outside the image")
        camera_direction = np.asarray(
            [
                (float(u) - float(self.cx)) / float(self.fx),
                (float(v) - float(self.cy)) / float(self.fy),
                1.0,
            ],
            dtype=np.float64,
        )
        world_direction = (
            np.asarray(self.camera_to_world_rotation, dtype=np.float64)
            @ camera_direction
        )
        norm = float(np.linalg.norm(world_direction))
        if not np.isfinite(norm) or norm <= 1e-12:
            raise ValueError("orbit ray direction is invalid")
        return (
            np.asarray(self.camera_position_world, dtype=np.float64).copy(),
            world_direction / norm,
        )

    def world_at(self, u: int, v: int) -> list[float] | None:
        height, width = self.depth_m.shape
        if not (0 <= int(u) < width and 0 <= int(v) < height):
            return None
        depth = float(self.depth_m[int(v), int(u)])
        if not np.isfinite(depth) or depth <= 0.0:
            return None
        camera_point = np.asarray(
            [
                (float(u) - float(self.cx)) * depth / float(self.fx),
                (float(v) - float(self.cy)) * depth / float(self.fy),
                depth,
            ],
            dtype=np.float64,
        )
        world = (
            np.asarray(self.camera_position_world, dtype=np.float64)
            + np.asarray(self.camera_to_world_rotation, dtype=np.float64)
            @ camera_point
        )
        if world.shape != (3,) or not np.all(np.isfinite(world)):
            return None
        return [float(value) for value in world]


def triangulate_rays(
    first_origin: np.ndarray,
    first_direction: np.ndarray,
    second_origin: np.ndarray,
    second_direction: np.ndarray,
) -> tuple[np.ndarray, float] | None:
    p1 = np.asarray(first_origin, dtype=np.float64)
    d1 = np.asarray(first_direction, dtype=np.float64)
    p2 = np.asarray(second_origin, dtype=np.float64)
    d2 = np.asarray(second_direction, dtype=np.float64)
    if any(value.shape != (3,) for value in (p1, d1, p2, d2)):
        return None
    if not all(np.all(np.isfinite(value)) for value in (p1, d1, p2, d2)):
        return None
    d1 /= max(float(np.linalg.norm(d1)), 1e-12)
    d2 /= max(float(np.linalg.norm(d2)), 1e-12)
    cross = float(np.dot(d1, d2))
    denominator = 1.0 - cross * cross
    if denominator <= 1e-8:
        return None
    offset = p1 - p2
    first_t = (cross * float(np.dot(d2, offset)) - float(np.dot(d1, offset))) / denominator
    second_t = (float(np.dot(d2, offset)) - cross * float(np.dot(d1, offset))) / denominator
    if first_t < 0.0 or second_t < 0.0:
        return None
    first_point = p1 + first_t * d1
    second_point = p2 + second_t * d2
    midpoint = (first_point + second_point) / 2.0
    return midpoint, float(np.linalg.norm(first_point - second_point))


def compose_orbit_sheet(
    frames: Iterable[OrbitFrame],
    *,
    tile_size: int = 384,
) -> np.ndarray:
    values = list(frames)
    if not values:
        raise ValueError("at least one orbit frame is required")
    tile = max(32, int(tile_size))
    columns = 2
    rows = int(math.ceil(len(values) / columns))
    sheet = np.zeros((rows * tile, columns * tile, 3), dtype=np.uint8)
    for index, frame in enumerate(values):
        row, column = divmod(index, columns)
        rgb = np.asarray(frame.rgb, dtype=np.uint8)
        resized = cv2.resize(rgb, (tile, tile), interpolation=cv2.INTER_AREA)
        cv2.putText(
            resized,
            frame.name,
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        sheet[row * tile : (row + 1) * tile, column * tile : (column + 1) * tile] = resized
    return sheet
