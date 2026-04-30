"""Compatibility module for `agent_runtime.execution.graph`."""

import sys as _sys

import agent_runtime.execution.graph as _execution_graph

_sys.modules[__name__] = _execution_graph
