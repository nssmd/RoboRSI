"""Provider-agnostic VLM tool-loop layer (no sim dependencies).

Houses the reusable pieces of the RoboTwin agent loop: LLM call/retry/provider
helpers, message construction + provider-format conversion, prompt / tool-spec
builders, and the static config + prompt-text constants. A real-robot agent
loop can reuse this layer unchanged; only the sim tool implementations live in
roborsi.embodied.sim.robotwin.
"""

from __future__ import annotations

from roborsi.embodied.agent_loop.config import (
    DEFAULT_MODEL,
    SYSTEM_PROMPT_LEGACY,
    _POINT_SYSTEM_PROMPT,
    _RULES,
    _SHORTLIST_ALWAYS,
)
from roborsi.embodied.agent_loop.env import (
    Backend,
    BackendUnavailable,
    Env,
    Observation,
    Rollout,
    Step,
)
from roborsi.embodied.agent_loop.registry import (
    get_backend,
    list_backends,
    register,
)
from roborsi.embodied.agent_loop.messages import (
    _append_image,
    _assistant_tool_calls_msg,
    _convert_messages_to_anthropic,
    _initial_messages,
    _sanitize_tool_pairing,
    _summarize_old_trace,
)
from roborsi.embodied.agent_loop.prompt_tools import (
    SYSTEM_PROMPT,
    _build_status_check_prompt,
    _build_tool_specs,
    _build_tools_block,
    _dispatch_meta_tool,
    _maybe_shortlist_skills,
    _PLUGIN_CACHE,
    _system_prompt,
    _try_load_plugin_dispatcher,
)
from roborsi.embodied.agent_loop.vlm_io import (
    _anthropic_call_with_tools,
    _azure_bearer_token,
    _call_vlm,
    _call_vlm_image,
    _call_vlm_no_tools,
    _call_vlm_tools,
    _image_dims,
    _openai_call_with_tools,
    _parse_json,
    _parse_tool_call,
    _patch_array_items,
    _retry_litellm,
    _sanitize_openai_tool,
    _vlm_complete_openai,
    _wrap_anthropic_response,
)

__all__ = [
    "DEFAULT_MODEL", "SYSTEM_PROMPT", "SYSTEM_PROMPT_LEGACY",
    "_POINT_SYSTEM_PROMPT", "_RULES", "_SHORTLIST_ALWAYS",
    "Backend", "BackendUnavailable", "Env", "Observation", "Rollout", "Step",
    "get_backend", "list_backends", "register",
    "_append_image", "_assistant_tool_calls_msg",
    "_convert_messages_to_anthropic", "_initial_messages",
    "_sanitize_tool_pairing", "_summarize_old_trace",
    "_build_status_check_prompt", "_build_tool_specs", "_build_tools_block",
    "_dispatch_meta_tool", "_maybe_shortlist_skills", "_PLUGIN_CACHE",
    "_system_prompt", "_try_load_plugin_dispatcher",
    "_anthropic_call_with_tools", "_azure_bearer_token", "_call_vlm",
    "_call_vlm_image", "_call_vlm_no_tools", "_call_vlm_tools", "_image_dims",
    "_openai_call_with_tools", "_parse_json", "_parse_tool_call",
    "_patch_array_items", "_retry_litellm", "_sanitize_openai_tool",
    "_vlm_complete_openai", "_wrap_anthropic_response",
]
