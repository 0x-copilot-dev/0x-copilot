"""Durable, single-writer idempotency ledger for the v2 CommitEngine (PRD-D2).

The desktop (``RUNTIME_STORE_BACKEND=file``) runs the CommitEngine in-process; an
in-memory claim would evaporate on restart, breaking at-most-once across a crash.
This adapter persists one file per ``commit_key`` under the file-store root, so a
claim written *before* a connector send survives a restart: a redelivered command
observes the claim and never resends.

The claim itself is the atomic filesystem primitive ``os.open(O_CREAT | O_EXCL)``
— it either creates the marker (the caller won) or raises ``FileExistsError`` (a
concurrent / prior attempt already claimed it). An ``asyncio.Lock`` serialises the
single-process desktop worker; ``O_EXCL`` is the defense-in-depth that also holds
across processes. ``complete`` rewrites the marker atomically (temp → ``fsync`` →
``os.replace``). Filenames are the sha256 of the ``commit_key`` so a
``stage_id:rev:decision_seq`` key (colons) is portable across every filesystem;
the original key rides inside the JSON.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import ClassVar

from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult
from agent_runtime.surfaces_v2.commit_engine import StageCommitLedgerEntry


class FileStageCommitLedger:
    """File-native ``StageCommitLedgerPort`` — one durable marker per ``commit_key``."""

    _SUBDIR: ClassVar[str] = "stage_commit_ledger"
    _TMP_SUFFIX: ClassVar[str] = ".tmp"
    _DIR_MODE: ClassVar[int] = 0o700
    _FILE_MODE: ClassVar[int] = 0o600

    _KEY = "commit_key"
    _COMMITTED = "committed"
    _RESULT = "result"

    def __init__(self, root: Path | str) -> None:
        base = Path(root).expanduser().resolve()
        # Accept either the file-store root or an already-scoped directory.
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def load(self, *, commit_key: str) -> StageCommitLedgerEntry | None:
        async with self._lock:
            return self._read(commit_key)

    async def claim(self, *, commit_key: str) -> bool:
        async with self._lock:
            path = self._path(commit_key)
            try:
                # Atomic create-if-absent: the ledger's claim primitive.
                fd = os.open(
                    path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, self._FILE_MODE
                )
            except FileExistsError:
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    self._encode(commit_key, committed=False, result=None), handle
                )
                handle.flush()
                os.fsync(handle.fileno())
            return True

    async def complete(self, *, commit_key: str, result: ConnectorCommitResult) -> None:
        async with self._lock:
            self._atomic_write(
                self._path(commit_key),
                self._encode(commit_key, committed=True, result=result),
            )

    # -- helpers -------------------------------------------------------------

    def _read(self, commit_key: str) -> StageCommitLedgerEntry | None:
        path = self._path(commit_key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        result_raw = raw.get(self._RESULT)
        result = (
            ConnectorCommitResult.model_validate(result_raw)
            if isinstance(result_raw, dict)
            else None
        )
        return StageCommitLedgerEntry(
            commit_key=str(raw.get(self._KEY, commit_key)),
            committed=bool(raw.get(self._COMMITTED, False)),
            result=result,
        )

    def _encode(
        self, commit_key: str, *, committed: bool, result: ConnectorCommitResult | None
    ) -> dict[str, object]:
        return {
            self._KEY: commit_key,
            self._COMMITTED: committed,
            self._RESULT: result.model_dump(mode="json")
            if result is not None
            else None,
        }

    def _atomic_write(self, target: Path, payload: dict[str, object]) -> None:
        tmp = target.with_name(target.name + self._TMP_SUFFIX)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        try:
            target.chmod(self._FILE_MODE)
        except OSError:  # pragma: no cover - some filesystems reject chmod
            pass

    def _path(self, commit_key: str) -> Path:
        digest = hashlib.sha256(commit_key.encode("utf-8")).hexdigest()
        return self._dir / f"{digest}.json"


__all__ = ["FileStageCommitLedger"]
