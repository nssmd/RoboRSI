"""Simulator backends for RoboRSI.

This package holds ONLY the sim backend implementations (``robotwin/``,
``robocasa/``). The backend-agnostic env contract + registry live in
``roborsi.embodied.agent_loop`` (``env`` / ``registry``); a real-robot
backend is not a sim and registers there too. Import backends via
``from roborsi.embodied.agent_loop import get_backend``.
"""
