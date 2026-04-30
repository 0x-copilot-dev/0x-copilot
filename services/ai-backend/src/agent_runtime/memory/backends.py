"""Compatibility module for `agent_runtime.context.memory.backends`."""

import sys as _sys

import agent_runtime.context.memory.backends as _context_memory_backends

_sys.modules[__name__] = _context_memory_backends
