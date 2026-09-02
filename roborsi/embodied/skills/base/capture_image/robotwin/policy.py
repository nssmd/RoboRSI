"""base.robotwin.capture_image — RGB(+depth) snapshot from RoboTwin cameras."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run(
    env,
    camera: str = "head_camera",
    with_depth: bool = False,
    save_to: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    if env is None or getattr(env, "_impl", None) is None:
        raise ValueError("capture_image requires an active RoboTwinEnv (after reset)")
    impl = env._impl
    impl._update_render()
    impl.cameras.update_picture()
    rgb_dict = impl.cameras.get_rgb()
    if camera not in rgb_dict:
        raise KeyError(f"camera '{camera}' not in {list(rgb_dict)}")
    rgb = rgb_dict[camera]["rgb"]
    out: dict[str, Any] = {"camera": camera, "rgb": rgb, "shape": list(rgb.shape)}
    if with_depth:
        depth_dict = impl.cameras.get_depth()
        out["depth"] = depth_dict.get(camera, {}).get("depth")
    if save_to:
        path = Path(save_to).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_jpg(path, rgb)
        out["path"] = str(path)
    return out


def _write_jpg(path: Path, rgb) -> None:
    import cv2
    arr = rgb[..., ::-1] if rgb.ndim == 3 else rgb
    cv2.imwrite(str(path), arr)
