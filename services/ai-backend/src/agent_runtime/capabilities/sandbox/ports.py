"""Substitution boundaries for the remote sandbox capability.

Runtime code depends on these protocols, never on a provider SDK type. Every
future provider (AgentCore, Daytona, Modal, Runloop, Vercel, E2B) implements
``SandboxProviderPort`` and passes the same conformance suite; the runtime
orchestration does not change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    ManagedSandboxSession,
    SandboxArtifactPublication,
    SandboxCreateRequest,
    SandboxIsolationAttestation,
    SandboxLifecycleRecord,
    SandboxPatchImportRequest,
    SandboxRunRequest,
    SandboxUsageAttribution,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol
    from agent_runtime.capabilities.sandbox.remote_execution_service import (
        ActiveSandbox,
    )
    from agent_runtime.capabilities.sandbox.workspace_transfer import RawSnapshotEntry
    from agent_runtime.capabilities.sandbox.cleanup_store import SandboxCleanupSchedule


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


@runtime_checkable
class SandboxCleanupStorePort(Protocol):
    """Durable provider-teardown duty store used by file-native recovery."""

    async def schedule(
        self, record: "SandboxCleanupSchedule"
    ) -> "SandboxCleanupSchedule":
        """Persist an immutable cleanup obligation before session persistence."""
        ...

    async def get(self, operation_id: str) -> "SandboxCleanupSchedule | None":
        """Return one durable teardown obligation."""
        ...

    async def transition(
        self,
        *,
        record: "SandboxCleanupSchedule",
        expected_transition_no: int,
    ) -> "SandboxCleanupSchedule":
        """Advance a duty with compare-and-swap semantics."""
        ...

    async def list_pending(
        self, *, limit: int = 100
    ) -> tuple["SandboxCleanupSchedule", ...]:
        """List pending durable teardown obligations."""
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


@dataclass(frozen=True)
class SandboxProcessOutput:
    """Raw provider command result before D3 redaction and preview bounding."""

    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    truncated: bool = False


@dataclass(frozen=True)
class SandboxDownloadedFile:
    """One provider file stream. Bytes are never placed in durable events."""

    path: str
    chunks: AsyncIterator[bytes]


@dataclass(frozen=True)
class SandboxPatchCollection:
    """Verified provider listing used to construct a canonical patch manifest."""

    result_entries: Mapping[str, "RawSnapshotEntry"]
    directories: tuple[str, ...] = ()
    moves: Mapping[str, str] = field(default_factory=dict)
    complete: bool = True


@runtime_checkable
class SandboxSnapshotContentPort(Protocol):
    """Authorized exact-byte reader for input artifact refs.

    C3 provides a snapshot assembled from a versioned overlay. The coordinator
    never accepts a local path, broker handle, or mutable mount as a substitute.
    """

    async def open(self, ref: ArtifactRef) -> AsyncIterator[bytes]:
        """Open one authorized immutable snapshot blob."""
        ...


@runtime_checkable
class SandboxRuntimePort(Protocol):
    """Provider-independent execution/transfer surface used by D3."""

    async def upload(
        self,
        *,
        active: "ActiveSandbox",
        request: SandboxRunRequest,
        source: SandboxSnapshotContentPort,
    ) -> int:
        """Transfer and verify the approved snapshot; return uploaded bytes."""
        ...

    async def execute(
        self, *, active: "ActiveSandbox", command: str
    ) -> SandboxProcessOutput:
        """Execute exactly one approved command in the active session."""
        ...

    async def download(
        self,
        *,
        active: "ActiveSandbox",
        paths: tuple[str, ...],
    ) -> tuple[SandboxDownloadedFile, ...]:
        """Return exact-byte deliverable streams for approved virtual paths."""
        ...


@runtime_checkable
class SandboxPatchCollectorPort(Protocol):
    """Provider-specific post-run workspace enumeration, isolated from C3."""

    async def collect(
        self, *, active: "ActiveSandbox", request: SandboxRunRequest
    ) -> SandboxPatchCollection:
        """Return a complete verified listing or mark the collection incomplete."""
        ...


@runtime_checkable
class SandboxArtifactPublisherPort(Protocol):
    """A2-backed exact-byte artifact publication boundary."""

    async def publish(
        self,
        *,
        publication: SandboxArtifactPublication,
        chunks: AsyncIterator[bytes],
    ) -> ArtifactRef:
        """Persist one bounded stream and return a digest-matching artifact ref."""
        ...


@runtime_checkable
class SandboxUsageMeterPort(Protocol):
    """Once-only provider usage attribution keyed by sandbox operation id."""

    async def record_once(self, attribution: SandboxUsageAttribution) -> None:
        """Durably record usage, treating an identical operation retry as a no-op."""
        ...


@runtime_checkable
class SandboxPatchImportPort(Protocol):
    """C3 handoff: import a complete patch into an overlay, never host files."""

    async def import_patch(self, request: SandboxPatchImportRequest) -> str:
        """Return an opaque overlay revision ref after validation/staging."""
        ...
