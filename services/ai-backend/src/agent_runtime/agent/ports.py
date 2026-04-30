"""Compatibility module for `agent_runtime.execution.ports`."""

import sys as _sys

import agent_runtime.execution.ports as _execution_ports

_sys.modules[__name__] = _execution_ports
