"""The MCP catalog as REAL FILES under the conversation's agent scratch.

``$COPILOT_HOME/.tmp/<conversation_id>/mcp/<server>/`` — a conversation-scoped
sibling of ``drafts/``, holding exactly what
:class:`~agent_runtime.capabilities.mcp.catalog.McpCatalogStore` held in
process::

    mcp/<server>/SERVER.md              always-loaded tier
    mcp/<server>/tools/<tool>.json      one tool each (LOADED tier only)
    mcp/<server>/resources/<slug>.json  one MCP resource each

Why real files, and why *here*
------------------------------
The in-memory store was per-harness, and a harness is rebuilt per run
(``runtime_worker.handlers.run``) and AGAIN on approval resume
(``runtime_worker.handlers.approval``). So a catalog published in turn 1 was
gone at the start of turn 2, and a catalog published before a write-approval
interrupt was gone when the run resumed — the same class of bug as R1 in
:mod:`runtime_worker.file_store_wiring`. Conversation scope is what covers the
supervisor, every subagent, the approval-resume harness and the next turn from
one directory. Run scope would re-empty every turn and reproduce the defect one
level up, which is exactly why ``drafts/`` is conversation-scoped and
``tool-results/`` is not.

The scratch rather than ``RUNTIME_FILE_STORE_ROOT`` for two reasons. The scratch
is the one tree designed for a human to browse (see
:mod:`agent_runtime.capabilities.desktop.agent_scratch`), whereas the file store
hashes every path segment through ``FileStoreLayout.safe_key``, so ``linear/``
would appear as a 64-hex directory. And the file store is the *record*; a
catalog is disposable ephemera rebuilt on every load. Retention comes free:
``ConversationScratch.delete()`` already ``rmtree``s the whole subtree and is
already cascaded from both conversation-deletion sites.

Desktop only. The gate is :class:`~runtime_worker.agent_scratch_wiring.AgentScratchWorkerWiring`
— a workspace backend exists — for the reason stated there: on a hosted image
there is no user at the machine, and writing ``~/.0xcopilot/.tmp/<conv>/`` would
put per-tenant scratch on the SERVER's home directory. Everywhere else the
in-memory store is composed instead, unchanged.

Consistency, stated honestly
----------------------------
The in-memory store swaps a whole mapping, so a reader never sees half a
catalog. On disk that guarantee is per-SERVER: each publish stages the new tree
beside the target and lands it with two ``os.replace`` calls, so a reader sees
the old tree or the new one, with a sub-millisecond window in between where the
directory is absent (and ``read`` answers with the "run ``ls /mcp/``" directive
rather than wrong bytes). Concurrent publishes of DIFFERENT servers cannot
interleave at all — different directories. There is no directory-level atomic
swap on POSIX, so this is the honest bound rather than a claimed one.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_runtime.capabilities.mcp.catalog import (
    Keys,
    Log,
    McpCatalogPaths,
    McpCatalogSeedOutcome,
    McpCatalogSeedPlanner,
    McpServerCatalog,
)
from runtime_adapters.file._paths import FileStoreLayout

_LOGGER = logging.getLogger(__name__)


class _Layout:
    """On-disk spellings. Local by construction: nothing here is a public path."""

    #: A directory being written, or one being retired by a swap. Dot-prefixed
    #: so a half-written tree can never be listed as a server: every reader here
    #: skips dotted entries, and ``_SAFE_SEGMENT`` upstream forbids a server
    #: slug from starting with a dot in the first place.
    STAGING_SUFFIX = ".staging"
    RETIRED_SUFFIX = ".retired"
    DOT = "."
    SEPARATOR = "/"
    ENCODING = "utf-8"
    #: ``tools/`` exists only for a LOADED catalog, so its presence IS the tier
    #: marker — no sidecar state file, and a human sees the same truth.
    LOADED_MARKER_DIR = Keys.Dir.TOOLS


class _Log:
    """Structured event names for the file-backed catalog."""

    ROOT = "mcp_catalog.file_root path=%s"
    ROOT_UNAVAILABLE = "mcp_catalog.file_root_unavailable path=%s"
    PUBLISH_FAILED = "mcp_catalog.file_publish_failed server=%s"
    PRUNE_FAILED = "mcp_catalog.file_prune_failed server=%s"
    UNREADABLE = "mcp_catalog.file_unreadable server=%s"


class FileMcpCatalogStore:
    """Publisher + reader for the MCP catalog, backed by ordinary files.

    Implements the same two protocols the in-process store does
    (:class:`~agent_runtime.capabilities.mcp.catalog.McpCatalogPublisher` and
    :class:`~agent_runtime.capabilities.mcp.catalog.McpCatalogReader`), so
    :class:`~agent_runtime.capabilities.mcp.catalog_backend.McpCatalogBackend`
    mounts it unchanged and there is exactly ONE catalog per run — never a
    file tree shadowed by an in-memory copy.
    """

    def __init__(self, root: Path) -> None:
        """``root`` is the ``mcp/`` directory inside one conversation's scratch.

        Created here, best-effort, so the directory a human is told to inspect
        exists even on a chat that never loads a connector — and so the
        approval-resume path, which never provisions the scratch, still lands in
        a real tree.
        """

        self._root = root
        self._cache: Mapping[str, str] | None = None
        self._cache_directories: tuple[str, ...] = ()
        self._cache_key: tuple[object, ...] | None = None
        #: Bumped by our own writes so the cache cannot survive a publish that
        #: landed inside one filesystem timestamp tick.
        self._generation = 0
        try:
            FileStoreLayout.ensure_dir(root)
        except OSError:
            _LOGGER.warning(_Log.ROOT_UNAVAILABLE, root, exc_info=True)
        _LOGGER.info(_Log.ROOT, root)

    @property
    def root(self) -> Path:
        """The ``mcp/`` directory this store owns — the path a human inspects."""

        return self._root

    # --- McpCatalogPublisher ------------------------------------------------

    def publish(self, catalog: McpServerCatalog) -> None:
        """Replace this server's directory with ``catalog``'s artifacts.

        Best-effort, like every other write into the scratch: a catalog that
        cannot be written degrades to "this server is not browsable this run",
        never to a failed run. The loader's pointer would then name files that
        do not exist, which the backend answers with its ``ls /mcp/`` directive.
        """

        if not self._write_or_warn(catalog):
            return
        self._invalidate()
        Log.published(catalog)

    def seed(self, catalogs: Sequence[McpServerCatalog]) -> McpCatalogSeedOutcome:
        """Apply a run-start seed pass over whatever the previous turn left."""

        plan = McpCatalogSeedPlanner.plan(
            incoming=catalogs,
            known=self.server_names(),
            loaded=self.loaded_server_names(),
        )
        for server_name in plan.prune:
            self._remove_server(server_name)
        for catalog in plan.publish:
            self._write_or_warn(catalog)
        self._invalidate()
        outcome = plan.outcome()
        outcome.log()
        return outcome

    def _write_or_warn(self, catalog: McpServerCatalog) -> bool:
        """Write one server's tree; ``False`` (and one warning) on failure."""

        try:
            self._write_server(catalog)
        except OSError:
            _LOGGER.warning(_Log.PUBLISH_FAILED, catalog.server_name, exc_info=True)
            return False
        return True

    # --- McpCatalogReader ---------------------------------------------------

    def snapshot(self) -> Mapping[str, str]:
        """Return ``{public_path: content}`` for every artifact on disk."""

        self._refresh()
        return self._cache or {}

    def directories(self) -> tuple[str, ...]:
        """Return the public paths of directories that exist even when empty."""

        self._refresh()
        return self._cache_directories

    # --- inspection ---------------------------------------------------------

    def server_names(self) -> tuple[str, ...]:
        """Return every server with a directory on disk, sorted."""

        return tuple(directory.name for directory in self._server_dirs())

    def loaded_server_names(self) -> tuple[str, ...]:
        """Return the servers ``load_mcp_server`` has already enriched.

        Read from the layout rather than from remembered state: the whole point
        of the file store is that a NEW process (turn 2, or the approval-resume
        harness) reaches the same answer as the one that wrote it.
        """

        return tuple(
            directory.name
            for directory in self._server_dirs()
            if (directory / _Layout.LOADED_MARKER_DIR).is_dir()
        )

    # --- writing ------------------------------------------------------------

    def _write_server(self, catalog: McpServerCatalog) -> None:
        """Stage this server's tree, then land it with two atomic renames."""

        name = catalog.server_name
        FileStoreLayout.ensure_dir(self._root)
        staging = self._root / f"{_Layout.DOT}{name}{_Layout.STAGING_SUFFIX}"
        retired = self._root / f"{_Layout.DOT}{name}{_Layout.RETIRED_SUFFIX}"
        target = self._root / name
        self._discard(staging)
        self._discard(retired)
        FileStoreLayout.ensure_dir(staging)
        for directory in catalog.directories:
            relative = self._relative_to_server(directory, name)
            if relative is not None:
                FileStoreLayout.ensure_dir(staging / relative)
        for file in catalog.files:
            relative = self._relative_to_server(file.path, name)
            if relative is None:
                continue
            destination = staging / relative
            FileStoreLayout.ensure_dir(destination.parent)
            destination.write_text(file.content, encoding=_Layout.ENCODING)
            FileStoreLayout.restrict_file(destination)
        if target.exists():
            os.replace(target, retired)
        os.replace(staging, target)
        self._discard(retired)

    def _remove_server(self, server_name: str) -> None:
        """Drop a server the user is no longer authorized for."""

        try:
            self._discard(self._root / server_name)
        except OSError:
            _LOGGER.warning(_Log.PRUNE_FAILED, server_name, exc_info=True)

    @staticmethod
    def _discard(path: Path) -> None:
        """Remove ``path`` and everything under it, if it exists."""

        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _relative_to_server(public_path: str, server_name: str) -> str | None:
        """Return ``public_path`` relative to ``/mcp/<server>/``, or ``None``.

        Refuses anything that would escape the server directory. The contracts
        upstream already normalize tool names to slugs and validate the ``/mcp/``
        prefix, so this is defense in depth against a future caller — but a
        catalog is projected from connector-authored names, and connector-authored
        names are untrusted input.
        """

        prefix = f"{McpCatalogPaths.server_dir(server_name)}{_Layout.SEPARATOR}"
        if not public_path.startswith(prefix):
            return None
        relative = public_path[len(prefix) :].strip(_Layout.SEPARATOR)
        if not relative:
            return None
        parts = relative.split(_Layout.SEPARATOR)
        if any(part in {"", os.curdir, os.pardir} for part in parts):
            return None
        return relative

    # --- reading ------------------------------------------------------------

    def _server_dirs(self) -> tuple[Path, ...]:
        """Return the per-server directories, skipping staging/retired trees."""

        try:
            children = sorted(self._root.iterdir())
        except OSError:
            return ()
        return tuple(
            child
            for child in children
            if child.is_dir() and not child.name.startswith(_Layout.DOT)
        )

    def _invalidate(self) -> None:
        self._generation += 1
        self._cache_key = None

    def _refresh(self) -> None:
        """Re-materialize the snapshot only when the tree has actually changed.

        ``McpCatalogBackend`` calls ``snapshot()`` on EVERY ``ls`` / ``read`` /
        ``grep`` / ``glob``, and a file-backed snapshot is a full directory walk
        plus a read of every artifact — at ten connectors that is hundreds of
        reads per call. The key is our own write generation plus the directory
        mtimes, and since every publish lands by renaming INTO ``mcp/``, the
        root's own mtime moves on each one.
        """

        key = self._fingerprint()
        if self._cache is not None and key == self._cache_key:
            return
        files: dict[str, str] = {}
        directories: list[str] = []
        for server_dir in self._server_dirs():
            self._collect_server(server_dir, files, directories)
        self._cache = files
        self._cache_directories = tuple(sorted(directories))
        self._cache_key = key

    def _fingerprint(self) -> tuple[object, ...]:
        try:
            root_mtime = self._root.stat().st_mtime_ns
        except OSError:
            return (self._generation,)
        parts: list[object] = [self._generation, root_mtime]
        for server_dir in self._server_dirs():
            try:
                parts.append((server_dir.name, server_dir.stat().st_mtime_ns))
            except OSError:  # pragma: no cover - raced removal
                parts.append((server_dir.name, None))
        return tuple(parts)

    def _collect_server(
        self,
        server_dir: Path,
        files: dict[str, str],
        directories: list[str],
    ) -> None:
        """Add one server's files and always-present directories to the view."""

        server_name = server_dir.name
        for path in sorted(server_dir.rglob("*")):
            parts = path.relative_to(server_dir).parts
            if any(part.startswith(_Layout.DOT) for part in parts):
                continue
            public = (
                f"{McpCatalogPaths.server_dir(server_name)}"
                f"{_Layout.SEPARATOR}{_Layout.SEPARATOR.join(parts)}"
            )
            if path.is_dir():
                directories.append(f"{public}{_Layout.SEPARATOR}")
                continue
            content = self._read_text(path, server_name)
            if content is not None:
                files[public] = content

    @staticmethod
    def _read_text(path: Path, server_name: str) -> str | None:
        """Return one artifact's text, or ``None`` when it cannot be read.

        A file that will not decode is skipped rather than surfaced: the catalog
        is a projection, and half a projection is worse than one missing entry
        the model is told to re-load.
        """

        try:
            return path.read_text(encoding=_Layout.ENCODING)
        except (OSError, UnicodeDecodeError):
            _LOGGER.warning(_Log.UNREADABLE, server_name, exc_info=True)
            return None


__all__ = ("FileMcpCatalogStore",)
