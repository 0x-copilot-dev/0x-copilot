"""Private host-write-free mutation engine for the durable workspace overlay.

This module deliberately exposes no public API.  ``WorkspaceOperationAdapter``
in :mod:`agent_runtime.capabilities.workspace.effects` is the sole production
constructor and caller, retained behind the worker-owned operation route.
Model-visible backends receive only ``WorkspaceOperationPort``, so a model
mutation has to cross the universal operation/effect gateway before an overlay
revision can be appended.

The engine keeps its own read composition for edit preconditions.  That is an
internal, non-model recovery/read path only; it has no host-write dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.workspace.contracts import (
    BaseExistence,
    BasePrecondition,
    OverlayEntry,
    OverlayManifest,
    OverlayMutation,
    OverlayMutationKind,
    WorkspaceBaseEntry,
    WorkspaceEntryKind,
    WorkspaceMutationResult,
    WorkspaceOperation,
    content_ref_for_blob,
    mount_id_for_path,
    normalize_virtual_path,
    sha256_digest,
)
from agent_runtime.capabilities.workspace.errors import (
    WorkspaceEditError,
    WorkspaceIsDirectoryError,
    WorkspaceLimitError,
    WorkspaceNotFoundError,
    WorkspaceOverlayConflictError,
    WorkspaceOverlayError,
)
from agent_runtime.capabilities.workspace.ports import (
    WorkspaceBaseReadPort,
    WorkspaceOverlayReadPort,
    WorkspaceOverlayStorePort,
)


@dataclass(frozen=True)
class _WorkspaceOverlayPlan:
    """A candidate manifest that cannot become model-visible by itself.

    Planning may persist content-addressed bytes, but its manifest lives only in
    the request-local store below.  The real overlay is updated exactly once by
    ``_project`` after proposal material and the effect stage are durable.
    """

    baseline: OverlayManifest
    candidate: OverlayManifest
    primary_path: str | None


class _PlanningOverlayStore:
    """Request-local, non-durable manifest store used to compile a proposal."""

    def __init__(self, manifest: OverlayManifest) -> None:
        self._manifest = manifest

    async def get_manifest(self, *, run_id: str) -> OverlayManifest:
        if run_id != self._manifest.run_id:
            raise WorkspaceOverlayConflictError()
        return self._manifest

    async def get_manifest_version(
        self, *, run_id: str, version: int
    ) -> OverlayManifest | None:
        if run_id != self._manifest.run_id:
            return None
        return self._manifest if version == self._manifest.version else None

    async def append_revision(
        self,
        *,
        run_id: str,
        expected_version: int,
        mutations: tuple[OverlayMutation, ...] | list[OverlayMutation],
    ) -> OverlayManifest:
        current = await self.get_manifest(run_id=run_id)
        if current.version != expected_version:
            raise WorkspaceOverlayConflictError()
        next_version = current.version + 1
        entries = {entry.virtual_path: entry for entry in current.entries}
        for mutation in mutations:
            if mutation.kind is OverlayMutationKind.REMOVE:
                entries.pop(mutation.virtual_path, None)
            elif mutation.entry is not None:
                entries[mutation.virtual_path] = mutation.entry.model_copy(
                    update={"overlay_revision": next_version}
                )
        self._manifest = OverlayManifest(
            run_id=run_id,
            version=next_version,
            entries=tuple(entries[path] for path in sorted(entries)),
        )
        return self._manifest

    async def compact(self, *, run_id: str) -> OverlayManifest:
        return await self.get_manifest(run_id=run_id)


class _WorkspaceOverlayMutationEngine:
    """Internal primitive used only after the universal gateway has admitted work.

    A2 owns the immutable bytes through ``ArtifactBlobStorePort``.  This service
    records only its digest and an opaque reference, then appends one metadata
    revision through the overlay store.  A4 stage binding is intentionally left
    for the next integration slice; ``stage_id`` fields remain available on the
    durable entry contract so that binding does not reshape persisted overlays.
    """

    MAX_ENTRIES = 1_000
    MAX_TOTAL_RESULT_BYTES = 250 * 1024 * 1024
    MAX_FILE_BYTES = 100 * 1024 * 1024

    def __init__(
        self,
        *,
        run_id: str,
        base_read: WorkspaceBaseReadPort,
        overlay_store: WorkspaceOverlayStorePort,
        blob_store: ArtifactBlobStorePort,
    ) -> None:
        self._run_id = run_id
        self._base_read = base_read
        self._overlay_store = overlay_store
        self._blob_store = blob_store

    async def _manifest(self) -> OverlayManifest:
        """Return the current immutable run overlay manifest."""

        return await self._overlay_store.get_manifest(run_id=self._run_id)

    async def _plan(
        self,
        *,
        op: str,
        arguments: dict[str, object],
        author: str = "agent",
    ) -> _WorkspaceOverlayPlan:
        """Compile one overlay candidate without touching the durable manifest.

        The existing mutation primitives are deliberately reused against a
        request-local manifest adapter.  This keeps all path, size, edit, and
        baseline rules identical to direct overlay-domain tests while making
        proposal compilation side-effect-free for model-visible reads.
        """

        baseline = await self._manifest()
        planner = _WorkspaceOverlayMutationEngine(
            run_id=self._run_id,
            base_read=self._base_read,
            overlay_store=_PlanningOverlayStore(baseline),
            blob_store=self._blob_store,
        )
        if op in {"create", "replace", "write"}:
            path = self._required_text(arguments, "virtual_path")
            content = self._required_text(arguments, "content", allow_empty=True)
            result = (
                await planner._propose_create(path, content, author=author)
                if op == "create"
                else await planner._propose_replace(path, content, author=author)
            )
            primary_path = path
        elif op == "edit":
            primary_path = self._required_text(arguments, "virtual_path")
            result = await planner._propose_edit(
                primary_path,
                self._required_text(arguments, "old_string"),
                self._required_text(arguments, "new_string", allow_empty=True),
                replace_all=bool(arguments.get("replace_all", False)),
                author=author,
            )
        elif op == "delete":
            primary_path = self._required_text(arguments, "virtual_path")
            result = await planner._propose_delete(primary_path, author=author)
        elif op == "move":
            source = self._required_text(arguments, "source_virtual_path")
            primary_path = self._required_text(arguments, "destination_virtual_path")
            result = await planner._propose_move(source, primary_path, author=author)
        elif op == "mkdir":
            primary_path = self._required_text(arguments, "virtual_path")
            result = await planner._propose_mkdir(primary_path, author=author)
        else:
            raise RuntimeError("workspace operation is not stageable")
        return _WorkspaceOverlayPlan(
            baseline=baseline,
            candidate=result.manifest,
            primary_path=primary_path,
        )

    async def _project(
        self,
        plan: _WorkspaceOverlayPlan,
        *,
        stage_id: str | None = None,
        stage_revision: int | None = None,
    ) -> WorkspaceMutationResult:
        """Publish a durable candidate only after its stage has been recorded.

        ``expected_version`` pins projection to the exact read used for
        planning.  A competing overlay update therefore fails closed instead
        of exposing a proposal whose stage describes older workspace content.
        """

        if (stage_id is None) is not (stage_revision is None):
            raise ValueError(
                "workspace stage projection requires id and revision together"
            )
        before = {entry.virtual_path: entry for entry in plan.baseline.entries}
        after = {entry.virtual_path: entry for entry in plan.candidate.entries}
        mutations: list[OverlayMutation] = []
        for path in sorted(set(before) | set(after)):
            candidate = after.get(path)
            if candidate is None:
                mutations.append(
                    OverlayMutation(kind=OverlayMutationKind.REMOVE, virtual_path=path)
                )
                continue
            if stage_id is not None and stage_revision is not None:
                candidate = candidate.model_copy(
                    update={"stage_id": stage_id, "stage_revision": stage_revision}
                )
            if before.get(path) != candidate:
                mutations.append(
                    OverlayMutation(
                        kind=OverlayMutationKind.UPSERT,
                        virtual_path=path,
                        entry=candidate,
                    )
                )
        if not mutations:
            return WorkspaceMutationResult(
                entry=(
                    plan.baseline.entry_at(plan.primary_path)
                    if plan.primary_path is not None
                    else None
                ),
                manifest=plan.baseline,
            )
        updated = await self._overlay_store.append_revision(
            run_id=self._run_id,
            expected_version=plan.baseline.version,
            mutations=tuple(mutations),
        )
        return WorkspaceMutationResult(
            entry=(
                updated.entry_at(plan.primary_path)
                if plan.primary_path is not None
                else None
            ),
            manifest=updated,
        )

    async def _propose_create(
        self, virtual_path: str, content: bytes | str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Create a new overlay file, failing if the merged target already exists."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if await self._merged_entry_exists(path, manifest):
            raise WorkspaceOverlayError("Workspace path already exists.")
        return await self._write(
            path=path,
            content=self._as_bytes(content),
            author=author,
            manifest=manifest,
            operation=WorkspaceOperation.CREATE,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
        )

    async def _propose_replace(
        self, virtual_path: str, content: bytes | str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Create or replace a file while preserving its original base precondition."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        existing = manifest.entry_at(path)
        if existing is not None and existing.entry_kind is WorkspaceEntryKind.DIRECTORY:
            raise WorkspaceIsDirectoryError()
        if existing is not None and existing.entry_kind is WorkspaceEntryKind.TOMBSTONE:
            # Recreating an existing base file retains its original compare-and-swap
            # guard.  Recreating an overlay-only file stays a create.
            operation = (
                WorkspaceOperation.CREATE
                if existing.baseline.existence is BaseExistence.MUST_NOT_EXIST
                else WorkspaceOperation.REPLACE
            )
            baseline = existing.baseline
        elif existing is not None:
            operation = existing.operation
            baseline = existing.baseline
        else:
            base = await self._base_read.stat(path)
            if base is None:
                operation = WorkspaceOperation.CREATE
                baseline = BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST)
            else:
                if base.entry_kind is WorkspaceEntryKind.DIRECTORY:
                    raise WorkspaceIsDirectoryError()
                operation = WorkspaceOperation.REPLACE
                baseline = await self._precondition_for_base(path, base)
        return await self._write(
            path=path,
            content=self._as_bytes(content),
            author=author,
            manifest=manifest,
            operation=operation,
            baseline=baseline,
        )

    async def _propose_delete(
        self, virtual_path: str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Place one explicit tombstone in the overlay; never infer descendants."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        existing = manifest.entry_at(path)
        if existing is not None and existing.entry_kind is WorkspaceEntryKind.TOMBSTONE:
            raise WorkspaceNotFoundError()
        if (
            existing is not None
            and existing.baseline.existence is BaseExistence.MUST_NOT_EXIST
        ):
            # create → delete before a host apply has no durable target left.
            updated = await self._overlay_store.append_revision(
                run_id=self._run_id,
                expected_version=manifest.version,
                mutations=(
                    OverlayMutation(
                        kind=OverlayMutationKind.REMOVE,
                        virtual_path=path,
                    ),
                ),
            )
            return WorkspaceMutationResult(manifest=updated)

        if existing is not None:
            baseline = existing.baseline
        else:
            base = await self._base_read.stat(path)
            if base is None:
                raise WorkspaceNotFoundError()
            baseline = await self._precondition_for_base(path, base)
        entry = OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.TOMBSTONE,
            operation=WorkspaceOperation.DELETE,
            baseline=baseline,
            stage_id=existing.stage_id if existing is not None else None,
            stage_revision=existing.stage_revision if existing is not None else None,
            author=author,
        )
        return await self._append(
            manifest,
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT, virtual_path=path, entry=entry
            ),
        )

    async def _propose_move(
        self,
        source_virtual_path: str,
        destination_virtual_path: str,
        *,
        author: str = "agent",
    ) -> WorkspaceMutationResult:
        """Record an explicit same-mount move without altering either base path."""

        source = normalize_virtual_path(source_virtual_path)
        destination = normalize_virtual_path(destination_virtual_path)
        if source == destination or mount_id_for_path(source) != mount_id_for_path(
            destination
        ):
            raise WorkspaceOverlayError("Workspace move must stay within one mount.")
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if not await self._merged_entry_exists(source, manifest):
            raise WorkspaceNotFoundError()
        if await self._merged_entry_exists(destination, manifest):
            raise WorkspaceOverlayError("Workspace move destination already exists.")

        source_entry = manifest.entry_at(source)
        if source_entry is not None:
            baseline = source_entry.baseline
        else:
            base = await self._base_read.stat(source)
            if base is None:  # race-safe guard after the merged existence check
                raise WorkspaceNotFoundError()
            baseline = await self._precondition_for_base(source, base)

        tombstone = OverlayEntry(
            virtual_path=source,
            entry_kind=WorkspaceEntryKind.TOMBSTONE,
            operation=WorkspaceOperation.MOVE,
            baseline=baseline,
            stage_id=source_entry.stage_id if source_entry is not None else None,
            stage_revision=(
                source_entry.stage_revision if source_entry is not None else None
            ),
            author=author,
        )
        content = (
            {
                "content_ref": source_entry.content_ref,
                "content_digest": source_entry.content_digest,
                "byte_size": source_entry.byte_size,
            }
            if source_entry is not None
            and source_entry.entry_kind is WorkspaceEntryKind.FILE
            else {}
        )
        moved = OverlayEntry(
            virtual_path=destination,
            entry_kind=WorkspaceEntryKind.MOVE,
            operation=WorkspaceOperation.MOVE,
            source_virtual_path=source,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            stage_id=source_entry.stage_id if source_entry is not None else None,
            stage_revision=(
                source_entry.stage_revision if source_entry is not None else None
            ),
            author=author,
            **content,
        )
        updated = await self._overlay_store.append_revision(
            run_id=self._run_id,
            expected_version=manifest.version,
            mutations=(
                OverlayMutation(
                    kind=OverlayMutationKind.UPSERT,
                    virtual_path=source,
                    entry=tombstone,
                ),
                OverlayMutation(
                    kind=OverlayMutationKind.UPSERT,
                    virtual_path=destination,
                    entry=moved,
                ),
            ),
        )
        return WorkspaceMutationResult(
            entry=updated.entry_at(destination), manifest=updated
        )

    async def _propose_mkdir(
        self, virtual_path: str, *, author: str = "agent"
    ) -> WorkspaceMutationResult:
        """Create exactly one virtual directory; parent recursion is explicit."""

        path = normalize_virtual_path(virtual_path)
        manifest = await self._overlay_store.get_manifest(run_id=self._run_id)
        if await self._merged_entry_exists(path, manifest):
            raise WorkspaceOverlayError("Workspace path already exists.")
        entry = OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.DIRECTORY,
            operation=WorkspaceOperation.MKDIR,
            baseline=BasePrecondition(existence=BaseExistence.MUST_NOT_EXIST),
            author=author,
        )
        return await self._append(
            manifest,
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT, virtual_path=path, entry=entry
            ),
        )

    async def _propose_edit(
        self,
        virtual_path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
        author: str = "agent",
    ) -> WorkspaceMutationResult:
        """Apply a strict textual replacement against the merged virtual bytes."""

        from agent_runtime.capabilities.workspace.merged_backend import (
            MergedWorkspaceBackend,
        )

        path = normalize_virtual_path(virtual_path)
        if not old_string:
            raise WorkspaceEditError("Workspace edit requires a non-empty old string.")
        backend = MergedWorkspaceBackend(
            run_id=self._run_id,
            base_read=self._base_read,
            overlay_store=WorkspaceOverlayReadPort.bind(self._overlay_store),
            blob_store=self._blob_store,
        )
        try:
            current = (await backend.aread_bytes(path)).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceEditError(
                "Workspace edit requires a UTF-8 text file."
            ) from exc
        occurrences = current.count(old_string)
        if occurrences == 0:
            raise WorkspaceEditError("Workspace edit string was not found.")
        if occurrences > 1 and not replace_all:
            raise WorkspaceEditError("Workspace edit string is ambiguous.")
        updated = current.replace(old_string, new_string, -1 if replace_all else 1)
        return await self._propose_replace(path, updated, author=author)

    async def _write(
        self,
        *,
        path: str,
        content: bytes,
        author: str,
        manifest: OverlayManifest,
        operation: WorkspaceOperation,
        baseline: BasePrecondition,
    ) -> WorkspaceMutationResult:
        if len(content) > self.MAX_FILE_BYTES:
            raise WorkspaceLimitError("Workspace file exceeds the overlay file limit.")
        await self._check_limits(manifest=manifest, path=path, byte_size=len(content))
        written = await self._blob_store.put_stream(
            expected_digest=sha256_digest(content),
            chunks=self._single_chunk(content),
            byte_limit=self.MAX_FILE_BYTES,
        )
        current = manifest.entry_at(path)
        entry = OverlayEntry(
            virtual_path=path,
            entry_kind=WorkspaceEntryKind.FILE,
            operation=operation,
            content_ref=content_ref_for_blob(written.blob_key),
            content_digest=written.content_digest,
            byte_size=written.byte_size,
            baseline=baseline,
            stage_id=current.stage_id if current is not None else None,
            stage_revision=(current.stage_revision if current is not None else None),
            author=author,
        )
        return await self._append(
            manifest,
            OverlayMutation(
                kind=OverlayMutationKind.UPSERT, virtual_path=path, entry=entry
            ),
        )

    async def _append(
        self, manifest: OverlayManifest, mutation: OverlayMutation
    ) -> WorkspaceMutationResult:
        updated = await self._overlay_store.append_revision(
            run_id=self._run_id,
            expected_version=manifest.version,
            mutations=(mutation,),
        )
        return WorkspaceMutationResult(
            entry=updated.entry_at(mutation.virtual_path), manifest=updated
        )

    async def _precondition_for_base(
        self, path: str, entry: WorkspaceBaseEntry
    ) -> BasePrecondition:
        if entry.entry_kind is WorkspaceEntryKind.FILE:
            digest = entry.content_digest
            if digest is None:
                digest = sha256_digest(await self._read_base_bytes(path))
            return BasePrecondition(
                existence=BaseExistence.MUST_EXIST,
                entry_kind=WorkspaceEntryKind.FILE,
                opaque_generation=entry.opaque_generation,
                content_digest=digest,
                stable_file_id=entry.stable_file_id,
                byte_size=entry.byte_size,
                mtime_ns=entry.mtime_ns,
            )
        return BasePrecondition(
            existence=BaseExistence.MUST_EXIST,
            entry_kind=entry.entry_kind,
            opaque_generation=entry.opaque_generation,
            stable_file_id=entry.stable_file_id,
            byte_size=entry.byte_size,
            mtime_ns=entry.mtime_ns,
        )

    async def _merged_entry_exists(self, path: str, manifest: OverlayManifest) -> bool:
        entry = manifest.entry_at(path)
        if entry is not None:
            return entry.entry_kind is not WorkspaceEntryKind.TOMBSTONE
        return await self._base_read.stat(path) is not None

    async def _check_limits(
        self, *, manifest: OverlayManifest, path: str, byte_size: int
    ) -> None:
        current = manifest.entry_at(path)
        entry_count = len(manifest.entries) + (0 if current is not None else 1)
        if entry_count > self.MAX_ENTRIES:
            raise WorkspaceLimitError("Workspace overlay entry limit was exceeded.")
        referenced = sum(
            entry.byte_size or 0
            for entry in manifest.entries
            if entry.entry_kind is WorkspaceEntryKind.FILE
        )
        if current is not None and current.entry_kind is WorkspaceEntryKind.FILE:
            referenced -= current.byte_size or 0
        if referenced + byte_size > self.MAX_TOTAL_RESULT_BYTES:
            raise WorkspaceLimitError("Workspace overlay byte limit was exceeded.")

    async def _read_base_bytes(self, path: str) -> bytes:
        stream = await self._base_read.read(path)
        body = bytearray()
        async for chunk in stream:
            if not isinstance(chunk, bytes):
                raise TypeError("workspace base read chunks must be bytes")
            body.extend(chunk)
            if len(body) > self.MAX_FILE_BYTES:
                raise WorkspaceLimitError(
                    "Workspace base file exceeds the overlay file limit."
                )
        return bytes(body)

    @staticmethod
    async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
        yield content

    @staticmethod
    def _as_bytes(content: bytes | str) -> bytes:
        return content if isinstance(content, bytes) else content.encode("utf-8")

    @staticmethod
    def _required_text(
        arguments: dict[str, object],
        key: str,
        *,
        allow_empty: bool = False,
    ) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or (not allow_empty and not value):
            raise RuntimeError(f"workspace argument {key} is invalid")
        return value


__all__: tuple[()] = ()
