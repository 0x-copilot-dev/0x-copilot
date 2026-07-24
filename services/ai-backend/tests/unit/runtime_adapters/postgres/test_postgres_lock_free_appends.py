"""Lock-free ``append_event`` path tests.

Two test surfaces:

- **Integration** (skipped when ``TEST_DATABASE_URL`` is unset): exercise the
  real Postgres adapter and verify monotonic sequence allocation, idempotent
  retry on cancel-mid-stream race, and that ``set_run_latest_sequence``
  never rewinds under the consolidated-write path.

- **Unit** (no DB required): exercise the pure retry-loop semantics —
  ``_is_event_sequence_conflict`` discriminates by constraint name,
  ``_retry_backoff`` honors the delay cap, and ``append_event`` raises
  :class:`RuntimeEventSequenceConflict` after the retry budget is exhausted.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.persistence.ports import RuntimeEventSequenceConflict
from psycopg import errors as psycopg_errors
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_adapters.postgres.runtime_api_store import _AppendEventRetry
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
    RuntimeRequestContext,
)


# ``psycopg.errors.UniqueViolation.diag`` is a read-only property that
# delegates to the underlying ``psycopg.pq.PGresult``. Synthesizing one in
# Python without a real result is impossible, so for tests of the
# constraint-name discrimination we subclass and override the property.
@dataclass(frozen=True)
class _FakeDiag:
    constraint_name: str | None
    table_name: str = "runtime_events"


class _FakeUniqueViolation(psycopg_errors.UniqueViolation):
    """Pure-Python UniqueViolation with an injectable ``diag``.

    The production retry path calls ``exc.diag.constraint_name`` to decide
    whether the exception is a per-run sequence race. Real psycopg
    exceptions can only be constructed by the libpq protocol layer, so the
    test path subclasses and stubs the property.
    """

    def __init__(self, *, constraint_name: str | None) -> None:
        super().__init__(constraint_name or "simulated")
        self._fake_diag = _FakeDiag(constraint_name=constraint_name)

    @property
    def diag(self) -> _FakeDiag:  # type: ignore[override]
        return self._fake_diag


# --------------------------------------------------------------------------
# Integration fixtures (real Postgres)
# --------------------------------------------------------------------------


_REQUIRES_DB = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgresRuntimeApiStore tests.",
)


@pytest.fixture
async def postgres_store() -> AsyncIterator[PostgresRuntimeApiStore]:
    store = PostgresRuntimeApiStore(
        os.environ["TEST_DATABASE_URL"],
        pool_min_size=2,
        pool_max_size=20,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    try:
        await store.migrate()
        yield store
    finally:
        await store.close()


def _request_context() -> RuntimeRequestContext:
    return RuntimeRequestContext(roles=("Admin",), permission_scopes=("Search:Read",))


def _make_runtime_context(suffix: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=f"user_{suffix}",
        org_id=f"org_{suffix}",
        roles=("Admin",),
        model_profile={
            "provider": "openai",
            "model_name": "gpt-5.4-mini",
            "max_input_tokens": 128000,
            "timeout_seconds": 30,
            "temperature": 0,
            "supports_streaming": True,
        },
        run_id=f"run_{suffix}",
        trace_id=f"trace_{suffix}",
    )


async def _seed_run(
    store: PostgresRuntimeApiStore, suffix: str | None = None
) -> tuple[str, str, str, str]:
    """Same shape as the sibling test file's helper — duplicated to keep this
    test module independent of import order on the shared conftest.
    """

    suffix = suffix or uuid4().hex
    org_id = f"org_{suffix}"
    user_id = f"user_{suffix}"
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=org_id,
            user_id=user_id,
            assistant_id=f"assistant_{suffix}",
        )
    )
    client_request = CreateRunRequest(
        conversation_id=conversation.conversation_id,
        org_id=org_id,
        user_id=user_id,
        user_input="hello",
        model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        request_context=_request_context(),
    )
    request = client_request.model_copy(
        update={"runtime_context": _make_runtime_context(suffix)}
    )
    run, _msg, _created = await store.create_run_with_user_message(
        request=request, conversation=conversation
    )
    return org_id, user_id, run.run_id, conversation.conversation_id


def _draft(
    *,
    run_id: str,
    conv_id: str,
    org_id: str,
    payload_index: int,
) -> RuntimeEventDraft:
    return RuntimeEventDraft(
        run_id=run_id,
        conversation_id=conv_id,
        org_id=org_id,
        trace_id="trace",
        source=StreamEventSource.RUNTIME,
        event_type=RuntimeApiEventType.PROGRESS,
        payload={"i": payload_index},
    )


# --------------------------------------------------------------------------
# Integration tests
# --------------------------------------------------------------------------


@_REQUIRES_DB
class TestLockFreeAppendIntegration:
    """Real Postgres — exercise the lock-free path end to end."""

    async def test_sequential_appends(
        self, postgres_store: PostgresRuntimeApiStore
    ) -> None:
        """Sequential appends produce 1..N with no gaps."""

        org_id, _user_id, run_id, conv_id = await _seed_run(postgres_store)
        sequences = []
        for i in range(20):
            envelope = await postgres_store.append_event(
                _draft(run_id=run_id, conv_id=conv_id, org_id=org_id, payload_index=i)
            )
            sequences.append(envelope.sequence_no)
        assert sequences == list(range(1, 21))
        latest = await postgres_store.get_latest_sequence(run_id=run_id)
        assert latest == 20

    async def test_concurrent_appends_keep_monotonic_sequence(
        self, postgres_store: PostgresRuntimeApiStore
    ) -> None:
        """100 concurrent appends to the same run produce 1..100 — no gaps, no
        duplicates. The UNIQUE index + retry loop replace any row lock.
        """

        org_id, _user_id, run_id, conv_id = await _seed_run(postgres_store)

        async def append(i: int) -> int:
            envelope = await postgres_store.append_event(
                _draft(run_id=run_id, conv_id=conv_id, org_id=org_id, payload_index=i)
            )
            return envelope.sequence_no

        results = await asyncio.gather(*[append(i) for i in range(100)])
        assert sorted(results) == list(range(1, 101))
        assert len(set(results)) == 100
        latest = await postgres_store.get_latest_sequence(run_id=run_id)
        assert latest == 100

    async def test_cursor_never_rewinds_under_retry(
        self, postgres_store: PostgresRuntimeApiStore
    ) -> None:
        """H3 monotonic guard prevents ``agent_runs.latest_sequence_no`` from
        rewinding even when a retry races a concurrent peer.
        """

        org_id, _, run_id, conv_id = await _seed_run(postgres_store)

        async def append(i: int) -> int:
            envelope = await postgres_store.append_event(
                _draft(run_id=run_id, conv_id=conv_id, org_id=org_id, payload_index=i)
            )
            return envelope.sequence_no

        results = await asyncio.gather(*[append(i) for i in range(30)])
        assert sorted(results) == list(range(1, 31))
        latest = await postgres_store.get_latest_sequence(run_id=run_id)
        assert latest == 30

    async def test_stable_event_retry_returns_one_postgres_row(
        self, postgres_store: PostgresRuntimeApiStore
    ) -> None:
        org_id, _, run_id, conv_id = await _seed_run(postgres_store)
        draft = _draft(
            run_id=run_id,
            conv_id=conv_id,
            org_id=org_id,
            payload_index=1,
        ).model_copy(update={"event_id": f"artevt_{'a' * 64}"})

        first = await postgres_store.append_event(draft)
        retried = await postgres_store.append_event(draft)

        assert retried == first
        assert await postgres_store.get_latest_sequence(run_id=run_id) == 1


# --------------------------------------------------------------------------
# Unit tests for the retry-loop helpers (no DB)
# --------------------------------------------------------------------------


class TestIsEventSequenceConflict:
    """Discrimination on ``UniqueViolation.diag.constraint_name``.

    The retry loop must fire ONLY when the failing index is
    ``idx_runtime_events_run_sequence``. Any other unique violation
    propagates unmodified.
    """

    @staticmethod
    def _unique_violation(
        constraint_name: str | None,
    ) -> psycopg_errors.UniqueViolation:
        return _FakeUniqueViolation(constraint_name=constraint_name)

    def test_matches_sequence_index(self) -> None:
        exc = self._unique_violation(_AppendEventRetry.SEQUENCE_INDEX)
        assert PostgresRuntimeApiStore._is_event_sequence_conflict(exc) is True

    def test_other_constraint_does_not_match(self) -> None:
        exc = self._unique_violation("idx_agent_conversations_idempotency")
        assert PostgresRuntimeApiStore._is_event_sequence_conflict(exc) is False

    def test_missing_constraint_name_does_not_match(self) -> None:
        exc = self._unique_violation(None)
        assert PostgresRuntimeApiStore._is_event_sequence_conflict(exc) is False

    def test_stable_event_primary_key_matches_only_with_assigned_id(self) -> None:
        exc = self._unique_violation(_AppendEventRetry.EVENT_ID_CONSTRAINT)
        stable = TestRetryLoopBehavior._draft().model_copy(
            update={"event_id": f"artevt_{'b' * 64}"}
        )
        ordinary = TestRetryLoopBehavior._draft()

        assert (
            PostgresRuntimeApiStore._is_stable_event_id_conflict(
                exc,
                event=stable,
            )
            is True
        )
        assert (
            PostgresRuntimeApiStore._is_stable_event_id_conflict(
                exc,
                event=ordinary,
            )
            is False
        )


class TestRetryBackoff:
    """``_retry_backoff`` must stay bounded under the MAX_DELAY ceiling."""

    def test_first_attempt_under_max_delay(self) -> None:
        for _ in range(100):
            delay = PostgresRuntimeApiStore._retry_backoff(attempt=0)
            assert 0.0 <= delay <= _AppendEventRetry.MAX_DELAY_SECONDS

    def test_high_attempt_capped_at_max_delay(self) -> None:
        # Attempt 10 would otherwise compute base * 2**10 = ~5s; the cap
        # forces it to MAX_DELAY_SECONDS.
        for _ in range(100):
            delay = PostgresRuntimeApiStore._retry_backoff(attempt=10)
            assert 0.0 <= delay <= _AppendEventRetry.MAX_DELAY_SECONDS

    def test_jitter_produces_varied_delays(self) -> None:
        samples = {PostgresRuntimeApiStore._retry_backoff(attempt=3) for _ in range(50)}
        # 50 jittered samples should produce more than one distinct value.
        assert len(samples) > 1


class TestRetryLoopBehavior:
    """Drives the retry loop with a mocked ``_append_event_once`` so we can
    exhaust the budget without a real DB.
    """

    @staticmethod
    def _store() -> PostgresRuntimeApiStore:
        # Construct without opening the pool — we never call methods that
        # touch the DB; we patch ``_append_event_once`` directly.
        return PostgresRuntimeApiStore("postgresql://unused:unused@127.0.0.1/unused")

    @staticmethod
    def _draft() -> RuntimeEventDraft:
        return RuntimeEventDraft(
            run_id="run_x",
            conversation_id="conv_x",
            org_id="org_x",
            trace_id="trace_x",
            source=StreamEventSource.RUNTIME,
            event_type=RuntimeApiEventType.PROGRESS,
            payload={"k": "v"},
        )

    @staticmethod
    def _sequence_conflict() -> psycopg_errors.UniqueViolation:
        return _FakeUniqueViolation(constraint_name=_AppendEventRetry.SEQUENCE_INDEX)

    @staticmethod
    def _other_violation() -> psycopg_errors.UniqueViolation:
        return _FakeUniqueViolation(constraint_name="idx_other_table")

    async def test_succeeds_after_one_conflict(self, monkeypatch) -> None:
        store = self._store()
        sentinel = MagicMock(name="envelope")
        attempts = {"n": 0}

        async def fake_once(event: RuntimeEventDraft):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise self._sequence_conflict()
            return sentinel

        # No real sleeping — keep the test instant.
        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(store, "_append_event_once", fake_once)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        result = await store.append_event(self._draft())
        assert result is sentinel
        assert attempts["n"] == 2

        # No DB so we don't need to close; the store never opened a pool.
        with suppress(Exception):
            await store.close()

    async def test_raises_runtime_event_sequence_conflict_after_budget(
        self, monkeypatch
    ) -> None:
        store = self._store()
        call_count = {"n": 0}

        async def fake_once(event: RuntimeEventDraft):
            call_count["n"] += 1
            raise self._sequence_conflict()

        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(store, "_append_event_once", fake_once)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        with pytest.raises(RuntimeEventSequenceConflict) as excinfo:
            await store.append_event(self._draft())
        assert excinfo.value.run_id == "run_x"
        assert excinfo.value.attempts == _AppendEventRetry.MAX_ATTEMPTS
        assert call_count["n"] == _AppendEventRetry.MAX_ATTEMPTS

        with suppress(Exception):
            await store.close()

    async def test_unrelated_unique_violation_does_not_retry(self, monkeypatch) -> None:
        store = self._store()
        unrelated = self._other_violation()
        call_count = {"n": 0}

        async def fake_once(event: RuntimeEventDraft):
            call_count["n"] += 1
            raise unrelated

        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(store, "_append_event_once", fake_once)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        with pytest.raises(psycopg_errors.UniqueViolation):
            await store.append_event(self._draft())
        # Exactly one attempt — non-matching constraint propagates immediately.
        assert call_count["n"] == 1

        with suppress(Exception):
            await store.close()

    async def test_stable_event_primary_key_race_retries(self, monkeypatch) -> None:
        store = self._store()
        sentinel = MagicMock(name="envelope")
        attempts = {"n": 0}
        draft = self._draft().model_copy(update={"event_id": f"artevt_{'c' * 64}"})

        async def fake_once(event: RuntimeEventDraft):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _FakeUniqueViolation(
                    constraint_name=_AppendEventRetry.EVENT_ID_CONSTRAINT
                )
            return sentinel

        async def fake_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(store, "_append_event_once", fake_once)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        assert await store.append_event(draft) is sentinel
        assert attempts["n"] == 2

        with suppress(Exception):
            await store.close()
