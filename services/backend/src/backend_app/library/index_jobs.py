"""Library index-jobs queue — claim-pattern records + in-memory adapter.

Mirrors the Routines scheduler's claim shape (cross-audit §3.5 +
routines-prd §3.7) at backend scope:

* ``status='pending'`` is the work queue. The worker claims a batch
  with ``FOR UPDATE SKIP LOCKED`` and sets ``status='indexing'`` +
  ``claim_expires_at = now() + CLAIM_TTL_SECONDS``.
* On success: ``status='indexed'``, ``content_hash`` + ``model_id``
  recorded for the "did the indexable text change?" gate.
* On retryable failure: ``attempts += 1``, ``next_run_at`` pushed out
  by exponential backoff.
* On hard failure (``attempts >= max_attempts``): ``status='failed'``.

The in-memory adapter is the dev / test default; the Postgres adapter
ships when the deployment composer wires it. Both expose the same
:class:`LibraryIndexJobsStore` Protocol.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _job_id() -> str:
    return f"libidx_{uuid4().hex}"


IndexJobStatusLiteral = Literal["pending", "indexing", "indexed", "failed"]
IndexJobTargetKindLiteral = Literal["file", "page", "dataset"]


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


class LibraryIndexJobRecord(BaseModel):
    """One row in ``library_index_jobs``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_job_id)
    tenant_id: str
    target_kind: IndexJobTargetKindLiteral
    target_id: str
    status: IndexJobStatusLiteral = "pending"
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    content_hash: str | None = None
    model_id: str | None = None
    claim_expires_at: datetime | None = None
    next_run_at: datetime = Field(default_factory=_now)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


@dataclass(frozen=True)
class IndexJobClaim:
    """One claimed job handed to the worker."""

    job_id: str
    tenant_id: str
    target_kind: IndexJobTargetKindLiteral
    target_id: str
    attempts: int
    max_attempts: int
    content_hash: str | None
    model_id: str | None


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


class LibraryIndexJobsStore(Protocol):
    """Adapter contract for the in-flight indexing queue."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def enqueue(
        self,
        *,
        tenant_id: str,
        target_kind: IndexJobTargetKindLiteral,
        target_id: str,
    ) -> LibraryIndexJobRecord:
        """Insert or re-arm a pending job.

        Idempotent: at most one in-flight (``pending``/``indexing``)
        job per ``(tenant_id, target_kind, target_id)`` — re-enqueue
        from the service layer either updates the existing pending row's
        ``next_run_at`` to ``now()`` or returns the in-flight row
        unchanged.
        """

    def claim_pending(
        self,
        *,
        now: datetime,
        claim_ttl_seconds: int,
        limit: int,
    ) -> tuple[IndexJobClaim, ...]:
        """Claim up to ``limit`` due pending jobs atomically."""

    def mark_indexed(
        self,
        *,
        job_id: str,
        content_hash: str,
        model_id: str,
    ) -> None: ...

    def mark_failed(
        self,
        *,
        job_id: str,
        error: str,
    ) -> None: ...

    def mark_retry(
        self,
        *,
        job_id: str,
        error: str,
        next_run_at: datetime,
    ) -> None: ...

    def reset_stuck_claims(self, *, now: datetime) -> int:
        """Reset jobs whose ``claim_expires_at`` has passed.

        Called by the scheduler before each tick — a crashed worker
        leaves its claim in ``indexing`` with an expired claim_expires_at,
        and the reaper flips it back to ``pending`` so a peer can pick
        it up.
        """

    def get(self, *, job_id: str) -> LibraryIndexJobRecord | None: ...


# ---------------------------------------------------------------------------
# In-memory adapter
# ---------------------------------------------------------------------------


@dataclass
class InMemoryLibraryIndexJobsStore:
    """Single-process queue used in tests + dev wiring."""

    jobs: dict[str, LibraryIndexJobRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def enqueue(
        self,
        *,
        tenant_id: str,
        target_kind: IndexJobTargetKindLiteral,
        target_id: str,
    ) -> LibraryIndexJobRecord:
        # Coalesce: if an in-flight job exists for the same target,
        # just pull its ``next_run_at`` forward and return it. The
        # Postgres adapter does the same via the partial UNIQUE index.
        latest_terminal: LibraryIndexJobRecord | None = None
        for record in self.jobs.values():
            if (
                record.tenant_id != tenant_id
                or record.target_kind != target_kind
                or record.target_id != target_id
            ):
                continue
            if record.status in ("pending", "indexing"):
                if record.status == "pending":
                    self.jobs[record.id] = record.model_copy(
                        update={
                            "next_run_at": _now(),
                            "updated_at": _now(),
                        }
                    )
                return self.jobs[record.id]
            # Track the most recent terminal job so the new pending
            # row can inherit its content_hash + model_id; that is the
            # signal the indexer uses to skip re-embedding on unchanged
            # content.
            if (
                latest_terminal is None
                or record.updated_at > latest_terminal.updated_at
            ):
                latest_terminal = record

        record = LibraryIndexJobRecord(
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_id=target_id,
            content_hash=(latest_terminal.content_hash if latest_terminal else None),
            model_id=(latest_terminal.model_id if latest_terminal else None),
        )
        self.jobs[record.id] = record
        return record

    def claim_pending(
        self,
        *,
        now: datetime,
        claim_ttl_seconds: int,
        limit: int,
    ) -> tuple[IndexJobClaim, ...]:
        # Order by next_run_at ASC (oldest-due first); cap at ``limit``.
        candidates = sorted(
            (
                record
                for record in self.jobs.values()
                if record.status == "pending" and record.next_run_at <= now
            ),
            key=lambda r: (r.next_run_at, r.id),
        )
        claimed: list[IndexJobClaim] = []
        for record in candidates[:limit]:
            updated = record.model_copy(
                update={
                    "status": "indexing",
                    "claim_expires_at": now + timedelta(seconds=claim_ttl_seconds),
                    "updated_at": now,
                }
            )
            self.jobs[record.id] = updated
            claimed.append(
                IndexJobClaim(
                    job_id=updated.id,
                    tenant_id=updated.tenant_id,
                    target_kind=updated.target_kind,
                    target_id=updated.target_id,
                    attempts=updated.attempts,
                    max_attempts=updated.max_attempts,
                    content_hash=updated.content_hash,
                    model_id=updated.model_id,
                )
            )
        return tuple(claimed)

    def mark_indexed(
        self,
        *,
        job_id: str,
        content_hash: str,
        model_id: str,
    ) -> None:
        record = self.jobs.get(job_id)
        if record is None:
            return
        self.jobs[job_id] = record.model_copy(
            update={
                "status": "indexed",
                "content_hash": content_hash,
                "model_id": model_id,
                "claim_expires_at": None,
                "last_error": None,
                "updated_at": _now(),
            }
        )

    def mark_failed(self, *, job_id: str, error: str) -> None:
        record = self.jobs.get(job_id)
        if record is None:
            return
        self.jobs[job_id] = record.model_copy(
            update={
                "status": "failed",
                "attempts": record.attempts + 1,
                "last_error": error,
                "claim_expires_at": None,
                "updated_at": _now(),
            }
        )

    def mark_retry(
        self,
        *,
        job_id: str,
        error: str,
        next_run_at: datetime,
    ) -> None:
        record = self.jobs.get(job_id)
        if record is None:
            return
        self.jobs[job_id] = record.model_copy(
            update={
                "status": "pending",
                "attempts": record.attempts + 1,
                "last_error": error,
                "claim_expires_at": None,
                "next_run_at": next_run_at,
                "updated_at": _now(),
            }
        )

    def reset_stuck_claims(self, *, now: datetime) -> int:
        reset = 0
        for record in list(self.jobs.values()):
            if (
                record.status == "indexing"
                and record.claim_expires_at is not None
                and record.claim_expires_at <= now
            ):
                self.jobs[record.id] = record.model_copy(
                    update={
                        "status": "pending",
                        "claim_expires_at": None,
                        "next_run_at": now,
                        "updated_at": now,
                    }
                )
                reset += 1
        return reset

    def get(self, *, job_id: str) -> LibraryIndexJobRecord | None:
        return self.jobs.get(job_id)


__all__ = [
    "InMemoryLibraryIndexJobsStore",
    "IndexJobClaim",
    "IndexJobStatusLiteral",
    "IndexJobTargetKindLiteral",
    "LibraryIndexJobRecord",
    "LibraryIndexJobsStore",
]
