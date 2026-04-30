"""Compatibility module for `agent_runtime.context.memory.contracts`."""

import sys as _sys

import agent_runtime.context.memory.contracts as _context_memory_contracts

_sys.modules[__name__] = _context_memory_contracts
