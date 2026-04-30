"""Compatibility module for `agent_runtime.capabilities.tools.registry`."""

import sys as _sys

import agent_runtime.capabilities.tools.registry as _capabilities_tools_registry

_sys.modules[__name__] = _capabilities_tools_registry
