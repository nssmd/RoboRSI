"""Agent core module.

Most heavy submodules (loop / context / memory / skills) require Python 3.11+
syntax (StrEnum, ``type X = ...``). The sim-only entry runs in the RoboTwin
conda env (Python 3.10) and only needs ``agent.lifecycle`` for the data
flywheel orchestrator. Keep ``__init__`` empty so importing
``roborsi.agent.lifecycle`` doesn't pull in the 3.11+ modules.
"""

__all__: list[str] = []

