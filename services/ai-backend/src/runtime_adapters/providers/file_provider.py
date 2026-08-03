"""File-native storage backend — the ``single_user_desktop`` default.

JSONL session folders plus a content-addressed object store; see
``runtime_adapters.file``. The provider stays a thin shim so the file stack
(and its sqlite catalog index) is imported only when this backend is selected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids an import cycle
    from agent_runtime.settings import RuntimeSettings
    from runtime_adapters.factory import RuntimePorts


def build_ports(settings: "RuntimeSettings", *, role: str = "api") -> "RuntimePorts":
    """Compose the file-native port surface.

    ``role`` is a connection-identity concept; a single-writer local store has
    none, so it is accepted for provider-signature uniformity and ignored.
    """

    from runtime_adapters.factory import build_file_ports

    return build_file_ports(settings)
