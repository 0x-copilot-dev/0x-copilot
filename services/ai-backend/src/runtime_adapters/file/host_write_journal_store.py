"""File-native durability for the agent-write undo journal.

Two existing primitives, no new storage concept:

* :class:`~runtime_adapters.file.object_store.FileObjectStore` holds the
  pre-image bytes, content-addressed. Identical prior versions of a file — the
  common case when an agent edits, re-reads and edits again — collapse to one
  blob, and binary content needs no encoding because the store speaks bytes.
* :class:`~runtime_adapters.file._state_ledger.StateLedger` holds the records,
  one append-only JSONL under ``state/host_write_journal.jsonl``. This is a
  ``load_puts`` list table, not a fold-by-key one: every capture is a distinct
  historical fact, and retention rewrites the file wholesale.

Both live under ``RUNTIME_FILE_STORE_ROOT``. That location is deliberate: the
floor admits host writes only inside the agent scratch or a writable granted
root, so the agent's own filesystem tools cannot rewrite the record of what it
did — see :mod:`agent_runtime.capabilities.desktop.write_journal`.
"""

from __future__ import annotations

from datetime import datetime

from agent_runtime.capabilities.desktop.write_journal import HostWriteRecord
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._state_ledger import StateLedger
from runtime_adapters.file.object_store import FileObjectStore

#: The ``state/`` table name. One ledger for the whole install: desktop is
#: single-tenant, and every record carries its own org/conversation/run.
TABLE = "host_write_journal"


class FileHostWriteJournalStore:
    """``StateLedger`` + ``FileObjectStore`` behind ``HostWriteJournalPort``."""

    def __init__(self, layout: FileStoreLayout, objects: FileObjectStore) -> None:
        self._layout = layout
        self._objects = objects
        FileStoreLayout.ensure_dir(layout.state_dir)
        self._ledger = StateLedger(layout.state_path(TABLE))

    def put_blob(self, data: bytes) -> str:
        """Store a pre-image and return its digest."""

        return self._objects.put(data).sha256

    def get_blob(self, digest: str) -> bytes:
        """Return a pre-image, verified against its digest by the object store."""

        return self._objects.get(digest)

    def append(self, record: HostWriteRecord) -> None:
        """Durably append one capture record."""

        self._ledger.append_put(record.model_dump(mode="json"))

    def records_for_run(
        self, *, org_id: str, run_id: str
    ) -> tuple[HostWriteRecord, ...]:
        """Every record for one run, in capture order.

        Scoped by ``org_id`` as well as ``run_id`` so a caller cannot reach
        another tenant's history by guessing a run id, even though the desktop
        profile has one tenant.
        """

        return tuple(
            sorted(
                (
                    record
                    for record in self._load()
                    if record.org_id == org_id and record.run_id == run_id
                ),
                key=lambda item: item.sequence,
            )
        )

    def prune(self, *, before: datetime) -> int:
        """Drop records captured before ``before`` and their orphaned blobs.

        Returns how many records went. The rewrite is the ledger's crash-safe
        temp-then-rename, so an interrupted prune leaves the prior log intact.
        Blobs are deleted only when NO surviving record still references them —
        content addressing means two files with identical prior bytes share one.
        """

        surviving: list[HostWriteRecord] = []
        expired: list[HostWriteRecord] = []
        for record in self._load():
            (expired if record.captured_at < before else surviving).append(record)
        if not expired:
            return 0
        self._ledger.rewrite(record.model_dump(mode="json") for record in surviving)
        live = {
            record.prior_sha256 for record in surviving if record.prior_sha256 is not None
        }
        for record in expired:
            if record.prior_sha256 is not None and record.prior_sha256 not in live:
                self._objects.delete(record.prior_sha256)
        return len(expired)

    def _load(self) -> list[HostWriteRecord]:
        """Parse the ledger, skipping rows a schema change made unreadable."""

        records: list[HostWriteRecord] = []
        for row in self._ledger.load_puts():
            try:
                records.append(HostWriteRecord.model_validate(row))
            except ValueError:
                # A row this build cannot read is history, not a live claim.
                # Dropping it from the view is strictly safer than failing the
                # whole listing and hiding every other undo.
                continue
        return records


__all__ = ("TABLE", "FileHostWriteJournalStore")
