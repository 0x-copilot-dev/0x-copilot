"""Golden, body-free F8 wire contract shared by backend and ai-backend.

This module deliberately only locates and parses immutable package data.  It
does not implement transport, identity verification, or retry behavior.
"""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable

MCP_CROSS_SERVICE_CONTRACT_VERSION = "f8.1"

_PACKAGE = "copilot_service_contracts"
_FILENAME = "mcp_cross_service_golden_contract.json"

MCP_CROSS_SERVICE_GOLDEN_CONTRACT_PATH: Traversable = files(_PACKAGE).joinpath(
    _FILENAME
)


def load_mcp_cross_service_golden_contract() -> dict[str, object]:
    """Return the versioned F8 backend-to-runtime golden contract."""
    raw = MCP_CROSS_SERVICE_GOLDEN_CONTRACT_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


__all__ = [
    "MCP_CROSS_SERVICE_CONTRACT_VERSION",
    "MCP_CROSS_SERVICE_GOLDEN_CONTRACT_PATH",
    "load_mcp_cross_service_golden_contract",
]
