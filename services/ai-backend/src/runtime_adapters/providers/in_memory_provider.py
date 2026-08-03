"""In-memory storage backend — the tests/dev default.

Registered under ``in_memory_async`` with the legacy alias ``in_memory``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids an import cycle
    from agent_runtime.settings import RuntimeSettings
    from runtime_adapters.factory import RuntimePorts


def build_ports(settings: "RuntimeSettings", *, role: str = "api") -> "RuntimePorts":
    """Compose the in-memory port surface.

    ``role`` is a connection-identity concept; an in-process store has none, so
    it is accepted for provider-signature uniformity and ignored.
    """

    from runtime_adapters.factory import RuntimeAdapterFactory

    return RuntimeAdapterFactory.build_in_memory_ports(settings)
