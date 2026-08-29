from __future__ import annotations

import os
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
    assert "Configure, evaluate, and inspect roborsi" in completed.stdout
