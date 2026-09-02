from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {
    ".git",
    ".deps",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    ".venv-pyroki",
    ".remotion",
    "__pycache__",
    "node_modules",
    "artifacts",
    "dist",
    "out",
    "runs",
    "site-preview",
}


def test_public_tree_has_no_private_runtime_defaults() -> None:
    private_paths = (
        re.compile(r"/data/[A-Za-z0-9._-]+/"),
        re.compile(r"/mnt/workspace/[A-Za-z0-9._-]+/"),
    )
    scanned = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or path.name == "roborsi.yaml"
            or any(part in IGNORED for part in path.parts)
            or path.suffix in {".pyc", ".mp4"}
        ):
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        scanned.append(path)
        for pattern in private_paths:
            assert not pattern.search(text), f"private path leaked in {path}"
    assert scanned


def test_public_tree_contains_no_private_scope_directories() -> None:
    names = {path.name.lower() for path in ROOT.rglob("*") if path.is_dir()}
    assert not names.intersection({"private", "internal-only"})
