#!/usr/bin/env python3
"""Create an isolated public RoboRSI LIBERO installation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

LIBERO_REPOSITORY = "https://github.com/Lifelong-Robot-Learning/LIBERO.git"
LIBERO_COMMIT = "8f1084e3132a39270c3a13ebe37270a43ece2a01"
PYROKI_REPOSITORY = "https://github.com/chungmin99/pyroki.git"
PYROKI_COMMIT = "388e43e1fc0d0ee382968d3dd72970fd62a0450c"


def bootstrap_commands(
    *,
    repo_root: Path,
    python: str,
    core_only: bool,
    with_dev: bool,
) -> list[list[str]]:
    root = Path(repo_root).resolve()
    venv = root / ".venv"
    venv_python = venv / "bin" / "python"
    executable = venv / "bin" / "roborsi"
    pyroki_venv = root / ".venv-pyroki"
    pyroki_python = pyroki_venv / "bin" / "python"
    if core_only:
        extras = "dev" if with_dev else ""
    else:
        extras = "runtime,dev" if with_dev else "runtime"
    install_target = f".[{extras}]" if extras else "."
    commands = [
        [python, "-m", "venv", str(venv)],
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "wheel"],
        [str(venv_python), "-m", "pip", "install", "-e", install_target],
    ]
    if not core_only:
        libero = root / ".deps" / "LIBERO"
        pyroki = root / ".deps" / "pyroki"
        commands.extend(
            [
                ["git", "clone", LIBERO_REPOSITORY, str(libero)],
                ["git", "-C", str(libero), "checkout", "--detach", LIBERO_COMMIT],
                [str(venv_python), "-m", "pip", "install", "-e", str(libero)],
                [
                    str(venv_python),
                    str(root / "scripts/configure_libero.py"),
                    "--libero-root",
                    str(libero),
                    "--config-root",
                    str(root / ".runtime/libero"),
                ],
                [python, "-m", "venv", str(pyroki_venv)],
                ["git", "clone", PYROKI_REPOSITORY, str(pyroki)],
                ["git", "-C", str(pyroki), "checkout", "--detach", PYROKI_COMMIT],
                [str(pyroki_python), "-m", "pip", "install", "--upgrade", "pip", "wheel"],
                [str(pyroki_python), "-m", "pip", "install", "-e", str(pyroki), "pyzmq"],
            ]
        )
    commands.extend(
        [
            [str(executable), "configure", "--output", str(root / "roborsi.yaml"), "--yes"],
        ]
    )
    if core_only:
        commands.append(
            [
                str(executable),
                "doctor",
                "--config",
                str(root / "roborsi.yaml"),
                "--offline",
                "--no-services",
                "--replay-only",
            ]
        )
    else:
        commands.extend(
            [
                [str(executable), "services", "start"],
                [
                    str(executable),
                    "doctor",
                    "--config",
                    str(root / "roborsi.yaml"),
                    "--offline",
                ],
            ]
        )
    return commands


def _run(command: list[str], *, cwd: Path) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def run_bootstrap(
    *,
    repo_root: Path,
    python: str,
    core_only: bool,
    with_dev: bool,
) -> None:
    root = Path(repo_root).resolve()
    (root / ".deps").mkdir(parents=True, exist_ok=True)
    commands = bootstrap_commands(
        repo_root=root,
        python=python,
        core_only=core_only,
        with_dev=with_dev,
    )
    for command in commands:
        if len(command) >= 4 and command[1:3] == ["-m", "venv"]:
            target = Path(command[3])
            if target.is_dir():
                print(f"= reuse {target}")
                continue
        if command[:2] == ["git", "clone"]:
            target = Path(command[-1])
            if target.is_dir():
                print(f"= reuse {target}")
                continue
        _run(command, cwd=root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--core-only", action="store_true", help="Skip simulator checkout.")
    parser.add_argument("--dev", action="store_true", help="Install test tooling.")
    args = parser.parse_args()
    run_bootstrap(
        repo_root=Path(__file__).resolve().parents[1],
        python=args.python,
        core_only=args.core_only,
        with_dev=args.dev,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
