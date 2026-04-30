"""Compatibility imports for runtime API schemas.

New code should import request/response schemas from `runtime_api.schemas` and
event-domain contracts from `agent_runtime.events` as those modules mature.
"""

from runtime_api.schemas import *  # noqa: F401,F403
