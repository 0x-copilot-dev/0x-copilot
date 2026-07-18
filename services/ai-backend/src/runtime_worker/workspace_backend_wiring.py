"""Per-run construction of the read-only ``/workspace/`` Deep Agents backend.

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
``None``.

Kept in its own module (mirroring :class:`runtime_worker.file_store_wiring.FileStoreWorkerWiring`)
so the run path constructs the workspace backend exactly once, per run, without
leaking desktop-only concerns into the run handler. The desktop capability
package is imported lazily so it never loads on non-desktop images.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import httpx

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
        access happens to be unavailable.
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
        return build_workspace_backend(config.with_mounts(mounts), client=client)


__all__ = ("WorkspaceBackendWorkerWiring",)
