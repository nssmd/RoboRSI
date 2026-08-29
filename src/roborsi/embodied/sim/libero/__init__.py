"""LIBERO short simulator backend."""

from roborsi.embodied.sim.libero.adapter import LiberoProBackend, LiberoProEnv

LiberoBackend = LiberoProBackend
LiberoEnv = LiberoProEnv

__all__ = ["LiberoBackend", "LiberoEnv", "LiberoProBackend", "LiberoProEnv"]
