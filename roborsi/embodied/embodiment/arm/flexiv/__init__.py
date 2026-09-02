"""Flexiv Rizon robot integration — CLI + LLM tool via sidecar sessions."""

from roborsi.embodied.embodiment.arm.flexiv.binding import FlexivBinding
from roborsi.embodied.embodiment.arm.flexiv.registry import (
    FlexivModel,
    all_models,
    get_model,
)

__all__ = ["FlexivBinding", "FlexivModel", "all_models", "get_model"]
