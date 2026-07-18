"""Export / backup / import one conversation as a self-contained archive.

The file store keeps each conversation as an inspectable directory
(``conversation.json`` + ``events.jsonl`` + ``messages.jsonl`` + ``runs.jsonl``
+ ``subagents/<task>.jsonl``), content-addressed blobs under
``objects/sha256/``, and a *disposable* SQLite catalog. This module turns one
such conversation into a single portable archive and reads one back — the
"copy the conversation directory plus referenced objects without translating an
opaque database" capability from AC2 (PRD §698 *Export*).

What the archive contains (and only this):

* the conversation's canonical session files, byte-for-byte;
* every ``objects/sha256/`` blob **actually referenced** by those files —
  reachability is walked from the JSONL, never the whole global object store;
* a manifest: format version, source ids, record counts, and the SHA-256 of
  every part (the object bytes and each session file).

What it deliberately excludes (PRD §698): the SQLite catalog (rebuildable),
in-process notification/queue state, back-office ``state/`` ledgers, secrets /
broker tokens, and any object bytes outside the referenced set.

Integrity is fail-closed. Import validates the manifest, verifies each part's
SHA-256 against the manifest **before** materialising anything, and re-checks
every blob against its own content address. A single flipped byte aborts the
import — nothing is written. A verified import lands under a **fresh**
conversation id (with fresh run / message ids) so it can never clobber an
existing conversation, re-registers the referenced blobs, and refreshes the
disposable catalog so listing / search pick the conversation up.

Gated to the file store: the two entry points are methods on
:class:`~runtime_adapters.file.runtime_api_store.FileRuntimeApiStore`
(:meth:`export_conversation` / :meth:`import_conversation`) which construct a
:class:`ConversationArchiver` bound to that store.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from runtime_adapters.file._jsonl import JsonlIo
from runtime_api.schemas import (
    ConversationRecord,
    MessageRecord,
    RunRecord,
    RuntimeEventEnvelope,
)

if TYPE_CHECKING:
    from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore


# ``<version>`` bumps only on an on-archive-format-breaking change.
FORMAT_VERSION = "0xcopilot.file-conversation-export/1"

_MANIFEST_NAME = "manifest.json"
_CONVERSATION_PREFIX = "conversation"
_OBJECTS_PREFIX = "objects"
# A fixed member mtime keeps the archive reproducible for identical inputs.
_FIXED_MTIME = 0


class ConversationExportError(RuntimeError):
    """Base class for export / import failures with a safe public message."""


class ConversationNotFoundError(ConversationExportError):
    """The requested conversation does not exist for the given scope."""


class ArchiveIntegrityError(ConversationExportError):
    """The archive is malformed, tampered, or fails a SHA-256 / scope check.

    Raised *before* any bytes are materialised so a rejected import leaves the
    store completely untouched.
    """


class ExportCounts(BaseModel):
    """Record counts carried in the manifest (informational + a sanity check)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: int = Field(ge=0, default=0)
    runs: int = Field(ge=0, default=0)
    events: int = Field(ge=0, default=0)
    subagents: int = Field(ge=0, default=0)
    objects: int = Field(ge=0, default=0)


class ExportManifest(BaseModel):
    """Self-describing header written as ``manifest.json`` in the archive.

    ``parts`` maps each archive member path to the SHA-256 hex of its bytes; it
    is the root of trust for the fail-closed import check. The manifest itself
    is not self-hashed — it is the trust anchor every other part is verified
    against.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: str
    conversation_id: str
    org_id: str
    user_id: str
    exported_at: datetime
    counts: ExportCounts
    parts: dict[str, str]


class ImportOutcome(BaseModel):
    """Result of a successful import — the freshly assigned conversation id."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: str
    source_conversation_id: str
    counts: ExportCounts


class ConversationArchiver:
    """Export / import one conversation for a bound file store.

    Reuses the store's existing primitives (layout, object store, reachability
    scanner, per-conversation lock, session replay, index rebuild) rather than
    inventing a second storage path.
    """

    def __init__(self, store: FileRuntimeApiStore) -> None:
        self._store = store
        self._layout = store.layout

    # ==================================================================
    # Export
    # ==================================================================

    async def export(
        self,
        *,
        org_id: str,
        user_id: str,
        conversation_id: str,
        destination: Path,
    ) -> ExportManifest:
        """Write a ``.tar.gz`` archive for one conversation; return its manifest.

        Takes the per-conversation lock briefly to snapshot the canonical files,
        walks object reachability from those bytes, and emits a single portable
        archive. The blob set is exactly the objects referenced by this
        conversation and still present on disk — never the global store.
        """

        conversation = await self._store.get_conversation(
            org_id=org_id, user_id=user_id, conversation_id=conversation_id
        )
        if conversation is None:
            raise ConversationNotFoundError(
                "conversation not found for the requested scope"
            )

        async with self._store._conversation_lock(conversation_id):
            parts, counts = self._collect_parts(conversation)

        manifest = ExportManifest(
            format_version=FORMAT_VERSION,
            conversation_id=conversation_id,
            org_id=org_id,
            user_id=user_id,
            exported_at=datetime.now(timezone.utc),
            counts=counts,
            parts={name: hashlib.sha256(data).hexdigest() for name, data in parts},
        )
        self._write_archive(destination, manifest=manifest, parts=parts)
        return manifest

    def _collect_parts(
        self, conversation: ConversationRecord
    ) -> tuple[list[tuple[str, bytes]], ExportCounts]:
        """Snapshot canonical files + referenced blobs as ``(name, bytes)``."""

        conv_dir = self._layout.conversation_dir(
            conversation.org_id, conversation.conversation_id
        )
        parts: list[tuple[str, bytes]] = []

        # 1) Canonical session files (verbatim bytes — byte-faithful export).
        meta_bytes = self._read_bytes(conv_dir / self._layout.CONVERSATION_META)
        if meta_bytes is None:
            raise ConversationNotFoundError("conversation session directory is missing")
        parts.append(
            (f"{_CONVERSATION_PREFIX}/{self._layout.CONVERSATION_META}", meta_bytes)
        )
        for filename in (
            self._layout.MESSAGES_FILE,
            self._layout.RUNS_FILE,
            self._layout.EVENTS_FILE,
        ):
            data = self._read_bytes(conv_dir / filename)
            if data is not None:
                parts.append((f"{_CONVERSATION_PREFIX}/{filename}", data))

        subagent_count = 0
        subagents_dir = conv_dir / self._layout.SUBAGENTS_DIR
        if subagents_dir.is_dir():
            for sub_file in sorted(subagents_dir.iterdir()):
                if sub_file.suffix != ".jsonl":
                    continue
                data = self._read_bytes(sub_file)
                if data is None:
                    continue
                parts.append(
                    (
                        f"{_CONVERSATION_PREFIX}/{self._layout.SUBAGENTS_DIR}/"
                        f"{sub_file.name}",
                        data,
                    )
                )
                subagent_count += 1

        # 2) Referenced blobs only — walk refs, never dump the global store.
        digests = self._store._reachability.scan(conversation)
        object_count = 0
        for digest in sorted(digests):
            if not self._store.object_store.exists(digest):
                continue
            parts.append(
                (f"{_OBJECTS_PREFIX}/{digest}", self._store.object_store.get(digest))
            )
            object_count += 1

        counts = ExportCounts(
            messages=self._count_lines_for(conv_dir / self._layout.MESSAGES_FILE),
            runs=self._count_lines_for(conv_dir / self._layout.RUNS_FILE),
            events=self._count_lines_for(conv_dir / self._layout.EVENTS_FILE),
            subagents=subagent_count,
            objects=object_count,
        )
        return parts, counts

    @staticmethod
    def _write_archive(
        destination: Path,
        *,
        manifest: ExportManifest,
        parts: list[tuple[str, bytes]],
    ) -> None:
        """Emit the ``manifest.json`` + every part into one gzip tar file."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8")
        with tarfile.open(destination, "w:gz") as tar:
            ConversationArchiver._add_member(tar, _MANIFEST_NAME, manifest_bytes)
            for name, data in parts:
                ConversationArchiver._add_member(tar, name, data)

    @staticmethod
    def _add_member(tar: tarfile.TarFile, name: str, data: bytes) -> None:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mtime = _FIXED_MTIME
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(data))

    # ==================================================================
    # Import
    # ==================================================================

    async def import_(
        self, *, org_id: str, user_id: str, source: Path
    ) -> ImportOutcome:
        """Verify + materialise an archive under a fresh conversation id.

        Fail-closed: the manifest and every part are validated (SHA-256 +
        content-address + scope) before a single byte is written. On success the
        conversation is re-keyed (fresh conversation / run / message ids), so it
        can never clobber an existing one, the referenced blobs are
        re-registered, and the disposable catalog is rebuilt.
        """

        manifest, members = self._read_and_verify(
            source, org_id=org_id, user_id=user_id
        )
        new_conversation_id = await self._materialize(
            manifest=manifest, members=members
        )
        return ImportOutcome(
            conversation_id=new_conversation_id,
            source_conversation_id=manifest.conversation_id,
            counts=manifest.counts,
        )

    def _read_and_verify(
        self, source: Path, *, org_id: str, user_id: str
    ) -> tuple[ExportManifest, dict[str, bytes]]:
        """Load every member into memory and reject any integrity/scope failure."""

        if not source.exists():
            raise ArchiveIntegrityError("archive file does not exist")
        try:
            with tarfile.open(source, "r:gz") as tar:
                members = {
                    member.name: self._extract(tar, member)
                    for member in tar.getmembers()
                    if member.isfile()
                }
        except (tarfile.TarError, OSError) as exc:
            raise ArchiveIntegrityError("archive is not a readable tar.gz") from exc

        manifest = self._parse_manifest(members)
        if manifest.format_version != FORMAT_VERSION:
            raise ArchiveIntegrityError(
                "archive format version is not supported by this store"
            )
        if manifest.org_id != org_id or manifest.user_id != user_id:
            raise ArchiveIntegrityError("archive belongs to a different scope")

        # Every declared part must be present and hash exactly as recorded.
        for name, expected_hash in manifest.parts.items():
            data = members.get(name)
            if data is None:
                raise ArchiveIntegrityError(f"archive is missing part: {name}")
            if hashlib.sha256(data).hexdigest() != expected_hash:
                raise ArchiveIntegrityError(
                    f"archive part failed integrity check: {name}"
                )
            # Blobs are content-addressed: the name is the digest of the bytes.
            if name.startswith(f"{_OBJECTS_PREFIX}/"):
                digest = name[len(_OBJECTS_PREFIX) + 1 :]
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ArchiveIntegrityError("archived blob failed content address")

        meta_name = f"{_CONVERSATION_PREFIX}/{self._layout.CONVERSATION_META}"
        if meta_name not in manifest.parts:
            raise ArchiveIntegrityError("archive has no conversation metadata")
        return manifest, members

    @staticmethod
    def _parse_manifest(members: dict[str, bytes]) -> ExportManifest:
        raw = members.get(_MANIFEST_NAME)
        if raw is None:
            raise ArchiveIntegrityError("archive has no manifest")
        try:
            return ExportManifest.model_validate_json(raw)
        except ValueError as exc:
            raise ArchiveIntegrityError("archive manifest is malformed") from exc

    @staticmethod
    def _extract(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
        handle = tar.extractfile(member)
        if handle is None:
            raise ArchiveIntegrityError("archive member is not a regular file")
        return handle.read()

    async def _materialize(
        self, *, manifest: ExportManifest, members: dict[str, bytes]
    ) -> str:
        """Re-key + write the verified conversation into the file store."""

        org_id = manifest.org_id
        conv_dir_key = manifest.conversation_id

        meta_doc = self._load_json_member(
            members, f"{_CONVERSATION_PREFIX}/{self._layout.CONVERSATION_META}"
        )
        message_docs = self._load_jsonl_member(
            members, f"{_CONVERSATION_PREFIX}/{self._layout.MESSAGES_FILE}"
        )
        run_docs = self._load_jsonl_member(
            members, f"{_CONVERSATION_PREFIX}/{self._layout.RUNS_FILE}"
        )
        event_docs = self._load_jsonl_member(
            members, f"{_CONVERSATION_PREFIX}/{self._layout.EVENTS_FILE}"
        )
        subagent_files = {
            name: self._decode_jsonl(members[name])
            for name in members
            if name.startswith(f"{_CONVERSATION_PREFIX}/{self._layout.SUBAGENTS_DIR}/")
        }

        mapping = self._build_id_remap(
            source_conversation_id=conv_dir_key,
            message_docs=message_docs,
            run_docs=run_docs,
        )
        new_conversation_id = mapping[conv_dir_key]

        # Re-key every record by whole-value substitution (safe: ids are 32-hex
        # uuids, blob refs are 64-hex, so object addresses are never rewritten).
        meta_doc = self._strip_idempotency(_remap_json(meta_doc, mapping))
        run_docs = [self._strip_idempotency(_remap_json(d, mapping)) for d in run_docs]
        message_docs = [_remap_json(d, mapping) for d in message_docs]
        event_docs = [_remap_json(d, mapping) for d in event_docs]

        # Validate through the domain models before touching disk (fail closed).
        ConversationRecord.model_validate(meta_doc)
        for doc in message_docs:
            MessageRecord.model_validate(doc)
        for doc in run_docs:
            RunRecord.model_validate(doc)
        for doc in event_docs:
            RuntimeEventEnvelope.model_validate(doc)

        new_dir = self._layout.conversation_dir(org_id, new_conversation_id)
        # A fresh uuid conversation id cannot collide, but guard defensively.
        if new_dir.exists():
            raise ArchiveIntegrityError("generated conversation id already exists")

        async with self._store._conversation_lock(new_conversation_id):
            JsonlIo.rewrite_json(
                self._layout.conversation_meta_path(org_id, new_conversation_id),
                meta_doc,
            )
            if message_docs:
                JsonlIo.rewrite_lines(
                    self._layout.messages_path(org_id, new_conversation_id),
                    message_docs,
                )
            if run_docs:
                JsonlIo.rewrite_lines(
                    self._layout.runs_path(org_id, new_conversation_id), run_docs
                )
            if event_docs:
                JsonlIo.rewrite_lines(
                    self._layout.events_path(org_id, new_conversation_id), event_docs
                )
            for name, docs in subagent_files.items():
                task_key = name.rsplit("/", 1)[-1]
                remapped = [_remap_json(d, mapping) for d in docs]
                for doc in remapped:
                    RuntimeEventEnvelope.model_validate(doc)
                JsonlIo.rewrite_lines(
                    self._layout.subagents_dir(org_id, new_conversation_id) / task_key,
                    remapped,
                )

            # Re-register referenced blobs (idempotent, content-addressed).
            for name, data in members.items():
                if name.startswith(f"{_OBJECTS_PREFIX}/"):
                    self._store.object_store.put(data)

            # Load the new conversation into the materialised view + rebuild the
            # disposable catalog so listing / search pick it up.
            self._store._load_one_conversation(new_dir)
            self._store._rebuild_index()

        return new_conversation_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_id_remap(
        *,
        source_conversation_id: str,
        message_docs: list[dict[str, Any]],
        run_docs: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Fresh ids for the conversation and every run / message it contains."""

        mapping: dict[str, str] = {source_conversation_id: uuid4().hex}
        for doc in run_docs:
            run_id = doc.get("run_id")
            if isinstance(run_id, str) and run_id not in mapping:
                mapping[run_id] = uuid4().hex
        for doc in message_docs:
            message_id = doc.get("message_id")
            if isinstance(message_id, str) and message_id not in mapping:
                mapping[message_id] = uuid4().hex
        return mapping

    @staticmethod
    def _strip_idempotency(doc: dict[str, Any]) -> dict[str, Any]:
        """Clear ``idempotency_key`` so a restored copy never dedupes live work."""

        if "idempotency_key" in doc:
            doc = {**doc, "idempotency_key": None}
        return doc

    def _load_json_member(self, members: dict[str, bytes], name: str) -> dict[str, Any]:
        raw = members.get(name)
        if raw is None:
            raise ArchiveIntegrityError(f"archive is missing part: {name}")
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArchiveIntegrityError(
                f"archive part is not valid JSON: {name}"
            ) from exc
        if not isinstance(doc, dict):
            raise ArchiveIntegrityError(f"archive part is not a JSON object: {name}")
        return doc

    def _load_jsonl_member(
        self, members: dict[str, bytes], name: str
    ) -> list[dict[str, Any]]:
        raw = members.get(name)
        if raw is None:
            return []
        return self._decode_jsonl(raw)

    @staticmethod
    def _decode_jsonl(raw: bytes) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        for line in raw.decode("utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                doc = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ArchiveIntegrityError("archive JSONL line is malformed") from exc
            if not isinstance(doc, dict):
                raise ArchiveIntegrityError("archive JSONL line is not an object")
            docs.append(doc)
        return docs

    @staticmethod
    def _read_bytes(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None

    @classmethod
    def _count_lines_for(cls, path: Path) -> int:
        data = cls._read_bytes(path)
        if data is None:
            return 0
        return sum(1 for line in data.decode("utf-8").splitlines() if line.strip())


def _remap_json(value: Any, mapping: dict[str, str]) -> Any:
    """Deep-replace whole string values found in ``mapping`` (structure-safe).

    Only exact string values are rewritten, so an id embedded at any depth (e.g.
    ``runtime_context.run_id``, an event's ``payload``) is re-keyed while
    substrings, content-addressed blob refs, and unrelated fields are left
    untouched.
    """

    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_remap_json(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _remap_json(item, mapping) for key, item in value.items()}
    return value


__all__ = (
    "FORMAT_VERSION",
    "ArchiveIntegrityError",
    "ConversationArchiver",
    "ConversationExportError",
    "ConversationNotFoundError",
    "ExportCounts",
    "ExportManifest",
    "ImportOutcome",
)
