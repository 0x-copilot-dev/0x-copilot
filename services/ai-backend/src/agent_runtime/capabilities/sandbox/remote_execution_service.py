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
from datetime import UTC, datetime

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
    SandboxCleanupStorePort,
    SandboxEvent,
    SandboxEventSink,
    SandboxHandle,
    SandboxSessionStore,
)
from agent_runtime.capabilities.sandbox.cleanup_store import SandboxCleanupSchedule
from agent_runtime.capabilities.sandbox.provisioning import (
    SandboxGuardedProvisioner,
    _new_remote_execution_service_authority,
)
from agent_runtime.capabilities.sandbox.provider_registry import (
    SandboxProviderRegistry,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    WorkspaceManifestBuilder,
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
        cleanup_store: SandboxCleanupStorePort | None = None,
        event_sink: SandboxEventSink | None = None,
    ) -> None:
        self._registry = registry
        self._config = config
        self._store = session_store
        self._cleanup_store = cleanup_store
        self._events = event_sink or _NullEventSink()
        self._provisioning_authority = _new_remote_execution_service_authority()
        provider = self._registry.provider
        self._guarded_provisioner: SandboxGuardedProvisioner | None = (
            provider if isinstance(provider, SandboxGuardedProvisioner) else None
        )
        if self._guarded_provisioner is not None:
            self._guarded_provisioner.bind_provisioning_authority(
                self._provisioning_authority
            )

    async def create(self, request: SandboxCreateRequest) -> ActiveSandbox:
        """Provision a session, record its projection, and wrap it in policy."""

        limits = self._config.resolve_limits()
        WorkspaceManifestBuilder.verify_manifest(request.snapshot)
        attestation = await self._registry.provider.attest(request)
        if not attestation.satisfies(request.egress):
            raise SandboxError(
                SandboxErrorCode.SANDBOX_ISOLATION_UNVERIFIED,
                "The sandbox provider could not verify required isolation controls.",
            )
        self._emit(SandboxEventName.PROVISION_STARTED, request.run_id)
        try:
            if self._guarded_provisioner is None:
                handle = await self._registry.provider.create(request)
                await self._schedule_cleanup(request=request, session=handle.session)
            else:
                handle = await self._create_guarded(
                    request=request, attestation=attestation
                )
        except SandboxError:
            self._emit(SandboxEventName.FAILED, request.run_id)
            raise
        except Exception as exc:  # noqa: BLE001 - normalize provider failure
            self._emit(SandboxEventName.FAILED, request.run_id)
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                "The sandbox provider could not provision a session.",
            ) from exc

        try:
            await self._store.upsert(handle.session)
        except Exception as exc:  # noqa: BLE001 - persistence boundary is unsafe
            self._emit(SandboxEventName.CLEANUP_PENDING, request.run_id)
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                "The sandbox session could not be recorded safely.",
            ) from exc
        self._emit(
            SandboxEventName.PROVISIONED,
            request.run_id,
            session=handle.session,
        )
        backend = PolicyEnforcedSandboxBackend(delegate=handle.backend, limits=limits)
        return ActiveSandbox(session=handle.session, backend=backend)

    async def teardown(
        self, session_id: str, *, operation_id: str | None = None
    ) -> ManagedSandboxSession | None:
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
        await self._mark_cleanup_cleaned(operation_id)
        self._emit(
            SandboxEventName.CLEANUP_CONFIRMED, session.session_id, session=deleted
        )
        return deleted

    async def cleanup_provider_ref(
        self, *, run_id: str, provider_session_ref: str, operation_id: str | None = None
    ) -> bool:
        """Best-effort recovery cleanup when only durable lifecycle state remains.

        After a worker crash the in-process session projection may be gone, but
        the lifecycle store still has the opaque provider reference.  Recovery
        is allowed to terminate that resource; it must never replay execution.
        ``False`` is an honest cleanup-pending result rather than a success.
        """

        self._emit(SandboxEventName.CLEANUP_STARTED, run_id)
        try:
            await self._registry.provider.terminate(provider_session_ref)
        except Exception:  # noqa: BLE001 - janitor will retry a failed cleanup
            self._emit(SandboxEventName.CLEANUP_PENDING, run_id)
            return False
        self._emit(SandboxEventName.CLEANUP_CONFIRMED, run_id)
        await self._mark_cleanup_cleaned(operation_id)
        return True

    async def cleanup_provisioning_reservation(
        self, *, run_id: str, owner_marker: str, operation_id: str | None = None
    ) -> bool:
        """Recover a pre-bind durable duty without replaying execution.

        A worker can die after the provider creates a container but before the
        provider ref is durably bound.  Guarded providers enumerate only the
        exact operation marker persisted in the reservation and terminate
        those resources; normal execution is never retried here.
        """

        guarded = self._guarded_provisioner
        if guarded is None:
            return False
        self._emit(SandboxEventName.CLEANUP_STARTED, run_id)
        try:
            await guarded.recover_provisioning(owner_marker)
        except Exception:  # noqa: BLE001 - janitor keeps the durable duty pending
            self._emit(SandboxEventName.CLEANUP_PENDING, run_id)
            return False
        self._emit(SandboxEventName.CLEANUP_CONFIRMED, run_id)
        await self._mark_cleanup_cleaned(operation_id)
        return True

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

    async def _schedule_cleanup(
        self, *, request: SandboxCreateRequest, session: ManagedSandboxSession
    ) -> None:
        if self._cleanup_store is None:
            return
        schedule = SandboxCleanupSchedule(
            operation_id=request.operation_id,
            run_id=request.run_id,
            provider_session_ref=session.provider_session_ref,
            snapshot_digest=request.snapshot.manifest_sha256,
        )
        try:
            await self._cleanup_store.schedule(schedule)
        except Exception as exc:  # noqa: BLE001 - never leave untracked provider state
            cleaned = await self._terminate_untracked(
                run_id=request.run_id,
                provider_session_ref=session.provider_session_ref,
                operation_id=request.operation_id,
            )
            if cleaned:
                raise SandboxError(
                    SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                    "The sandbox session could not be recorded safely.",
                ) from exc
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "The sandbox session cleanup could not be recorded safely.",
            ) from exc

    async def _create_guarded(
        self,
        *,
        request: SandboxCreateRequest,
        attestation,
    ) -> SandboxHandle:
        """Reserve file-native recovery before guarded provisioning is reachable."""

        if self._cleanup_store is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Guarded sandbox provisioning requires a durable cleanup store.",
            )
        guarded = self._guarded_provisioner
        if guarded is None:  # pragma: no cover - guarded by caller
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Guarded sandbox provisioning is unavailable.",
            )
        reservation = SandboxCleanupSchedule(
            operation_id=request.operation_id,
            run_id=request.run_id,
            owner_marker=guarded.cleanup_owner_marker(request),
            snapshot_digest=request.snapshot.manifest_sha256,
            state="provisioning",
        )
        try:
            reservation = await self._cleanup_store.schedule(reservation)
        except Exception as exc:  # noqa: BLE001 - must precede any provider call
            raise SandboxError(
                SandboxErrorCode.SANDBOX_PROVISION_FAILED,
                "The sandbox cleanup reservation could not be recorded safely.",
            ) from exc
        if reservation.state == "cleaned":
            raise SandboxError(
                SandboxErrorCode.SANDBOX_LIFECYCLE_CONFLICT,
                "The sandbox cleanup reservation is already terminal.",
            )
        capability = self._provisioning_authority.mint(
            request=request,
            attestation=attestation,
            cleanup=reservation,
        )
        if reservation.state == "cleanup_pending":
            return await guarded.provision_with_capability(capability)
        try:
            handle = await guarded.provision_with_capability(capability)
        except Exception:
            # The reservation is durable before the provider is ever called.
            # Preserve it for reaping even when the provider response is lost.
            await self.cleanup_provisioning_reservation(
                run_id=request.run_id,
                owner_marker=reservation.owner_marker or "",
                operation_id=request.operation_id,
            )
            raise
        try:
            bound = reservation.model_copy(
                update={
                    "provider_session_ref": handle.session.provider_session_ref,
                    "state": "cleanup_pending",
                    "transition_no": reservation.transition_no + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._cleanup_store.transition(
                record=bound,
                expected_transition_no=reservation.transition_no,
            )
        except Exception as exc:  # noqa: BLE001 - reservation remains reaper-owned
            await self.cleanup_provisioning_reservation(
                run_id=request.run_id,
                owner_marker=reservation.owner_marker or "",
                operation_id=request.operation_id,
            )
            raise SandboxError(
                SandboxErrorCode.SANDBOX_EXECUTION_INDETERMINATE,
                "The sandbox provider session could not be bound to cleanup safely.",
            ) from exc
        return handle

    async def _terminate_untracked(
        self,
        *,
        run_id: str,
        provider_session_ref: str,
        operation_id: str,
    ) -> bool:
        self._emit(SandboxEventName.CLEANUP_STARTED, run_id)
        try:
            await self._registry.provider.terminate(provider_session_ref)
        except Exception:  # noqa: BLE001 - the caller reports indeterminate
            self._emit(SandboxEventName.CLEANUP_PENDING, run_id)
            return False
        await self._mark_cleanup_cleaned(operation_id)
        self._emit(SandboxEventName.CLEANUP_CONFIRMED, run_id)
        return True

    async def _mark_cleanup_cleaned(self, operation_id: str | None) -> None:
        if self._cleanup_store is None or operation_id is None:
            return
        record = await self._cleanup_store.get(operation_id)
        if record is None or record.state == "cleaned":
            return
        await self._cleanup_store.transition(
            record=record.model_copy(
                update={
                    "state": "cleaned",
                    "transition_no": record.transition_no + 1,
                    "updated_at": datetime.now(UTC),
                    "attempts": record.attempts + 1,
                    "error_summary": None,
                }
            ),
            expected_transition_no=record.transition_no,
        )

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
