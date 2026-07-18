"""File-native persistence for agent/user memory, skills, and subagent defs.

The desktop (``single_user_desktop``) file store keeps conversation history as
plaintext JSONL under ``workspaces/``. This module extends that plaintext-first
philosophy to the three long-lived agent-state kinds that were previously held
only in Deep Agents' ephemeral, per-run ``StateBackend`` (memory) or shipped
read-only with the wheel (skills / subagent defs):

* **memory** — user / agent / org notes the model reads and writes through the
  ``/memories/`` · ``/policies/`` · ``/skills/`` virtual paths. Persisted as a
  canonical ``memory/<scope>/<key>.json`` plus a human ``.md`` view that is
  regenerated from the JSON, so the folder is inspectable and rebuildable and
  the ``.md`` never becomes an authoritative second copy.
* **skills** — Agent-Skills ``SKILL.md`` bundles written under ``skills/<name>/``
  so the existing :class:`SkillSourceRegistry` discovers them exactly like the
  built-in wheel skills (a user can hand-edit or drop in a folder).
* **subagent definitions** — compact :class:`SubagentDefinition` configs written
  as ``subagent_defs/<name>.json`` and loaded back through the standard
  :class:`SubagentDefinitionProvider` port into the dynamic subagent catalog.

Everything here is **gated** on the file store being active
(:class:`FileAgentStoreGate`) — ``RUNTIME_STORE_BACKEND=file`` with
``RUNTIME_FILE_STORE_ROOT`` set. On the web / postgres / in-memory images the
gate returns ``None`` and none of this loads, so those deployments are
byte-identical.

Secrets never land on disk: memory metadata is passed through
:class:`~agent_runtime.context.memory.contracts.MemoryRedactor`, which replaces
credential-shaped keys (``DENY_KEYS``) with ``[redacted]`` before the JSON is
written. Provider keys, bearer tokens, and vault material live only in the
in-memory runtime context and are never handed to these stores.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.context.memory.backends import MemoryBackendRoute, MemoryRoutePlan
from agent_runtime.context.memory.contracts import (
    MemoryMetadata,
    MemoryRedactor,
    MemoryScope,
)
from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file._paths import FileStoreLayout


class _Env:
    """Environment signals that gate the file store (mirror the adapter factory)."""

    STORE_BACKEND = "RUNTIME_STORE_BACKEND"
    FILE_STORE_ROOT = "RUNTIME_FILE_STORE_ROOT"
    FILE_BACKEND_VALUE = "file"


class _Dirs:
    """Top-level store subdirectories owned by this module."""

    MEMORY = "memory"
    SKILLS = "skills"
    SUBAGENT_DEFS = "subagent_defs"


class FileAgentStoreGate:
    """Resolve the active file-store layout from the environment, or ``None``.

    The single gate used by the worker wiring so the desktop-only file adapter
    is never imported on non-file images. Matches the fail-open-to-``None``
    contract of :class:`~runtime_worker.file_store_wiring.FileStoreWorkerWiring`:
    when either signal is missing the caller keeps its prior behavior unchanged.
    """

    @classmethod
    def active_layout(cls) -> FileStoreLayout | None:
        """Return a :class:`FileStoreLayout` when the file store is active."""

        backend = os.environ.get(_Env.STORE_BACKEND, "").strip().lower()
        root = os.environ.get(_Env.FILE_STORE_ROOT, "").strip()
        if backend != _Env.FILE_BACKEND_VALUE or not root:
            return None
        return FileStoreLayout(Path(root))


class MemoryDocument(BaseModel):
    """Canonical, rebuildable representation of one persisted memory file.

    The ``.json`` on disk is exactly this model; the sibling ``.md`` is a
    disposable human view regenerated from it. ``scope_namespace`` and
    ``memory_path`` are the real logical identifiers, so a full scan of the
    ``memory/`` tree reconstructs every key without reversing a path hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_path: str
    scope_namespace: tuple[str, ...] = Field(min_length=1)
    scope_type: str
    content: str
    metadata: MemoryMetadata = Field(default_factory=dict)
    version: int = Field(ge=1, default=1)
    updated_at: str


class FileMemoryStore:
    """Read/write persisted memory documents under ``<root>/memory/``.

    Keyed by ``(scope, memory_path)``. The scope namespace and the memory path
    are each hashed to a safe path segment (never used raw), while the canonical
    identifiers live inside the JSON so the store is rebuilt by scanning, never
    by reversing a hash. Single in-process writer (the desktop worker), so no
    lock is needed: JSON is rewritten atomically (temp + fsync + rename).
    """

    _JSON_SUFFIX = ".json"
    _MD_SUFFIX = ".md"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._layout = layout

    # ----- path derivation ----------------------------------------------

    @property
    def _root(self) -> Path:
        return self._layout.root / _Dirs.MEMORY

    def _scope_dir(self, scope: MemoryScope) -> Path:
        key = FileStoreLayout.safe_key("/".join(scope.namespace))
        return self._root / key

    def _doc_path(self, scope: MemoryScope, memory_path: str) -> Path:
        key = FileStoreLayout.safe_key(memory_path)
        return self._scope_dir(scope) / (key + self._JSON_SUFFIX)

    def _view_path(self, scope: MemoryScope, memory_path: str) -> Path:
        key = FileStoreLayout.safe_key(memory_path)
        return self._scope_dir(scope) / (key + self._MD_SUFFIX)

    # ----- read / write --------------------------------------------------

    def write(
        self,
        *,
        scope: MemoryScope,
        memory_path: str,
        content: str,
        metadata: Mapping[str, object] | None = None,
    ) -> MemoryDocument:
        """Persist one memory document (canonical JSON + human ``.md`` view).

        Metadata is redacted through :class:`MemoryRedactor` before it touches
        disk, so a credential-shaped key can never be written verbatim. The
        version increments monotonically off any existing document at the key.
        """

        existing = self.read(scope=scope, memory_path=memory_path)
        document = MemoryDocument(
            memory_path=memory_path,
            scope_namespace=scope.namespace,
            scope_type=scope.scope_type.value,
            content=content,
            metadata=MemoryRedactor.redact_metadata(dict(metadata or {})),
            version=(existing.version + 1) if existing is not None else 1,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        FileStoreLayout.ensure_dir(self._scope_dir(scope))
        JsonlIo.rewrite_json(
            self._doc_path(scope, memory_path), document.model_dump(mode="json")
        )
        self._write_view(scope, document)
        return document

    def read(self, *, scope: MemoryScope, memory_path: str) -> MemoryDocument | None:
        """Return the document at ``(scope, memory_path)``, or ``None`` if absent."""

        raw = JsonlIo.read_json(self._doc_path(scope, memory_path))
        if raw is None:
            return None
        return MemoryDocument.model_validate(raw)

    def list_documents(self, scope: MemoryScope) -> tuple[MemoryDocument, ...]:
        """Return every document under ``scope``, ordered by memory path."""

        scope_dir = self._scope_dir(scope)
        if not scope_dir.is_dir():
            return ()
        documents: list[MemoryDocument] = []
        for path in sorted(scope_dir.iterdir()):
            if path.suffix != self._JSON_SUFFIX:
                continue
            raw = JsonlIo.read_json(path)
            if raw is not None:
                documents.append(MemoryDocument.model_validate(raw))
        return tuple(sorted(documents, key=lambda doc: doc.memory_path))

    def delete(self, *, scope: MemoryScope, memory_path: str) -> bool:
        """Remove a document's JSON + ``.md`` view. Returns whether JSON existed."""

        removed = False
        for path in (
            self._doc_path(scope, memory_path),
            self._view_path(scope, memory_path),
        ):
            try:
                path.unlink()
                if path.suffix == self._JSON_SUFFIX:
                    removed = True
            except FileNotFoundError:
                continue
        return removed

    def rebuild_human_views(self, scope: MemoryScope) -> int:
        """Regenerate every ``.md`` view under ``scope`` from its canonical JSON.

        The ``.md`` files are disposable — deleting them and calling this
        restores an identical human view, proving the JSON is the single source
        of truth. Returns the number of views written.
        """

        count = 0
        for document in self.list_documents(scope):
            self._write_view(scope, document)
            count += 1
        return count

    def _write_view(self, scope: MemoryScope, document: MemoryDocument) -> None:
        """Write the disposable human ``.md`` view for a document."""

        view_path = self._view_path(scope, document.memory_path)
        FileStoreLayout.ensure_dir(view_path.parent)
        header = (
            f"<!-- memory_path: {document.memory_path} -->\n"
            f"<!-- version: {document.version} · "
            f"updated_at: {document.updated_at} -->\n\n"
        )
        view_path.write_text(header + document.content, encoding="utf-8")
        FileStoreLayout.restrict_file(view_path)


# Accept the full ``/memories/<name>`` form and the prefix-stripped ``/<name>``
# form that ``CompositeBackend`` delivers after routing on the prefix.
_INNER_LEAF = re.compile(r"^/?(?P<name>[^/].*?)/?$")
_MEMORY_READ_ONLY_HINT = "Memory file not found."


class FileMemoryBackend(BackendProtocol):
    """Deep Agents ``BackendProtocol`` for one memory route, backed by files.

    Bound to a single :class:`MemoryBackendRoute` (its ``path_prefix`` + scope),
    so one instance is mounted per prefix in a ``CompositeBackend`` exactly like
    the ``/subagents/`` and ``/large_tool_results/`` file backends. Read / write
    / ls / edit persist through :class:`FileMemoryStore`; grep / glob are
    unsupported (memory is small and keyed, not searched).
    """

    def __init__(
        self,
        *,
        store: FileMemoryStore,
        route: MemoryBackendRoute,
    ) -> None:
        self._store = store
        self._route = route

    @property
    def path_prefix(self) -> str:
        """Route prefix this backend is mounted under (e.g. ``/memories/``)."""

        return self._route.path_prefix

    # --- path handling -----------------------------------------------------

    def _memory_path(self, file_path: str) -> str | None:
        """Reconstruct the canonical ``/prefix/<name>`` memory path, or ``None``."""

        stripped = file_path
        if stripped.startswith(self._route.path_prefix):
            stripped = "/" + stripped[len(self._route.path_prefix) :]
        match = _INNER_LEAF.match(stripped)
        if match is None:
            return None
        name = match.group("name")
        if not name or ".." in name.split("/"):
            return None
        return self._route.path_prefix + name

    # --- BackendProtocol: reads -------------------------------------------

    def ls(self, path: str) -> LsResult:
        # Entries are leaf-relative (``/<name>``); ``CompositeBackend``
        # re-prepends this backend's route prefix, exactly like the
        # ``/subagents/`` trace backend.
        entries: list[FileInfo] = []
        for document in self._store.list_documents(self._route.scope):
            leaf = document.memory_path[len(self._route.path_prefix) :]
            entries.append(
                {
                    "path": "/" + leaf,
                    "is_dir": False,
                    "modified_at": document.updated_at,
                }
            )
        return LsResult(entries=entries)

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        memory_path = self._memory_path(file_path)
        if memory_path is None:
            return ReadResult(error=_MEMORY_READ_ONLY_HINT)
        document = self._store.read(scope=self._route.scope, memory_path=memory_path)
        if document is None:
            return ReadResult(error=_MEMORY_READ_ONLY_HINT)
        return ReadResult(
            file_data={
                "content": document.content,
                "encoding": "utf-8",
                "modified_at": document.updated_at,
            }
        )

    async def aread(
        self, file_path: str, offset: int = 0, limit: int = 2000
    ) -> ReadResult:
        return self.read(file_path, offset, limit)

    # --- BackendProtocol: writes ------------------------------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        memory_path = self._memory_path(file_path)
        if memory_path is None:
            return WriteResult(error="Invalid memory path.")
        self._store.write(
            scope=self._route.scope, memory_path=memory_path, content=content
        )
        return WriteResult(path=memory_path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        memory_path = self._memory_path(file_path)
        if memory_path is None:
            return EditResult(error="Invalid memory path.")
        document = self._store.read(scope=self._route.scope, memory_path=memory_path)
        if document is None:
            return EditResult(error=_MEMORY_READ_ONLY_HINT)
        if old_string not in document.content:
            return EditResult(error="old_string was not found in the memory file.")
        occurrences = document.content.count(old_string)
        if occurrences > 1 and not replace_all:
            return EditResult(
                error="Ambiguous match — pass replace_all=True or a more specific anchor."
            )
        count = occurrences if replace_all else 1
        updated = document.content.replace(
            old_string, new_string, -1 if replace_all else 1
        )
        self._store.write(
            scope=self._route.scope,
            memory_path=memory_path,
            content=updated,
            metadata=document.metadata,
        )
        return EditResult(path=memory_path, occurrences=count)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    # --- BackendProtocol: unsupported search ------------------------------

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(matches=[])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(matches=[])


class FileMemoryBackendFactory:
    """Build per-route :class:`FileMemoryBackend` instances for a run.

    Injected as the ``backend_builder`` of
    :class:`~agent_runtime.context.memory.backends.ScopedMemoryBackendFactory`
    when the file store is active, so the memory subsystem reads and writes the
    on-disk ``memory/`` tree instead of the ephemeral ``StateBackend`` default.
    """

    def __init__(self, layout: FileStoreLayout) -> None:
        self._store = FileMemoryStore(layout)

    def __call__(self, plan: MemoryRoutePlan) -> dict[str, FileMemoryBackend]:
        """Return a ``{path_prefix: FileMemoryBackend}`` map for every route."""

        return {
            route.path_prefix: FileMemoryBackend(store=self._store, route=route)
            for route in plan.routes
        }


class FileSkillsStore:
    """Persist / discover Agent-Skills ``SKILL.md`` bundles under ``skills/``.

    Layout: ``skills/<name>/SKILL.md`` (+ optional bundled asset files). The
    directory is registered as a :class:`SkillSource` so the standard
    :class:`SkillSourceRegistry` discovers these exactly like wheel skills — no
    parsing lives here, only durable placement and a stable root path.
    """

    _SKILL_FILE = "SKILL.md"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._layout = layout

    @property
    def root(self) -> Path:
        """The ``skills/`` root, created on demand so discovery never errors."""

        root = self._layout.root / _Dirs.SKILLS
        return FileStoreLayout.ensure_dir(root)

    def write_skill(self, *, name: str, markdown: str) -> Path:
        """Persist a ``SKILL.md`` under ``skills/<name>/`` and return its dir."""

        safe_name = self._safe_name(name)
        directory = FileStoreLayout.ensure_dir(self.root / safe_name)
        skill_path = directory / self._SKILL_FILE
        skill_path.write_text(markdown, encoding="utf-8")
        FileStoreLayout.restrict_file(skill_path)
        return directory

    def write_asset(self, *, skill_name: str, relative_path: str, data: str) -> Path:
        """Persist a bundled asset next to a skill's ``SKILL.md``.

        Rejects absolute paths and ``..`` traversal so an asset can never escape
        the skill directory.
        """

        safe_name = self._safe_name(skill_name)
        directory = self.root / safe_name
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Skill asset path must be relative and traversal-free.")
        target = directory / candidate
        FileStoreLayout.ensure_dir(target.parent)
        target.write_text(data, encoding="utf-8")
        FileStoreLayout.restrict_file(target)
        return target

    @staticmethod
    def _safe_name(name: str) -> str:
        """Constrain a skill folder name to a filesystem-safe slug."""

        slug = re.sub(r"[^A-Za-z0-9._-]", "-", name.strip())
        if not slug or slug in {".", ".."}:
            raise ValueError("Skill name must contain filesystem-safe characters.")
        return slug


class FileSubagentDefinitionStore:
    """Persist / read compact subagent definitions as ``subagent_defs/<name>.json``.

    The JSON is exactly a serialized
    :class:`~agent_runtime.delegation.subagents.contracts.SubagentDefinition`,
    so a user can inspect or hand-edit a subagent's tools, skills, scopes, and
    timeouts. Loaded back by :class:`FileSubagentDefinitionProvider`.
    """

    _SUFFIX = ".json"

    def __init__(self, layout: FileStoreLayout) -> None:
        self._layout = layout

    @property
    def root(self) -> Path:
        """The ``subagent_defs/`` root, created on demand."""

        root = self._layout.root / _Dirs.SUBAGENT_DEFS
        return FileStoreLayout.ensure_dir(root)

    def write_definition(self, definition: object) -> Path:
        """Persist a ``SubagentDefinition`` (or its dict form) as JSON.

        Imported lazily so the domain contract is only pulled in on the desktop
        path. Validates before writing so a malformed definition never lands.
        """

        from agent_runtime.delegation.subagents.contracts import (  # noqa: PLC0415
            SubagentDefinition,
        )

        model = (
            definition
            if isinstance(definition, SubagentDefinition)
            else SubagentDefinition.model_validate(definition)
        )
        target = self.root / (FileSkillsStore._safe_name(model.name) + self._SUFFIX)
        JsonlIo.rewrite_json(target, model.model_dump(mode="json"))
        return target

    def read_raw_definitions(self) -> tuple[dict, ...]:
        """Return every persisted definition as a raw dict, ordered by file name.

        Raw (not yet validated) so the caller's provider port applies the single
        validation pass through :class:`SubagentDefinition`, keeping malformed-
        input handling in one place.
        """

        root = self._layout.root / _Dirs.SUBAGENT_DEFS
        if not root.is_dir():
            return ()
        definitions: list[dict] = []
        for path in sorted(root.iterdir()):
            if path.suffix != self._SUFFIX:
                continue
            raw = JsonlIo.read_json(path)
            if raw is not None:
                definitions.append(raw)
        return tuple(definitions)


class FileSubagentDefinitionProvider:
    """`SubagentDefinitionProvider` backed by ``subagent_defs/*.json`` on disk.

    Plugs into the standard
    :class:`~agent_runtime.delegation.subagents.definitions.DynamicSubagentCatalog`
    so file-persisted subagents pass the same permission-visibility and
    duplicate-name checks as any other provider.
    """

    def __init__(self, layout: FileStoreLayout) -> None:
        self._store = FileSubagentDefinitionStore(layout)

    def list_subagent_definitions(self) -> Sequence[Mapping[str, object]]:
        """Return raw definition dicts for the catalog to validate."""

        return self._store.read_raw_definitions()


class FileAgentStateWiring:
    """Gate + builders for file-persisted memory, skills, and subagent defs.

    The single seam the worker's dependency factory consults. Every method is a
    ``None``-safe / passthrough no-op when the file store is inactive, so the
    web / postgres / in-memory dependency graphs are byte-identical.
    """

    def __init__(self, layout: FileStoreLayout | None = None) -> None:
        self._layout = (
            layout if layout is not None else FileAgentStoreGate.active_layout()
        )

    @property
    def active(self) -> bool:
        """Whether the file store is active for this worker process."""

        return self._layout is not None

    def memory_backend_builder(self) -> FileMemoryBackendFactory | None:
        """Return the memory ``backend_builder``, or ``None`` off the file store."""

        if self._layout is None:
            return None
        return FileMemoryBackendFactory(self._layout)

    def skills_root(self) -> str | None:
        """Return the file-store ``skills/`` root path, or ``None`` off the store."""

        if self._layout is None:
            return None
        return str(FileSkillsStore(self._layout).root)

    def subagent_definition_provider(self) -> FileSubagentDefinitionProvider | None:
        """Return the file-backed subagent-def provider, or ``None`` off the store."""

        if self._layout is None:
            return None
        return FileSubagentDefinitionProvider(self._layout)


__all__ = (
    "FileAgentStateWiring",
    "FileAgentStoreGate",
    "FileMemoryBackend",
    "FileMemoryBackendFactory",
    "FileMemoryStore",
    "FileSkillsStore",
    "FileSubagentDefinitionProvider",
    "FileSubagentDefinitionStore",
    "MemoryDocument",
)
