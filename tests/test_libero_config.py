from __future__ import annotations

import json
from pathlib import Path

from scripts.configure_libero import write_libero_config


def test_libero_config_is_generated_noninteractively_from_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "LIBERO"
    benchmark = checkout / "libero/libero"
    for name in ("bddl_files", "init_files", "assets"):
        (benchmark / name).mkdir(parents=True)
    (checkout / "libero/datasets").mkdir(parents=True)
    config_root = tmp_path / "libero-config"

    path = write_libero_config(checkout, config_root)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path == config_root / "config.yaml"
    assert payload["benchmark_root"] == str(benchmark.resolve())
    assert payload["bddl_files"] == str((benchmark / "bddl_files").resolve())
    assert payload["init_states"] == str((benchmark / "init_files").resolve())
    assert payload["assets"] == str((benchmark / "assets").resolve())
