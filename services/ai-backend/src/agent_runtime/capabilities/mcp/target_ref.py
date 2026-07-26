"""Opaque MCP effect-target references shared outside the transport adapter."""

from __future__ import annotations

import re

from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.execution.contracts import RuntimeContract

_TARGET_REF = re.compile(
    r"^mcp-target://([a-z0-9][a-z0-9._-]{0,127})/([a-z0-9][a-z0-9._-]{0,127})$"
)


class McpTargetRef(RuntimeContract):
    """A parsed, server-owned MCP effect target."""

    capability: str
    op: str


class McpTargetRefCodec:
    """Format/parse the opaque target identity accepted by the MCP executor."""

    @classmethod
    def format(cls, *, capability: str, op: str) -> str:
        normalized_capability = OperationDescriptorRegistry.normalize_capability(
            capability
        )
        normalized_op = OperationDescriptorRegistry.normalize(op)
        return f"mcp-target://{normalized_capability}/{normalized_op}"

    @classmethod
    def parse(cls, value: str) -> McpTargetRef:
        match = _TARGET_REF.fullmatch(value)
        if match is None:
            raise ValueError("MCP target reference is invalid")
        return McpTargetRef(capability=match.group(1), op=match.group(2))


__all__ = ("McpTargetRef", "McpTargetRefCodec")
