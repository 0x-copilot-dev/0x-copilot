"""Compatibility module for `agent_runtime.capabilities.tools.builtin.load_tool`."""

import sys as _sys

import agent_runtime.capabilities.tools.builtin.load_tool as _capabilities_tools_builtin_load_tool

_sys.modules[__name__] = _capabilities_tools_builtin_load_tool
