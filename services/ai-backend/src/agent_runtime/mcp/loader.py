"""Compatibility module for `agent_runtime.capabilities.mcp.loader`."""

import sys as _sys

import agent_runtime.capabilities.mcp.loader as _capabilities_mcp_loader

_sys.modules[__name__] = _capabilities_mcp_loader
