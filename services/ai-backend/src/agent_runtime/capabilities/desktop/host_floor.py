"""The floor beneath the host filesystem rules — for the paths globs cannot see.

:mod:`~agent_runtime.capabilities.desktop.host_filesystem` states that its last
two rules are total::

    4. every other READ   -> interrupt   paths: ["/**"]
    5. every other WRITE  -> deny        paths: ["/**"]

    "Together, rules 4 and 5 are total over absolute paths, so nothing is left
     to deepagents' unmatched-means-allow default."

That claim is FALSE, and the gap it leaves is the worst-shaped one available.
deepagents matches rule paths with ``wcmatch`` under
``_FS_WCMATCH_FLAGS = BRACE | GLOBSTAR`` — **without** ``DOTGLOB`` — and under
those flags neither ``*`` nor ``**`` matches a path segment that begins with a
dot::

    globmatch("/Users/ada/.ssh/id_rsa", "/**", flags=BRACE|GLOBSTAR)  ->  False

``_check_fs_permission`` returns ``"allow"`` when NO rule matches. So every
hidden path on the machine — ``~/.ssh/id_rsa``, ``~/.aws/credentials``,
``~/.env``, ``~/.config/gh/hosts.yml`` — evaluated ``allow`` for BOTH read and
write, with no consent card, on a first run with zero grants.

No pattern closes it
--------------------
A leading literal dot does match, so ``/**/.*/**`` covers ONE hidden segment —
and fails on ``/Users/ada/.a/.b/.c``. ``[.]`` is not a literal dot to wcmatch,
and brace alternation cannot recurse. Only ``DOTGLOB`` (an upstream flag we do
not control) makes ``/**`` total. The totality property therefore cannot be
re-established in the rule set; it has to be re-established somewhere the
matcher's blind spot does not exist.

This module is that place: the object every host path finally lands on.

What it enforces, and what it deliberately does not
---------------------------------------------------
The rules stay the boundary. This wrapper only supplies the verdicts the
matcher structurally cannot reach, and it is careful never to overrule a
decision a human already made:

* **write / edit** on a host path — allowed only inside a writable granted
  root's ``.copilot`` scratch. This mirrors rules 2 + 5 exactly: no host write
  is ever interruptible, so there is no approval this can be trampling. It also
  closes the read-only-grant case, where ``<root>/.copilot`` is hidden and so
  evaluated ``allow`` for write even though the grant forbade writing.
* **read** on a *matcher-blind* host path (any segment starting with ``.``) —
  allowed only inside a granted root. The exact-scope predicate
  (``read_file``) fires an interrupt only when ``_check_fs_permission`` says
  ``"interrupt"``, which for a hidden path it never does, so nothing asked and
  nothing may be assumed approved.
* **ls / glob / grep are delegated untouched.** Their HITL predicate is
  ``_make_bulk_when_predicate``, which fires whenever the call's subtree
  overlaps an interrupt anchor — and rule 4's anchor is ``/``. Those calls
  therefore ALWAYS ask first, hidden or not, so refusing them here would deny
  a read the user had just approved. ``test_host_floor`` pins that dependency
  against deepagents' own predicate so a narrowing of rule 4 fails loudly
  rather than silently opening this lane.
* Non-host paths (the agent's own ``/memories/``, ``/drafts/``, ``/workspace/``
  …) are delegated untouched — they are routed elsewhere by the composite and
  are not this module's business.

Not a ``BackendProtocol`` subclass, for the reason
:mod:`~agent_runtime.capabilities.desktop.host_route` documents: delegating via
``__getattr__`` keeps every op this class does not name behaving exactly as it
did before the floor existed.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Final

from deepagents.backends.protocol import (
    PERMISSION_DENIED,
    EditResult,
    FileDownloadResponse,
    ReadResult,
    WriteResult,
)

from agent_runtime.capabilities.desktop.host_path import HostPathClassifier

if TYPE_CHECKING:
    from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot


class HostFloorMessages:
    """Safe public refusals. They never echo the offending host path."""

    #: A hidden path nobody attached. Says what would make it work, so the model
    #: asks for a grant instead of retrying the same read forever.
    HIDDEN_READ: Final = (
        "Permission denied: that is a hidden path outside every folder the user "
        "has attached. Ask the user to attach the folder it lives in, then read "
        "it again."
    )
    #: Every host write, except the agent's own scratch inside a writable grant.
    HOST_WRITE: Final = (
        "Permission denied: the agent cannot write directly to the host "
        "filesystem. Stage the change so the user can review and apply it."
    )


class HostFilesystemFloor:
    """Wraps the real filesystem backend with the verdicts globs cannot express.

    ``roots`` is the same ``GrantedRoot`` tuple the rule set was built from, so
    the floor and the rules cannot disagree about which folders are attached.
    """

    #: Deep Agents' own ``read`` defaults, mirrored so a delegated call that
    #: omits them behaves identically on both sides of the guard.
    _READ_OFFSET: Final = 0
    _READ_LIMIT: Final = 2000

    def __init__(
        self,
        backend: object,
        *,
        roots: tuple[GrantedRoot, ...] = (),
    ) -> None:
        """Guard ``backend`` for the host paths ``roots`` does not cover."""
        self._backend = backend
        self._roots = roots

    @property
    def backend(self) -> object:
        """The wrapped backend (deepagents' real ``FilesystemBackend``)."""
        return self._backend

    @property
    def roots(self) -> tuple[GrantedRoot, ...]:
        """The granted roots this floor admits."""
        return self._roots

    def __getattr__(self, name: str) -> Any:
        """Delegate every op this floor does not guard (see the module header)."""
        return getattr(self._backend, name)

    # --- verdicts -----------------------------------------------------------

    @staticmethod
    def is_matcher_blind(path: str | None) -> bool:
        """True when deepagents' glob matcher cannot see ``path`` at all.

        Any segment beginning with a dot — ``.ssh``, ``.env``, and also ``.``
        and ``..`` — is unmatchable by ``*``/``**`` without ``DOTGLOB``, so a
        rule set built from those patterns has NO opinion about this path and
        ``_check_fs_permission`` falls through to ``allow``.
        """

        if not path:
            return False
        return any(part.startswith(".") for part in PurePosixPath(path).parts)

    def permits_read(self, path: str | None) -> bool:
        """Whether a READ of ``path`` may proceed past the floor.

        Everything the rule set can see is delegated to it; a matcher-blind
        host path is admitted only from inside a folder the user attached.
        """

        if not self._is_host(path) or not self.is_matcher_blind(path):
            return True
        return any(self._within(path, root.path) for root in self._roots)

    def permits_write(self, path: str | None) -> bool:
        """Whether a WRITE of ``path`` may proceed past the floor.

        Unlike reads this covers EVERY host path, not only the matcher-blind
        ones: no host write is ever interruptible, so a second enforcement of
        the same verdict can never contradict a user decision.
        """

        if not self._is_host(path):
            return True
        return any(
            self._within(path, root.scratch_path)
            for root in self._roots
            if root.writable
        )

    @staticmethod
    def _is_host(path: str | None) -> bool:
        """True when ``path`` addresses the real machine rather than a namespace."""
        return bool(path) and HostPathClassifier.is_host_shaped(path or "")

    @staticmethod
    def _within(path: str | None, root: str) -> bool:
        """True when ``path`` is ``root`` or lies beneath it, segment-wise.

        Segment-wise so ``/a/ProjectsSecret`` is never admitted by a grant on
        ``/a/Projects``. A traversal segment is never admitted at all: a
        lexical parent-walk cannot be trusted through a symlink, and deepagents'
        ``validate_path`` already rejects ``..`` before any op reaches a backend.
        """

        if not path or not path.startswith("/") or not root.startswith("/"):
            return False
        parts = PurePosixPath(path).parts
        if ".." in parts:
            return False
        root_parts = PurePosixPath(root).parts
        return parts[: len(root_parts)] == root_parts

    # --- read ---------------------------------------------------------------

    def read(
        self,
        file_path: str,
        offset: int = _READ_OFFSET,
        limit: int = _READ_LIMIT,
    ) -> ReadResult:
        """Read ``file_path`` unless the floor refuses it."""
        if not self.permits_read(file_path):
            return ReadResult(error=HostFloorMessages.HIDDEN_READ)
        return self._backend.read(file_path, offset, limit)  # type: ignore[attr-defined]

    async def aread(
        self,
        file_path: str,
        offset: int = _READ_OFFSET,
        limit: int = _READ_LIMIT,
    ) -> ReadResult:
        """Read ``file_path`` unless the floor refuses it."""
        if not self.permits_read(file_path):
            return ReadResult(error=HostFloorMessages.HIDDEN_READ)
        return await self._backend.aread(file_path, offset, limit)  # type: ignore[attr-defined]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Batch-read, refusing per path — the alternate read surface.

        Deep Agents' skills / memory / summarization middleware read through
        this op rather than ``read``, so leaving it delegated would leave the
        same hidden-path lane open one call away.
        """

        refused, allowed = self._split_reads(paths)
        if not allowed:
            return [refused[path] for path in paths]
        served = iter(self._backend.download_files(allowed))  # type: ignore[attr-defined]
        return [refused[path] if path in refused else next(served) for path in paths]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Batch-read, refusing per path (see :meth:`download_files`)."""
        refused, allowed = self._split_reads(paths)
        if not allowed:
            return [refused[path] for path in paths]
        served = iter(await self._backend.adownload_files(allowed))  # type: ignore[attr-defined]
        return [refused[path] if path in refused else next(served) for path in paths]

    def _split_reads(
        self, paths: list[str]
    ) -> tuple[dict[str, FileDownloadResponse], list[str]]:
        """Partition a batch into per-path refusals and the paths to delegate."""
        refused = {
            path: FileDownloadResponse(path=path, content=None, error=PERMISSION_DENIED)
            for path in paths
            if not self.permits_read(path)
        }
        return refused, [path for path in paths if path not in refused]

    # --- mutation -----------------------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return WriteResult(error=HostFloorMessages.HOST_WRITE)
        return self._backend.write(file_path, content)  # type: ignore[attr-defined]

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return WriteResult(error=HostFloorMessages.HOST_WRITE)
        return await self._backend.awrite(file_path, content)  # type: ignore[attr-defined]

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return EditResult(error=HostFloorMessages.HOST_WRITE)
        return self._backend.edit(  # type: ignore[attr-defined]
            file_path, old_string, new_string, replace_all
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return EditResult(error=HostFloorMessages.HOST_WRITE)
        return await self._backend.aedit(  # type: ignore[attr-defined]
            file_path, old_string, new_string, replace_all
        )


__all__ = ("HostFilesystemFloor", "HostFloorMessages")
