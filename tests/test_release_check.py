from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.release_check import collect_findings

ROOT = Path(__file__).resolve().parents[1]


def test_release_tree_passes_publication_gate() -> None:
    assert collect_findings(ROOT) == []


def test_release_check_cli_runs_without_pythonpath() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "scripts/release_check.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PASS public release checks" in completed.stdout


def test_checkout_cli_wrapper_runs_without_install() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(ROOT / "roborsi"), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Configure, evaluate, and inspect RoboRSI" in completed.stdout


def test_checkout_cli_wrapper_prefers_local_venv(tmp_path: Path) -> None:
    wrapper = tmp_path / "roborsi"
    shutil.copy2(ROOT / "roborsi", wrapper)
    wrapper.chmod(0o755)
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"-c\" ]]; then exit 0; fi\n"
        "printf 'local-venv:%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)

    env = dict(os.environ)
    env.pop("PYTHON", None)
    completed = subprocess.run(
        [str(wrapper), "results", "replay"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == (
        "local-venv:-m roborsi.libero.cli results replay"
    )


def test_release_check_rejects_case_insensitive_legacy_names(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".venv-pyroki",
            ".deps",
            ".pytest_cache",
            ".ruff_cache",
            ".runtime",
            "build",
            "dist",
            "runs",
            "__pycache__",
        ),
    )
    (checkout / "legacy-note.md").write_text(
        "This note still uses " + "MaE" + "sTrO terminology.\n",
        encoding="utf-8",
    )

    findings = collect_findings(checkout)

    assert any("legacy public terminology" in finding for finding in findings)


def test_release_check_rejects_machine_specific_paths(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".venv-pyroki",
            ".deps",
            ".pytest_cache",
            ".ruff_cache",
            ".runtime",
            "build",
            "dist",
            "runs",
            "__pycache__",
        ),
    )
    (checkout / "private-note.md").write_text(
        "Workspace: /" + "data/example-user/project\n",
        encoding="utf-8",
    )

    findings = collect_findings(checkout)

    assert any("machine-specific path" in finding for finding in findings)


def test_release_check_rejects_stale_tool_names(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".venv-pyroki",
            ".deps",
            ".pytest_cache",
            ".ruff_cache",
            ".runtime",
            "build",
            "dist",
            "runs",
            "__pycache__",
        ),
    )
    (checkout / "stale-note.md").write_text(
        "Use verify_" + "holding_visual before transport.\n",
        encoding="utf-8",
    )

    findings = collect_findings(checkout)

    assert any("legacy public terminology" in finding for finding in findings)
