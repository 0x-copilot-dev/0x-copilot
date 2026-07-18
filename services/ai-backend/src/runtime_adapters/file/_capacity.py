"""Capacity + admission control for the file-native runtime store.

Two desktop-only capacity controls, both **off by default** (unlimited / keep
forever) so the web / Postgres / in-memory backends and any store constructed
without explicit limits behave exactly as before:

* :class:`QuotaGuard` — a configurable byte-ceiling on the whole store root.
  A single guard call on the object-store write path admits or rejects an
  incoming blob *before* any bytes land, so a rejected write can never leave a
  partial or corrupt object behind. Rejection raises the typed, catchable
  :class:`FileStoreQuotaError` (``file_store_quota_exceeded``) rather than a
  bare ``OSError`` from a mid-write disk-full.
* :class:`FileStoreCleanupReport` — the tally returned by the store's
  age-based cleanup sweeper (``FileRuntimeApiStore.sweep_expired_conversations``),
  which reuses the existing physical-delete + object-GC path to reap
  conversations whose last activity predates the retention window.

The PRD's "Capacity and admission" and "Physical purge / retention sweep"
sections (``docs/plan/desktop/agent-capabilities/02-ac2-file-session-store.md``)
listed both as deferred; this module delivers the light desktop form of them.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from runtime_adapters.file._paths import FileStoreLayout


class FileStoreQuotaError(RuntimeError):
    """Raised when a write would grow the file store past its byte ceiling.

    Fail-closed and **not** retryable: the guard rejects the write before any
    bytes are written, so the store is never left with a partial blob. Unlike a
    transient disk-full, retrying is futile until data is deleted (see the
    retention sweeper), so callers should surface this as a capacity condition,
    not a corruption or a transient storage error.
    """

    code = "file_store_quota_exceeded"
    retryable = False

    def __init__(self, *, used_bytes: int, incoming_bytes: int, max_bytes: int) -> None:
        self.used_bytes = used_bytes
        self.incoming_bytes = incoming_bytes
        self.max_bytes = max_bytes
        super().__init__(
            "file store quota exceeded: writing "
            f"{incoming_bytes} byte(s) on top of {used_bytes} would exceed the "
            f"{max_bytes}-byte ceiling"
        )


class QuotaGuard:
    """Enforce a configurable byte-ceiling on one file-store root.

    A ceiling of ``0`` (the default) means *unlimited*: :meth:`admit` is a
    no-op and never walks the tree, so the guard costs nothing on the common
    path. When a positive ceiling is set, :meth:`admit` measures the store's
    current on-disk footprint and rejects any write that would push the total
    past the ceiling.
    """

    def __init__(
        self,
        layout: FileStoreLayout,
        *,
        max_bytes: int = 0,
        on_reject: Callable[[int], None] | None = None,
    ) -> None:
        self._layout = layout
        # Clamp negatives to 0 (unlimited); the settings layer also validates.
        self._max_bytes = max_bytes if max_bytes > 0 else 0
        # Best-effort observability hook fired *before* the reject is raised, so
        # a metric/log is emitted even when a caller swallows the exception.
        self._on_reject = on_reject

    @property
    def enabled(self) -> bool:
        """Whether a positive ceiling is configured (``False`` = unlimited)."""

        return self._max_bytes > 0

    @property
    def max_bytes(self) -> int:
        """The configured ceiling in bytes (``0`` = unlimited)."""

        return self._max_bytes

    def current_bytes(self) -> int:
        """Sum the sizes of every regular file under the store root.

        Symlinks are never followed and unreadable entries are skipped, so a
        transient race (a file removed mid-walk) can only *under*-count, which
        is the safe direction for admission.
        """

        root = self._layout.root
        if not root.exists():
            return 0
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def admit(self, incoming_bytes: int) -> None:
        """Admit a write of ``incoming_bytes`` or raise :class:`FileStoreQuotaError`.

        No-op when the guard is disabled or the write adds no bytes.
        """

        if not self.enabled or incoming_bytes <= 0:
            return
        used = self.current_bytes()
        if used + incoming_bytes > self._max_bytes:
            if self._on_reject is not None:
                try:
                    self._on_reject(incoming_bytes)
                except Exception:  # pragma: no cover - telemetry must not mask
                    pass
            raise FileStoreQuotaError(
                used_bytes=used,
                incoming_bytes=incoming_bytes,
                max_bytes=self._max_bytes,
            )


class FileStoreCleanupReport(BaseModel):
    """Tally of what an age-based cleanup sweep removed (or would remove).

    ``dry_run`` reports mirror what a live sweep *would* delete without touching
    disk. All counts are cumulative across every org swept in one pass.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    conversations_deleted: int = 0
    messages_deleted: int = 0
    runs_deleted: int = 0
    events_deleted: int = 0
    objects_collected: int = 0
    skipped_legal_hold: int = 0
    dry_run: bool = False

    def adding(
        self,
        *,
        conversations: int,
        messages: int,
        runs: int,
        events: int,
        objects: int,
        skipped_legal_hold: int,
    ) -> "FileStoreCleanupReport":
        """Return a copy with one purge outcome's tallies folded in."""

        return self.model_copy(
            update={
                "conversations_deleted": self.conversations_deleted + conversations,
                "messages_deleted": self.messages_deleted + messages,
                "runs_deleted": self.runs_deleted + runs,
                "events_deleted": self.events_deleted + events,
                "objects_collected": self.objects_collected + objects,
                "skipped_legal_hold": self.skipped_legal_hold + skipped_legal_hold,
            }
        )


__all__ = (
    "FileStoreCleanupReport",
    "FileStoreQuotaError",
    "QuotaGuard",
)
