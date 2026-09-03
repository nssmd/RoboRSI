from __future__ import annotations

from types import SimpleNamespace

import torch

from roborsi.embodied.sim.libero import adapter, runtime


def test_perturbation_suite_maps_to_base_init_folder() -> None:
    assert (
        adapter.LiberoProBackend._base_init_problem_folder("libero_goal_lan")
        == "libero_goal"
    )
    assert (
        adapter.LiberoProBackend._base_init_problem_folder(
            "libero_object_swap"
        )
        == "libero_object"
    )


def test_missing_perturb_init_uses_shipped_base_state(
    monkeypatch,
    tmp_path,
) -> None:
    checkout = tmp_path / "LIBERO-PRO"
    path = (
        checkout
        / "libero"
        / "libero"
        / "init_files"
        / "libero_goal"
        / "demo.pruned_init"
    )
    path.parent.mkdir(parents=True)
    expected = torch.arange(6)
    torch.save(expected, path)
    bench = SimpleNamespace(
        tasks=[
            SimpleNamespace(
                problem_folder="libero_goal_lan",
                init_states_file="demo.pruned_init",
            )
        ],
        get_task_init_states=lambda _task_id: (_ for _ in ()).throw(
            AssertionError("base-file fallback should resolve first")
        ),
    )
    monkeypatch.delenv("ROBORSI_LIBERO_INITDIR", raising=False)
    monkeypatch.setattr(runtime, "configured_initdir", lambda: None)
    monkeypatch.setattr(runtime, "configured_root", lambda: checkout)

    states = adapter.LiberoProBackend._load_init_states(bench, 0)

    assert torch.equal(states, expected)
