"""Compatibility module for `agent_runtime.delegation.subagents.runner`."""

import sys as _sys

import agent_runtime.delegation.subagents.runner as _delegation_subagents_runner

_sys.modules[__name__] = _delegation_subagents_runner
