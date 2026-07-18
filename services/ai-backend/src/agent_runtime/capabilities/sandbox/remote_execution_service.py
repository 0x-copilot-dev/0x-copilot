"""``RemoteExecutionService`` — owns the sandbox lifecycle for a run.

Responsibilities:

* provision a session through the provider registry and wrap the provider
  backend in :class:`PolicyEnforcedSandboxBackend`;
* record a credential-free session projection so a reaper can clean up after a
  worker crash;
* emit redaction-safe lifecycle events carrying the provider id / session ref;
* guarantee teardown via an async context manager (`session_scope`) whose
  ``finally`` terminates the environment even on cancellation or error;
* detect and reap leaked (TTL-expired but still-active) sessions.

Ownership boundary: this service owns *lifecycle*; the provider adapter owns SDK
translation; AC5 owns host files; AC4 owns bytes. It never writes host files and
never constructs a ``LocalShellBackend``.

DEFERRED: applying the output patch to the host is a SEPARATE AC5 broker
operation and is not driven from here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxCreateRequest,
    SandboxError,
    SandboxErrorCode,
    _utcnow,
)
from agent_runtime.capabilities.sandbox.policy_backend import (
    PolicyEnforcedSandboxBackend,
)
from agent_runtime.capabilities.sandbox.ports import (
    SandboxEvent,
    SandboxEventSink,
    SandboxHandle,
    SandboxSessionStore,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    SandboxProviderRegistry,
)


class SandboxEventName:
    """Lifecycle event names (subset of the PRD "Events" catalogue)."""

    PROVISION_STARTED = "sandbox.provision_started"
    PROVISIONED = "sandbox.provisioned"
    CLEANUP_STARTED = "sandbox.cleanup_started"
    CLEANUP_CONFIRMED = "sandbox.cleanup_confirmed"
    CLEANUP_PENDING = "sandbox.cleanup_pending"
    FAILED = "sandbox.failed"


class _NullEventSink:
    """Drop events on the floor (default when no sink is wired)."""

    def emit(self, event: SandboxEvent) -> None:  # noqa: D401 - trivial
        return None


class ActiveSandbox:
    """A provisioned, policy-wrapped sandbox bound to one run."""

    def __init__(
        self, *, session: ManagedSandboxSession, backend: PolicyEnforcedSandboxBackend
    ) -> None:
        self._session = session
        self._backend = backend

    @property
    def session(self) -> ManagedSandboxSession:
        """The credential-free session projection."""

        return self._session

    @property
    def backend(self) -> PolicyEnforcedSandboxBackend:
        """The Deep Agents ``SandboxBackendProtocol`` façade for the agent."""

        return self._backend


class RemoteExecutionService:
    """Provision/teardown/reap orchestration over one selected provider."""

    def __init__(
        self,
        *,
        registry: SandboxProviderRegistry,
        config: RemoteSandboxConfig,
        session_store: SandboxSessionStore,
        event_sink: SandboxEventSink | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._store = session_store
        self._events = event_sink or _NullEventSink()

    async def create(self, request: SandboxCreateRequest) -> ActiveSandbox:
        """Provision a session, record its projection, and wrap it in policy."""

        limits = self._config.resolve_limits()
        self._emit(SandboxEventName.PROVISION_STARTED, request.run_id)
        try:
            handle: SandboxHandle = await self._registry.provider.create(request)
        except SandboxError:
            self._emit(SandboxEventName.FAILED, request.run_id)
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider failure
            self._emit(SandboxEventName.FAILED, request.run_id)
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                "The sandbox provider could not provision a session.",
            ) from exc

        await self._store.upsert(handle.session)
        self._emit(
            SandboxEventName.PROVISIONED,
            request.run_id,
            session=handle.session,
        )
        backend = PolicyEnforcedSandboxBackend(delegate=handle.backend, limits=limits)
        return ActiveSandbox(session=handle.session, backend=backend)

    async def teardown(self, session_id: str) -> ManagedSandboxSession | None:
        """Terminate a session and mark it deleted. Idempotent.

        Records ``cleanup_pending`` if the provider terminate fails so a reaper
        retries; the run is never told the environment is gone when it may not
        be.
        """

        session = await self._store.get(session_id)
        if session is None or session.cleanup_state == "deleted":
            return session
        self._emit(
            SandboxEventName.CLEANUP_STARTED, session.session_id, session=session
        )
        terminating = session.with_state("terminating")
        await self._store.upsert(terminating)
        try:
            await self._registry.provider.terminate(session.provider_session_ref)
        except Exception:  # noqa: BLE001 - defer to reaper on failure
            pending = session.with_state("cleanup_pending")
            await self._store.upsert(pending)
            self._emit(
                SandboxEventName.CLEANUP_PENDING,
                session.session_id,
                session=pending,
            )
            return pending
        deleted = session.with_state("deleted")
        await self._store.upsert(deleted)
        self._emit(
            SandboxEventName.CLEANUP_CONFIRMED, session.session_id, session=deleted
        )
        return deleted

    @asynccontextmanager
    async def session_scope(
        self, request: SandboxCreateRequest
    ) -> AsyncIterator[ActiveSandbox]:
        """Provision → yield → guarantee teardown in ``finally``.

        This is the worker's ``try/finally`` termination boundary: cancel,
        error, or normal completion all converge to a teardown attempt.
        """

        active = await self.create(request)
        try:
            yield active
        finally:
            await self.teardown(active.session.session_id)

    async def detect_leaks(
        self, *, now: datetime | None = None
    ) -> tuple[ManagedSandboxSession, ...]:
        """Return non-terminal sessions whose TTL has elapsed."""

        moment = now or _utcnow()
        sessions = await self._store.list_non_terminal()
        return tuple(
            session
            for session in sessions
            if session.cleanup_state != "deleted" and session.is_expired(now=moment)
        )

    async def reap(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """Terminate every detected leak. Returns the swept session ids.

        Used by the durable reaper after worker death. Uses the same idempotent
        ``teardown`` path, so a duplicate sweep is a no-op.
        """

        leaked = await self.detect_leaks(now=now)
        swept: list[str] = []
        for session in leaked:
            await self.teardown(session.session_id)
            swept.append(session.session_id)
        return tuple(swept)

    def _emit(
        self,
        name: str,
        run_id: str,
        *,
        session: ManagedSandboxSession | None = None,
    ) -> None:
        self._events.emit(
            SandboxEvent(
                name=name,
                run_id=run_id,
                session_id=session.session_id if session else None,
                provider=self._registry.provider_id.value,
                provider_session_ref=session.provider_session_ref if session else None,
                region=self._config.region,
                at=_utcnow(),
            )
        )
