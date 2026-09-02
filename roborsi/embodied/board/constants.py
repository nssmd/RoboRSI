"""Constants for session state, commands, and episode phases."""

from enum import StrEnum

# Sim per-step channel: sync sim producers publish inner tool call/result
# events here (board.publish_sync) → trace_bridge.attach_sim_bridge translates
# them into live_trace by chat_id. Not a WebSocket channel (no WS_TYPES entry).
CH_SIM_STEP = "sim.step"


class SessionState(StrEnum):
    IDLE = "idle"
    PREPARING = "preparing"
    CALIBRATING = "calibrating"
    TELEOPERATING = "teleoperating"
    RECORDING = "recording"
    REPLAYING = "replaying"
    INFERRING = "inferring"
    ERROR = "error"


class Command(StrEnum):
    SAVE_EPISODE = "save_episode"
    DISCARD_EPISODE = "discard_episode"
    SKIP_RESET = "skip_reset"
    STOP = "stop"
    CONFIRM = "confirm"
    RECALIBRATE = "recalibrate"


class EpisodePhase(StrEnum):
    RECORDING = "recording"
    SAVING = "saving"
    RESETTING = "resetting"
    STOPPING = "stopping"
    DISCARDING = "discarding"
