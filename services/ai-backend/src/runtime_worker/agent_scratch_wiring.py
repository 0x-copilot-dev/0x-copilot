"""Provision ``$COPILOT_HOME/.tmp/<conversation_id>/`` for a run (PRD-FS-12).

The one place a run turns the scratch CONTRACT
(:mod:`agent_runtime.capabilities.desktop.agent_scratch`) into directories that
exist. Kept out of :mod:`runtime_worker.handlers.run` for the same reason
:class:`~runtime_worker.file_store_wiring.FileStoreWorkerWiring` is: the gate
and the builder belong together, so the two run paths (initial + approval
resume) cannot drift into one provisioning and the other not.

Desktop only, on the same signal the host filesystem rules use — a workspace
backend exists. That is not merely symmetry. On a hosted image there is no user
at the machine, no host filesystem lane is composed at all, and creating
``~/.0xcopilot/.tmp/<conv>/`` would be writing per-tenant scratch onto the
SERVER's home directory for every conversation in the deployment.

Never raises. A scratch that cannot be provisioned degrades to "the agent has no
working directory this run" — the tool layer then refuses those writes through
the ordinary host-write deny — never to a failed run. It is a working area, not
the record.

Deletion (D6) deliberately does NOT live here. It is
:func:`~agent_runtime.capabilities.desktop.agent_scratch.delete_conversation_scratch`,
so the file store adapter can cascade into it without a store depending on the
worker.

The MCP catalog rides the same gate
-----------------------------------
``mcp/`` is a conversation-scoped directory in this same tree, so
:meth:`AgentScratchWorkerWiring.mcp_catalog_store` lives here rather than in a
wiring class of its own: one gate, one id validation, one failure posture. That
is deliberate — the R1 bug (:mod:`runtime_worker.file_store_wiring`) was two run
paths drifting into one wiring and the other not, and the approval-resume path
must reach the SAME catalog the interrupted turn was browsing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runtime.capabilities.desktop.agent_scratch import ConversationScratch

_LOGGER = logging.getLogger(__name__)


class _Log:
    """Structured event names emitted by this wiring."""

    UNUSABLE_ID = "agent_scratch.unusable_conversation_id"
    PROVISION_FAILED = "agent_scratch.provision_failed"
    CATALOG_DECLINED = "mcp_catalog.store_declined reason=%s"


class _DeclineReason:
    """Why a run got no file-backed MCP catalog. One of these is always logged."""

    #: No workspace backend — the hosted/web/postgres images, where writing
    #: ``~/.0xcopilot/.tmp/<conv>/`` would land per-tenant scratch on the
    #: server's own home directory.
    NOT_DESKTOP = "not_desktop"
    #: The conversation id is not usable as a directory name.
    UNUSABLE_ID = "unusable_conversation_id"
    #: The scratch directory could not be created or resolved.
    SCRATCH_UNAVAILABLE = "scratch_unavailable"


class AgentScratchWorkerWiring:
    """Gate + provisioner for one run's scratch directories."""

    def __init__(self, *, workspace_backend: object | None) -> None:
        """``workspace_backend`` is the desktop gate; ``None`` disables everything."""

        self._enabled = workspace_backend is not None

    @property
    def enabled(self) -> bool:
        """Whether this process should touch the scratch at all."""

        return self._enabled

    def provision(
        self,
        *,
        conversation_id: str,
        run_id: str | None = None,
        title: str | None = None,
    ) -> ConversationScratch | None:
        """Create the conversation (and run) scratch; return it, or ``None``.

        ``title`` is written into ``meta.json`` and NEVER into a path (D4/D5).
        A ``conversation_id`` that is not an opaque identifier yields ``None``
        rather than a sanitised directory — see
        :func:`~agent_runtime.capabilities.desktop.agent_scratch.safe_segment`.
        """

        if not self._enabled:
            return None
        from agent_runtime.capabilities.desktop.agent_scratch import (  # noqa: PLC0415
            ScratchIdError,
            agent_scratch_root,
        )

        try:
            scratch = agent_scratch_root().conversation(conversation_id)
            scratch.provision(title=title)
            if run_id is not None:
                scratch.run(run_id).provision()
            return scratch
        except ScratchIdError:
            # The id is not usable as a directory name. Deliberately not logged
            # with the value: it may be user content, which is the whole reason
            # a title never names a path.
            _LOGGER.warning(_Log.UNUSABLE_ID)
            return None
        except (OSError, RuntimeError):
            # ``RuntimeError`` is here for ``Path.home()`` on a process with no
            # resolvable home, which does NOT raise ``OSError``. A run must not
            # fail because the agent could not get a working directory.
            _LOGGER.warning(_Log.PROVISION_FAILED)
            return None

    def mcp_catalog_store(self, *, conversation_id: str) -> object | None:
        """Return the file-backed MCP catalog store for this chat, or ``None``.

        ``None`` means the run composes the in-process
        :class:`~agent_runtime.capabilities.mcp.catalog.McpCatalogStore` instead
        — byte-for-byte the previous behavior on web / postgres / in-memory, and
        on a desktop whose scratch is unusable. Every decline logs WHY, because
        "the model said ``/mcp`` was empty" is otherwise indistinguishable from
        "the catalog was never mounted" in a live report.

        The store creates its own directory, so this is safe on the
        approval-resume path, which never calls :meth:`provision`.
        """

        if not self._enabled:
            _LOGGER.info(_Log.CATALOG_DECLINED, _DeclineReason.NOT_DESKTOP)
            return None
        from agent_runtime.capabilities.desktop.agent_scratch import (  # noqa: PLC0415
            ScratchIdError,
            agent_scratch_root,
        )
        from runtime_adapters.file.mcp_catalog_store import (  # noqa: PLC0415
            FileMcpCatalogStore,
        )

        try:
            root = agent_scratch_root().conversation(conversation_id).mcp
            return FileMcpCatalogStore(root)
        except ScratchIdError:
            _LOGGER.warning(_Log.CATALOG_DECLINED, _DeclineReason.UNUSABLE_ID)
            return None
        except (OSError, RuntimeError):
            _LOGGER.warning(
                _Log.CATALOG_DECLINED, _DeclineReason.SCRATCH_UNAVAILABLE, exc_info=True
            )
            return None


__all__ = ("AgentScratchWorkerWiring",)
