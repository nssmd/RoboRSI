from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-pyroki",
    ".remotion",
    "__pycache__",
    "node_modules",
    "out",
    "site-preview",
}


def test_public_tree_has_no_private_runtime_defaults() -> None:
    forbidden = (
        "/mnt" + "/workspace",
        "/data" + "/yijia",
        "copilot" + "-proxy-local",
        "ANTHROPIC" + "_AUTH_TOKEN",
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
        for needle in forbidden:
            assert needle not in text, f"{needle!r} leaked in {path}"
    assert scanned


def test_public_tree_contains_no_excluded_scope_directories() -> None:
    names = {path.name.lower() for path in ROOT.rglob("*") if path.is_dir()}
    assert "pro_long" not in names
    assert "opd" not in names
