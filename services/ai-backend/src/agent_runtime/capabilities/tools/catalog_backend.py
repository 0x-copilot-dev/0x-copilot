"""Read-only Deep Agents backend serving first-party tool guidance at ``/tools/``.

Mounted as a ``CompositeBackend`` route exactly like ``/mcp/``, ``/subagents/``
and ``/large_tool_results/``: the files are authored by ai-backend and the model
reaches them with the primitives it already has. Nothing here is
provider-specific — ``ls`` / ``read_file`` / ``glob`` / ``grep`` are exposed by
every model, which is why the MCP catalog chose a filesystem over a bespoke
"expand this tool" tool, and why this does too. A new tool would cost resident
schema tokens to save resident schema tokens.

**Why this is a separate class from** :class:`McpCatalogBackend`. That backend
serves a MUTABLE, two-tier, per-server tree: seeded at run start, replaced when
``load_mcp_server`` returns descriptors over the network, with four distinct
empty-listing directives (``NO_SERVERS`` / ``UNKNOWN_SERVER`` /
``SERVER_NOT_LOADED`` / genuinely-empty) that exist to tell those states apart.
``/tools/`` is a FLAT, FROZEN directory of text known at import time. Every one
of those mechanisms would become a parameter with exactly one value, and
parameterising a working, live-debugged module to serve a case that needs none
of it is how a shared abstraction ends up harder to read than the two things it
replaced. What is genuinely shared — slicing a read, matching a grep — is
imported from deepagents' own helpers by both.

**An empty listing is an error, not a success.** ``ls /mcp`` used to answer
``[]``, and a live model correctly read that as "this capability does not exist"
and stopped. Every miss here answers with a directive naming the one call that
fixes it.

Path spelling: ``CompositeBackend`` strips the ``/tools`` route prefix before
calling us and re-prepends it to the paths we return, so this backend speaks the
**mount-relative** spelling on the wire (``/publish_artifact.md``). Direct
callers passing the full public path are also accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileData,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import grep_matches_from_files, slice_read_response
from wcmatch import glob as wcglob

from agent_runtime.capabilities.tools.catalog import (
    Keys,
    Messages,
    ToolGuidanceCatalog,
)


class Values:
    """Stable values used at the mount boundary."""

    ROOT: Final[str] = "/"
    SEPARATOR: Final[str] = "/"
    ENCODING: Final[str] = "utf-8"
    GLOB_FLAGS = wcglob.BRACE | wcglob.GLOBSTAR


class ToolCatalogMount:
    """Translate between the wire spelling and the catalog's public paths."""

    #: ``/tools`` — what ``CompositeBackend`` strips on the way in.
    PREFIX: Final[str] = Keys.Dir.ROOT

    @classmethod
    def to_mount(cls, public_path: str) -> str:
        """Strip the route prefix from a public catalog path."""

        return public_path[len(cls.PREFIX) :]

    @classmethod
    def candidates(cls, path: str | None) -> tuple[str, ...]:
        """Return the mount-relative readings of ``path``, best first.

        The composite always hands us a mount-relative path, so that reading
        wins; the full public path from a direct caller is the fallback.
        """

        raw = (path or Values.ROOT).strip() or Values.ROOT
        normalized = raw if raw.startswith(Values.SEPARATOR) else f"/{raw}"
        readings = [normalized]
        if normalized == cls.PREFIX:
            readings.append(Values.ROOT)
        elif normalized.startswith(f"{cls.PREFIX}/"):
            readings.append(normalized[len(cls.PREFIX) :])
        return tuple(readings)


class ToolCatalogBackend(BackendProtocol):
    """Serve the frozen first-party tool guidance as a read-only filesystem."""

    #: Route prefix this backend is mounted under.
    PATH_PREFIX: Final[str] = f"{Keys.Dir.ROOT}/"

    def __init__(self, catalog: ToolGuidanceCatalog) -> None:
        self._catalog = catalog

    # --- BackendProtocol surface -------------------------------------------

    def ls(self, path: str) -> LsResult:
        """List the immediate children of the guidance directory."""

        return self._ls(path)

    async def als(self, path: str) -> LsResult:
        """Async twin of :meth:`ls`."""

        return self._ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Return one guidance file, sliced by source line."""

        return self._read(file_path, offset, limit)

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        """Async twin of :meth:`read`."""

        return self._read(file_path, offset, limit)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Literal-text search across the guidance files."""

        return self._grep(pattern, path, glob)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Async twin of :meth:`grep`."""

        return self._grep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Path-pattern search across the guidance files."""

        return self._glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Async twin of :meth:`glob`."""

        return self._glob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Refuse — the guidance is authored by the runtime, not the model."""

        del file_path, content
        return WriteResult(error=Messages.READ_ONLY)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Refuse — the guidance is authored by the runtime, not the model."""

        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Refuse — an edited rule would diverge from what the tool enforces."""

        del file_path, old_string, new_string, replace_all
        return EditResult(error=Messages.READ_ONLY)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Refuse — an edited rule would diverge from what the tool enforces."""

        return self.edit(file_path, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> WriteResult:
        """Refuse — guidance is a projection and is never deleted."""

        del file_path
        return WriteResult(error=Messages.READ_ONLY)

    async def amkdir(self, path: str) -> WriteResult:
        """Refuse — ``/tools/`` is flat and its layout is fixed."""

        del path
        return WriteResult(error=Messages.READ_ONLY)

    async def amove(self, source: str, destination: str) -> WriteResult:
        """Refuse — a guidance file is addressed by its tool's name."""

        del source, destination
        return WriteResult(error=Messages.READ_ONLY)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        """Refuse — nothing is uploaded into a projection."""

        del files
        return [WriteResult(error=Messages.READ_ONLY)]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[object]:
        """Refuse — nothing is uploaded into a projection."""

        return self.upload_files(files)

    # --- implementation ----------------------------------------------------

    def _files(self) -> dict[str, str]:
        """Return the catalog as ``{mount_relative_path: content}``."""

        return {
            ToolCatalogMount.to_mount(path): content
            for path, content in self._catalog.snapshot().items()
        }

    def _resolve_file(self, files: Mapping[str, str], path: str) -> str | None:
        """Return the mount-relative key naming an existing file, or ``None``."""

        for candidate in ToolCatalogMount.candidates(path):
            if candidate in files:
                return candidate
        return None

    def _ls(self, path: str) -> LsResult:
        del path  # ``/tools`` is flat: every path under it lists the same set.
        files = self._files()
        if not files:
            return LsResult(error=Messages.EMPTY)
        entries = [
            FileInfo(
                path=key,
                is_dir=False,
                size=len(content.encode(Values.ENCODING)),
            )
            for key, content in sorted(files.items())
        ]
        return LsResult(entries=entries)

    def _read(self, file_path: str, offset: int, limit: int) -> ReadResult:
        files = self._files()
        key = self._resolve_file(files, file_path)
        if key is None:
            return ReadResult(error=Messages.NOT_FOUND)
        sliced = slice_read_response(self._file_data(files[key]), offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=FileData(content=sliced, encoding=Values.ENCODING))

    def _grep(self, pattern: str, path: str | None, glob: str | None) -> GrepResult:
        del path  # Flat directory: the only base is the mount root.
        return grep_matches_from_files(
            {key: self._file_data(content) for key, content in self._files().items()},
            pattern,
            Values.ROOT,
            glob,
        )

    def _glob(self, pattern: str, path: str | None) -> GlobResult:
        del path
        effective = self._mount_pattern(pattern)
        matches = [
            FileInfo(
                path=key,
                is_dir=False,
                size=len(content.encode(Values.ENCODING)),
            )
            for key, content in self._files().items()
            if wcglob.globmatch(
                key.lstrip(Values.SEPARATOR), effective, flags=Values.GLOB_FLAGS
            )
        ]
        matches.sort(key=lambda entry: entry.get("path", ""))
        return GlobResult(matches=matches)

    @classmethod
    def _mount_pattern(cls, pattern: str) -> str:
        """Drop a route prefix a caller left on the pattern, then de-anchor it.

        ``CompositeBackend`` strips the prefix on one glob path and leaves it on
        another, so both spellings arrive here.
        """

        bare = pattern.strip().lstrip(Values.SEPARATOR)
        route = f"{ToolCatalogMount.PREFIX.lstrip(Values.SEPARATOR)}/"
        if bare.startswith(route):
            bare = bare[len(route) :]
        return bare.lstrip(Values.SEPARATOR)

    @classmethod
    def _file_data(cls, content: str) -> FileData:
        """Wrap raw text in the ``FileData`` shape deepagents' helpers expect."""

        return FileData(content=content, encoding=Values.ENCODING)


__all__ = ["ToolCatalogBackend", "ToolCatalogMount"]
