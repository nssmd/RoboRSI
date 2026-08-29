from __future__ import annotations

from pathlib import Path

from roborsi_libero.services import pyroki_command, service_status


def test_pyroki_command_uses_isolated_environment_and_configured_port(tmp_path: Path) -> None:
    command, env = pyroki_command(tmp_path, port=5559)

    assert command == [
        str(tmp_path / ".venv-pyroki/bin/python"),
        str(tmp_path / "scripts/pyroki_ik_server.py"),
    ]
    assert env["ROBORSI_PYROKI_PORT"] == "5559"


def test_missing_service_reports_stopped_without_deleting_state(tmp_path: Path) -> None:
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    (runtime / "services.json").write_text(
        '{"pyroki":{"pid":99999999,"port":5559,"status":"running"}}',
        encoding="utf-8",
    )

    status = service_status(tmp_path)

    assert status.running is False
    assert status.pid == 99999999
    assert (runtime / "services.json").is_file()
