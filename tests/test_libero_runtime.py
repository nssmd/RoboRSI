from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from roborsi.cli.commands import app
from roborsi.embodied.sim.libero import runtime


def _fake_checkout(root: Path) -> Path:
    (root / "libero" / "libero" / "bddl_files").mkdir(parents=True)
    (root / "libero" / "libero" / "init_files").mkdir()
    (root / "libero" / "libero" / "assets").mkdir()
    (root / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
    (root / "libero" / "libero" / "__init__.py").write_text(
        "__version__ = 'test'\n",
        encoding="utf-8",
    )
    return root


def _clear_libero_modules() -> None:
    for name in list(sys.modules):
        if name == "libero" or name.startswith("libero."):
            sys.modules.pop(name, None)


def test_configure_runtime_writes_roborsi_and_upstream_configs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = _fake_checkout(tmp_path / "LIBERO-PRO")
    home = tmp_path / "home"
    monkeypatch.setenv("ROBORSI_HOME", str(home))
    monkeypatch.delenv(runtime.ROOT_ENV, raising=False)
    monkeypatch.delenv(runtime.BDDLDIR_ENV, raising=False)
    monkeypatch.delenv(runtime.UPSTREAM_CONFIG_ENV, raising=False)

    record = runtime.configure_runtime(checkout)

    assert record["root"] == str(checkout)
    assert runtime.config_path().is_file()
    upstream = Path(record["upstream_config"])
    config = json.loads(upstream.read_text(encoding="utf-8"))
    assert config["bddl_files"] == str(
        checkout / "libero" / "libero" / "bddl_files"
    )
    assert config["init_states"] == str(
        checkout / "libero" / "libero" / "init_files"
    )
    assert (home / "libero-datasets").is_dir()


def test_activate_runtime_imports_configured_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = _fake_checkout(tmp_path / "LIBERO-PRO")
    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(runtime.ROOT_ENV, raising=False)
    monkeypatch.delenv(runtime.BDDLDIR_ENV, raising=False)
    monkeypatch.delenv(runtime.UPSTREAM_CONFIG_ENV, raising=False)
    _clear_libero_modules()

    runtime.configure_runtime(checkout)
    active = runtime.activate_runtime()
    imported = sys.modules["libero.libero"]

    assert active == checkout
    assert Path(imported.__file__).is_relative_to(checkout)
    _clear_libero_modules()
    if str(checkout) in sys.path:
        sys.path.remove(str(checkout))


def test_configure_runtime_persists_external_pro_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checkout = _fake_checkout(tmp_path / "LIBERO-PRO")
    home = tmp_path / "home"
    initdir = tmp_path / "pro-assets" / "init_files"
    bddldir = tmp_path / "pro-assets" / "bddl_files"
    initdir.mkdir(parents=True)
    bddldir.mkdir(parents=True)
    monkeypatch.setenv("ROBORSI_HOME", str(home))
    monkeypatch.delenv(runtime.INITDIR_ENV, raising=False)
    monkeypatch.delenv(runtime.BDDLDIR_ENV, raising=False)

    record = runtime.configure_runtime(
        checkout,
        initdir=initdir,
        bddldir=bddldir,
    )

    assert record["initdir"] == str(initdir)
    assert record["bddldir"] == str(bddldir)
    assert runtime.configured_initdir() == initdir
    assert runtime.configured_bddldir() == bddldir


def test_configure_cli_rejects_non_libero_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path / "home"))
    empty = tmp_path / "empty"
    empty.mkdir()

    result = CliRunner().invoke(
        app,
        ["libero", "configure", "--root", str(empty)],
    )

    assert result.exit_code != 0
    assert "not a LIBERO checkout" in result.output


def test_validate_checkout_requires_benchmark_assets(tmp_path: Path) -> None:
    root = tmp_path / "LIBERO-PRO"
    root.mkdir()
    with pytest.raises(runtime.LiberoRuntimeError, match="missing"):
        runtime.validate_checkout(root)


def test_headless_rendering_creates_user_egl_vendor_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ROBORSI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("MUJOCO_GL", raising=False)
    monkeypatch.delenv("PYOPENGL_PLATFORM", raising=False)
    monkeypatch.delenv("__EGL_VENDOR_LIBRARY_FILENAMES", raising=False)
    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime, "find_library", lambda _name: "libEGL_nvidia.so.0")

    rendering = runtime.configure_headless_rendering()
    vendor = Path(rendering["egl_vendor"])

    assert rendering["backend"] == "egl"
    assert vendor.is_file()
    assert json.loads(vendor.read_text())["ICD"]["library_path"] == (
        "libEGL_nvidia.so.0"
    )
