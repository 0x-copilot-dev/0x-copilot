"""Runtime model admission, memory, and compression."""

from __future__ import annotations

from agent_runtime.context.tool_result_admission import (
    ToolResultAdmission,
    ToolResultAdmissionAdapter,
)

__all__ = [
    "ToolResultAdmission",
    "ToolResultAdmissionAdapter",
    "memory",
]
