#!/usr/bin/env python3
"""Expose the pinned LIBERO checkout through the active Python environment."""

from __future__ import annotations

import argparse
import site
import subprocess
import sys
from pathlib import Path


def install_libero_checkout(checkout: Path) -> Path:
    root = Path(checkout).expanduser().resolve()
    package = root / "libero/libero/__init__.py"
    if not package.is_file():
        raise FileNotFoundError(f"invalid LIBERO checkout: missing {package}")
    candidates = [Path(path) for path in site.getsitepackages()]
    if not candidates:
        raise RuntimeError("Python environment has no writable site-packages")
    target = candidates[0] / "roborsi-libero-checkout.pth"
    target.write_text(str(root) + "\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import libero.libero; "
                "from libero.libero import benchmark; "
                "assert benchmark.get_benchmark_dict()"
            ),
        ],
        check=True,
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libero-root", type=Path, required=True)
    args = parser.parse_args()
    print(install_libero_checkout(args.libero_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
