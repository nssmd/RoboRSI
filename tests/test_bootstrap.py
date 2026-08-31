from __future__ import annotations

from pathlib import Path

from scripts.bootstrap import LIBERO_COMMIT, PYROKI_COMMIT, bootstrap_commands


def test_core_dev_setup_does_not_install_runtime_extra(tmp_path: Path) -> None:
    commands = bootstrap_commands(
        repo_root=tmp_path,
        python="python3",
        core_only=True,
        with_dev=True,
    )

    assert ["python3", "-m", "venv", str(tmp_path / ".venv")] in commands
    assert [
        str(tmp_path / ".venv/bin/python"),
        "-m",
        "pip",
        "install",
        "-e",
        ".[dev]",
    ] in commands
    assert all(".[runtime,dev]" not in command for row in commands for command in row)


def test_bootstrap_core_only_is_idempotent_and_skips_simulator_clone(tmp_path: Path) -> None:
    commands = bootstrap_commands(
        repo_root=tmp_path,
        python="python3",
        core_only=True,
        with_dev=True,
    )
    rendered = [" ".join(command) for command in commands]

    assert any("-m venv" in command for command in rendered)
    assert any("-e .[dev]" in command for command in rendered)
    assert not any("-e .[runtime,dev]" in command for command in rendered)
    assert not any("git clone" in command for command in rendered)
    assert any("--replay-only" in command for command in rendered)


def test_full_bootstrap_pins_the_public_libero_revision(tmp_path: Path) -> None:
    commands = bootstrap_commands(
        repo_root=tmp_path,
        python="python3",
        core_only=False,
        with_dev=False,
    )
    rendered = [" ".join(command) for command in commands]

    assert len(LIBERO_COMMIT) == 40
    assert any("Lifelong-Robot-Learning/LIBERO.git" in command for command in rendered)
    assert any(LIBERO_COMMIT in command for command in rendered)
    assert len(PYROKI_COMMIT) == 40
    assert any("chungmin99/pyroki.git" in command for command in rendered)
    assert any(PYROKI_COMMIT in command for command in rendered)
    assert any(".venv-pyroki" in command and "-m venv" in command for command in rendered)
    assert any("configure_libero.py" in command for command in rendered)
    assert any("services start" in command for command in rendered)
    assert any("-e .[runtime]" in command for command in rendered)
