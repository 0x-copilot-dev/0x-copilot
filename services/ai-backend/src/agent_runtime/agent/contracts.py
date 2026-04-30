"""Compatibility module for `agent_runtime.execution.contracts`."""

import sys as _sys

import agent_runtime.execution.contracts as _execution_contracts

_sys.modules[__name__] = _execution_contracts
