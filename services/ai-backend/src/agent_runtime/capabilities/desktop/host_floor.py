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

* **write / edit** on a host path — allowed only inside the agent's own scratch
  root, ``$COPILOT_HOME/.tmp`` (:mod:`~agent_runtime.capabilities.desktop.agent_scratch`).
  This mirrors rules 2 + 5 exactly: no host write is ever interruptible, so
  there is no approval this can be trampling. Before PRD-FS-12 D7 the writable
  location was ``<granted root>/.copilot``, which made a read-only grant a
  special case this module had to re-close by hand; the scratch is now
  somewhere that is ours, so a grant's writability no longer decides anything
  here and NOTHING is ever written inside a folder the user attached.
* **read** on a *matcher-blind* host path (any segment starting with ``.``) —
  allowed only inside a granted root or inside the scratch. The exact-scope
  predicate (``read_file``) fires an interrupt only when
  ``_check_fs_permission`` says ``"interrupt"``, which for a hidden path it
  never does, so nothing asked and nothing may be assumed approved. The scratch
  needs naming here as well as in the rule set precisely because ``.tmp`` is
  itself a dotted segment: the literal rule covers it only as far as the
  matcher can see, and a further hidden segment beneath it
  (``.tmp/<conv>/.hidden``) is decided here.
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

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final

from deepagents.backends.protocol import (
    PERMISSION_DENIED,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    ReadResult,
    WriteResult,
)

from agent_runtime.capabilities.desktop.host_path import HostPathClassifier
from agent_runtime.capabilities.desktop.write_journal import path_within

if TYPE_CHECKING:
    from agent_runtime.capabilities.desktop.agent_scratch import AgentScratchRoot
    from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot
    from agent_runtime.capabilities.desktop.write_journal import HostWriteJournal


class HostFloorMessages:
    """Safe public refusals. They never echo the offending host path."""

    #: A hidden path nobody attached. Says what would make it work, so the model
    #: asks for a grant instead of retrying the same read forever.
    HIDDEN_READ: Final = (
        "Permission denied: that is a hidden path outside every folder the user "
        "has attached. Ask the user to attach the folder it lives in, then read "
        "it again."
    )
    #: Every host write, except the agent's own scratch root.
    HOST_WRITE: Final = (
        "Permission denied: the agent cannot write directly to the host "
        "filesystem. Stage the change so the user can review and apply it."
    )


class HostFilesystemFloor:
    """Wraps the real filesystem backend with the verdicts globs cannot express.

    ``roots`` is the same ``GrantedRoot`` tuple the rule set was built from, and
    ``scratch`` is the same ``AgentScratchRoot``, so the floor and the rules
    cannot disagree about which folders are attached or where the agent's own
    working area is. ``scratch=None`` means the run has no scratch, and then
    NO host write is permitted at all — the strictly safer degradation.
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
        scratch: AgentScratchRoot | None = None,
        assets: tuple[str, ...] = (),
        journal: HostWriteJournal | None = None,
    ) -> None:
        """Guard ``backend`` for the host paths ``roots`` does not cover.

        ``assets`` are READ-ONLY locations shipped inside the runtime's own
        installation — see :func:`builtin_asset_roots`.

        ``journal`` captures the pre-image of every write this floor admits, so
        the user can undo it (see
        :mod:`~agent_runtime.capabilities.desktop.write_journal`). ``None``
        means the run keeps no undo history — the previous behaviour, and the
        correct degradation on every non-file store.
        """
        self._backend = backend
        self._roots = roots
        self._scratch_path = None if scratch is None else scratch.posix
        self._assets = assets
        self._journal = journal

    @property
    def backend(self) -> object:
        """The wrapped backend (deepagents' real ``FilesystemBackend``)."""
        return self._backend

    @property
    def roots(self) -> tuple[GrantedRoot, ...]:
        """The granted roots this floor admits."""
        return self._roots

    @property
    def scratch_path(self) -> str | None:
        """The agent's scratch root as a POSIX path, or ``None`` if it has none."""
        return self._scratch_path

    @property
    def assets(self) -> tuple[str, ...]:
        """Read-only roots shipped inside the runtime's own installation."""
        return self._assets

    @property
    def journal(self) -> HostWriteJournal | None:
        """The undo journal capturing this run's writes, if it has one."""
        return self._journal

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
        host path is admitted only from inside a folder the user attached, from
        inside the agent's own scratch — which is itself matcher-blind
        (``$COPILOT_HOME/.tmp``), so without naming it here the agent could not
        read back the working files it had just written — or from inside the
        runtime's own shipped assets.

        That last one was a live bug, and a total one. The built-in Skills ship
        at ``<install>/services/ai-backend/skills/<name>/SKILL.md``, and a
        packaged install puts ``<install>`` under ``$COPILOT_HOME`` — which is
        ``~/.0xcopilot`` by convention. One dotted segment anywhere in a path is
        enough to blind the matcher, so EVERY shipped skill failed this
        predicate and deepagents' loader logged ``permission_denied; skipping``
        and moved on. 2 of 2 skills were dead in the packaged app, silently,
        while the same paths resolved fine in a checkout that happened not to
        sit under a dotted directory. Read-only: shipped assets are never a
        write target, so ``permits_write`` deliberately does not consult them.
        """

        if not self._is_host(path) or not self.is_matcher_blind(path):
            return True
        if self._within_scratch(path):
            return True
        if any(self._within(path, asset) for asset in self._assets):
            return True
        return any(self._within(path, root.path) for root in self._roots)

    def permits_write(self, path: str | None) -> bool:
        """Whether a WRITE of ``path`` may proceed past the floor.

        Unlike reads this covers EVERY host path, not only the matcher-blind
        ones: no host write is ever interruptible, so a second enforcement of
        the same verdict can never contradict a user decision.

        Two host locations pass, and no others:

        * the agent's own scratch, which needs no grant because it is ours;
        * a root the user attached AND marked WRITABLE.

        The second is not a relaxation of the rule set — it is the same verdict
        rule 3 reaches, restated where globs cannot see. `wcmatch` runs without
        `DOTGLOB`, so a granted folder under a dotted segment (`~/.config/app`,
        or any path below one) is invisible to every pattern; without this the
        rules would allow the write and the floor would silently refuse it. The
        two layers must agree, and this is where they are made to.

        A read-only grant still fails here, which is the whole point of asking
        the question when the folder is attached.
        """

        return not self._is_host(path) or self.writable_root_for(path) is not None

    def writable_root_for(self, path: str | None) -> str | None:
        """WHICH root admits a host write of ``path`` — the undo journal's key.

        :meth:`permits_write` answers the security question; this answers the
        accountability one, and they are the same computation so they cannot
        disagree. The returned root is stored on every capture record and
        re-checked at revert time, which is what stops a revert from becoming a
        way to write outside the granted set.
        """

        if not self._is_host(path):
            return None
        if self._scratch_path is not None and self._within(path, self._scratch_path):
            return self._scratch_path
        for root in self._roots:
            if root.writable and self._within(path, root.path):
                return root.path
        return None

    def _capture(self, path: str, *, deleting: bool = False) -> None:
        """Journal what is at ``path`` before an ADMITTED mutation lands.

        Called only after ``permits_write`` has passed, so the journal can never
        hold a path the floor refused.
        """

        if self._journal is None:
            return
        root = self.writable_root_for(path)
        if root is not None:
            self._journal.capture(path, root, deleting=deleting)

    def _within_scratch(self, path: str | None) -> bool:
        """True when ``path`` is the scratch root or lies beneath it."""

        if self._scratch_path is None:
            return False
        return self._within(path, self._scratch_path)

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

        The predicate itself lives in
        :func:`~agent_runtime.capabilities.desktop.write_journal.path_within`
        because a revert must decide containment EXACTLY as the floor did. Two
        spellings of one rule is how a restore ends up writing somewhere this
        object would have refused.
        """

        return path_within(path, root)

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
        self._capture(file_path)
        return self._backend.write(file_path, content)  # type: ignore[attr-defined]

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return WriteResult(error=HostFloorMessages.HOST_WRITE)
        self._capture(file_path)
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
        self._capture(file_path)
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
        self._capture(file_path)
        return await self._backend.aedit(  # type: ignore[attr-defined]
            file_path, old_string, new_string, replace_all
        )

    # --- deletion -----------------------------------------------------------
    #
    # `delete` was not guarded here, and `__getattr__` delegated it straight to
    # the real filesystem. Today that is DEFENCE IN DEPTH rather than an open
    # hole, and the distinction is worth stating precisely because the obvious
    # reading is wrong: deepagents classifies its `delete` tool as the ``write``
    # operation (`_DEFAULT_FS_TOOL_OPS`), and while the ALLOW rule for a granted
    # root genuinely cannot see a dotted segment, the catch-all DENY ``/**``
    # matches one fine — so the model's `delete` tool is refused before any
    # backend is reached, for an ordinary path and a hidden one alike. Measured,
    # not assumed; `test_write_journal.py` pins it both ways.
    #
    # It is still guarded here for two reasons. The rule set is upstream code we
    # do not own, and a future deepagents that drops or narrows that catch-all
    # would silently hand `delete` a real filesystem — this class is the only
    # thing that decides on a dotted path, so it should decide on this op too.
    # And capture has to live wherever a removal can land, or a delete that does
    # become reachable is the one mutation with no pre-image.

    def delete(self, file_path: str) -> DeleteResult:
        """Delete ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return DeleteResult(error=HostFloorMessages.HOST_WRITE)
        self._capture(file_path, deleting=True)
        return self._backend.delete(file_path)  # type: ignore[attr-defined]

    async def adelete(self, file_path: str) -> DeleteResult:
        """Delete ``file_path`` unless the floor refuses it."""
        if not self.permits_write(file_path):
            return DeleteResult(error=HostFloorMessages.HOST_WRITE)
        self._capture(file_path, deleting=True)
        return await self._backend.adelete(file_path)  # type: ignore[attr-defined]


def builtin_skills_root() -> Path:
    """Where the Skills that ship WITH the runtime live.

    ``<service_root>/skills`` — resolved from this module's own location so a
    wheel-installed deployment, a staged packaged runtime, and a local checkout
    all answer correctly with no configuration.

    It lives here, next to the floor that must admit it, and
    ``runtime_worker.dependencies.BUILTIN_SKILLS_ROOT`` re-exports it rather
    than re-deriving it. Two independent derivations of one path is precisely
    how the loader ends up reading a directory the floor refuses.
    """

    return Path(__file__).resolve().parents[4] / "skills"


def builtin_asset_roots() -> tuple[str, ...]:
    """POSIX roots the runtime ships and the agent may READ but never write.

    Handed to :class:`HostFilesystemFloor` as ``assets``. Only directories that
    genuinely exist are returned, so a deployment that ships no skills grants
    nothing — an allow-rule for an absent path is a hole waiting for someone to
    create it.
    """

    skills = builtin_skills_root()
    return (skills.as_posix(),) if skills.is_dir() else ()


__all__ = (
    "HostFilesystemFloor",
    "HostFloorMessages",
    "builtin_asset_roots",
    "builtin_skills_root",
)
