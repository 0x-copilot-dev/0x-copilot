"""Compatibility module for `agent_runtime.capabilities.tools.loader`."""

import sys as _sys

import agent_runtime.capabilities.tools.loader as _capabilities_tools_loader

_sys.modules[__name__] = _capabilities_tools_loader
