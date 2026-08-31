from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reproduce_script_generates_replay_and_dashboard(tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("PYTHON", None)
    completed = subprocess.run(
        [
            str(ROOT / "reproduce.sh"),
            "--skip-setup",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (tmp_path / "replay.json").is_file()
    dashboard = (tmp_path / "dashboard.html").read_text(encoding="utf-8")
    assert "95 / 120" in dashboard
    assert "RoboRSI reproduction complete." in completed.stdout
