"""Cross-process publication lock and durable GC quarantine paths."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, local
from typing import Iterator

from runtime_adapters.file._jsonl import JsonlIo
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


@dataclass(frozen=True, slots=True)
class FileArtifactCandidateState:
    provenance_org_id: str | None
    candidate_since: datetime


@dataclass(frozen=True, slots=True)
class FileArtifactQuarantineState:
    quarantined_at: datetime


class FileArtifactPublicationCoordinator:
    """Serialize artifact publication and GC across processes portably."""

    def __init__(self, layout: FileStoreLayout) -> None:
        self.layout = layout
        self._process_lock = RLock()
        self._local = local()
        self.lock = self
        self.lock_path = layout.state_dir / ".artifact-publication.lock"
        self.state_path = layout.state_path("artifact_gc_state")
        self.gc_quarantine_dir = layout.objects_dir / ".gc-quarantine"
        self.gc_reaping_dir = layout.objects_dir / ".gc-reaping"
        self.candidates: dict[str, FileArtifactCandidateState] = {}
        self.quarantine: dict[str, FileArtifactQuarantineState] = {}
        FileStoreLayout.ensure_dir(self.lock_path.parent)
        FileStoreLayout.ensure_dir(self.gc_quarantine_dir)
        FileStoreLayout.ensure_dir(self.gc_reaping_dir)
        with self.locked():
            self._load_state_locked()
            self._reconcile_filesystem_locked()

    def __enter__(self) -> None:
        context = self.locked()
        stack = getattr(self._local, "contexts", [])
        stack.append(context)
        self._local.contexts = stack
        context.__enter__()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        context = self._local.contexts.pop()
        context.__exit__(exc_type, exc_value, traceback)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the reusable process lock and the cross-process advisory file."""

        with self._process_lock:
            depth = getattr(self._local, "depth", 0)
            if depth:
                self._local.depth = depth + 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                acquire_exclusive(descriptor)
                self._local.depth = 1
                # Another process may have appended candidate/quarantine state
                # since this coordinator instance was created. Refresh only
                # after acquiring the cross-process lock so every publication,
                # reference, GC, and reaper decision sees durable truth.
                self._load_state_locked()
                self._reconcile_filesystem_locked()
                yield
            finally:
                self._local.depth = 0
                release_exclusive(descriptor)
                os.close(descriptor)

    def quarantine_path(self, blob_key: str) -> Path:
        return self.gc_quarantine_dir / blob_key[:2] / blob_key

    def reaping_path(self, blob_key: str) -> Path:
        return self.gc_reaping_dir / blob_key[:2] / blob_key

    def restore_locked(self, blob_key: str) -> bool:
        """Atomically restore one GC-quarantined digest while locked."""

        source = self.quarantine_path(blob_key)
        if not source.exists():
            source = self.reaping_path(blob_key)
        if not source.exists():
            return False
        target = self.layout.object_path(blob_key)
        FileStoreLayout.ensure_dir(target.parent)
        if target.exists():
            return False
        os.replace(source, target)
        self._fsync_directory(source.parent)
        self._fsync_directory(target.parent)
        self.quarantine.pop(blob_key, None)
        self._append_state_locked("restore", blob_key)
        return True

    def require_active_locked(self, blob_key: str) -> None:
        """Fail closed unless the digest's verified active bytes exist."""

        target = self.layout.object_path(blob_key)
        if not target.is_file():
            raise FileNotFoundError("artifact blob is unavailable")

    def rollback_restoration_locked(self, blob_key: str) -> None:
        """Return active bytes to quarantine after metadata commit failure."""

        active = self.layout.object_path(blob_key)
        quarantine = self.quarantine_path(blob_key)
        if not active.exists() or quarantine.exists():
            return
        FileStoreLayout.ensure_dir(quarantine.parent)
        os.replace(active, quarantine)
        self._fsync_directory(active.parent)
        self._fsync_directory(quarantine.parent)
        self.mark_quarantined_locked(
            blob_key=blob_key,
            quarantined_at=datetime.now(timezone.utc),
        )

    def record_candidate_locked(
        self,
        *,
        blob_key: str,
        provenance_org_id: str | None,
        candidate_since: datetime,
    ) -> None:
        current = self.candidates.get(blob_key)
        if current is not None and current.candidate_since <= candidate_since:
            return
        state = FileArtifactCandidateState(
            provenance_org_id=provenance_org_id,
            candidate_since=candidate_since,
        )
        self.candidates[blob_key] = state
        self._append_state_locked(
            "candidate",
            blob_key,
            provenance_org_id=provenance_org_id,
            candidate_since=candidate_since.isoformat(),
        )

    def cancel_candidate_locked(self, blob_key: str) -> None:
        removed = self.candidates.pop(blob_key, None)
        quarantined = self.quarantine.pop(blob_key, None)
        if removed is not None or quarantined is not None:
            self._append_state_locked("cancel", blob_key)

    def mark_quarantined_locked(
        self, *, blob_key: str, quarantined_at: datetime
    ) -> None:
        self.quarantine[blob_key] = FileArtifactQuarantineState(
            quarantined_at=quarantined_at
        )
        self._append_state_locked(
            "quarantine",
            blob_key,
            quarantined_at=quarantined_at.isoformat(),
        )

    def clear_reaped_locked(self, blob_key: str) -> None:
        self.candidates.pop(blob_key, None)
        self.quarantine.pop(blob_key, None)
        self._append_state_locked("reap", blob_key)

    def _load_state_locked(self) -> None:
        self.candidates.clear()
        self.quarantine.clear()
        for row in JsonlIo.iter_lines(self.state_path):
            blob_key = row.get("blob_key")
            if not isinstance(blob_key, str):
                continue
            op = row.get("op")
            if op == "candidate":
                self.candidates[blob_key] = FileArtifactCandidateState(
                    provenance_org_id=(
                        str(row["provenance_org_id"])
                        if row.get("provenance_org_id") is not None
                        else None
                    ),
                    candidate_since=datetime.fromisoformat(
                        str(row["candidate_since"]).replace("Z", "+00:00")
                    ),
                )
            elif op == "quarantine":
                self.quarantine[blob_key] = FileArtifactQuarantineState(
                    quarantined_at=datetime.fromisoformat(
                        str(row["quarantined_at"]).replace("Z", "+00:00")
                    )
                )
            elif op == "restore":
                self.quarantine.pop(blob_key, None)
            elif op in {"cancel", "reap"}:
                self.candidates.pop(blob_key, None)
                self.quarantine.pop(blob_key, None)

    def _reconcile_filesystem_locked(self) -> None:
        """Converge deterministic crash residues without deleting bytes."""

        for reaping in self.gc_reaping_dir.glob("*/*"):
            if not reaping.is_file():
                continue
            quarantine = self.quarantine_path(reaping.name)
            FileStoreLayout.ensure_dir(quarantine.parent)
            if not quarantine.exists():
                os.replace(reaping, quarantine)
                self._fsync_directory(reaping.parent)
                self._fsync_directory(quarantine.parent)
        now = datetime.now(timezone.utc)
        for quarantined in self.gc_quarantine_dir.glob("*/*"):
            if not quarantined.is_file():
                continue
            blob_key = quarantined.name
            if blob_key not in self.candidates:
                timestamp = datetime.fromtimestamp(
                    quarantined.stat().st_mtime, tz=timezone.utc
                )
                self.record_candidate_locked(
                    blob_key=blob_key,
                    provenance_org_id=None,
                    candidate_since=timestamp,
                )
            if blob_key not in self.quarantine:
                self.mark_quarantined_locked(
                    blob_key=blob_key,
                    quarantined_at=now,
                )
        for blob_key in tuple(self.quarantine):
            if self.quarantine_path(blob_key).exists():
                continue
            if self.layout.object_path(blob_key).exists():
                self.quarantine.pop(blob_key, None)
                self._append_state_locked("restore", blob_key)
            else:
                self.clear_reaped_locked(blob_key)

    def _append_state_locked(self, op: str, blob_key: str, **values: object) -> None:
        JsonlIo.append_line(
            self.state_path,
            {"op": op, "blob_key": blob_key, **values},
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        # Windows does not permit opening a directory descriptor for fsync.
        # Publication data itself is fsynced before the atomic replacement;
        # the byte-range lock still gives equivalent cross-process visibility.
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = ("FileArtifactPublicationCoordinator",)
