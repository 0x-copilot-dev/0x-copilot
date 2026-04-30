"""Compatibility module for `agent_runtime.delegation.subagents.definitions`."""

import sys as _sys

import agent_runtime.delegation.subagents.definitions as _delegation_subagents_definitions

_sys.modules[__name__] = _delegation_subagents_definitions
