#!/usr/bin/env python3
"""Write LIBERO's upstream config without an interactive import prompt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_libero_config(checkout: Path, config_root: Path) -> Path:
    root = Path(checkout).expanduser().resolve()
    benchmark = root / "libero/libero"
    datasets = root / "libero/datasets"
    datasets.mkdir(parents=True, exist_ok=True)
    required = {
        "benchmark_root": benchmark,
        "bddl_files": benchmark / "bddl_files",
        "init_states": benchmark / "init_files",
        "datasets": datasets,
        "assets": benchmark / "assets",
    }
    missing = [str(path) for path in required.values() if not path.is_dir()]
    if missing:
        raise FileNotFoundError("invalid LIBERO checkout; missing: " + ", ".join(missing))
    destination = Path(config_root).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "config.yaml"
    path.write_text(
        json.dumps({key: str(value.resolve()) for key, value in required.items()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libero-root", type=Path, required=True)
    parser.add_argument("--config-root", type=Path, required=True)
    args = parser.parse_args()
    print(write_libero_config(args.libero_root, args.config_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
