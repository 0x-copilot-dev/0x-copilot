"""Per-run construction of the ``/workspace/`` Deep Agents backend.

Gated on the desktop capability broker. For each run this seam:

1. reads ``DESKTOP_BROKER_URL`` / ``DESKTOP_BROKER_TOKEN`` from the environment
   (only the desktop supervisor sets these);
2. fetches the run's CURRENT active grant snapshot — path-free ``BrokerGrant``s
   carrying a ``grant_id`` + opaque ``mount`` id + sanitized ``label``, never a
   host path — from the loopback broker;
3. resolves those grants into the mount table (readable mount name → grant id);
4. hands the mount-bound config to ``build_workspace_backend``, reusing the same
   broker client so a run opens one client.

It returns ``None`` — and the factory composes no ``/workspace/`` route —
whenever broker config is absent (non-desktop / web / postgres / in-memory
images), the broker is unreachable, or the user has granted no folders. That
keeps every non-desktop image byte-identical: no route, dependency stays
``None``. This adapter is permanently read-only; C2 workspace effects are
constructed through their separate staged authority.

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
    )

logger = logging.getLogger(__name__)


class WorkspaceBackendWorkerWiring:
    """Gate + builder for the per-run read-only ``/workspace/`` backend.

    ``env`` defaults to ``os.environ`` (via ``WorkspaceBackendConfig.from_env``);
    ``http_client`` defaults to the process-shared broker pool. Both are
    injectable so a test can drive the whole path against an in-memory fake
    broker without touching the environment or the network.

    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._env = env
        self._http_client = http_client

    async def workspace_backend(self) -> object | None:
        """Build the ``/workspace/`` backend for this run, or ``None`` off desktop.

        Fails soft: a broker that is unreachable or returns no active grants
        yields ``None`` rather than raising, so a run never breaks because host
        reads happen to be unavailable.
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
                service_identity=config.service_identity,
                broker_audience=config.broker_audience,
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

        return build_workspace_backend(config.with_mounts(mounts), client=client)

    @staticmethod
    async def release_backend(backend: object | None) -> None:
        """Compatibility no-op: the read-only backend owns no release handle."""
        del backend


class WorkspaceSnapshotEventEmitter:
    """Emit path-free audit evidence for a historic snapshot record.

    The retired direct workspace writer no longer calls this sink. It remains a
    stable, independently verifiable audit projection for records created
    before the C2 prepared/attested commit path took over. It accepts only the
    redacted snapshot projection; the run capability context is never emitted.
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
        """Write the timeline and signed audit evidence for one historic record."""

        from datetime import datetime, timezone  # noqa: PLC0415

        from agent_runtime.execution.contracts import StreamEventSource  # noqa: PLC0415
        from runtime_adapters.file._audit_manifest import AuditManifest  # noqa: PLC0415
        from runtime_api.schemas import RuntimeApiEventType  # noqa: PLC0415

        run = await self._persistence.get_run(  # type: ignore[attr-defined]
            org_id=self._org_id, run_id=self._run_id
        )
        if run is None:  # pragma: no cover - terminal-race fallback
            return
        await self._event_producer.append_api_event(
            run=run,
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.WORKSPACE_SNAPSHOT_CAPTURED,
            payload=record.event_payload(),
            summary=record.event_summary(),
            status="completed",
        )
        now = datetime.now(timezone.utc)
        await self._persistence.write_audit_log(  # type: ignore[attr-defined]
            event_type=AuditManifest.EVENT_WORKSPACE_WRITE,
            record=AuditManifest.workspace_write_record(
                audit_event_id=(
                    f"workspace_write_{self._org_id}_{self._run_id}_"
                    f"{int(now.timestamp() * 1_000_000)}"
                ),
                org_id=self._org_id,
                user_id=getattr(run, "user_id", None),
                run_id=self._run_id,
                op=record.op,
                mount=record.mount,
                path=record.path,
                object_sha256=record.object_sha256,
                size=record.size,
                created_at=now.isoformat(),
            ),
        )


__all__ = ("WorkspaceBackendWorkerWiring", "WorkspaceSnapshotEventEmitter")
