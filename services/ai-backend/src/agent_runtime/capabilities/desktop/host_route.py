r"""The default backend a host-absolute path cannot slip past.

Why a prefix route is not enough
--------------------------------
Deep Agents' ``CompositeBackend`` routes by path PREFIX and sends everything else
to its ``default``. ``/workspace/`` is a prefix, so the granted-folder route works
— but ``/Users/<name>/Downloads`` and ``C:\Users\<name>\Downloads`` are not
prefixes of anything and never will be: the set of host paths is not enumerable as
routes. Every one of them therefore lands on the default, which is the agent-MEMORY
``StateBackend``. Memory holds nothing at that path, so ``ls`` returned an EMPTY
LISTING as a SUCCESS and the agent reported an empty Downloads folder that holds
1009 files, under a green tool card and 175 ms.

That is why the claim cannot be expressed as a route. It has to be expressed in
the DEFAULT — the one place every unrouted path passes through.
:class:`HostPathGuardBackend` wraps the default and asks
``BrokeredWorkspaceBackend.claims_path`` about each addressed path first:

* claimed (host-shaped, or the ``/workspace/`` namespace) → the workspace backend,
  which answers with a real listing, a grant request, or an explicit refusal;
* everything else → the wrapped default, byte-for-byte as before.

Writes are routed by the same claim on purpose. A host-absolute ``write_file`` sent
to agent memory would report success while nothing reached the disk the user was
thinking of — the empty-listing lie with the arrow reversed. Routed here it reaches
the workspace backend's permanent refusal instead.

Not a ``BackendProtocol`` subclass
----------------------------------
``BackendProtocol`` is an ABC carrying concrete implementations, so subclassing it
would shadow the wrapped default's behavior for every op this class does not name
(``download_files``, ``ls_info``, ``grep_raw``, ``execute``, …) and normal
attribute lookup would never reach ``__getattr__``. Delegating instead makes the
non-routed surface total by construction: an op this guard does not know about
behaves exactly as it did before the guard existed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from deepagents.backends.protocol import (
        EditResult,
        GlobResult,
        GrepResult,
        LsResult,
        ReadResult,
        WriteResult,
    )

    from agent_runtime.capabilities.desktop.workspace_backend import (
        BrokeredWorkspaceBackend,
    )


class HostPathGuardBackend:
    """Routes claimed host paths to the workspace backend, the rest to ``default``.

    ``default`` is whatever the composite would otherwise have fallen back to
    (deepagents' ``StateBackend``, i.e. agent memory). ``workspace`` is the
    ``/workspace/`` backend, which owns the claim rule and every answer for a
    claimed path.

    Composed as the composite's default rather than as one of its routes::

        CompositeBackend(
            default=HostPathGuardBackend(default=StateBackend(), workspace=ws),
            routes=routes,
        )

    The ``/workspace/`` prefix route stays registered alongside it: the composite
    strips that prefix before delegating, which the workspace backend relies on.
    This guard only ever sees paths the composite did NOT match — and it receives
    them unmodified, which is what makes a host-absolute path recognisable here.
    """

    #: Deep Agents' own ``read`` defaults, mirrored so a delegated call that omits
    #: them behaves identically on both sides of the routing decision.
    _READ_OFFSET: Final = 0
    _READ_LIMIT: Final = 2000

    def __init__(
        self,
        *,
        default: object,
        workspace: BrokeredWorkspaceBackend,
    ) -> None:
        """Wrap ``default``, diverting paths ``workspace`` claims."""
        self._default = default
        self._workspace = workspace

    @property
    def default(self) -> object:
        """The wrapped fallback backend (agent memory in the composed graph)."""
        return self._default

    @property
    def workspace(self) -> BrokeredWorkspaceBackend:
        """The backend every claimed path is answered by."""
        return self._workspace

    def claims(self, path: str | None) -> bool:
        """True when ``path`` must be answered by the workspace backend."""
        return self._workspace.claims_path(path)

    def _for(self, path: str | None) -> object:
        """The backend that must answer ``path``."""
        return self._workspace if self.claims(path) else self._default

    def __getattr__(self, name: str) -> Any:
        """Delegate every op this guard does not route to the wrapped default.

        Reached only for names this class does not define (see the module header),
        so the non-routed surface is unchanged rather than reimplemented.
        """
        return getattr(self._default, name)

    # --- list ---------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        """List ``path`` on whichever backend owns it."""
        return self._for(path).ls(path)  # type: ignore[union-attr]

    async def als(self, path: str) -> LsResult:
        """List ``path`` on whichever backend owns it."""
        return await self._for(path).als(path)  # type: ignore[union-attr]

    # --- read ---------------------------------------------------------------

    def read(
        self,
        file_path: str,
        offset: int = _READ_OFFSET,
        limit: int = _READ_LIMIT,
    ) -> ReadResult:
        """Read ``file_path`` on whichever backend owns it."""
        return self._for(file_path).read(file_path, offset, limit)  # type: ignore[union-attr]

    async def aread(
        self,
        file_path: str,
        offset: int = _READ_OFFSET,
        limit: int = _READ_LIMIT,
    ) -> ReadResult:
        """Read ``file_path`` on whichever backend owns it."""
        return await self._for(file_path).aread(file_path, offset, limit)  # type: ignore[union-attr]

    # --- search -------------------------------------------------------------

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Glob under ``path`` on whichever backend owns it (``None`` → default)."""
        return self._for(path).glob(pattern, path)  # type: ignore[union-attr]

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Glob under ``path`` on whichever backend owns it (``None`` → default)."""
        return await self._for(path).aglob(pattern, path)  # type: ignore[union-attr]

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Grep under ``path`` on whichever backend owns it (``None`` → default)."""
        return self._for(path).grep(pattern, path, glob)  # type: ignore[union-attr]

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Grep under ``path`` on whichever backend owns it (``None`` → default)."""
        return await self._for(path).agrep(pattern, path, glob)  # type: ignore[union-attr]

    # --- mutation -----------------------------------------------------------
    #
    # Claimed writes go to the workspace backend so they hit its permanent
    # refusal. Sending them to memory would report a success the host never saw.

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` on whichever backend owns it."""
        return self._for(file_path).write(file_path, content)  # type: ignore[union-attr]

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` on whichever backend owns it."""
        return await self._for(file_path).awrite(file_path, content)  # type: ignore[union-attr]

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit ``file_path`` on whichever backend owns it."""
        return self._for(file_path).edit(  # type: ignore[union-attr]
            file_path, old_string, new_string, replace_all
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit ``file_path`` on whichever backend owns it."""
        return await self._for(file_path).aedit(  # type: ignore[union-attr]
            file_path, old_string, new_string, replace_all
        )


def guarded_default(
    default: object,
    workspace: object | None,
) -> object:
    """Wrap ``default`` so claimed host paths reach ``workspace``.

    The single call site the composite needs: returns ``default`` untouched when
    there is no workspace backend (every non-desktop image), so nothing changes
    off the desktop path. ``agent_runtime.execution.factory._composed_deep_backend``
    adopts this by building its ``CompositeBackend`` with
    ``default=guarded_default(StateBackend(), workspace_backend)``.

    Typed loosely because the factory holds these as ``object`` — it composes
    optional backends it must not import types for.
    """

    from agent_runtime.capabilities.desktop.workspace_backend import (  # noqa: PLC0415
        BrokeredWorkspaceBackend,
    )

    if not isinstance(workspace, BrokeredWorkspaceBackend):
        return default
    return HostPathGuardBackend(default=default, workspace=workspace)


__all__ = ("HostPathGuardBackend", "guarded_default")
