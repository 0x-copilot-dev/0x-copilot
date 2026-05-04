"""C11 — pg_stat_statements scraper + slow-query tracer tests.

The scraper is exercised against a fake ``run_query`` so we don't need a
live Postgres. The privacy assertion ("no query text in spans") is the
load-bearing one — production deploys can leak PII through metric labels.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_runtime.observability.db_statement_metrics import (
    DbStatementDigest,
    DbStatementMetricsCollector,
    SlowQueryTracer,
)


class TestDbStatementDigest:
    def test_normalizes_parameters(self) -> None:
        a = DbStatementDigest.for_statement("SELECT * FROM t WHERE id = $1")
        b = DbStatementDigest.for_statement("SELECT * FROM t WHERE id = $42")
        assert a == b

    def test_normalizes_whitespace(self) -> None:
        a = DbStatementDigest.for_statement("SELECT *\n  FROM t WHERE id = $1")
        b = DbStatementDigest.for_statement("SELECT * FROM t WHERE id = $1")
        assert a == b

    def test_case_insensitive(self) -> None:
        a = DbStatementDigest.for_statement("select * from t where id = $1")
        b = DbStatementDigest.for_statement("SELECT * FROM t WHERE id = $1")
        assert a == b

    def test_distinct_statements_produce_distinct_digests(self) -> None:
        a = DbStatementDigest.for_statement("SELECT * FROM t1 WHERE id = $1")
        b = DbStatementDigest.for_statement("SELECT * FROM t2 WHERE id = $1")
        assert a != b


class TestSlowQueryTracer:
    def test_below_threshold_does_not_fire(self) -> None:
        tracer = SlowQueryTracer(threshold_ms=500)
        fired = tracer.observe(query="SELECT 1", duration_ms=100.0)
        assert fired is False

    def test_above_threshold_fires(self) -> None:
        tracer = SlowQueryTracer(threshold_ms=500)
        fired = tracer.observe(query="SELECT 1", duration_ms=1500.0)
        assert fired is True

    def test_threshold_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUNTIME_DB_SLOW_QUERY_MS", raising=False)
        tracer = SlowQueryTracer()
        assert tracer.threshold_ms == 500

    def test_threshold_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNTIME_DB_SLOW_QUERY_MS", "250")
        tracer = SlowQueryTracer()
        assert tracer.threshold_ms == 250

    def test_query_text_never_in_span_attributes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Replace the tracer with an in-memory recorder; assert the only
        # attributes set are digest + duration_ms.
        captured: dict[str, object] = {}

        class _FakeSpan:
            def set_attribute(self, key: str, value: object) -> None:
                captured[key] = value

            def __enter__(self) -> "_FakeSpan":
                return self

            def __exit__(self, *a: object) -> None:
                return None

        class _FakeCtx:
            def __enter__(self) -> _FakeSpan:
                return _FakeSpan()

            def __exit__(self, *a: object) -> None:
                return None

        class _FakeTracer:
            def start_as_current_span(self, name: str) -> _FakeCtx:
                assert name == "db.slow_query"
                return _FakeCtx()

        tracer = SlowQueryTracer(threshold_ms=10)
        tracer._tracer = _FakeTracer()
        plaintext = "SELECT * FROM users WHERE email = 'leaked@example.com'"
        tracer.observe(query=plaintext, duration_ms=100.0)
        assert "db.statement.digest" in captured
        assert "db.statement.duration_ms" in captured
        # The plaintext (and any literal in it) must NEVER appear in any
        # attribute key OR value.
        for key, value in captured.items():
            assert "leaked@example.com" not in str(key)
            assert "leaked@example.com" not in str(value)
            assert "users" not in str(value)


class TestDbStatementMetricsCollector:
    def test_scrape_once_invokes_query_and_returns_count(self) -> None:
        captured: list[str] = []

        async def fake_query(sql: str) -> list[dict]:
            captured.append(sql)
            return [
                {
                    "query_id": "q1",
                    "calls": 5,
                    "total_exec_time_ms": 250.0,
                    "rows": 100,
                    "query_text": "SELECT * FROM t WHERE id = $1",
                },
                {
                    "query_id": "q2",
                    "calls": 1,
                    "total_exec_time_ms": 12.0,
                    "rows": 1,
                    "query_text": "SELECT * FROM other WHERE x = $1",
                },
            ]

        collector = DbStatementMetricsCollector(run_query=fake_query)
        count = asyncio.run(collector.scrape_once())
        assert count == 2
        assert "pg_stat_statements" in captured[0]

    def test_extension_unavailable_logs_once_and_continues(self) -> None:
        async def boom(_sql: str) -> list[dict]:
            raise RuntimeError("relation pg_stat_statements does not exist")

        collector = DbStatementMetricsCollector(run_query=boom)
        # First call records the warning; subsequent calls don't re-emit
        # (the flag short-circuits).
        assert asyncio.run(collector.scrape_once()) == 0
        assert collector._extension_warning_emitted is True
        assert asyncio.run(collector.scrape_once()) == 0
