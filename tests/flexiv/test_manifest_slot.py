from __future__ import annotations

import pytest

from roborsi.embodied.embodiment.arm.flexiv.binding import FlexivBinding
from roborsi.embodied.embodiment.arm.flexiv.manifest_slot import (
    FlexivRegistry,
    get_flexiv_registry_path,
)


def test_registry_add_and_list():
    reg = FlexivRegistry()
    assert reg.list() == []
    binding = reg.add("main", "Rizon4-ABCD", "Rizon4")
    assert isinstance(binding, FlexivBinding)
    assert binding.alias == "main"
    assert [b.to_dict() for b in reg.list()] == [
        {"alias": "main", "sn": "Rizon4-ABCD", "model": "Rizon4"},
    ]


def test_registry_persists_across_instances():
    FlexivRegistry().add("a", "SN-1", "Rizon4")
    reloaded = FlexivRegistry()
    assert [b.alias for b in reloaded.list()] == ["a"]


def test_registry_remove():
    reg = FlexivRegistry()
    reg.add("b", "SN-2", "Rizon4")
    reg.remove("b")
    assert reg.list() == []


def test_registry_duplicate_alias_rejected():
    reg = FlexivRegistry()
    reg.add("dup", "SN-3", "Rizon4")
    with pytest.raises(ValueError, match="already registered"):
        reg.add("dup", "SN-4", "Rizon4")


def test_registry_unknown_model_rejected():
    with pytest.raises(ValueError, match="Unknown Flexiv model"):
        FlexivRegistry().add("x", "SN", "NotARealModel")


def test_registry_path_under_home():
    reg = FlexivRegistry()
    reg.add("p", "SN-P", "Rizon4")
    expected = get_flexiv_registry_path()
    assert expected.exists()
    assert expected.name == "flexiv.json"
