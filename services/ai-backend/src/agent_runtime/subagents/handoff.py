"""Compatibility module for `agent_runtime.delegation.subagents.handoff`."""

import sys as _sys

import agent_runtime.delegation.subagents.handoff as _delegation_subagents_handoff

_sys.modules[__name__] = _delegation_subagents_handoff
