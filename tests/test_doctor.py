from __future__ import annotations

from pathlib import Path

from roborsi.libero.config import ReleaseConfig
from roborsi.libero.doctor import run_doctor


def _fake_libero(root: Path) -> None:
    (root / "libero/libero/benchmark").mkdir(parents=True)
    (root / "libero/libero/envs").mkdir(parents=True)
    (root / "libero/libero/bddl_files").mkdir(parents=True)
    (root / "libero/libero/init_files").mkdir(parents=True)


def test_offline_doctor_passes_config_and_fake_simulator_layout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from roborsi.embodied import agent_loop

    class FakeBackend:
        @staticmethod
        def available():
            return True, ""

        @staticmethod
        def list_tasks():
            from roborsi.libero.catalog import SHORT_TASK_CATALOG

            return list(SHORT_TASK_CATALOG)

    monkeypatch.setattr(agent_loop, "get_backend", lambda name: FakeBackend())
    config = ReleaseConfig.default(repo_root=tmp_path)
    _fake_libero(config.simulator.root)
    config.simulator.config_root.mkdir(parents=True)
    (config.simulator.config_root / "config.yaml").write_text("{}", encoding="utf-8")

    report = run_doctor(config, offline=True, check_services=False)

    assert report.ok
    assert {check.name for check in report.checks} >= {
        "configuration",
        "task catalog",
        "LIBERO checkout",
        "LIBERO runtime import",
        "result directory",
    }


def test_offline_doctor_fails_with_actionable_missing_libero_message(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)

    report = run_doctor(config, offline=True, check_services=False)

    missing = next(check for check in report.checks if check.name == "LIBERO checkout")
    assert not missing.ok
    assert "setup.sh" in missing.detail
    assert not report.ok


def test_replay_only_doctor_does_not_require_simulator_checkout(tmp_path: Path) -> None:
    config = ReleaseConfig.default(repo_root=tmp_path)

    report = run_doctor(
        config,
        offline=True,
        check_services=False,
        check_simulator=False,
    )

    assert report.ok
    assert "LIBERO checkout" not in {check.name for check in report.checks}
