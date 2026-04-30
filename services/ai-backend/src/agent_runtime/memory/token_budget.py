"""Compatibility module for `agent_runtime.context.memory.token_budget`."""

import sys as _sys

import agent_runtime.context.memory.token_budget as _context_memory_token_budget

_sys.modules[__name__] = _context_memory_token_budget
