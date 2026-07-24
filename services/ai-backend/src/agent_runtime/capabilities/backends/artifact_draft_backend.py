"""Canonical Artifact Repository adapter for Deep Agents' ``/drafts/`` mount.

This is intentionally a *draft* adapter, not a generic filesystem backend.
The model can address only the existing UUID-shaped ``/drafts/<id>.md``
namespace.  Every new byte lands in an immutable Artifact revision; the
optional legacy ``DraftStorePort`` is read-only and exists solely for bounded
read-through migration.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from threading import RLock
from collections.abc import AsyncIterator, Sequence
from typing import cast
from uuid import UUID

from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from agent_runtime.artifacts import (
    ArtifactConflictError,
    ArtifactCreateRequest,
    ArtifactNotFoundError,
    ArtifactProvenance,
    ArtifactRevisionRequest,
    ArtifactService,
)
from agent_runtime.capabilities.backends.draft_backend import _run_sync
from agent_runtime.persistence.ports import DraftStorePort
from agent_runtime.persistence.records import DraftPath, DraftRecord
from agent_runtime.surfaces_v2.ledger_ids import ArtifactIdCodec
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind


_INNER_PATH_RE = re.compile(r"^/([0-9a-f]{32})\.md$")
_FULL_PATH_RE = re.compile(r"^/drafts/([0-9a-f]{32})\.md$")
_DRAFT_SOURCE_RE = re.compile(r"^draft://([0-9a-f]{32})$")


class _Errors:
    INVALID_PATH = "invalid_path"
    FILE_NOT_FOUND = "file_not_found"
    AMBIGUOUS_MATCH = (
        "Ambiguous match — old_string occurs multiple times; pass "
        "replace_all=True or use a more specific anchor."
    )
    NO_MATCH = "old_string was not found in the draft body."
    EMPTY_DRAFT = "edit_file cannot be applied to an empty draft."
    STORAGE_FAILURE = "draft_artifact_unavailable"


@dataclass(frozen=True)
class ArtifactDraftPathBinding:
    """Pure, scope-bound virtual-path → canonical-artifact binding.

    The deterministic id is an adapter mapping, not a second database table:
    the artifact revision records the reserved ``draft://`` provenance marker,
    while the digest incorporates every authority boundary.  A model supplies
    at most a validated virtual path; it never selects an artifact id.
    """

    org_id: str
    user_id: str
    conversation_id: str
    run_id: str
    draft_id: str

    DOMAIN = b"0x-copilot/artifact-draft-binding/v1\x00"

    @property
    def virtual_path(self) -> str:
        return DraftPath.for_draft_id(self.draft_id)

    @property
    def source_ref(self) -> str:
        return f"draft://{self.draft_id}"

    @property
    def artifact_id(self) -> str:
        material = "\x00".join(
            (
                self.org_id,
                self.user_id,
                self.conversation_id,
                self.run_id,
                self.virtual_path,
            )
        ).encode("utf-8")
        raw = bytearray(hashlib.sha256(self.DOMAIN + material).digest()[:16])
        # Render a canonical UUID4-shaped value for the existing A1 codec.
        raw[6] = (raw[6] & 0x0F) | 0x40
        raw[8] = (raw[8] & 0x3F) | 0x80
        return ArtifactIdCodec.format(UUID(bytes=bytes(raw)))

    @classmethod
    def parse_source_ref(cls, value: str) -> str | None:
        match = _DRAFT_SOURCE_RE.fullmatch(value)
        return match.group(1) if match is not None else None


class ArtifactDraftBackend(BackendProtocol):
    """Deep Agents' ``/drafts`` backend backed solely by Artifact revisions."""

    PATH_PREFIX = DraftPath.PREFIX
    _MAX_RETRIES = 3

    def __init__(
        self,
        *,
        artifacts: ArtifactService,
        org_id: str,
        conversation_id: str,
        run_id: str,
        user_id: str,
        legacy_store: DraftStorePort | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._user_id = user_id
        # Compatibility-only.  This class never calls insert_version.
        self._legacy_store = legacy_store
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = RLock()

    def write(self, file_path: str, content: str) -> WriteResult:
        return _run_sync(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        binding = self._binding(file_path)
        if binding is None:
            return WriteResult(error=_Errors.INVALID_PATH)
        try:
            await self._write(binding=binding, content=content)
        except Exception:
            return WriteResult(error=_Errors.STORAGE_FAILURE)
        return WriteResult(path=binding.virtual_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return _run_sync(self.aedit(file_path, old_string, new_string, replace_all))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        binding = self._binding(file_path)
        if binding is None:
            return EditResult(error=_Errors.INVALID_PATH)
        current = await self._read(binding=binding, import_legacy=True)
        if current is None:
            return EditResult(error=_Errors.FILE_NOT_FOUND)
        if not current:
            return EditResult(error=_Errors.EMPTY_DRAFT)
        occurrences = current.count(old_string)
        if occurrences == 0:
            return EditResult(error=_Errors.NO_MATCH)
        if occurrences > 1 and not replace_all:
            return EditResult(error=_Errors.AMBIGUOUS_MATCH)
        replacement = (
            current.replace(old_string, new_string)
            if replace_all
            else current.replace(old_string, new_string, 1)
        )
        try:
            await self._write(binding=binding, content=replacement)
        except Exception:
            return EditResult(error=_Errors.STORAGE_FAILURE)
        return EditResult(
            path=binding.virtual_path,
            occurrences=occurrences if replace_all else 1,
        )

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return _run_sync(self.aread(file_path, offset, limit))

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        del offset, limit  # Preserve legacy DraftBackend's full-document contract.
        binding = self._binding(file_path)
        if binding is None:
            return ReadResult(error=_Errors.INVALID_PATH)
        try:
            content = await self._read(binding=binding, import_legacy=True)
        except Exception:
            return ReadResult(error=_Errors.STORAGE_FAILURE)
        if content is None:
            return ReadResult(error=_Errors.FILE_NOT_FOUND)
        return ReadResult(file_data={"content": content, "encoding": "utf-8"})

    def ls(self, path: str) -> LsResult:
        return _run_sync(self.als(path))

    async def als(self, path: str) -> LsResult:
        if path not in ("/", "", "/."):
            return LsResult(entries=[])
        try:
            page = await self._artifacts.list_for_run(
                org_id=self._org_id,
                user_id=self._user_id,
                run_id=self._run_id,
                kind=ArtifactKind.DOCUMENT,
                limit=100,
            )
        except Exception:
            return LsResult(entries=[])
        entries: list[FileInfo] = []
        canonical_draft_ids: set[str] = set()
        for record in page.artifacts:
            draft_id = ArtifactDraftPathBinding.parse_source_ref(
                record.current_revision.revision.source_ref or ""
            )
            if draft_id is None:
                continue
            canonical_draft_ids.add(draft_id)
            entries.append(
                cast(
                    FileInfo,
                    {
                        "path": f"/{draft_id}.md",
                        "is_dir": False,
                        "size": record.current_revision.revision.byte_size,
                        "modified_at": record.artifact.updated_at,
                    },
                )
            )
        # Read-through migration must not make existing drafts disappear from a
        # directory listing.  They remain legacy-backed until the first read;
        # that read performs the one-way canonical import.  Scope all four
        # identity dimensions before exposing a path because the new mapping
        # deliberately binds paths to a particular run.
        for legacy in await self._legacy_latest_for_run():
            if legacy.draft_id in canonical_draft_ids:
                continue
            entries.append(
                cast(
                    FileInfo,
                    {
                        "path": f"/{legacy.draft_id}.md",
                        "is_dir": False,
                        "size": len(legacy.content_text.encode("utf-8")),
                        "modified_at": legacy.created_at,
                    },
                )
            )
        entries.sort(key=lambda entry: str(entry["path"]))
        return LsResult(entries=entries)

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return _run_sync(self.agrep(pattern, path, glob))

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        del path, glob
        listing = await self.als("/")
        matches: list[GrepMatch] = []
        for entry in listing.entries or []:
            content = await self.aread(str(entry["path"]))
            if content.file_data is None:
                continue
            for line_no, line in enumerate(
                str(content.file_data["content"]).splitlines(), start=1
            ):
                if pattern in line:
                    matches.append(
                        cast(
                            GrepMatch,
                            {"path": entry["path"], "line": line_no, "text": line},
                        )
                    )
        return GrepResult(matches=matches)

    def glob(self, pattern: str, path: str = "/") -> GlobResult:
        return _run_sync(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        del pattern, path
        listing = await self.als("/")
        return GlobResult(matches=listing.entries or [])

    def _binding(self, file_path: str) -> ArtifactDraftPathBinding | None:
        draft_id = None
        for candidate in (_INNER_PATH_RE, _FULL_PATH_RE):
            match = candidate.fullmatch(file_path)
            if match is not None:
                draft_id = match.group(1)
                break
        if draft_id is None:
            return None
        return ArtifactDraftPathBinding(
            org_id=self._org_id,
            user_id=self._user_id,
            conversation_id=self._conversation_id,
            run_id=self._run_id,
            draft_id=draft_id,
        )

    async def _write(self, *, binding: ArtifactDraftPathBinding, content: str) -> None:
        encoded = content.encode("utf-8")
        digest = ArtifactService.digest_bytes(encoded)
        async with self._lock_for(binding.draft_id):
            for _ in range(self._MAX_RETRIES):
                record = await self._record(binding)
                if record is None:
                    try:
                        await self._artifacts.create_draft_from_bytes(
                            org_id=self._org_id,
                            user_id=self._user_id,
                            request=ArtifactCreateRequest(
                                run_id=self._run_id,
                                kind=ArtifactKind.DOCUMENT,
                                title=_title_for(content, f"{binding.draft_id}.md"),
                                media_type="text/markdown",
                                suggested_filename=f"{binding.draft_id}.md",
                                idempotency_key=(
                                    f"draft-create:{binding.draft_id}:{digest}"
                                ),
                            ),
                            provenance=ArtifactProvenance(
                                author=ArtifactAuthor.MODEL,
                                source_ref=binding.source_ref,
                            ),
                            content=encoded,
                            artifact_id=binding.artifact_id,
                        )
                        return
                    except ArtifactConflictError:
                        continue
                else:
                    revision = record.current_revision.revision
                    if revision.content_digest == digest:
                        return
                    try:
                        await self._artifacts.append_revision_from_stream(
                            org_id=self._org_id,
                            user_id=self._user_id,
                            request=ArtifactRevisionRequest(
                                artifact_id=binding.artifact_id,
                                parent_revision=revision.revision,
                                expected_digest=digest,
                                idempotency_key=(
                                    f"draft-revise:{binding.draft_id}:"
                                    f"{revision.revision}:{digest}"
                                ),
                            ),
                            provenance=ArtifactProvenance(
                                author=ArtifactAuthor.MODEL,
                                source_ref=binding.source_ref,
                            ),
                            chunks=_single_chunk(encoded),
                        )
                        return
                    except ArtifactConflictError:
                        continue
            raise ArtifactConflictError()

    async def _read(
        self, *, binding: ArtifactDraftPathBinding, import_legacy: bool
    ) -> str | None:
        record = await self._record(binding)
        if record is None and import_legacy:
            await self._import_legacy_latest(binding)
            record = await self._record(binding)
        if record is None:
            return None
        _record, _revision, stream = await self._artifacts.stream_revision(
            org_id=self._org_id,
            user_id=self._user_id,
            artifact_id=binding.artifact_id,
            revision=record.current_revision.revision.revision,
        )
        payload = b"".join([chunk async for chunk in stream])
        return payload.decode("utf-8")

    async def _record(self, binding: ArtifactDraftPathBinding):
        try:
            record = await self._artifacts.get_metadata(
                org_id=self._org_id,
                user_id=self._user_id,
                artifact_id=binding.artifact_id,
            )
        except ArtifactNotFoundError:
            return None
        if record.artifact.conversation_id != self._conversation_id:
            return None
        if record.artifact.run_id != self._run_id:
            return None
        if record.current_revision.revision.source_ref != binding.source_ref:
            return None
        return record

    async def _import_legacy_latest(self, binding: ArtifactDraftPathBinding) -> None:
        if self._legacy_store is None:
            return
        legacy = await self._legacy_store.latest(
            org_id=self._org_id, draft_id=binding.draft_id
        )
        if legacy is None:
            return
        if (
            legacy.conversation_id != self._conversation_id
            or legacy.run_id != self._run_id
            or legacy.user_id != self._user_id
        ):
            # Legacy rows are keyed only by org + draft id.  Never turn a
            # guessed id into a cross-run/user import merely because the
            # record exists in the same tenant.
            return
        try:
            await self._artifacts.create_draft_from_bytes(
                org_id=self._org_id,
                user_id=self._user_id,
                request=ArtifactCreateRequest(
                    run_id=self._run_id,
                    kind=ArtifactKind.DOCUMENT,
                    title=legacy.title or _title_for(legacy.content_text, "Draft"),
                    media_type="text/markdown",
                    suggested_filename=f"{binding.draft_id}.md",
                    idempotency_key=(
                        "draft-import:"
                        f"{binding.draft_id}:"
                        f"{ArtifactService.digest_bytes(legacy.content_text.encode('utf-8'))}"
                    ),
                ),
                provenance=ArtifactProvenance(
                    author=ArtifactAuthor.IMPORT,
                    source_ref=binding.source_ref,
                ),
                content=legacy.content_text.encode("utf-8"),
                artifact_id=binding.artifact_id,
            )
        except ArtifactConflictError:
            # A concurrent first read/import won. The next canonical lookup is
            # authoritative and no legacy write is attempted.
            return

    async def _legacy_latest_for_run(self) -> Sequence[DraftRecord]:
        if self._legacy_store is None:
            return ()
        records = await self._legacy_store.latest_for_conversation(
            org_id=self._org_id,
            conversation_id=self._conversation_id,
        )
        return tuple(
            record
            for record in records
            if record.run_id == self._run_id and record.user_id == self._user_id
        )

    def _lock_for(self, draft_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(draft_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[draft_id] = lock
            return lock


def _title_for(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:240] or fallback
    for line in content.splitlines():
        if line.strip():
            return line.strip()[:240]
    return fallback


async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
    if content:
        yield content


__all__ = ("ArtifactDraftBackend", "ArtifactDraftPathBinding")
