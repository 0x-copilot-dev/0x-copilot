"""Compatibility module for `agent_runtime.execution.factory`."""

import sys as _sys

import agent_runtime.execution.factory as _execution_factory

_sys.modules[__name__] = _execution_factory
