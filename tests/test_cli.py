from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from roborsi.libero.cli import app
from roborsi.libero.config import ReleaseConfig, write_config

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


def test_configure_reports_custom_secret_environment_variable(tmp_path: Path) -> None:
    config = tmp_path / "roborsi.yaml"

    result = runner.invoke(
        app,
        [
            "configure",
            "--output",
            str(config),
            "--api-key-env",
            "MY_ROBOT_API_KEY",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Set MY_ROBOT_API_KEY" in result.output


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


def test_skills_list_exposes_migrated_robotwin_profiles() -> None:
    result = runner.invoke(
        app,
        ["skills", "list", "--category", "atomic", "--backend", "robotwin"],
    )

    assert result.exit_code == 0, result.output
    assert "RoboRSI skills (52)" in result.output
    assert "handover_block" in result.output
    assert "stack_blocks_two" in result.output
    assert "requires_robotwin_backend" in result.output


def test_skills_show_reports_runtime_status() -> None:
    result = runner.invoke(app, ["skills", "show", "lift_pot"])

    assert result.exit_code == 0, result.output
    assert "Backend: robotwin" in result.output
    assert "Runtime: requires_robotwin_backend" in result.output
    assert "Parent: robotwin_bimanual" in result.output
    assert "synchronized dual-arm lifting" in result.output


def test_skills_list_includes_all_libero_short_and_long_tasks() -> None:
    result = runner.invoke(
        app,
        ["skills", "list", "--category", "atomic", "--backend", "libero"],
    )

    assert result.exit_code == 0, result.output
    assert "RoboRSI skills (130)" in result.output
    assert "libero_spatial_00" in result.output
    assert "libero_10_09" in result.output


def test_skills_show_reports_long_horizon_task_key() -> None:
    result = runner.invoke(app, ["skills", "show", "libero_10_00"])

    assert result.exit_code == 0, result.output
    assert "Parent: libero_long" in result.output
    assert "Benchmark: libero_10/0" in result.output


def test_visualize_skill_tree_writes_standalone_html(tmp_path: Path) -> None:
    output = tmp_path / "skill-tree.html"

    result = runner.invoke(
        app,
        [
            "visualize",
            "skill-tree",
            "--output",
            str(output),
            "--no-browser",
        ],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert "ROBORSI SELF-EVOLUTION" in output.read_text(encoding="utf-8")


def _write_cli_campaign(tmp_path: Path) -> tuple[Path, Path]:
    results_root = tmp_path / "runs"
    campaign = results_root / "run-cli"
    campaign.mkdir(parents=True)
    (campaign / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "roborsi.libero_short_campaign.v1",
                "created_at": "2026-08-31T10:00:00+00:00",
                "run_id": "run-cli",
                "mode": "adaptive",
                "metric": "task_level_adaptive_pass_at_10",
                "claim_scope": "adaptive_cross_release_campaign",
                "task_count": 120,
                "task_catalog": ["libero_spatial/0"],
                "seeds": list(range(10)),
                "model": "responses/gpt-5.6-sol",
                "reasoning_effort": "medium",
                "controller": "JOINT_POSITION",
                "image_size": 512,
                "horizon": 5000,
                "release_id": "r1",
            }
        ),
        encoding="utf-8",
    )
    (campaign / "state.json").write_text(
        json.dumps(
            {
                "release_history": ["r1"],
                "current_release_id": "r1",
                "completed_seeds": [0],
                "status": "running",
                "solved_tasks": ["libero_spatial/0"],
                "records": [
                    {
                        "task_key": "libero_spatial/0",
                        "seed": 0,
                        "category": "task_success",
                        "simulator_verdict": "task_success",
                        "release_id": "r1",
                        "attempt": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "roborsi.yaml"
    base = ReleaseConfig.default(repo_root=tmp_path)
    write_config(
        base.model_copy(
            update={
                "runtime": base.runtime.model_copy(
                    update={"results_root": results_root}
                )
            }
        ),
        config,
    )
    return config, campaign


def test_runs_list_and_status_show_latest_campaign(tmp_path: Path) -> None:
    config, _ = _write_cli_campaign(tmp_path)

    listed = runner.invoke(app, ["runs", "list", "--config", str(config)])
    status = runner.invoke(app, ["status", "--config", str(config)])

    assert listed.exit_code == 0, listed.output
    assert "run-cli" in listed.output
    assert status.exit_code == 0, status.output
    assert "RUNNING" in status.output
    assert "1/120" in status.output.replace(" ", "")


def test_web_command_writes_campaign_dashboard(tmp_path: Path) -> None:
    config, campaign = _write_cli_campaign(tmp_path)
    output = tmp_path / "campaign.html"

    result = runner.invoke(
        app,
        [
            "web",
            "--config",
            str(config),
            "--run",
            campaign.name,
            "--output",
            str(output),
            "--no-browser",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "run-cli" in output.read_text(encoding="utf-8")
