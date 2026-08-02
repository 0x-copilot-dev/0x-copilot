"""Read-only Deep Agents backend serving the MCP catalog at ``/mcp/``.

Mounted as a ``CompositeBackend`` route exactly like ``/subagents/`` and
``/large_tool_results/``: the artifacts are authored by ai-backend when a
connector loads, and the model reaches them with the filesystem tools it
already has. Nothing here is provider-specific — ``ls`` / ``read_file`` /
``glob`` / ``grep`` are primitives every model exposes.

**Read-only to the model, deliberately.** The catalog is a projection of what a
connector published; letting the model edit it would let one turn rewrite the
schema a later turn dispatches against, and a "fix" to a tool file would
silently diverge from the descriptor the gateway validates. Writes are refused
with a stable, safe message rather than an exception, so a misdirected
``write_file`` is a readable answer instead of a run failure.

Path spelling: ``CompositeBackend`` strips the ``/mcp`` route prefix before
calling us and re-prepends it to every path we return, so this backend speaks
the **mount-relative** spelling on the wire (``/linear/SERVER.md``). Direct
callers that pass the full public path (``/mcp/linear/SERVER.md``) are also
accepted — the mount-relative reading is tried first, so a connector literally
named ``mcp`` still resolves.
"""

from __future__ import annotations

from collections.abc import Mapping

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

from agent_runtime.capabilities.mcp.catalog import (
    Keys,
    McpCatalogReader,
    Messages,
)


class Values:
    """Stable values used at the mount boundary."""

    ROOT = "/"
    SEPARATOR = "/"
    ENCODING = "utf-8"
    GLOB_FLAGS = wcglob.BRACE | wcglob.GLOBSTAR


class McpCatalogMount:
    """Translate between the wire spelling and the catalog's public paths."""

    #: ``/mcp`` — what ``CompositeBackend`` strips on the way in.
    PREFIX = Keys.Dir.ROOT

    @classmethod
    def to_mount(cls, public_path: str) -> str:
        """Strip the route prefix from a public catalog path."""

        return public_path[len(cls.PREFIX) :]

    @classmethod
    def candidates(cls, path: str | None) -> tuple[str, ...]:
        """Return the mount-relative readings of ``path``, best first.

        The composite always hands us a mount-relative path, so that reading
        wins; a full public path from a direct caller is the fallback. Ordering
        this way keeps a server named ``mcp`` addressable.
        """

        raw = (path or Values.ROOT).strip() or Values.ROOT
        normalized = raw if raw.startswith(Values.SEPARATOR) else f"/{raw}"
        readings = [normalized]
        if normalized == cls.PREFIX:
            readings.append(Values.ROOT)
        elif normalized.startswith(f"{cls.PREFIX}/"):
            readings.append(normalized[len(cls.PREFIX) :])
        return tuple(readings)


class McpCatalogBackend(BackendProtocol):
    """Serve the published MCP catalog as a read-only virtual filesystem."""

    #: Route prefix this backend is mounted under.
    PATH_PREFIX: str = f"{Keys.Dir.ROOT}/"

    def __init__(self, catalog: McpCatalogReader) -> None:
        self._catalog = catalog

    # --- BackendProtocol surface -------------------------------------------

    def ls(self, path: str) -> LsResult:
        """List the immediate children of a catalog directory."""

        return self._ls(path)

    async def als(self, path: str) -> LsResult:
        """Async twin of :meth:`ls`."""

        return self._ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Return one catalog artifact, sliced by source line."""

        return self._read(file_path, offset, limit)

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        """Async twin of :meth:`read`."""

        return self._read(file_path, offset, limit)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Literal-text search across catalog artifacts."""

        return self._grep(pattern, path, glob)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Async twin of :meth:`grep`."""

        return self._grep(pattern, path, glob)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Path-pattern search across catalog artifacts."""

        return self._glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Async twin of :meth:`glob`."""

        return self._glob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Refuse — the catalog is authored by the loader, not the model."""

        del file_path, content
        return WriteResult(error=Messages.READ_ONLY)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Refuse — the catalog is authored by the loader, not the model."""

        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Refuse — an edited descriptor would diverge from what dispatch validates."""

        del file_path, old_string, new_string, replace_all
        return EditResult(error=Messages.READ_ONLY)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Refuse — an edited descriptor would diverge from what dispatch validates."""

        return self.edit(file_path, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> WriteResult:
        """Refuse — catalog artifacts are replaced by a reload, never deleted."""

        del file_path
        return WriteResult(error=Messages.READ_ONLY)

    async def amkdir(self, path: str) -> WriteResult:
        """Refuse — the catalog layout is fixed by the loader."""

        del path
        return WriteResult(error=Messages.READ_ONLY)

    async def amove(self, source: str, destination: str) -> WriteResult:
        """Refuse — catalog paths are addressed by server and tool name."""

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
        """Return the current catalog as ``{mount_relative_path: content}``."""

        return {
            McpCatalogMount.to_mount(path): content
            for path, content in self._catalog.snapshot().items()
        }

    def _directories(self) -> tuple[str, ...]:
        """Return always-present directories in the mount-relative spelling."""

        return tuple(
            McpCatalogMount.to_mount(directory)
            for directory in self._catalog.directories()
        )

    def _resolve_file(self, files: Mapping[str, str], path: str) -> str | None:
        """Return the mount-relative key naming an existing artifact, or ``None``."""

        for candidate in McpCatalogMount.candidates(path):
            if candidate in files:
                return candidate
        return None

    def _resolve_dir(
        self, files: Mapping[str, str], directories: tuple[str, ...], path: str | None
    ) -> str:
        """Return the mount-relative directory to operate on.

        Falls back to the best-effort first reading when nothing matches, so an
        unknown directory lists empty rather than mis-resolving.
        """

        candidates = McpCatalogMount.candidates(path)
        for candidate in candidates:
            prefix = (
                candidate if candidate.endswith(Values.SEPARATOR) else f"{candidate}/"
            )
            if candidate == Values.ROOT or any(key.startswith(prefix) for key in files):
                return candidate
            if any(directory.startswith(prefix) for directory in directories):
                return candidate
        return candidates[0]

    def _ls(self, path: str) -> LsResult:
        files = self._files()
        directories = self._directories()
        base = self._resolve_dir(files, directories, path)
        prefix = base if base.endswith(Values.SEPARATOR) else f"{base}/"
        entries: list[FileInfo] = []
        subdirs: set[str] = set()
        for key, content in files.items():
            if not key.startswith(prefix):
                continue
            relative = key[len(prefix) :]
            if Values.SEPARATOR in relative:
                subdirs.add(f"{prefix}{relative.split(Values.SEPARATOR)[0]}/")
                continue
            entries.append(
                FileInfo(
                    path=key,
                    is_dir=False,
                    size=len(content.encode(Values.ENCODING)),
                )
            )
        for directory in directories:
            if not directory.startswith(prefix) or directory == prefix:
                continue
            remainder = directory[len(prefix) :]
            if remainder.count(Values.SEPARATOR) == 1:
                subdirs.add(f"{prefix}{remainder}")
        entries.extend(
            FileInfo(path=subdir, is_dir=True, size=0) for subdir in sorted(subdirs)
        )
        entries.sort(key=lambda entry: entry.get("path", ""))
        return LsResult(entries=entries)

    def _read(self, file_path: str, offset: int, limit: int) -> ReadResult:
        files = self._files()
        key = self._resolve_file(files, file_path)
        if key is None:
            return ReadResult(error=Messages.NOT_FOUND)
        file_data = self._file_data(files[key])
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=FileData(content=sliced, encoding=Values.ENCODING))

    def _grep(self, pattern: str, path: str | None, glob: str | None) -> GrepResult:
        files = self._files()
        base = self._resolve_dir(files, self._directories(), path)
        return grep_matches_from_files(
            {key: self._file_data(content) for key, content in files.items()},
            pattern,
            base,
            glob,
        )

    def _glob(self, pattern: str, path: str | None) -> GlobResult:
        files = self._files()
        base = self._resolve_dir(files, self._directories(), path)
        root = base.rstrip(Values.SEPARATOR)
        effective = self._mount_pattern(pattern)
        matches: list[FileInfo] = []
        for key, content in files.items():
            relative = self._relative_to(key, root)
            if relative is None:
                continue
            if wcglob.globmatch(relative, effective, flags=Values.GLOB_FLAGS):
                matches.append(
                    FileInfo(
                        path=key,
                        is_dir=False,
                        size=len(content.encode(Values.ENCODING)),
                    )
                )
        matches.sort(key=lambda entry: entry.get("path", ""))
        return GlobResult(matches=matches)

    @classmethod
    def _mount_pattern(cls, pattern: str) -> str:
        """Drop a route prefix a caller left on the pattern, then de-anchor it.

        ``CompositeBackend`` strips the prefix on one glob path and leaves it on
        another, so both spellings arrive here.
        """

        bare = pattern.strip().lstrip(Values.SEPARATOR)
        route = f"{McpCatalogMount.PREFIX.lstrip(Values.SEPARATOR)}/"
        if bare.startswith(route):
            bare = bare[len(route) :]
        return bare.lstrip(Values.SEPARATOR)

    @classmethod
    def _relative_to(cls, key: str, root: str) -> str | None:
        """Return ``key`` relative to ``root``, or ``None`` when outside it."""

        if not root:
            return key.lstrip(Values.SEPARATOR)
        if key == root:
            return key.split(Values.SEPARATOR)[-1]
        prefix = f"{root}/"
        if key.startswith(prefix):
            return key[len(prefix) :]
        return None

    @classmethod
    def _file_data(cls, content: str) -> FileData:
        """Wrap raw text in the ``FileData`` shape deepagents' helpers expect."""

        return FileData(content=content, encoding=Values.ENCODING)


__all__ = ["McpCatalogBackend", "McpCatalogMount"]
