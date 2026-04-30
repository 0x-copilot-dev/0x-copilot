"""Compatibility package for legacy `agent_runtime.api.*` imports.

New code should import FastAPI composition from `runtime_api`, concrete test
adapters from `runtime_adapters`, and runtime producer ports/services from the
specific `agent_runtime.api` submodules.
"""

__all__: list[str] = []
