"""Per-run construction of the ``/workspace/`` Deep Agents backend.

Gated on the desktop capability broker. For each run this seam:

1. reads ``DESKTOP_BROKER_URL`` / ``DESKTOP_BROKER_TOKEN`` from the environment
   (only the desktop supervisor sets these);
2. fetches the run's CURRENT active grant snapshot — path-free ``BrokerGrant``s
   carrying a ``grant_id`` + opaque ``mount`` id + sanitized ``label``, never a
   host path — from the loopback broker;
3. resolves those grants into the mount table (readable mount name → grant id);
4. when at least one mount is WRITABLE (``read_write*``) **and** the handler
   supplied the write triple (a durable snapshot store + a snapshot-event
   emitter), pins the run's grant snapshot via ``/v1/runs/begin`` and threads
   the minted ``run_capability_context`` + store + emitter into the backend so
   the approval-gated write path is live; otherwise builds read-only;
5. hands the mount-bound config to ``build_workspace_backend``, reusing the same
   broker client so a run opens one client.

It returns ``None`` — and the factory composes no ``/workspace/`` route —
whenever broker config is absent (non-desktop / web / postgres / in-memory
images), the broker is unreachable, or the user has granted no folders. That
keeps every non-desktop image byte-identical: no route, dependency stays
``None``. The write path is likewise inert unless a writable grant AND the
store + emitter are present, so a read-only run stays byte-identical too.

Kept in its own module (mirroring :class:`runtime_worker.file_store_wiring.FileStoreWorkerWiring`)
so the run path constructs the workspace backend exactly once, per run, without
leaking desktop-only concerns into the run handler. The desktop capability
package is imported lazily so it never loads on non-desktop images.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from agent_runtime.api.events import RuntimeEventProducer
    from agent_runtime.capabilities.desktop.workspace_backend import (
        WorkspaceMutationSnapshot,
        WorkspaceSnapshotStore,
    )

logger = logging.getLogger(__name__)


class WorkspaceBackendWorkerWiring:
    """Gate + builder for the per-run ``/workspace/`` backend (read-only or write-through).

    ``env`` defaults to ``os.environ`` (via ``WorkspaceBackendConfig.from_env``);
    ``http_client`` defaults to the process-shared broker pool. Both are
    injectable so a test can drive the whole path against an in-memory fake
    broker without touching the environment or the network.

    ``snapshot_store`` + ``snapshot_emitter`` are the write triple's durable
    half, supplied by the worker handler (the object store + a snapshot-event
    emitter). They are only consumed when a writable grant is present; omit them
    (or leave them ``None``) and the backend is built strictly read-only.
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        snapshot_store: WorkspaceSnapshotStore | None = None,
        snapshot_emitter: object | None = None,
    ) -> None:
        self._env = env
        self._http_client = http_client
        self._snapshot_store = snapshot_store
        self._snapshot_emitter = snapshot_emitter

    async def workspace_backend(self) -> object | None:
        """Build the ``/workspace/`` backend for this run, or ``None`` off desktop.

        Fails soft: a broker that is unreachable or returns no active grants
        yields ``None`` rather than raising, so a run never breaks because host
        access happens to be unavailable. A ``/v1/runs/begin`` failure downgrades
        to a read-only backend (host reads survive; writes stay inert) rather
        than breaking the run.
        """

        # Lazy import: the desktop capability package must not load on the
        # web / postgres / in-memory worker images.
        from agent_runtime.capabilities.desktop import (  # noqa: PLC0415
            BrokerClientConfig,
            BrokerError,
            DesktopBrokerClient,
            WorkspaceBackendConfig,
            WorkspaceMountTable,
            build_workspace_backend,
        )

        config = WorkspaceBackendConfig.from_env(env=self._env)
        if not config.broker_base_url or not config.broker_token:
            return None
        client = DesktopBrokerClient(
            BrokerClientConfig(
                base_url=config.broker_base_url,
                token=config.broker_token,
                protocol_version=config.protocol_version,
                timeout_seconds=config.timeout_seconds,
            ),
            http_client=self._http_client,
        )
        try:
            snapshot = await client.grants_snapshot()
        except BrokerError:
            # Diagnostics carry no token, no path, no broker internals.
            logger.debug("workspace_backend.grants_unavailable")
            return None
        mounts = WorkspaceMountTable.from_broker_grants(snapshot.grants)
        if not mounts:
            return None

        run_capability_context = await self._maybe_begin_run(client, mounts)
        if run_capability_context is None:
            # Read-only: no writable grant, no store/emitter, or begin failed.
            return build_workspace_backend(config.with_mounts(mounts), client=client)
        return build_workspace_backend(
            config.with_mounts(mounts),
            client=client,
            run_capability_context=run_capability_context,
            snapshot_store=self._snapshot_store,
            snapshot_emitter=self._snapshot_emitter,
        )

    async def _maybe_begin_run(
        self, client: object, mounts: tuple[object, ...]
    ) -> str | None:
        """Pin the run's grant snapshot and return its context id, or ``None``.

        Returns ``None`` — leaving the backend read-only — when no mount is
        writable, the write triple's store/emitter were not supplied, or the
        broker's ``/v1/runs/begin`` is unavailable (fail-soft: reads still work).
        """
        from agent_runtime.capabilities.desktop import BrokerError  # noqa: PLC0415

        writable = any(getattr(mount, "writable", False) for mount in mounts)
        if (
            not writable
            or self._snapshot_store is None
            or self._snapshot_emitter is None
        ):
            return None
        try:
            binding = await client.runs_begin()  # type: ignore[attr-defined]
        except BrokerError:
            logger.debug("workspace_backend.run_begin_unavailable")
            return None
        return binding.run_capability_context

    @staticmethod
    async def release_backend(backend: object | None) -> None:
        """Release a run's pinned grant snapshot on teardown (``/v1/runs/end``).

        Called from both the run and approval-resume handlers' ``finally`` so a
        finished / failed / cancelled run never leaks its pinned authority.
        Best-effort and ``None``-safe: a read-only backend (no ``aclose``) or a
        ``None`` backend is a no-op, and ``aclose`` itself never raises.
        """
        if backend is None:
            return
        aclose = getattr(backend, "aclose", None)
        if aclose is not None:
            await aclose()


class WorkspaceSnapshotEventEmitter:
    """Durable sink for pre-image references — the write triple's emitter half.

    Called by ``BrokeredWorkspaceBackend`` BEFORE it mutates a host file: it
    persists a ``WORKSPACE_SNAPSHOT_CAPTURED`` event through the SAME
    :class:`~agent_runtime.api.events.RuntimeEventProducer` chokepoint every
    other API-authored event flows through (redaction + presentation projection
    + run sequence cursor). Any failure propagates so the backend fails closed
    and the host file is never mutated without a durable pre-image reference.

    The run record is re-fetched per emit (the handler's cached record may be
    stale by the time a write lands), mirroring the drafts emitter.
    """

    def __init__(
        self,
        *,
        event_producer: RuntimeEventProducer,
        persistence: object,
        org_id: str,
        run_id: str,
    ) -> None:
        self._event_producer = event_producer
        self._persistence = persistence
        self._org_id = org_id
        self._run_id = run_id

    async def __call__(self, record: WorkspaceMutationSnapshot) -> None:
        """Persist the pre-image reference as a ``WORKSPACE_SNAPSHOT_CAPTURED`` event."""
        from agent_runtime.execution.contracts import StreamEventSource  # noqa: PLC0415
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        run = await self._persistence.get_run(  # type: ignore[attr-defined]
            org_id=self._org_id, run_id=self._run_id
        )
        if run is None:  # pragma: no cover — terminal-race fallback
            return
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.WORKSPACE_SNAPSHOT_CAPTURED,
            payload=record.event_payload(),
            summary=record.event_summary(),
            status="completed",
        )


__all__ = ("WorkspaceBackendWorkerWiring", "WorkspaceSnapshotEventEmitter")
