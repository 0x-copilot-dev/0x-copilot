"""Compatibility module for `agent_runtime.context.memory.policy`."""

import sys as _sys

import agent_runtime.context.memory.policy as _context_memory_policy

_sys.modules[__name__] = _context_memory_policy
