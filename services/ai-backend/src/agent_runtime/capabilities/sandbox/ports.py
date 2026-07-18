"""Substitution boundaries for the remote sandbox capability.

Runtime code depends on these protocols, never on a provider SDK type. Every
future provider (AgentCore, Daytona, Modal, Runloop, Vercel, E2B) implements
``SandboxProviderPort`` and passes the same conformance suite; the runtime
orchestration does not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxCreateRequest,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol


@dataclass(frozen=True)
class SandboxHandle:
    """Live handle to a provisioned provider session.

    ``backend`` is runtime-only and implements the pinned Deep Agents
    ``SandboxBackendProtocol``. It is a plain dataclass (not a Pydantic model)
    precisely so the backend object is never serialized into events, contracts,
    or logs — provider clients and credentials must not leak through
    persistence. ``session`` is the credential-free projection that *is* safe
    to persist.
    """

    session: ManagedSandboxSession
    backend: "SandboxBackendProtocol" = field(repr=False)


@runtime_checkable
class SandboxProviderPort(Protocol):
    """Provider-neutral lifecycle port. One implementation ships in AC7.

    Implementations translate a provider SDK into these operations and MUST be
    substitutable: identical create/status/terminate/enumerate semantics, so
    the conformance suite is provider-independent.
    """

    async def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        """Provision a session and return a live handle. Idempotent on
        ``request.idempotency_key`` — a retry must not create a duplicate paid
        session."""
        ...

    async def status(self, provider_session_ref: str) -> ManagedSandboxSession:
        """Return the current projection for a provider session ref."""
        ...

    async def terminate(self, provider_session_ref: str) -> None:
        """Stop and delete a session. Idempotent — deleting a gone session is a
        no-op, never an error."""
        ...

    async def list_owned_sessions(
        self, owner_tag: str
    ) -> tuple[ManagedSandboxSession, ...]:
        """Enumerate live sessions tagged with ``owner_tag`` (for leak sweeps)."""
        ...


@runtime_checkable
class SandboxSessionStore(Protocol):
    """Durable projection of non-terminal sessions used by the reaper.

    AC7 FOUNDATION ships an in-memory implementation; the postgres/file-store
    projection is a later adapter behind this same port.
    """

    async def upsert(self, session: ManagedSandboxSession) -> None:
        """Insert or update a session projection."""
        ...

    async def get(self, session_id: str) -> ManagedSandboxSession | None:
        """Return a session by id, or ``None``."""
        ...

    async def list_non_terminal(self) -> tuple[ManagedSandboxSession, ...]:
        """Return sessions not yet ``deleted`` (candidates for cleanup)."""
        ...

    async def delete(self, session_id: str) -> None:
        """Remove a session projection."""
        ...


@runtime_checkable
class SandboxEventSink(Protocol):
    """Where lifecycle events go. AC7 FOUNDATION wires an in-memory/list sink;
    the real ``RuntimeEventEnvelope`` projection is deferred to worker wiring."""

    def emit(self, event: "SandboxEvent") -> None:
        """Record one lifecycle event. Must never receive secret material,
        absolute host paths, provider credentials, or file content."""
        ...


@dataclass(frozen=True)
class SandboxEvent:
    """Redaction-safe lifecycle event. Carries provider id and correlation ids,
    never credentials/secrets/absolute-paths/file-content/URL query strings."""

    name: str
    run_id: str
    session_id: str | None = None
    provider: str | None = None
    provider_session_ref: str | None = None
    region: str | None = None
    detail: str | None = None
    at: datetime | None = None
