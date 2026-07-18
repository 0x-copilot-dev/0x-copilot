"""Factory seam for wiring the sandbox capability into the runtime.

``execution/factory.py`` is intentionally NOT edited in this FOUNDATION change.
When the follow-up wires remote execution into the ``CompositeBackend`` route
for ``/workspace``, it calls :func:`build_sandbox_backend` here. Today the
function returns ``None`` whenever the capability is disabled/unconfigured, so a
caller that opts in gets a no-op until a provider is configured and enabled.

The seam returns a :class:`RemoteExecutionService` (the per-process lifecycle
owner), not a backend instance, because a sandbox backend only exists once a run
has an approved snapshot and a provisioned session — the service mints the
policy-wrapped backend per run via ``session_scope``/``create``.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import SandboxProviderId
from agent_runtime.capabilities.sandbox.ports import (
    SandboxEventSink,
    SandboxProviderPort,
    SandboxSessionStore,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    InMemorySandboxSessionStore,
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.remote_execution_service import (
    RemoteExecutionService,
)


def build_sandbox_backend(
    config: RemoteSandboxConfig | None = None,
    *,
    provider_overrides: Mapping[SandboxProviderId, SandboxProviderPort] | None = None,
    session_store: SandboxSessionStore | None = None,
    event_sink: SandboxEventSink | None = None,
) -> RemoteExecutionService | None:
    """Return a wired :class:`RemoteExecutionService`, or ``None`` when disabled.

    Args:
        config: Resolved deployment config; ``None`` reads it from the
            environment. When the capability is inactive this returns ``None``
            (no provider is constructed, no execute path is registered) — there
            is no host fallback.
        provider_overrides: Test hook to bind a fake provider without the real
            SDK; production passes ``None``.
        session_store: Durable session projection; defaults to the in-memory
            store (swap a postgres/file-store adapter in production).
        event_sink: Lifecycle event sink; defaults to a null sink.
    """

    resolved = config if config is not None else RemoteSandboxConfig.from_env()
    if not resolved.is_active:
        return None
    registry = SandboxProviderRegistry.from_config(
        resolved, overrides=provider_overrides
    )
    return RemoteExecutionService(
        registry=registry,
        config=resolved,
        session_store=session_store or InMemorySandboxSessionStore(),
        event_sink=event_sink,
    )
