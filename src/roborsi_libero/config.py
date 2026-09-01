"""One validated configuration contract for setup, evaluation, and UI."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

PUBLIC_MODEL = "responses/gpt-5.6-sol"


def detect_gpu_devices(
    environ: Mapping[str, str] | None = None,
    *,
    run=subprocess.run,
) -> list[int]:
    source = os.environ if environ is None else environ
    if "CUDA_VISIBLE_DEVICES" in source:
        raw = str(source.get("CUDA_VISIBLE_DEVICES") or "").strip()
        if not raw or raw == "-1":
            return []
        values = [value.strip() for value in raw.split(",") if value.strip()]
        if all(value.isdigit() for value in values):
            return [int(value) for value in values]
        return list(range(len(values)))
    try:
        result = run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line.strip()) for line in result.stdout.splitlines() if line.strip().isdigit()]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = PUBLIC_MODEL
    reasoning_effort: Literal["medium"] = "medium"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_s: float = Field(default=120.0, gt=0)

    @model_validator(mode="before")
    @classmethod
    def reject_non_public_provider(cls, value: object) -> object:
        if isinstance(value, dict):
            model = str(value.get("model", PUBLIC_MODEL))
            effort = str(value.get("reasoning_effort", "medium"))
            if model != PUBLIC_MODEL or effort != "medium":
                raise ValueError(
                    f"public release requires {PUBLIC_MODEL} with reasoning_effort=medium"
                )
        return value

    @model_validator(mode="after")
    def enforce_public_contract(self) -> "ProviderConfig":
        if self.model != PUBLIC_MODEL:
            raise ValueError(f"public release requires model {PUBLIC_MODEL}")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("provider.base_url must be an HTTP(S) URL")
        if not self.api_key_env or " " in self.api_key_env:
            raise ValueError("provider.api_key_env must name one environment variable")
        return self


class SimulatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path = Path(".deps/LIBERO")
    config_root: Path = Path(".runtime/libero")
    mujoco_gl: Literal["egl", "osmesa"] = "egl"
    controller: Literal["JOINT_POSITION"] = "JOINT_POSITION"
    image_size: int = Field(default=512, ge=128, le=2048)
    horizon: int = Field(default=5000, ge=1)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results_root: Path = Path("runs")
    workers: int = Field(default=0, ge=0)
    gpu_devices: list[int] = Field(default_factory=list)
    python: str = ""


class ServiceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pyroki_port: int = Field(default=5559, ge=1, le=65535)
    sam_port: int = Field(default=0, ge=0, le=65535)
    graspgen_host: str = "127.0.0.1"
    graspgen_port: int = Field(default=0, ge=0, le=65535)


class EvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["adaptive", "fixed"] = "adaptive"
    seeds: list[int] = Field(default_factory=lambda: list(range(10)))
    task_count: Literal[120] = 120
    tool_budget: int = Field(default=120, ge=1)
    retain_all_artifacts: Literal[True] = True

    @model_validator(mode="after")
    def validate_protocol(self) -> "EvaluationConfig":
        if self.seeds != list(range(10)):
            raise ValueError("full LIBERO short protocol requires ordered seeds 0..9")
        return self


class IntegrityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success_source: Literal["posthoc_simulator_predicate"] = "posthoc_simulator_predicate"
    expose_task_checker: Literal[False] = False
    action_success_latch: Literal[False] = False
    allow_hidden_object_state: Literal[False] = False


class ReleaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    simulator: SimulatorConfig = Field(default_factory=SimulatorConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    integrity: IntegrityConfig = Field(default_factory=IntegrityConfig)

    @classmethod
    def default(cls, *, repo_root: Path) -> "ReleaseConfig":
        root = Path(repo_root).expanduser().resolve()
        return cls(
            simulator=SimulatorConfig(
                root=root / ".deps" / "LIBERO",
                config_root=root / ".runtime" / "libero",
            ),
            runtime=RuntimeConfig(
                results_root=root / "runs",
                gpu_devices=detect_gpu_devices(),
            ),
        )

    def runtime_environment(self, source: Mapping[str, str] | None = None) -> dict[str, str]:
        source_env = os.environ if source is None else source
        key = str(source_env.get(self.provider.api_key_env, ""))
        env = {
            "ROBORSI_VLM_MODEL": self.provider.model,
            "ROBORSI_PERCEPTION_MODEL": self.provider.model,
            "ROBORSI_REASONING_EFFORT": self.provider.reasoning_effort,
            "ROBORSI_OPENAI_BASE_URL": self.provider.base_url,
            "ROBORSI_OPENAI_API_KEY": key,
            "ROBORSI_LIBERO_ROOT": str(self.simulator.root),
            "LIBERO_CONFIG_PATH": str(self.simulator.config_root),
            "ROBORSI_LIBERO_RES": str(self.simulator.image_size),
            "ROBORSI_LIBERO_HORIZON": str(self.simulator.horizon),
            "ROBORSI_LIBERO_CONTROLLER": self.simulator.controller,
            "ROBORSI_PYROKI_PORT": str(self.services.pyroki_port),
            "ROBORSI_SAM3_PORT": str(self.services.sam_port),
            "GRASPGEN_HOST": self.services.graspgen_host,
            "GRASPGEN_PORT": str(self.services.graspgen_port),
            "MUJOCO_GL": self.simulator.mujoco_gl,
        }
        if self.runtime.gpu_devices:
            env["ROBORSI_GPU_LIST"] = ",".join(str(device) for device in self.runtime.gpu_devices)
        return env


def _resolve_path(value: Path, base: Path) -> Path:
    path = value.expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def load_config(path: Path | str) -> ReleaseConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {config_path}") from exc
    try:
        config = ReleaseConfig.model_validate(payload)
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    base = config_path.parent
    return config.model_copy(
        update={
            "simulator": config.simulator.model_copy(
                update={
                    "root": _resolve_path(config.simulator.root, base),
                    "config_root": _resolve_path(config.simulator.config_root, base),
                }
            ),
            "runtime": config.runtime.model_copy(
                update={"results_root": _resolve_path(config.runtime.results_root, base)}
            ),
        }
    )


def write_config(config: ReleaseConfig, path: Path | str) -> Path:
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return config_path
