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
images) or the broker is unreachable. That keeps every non-desktop image
byte-identical: no route, dependency stays ``None``. This adapter is permanently
read-only; C2 workspace effects are constructed through their separate staged
authority.

A user who has granted NO folders yet is no longer one of those cases. Host
access is on by default and stays grant-scoped: with zero grants the route still
exists, holding an empty mount table, so a host-absolute path can reach the grant
request instead of falling through to a virtual backend that would answer
``ls /Users/<name>/Downloads`` with an empty listing and a green tick. Nothing is
readable until the user grants it — the affordance exists, the access does not.

Two things come off the broker here, and only one of them is the backend
------------------------------------------------------------------------
:meth:`WorkspaceBackendWorkerWiring.workspace_backend` builds the ``/workspace/``
route. :meth:`WorkspaceBackendWorkerWiring.granted_host_roots` answers a
different and narrower question — *which host folders has the user attached* —
for the deepagents ``FilesystemPermission`` rules and
:class:`~agent_runtime.capabilities.desktop.host_floor.HostFilesystemFloor`.

They are separate because the second question has a lane-independent answer.
``workspace_effect_mode=enforce`` replaces the ``/workspace/`` object with C3's
gateway/tombstone backend, and neither can name a host root (their host-session
projection is path-free by design, and it is C2's private WRITE bootstrap — not
a channel to widen). Reading the roots off that object therefore yielded nothing
and left every attached folder prompting on every read. Asking the broker
instead makes the rule set depend on what the USER granted rather than on which
backend a write-staging mode happened to select.

Kept in its own module (mirroring :class:`runtime_worker.file_store_wiring.FileStoreWorkerWiring`)
so the run path constructs the workspace backend exactly once, per run, without
leaking desktop-only concerns into the run handler. The desktop capability
package is imported lazily so it never loads on non-desktop images.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from agent_runtime.api.events import RuntimeEventProducer
    from agent_runtime.capabilities.desktop.broker_client import DesktopBrokerClient
    from agent_runtime.capabilities.desktop.workspace_backend import (
        WorkspaceBackendConfig,
        WorkspaceMutationSnapshot,
    )

logger = logging.getLogger(__name__)


class WorkspaceBackendWorkerWiring:
    """Gate + builder for the per-run read-only ``/workspace/`` backend.

    ``env`` defaults to ``os.environ`` (via ``WorkspaceBackendConfig.from_env``);
    ``http_client`` defaults to the process-shared broker pool. Both are
    injectable so a test can drive the whole path against an in-memory fake
    broker without touching the environment or the network. ``run_id`` scopes the
    deterministic approval id of a mid-run grant request; callers that have the
    run in hand should pass it, and it is optional so existing call sites keep
    working unchanged.

    ``interrupt_handler`` overrides the seam the grant request blocks on. It
    defaults to ``langgraph.types.interrupt`` — the same mechanism the MCP auth
    gate parks on — and is injectable so the grant path can be driven without a
    graph.
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        run_id: str | None = None,
        interrupt_handler: Callable[[dict[str, Any]], object] | None = None,
    ) -> None:
        self._env = env
        self._http_client = http_client
        self._run_id = run_id
        self._interrupt_handler = interrupt_handler

    async def workspace_backend(self, *, run_id: str | None = None) -> object | None:
        """Build the ``/workspace/`` backend for this run, or ``None`` off desktop.

        Fails soft on the broker: an unreachable broker yields ``None`` rather
        than raising, so a run never breaks because host reads happen to be
        unavailable. An empty grant snapshot is NOT a soft failure — it yields a
        zero-mount backend, because "you have granted nothing" is an answer the
        agent must be able to give.
        """

        # Lazy import: the desktop capability package must not load on the
        # web / postgres / in-memory worker images.
        from agent_runtime.capabilities.desktop import (  # noqa: PLC0415
            BrokerError,
            WorkspaceBackendConfig,
            WorkspaceGrantGate,
            WorkspaceMountTable,
            build_workspace_backend,
        )

        config = WorkspaceBackendConfig.from_env(env=self._env)
        if not config.broker_base_url or not config.broker_token:
            # Say WHICH half is missing. Silence here is indistinguishable from
            # "not a desktop image", which is how a broker env-name mismatch hid
            # for an entire release: the route was never composed, host paths
            # fell through to agent memory, and `ls ~/Downloads` answered with an
            # empty listing and a green tick. No secret is logged — only whether
            # each value was present.
            logger.info(
                "workspace_backend.broker_config_absent url=%s token=%s",
                bool(config.broker_base_url),
                bool(config.broker_token),
            )
            return None
        client = self._broker_client(config)
        try:
            snapshot = await client.grants_snapshot()
        except BrokerError as exc:
            # WARNING, not debug. This branch silently disables host filesystem
            # access for the whole run: no `/workspace/` route, so a host path
            # lands on the StateBackend default and agent memory answers it with
            # an empty listing. At debug level that outage was invisible in every
            # packaged log, which is why it survived a live investigation.
            # Diagnostics carry no token, no path, no broker internals — the
            # error CLASS is enough to tell "broker refused me" from
            # "broker unreachable".
            logger.warning(
                "workspace_backend.grants_unavailable error_class=%s "
                "(host filesystem access is DISABLED for this run)",
                type(exc).__name__,
            )
            return None
        mounts = WorkspaceMountTable.from_broker_grants(snapshot.grants)
        scoped = config.with_mounts(mounts).with_run(run_id or self._run_id)
        gate = (
            None
            if self._interrupt_handler is None
            else WorkspaceGrantGate(
                grants=client,
                interrupt_handler=self._interrupt_handler,
                run_id=scoped.run_id,
            )
        )
        return build_workspace_backend(scoped, client=client, grant_gate=gate)

    async def granted_host_roots(self) -> tuple[object, ...]:
        """The host roots the user attached, as ``GrantedRoot`` rule/floor input.

        WHICH FOLDERS THE USER ATTACHED IS A BROKER FACT. It is not a property of
        whichever ``/workspace/`` backend object the run's ``workspace_effect_mode``
        happened to select, and reading it off that object is what made attached
        folders inert in the ENFORCE lane: there, ``RunHandler`` builds a
        ``WorkspaceGatewayBackend`` / ``WorkspaceTombstoneBackend``, neither of
        which exposes ``granted_roots``, and their host-session projection is
        deliberately path-free — so ``_granted_host_roots`` found nothing, no
        ``allow`` rule was built, and every read of a folder the user had
        explicitly attached interrupted and asked again. The effect mode decides
        how workspace WRITES are staged; it has no business deciding which
        folders are readable.

        So this asks the broker directly, over the SAME ``/v1/grants/snapshot``
        projection and the SAME :class:`WorkspaceMountTable` mapping the default
        lane's backend is built from — one source of truth for the roots, reached
        from any lane. ``root`` crosses only for ACTIVE grants (the broker's own
        rule), and the RENDERER projection stays path-free; this is the worker
        audience, which already resolves and reads real host paths itself.

        Every failure degrades toward "that folder still ASKS", never toward
        "now open": no broker config (non-desktop, or the filesystem capability
        opted out) yields ``()`` silently, and an unreachable broker yields ``()``
        with a warning, because "your grants do nothing this run" must be
        discoverable from a packaged log.
        """

        # Lazy import: the desktop capability package must not load on the
        # web / postgres / in-memory worker images.
        from agent_runtime.capabilities.desktop import (  # noqa: PLC0415
            BrokerError,
            WorkspaceBackendConfig,
            WorkspaceMountTable,
        )

        config = WorkspaceBackendConfig.from_env(env=self._env)
        if not config.broker_base_url or not config.broker_token:
            # Not a desktop image (or the capability is off). Silent on purpose:
            # `workspace_backend()` already logs the absent-config line once per
            # run, and a second copy would say nothing new.
            return ()
        try:
            snapshot = await self._broker_client(config).grants_snapshot()
        except BrokerError as exc:
            logger.warning(
                "workspace_backend.granted_roots_unavailable error_class=%s "
                "(attached folders will keep asking on every read)",
                type(exc).__name__,
            )
            return ()
        return WorkspaceMountTable.granted_roots(
            WorkspaceMountTable.from_broker_grants(snapshot.grants)
        )

    def _broker_client(self, config: WorkspaceBackendConfig) -> DesktopBrokerClient:
        """One broker client shape for both the backend and the roots lane."""

        from agent_runtime.capabilities.desktop import (  # noqa: PLC0415
            BrokerClientConfig,
            DesktopBrokerClient,
        )

        return DesktopBrokerClient(
            BrokerClientConfig(
                base_url=config.broker_base_url or "",
                token=config.broker_token or "",
                service_identity=config.service_identity,
                broker_audience=config.broker_audience,
                protocol_version=config.protocol_version,
                timeout_seconds=config.timeout_seconds,
            ),
            http_client=self._http_client,
        )

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
