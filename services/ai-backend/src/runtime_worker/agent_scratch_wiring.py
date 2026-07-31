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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_runtime.capabilities.desktop.agent_scratch import ConversationScratch

_LOGGER = logging.getLogger(__name__)


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
            _LOGGER.warning("agent_scratch.unusable_conversation_id")
            return None
        except (OSError, RuntimeError):
            # ``RuntimeError`` is here for ``Path.home()`` on a process with no
            # resolvable home, which does NOT raise ``OSError``. A run must not
            # fail because the agent could not get a working directory.
            _LOGGER.warning("agent_scratch.provision_failed")
            return None


__all__ = ("AgentScratchWorkerWiring",)
