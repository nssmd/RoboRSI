from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from roborsi_libero.cli import app

runner = CliRunner()


def test_configure_creates_one_canonical_yaml(tmp_path: Path) -> None:
    config = tmp_path / "roborsi.yaml"

    result = runner.invoke(app, ["configure", "--output", str(config), "--yes"])

    assert result.exit_code == 0, result.output
    assert config.is_file()
    assert "responses/gpt-5.6-sol" in config.read_text(encoding="utf-8")


def test_configure_accepts_one_command_endpoint_and_worker_overrides(tmp_path: Path) -> None:
    config = tmp_path / "roborsi.yaml"

    result = runner.invoke(
        app,
        [
            "configure",
            "--output",
            str(config),
            "--base-url",
            "http://127.0.0.1:9999/v1",
            "--workers",
            "8",
            "--gpus",
            "0,2",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    text = config.read_text(encoding="utf-8")
    assert "http://127.0.0.1:9999/v1" in text
    assert "workers: 8" in text
    assert "gpu_devices:\n  - 0\n  - 2" in text


def test_eval_dry_run_prints_all_120_tasks(tmp_path: Path) -> None:
    config = tmp_path / "roborsi.yaml"
    runner.invoke(app, ["configure", "--output", str(config), "--yes"])

    result = runner.invoke(
        app,
        ["eval", "libero-short", "--config", str(config), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "120 tasks" in result.output
    assert "adaptive" in result.output.lower()


def test_results_replay_emits_machine_readable_json(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        '{"schema":"roborsi.libero_short_evidence.v1",'
        '"metric":"task_level_adaptive_pass_at_k",'
        '"claim_scope":"test", "k":1,'
        '"task_catalog":["libero_goal/0"],"episodes":"episodes.jsonl"}',
        encoding="utf-8",
    )
    (bundle / "episodes.jsonl").write_text(
        '{"schema":"roborsi.libero_episode.v1","task_key":"libero_goal/0",'
        '"seed":0,"attempt":1,"release_id":"r1","category":"task_success",'
        '"simulator_verdict":"task_success","prompt_tokens":1,'
        '"completion_tokens":1,"total_tokens":2,"vlm_calls":1,"elapsed_s":1}\n',
        encoding="utf-8",
    )
    output = tmp_path / "result.json"

    result = runner.invoke(
        app,
        [
            "results",
            "replay",
            "--manifest",
            str(bundle / "manifest.json"),
            "--json",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"solved_tasks": 1' in output.read_text(encoding="utf-8")


def test_results_replay_defaults_to_packaged_public_bundle() -> None:
    result = runner.invoke(app, ["results", "replay"])

    assert result.exit_code == 0, result.output
    assert "95/120" in result.output.replace(" ", "")
