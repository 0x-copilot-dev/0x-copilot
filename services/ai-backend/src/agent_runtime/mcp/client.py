"""Compatibility module for `agent_runtime.capabilities.mcp.client`."""

import sys as _sys

import agent_runtime.capabilities.mcp.client as _capabilities_mcp_client

_sys.modules[__name__] = _capabilities_mcp_client
