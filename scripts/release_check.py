#!/usr/bin/env python3
"""Fail-closed checks for the public RoboRSI code repository."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

IGNORED_DIRS = {
    ".deps",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    ".venv-pyroki",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "runs",
}
TEXT_SUFFIXES = {
    "",
    ".cff",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}
PRIVATE_LITERALS = (
    "/mnt" + "/workspace",
    "/data" + "/yijia",
    "copilot" + "-proxy-local",
    "ANTHROPIC" + "_AUTH_TOKEN",
)
LEGACY_NAMES = (
    "Robo" + "Hermes",
    "ROBO" + "HERMES",
    "robo" + "hermes",
    "mae" + "stro",
)
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[opurs]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def iter_files(root: Path):
    for directory, child_dirs, filenames in os.walk(root, topdown=True):
        child_dirs[:] = [
            name
            for name in child_dirs
            if name not in IGNORED_DIRS and not name.endswith(".egg-info")
        ]
        base = Path(directory)
        for filename in filenames:
            if filename == "roborsi.yaml":
                continue
            yield base / filename


def local_markdown_links(text: str) -> list[str]:
    links = []
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        target = target.split("#", 1)[0]
        if target and "://" not in target and not target.startswith("mailto:"):
            links.append(target)
    return links


def collect_findings(root: Path) -> list[str]:
    root = Path(root).resolve()
    findings: list[str] = []
    required = (
        "README.md",
        "REPRODUCING.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
        "CITATION.cff",
        "pyproject.toml",
        "setup.sh",
        "roborsi",
        "configs/default.yaml",
        "evidence/adaptive-pass10-v1/manifest.json",
        "evidence/adaptive-pass10-v1/episodes.jsonl",
        "src/roborsi/__init__.py",
        "src/roborsi_libero/cli.py",
    )
    for relative in required:
        if not (root / relative).is_file():
            findings.append(f"missing required file: {relative}")
    for relative in ("setup.sh", "roborsi", "scripts/bootstrap.py"):
        path = root / relative
        if path.is_file() and not os.access(path, os.X_OK):
            findings.append(f"entrypoint is not executable: {relative}")

    for path in iter_files(root):
        relative = path.relative_to(root)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if relative != Path("scripts/release_check.py"):
            for needle in (*PRIVATE_LITERALS, *LEGACY_NAMES):
                if needle in text:
                    findings.append(f"forbidden public literal {needle!r}: {relative}")
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(f"possible secret in {relative}")
        if path.suffix.lower() == ".md":
            for target in local_markdown_links(text):
                if not (path.parent / target).resolve().exists():
                    findings.append(f"broken local link {target!r}: {relative}")

    names = {
        path.name.lower()
        for path in root.rglob("*")
        if path.is_dir() and not any(part in IGNORED_DIRS for part in path.parts)
    }
    for excluded in ("opd", "pro_long", "long"):
        if excluded in names:
            findings.append(f"excluded scope directory included: {excluded}")

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    if 'name = "roborsi"' not in pyproject:
        findings.append("project name is not roborsi")
    if 'roborsi = "roborsi_libero.cli:app"' not in pyproject:
        findings.append("console script is not roborsi")
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        findings.append("LICENSE is not Apache-2.0")

    manifest_path = root / "evidence/adaptive-pass10-v1/manifest.json"
    episodes_path = root / "evidence/adaptive-pass10-v1/episodes.jsonl"
    if manifest_path.is_file() and episodes_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = [
            json.loads(line)
            for line in episodes_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        if manifest.get("metric") != "task_level_adaptive_pass_at_k":
            findings.append("evidence metric mismatch")
        expected = manifest.get("expected_result") or {}
        if (expected.get("solved_tasks"), expected.get("total_tasks")) != (95, 120):
            findings.append("evidence headline does not match 95/120")
        solved = {
            row["task_key"]
            for row in rows
            if row.get("category") == "task_success"
            and row.get("simulator_verdict") == "task_success"
        }
        if len(solved) != 95:
            findings.append(f"evidence rows replay to {len(solved)}/120")
        if any(row.get("category") == "infrastructure" for row in rows):
            findings.append("compact success bundle contains infrastructure rows")

    return sorted(set(findings))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = collect_findings(root)
    if findings:
        for finding in findings:
            print(f"FAIL {finding}")
        return 1
    print("PASS public release checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
