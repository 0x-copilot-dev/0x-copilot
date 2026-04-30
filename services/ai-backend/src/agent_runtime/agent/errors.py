"""Compatibility module for `agent_runtime.execution.errors`."""

import sys as _sys

import agent_runtime.execution.errors as _execution_errors

_sys.modules[__name__] = _execution_errors
