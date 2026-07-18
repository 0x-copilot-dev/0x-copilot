"""Corruption diagnosis, salvage-export, and guarded repair for the file store.

The canonical file store (:mod:`runtime_adapters.file.runtime_api_store`) *fails
closed* on a corrupt JSONL stream: :func:`JsonlIo.iter_lines` raises
:class:`JsonlCorruptionError` on an interior malformed line rather than silently
truncating real history, so a conversation with mid-file corruption becomes
read-only and the whole store refuses to open. That protects data but leaves the
user stuck. This module is the offline recovery tool AC2 promises alongside the
"this chat needs repair" state — it reads the on-disk truth *without* raising and
turns it into three operator actions:

1. :meth:`StoreRepair.diagnose` — scan a store (or one conversation) and return a
   structured, exception-free :class:`StoreDiagnosis`: which JSONL streams have a
   torn trailing line vs. interior corruption, which events reference
   ``objects/sha256/`` blobs that are missing (dangling refs), and which blobs are
   present but referenced by nothing (orphans).
2. :meth:`StoreRepair.salvage_export` — copy every *recoverable* record (the good
   prefix before the first unrecoverable point, plus any independently-parseable
   post-corruption lines preserved verbatim in a clearly-labelled sidecar) and
   the objects they reference into a fresh bundle, with a report of exactly what
   was dropped and why. It never re-serializes or fabricates a record — recovered
   lines are copied byte-for-byte.
3. :meth:`StoreRepair.rebuild_catalog` / :meth:`StoreRepair.quarantine_corrupt_tail`
   — opt-in, guarded in-place repair: rebuild the disposable SQLite catalog from
   the recoverable JSONL truth, and/or *move* (never hard-delete) an unrecoverable
   tail into a ``.corrupt/`` sidecar so the conversation becomes readable again.

Nothing here deletes user data. Quarantine moves bytes aside; the catalog is
disposable by construction. The module reuses the store's own path layout, object
store, and reference-scan primitives rather than re-deriving them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field

from runtime_adapters.file._catalog_index import CatalogIndex
from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.object_store import FileObjectStore

# The object-reference form the offload writer emits into event/message JSON:
# ``/large_tool_results/<64-char lowercase hex sha256>``. Matching this exact
# shape (rather than any bare 64-hex token) keeps dangling-ref diagnosis precise
# — an audit hash-chain digest that happens to be 64 hex chars is never
# mis-reported as a missing object.
_OBJECT_REF = re.compile(r"/large_tool_results/([0-9a-f]{64})")

_CORRUPT_DIR = ".corrupt"


class JsonlLineKind(StrEnum):
    """How a malformed JSONL line relates to the fail-closed read contract."""

    # A malformed *final* line with only blank lines after it: an append
    # interrupted by a crash before ``fsync``. Never durably committed — safe to
    # drop, exactly as the live read path does.
    TORN_TAIL = "torn_tail"
    # A malformed line with a committed (non-blank) line after it: real interior
    # corruption. Everything from this line on is untrusted; the live read path
    # raises rather than return a truncated prefix.
    INTERIOR_CORRUPT = "interior_corrupt"


class _ScannedStream:
    """Non-raising scan of one JSONL file, mirroring the fail-closed contract.

    Unlike :func:`JsonlIo.iter_lines` (which raises on interior corruption), this
    classifies the failure and preserves both the trusted good prefix and any
    parseable post-corruption lines so the repair tools can act on them.
    """

    __slots__ = (
        "good_records",
        "good_raw_lines",
        "salvageable_tail_raw",
        "malformed_line_no",
        "malformed_kind",
        "content_lines",
    )

    def __init__(self) -> None:
        # Records/raw lines of the trusted prefix (before any malformed line).
        self.good_records: list[dict] = []
        self.good_raw_lines: list[str] = []
        # Parseable lines that appear *after* an interior-corruption point. Real
        # JSON, but past the fail-closed boundary, so never mixed into canonical
        # history — surfaced verbatim in a labelled salvage sidecar only.
        self.salvageable_tail_raw: list[str] = []
        self.malformed_line_no: int | None = None
        self.malformed_kind: JsonlLineKind | None = None
        self.content_lines = 0

    @property
    def has_interior_corruption(self) -> bool:
        return self.malformed_kind is JsonlLineKind.INTERIOR_CORRUPT


class StreamDiagnosis(BaseModel):
    """Corruption verdict for a single JSONL stream file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    content_lines: int = Field(ge=0)
    recovered_records: int = Field(ge=0)
    malformed_line: int | None = None
    malformed_kind: JsonlLineKind | None = None
    salvageable_tail_lines: int = Field(ge=0, default=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def healthy(self) -> bool:
        # A torn trailing line is benign (the live read path tolerates and drops
        # it); only interior corruption makes a stream unhealthy.
        return self.malformed_kind is not JsonlLineKind.INTERIOR_CORRUPT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_repair(self) -> bool:
        return self.malformed_kind is JsonlLineKind.INTERIOR_CORRUPT

    @property
    def has_torn_tail(self) -> bool:
        return self.malformed_kind is JsonlLineKind.TORN_TAIL


class ConversationDiagnosis(BaseModel):
    """Aggregate corruption verdict for one conversation session folder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_dir: str
    conversation_id: str | None = None
    org_id: str | None = None
    meta_readable: bool
    streams: tuple[StreamDiagnosis, ...]
    dangling_object_refs: tuple[str, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_repair(self) -> bool:
        return any(stream.needs_repair for stream in self.streams)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def healthy(self) -> bool:
        return (
            self.meta_readable
            and not self.dangling_object_refs
            and all(stream.healthy for stream in self.streams)
        )


class StoreDiagnosis(BaseModel):
    """Whole-store diagnosis: per-conversation verdicts plus orphan blobs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    conversations: tuple[ConversationDiagnosis, ...]
    orphan_objects: tuple[str, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def healthy(self) -> bool:
        return not self.orphan_objects and all(
            conversation.healthy for conversation in self.conversations
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_dangling_refs(self) -> int:
        return sum(len(c.dangling_object_refs) for c in self.conversations)


class DroppedRecords(BaseModel):
    """One reason-coded bucket of records a salvage pass could not recover."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    reason: JsonlLineKind
    from_line: int
    count: int = Field(ge=0)


class SalvageReport(BaseModel):
    """What a :meth:`StoreRepair.salvage_export` recovered and what it dropped."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    destination: str
    conversations_exported: int = Field(ge=0)
    records_recovered: int = Field(ge=0)
    salvageable_tail_records: int = Field(ge=0)
    objects_copied: int = Field(ge=0)
    dropped: tuple[DroppedRecords, ...]
    missing_objects: tuple[str, ...]


class QuarantinedStream(BaseModel):
    """Record of one corrupt tail moved aside during in-place repair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    quarantine_path: str
    good_prefix_records: int = Field(ge=0)
    quarantined_lines: int = Field(ge=0)


class QuarantineResult(BaseModel):
    """Outcome of quarantining every corrupt tail under one conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_dir: str
    streams: tuple[QuarantinedStream, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def changed(self) -> bool:
        return bool(self.streams)


class StoreRepair:
    """Offline diagnosis + salvage + guarded repair over one file-store root.

    Read-only by default: :meth:`diagnose` and :meth:`salvage_export` never
    mutate the source store. :meth:`rebuild_catalog` and
    :meth:`quarantine_corrupt_tail` are the opt-in write paths, and even they
    only *move* or *rebuild disposable* data — never hard-delete user records.
    """

    _CANONICAL_STREAMS = (
        FileStoreLayout.EVENTS_FILE,
        FileStoreLayout.MESSAGES_FILE,
        FileStoreLayout.RUNS_FILE,
    )

    def __init__(self, root: str | Path) -> None:
        self._layout = FileStoreLayout(Path(root))
        self._object_store = FileObjectStore(self._layout)

    @property
    def layout(self) -> FileStoreLayout:
        return self._layout

    # ------------------------------------------------------------------
    # Diagnosis
    # ------------------------------------------------------------------

    def diagnose(self) -> StoreDiagnosis:
        """Scan the whole store; never raises on corrupt input."""

        conversations: list[ConversationDiagnosis] = []
        referenced: set[str] = set()
        for conversation_dir in self._iter_conversation_dirs():
            diagnosis, refs = self._diagnose_conversation(conversation_dir)
            conversations.append(diagnosis)
            referenced |= refs
        present = set(self._object_store.iter_digests())
        orphans = sorted(present - referenced)
        return StoreDiagnosis(
            root=str(self._layout.root),
            conversations=tuple(conversations),
            orphan_objects=tuple(orphans),
        )

    def diagnose_conversation(self, conversation_dir: Path) -> ConversationDiagnosis:
        """Diagnose a single conversation session folder; never raises.

        The per-conversation slice of :meth:`diagnose`, used by the live store's
        health API to answer "does this chat need repair?" without scanning the
        whole tree.
        """

        diagnosis, _refs = self._diagnose_conversation(conversation_dir)
        return diagnosis

    def _diagnose_conversation(
        self, conversation_dir: Path
    ) -> tuple[ConversationDiagnosis, set[str]]:
        meta = self._read_meta(conversation_dir)
        streams: list[StreamDiagnosis] = []
        refs: set[str] = set()

        # Meta may itself carry an object ref.
        if meta is not None:
            refs |= _OBJECT_REF_from_text(json.dumps(meta))

        for stream_path in self._stream_paths(conversation_dir):
            scan = self._scan_stream(stream_path)
            streams.append(
                StreamDiagnosis(
                    relative_path=self._relative(stream_path),
                    content_lines=scan.content_lines,
                    recovered_records=len(scan.good_records),
                    malformed_line=scan.malformed_line_no,
                    malformed_kind=scan.malformed_kind,
                    salvageable_tail_lines=len(scan.salvageable_tail_raw),
                )
            )
            for raw in (*scan.good_raw_lines, *scan.salvageable_tail_raw):
                refs |= _OBJECT_REF_from_text(raw)

        dangling = sorted(
            digest for digest in refs if not self._object_store.exists(digest)
        )
        diagnosis = ConversationDiagnosis(
            relative_dir=self._relative(conversation_dir),
            conversation_id=(meta or {}).get("conversation_id"),
            org_id=(meta or {}).get("org_id"),
            meta_readable=meta is not None,
            streams=tuple(streams),
            dangling_object_refs=tuple(dangling),
        )
        return diagnosis, refs

    # ------------------------------------------------------------------
    # Salvage export
    # ------------------------------------------------------------------

    def salvage_export(
        self, destination: str | Path, *, conversation_dir: Path | None = None
    ) -> SalvageReport:
        """Emit a recovery bundle of every recoverable record + its objects.

        Recovered lines are copied *verbatim* — never re-serialized — so the
        bundle can never fabricate or reorder a record. Post-corruption
        parseable lines are preserved in a ``<stream>.recovered-tail`` sidecar,
        labelled as unverified and out of canonical order.
        """

        dest = Path(destination)
        FileStoreLayout.ensure_dir(dest)
        dirs = (
            [conversation_dir]
            if conversation_dir is not None
            else list(self._iter_conversation_dirs())
        )
        dropped: list[DroppedRecords] = []
        referenced: set[str] = set()
        recovered = 0
        tail_records = 0
        exported = 0

        for conversation_dir in dirs:
            out_dir = dest / "conversations" / conversation_dir.name
            FileStoreLayout.ensure_dir(out_dir)
            exported += 1

            meta = self._read_meta(conversation_dir)
            if meta is not None:
                self._write_text(
                    out_dir / FileStoreLayout.CONVERSATION_META,
                    json.dumps(meta, ensure_ascii=False, indent=2),
                )
                referenced |= _OBJECT_REF_from_text(json.dumps(meta))

            for stream_path in self._stream_paths(conversation_dir):
                scan = self._scan_stream(stream_path)
                target = out_dir / self._stream_relative_name(
                    conversation_dir, stream_path
                )
                if scan.good_raw_lines:
                    self._write_lines(target, scan.good_raw_lines)
                recovered += len(scan.good_records)
                for raw in (*scan.good_raw_lines, *scan.salvageable_tail_raw):
                    referenced |= _OBJECT_REF_from_text(raw)
                if scan.salvageable_tail_raw:
                    self._write_lines(
                        target.with_name(target.name + ".recovered-tail"),
                        scan.salvageable_tail_raw,
                    )
                    tail_records += len(scan.salvageable_tail_raw)
                if scan.malformed_kind is not None:
                    dropped.append(
                        DroppedRecords(
                            relative_path=self._relative(stream_path),
                            reason=scan.malformed_kind,
                            from_line=scan.malformed_line_no or 0,
                            count=self._dropped_count(scan),
                        )
                    )

        missing = self._copy_objects(referenced, dest)
        report = SalvageReport(
            root=str(self._layout.root),
            destination=str(dest),
            conversations_exported=exported,
            records_recovered=recovered,
            salvageable_tail_records=tail_records,
            objects_copied=len(referenced) - len(missing),
            dropped=tuple(dropped),
            missing_objects=tuple(sorted(missing)),
        )
        self._write_text(dest / "SALVAGE_REPORT.json", report.model_dump_json(indent=2))
        return report

    def _copy_objects(self, digests: set[str], dest: Path) -> set[str]:
        """Copy referenced blobs into ``dest/objects``; return missing digests."""

        missing: set[str] = set()
        for digest in digests:
            source = self._layout.object_path(digest)
            if not source.exists():
                missing.add(digest)
                continue
            target = dest / "objects" / "sha256" / digest[:2] / digest
            FileStoreLayout.ensure_dir(target.parent)
            shutil.copyfile(source, target)
        return missing

    # ------------------------------------------------------------------
    # In-place repair (opt-in, guarded)
    # ------------------------------------------------------------------

    def rebuild_catalog(self) -> None:
        """Rebuild the disposable SQLite catalog from recoverable JSONL truth.

        The live store's rebuild fails closed on interior corruption; this one
        indexes the recoverable prefix of every conversation so listing/search
        work again even before a corrupt tail is quarantined. Any prior DB (and
        its WAL/SHM sidecars) is moved aside, never trusted.
        """

        self._retire_catalog_files()
        index = CatalogIndex(self._layout.index_db_path)
        index.connect()
        try:
            conversations: list[dict] = []
            messages: list[dict] = []
            runs: list[dict] = []
            events: list[dict] = []
            for conversation_dir in self._iter_conversation_dirs():
                meta = self._read_meta(conversation_dir)
                if meta is None:
                    # No metadata → cannot scope rows; skip rather than guess.
                    continue
                conversations.append(meta)
                org_id = meta.get("org_id", "")
                messages.extend(
                    self._recoverable(conversation_dir, FileStoreLayout.MESSAGES_FILE)
                )
                runs.extend(
                    self._recoverable(conversation_dir, FileStoreLayout.RUNS_FILE)
                )
                for doc in self._recoverable(
                    conversation_dir, FileStoreLayout.EVENTS_FILE
                ):
                    events.append({**doc, "org_id": org_id})
                sub_dir = conversation_dir / FileStoreLayout.SUBAGENTS_DIR
                if sub_dir.is_dir():
                    for sub_path in sorted(sub_dir.iterdir()):
                        if sub_path.suffix != ".jsonl":
                            continue
                        for doc in self._scan_stream(sub_path).good_records:
                            events.append({**doc, "org_id": org_id})
            index.rebuild(
                conversations=conversations,
                messages=messages,
                runs=runs,
                events=events,
            )
        finally:
            index.close()

    def quarantine_corrupt_tail(self, conversation_dir: Path) -> QuarantineResult:
        """Move each stream's unrecoverable tail into a ``.corrupt/`` sidecar.

        For every stream with interior corruption, the good prefix is rewritten
        atomically (temp + ``fsync`` + ``os.replace``) as the canonical file and
        the tail (from the first malformed line onward) is *moved* — never
        deleted — into ``<dir>/.corrupt/<stream>.<utc>.jsonl``. Healthy streams
        are left untouched, so the call is a safe no-op on a clean conversation.
        """

        moved: list[QuarantinedStream] = []
        for stream_path in self._stream_paths(conversation_dir):
            scan = self._scan_stream(stream_path)
            if not scan.has_interior_corruption:
                continue
            corrupt_dir = FileStoreLayout.ensure_dir(conversation_dir / _CORRUPT_DIR)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            quarantine_path = corrupt_dir / f"{stream_path.name}.{stamp}.jsonl"
            # Preserve the full original bytes of the corrupt tail before the
            # canonical file is rewritten to only its good prefix.
            tail_bytes = self._tail_bytes(stream_path, scan.malformed_line_no or 1)
            quarantine_path.write_bytes(tail_bytes)
            FileStoreLayout.restrict_file(quarantine_path)
            self._atomic_rewrite_lines(stream_path, scan.good_raw_lines)
            moved.append(
                QuarantinedStream(
                    relative_path=self._relative(stream_path),
                    quarantine_path=self._relative(quarantine_path),
                    good_prefix_records=len(scan.good_records),
                    quarantined_lines=scan.content_lines - len(scan.good_records),
                )
            )
        return QuarantineResult(
            relative_dir=self._relative(conversation_dir),
            streams=tuple(moved),
        )

    # ------------------------------------------------------------------
    # Stream scanning (non-raising fail-closed classifier)
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_stream(path: Path) -> _ScannedStream:
        """Classify a JSONL file without raising (see :class:`_ScannedStream`)."""

        result = _ScannedStream()
        if not path.exists():
            return result
        interior = False
        pending_bad: int | None = None
        with open(path, encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                result.content_lines += 1
                try:
                    parsed = json.loads(stripped)
                    ok = True
                except json.JSONDecodeError:
                    parsed = None
                    ok = False
                if interior:
                    # Past the corruption boundary: keep parseable lines as an
                    # unverified salvageable tail, ignore further malformed ones.
                    if ok:
                        result.salvageable_tail_raw.append(stripped)
                    continue
                if pending_bad is not None:
                    # A content line follows the earlier malformed line: this is
                    # interior corruption, not a torn tail.
                    interior = True
                    result.malformed_line_no = pending_bad
                    result.malformed_kind = JsonlLineKind.INTERIOR_CORRUPT
                    if ok:
                        result.salvageable_tail_raw.append(stripped)
                    continue
                if ok:
                    result.good_records.append(parsed)
                    result.good_raw_lines.append(stripped)
                else:
                    pending_bad = line_number
        if pending_bad is not None and not interior:
            # Malformed final line with nothing committed after it: torn tail.
            result.malformed_line_no = pending_bad
            result.malformed_kind = JsonlLineKind.TORN_TAIL
        return result

    def _recoverable(self, conversation_dir: Path, filename: str) -> list[dict]:
        return self._scan_stream(conversation_dir / filename).good_records

    @staticmethod
    def _dropped_count(scan: _ScannedStream) -> int:
        if scan.malformed_kind is JsonlLineKind.TORN_TAIL:
            return 1
        # Interior corruption drops everything from the malformed line onward.
        return scan.content_lines - len(scan.good_records)

    @staticmethod
    def _tail_bytes(path: Path, from_line: int) -> bytes:
        """Return the verbatim bytes of ``path`` from ``from_line`` (1-based) on.

        Counts *physical* lines so the quarantined sidecar preserves the exact
        original bytes — blank lines included — of the corrupt region.
        """

        out: list[str] = []
        with open(path, encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if line_number >= from_line:
                    out.append(raw)
        return "".join(out).encode("utf-8")

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------

    def _iter_conversation_dirs(self) -> list[Path]:
        sessions_root = self._layout.workspaces_dir
        if not sessions_root.exists():
            return []
        dirs: list[Path] = []
        for workspace_dir in sorted(sessions_root.iterdir()):
            sessions_dir = workspace_dir / "sessions"
            if not sessions_dir.is_dir():
                continue
            dirs.extend(
                path for path in sorted(sessions_dir.iterdir()) if path.is_dir()
            )
        return dirs

    def _stream_paths(self, conversation_dir: Path) -> list[Path]:
        paths = [conversation_dir / name for name in self._CANONICAL_STREAMS]
        sub_dir = conversation_dir / FileStoreLayout.SUBAGENTS_DIR
        if sub_dir.is_dir():
            paths.extend(
                path for path in sorted(sub_dir.iterdir()) if path.suffix == ".jsonl"
            )
        return paths

    @staticmethod
    def _stream_relative_name(conversation_dir: Path, stream_path: Path) -> str:
        return str(stream_path.relative_to(conversation_dir))

    @staticmethod
    def _read_meta(conversation_dir: Path) -> dict | None:
        try:
            return JsonlIo.read_json(
                conversation_dir / FileStoreLayout.CONVERSATION_META
            )
        except (json.JSONDecodeError, OSError):
            return None

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._layout.root))
        except ValueError:
            return str(path)

    def _retire_catalog_files(self) -> None:
        db_path = self._layout.index_db_path
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for suffix in ("", "-wal", "-shm"):
            candidate = db_path.with_name(db_path.name + suffix)
            if candidate.exists():
                candidate.replace(candidate.with_name(f"{candidate.name}.{stamp}.bak"))

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        FileStoreLayout.ensure_dir(path.parent)
        path.write_text(text, encoding="utf-8")
        FileStoreLayout.restrict_file(path)

    def _write_lines(self, path: Path, lines: list[str]) -> None:
        self._write_text(path, "\n".join(lines) + "\n" if lines else "")

    @staticmethod
    def _atomic_rewrite_lines(path: Path, lines: list[str]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        body = ("\n".join(lines) + "\n") if lines else ""
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        FileStoreLayout.restrict_file(path)


def _OBJECT_REF_from_text(text: str) -> set[str]:
    """Extract every ``/large_tool_results/<sha256>`` digest referenced in text."""

    return set(_OBJECT_REF.findall(text))


class RepairCli:
    """Thin CLI over :class:`StoreRepair` (``python -m runtime_adapters.file.repair``).

    Kept behaviourally inside a class per the service's organisation rules; the
    module-level ``__main__`` guard only forwards ``argv``.
    """

    @classmethod
    def main(cls, argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(
            prog="runtime_adapters.file.repair",
            description="Diagnose, salvage, and repair a corrupt file-store root.",
        )
        sub = parser.add_subparsers(dest="command", required=True)

        diag = sub.add_parser("diagnose", help="scan and print a JSON diagnosis")
        diag.add_argument("root")

        salvage = sub.add_parser("salvage", help="export a recovery bundle")
        salvage.add_argument("root")
        salvage.add_argument("destination")

        rebuild = sub.add_parser(
            "rebuild-index", help="rebuild the disposable SQLite catalog"
        )
        rebuild.add_argument("root")

        quarantine = sub.add_parser(
            "quarantine", help="move corrupt tails into .corrupt/ sidecars"
        )
        quarantine.add_argument("root")

        args = parser.parse_args(argv)
        repair = StoreRepair(args.root)
        if args.command == "diagnose":
            print(repair.diagnose().model_dump_json(indent=2))
        elif args.command == "salvage":
            print(repair.salvage_export(args.destination).model_dump_json(indent=2))
        elif args.command == "rebuild-index":
            repair.rebuild_catalog()
            print(json.dumps({"rebuilt": True, "root": str(repair.layout.root)}))
        elif args.command == "quarantine":
            results = [
                repair.quarantine_corrupt_tail(conversation_dir).model_dump()
                for conversation_dir in repair._iter_conversation_dirs()
            ]
            print(json.dumps({"quarantined": results}, indent=2, default=str))
        return 0


__all__ = (
    "JsonlLineKind",
    "StreamDiagnosis",
    "ConversationDiagnosis",
    "StoreDiagnosis",
    "DroppedRecords",
    "SalvageReport",
    "QuarantinedStream",
    "QuarantineResult",
    "StoreRepair",
    "RepairCli",
)


if __name__ == "__main__":  # pragma: no cover - CLI shim
    sys.exit(RepairCli.main())
