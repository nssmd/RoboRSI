from __future__ import annotations

from pathlib import Path

import pytest

from roborsi_libero.config import (
    ReleaseConfig,
    detect_gpu_devices,
    load_config,
    write_config,
)


def test_default_config_uses_public_gpt_responses_contract(tmp_path: Path) -> None:
    path = tmp_path / "roborsi.yaml"

    write_config(ReleaseConfig.default(repo_root=tmp_path), path)
    config = load_config(path)

    assert config.provider.model == "responses/gpt-5.6-sol"
    assert config.provider.reasoning_effort == "medium"
    assert config.provider.api_key_env == "OPENAI_API_KEY"
    assert config.evaluation.seeds == list(range(10))
    assert config.evaluation.task_count == 120
    assert "secret-value" not in path.read_text(encoding="utf-8")


def test_config_rejects_high_reasoning_and_non_responses_models(tmp_path: Path) -> None:
    path = tmp_path / "roborsi.yaml"
    path.write_text(
        """
schema_version: 1
provider:
  model: other-provider/not-public
  reasoning_effort: high
  api_key_env: OPENAI_API_KEY
simulator:
  root: ./LIBERO
runtime:
  results_root: ./runs
evaluation:
  seeds: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="responses/gpt-5.6-sol"):
        load_config(path)


def test_resolved_environment_contains_no_literal_secret(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)

    env = config.runtime_environment({"OPENAI_API_KEY": "secret-value"})

    assert env["ROBORSI_VLM_MODEL"] == "responses/gpt-5.6-sol"
    assert env["ROBORSI_REASONING_EFFORT"] == "medium"
    assert env["ROBORSI_OPENAI_API_KEY"] == "secret-value"
    assert env["LIBERO_CONFIG_PATH"] == str(config.simulator.config_root)
    assert "secret-value" not in config.model_dump_json()


def test_gpu_detection_prefers_explicit_visible_device_list() -> None:
    devices = detect_gpu_devices(
        {"CUDA_VISIBLE_DEVICES": "3,7"},
        run=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    assert devices == [3, 7]
