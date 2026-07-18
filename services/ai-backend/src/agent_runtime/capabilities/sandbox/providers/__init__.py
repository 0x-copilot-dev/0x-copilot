"""Provider adapters for the remote sandbox capability.

Exactly one adapter ships in AC7 (``langsmith``). Adapters are imported lazily
by the registry so a provider's SDK extra is only required when that provider is
selected. Each adapter implements
:class:`agent_runtime.capabilities.sandbox.ports.SandboxProviderPort` and must
pass the provider-independent conformance suite.
"""

from __future__ import annotations
