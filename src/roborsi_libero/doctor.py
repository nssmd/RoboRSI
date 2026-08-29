"""Fail-closed environment diagnostics for public reproduction."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path

from roborsi_libero.catalog import SHORT_TASK_CATALOG
from roborsi_libero.config import PUBLIC_MODEL, ReleaseConfig


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    required: bool = True


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if check.required)


def _port_ready(host: str, port: int, timeout: float = 0.25) -> bool:
    if port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _result_root_check(path: Path) -> DoctorCheck:
    current = path.expanduser()
    while not current.exists() and current != current.parent:
        current = current.parent
    ok = current.is_dir() and os.access(current, os.W_OK)
    detail = f"writable parent {current}" if ok else f"no writable parent for {path}"
    return DoctorCheck("result directory", ok, detail)


def _provider_check(config: ReleaseConfig) -> DoctorCheck:
    key = os.environ.get(config.provider.api_key_env, "")
    if not key:
        return DoctorCheck(
            "Responses provider",
            False,
            f"set {config.provider.api_key_env} before evaluation",
        )
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=key,
            base_url=config.provider.base_url,
            timeout=min(30.0, config.provider.timeout_s),
        )
        response = client.responses.create(
            model=PUBLIC_MODEL.split("/", 1)[1],
            input="Reply OK",
            max_output_tokens=32,
            reasoning={"effort": "medium"},
        )
        text = str(getattr(response, "output_text", "") or "").strip()
        served = str(getattr(response, "model", "") or "")
        requested = PUBLIC_MODEL.split("/", 1)[1]
        ok = bool(text) and served.split("/", 1)[-1] == requested
        detail = (
            f"served={served or '(missing)'}"
            if ok
            else "empty response or served-model mismatch"
        )
        return DoctorCheck("Responses provider", ok, detail)
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("Responses provider", False, f"{type(exc).__name__}: {exc}")


def run_doctor(
    config: ReleaseConfig,
    *,
    offline: bool = False,
    check_services: bool = True,
    check_simulator: bool = True,
) -> DoctorReport:
    checks: list[DoctorCheck] = []
    config_ok = (
        config.provider.model == PUBLIC_MODEL
        and config.provider.reasoning_effort == "medium"
        and config.integrity.success_source == "posthoc_simulator_predicate"
        and not config.integrity.expose_task_checker
        and not config.integrity.action_success_latch
        and not config.integrity.allow_hidden_object_state
    )
    checks.append(
        DoctorCheck(
            "configuration",
            config_ok,
            "GPT Responses medium; post-hoc simulator adjudication"
            if config_ok
            else "public model or integrity contract changed",
        )
    )
    checks.append(
        DoctorCheck(
            "task catalog",
            len(SHORT_TASK_CATALOG) == 120 and len(set(SHORT_TASK_CATALOG)) == 120,
            "120 unique short tasks",
        )
    )
    if check_simulator:
        required_paths = (
            config.simulator.root / "libero/libero/benchmark",
            config.simulator.root / "libero/libero/envs",
            config.simulator.root / "libero/libero/bddl_files",
            config.simulator.root / "libero/libero/init_files",
        )
        missing = [
            str(path.relative_to(config.simulator.root))
            for path in required_paths
            if not path.is_dir()
        ]
        checks.append(
            DoctorCheck(
                "LIBERO checkout",
                not missing,
                f"ready at {config.simulator.root}"
                if not missing
                else "run ./setup.sh; missing " + ", ".join(missing),
            )
        )
        config_file = config.simulator.config_root / "config.yaml"
        checks.append(
            DoctorCheck(
                "LIBERO path config",
                config_file.is_file(),
                f"ready at {config_file}"
                if config_file.is_file()
                else "run ./setup.sh to generate LIBERO_CONFIG_PATH noninteractively",
            )
        )
    checks.append(_result_root_check(config.runtime.results_root))

    if offline:
        checks.append(DoctorCheck("Responses provider", True, "offline probe skipped"))
    else:
        checks.append(_provider_check(config))

    if check_services and check_simulator:
        pyroki_ok = _port_ready("127.0.0.1", config.services.pyroki_port)
        checks.append(
            DoctorCheck(
                "PyRoKi service",
                pyroki_ok,
                f"127.0.0.1:{config.services.pyroki_port}"
                if pyroki_ok
                else "not listening; run ./roborsi services start",
            )
        )
        if config.services.sam_port:
            sam_ok = _port_ready("127.0.0.1", config.services.sam_port)
            checks.append(
                DoctorCheck(
                    "segmentation service",
                    sam_ok,
                    f"127.0.0.1:{config.services.sam_port}"
                    if sam_ok
                    else "not listening; visual pointing remains available",
                    required=False,
                )
            )
        if config.services.graspgen_port:
            grasp_ok = _port_ready(config.services.graspgen_host, config.services.graspgen_port)
            checks.append(
                DoctorCheck(
                    "GraspGen service",
                    grasp_ok,
                    f"{config.services.graspgen_host}:{config.services.graspgen_port}"
                    if grasp_ok
                    else "not listening; install optional GraspGen service",
                    required=False,
                )
            )
    return DoctorReport(tuple(checks))
