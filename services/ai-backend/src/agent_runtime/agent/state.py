"""Compatibility module for `agent_runtime.execution.state`."""

import sys as _sys

import agent_runtime.execution.state as _execution_state

_sys.modules[__name__] = _execution_state
