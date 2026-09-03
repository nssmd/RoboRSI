"""LIBERO checkout discovery and import activation."""

from __future__ import annotations

import importlib
import json
import os
import platform
import subprocess
import sys
from ctypes.util import find_library
from pathlib import Path
from typing import Any

from roborsi.embodied.paths import home

CONFIG_SCHEMA = "roborsi.libero_runtime.v1"
ROOT_ENV = "ROBORSI_LIBERO_ROOT"
LEGACY_ROOT_ENV = "LIBERO_PRO_ROOT"
INITDIR_ENV = "ROBORSI_LIBERO_INITDIR"
BDDLDIR_ENV = "ROBORSI_LIBERO_BDDLDIR"
UPSTREAM_CONFIG_ENV = "LIBERO_CONFIG_PATH"


class LiberoRuntimeError(RuntimeError):
    """Raised when the configured LIBERO runtime cannot be imported."""


def config_path() -> Path:
    return home() / "libero.json"


def configure_runtime(
    root: str | Path,
    *,
    initdir: str | Path | None = None,
    bddldir: str | Path | None = None,
) -> dict[str, Any]:
    checkout = validate_checkout(root)
    init_path = _optional_directory(initdir, "LIBERO init-state directory")
    bddl_path = _optional_directory(bddldir, "LIBERO BDDL directory")
    record = {
        "schema": CONFIG_SCHEMA,
        "root": str(checkout),
        "initdir": str(init_path) if init_path else None,
        "bddldir": str(bddl_path) if bddl_path else None,
        "upstream_config": str(_ensure_upstream_config(checkout)),
    }
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    os.environ[ROOT_ENV] = str(checkout)
    if init_path:
        os.environ[INITDIR_ENV] = str(init_path)
    if bddl_path:
        os.environ[BDDLDIR_ENV] = str(bddl_path)
    os.environ[UPSTREAM_CONFIG_ENV] = str(
        Path(record["upstream_config"]).parent
    )
    return record


def load_runtime_config() -> dict[str, Any]:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LiberoRuntimeError(f"invalid LIBERO config JSON: {path}") from exc
    if record.get("schema") != CONFIG_SCHEMA:
        raise LiberoRuntimeError(
            f"unsupported LIBERO config schema in {path}: {record.get('schema')!r}"
        )
    return record


def configured_root() -> Path | None:
    raw = (
        os.environ.get(ROOT_ENV)
        or os.environ.get(LEGACY_ROOT_ENV)
        or load_runtime_config().get("root")
    )
    return validate_checkout(raw) if raw else None


def configured_initdir() -> Path | None:
    raw = os.environ.get(INITDIR_ENV) or load_runtime_config().get("initdir")
    return _optional_directory(raw, "LIBERO init-state directory")


def configured_bddldir() -> Path | None:
    raw = os.environ.get(BDDLDIR_ENV) or load_runtime_config().get("bddldir")
    return _optional_directory(raw, "LIBERO BDDL directory")


def activate_runtime(root: str | Path | None = None) -> Path:
    """Put the configured checkout on ``sys.path`` and import LIBERO."""
    configure_headless_rendering()
    checkout = validate_checkout(root) if root else configured_root()
    if checkout is not None:
        source = str(checkout)
        if source not in sys.path:
            sys.path.insert(0, source)
        os.environ[ROOT_ENV] = source
        upstream_config = _ensure_upstream_config(checkout)
        os.environ[UPSTREAM_CONFIG_ENV] = str(upstream_config.parent)
    elif not _upstream_config_exists():
        raise LiberoRuntimeError(
            "no LIBERO checkout is configured; run `roborsi libero configure "
            "--root /path/to/LIBERO-PRO`"
        )
    initdir = configured_initdir()
    if initdir is not None:
        os.environ[INITDIR_ENV] = str(initdir)
    bddldir = configured_bddldir()
    if bddldir is not None:
        os.environ[BDDLDIR_ENV] = str(bddldir)
    importlib.invalidate_caches()
    try:
        module = importlib.import_module("libero.libero")
    except Exception as exc:
        hint = " Install dependencies with `pip install -e '.[libero]'`."
        if checkout is None:
            hint += (
                " Configure a checkout with `roborsi libero configure --root "
                "/path/to/LIBERO-PRO`."
            )
        raise LiberoRuntimeError(
            f"LIBERO import failed: {type(exc).__name__}: {exc}.{hint}"
        ) from exc

    module_file = Path(module.__file__).resolve()
    detected_root = module_file.parents[2]
    if checkout is not None and not module_file.is_relative_to(checkout):
        raise LiberoRuntimeError(
            f"configured LIBERO root is {checkout}, but Python imported "
            f"{module_file}"
        )
    return checkout or detected_root


def configure_headless_rendering() -> dict[str, str]:
    """Configure headless MuJoCo rendering without requiring root access."""
    gl = os.environ.setdefault("MUJOCO_GL", "egl").lower()
    if gl != "egl":
        return {"backend": gl}
    os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
    vendor_env = "__EGL_VENDOR_LIBRARY_FILENAMES"
    vendor = os.environ.get(vendor_env)
    if not vendor and platform.system() == "Linux" and find_library("EGL_nvidia"):
        path = home() / "egl-vendor" / "10_nvidia.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "file_format_version": "1.0.0",
                "ICD": {"library_path": "libEGL_nvidia.so.0"},
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        os.environ[vendor_env] = str(path)
        vendor = str(path)
    return {
        "backend": gl,
        "egl_vendor": vendor or "system",
    }


def runtime_status() -> dict[str, Any]:
    status: dict[str, Any] = {
        "config_path": str(config_path()),
        "configured_root": None,
        "configured_initdir": None,
        "configured_bddldir": None,
        "importable": False,
        "rendering": configure_headless_rendering(),
        "versions": {},
    }
    try:
        root = configured_root()
        status["configured_root"] = str(root) if root else None
        initdir = configured_initdir()
        status["configured_initdir"] = str(initdir) if initdir else None
        bddldir = configured_bddldir()
        status["configured_bddldir"] = str(bddldir) if bddldir else None
        active_root = activate_runtime(root)
        status["root"] = str(active_root)
        status["commit"] = _git_revision(active_root)
        status["versions"] = {
            name: _module_version(name)
            for name in ("libero.libero", "robosuite", "mujoco", "numpy", "torch")
        }
        status["importable"] = all(
            value.get("ok") for value in status["versions"].values()
        )
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
    return status


def validate_checkout(root: str | Path) -> Path:
    checkout = Path(root).expanduser().resolve()
    markers = (
        checkout / "setup.py",
        checkout / "libero" / "libero" / "__init__.py",
        checkout / "libero" / "libero" / "bddl_files",
    )
    missing = [str(path.relative_to(checkout)) for path in markers if not path.exists()]
    if missing:
        raise LiberoRuntimeError(
            f"{checkout} is not a LIBERO checkout; missing: {', '.join(missing)}"
        )
    return checkout


def _ensure_upstream_config(checkout: Path) -> Path:
    config_dir = home() / "libero-upstream"
    config_file = config_dir / "config.yaml"
    benchmark_root = checkout / "libero" / "libero"
    datasets = home() / "libero-datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    record = {
        "benchmark_root": str(benchmark_root),
        "bddl_files": str(benchmark_root / "bddl_files"),
        "init_states": str(benchmark_root / "init_files"),
        "datasets": str(datasets),
        "assets": str(benchmark_root / "assets"),
    }
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_file


def _upstream_config_exists() -> bool:
    configured = os.environ.get(UPSTREAM_CONFIG_ENV)
    config_dir = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".libero"
    )
    return (config_dir / "config.yaml").is_file()


def _optional_directory(
    value: str | Path | None,
    label: str,
) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise LiberoRuntimeError(f"{label} does not exist: {path}")
    return path


def _module_version(name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "version": str(getattr(module, "__version__", "unknown")),
        "file": str(getattr(module, "__file__", "") or ""),
    }


def _git_revision(root: Path) -> str | None:
    process = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return process.stdout.strip() or None
