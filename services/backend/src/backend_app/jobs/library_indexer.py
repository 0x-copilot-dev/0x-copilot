"""Library indexer worker — claim → extract → chunk → embed → insert.

Modelled on ``runtime_worker/jobs/retention_sweeper.py`` and the Phase
5 Routines scheduler (library-prd §6.2). Lives in the backend's own
process tree so the indexing queue stays alongside the data it owns.

**Boundary discipline.** The worker never imports an LLM provider SDK
directly — it goes through ai-backend's ``POST /internal/v1/llm/embed``
endpoint (P7.5-A1 in a sibling worktree owns that route). The static
guard ``tools/check_llm_provider_imports.py`` enforces the rule for
the whole ``services/backend`` tree.

**Idempotency.** Two layers:

1. **Skip when content is unchanged.** Each ``library_index_jobs`` row
   carries a ``content_hash`` + ``model_id`` set on the last successful
   index. If the hash matches the current extraction, we skip the
   embedding call and mark the job indexed again — model_id changes
   force a re-embed even on identical text.
2. **ON CONFLICT on the natural key.** The ``library_embeddings``
   UNIQUE constraint on ``(tenant, target_kind, target_id, ordinal,
   model_id)`` makes the bulk-insert safe under concurrent runs.

**Retry policy.** Up to ``max_attempts`` (default 3). Transient errors
(HTTP 5xx, network) bump ``attempts`` and push ``next_run_at`` out by
``base_backoff_seconds * 2^attempts`` capped at ``max_backoff_seconds``.
After ``max_attempts`` failures we flip to ``status='failed'`` and
emit an audit row (audit hook is best-effort; the indexer should not
deadlock on audit-store backpressure).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Protocol

import httpx

from backend_app.library.embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL_ID,
    EmbeddingsStore,
    build_embedding_rows,
    chunk_text,
    compute_content_hash,
    extract_text,
)
from backend_app.library.index_jobs import (
    IndexJobClaim,
    LibraryIndexJobsStore,
)
from backend_app.library.store import (
    LibraryItemRecord,
    LibraryStore,
)


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env-var keys + defaults (mirrors retention_sweeper's pattern)
# ---------------------------------------------------------------------------


class LibraryIndexerEnv:
    """Tunables for the library indexer loop."""

    INTERVAL_SECONDS = "LIBRARY_INDEXER_INTERVAL_SECONDS"
    BATCH_LIMIT = "LIBRARY_INDEXER_BATCH_LIMIT"
    CLAIM_TTL_SECONDS = "LIBRARY_INDEXER_CLAIM_TTL_SECONDS"
    MAX_ATTEMPTS = "LIBRARY_INDEXER_MAX_ATTEMPTS"
    EMBED_ENDPOINT_URL = "LIBRARY_INDEXER_EMBED_URL"
    ENABLED = "LIBRARY_INDEXER_ENABLED"

    DEFAULT_INTERVAL_SECONDS = 30.0
    DEFAULT_BATCH_LIMIT = 16
    DEFAULT_CLAIM_TTL_SECONDS = 300  # library-prd §6.2 — 5 minutes.
    DEFAULT_MAX_ATTEMPTS = 3
    DEFAULT_BASE_BACKOFF_SECONDS = 30.0
    DEFAULT_MAX_BACKOFF_SECONDS = 1800.0  # 30 minutes.

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    @classmethod
    def env_int(cls, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default


# ---------------------------------------------------------------------------
# Embeddings client — HTTP boundary to ai-backend
# ---------------------------------------------------------------------------


class EmbeddingClientError(Exception):
    """Generic embeddings call failure. Wraps the underlying httpx /
    response error so callers see one exception type."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingsClient(Protocol):
    """Async embed contract.

    Production wiring instantiates :class:`HttpEmbeddingsClient`; tests
    inject a fake. The contract is small on purpose — the worker
    never needs to know which provider serves the request.
    """

    async def embed(
        self,
        *,
        texts: list[str],
        model_id: str,
        tenant_id: str,
        target_kind: str,
        target_id: str,
    ) -> list[tuple[float, ...]]: ...


@dataclass
class HttpEmbeddingsClient:
    """HTTP client for ``POST /internal/v1/llm/embed`` on ai-backend.

    The endpoint contract (P7.5-A1) is:

    .. code-block:: json

        {
          "purpose": "library_indexing",
          "tenant_id": "...",
          "model_id": "text-embedding-3-small",
          "texts": ["...", "..."],
          "target": {"kind": "page", "id": "libpage_..."}
        }

    and returns ``{"vectors": [[..1536..], ...], "model_id": "..."}``.

    We pass ``texts`` in a single call; ai-backend batches under the
    hood. The retryable flag on errors is the worker's signal to retry.
    """

    base_url: str
    service_token: str | None = None
    http_client: httpx.AsyncClient | None = None
    timeout_seconds: float = 60.0

    async def embed(
        self,
        *,
        texts: list[str],
        model_id: str,
        tenant_id: str,
        target_kind: str,
        target_id: str,
    ) -> list[tuple[float, ...]]:
        if not texts:
            return []
        client = self.http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds)
        )
        owns_client = self.http_client is None
        headers = {"Content-Type": "application/json"}
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
            # Internal-call identity headers per backend CLAUDE.md.
            headers["x-enterprise-org-id"] = tenant_id
        body = {
            "purpose": "library_indexing",
            "tenant_id": tenant_id,
            "model_id": model_id,
            "texts": texts,
            "target": {"kind": target_kind, "id": target_id},
        }
        url = self.base_url.rstrip("/") + "/internal/v1/llm/embed"
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            if owns_client:
                await client.aclose()
            raise EmbeddingClientError(
                f"embed request failed: {exc!r}", retryable=True
            ) from exc
        try:
            if response.status_code >= 500:
                raise EmbeddingClientError(
                    f"embed 5xx: {response.status_code}", retryable=True
                )
            if response.status_code >= 400:
                raise EmbeddingClientError(
                    f"embed 4xx: {response.status_code} {response.text[:200]}",
                    retryable=False,
                )
            payload = response.json()
        finally:
            if owns_client:
                await client.aclose()

        vectors = payload.get("vectors") if isinstance(payload, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingClientError(
                "embed response: vectors missing or count mismatch", retryable=False
            )
        out: list[tuple[float, ...]] = []
        for vec in vectors:
            if not isinstance(vec, list):
                raise EmbeddingClientError(
                    "embed response: vector not a list", retryable=False
                )
            if len(vec) != DEFAULT_EMBEDDING_DIMENSIONS:
                # Model misconfiguration on the ai-backend side. Non-retryable.
                raise EmbeddingClientError(
                    f"embed response: dim={len(vec)} != {DEFAULT_EMBEDDING_DIMENSIONS}",
                    retryable=False,
                )
            out.append(tuple(float(v) for v in vec))
        return out


# ---------------------------------------------------------------------------
# Outcome dataclasses (small + frozen so the loop is observable in tests)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexerTickOutcome:
    """What one ``tick_once`` did."""

    claimed: int
    indexed: int
    skipped_unchanged: int
    retried: int
    failed: int


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


class LibraryIndexerLoop:
    """Periodic claim → embed → insert loop.

    Construction takes ports for every external boundary so tests can
    swap any of them. The loop's responsibility is the orchestration;
    each port is small and testable on its own.
    """

    def __init__(
        self,
        *,
        library_store: LibraryStore,
        jobs_store: LibraryIndexJobsStore,
        embeddings_store: EmbeddingsStore,
        embeddings_client: EmbeddingsClient,
        interval_seconds: float | None = None,
        batch_limit: int | None = None,
        claim_ttl_seconds: int | None = None,
        max_attempts: int | None = None,
        base_backoff_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
        model_id: str = DEFAULT_EMBEDDING_MODEL_ID,
        clock: Callable[[], datetime] | None = None,
        blob_loader: Callable[
            [LibraryItemRecord], Awaitable[tuple[bytes | None, str | None]]
        ]
        | None = None,
    ) -> None:
        self._library = library_store
        self._jobs = jobs_store
        self._embeddings = embeddings_store
        self._client = embeddings_client
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else LibraryIndexerEnv.env_float(
                LibraryIndexerEnv.INTERVAL_SECONDS,
                LibraryIndexerEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._batch_limit = (
            batch_limit
            if batch_limit is not None
            else LibraryIndexerEnv.env_int(
                LibraryIndexerEnv.BATCH_LIMIT,
                LibraryIndexerEnv.DEFAULT_BATCH_LIMIT,
            )
        )
        self._claim_ttl = (
            claim_ttl_seconds
            if claim_ttl_seconds is not None
            else LibraryIndexerEnv.env_int(
                LibraryIndexerEnv.CLAIM_TTL_SECONDS,
                LibraryIndexerEnv.DEFAULT_CLAIM_TTL_SECONDS,
            )
        )
        self._max_attempts = (
            max_attempts
            if max_attempts is not None
            else LibraryIndexerEnv.env_int(
                LibraryIndexerEnv.MAX_ATTEMPTS,
                LibraryIndexerEnv.DEFAULT_MAX_ATTEMPTS,
            )
        )
        self._base_backoff = (
            base_backoff_seconds
            if base_backoff_seconds is not None
            else LibraryIndexerEnv.DEFAULT_BASE_BACKOFF_SECONDS
        )
        self._max_backoff = (
            max_backoff_seconds
            if max_backoff_seconds is not None
            else LibraryIndexerEnv.DEFAULT_MAX_BACKOFF_SECONDS
        )
        self._model_id = model_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._blob_loader = blob_loader or _no_blob_loader
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="library-indexer-loop")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover — defensive
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            try:
                await self.tick_once()
            except Exception:
                _LOGGER.warning("library_indexer.tick_failed", exc_info=True)

    async def tick_once(self) -> IndexerTickOutcome:
        """One pass: reset stuck claims, then claim + process a batch."""

        now = self._clock()
        self._jobs.reset_stuck_claims(now=now)
        claims = self._jobs.claim_pending(
            now=now,
            claim_ttl_seconds=self._claim_ttl,
            limit=self._batch_limit,
        )
        indexed = 0
        skipped = 0
        retried = 0
        failed = 0
        for claim in claims:
            outcome = await self._process_claim(claim)
            if outcome == _Outcome.INDEXED:
                indexed += 1
            elif outcome == _Outcome.SKIPPED_UNCHANGED:
                skipped += 1
            elif outcome == _Outcome.RETRIED:
                retried += 1
            elif outcome == _Outcome.FAILED:
                failed += 1
        return IndexerTickOutcome(
            claimed=len(claims),
            indexed=indexed,
            skipped_unchanged=skipped,
            retried=retried,
            failed=failed,
        )

    # ------------------------------------------------------------------ claim

    async def _process_claim(self, claim: IndexJobClaim) -> "_Outcome":
        record = self._lookup_record(claim)
        if record is None:
            # Target was hard-deleted between enqueue + claim. Mark as
            # failed so we don't tie up the queue; the row is the
            # "nothing to do" terminal state.
            self._jobs.mark_failed(job_id=claim.job_id, error="target_missing")
            return _Outcome.FAILED

        if record.deleted_at is not None:
            # Soft-deleted: drop any existing embeddings (cascade) and
            # mark indexed with empty content.
            self._embeddings.delete_embeddings_for_target(
                tenant_id=record.tenant_id,
                target_kind=claim.target_kind,
                target_id=record.id,
            )
            self._jobs.mark_indexed(
                job_id=claim.job_id,
                content_hash=compute_content_hash(""),
                model_id=self._model_id,
            )
            return _Outcome.INDEXED

        try:
            blob, mime = await self._blob_loader(record)
        except Exception as exc:  # pragma: no cover — defensive
            return self._schedule_retry(claim, error=f"blob_loader_failed: {exc!r}")

        try:
            text = extract_text(record=record, blob=blob, mime=mime)
        except Exception as exc:
            # Extraction failures (bad upload, decoder crash) are not
            # retryable — bytes won't change between attempts.
            self._jobs.mark_failed(
                job_id=claim.job_id, error=f"extract_failed: {exc!r}"
            )
            return _Outcome.FAILED

        content_hash = compute_content_hash(text)
        # Idempotency: if the same hash + model_id was already embedded,
        # skip the LLM round-trip. Re-embed on model_id change even with
        # identical text (library-prd §6.5).
        if (
            claim.content_hash is not None
            and claim.content_hash == content_hash
            and claim.model_id == self._model_id
        ):
            self._jobs.mark_indexed(
                job_id=claim.job_id,
                content_hash=content_hash,
                model_id=self._model_id,
            )
            return _Outcome.SKIPPED_UNCHANGED

        chunks = chunk_text(text)
        if not chunks:
            # Nothing to embed — drop any prior chunks and mark indexed.
            self._embeddings.delete_embeddings_for_target(
                tenant_id=record.tenant_id,
                target_kind=claim.target_kind,
                target_id=record.id,
            )
            self._jobs.mark_indexed(
                job_id=claim.job_id,
                content_hash=content_hash,
                model_id=self._model_id,
            )
            return _Outcome.INDEXED

        try:
            vectors = await self._client.embed(
                texts=[chunk.text for chunk in chunks],
                model_id=self._model_id,
                tenant_id=record.tenant_id,
                target_kind=claim.target_kind,
                target_id=record.id,
            )
        except EmbeddingClientError as exc:
            if exc.retryable:
                return self._schedule_retry(claim, error=str(exc))
            self._jobs.mark_failed(job_id=claim.job_id, error=str(exc))
            return _Outcome.FAILED
        except Exception as exc:  # pragma: no cover — defensive
            return self._schedule_retry(claim, error=f"embed_failed: {exc!r}")

        rows = build_embedding_rows(
            tenant_id=record.tenant_id,
            target_kind=claim.target_kind,
            target_id=record.id,
            chunks=chunks,
            vectors=vectors,
            model_id=self._model_id,
        )
        # On model_id change we delete the old chunks for this model_id
        # to avoid orphaned vectors from prior attempts of the same
        # ordinal. Other-model rows remain so search can serve both
        # during migration (library-prd §6.5).
        self._embeddings.delete_embeddings_for_target(
            tenant_id=record.tenant_id,
            target_kind=claim.target_kind,
            target_id=record.id,
            model_id=self._model_id,
        )
        self._embeddings.insert_embeddings(rows)
        self._jobs.mark_indexed(
            job_id=claim.job_id,
            content_hash=content_hash,
            model_id=self._model_id,
        )
        return _Outcome.INDEXED

    # ------------------------------------------------------------------ helpers

    def _lookup_record(self, claim: IndexJobClaim) -> LibraryItemRecord | None:
        if claim.target_kind == "file":
            return self._library.get_file(
                tenant_id=claim.tenant_id,
                file_id=claim.target_id,
                include_deleted=True,
            )
        if claim.target_kind == "page":
            return self._library.get_page(
                tenant_id=claim.tenant_id,
                page_id=claim.target_id,
                include_deleted=True,
            )
        if claim.target_kind == "dataset":
            return self._library.get_dataset(
                tenant_id=claim.tenant_id,
                dataset_id=claim.target_id,
                include_deleted=True,
            )
        return None  # pragma: no cover — type-system already covers this

    def _schedule_retry(self, claim: IndexJobClaim, *, error: str) -> "_Outcome":
        next_attempts = claim.attempts + 1
        if next_attempts >= claim.max_attempts:
            self._jobs.mark_failed(job_id=claim.job_id, error=error)
            return _Outcome.FAILED
        backoff = min(
            self._max_backoff,
            self._base_backoff * (2**claim.attempts),
        )
        next_run_at = self._clock() + timedelta(seconds=backoff)
        self._jobs.mark_retry(job_id=claim.job_id, error=error, next_run_at=next_run_at)
        return _Outcome.RETRIED


# ---------------------------------------------------------------------------
# Private outcome sentinel (kept module-local; tests assert via the public
# IndexerTickOutcome counters)
# ---------------------------------------------------------------------------


class _Outcome:
    INDEXED = "indexed"
    SKIPPED_UNCHANGED = "skipped_unchanged"
    RETRIED = "retried"
    FAILED = "failed"


async def _no_blob_loader(
    _record: LibraryItemRecord,
) -> tuple[bytes | None, str | None]:
    """Default loader — never fetches bytes.

    Pages and datasets don't need a blob (extraction reads from the
    metadata row). Files fall back to name+tag indexing without a
    loader (per library-prd §6.4). Production wiring replaces this
    with an object-store-backed loader that streams bytes for the
    PDF / text / office mimes.
    """

    return None, None


__all__ = [
    "EmbeddingClientError",
    "EmbeddingsClient",
    "HttpEmbeddingsClient",
    "IndexerTickOutcome",
    "LibraryIndexerEnv",
    "LibraryIndexerLoop",
]
