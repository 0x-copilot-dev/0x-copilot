"""Compatibility module for `agent_runtime.delegation.subagents.contracts`."""

import sys as _sys

import agent_runtime.delegation.subagents.contracts as _delegation_subagents_contracts

_sys.modules[__name__] = _delegation_subagents_contracts
