"""Compatibility module for `agent_runtime.capabilities.mcp.registry`."""

import sys as _sys

import agent_runtime.capabilities.mcp.registry as _capabilities_mcp_registry

_sys.modules[__name__] = _capabilities_mcp_registry
