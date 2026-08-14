"""Prior-content capture for every agent write to the user's real disk.

Nothing the agent wrote could be undone. There is no ``git`` invocation
anywhere in this service or in the desktop main process, no pre-write content
capture, and no revert; every ``snapshot`` under this package is a permission
GRANT snapshot, not file bytes. So a single bad ``edit_file`` on a file the user
attached was final.

Why a content-addressed journal and not a shadow git tree
--------------------------------------------------------
OpenCode's ``snapshot/index.ts`` keeps a shadow ``--git-dir`` per worktree and
commits the whole tree per turn. That is right for a CLI that lives inside a
developer's repository and wrong here, for three reasons:

1. **No git.** The packaged app bundles Python and PostgreSQL; it does not
   bundle git, and a capability that silently disappears on a machine without
   a developer toolchain is the failure mode this program keeps re-learning.
2. **The granted root is not a repo.** A user attaches ``~/Documents``. A
   tree-level snapshot means ``git add -A`` over an arbitrarily large folder of
   the user's own content on every tool call — and it writes a ``.git``
   worktree pointer at a location we do not own.
3. **The store already exists.** ``runtime_adapters.file.FileObjectStore`` is
   an atomic, verify-on-read, quota-guarded content-addressed blob store, and
   ``StateLedger`` is the append-with-fold JSONL ledger this service already
   uses for back-office tables. Reusing them is one adapter, not a subsystem.

Our write granularity also happens to be exactly one file: deepagents'
``write``/``edit``/``delete`` each take a single ``file_path``, so per-file
capture is precise where a tree commit is approximate.

Where the bytes are captured
----------------------------
:class:`~agent_runtime.capabilities.desktop.host_floor.HostFilesystemFloor` —
the object every host path finally lands on, and the one place that already
decides whether a host write may happen at all. Capture runs immediately after
that verdict and immediately before the delegated call, so the journal can
never hold a path the floor refused.

Where the journal is NOT stored
-------------------------------
Not ``$COPILOT_HOME/.tmp`` (the agent's own scratch) and not inside any granted
root — the agent can write to both, so a journal there could be rewritten by the
thing it exists to hold accountable. It lives under ``RUNTIME_FILE_STORE_ROOT``,
alongside ``events.jsonl``: the floor refuses every host write there
(``permits_write`` admits only the scratch and writable granted roots), so the
agent cannot reach it.

Renames
-------
There is no rename op on deepagents' backend protocol. A model-driven rename is
a read, a write at the new path and a delete at the old, so it lands as two
records — ``CREATED`` at the new path and ``DELETED`` at the old — and
reverting both restores the original name and content.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import logging
import os
from pathlib import Path, PurePosixPath
from typing import Final, Protocol
from uuid import uuid4

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract

_LOGGER = logging.getLogger(__name__)

#: Largest prior file we read into memory to capture. Above this the write is
#: still RECORDED — with ``prior_sha256=None``, which reads as "changed, not
#: revertible" — rather than silently omitted. An honest "cannot undo this one"
#: is a usable answer; a missing row is not. ``os.lstat`` decides, so an
#: oversized file is never read.
MAX_CAPTURE_BYTES: Final = 8 * 1024 * 1024

#: How long a captured prior version is kept. Matches OpenCode's ``7.days``.
RETENTION_DAYS: Final = 7


class HostWriteKind(StrEnum):
    """What the agent's operation did to a path that the journal must undo."""

    #: Nothing was there. Undo = remove the file the agent created.
    CREATED = "created"
    #: A file was there and its bytes changed. Undo = write the prior bytes.
    MODIFIED = "modified"
    #: A file was there and the agent removed it. Undo = write it back.
    DELETED = "deleted"


class HostWriteRecord(RuntimeContract):
    """One captured pre-image, addressable down to a single tool call.

    ``tool_call_id`` is the provider's own tool-call id, read from the bound
    :class:`~agent_runtime.execution.call_identity.RuntimeToolCallIdentity`.
    It is what makes "undo just that one edit" expressible; ``None`` means the
    write happened outside a bound tool call (a direct backend call in a test,
    or a lane that has not bound run control) and is then reachable only through
    a whole-run revert.

    ``authorized_root`` is the granted root — or the agent scratch — that the
    floor admitted the write under. A revert re-checks the target against it, so
    the journal can never become a way to write somewhere the floor would not
    have.
    """

    entry_id: str = Field(min_length=1, max_length=64)
    org_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    tool_call_id: str | None = Field(default=None, max_length=256)
    sequence: int = Field(ge=0)
    path: str = Field(min_length=1, max_length=4096)
    authorized_root: str = Field(min_length=1, max_length=4096)
    kind: HostWriteKind
    prior_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    prior_size: int = Field(default=0, ge=0)
    prior_mode: int | None = Field(default=None, ge=0)
    captured_at: datetime

    @property
    def revertible(self) -> bool:
        """Can this record's prior state actually be put back?

        A ``CREATED`` record needs no bytes (undo is a delete). Anything else
        needs the pre-image, and a missing digest is the honest signal that
        capture declined — the file was above :data:`MAX_CAPTURE_BYTES`, or
        unreadable.
        """

        return self.kind is HostWriteKind.CREATED or self.prior_sha256 is not None


class HostWriteJournalPort(Protocol):
    """Durable side of the journal. Implemented by the file store adapter."""

    def put_blob(self, data: bytes) -> str:
        """Store ``data`` and return its sha256 hex digest."""

    def get_blob(self, digest: str) -> bytes:
        """Return the bytes for ``digest``; raise if absent or corrupted."""

    def append(self, record: HostWriteRecord) -> None:
        """Durably append one capture record."""

    def records_for_run(
        self, *, org_id: str, run_id: str
    ) -> tuple[HostWriteRecord, ...]:
        """Every record for one run, in capture order."""

    def prune(self, *, before: datetime) -> int:
        """Drop records captured before ``before``; return how many went."""


def path_within(path: str | None, root: str) -> bool:
    """Is ``path`` ``root`` or beneath it, compared segment-wise?

    Segment-wise so ``/a/ProjectsSecret`` is never admitted by ``/a/Projects``,
    and traversals are never admitted at all.
    :class:`~agent_runtime.capabilities.desktop.host_floor.HostFilesystemFloor`
    delegates its own containment check here so the floor and every revert
    decide identically — two spellings of one predicate is how a revert ends up
    writing somewhere the floor would have refused.
    """

    if not path or not path.startswith("/") or not root.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if ".." in parts:
        return False
    root_parts = PurePosixPath(root).parts
    return parts[: len(root_parts)] == root_parts


class HostWriteJournal:
    """Captures the pre-image of one run's host writes.

    Bound to one (org, conversation, run) by the worker wiring, so the floor
    only ever hands it a path. Every method is failure-tolerant: a run must not
    break because its undo history could not be written. A capture that fails is
    logged and the write proceeds — losing an undo is strictly better than
    losing the user's task.
    """

    def __init__(
        self,
        store: HostWriteJournalPort,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str,
        max_capture_bytes: int = MAX_CAPTURE_BYTES,
    ) -> None:
        self._store = store
        self._org_id = org_id
        self._conversation_id = conversation_id
        self._run_id = run_id
        self._max_capture_bytes = max_capture_bytes
        self._sequence = 0

    def capture(self, path: str, root: str, *, deleting: bool = False) -> None:
        """Record what is at ``path`` before the agent's operation lands.

        ``root`` is the floor's own answer to "what admitted this write" and is
        stored verbatim; the revert re-derives nothing.
        """

        try:
            self._capture(path, root, deleting=deleting)
        except Exception:  # noqa: BLE001 - undo history is never worth a failed run
            _LOGGER.warning("host_write_journal.capture_failed", exc_info=True)

    def _capture(self, path: str, root: str, *, deleting: bool) -> None:
        native = Path(path)
        try:
            stat = os.lstat(native)
        except (OSError, ValueError):
            stat = None
        if stat is not None and not os.path.isfile(native):
            # A directory, socket, fifo or dangling symlink. deepagents writes
            # files; anything else is not a pre-image we can restore, and
            # pretending otherwise would put a broken row in the undo list.
            return
        if stat is None:
            if deleting:
                # Deleting something that is not there changes nothing.
                return
            self._append(path, root, kind=HostWriteKind.CREATED)
            return
        kind = HostWriteKind.DELETED if deleting else HostWriteKind.MODIFIED
        if stat.st_size > self._max_capture_bytes:
            # Recorded and honestly marked un-revertible. `st_size` is consulted
            # BEFORE any read, so an oversized file is never pulled into memory.
            _LOGGER.info(
                "host_write_journal.capture_skipped_large size=%d limit=%d",
                stat.st_size,
                self._max_capture_bytes,
            )
            self._append(path, root, kind=kind, prior_size=stat.st_size)
            return
        data = native.read_bytes()
        self._append(
            path,
            root,
            kind=kind,
            prior_sha256=self._store.put_blob(data),
            prior_size=len(data),
            prior_mode=stat.st_mode & 0o7777,
        )

    def _append(
        self,
        path: str,
        root: str,
        *,
        kind: HostWriteKind,
        prior_sha256: str | None = None,
        prior_size: int = 0,
        prior_mode: int | None = None,
    ) -> None:
        from agent_runtime.execution.call_identity import (  # noqa: PLC0415
            RuntimeCallContext,
        )

        identity = RuntimeCallContext.current()
        self._sequence += 1
        self._store.append(
            HostWriteRecord(
                entry_id=uuid4().hex,
                org_id=self._org_id,
                conversation_id=self._conversation_id,
                run_id=self._run_id,
                tool_call_id=(
                    None if identity is None else identity.model_tool_call_id
                ),
                sequence=self._sequence,
                path=path,
                authorized_root=root,
                kind=kind,
                prior_sha256=prior_sha256,
                prior_size=prior_size,
                prior_mode=prior_mode,
                captured_at=datetime.now(timezone.utc),
            )
        )


class HostWriteRevertOutcome(RuntimeContract):
    """What one attempted restore actually did. Never raises past the service."""

    path: str
    kind: HostWriteKind
    status: str
    detail: str | None = None


class RevertStatus(StrEnum):
    """Per-path revert outcomes. Every selected path gets exactly one."""

    RESTORED = "restored"
    REMOVED = "removed"
    #: Captured, but the pre-image was never stored (too large / unreadable).
    NOT_REVERTIBLE = "not_revertible"
    #: The path is no longer inside the root that admitted the original write,
    #: or the target became a symlink. Refused rather than followed.
    REFUSED = "refused"
    FAILED = "failed"


class HostWriteReverter:
    """Puts captured pre-images back, bounded by the floor that admitted them.

    Selection semantics: for each affected PATH the OLDEST record in the
    selected set wins. Reverting a set of changes means "restore the state
    before that set began", so two writes to one file inside the selection
    collapse to one restore of the content that preceded both. Paths outside
    the selection are never touched — that is what makes reverting a single
    tool call leave a later unrelated write intact.
    """

    def __init__(self, store: HostWriteJournalPort) -> None:
        self._store = store

    def select(
        self,
        records: Iterable[HostWriteRecord],
        *,
        tool_call_id: str | None = None,
    ) -> tuple[HostWriteRecord, ...]:
        """The oldest record per path, narrowed to one tool call when asked."""

        oldest: dict[str, HostWriteRecord] = {}
        for record in records:
            if tool_call_id is not None and record.tool_call_id != tool_call_id:
                continue
            current = oldest.get(record.path)
            if current is None or record.sequence < current.sequence:
                oldest[record.path] = record
        return tuple(sorted(oldest.values(), key=lambda item: item.sequence))

    def revert(
        self, records: Sequence[HostWriteRecord]
    ) -> tuple[HostWriteRevertOutcome, ...]:
        """Restore each selected record, reporting one outcome per path."""

        return tuple(self._revert_one(record) for record in records)

    def _revert_one(self, record: HostWriteRecord) -> HostWriteRevertOutcome:
        if not path_within(record.path, record.authorized_root):
            # Only reachable through a tampered journal, and the one check that
            # keeps a revert from becoming a write primitive.
            return self._outcome(record, RevertStatus.REFUSED, "outside granted root")
        target = Path(record.path)
        if target.is_symlink():
            # A symlink introduced after capture would redirect these bytes to a
            # path nobody granted. Refuse rather than follow it.
            return self._outcome(record, RevertStatus.REFUSED, "target is a symlink")
        if not record.revertible:
            return self._outcome(
                record, RevertStatus.NOT_REVERTIBLE, "no captured prior content"
            )
        try:
            if record.kind is HostWriteKind.CREATED:
                target.unlink(missing_ok=True)
                return self._outcome(record, RevertStatus.REMOVED)
            self._restore(record, target)
        except (OSError, ValueError) as exc:
            return self._outcome(record, RevertStatus.FAILED, type(exc).__name__)
        return self._outcome(record, RevertStatus.RESTORED)

    def _restore(self, record: HostWriteRecord, target: Path) -> None:
        """Write the pre-image back atomically, digest-checked, mode preserved."""

        assert record.prior_sha256 is not None  # noqa: S101 - guarded by `revertible`
        data = self._store.get_blob(record.prior_sha256)
        if hashlib.sha256(data).hexdigest() != record.prior_sha256:
            raise ValueError("captured prior content failed its integrity check")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.revert-{record.entry_id[:8]}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        if record.prior_mode is not None:
            try:
                target.chmod(record.prior_mode)
            except OSError:
                # Windows and network mounts reject chmod; the bytes are the
                # contract, the permission bit is best effort.
                pass

    @staticmethod
    def _outcome(
        record: HostWriteRecord, status: RevertStatus, detail: str | None = None
    ) -> HostWriteRevertOutcome:
        return HostWriteRevertOutcome(
            path=record.path, kind=record.kind, status=status.value, detail=detail
        )


__all__ = (
    "MAX_CAPTURE_BYTES",
    "RETENTION_DAYS",
    "HostWriteJournal",
    "HostWriteJournalPort",
    "HostWriteKind",
    "HostWriteRecord",
    "HostWriteReverter",
    "HostWriteRevertOutcome",
    "RevertStatus",
    "path_within",
)
