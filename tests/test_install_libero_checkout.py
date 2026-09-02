from __future__ import annotations

from pathlib import Path

from scripts import install_libero_checkout as installer


def test_install_libero_checkout_writes_active_environment_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "LIBERO"
    (checkout / "libero/libero/__init__.py").parent.mkdir(parents=True)
    (checkout / "libero/libero/__init__.py").write_text("", encoding="utf-8")
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    calls = []

    monkeypatch.setattr(installer.site, "getsitepackages", lambda: [str(site_packages)])
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda command, check: calls.append((command, check)),
    )

    path = installer.install_libero_checkout(checkout)

    assert path == site_packages / "roborsi-libero-checkout.pth"
    assert path.read_text(encoding="utf-8") == str(checkout.resolve()) + "\n"
    assert calls and calls[0][1] is True
