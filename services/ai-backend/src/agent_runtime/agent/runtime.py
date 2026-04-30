"""Compatibility module for `agent_runtime.execution.runtime`."""

import sys as _sys

import agent_runtime.execution.runtime as _execution_runtime

_sys.modules[__name__] = _execution_runtime
