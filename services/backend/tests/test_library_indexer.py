"""Library indexer worker tests — Phase 7.5 P7.5-A2.

Coverage:

* Claim-pattern semantics: one tick claims pending jobs, marks them
  ``indexing``, and processes them.
* Idempotency: an unchanged content_hash skips the LLM round-trip and
  marks the job indexed without inserting fresh embeddings.
* Retry: transient (``retryable=True``) errors push the job back to
  ``pending`` with attempts+1 and an exponential backoff.
* Hard failure: after ``max_attempts``, the job lands in ``failed``.
* Soft-delete cascade: a claimed job for a soft-deleted target drops
  its ``library_embeddings`` rows.
* Stuck-claim reset: a job whose ``claim_expires_at`` has passed is
  reset to ``pending`` at the start of the next tick.
* Tenant isolation: claims never leak across tenants.
* Re-embed on model_id change: an unchanged content_hash but a new
  ``model_id`` still triggers re-embedding (library-prd §6.5).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from backend_app.jobs.library_indexer import (
    EmbeddingClientError,
    LibraryIndexerLoop,
)
from backend_app.library.embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL_ID,
    InMemoryEmbeddingsStore,
)
from backend_app.library.index_jobs import InMemoryLibraryIndexJobsStore
from backend_app.library.store import (
    InMemoryLibraryStore,
    LibraryPageRecord,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeEmbeddingsClient:
    """In-memory embedding client. Returns a deterministic vector
    derived from each text so assertions can verify which texts were
    embedded."""

    def __init__(
        self,
        *,
        fail_with: Exception | None = None,
        capture: list[tuple[str, ...]] | None = None,
    ) -> None:
        self.fail_with = fail_with
        self.capture = capture if capture is not None else []

    async def embed(
        self,
        *,
        texts: list[str],
        model_id: str,
        tenant_id: str,
        target_kind: str,
        target_id: str,
    ) -> list[tuple[float, ...]]:
        if self.fail_with is not None:
            raise self.fail_with
        self.capture.append(tuple(texts))
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            seed = float(len(text)) / 100.0
            vectors.append(tuple([seed] * DEFAULT_EMBEDDING_DIMENSIONS))
        return vectors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_page(
    library: InMemoryLibraryStore,
    *,
    tenant_id: str,
    page_id: str = "libpage_test",
    markdown: str = "the quick brown fox jumps over the lazy dog",
) -> LibraryPageRecord:
    record = LibraryPageRecord(
        id=page_id,
        tenant_id=tenant_id,
        owner_user_id="usr_1",
        title="Notes",
        markdown=markdown,
        source={"kind": "user_upload"},
    )
    library.insert_page(record)
    return record


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaimAndProcess:
    def test_pending_job_processed_to_indexed(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()

        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        outcome = _run(loop.tick_once())
        assert outcome.claimed == 1
        assert outcome.indexed == 1
        assert outcome.failed == 0
        # Embeddings landed in the store.
        rows = embeddings.list_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id=page.id
        )
        assert len(rows) >= 1
        assert rows[0].model_id == DEFAULT_EMBEDDING_MODEL_ID
        # Job is terminal.
        job = next(iter(jobs.jobs.values()))
        assert job.status == "indexed"
        assert job.content_hash is not None
        assert job.model_id == DEFAULT_EMBEDDING_MODEL_ID

    def test_unchanged_content_skips_embed_call(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()

        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        _run(loop.tick_once())
        first_call_count = len(client.capture)

        # Re-enqueue the same job (e.g. user touched updated_at without
        # changing content) and tick again — the embed call must NOT
        # fire because the content_hash matches.
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)
        outcome = _run(loop.tick_once())
        assert outcome.skipped_unchanged == 1
        assert outcome.indexed == 0
        assert len(client.capture) == first_call_count

    def test_model_id_change_triggers_reembed_even_on_same_text(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()

        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        loop_v1 = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
            model_id="text-embedding-3-small",
        )
        _run(loop_v1.tick_once())
        rows_v1 = embeddings.list_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id=page.id
        )
        assert {row.model_id for row in rows_v1} == {"text-embedding-3-small"}

        # Re-enqueue and run with a different model_id.
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)
        loop_v2 = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
            model_id="text-embedding-3-large",
        )
        outcome = _run(loop_v2.tick_once())
        assert outcome.indexed == 1
        rows_v2 = embeddings.list_embeddings_for_target(
            tenant_id="org_a", target_kind="page", target_id=page.id
        )
        # The new model wrote its own rows; old model rows survive so
        # search remains available during migration.
        assert "text-embedding-3-large" in {r.model_id for r in rows_v2}


class TestRetryAndFailure:
    def test_transient_error_schedules_retry(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient(
            fail_with=EmbeddingClientError("upstream 503", retryable=True)
        )
        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
            max_attempts=3,
        )
        outcome = _run(loop.tick_once())
        assert outcome.retried == 1
        assert outcome.failed == 0
        job = next(iter(jobs.jobs.values()))
        assert job.status == "pending"
        assert job.attempts == 1
        assert job.last_error and "503" in job.last_error
        # next_run_at is pushed into the future.
        assert job.next_run_at > datetime.now(timezone.utc)

    def test_hard_failure_after_max_attempts(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient(
            fail_with=EmbeddingClientError("upstream 503", retryable=True)
        )
        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        # Pre-bump attempts to N-1 so this tick triggers the failure.
        job_id = next(iter(jobs.jobs))
        jobs.jobs[job_id] = jobs.jobs[job_id].model_copy(update={"attempts": 2})

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
            max_attempts=3,
        )
        outcome = _run(loop.tick_once())
        assert outcome.failed == 1
        job = jobs.jobs[job_id]
        assert job.status == "failed"
        assert job.last_error and "503" in job.last_error

    def test_non_retryable_error_marks_failed_immediately(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient(
            fail_with=EmbeddingClientError("400 bad model", retryable=False)
        )
        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        outcome = _run(loop.tick_once())
        assert outcome.failed == 1
        job = next(iter(jobs.jobs.values()))
        assert job.status == "failed"


class TestCascadeAndStuckClaims:
    def test_soft_delete_cascades_to_embeddings(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()

        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        # First tick: embed.
        _run(loop.tick_once())
        assert (
            len(
                embeddings.list_embeddings_for_target(
                    tenant_id="org_a", target_kind="page", target_id=page.id
                )
            )
            > 0
        )

        # Soft-delete the page and re-enqueue.
        library.soft_delete_page(tenant_id="org_a", page_id=page.id)
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)
        outcome = _run(loop.tick_once())
        assert outcome.indexed == 1
        # Embeddings were cascaded.
        assert (
            embeddings.list_embeddings_for_target(
                tenant_id="org_a", target_kind="page", target_id=page.id
            )
            == ()
        )

    def test_stuck_claim_is_reset_on_next_tick(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()
        page = _insert_page(library, tenant_id="org_a")
        jobs.enqueue(tenant_id="org_a", target_kind="page", target_id=page.id)
        # Simulate a crashed worker: flip job to indexing with an
        # already-expired claim_expires_at.
        job_id = next(iter(jobs.jobs))
        past = datetime.now(timezone.utc) - timedelta(seconds=600)
        jobs.jobs[job_id] = jobs.jobs[job_id].model_copy(
            update={"status": "indexing", "claim_expires_at": past}
        )

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        outcome = _run(loop.tick_once())
        # The reaper resets the stuck claim, then the tick claims +
        # processes it.
        assert outcome.indexed == 1
        assert jobs.jobs[job_id].status == "indexed"

    def test_target_missing_marks_failed(self) -> None:
        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()
        jobs.enqueue(
            tenant_id="org_a",
            target_kind="page",
            target_id="libpage_does_not_exist",
        )
        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        outcome = _run(loop.tick_once())
        assert outcome.failed == 1
        job = next(iter(jobs.jobs.values()))
        assert job.status == "failed"
        assert "target_missing" in (job.last_error or "")


class TestTenantIsolation:
    def test_other_tenant_jobs_are_not_processed_across_tenants(self) -> None:
        """Each job carries its own tenant — the worker uses it as the
        first filter on every library lookup. A job for tenant-a never
        reads a tenant-b row even if the page id collides."""

        library = InMemoryLibraryStore()
        jobs = InMemoryLibraryIndexJobsStore()
        embeddings = InMemoryEmbeddingsStore()
        client = FakeEmbeddingsClient()
        _insert_page(library, tenant_id="org_a", page_id="libpage_shared")
        # tenant-b has no page with this id; the lookup must return None.
        jobs.enqueue(tenant_id="org_b", target_kind="page", target_id="libpage_shared")

        loop = LibraryIndexerLoop(
            library_store=library,
            jobs_store=jobs,
            embeddings_store=embeddings,
            embeddings_client=client,
            interval_seconds=10.0,
            batch_limit=10,
        )
        outcome = _run(loop.tick_once())
        # Tenant-b's job hits ``target_missing`` and is marked failed.
        assert outcome.failed == 1
        # Tenant-a's page must not have any embeddings written.
        assert (
            embeddings.list_embeddings_for_target(
                tenant_id="org_a",
                target_kind="page",
                target_id="libpage_shared",
            )
            == ()
        )


class TestEnqueueIdempotency:
    def test_enqueue_collapses_inflight_jobs(self) -> None:
        jobs = InMemoryLibraryIndexJobsStore()
        first = jobs.enqueue(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        second = jobs.enqueue(
            tenant_id="org_a", target_kind="page", target_id="libpage_abc"
        )
        # Same job — re-enqueue is idempotent on the in-flight axis.
        assert first.id == second.id
        assert len(jobs.jobs) == 1
