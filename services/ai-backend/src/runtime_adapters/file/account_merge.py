"""Account-merge re-key for the file-native runtime store (PRD §6.4).

Linking a Google account to an existing device account absorbs one tenancy into
another. `backend` re-keys its own tables in a single transaction and then calls
`POST /internal/v1/admin/account-merge` so the runtime re-keys its share. On the
desktop that call previously returned 501 — no file re-keyer existed — so the
saga's runtime leg could never complete and a linked account's chats stayed
under the absorbed tenancy.

Two halves, and both must land or neither is true:

* **The materialized view** (the dicts the store serves reads from) is re-keyed
  by :class:`~runtime_adapters.in_memory.account_merge.InMemoryAccountMergeRekeyer`.
  That is not a shortcut: `FileRuntimeApiStore` and `InMemoryRuntimeApiStore`
  are both `MaterializedViewStoreBase`, so the dict surface is identical, and
  reusing the audited re-key rules is how the two backends stay comparable.
* **The canonical JSONL on disk** is rewritten here. Without this the merge
  would survive only until the next boot, when `open()` replays the old files
  back over the corrected view — the exact shape of bug that makes an
  in-memory-only fix look green and fail overnight.

On-disk work is a move plus a field rewrite. A conversation's directory name is
``safe_key(conversation_id)`` and conversation ids are globally unique, so the
directory keeps its name and simply moves from the absorbed workspace to the
survivor's — no collision is possible and no id is rewritten.

Deliberately untouched, mirroring the other two backends:

* the hash-chained audit ledger — append-only across every backend; the caller
  appends a merge marker to the survivor chain instead;
* event envelopes carry no tenancy fields, so per-run ``sequence_no`` ordering
  is preserved by construction;
* the content-addressed object store — blobs are keyed by digest, not tenancy.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.in_memory.account_merge import InMemoryAccountMergeRekeyer

if TYPE_CHECKING:  # pragma: no cover — typing only
    from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore


class FileAccountMergeRekeyer:
    """Move one absorbed account's file-native records onto the survivor."""

    #: Ledger that must never be rewritten. The audit chain is append-only and
    #: hash-linked: rewriting a row breaks every subsequent link.
    AUDIT_LEDGER = "audit_log"

    #: Owner columns rewritten when they name the absorbed user. Shared with the
    #: in-memory re-keyer so a field added there is covered here too, rather
    #: than the two catalogs silently drifting apart.
    USER_ID_FIELDS = InMemoryAccountMergeRekeyer._USER_ID_FIELDS  # noqa: SLF001

    def __init__(self, store: FileRuntimeApiStore) -> None:
        self._store = store
        self._layout = store._layout  # noqa: SLF001
        self.tables: dict[str, int] = {}
        self.warnings: list[str] = []

    def rekey(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> tuple[dict[str, int], list[str]]:
        """Re-key the absorbed account, on disk and in the served view.

        Returns the same ``(tables, warnings)`` shape as the other backends so
        the API response does not vary by store.
        """

        view = InMemoryAccountMergeRekeyer(
            absorbed_org_id=absorbed_org_id,
            absorbed_user_id=absorbed_user_id,
            survivor_org_id=survivor_org_id,
            survivor_user_id=survivor_user_id,
        )
        # Disk first: if the rewrite raises, the served view is still the old
        # (consistent) tenancy rather than a corrected view over stale files.
        self._rekey_sessions_on_disk(
            absorbed_org_id=absorbed_org_id,
            absorbed_user_id=absorbed_user_id,
            survivor_org_id=survivor_org_id,
            survivor_user_id=survivor_user_id,
        )
        self._rekey_state_ledgers(
            absorbed_org_id=absorbed_org_id,
            absorbed_user_id=absorbed_user_id,
            survivor_org_id=survivor_org_id,
            survivor_user_id=survivor_user_id,
        )
        view.rekey_store(self._store)  # type: ignore[arg-type]
        self.tables = dict(view.tables)
        self.warnings = list(view.warnings)
        # The catalog index is derived from the dicts we just corrected; leaving
        # it stale would keep serving the absorbed tenancy from list/search.
        self._store._rebuild_index()  # noqa: SLF001
        return self.tables, self.warnings

    # ----- disk ---------------------------------------------------------

    def _rekey_sessions_on_disk(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> None:
        """Rewrite tenancy in every absorbed session folder, then move it."""

        source_sessions = self._layout.sessions_dir(absorbed_org_id)
        if not source_sessions.is_dir():
            return
        destination_sessions = self._layout.ensure_dir(
            self._layout.sessions_dir(survivor_org_id)
        )
        moved = 0
        for conversation_dir in sorted(source_sessions.iterdir()):
            if not conversation_dir.is_dir():
                continue
            self._rewrite_conversation_dir(
                conversation_dir,
                absorbed_org_id=absorbed_org_id,
                absorbed_user_id=absorbed_user_id,
                survivor_org_id=survivor_org_id,
                survivor_user_id=survivor_user_id,
            )
            destination = destination_sessions / conversation_dir.name
            if destination.exists():
                # Impossible via conversation_id (globally unique), so treat it
                # as a real anomaly: keep the survivor's copy and say so rather
                # than overwriting data we cannot reconstruct.
                self.warnings.append(
                    "session folder already present under the survivor workspace; "
                    f"kept the survivor copy for {conversation_dir.name}"
                )
                continue
            shutil.move(str(conversation_dir), str(destination))
            moved += 1
        if moved:
            self.tables["file_session_folders"] = moved
        # An emptied workspace directory is meaningless; leaving it behind would
        # advertise a tenancy that no longer owns anything.
        self._remove_if_empty(source_sessions)
        self._remove_if_empty(self._layout.workspace_dir(absorbed_org_id))

    def _rewrite_conversation_dir(
        self,
        conversation_dir: Path,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> None:
        """Rewrite tenancy fields in one conversation's canonical files."""

        meta = conversation_dir / self._layout.CONVERSATION_META
        if meta.is_file():
            record = JsonlIo.read_json(meta)
            if isinstance(record, dict):
                JsonlIo.rewrite_json(
                    meta,
                    self._rekey_record(
                        record,
                        absorbed_org_id=absorbed_org_id,
                        absorbed_user_id=absorbed_user_id,
                        survivor_org_id=survivor_org_id,
                        survivor_user_id=survivor_user_id,
                    ),
                )
        jsonl_files = [
            conversation_dir / self._layout.EVENTS_FILE,
            conversation_dir / self._layout.MESSAGES_FILE,
            conversation_dir / self._layout.RUNS_FILE,
        ]
        subagents = conversation_dir / self._layout.SUBAGENTS_DIR
        if subagents.is_dir():
            jsonl_files.extend(sorted(subagents.glob("*.jsonl")))
        for path in jsonl_files:
            self._rewrite_jsonl(
                path,
                absorbed_org_id=absorbed_org_id,
                absorbed_user_id=absorbed_user_id,
                survivor_org_id=survivor_org_id,
                survivor_user_id=survivor_user_id,
            )

    def _rekey_state_ledgers(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> None:
        """Rewrite tenancy in the back-office ledgers, never the audit chain."""

        state_dir = self._layout.state_dir
        if not state_dir.is_dir():
            return
        for path in sorted(state_dir.glob("*.jsonl")):
            if path.stem == self.AUDIT_LEDGER:
                continue
            self._rewrite_jsonl(
                path,
                absorbed_org_id=absorbed_org_id,
                absorbed_user_id=absorbed_user_id,
                survivor_org_id=survivor_org_id,
                survivor_user_id=survivor_user_id,
            )

    def _rewrite_jsonl(
        self,
        path: Path,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> None:
        """Rewrite one JSONL file in place, atomically, when anything changed."""

        if not path.is_file():
            return
        rows = list(JsonlIo.iter_lines(path))
        rekeyed = [
            self._rekey_record(
                row,
                absorbed_org_id=absorbed_org_id,
                absorbed_user_id=absorbed_user_id,
                survivor_org_id=survivor_org_id,
                survivor_user_id=survivor_user_id,
            )
            for row in rows
        ]
        if rekeyed != rows:
            JsonlIo.rewrite_lines(path, rekeyed)

    def _rekey_record(
        self,
        record: dict,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> dict:
        """Return *record* with absorbed tenancy replaced by the survivor's.

        A record is only touched when it belongs to the absorbed org — the same
        guard the SQL re-keyer applied per table. A user field is rewritten only
        when it names the absorbed user, so a shared org's other members keep
        their ownership.
        """

        if record.get("org_id") != absorbed_org_id:
            return record
        updated = dict(record)
        updated["org_id"] = survivor_org_id
        for field in self.USER_ID_FIELDS:
            if updated.get(field) == absorbed_user_id:
                updated[field] = survivor_user_id
        return updated

    @staticmethod
    def _remove_if_empty(path: Path) -> None:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


__all__ = ("FileAccountMergeRekeyer",)
