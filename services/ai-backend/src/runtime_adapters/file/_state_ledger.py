"""Append-with-fold JSONL ledgers for the back-office ("state") tables.

Session data (conversations, messages, runs, events) lives in the folder layout
under ``workspaces/``. The relational back-office state that has no session
home — approvals, budgets, usage, pricing, retention, audit, workspace defaults,
the command queue — lives as one JSONL ledger per table under ``state/``.

Two write shapes, one file format (each line is an ``{"op": ...}`` envelope):

* **Fold-by-key tables** append ``put`` / ``delete`` ops; on load the store
  folds them into a dict (last write wins per key, deletes remove). This keeps
  the hot write path append-only.
* **Whole-collection tables** (those with multi-row cascade deletes, e.g.
  budget states/reservations, retention policies) atomically rewrite the file
  from the current in-memory set — trivially correct at desktop volumes.

The store owns the typed dicts and the per-table key functions; this class only
owns the bytes and the op vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from runtime_adapters.file._jsonl import JsonlIo

_OP = "op"
_PUT = "put"
_DELETE = "delete"
_RECORD = "record"
_KEY = "key"


class StateLedger:
    """One append-with-fold JSONL ledger file for a single back-office table."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def append_put(self, record_json: dict) -> None:
        """Append a ``put`` op carrying one record's JSON snapshot."""

        JsonlIo.append_line(self._path, {_OP: _PUT, _RECORD: record_json})

    def append_delete(self, key: str) -> None:
        """Append a ``delete`` op for one string key."""

        JsonlIo.append_line(self._path, {_OP: _DELETE, _KEY: key})

    def rewrite(self, records_json: Iterable[dict]) -> None:
        """Atomically replace the file with ``put`` ops for the given records."""

        JsonlIo.rewrite_lines(
            self._path, ({_OP: _PUT, _RECORD: record} for record in records_json)
        )

    def load_ops(self) -> list[tuple[str, dict | str]]:
        """Return ordered ops: ``("put", record_json)`` / ``("delete", key)``."""

        ops: list[tuple[str, dict | str]] = []
        for line in JsonlIo.iter_lines(self._path):
            op = line.get(_OP)
            if op == _PUT:
                record = line.get(_RECORD)
                if isinstance(record, dict):
                    ops.append((_PUT, record))
            elif op == _DELETE:
                key = line.get(_KEY)
                if isinstance(key, str):
                    ops.append((_DELETE, key))
        return ops

    def load_puts(self) -> list[dict]:
        """Return every ``put`` record in file order (for append-only lists)."""

        return [
            record
            for op, record in self.load_ops()
            if op == _PUT and isinstance(record, dict)
        ]


__all__ = ("StateLedger",)
