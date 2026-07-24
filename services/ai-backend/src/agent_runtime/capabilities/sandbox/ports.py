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
    SandboxIsolationAttestation,
    SandboxLifecycleRecord,
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

    @property
    def isolation_ready(self) -> bool:
        """Whether the adapter can verify every required D3 control.

        This is a construction-time fail-closed gate.  A provider whose SDK
        merely *claims* a sandbox exists but cannot prove isolation and exact
        egress enforcement must never produce a model-visible production tool.
        """
        ...

    async def attest(
        self, request: SandboxCreateRequest
    ) -> SandboxIsolationAttestation:
        """Return verified effective isolation and egress controls for launch."""
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


@dataclass(frozen=True)
class SandboxLifecycleAcquisition:
    """Atomic create-or-read result for a sandbox idempotency key."""

    created: bool
    record: SandboxLifecycleRecord


@runtime_checkable
class SandboxLifecycleStore(Protocol):
    """Durable lifecycle authority for replay-safe sandbox execution.

    The unique identity is the operation's server-generated idempotency key.
    A second request with different immutable facts is a conflict, never a
    chance to overwrite history or reuse provider execution state.
    """

    async def acquire(
        self, *, record: SandboxLifecycleRecord
    ) -> SandboxLifecycleAcquisition:
        """Atomically create ``record`` or return an identical prior record."""
        ...

    async def get(self, *, idempotency_key: str) -> SandboxLifecycleRecord | None:
        """Return the exact persisted record for recovery."""
        ...

    async def update(self, *, record: SandboxLifecycleRecord) -> SandboxLifecycleRecord:
        """Persist a validated monotonic state transition."""
        ...

    async def list_recoverable(
        self, *, limit: int = 100
    ) -> tuple[SandboxLifecycleRecord, ...]:
        """List unresolved executions and cleanup-pending resources."""
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
