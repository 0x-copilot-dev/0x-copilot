"""HTTP route tests for P8-A4 per-agent usage aggregation.

Covers:
- aggregation correctness (token sums, distinct-run count, cost
  breakdown by purpose),
- tenant isolation (cross-tenant agent_id returns zero/empty totals),
- period buckets (rows outside the window are excluded),
- read-only posture (no usage row written, no Purpose enum extension).

Per cross-audit §5.5 the single-tracker invariant means everything
this route returns must come from the canonical
``runtime_model_call_usage`` table joined to
``agent_runs.runtime_context.trace_metadata.agent_id``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas.runs import RunRecord


class _AgentUsageMixin:
    """Shared fixtures for the per-agent usage route tests."""

    AGENT_ID = "agent_calendar_whisperer"
    OTHER_AGENT_ID = "agent_other"
    ORG_ID = "org_a"
    USER_ID = "user_1"
    CONVERSATION_ID = "conv-1"

    def _client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        return TestClient(app), store

    def _seed_run(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        run_id: str,
        org_id: str,
        agent_id: str | None,
        created_at: datetime,
    ) -> RunRecord:
        """Insert a ``RunRecord`` carrying ``agent_id`` on ``trace_metadata``."""

        trace_metadata: dict[str, object] = {}
        if agent_id is not None:
            trace_metadata["agent_id"] = agent_id
        runtime_context = AgentRuntimeContext(
            user_id=self.USER_ID,
            org_id=org_id,
            roles=["employee"],
            run_id=run_id,
            trace_id=f"trace-{run_id}",
            trace_metadata=trace_metadata,
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        )
        record = RunRecord(
            run_id=run_id,
            conversation_id=self.CONVERSATION_ID,
            org_id=org_id,
            user_id=self.USER_ID,
            user_message_id=f"msg-{run_id}",
            trace_id=f"trace-{run_id}",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=runtime_context,
            created_at=created_at,
        )
        store.runs[run_id] = record
        return record

    def _seed_call(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        run_id: str,
        org_id: str,
        created_at: datetime,
        purpose: str = "main",
        input_tokens: int = 100,
        output_tokens: int = 50,
        cost_micro_usd: int | None = 1_000,
    ) -> RuntimeModelCallUsageRecord:
        """Insert one canonical per-call usage row."""

        row = RuntimeModelCallUsageRecord(
            id=f"call-{len(store.model_call_usage)}",
            org_id=org_id,
            run_id=run_id,
            conversation_id=self.CONVERSATION_ID,
            trace_id=f"trace-{run_id}",
            purpose=purpose,
            model_provider="openai",
            model_name="gpt-5.4-mini",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=0,
            total_tokens=input_tokens + output_tokens,
            duration_ms=500,
            cost_micro_usd=cost_micro_usd,
            created_at=created_at,
        )
        store.model_call_usage.append(row)
        return row


class TestAgentUsageAggregation(_AgentUsageMixin):
    """Aggregation correctness — tokens, run count, purpose breakdown."""

    def test_sums_tokens_and_counts_distinct_runs(self) -> None:
        client, store = self._client()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        # Two runs for our agent, one not for our agent.
        self._seed_run(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_run(
            store,
            run_id="r2",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_run(
            store,
            run_id="r3",
            org_id=self.ORG_ID,
            agent_id=self.OTHER_AGENT_ID,
            created_at=completed,
        )
        # r1: two calls (main + tool_planning), r2: one call (main),
        # r3: one call belonging to the OTHER agent — must not leak.
        self._seed_call(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="main",
            input_tokens=100,
            output_tokens=50,
            cost_micro_usd=1_000,
        )
        self._seed_call(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="tool_planning",
            input_tokens=20,
            output_tokens=10,
            cost_micro_usd=200,
        )
        self._seed_call(
            store,
            run_id="r2",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="main",
            input_tokens=30,
            output_tokens=15,
            cost_micro_usd=400,
        )
        self._seed_call(
            store,
            run_id="r3",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="main",
            input_tokens=999,
            output_tokens=999,
            cost_micro_usd=99_999,
        )

        response = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": self.ORG_ID, "user_id": self.USER_ID, "period": "7d"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["agent_id"] == self.AGENT_ID
        assert body["run_count"] == 2  # r1 + r2, NOT r3
        assert body["token_in"] == 150  # 100 + 20 + 30
        assert body["token_out"] == 75  # 50 + 10 + 15
        assert body["cost_usd_micro"] == 1_600  # 1000 + 200 + 400
        # Cost breakdown by purpose — main (1000+400) + tool_planning (200).
        breakdown = body["cost_breakdown_by_purpose"]
        assert breakdown["main"] == 1_400
        assert breakdown["tool_planning"] == 200
        # The other agent's purpose must not appear at all.
        assert sum(breakdown.values()) == 1_600

    def test_returns_zero_when_agent_has_no_runs(self) -> None:
        client, _ = self._client()
        response = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": self.ORG_ID, "user_id": self.USER_ID, "period": "7d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["agent_id"] == self.AGENT_ID
        assert body["run_count"] == 0
        assert body["token_in"] == 0
        assert body["token_out"] == 0
        assert body["cost_usd_micro"] == 0
        assert body["cost_breakdown_by_purpose"] == {}

    def test_null_cost_rows_contribute_zero(self) -> None:
        """Pre-pricing or unpriced models leave ``cost_micro_usd`` ``NULL``.

        Token counts must still aggregate; cost stays at zero rather
        than panicking — mirrors the rollup loop's behaviour.
        """

        client, store = self._client()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._seed_run(
            store,
            run_id="r_np",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_call(
            store,
            run_id="r_np",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="main",
            input_tokens=10,
            output_tokens=5,
            cost_micro_usd=None,
        )
        response = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": self.ORG_ID, "user_id": self.USER_ID, "period": "7d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["run_count"] == 1
        assert body["token_in"] == 10
        assert body["token_out"] == 5
        assert body["cost_usd_micro"] == 0
        assert body["cost_breakdown_by_purpose"] == {}


class TestAgentUsageTenantIsolation(_AgentUsageMixin):
    """Cross-tenant reads must return zero — tenant scope wins before any sum."""

    def test_other_tenant_cannot_read_agent_usage(self) -> None:
        client, store = self._client()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        # Seed a run + call in org_a for our agent_id.
        self._seed_run(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_call(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="main",
            input_tokens=100,
            output_tokens=50,
            cost_micro_usd=1_000,
        )
        # Query as org_b — must see zero totals, never org_a's data.
        response = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": "org_b", "user_id": "user_x", "period": "7d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["run_count"] == 0
        assert body["token_in"] == 0
        assert body["token_out"] == 0
        assert body["cost_usd_micro"] == 0
        assert body["cost_breakdown_by_purpose"] == {}

    def test_same_agent_id_in_different_tenants_does_not_leak(self) -> None:
        """A SaaS-y system-agent name (e.g. ``agent_atlas``) can exist in
        many tenants. Each tenant's view must only contain its own rows.
        """

        client, store = self._client()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._seed_run(
            store,
            run_id="r1",
            org_id="org_a",
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_call(
            store,
            run_id="r1",
            org_id="org_a",
            created_at=completed,
            purpose="main",
            input_tokens=100,
            output_tokens=50,
            cost_micro_usd=1_000,
        )
        self._seed_run(
            store,
            run_id="r_b",
            org_id="org_b",
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_call(
            store,
            run_id="r_b",
            org_id="org_b",
            created_at=completed,
            purpose="main",
            input_tokens=7,
            output_tokens=3,
            cost_micro_usd=50,
        )
        # Org_a only sees its own row.
        response_a = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": "org_a", "user_id": "user_1", "period": "7d"},
        )
        body_a = response_a.json()
        assert body_a["run_count"] == 1
        assert body_a["token_in"] == 100
        assert body_a["cost_usd_micro"] == 1_000
        # Org_b only sees its own row.
        response_b = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": "org_b", "user_id": "user_2", "period": "7d"},
        )
        body_b = response_b.json()
        assert body_b["run_count"] == 1
        assert body_b["token_in"] == 7
        assert body_b["cost_usd_micro"] == 50


class TestAgentUsagePeriodBuckets(_AgentUsageMixin):
    """Period parsing — rows outside the window must be excluded."""

    def test_old_rows_outside_7d_window_excluded(self) -> None:
        client, store = self._client()
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        ancient = datetime.now(timezone.utc) - timedelta(days=45)
        self._seed_run(
            store,
            run_id="r_recent",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=recent,
        )
        self._seed_run(
            store,
            run_id="r_ancient",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=ancient,
        )
        self._seed_call(
            store,
            run_id="r_recent",
            org_id=self.ORG_ID,
            created_at=recent,
            purpose="main",
            input_tokens=10,
            output_tokens=5,
            cost_micro_usd=100,
        )
        # Ancient row (45 days ago) — outside the 7d AND 30d windows.
        self._seed_call(
            store,
            run_id="r_ancient",
            org_id=self.ORG_ID,
            created_at=ancient,
            purpose="main",
            input_tokens=999,
            output_tokens=999,
            cost_micro_usd=99_999,
        )

        # 7d: only the recent row.
        response_7d = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": self.ORG_ID, "user_id": self.USER_ID, "period": "7d"},
        )
        body_7d = response_7d.json()
        assert body_7d["run_count"] == 1
        assert body_7d["token_in"] == 10
        assert body_7d["cost_usd_micro"] == 100

        # 30d: still only the recent row (ancient is 45 days back).
        response_30d = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": self.ORG_ID, "user_id": self.USER_ID, "period": "30d"},
        )
        body_30d = response_30d.json()
        assert body_30d["run_count"] == 1
        assert body_30d["token_in"] == 10
        assert body_30d["cost_usd_micro"] == 100


class TestAgentUsageReadOnly(_AgentUsageMixin):
    """The route must never write — no usage row, no run, no audit row produced.

    This is the cross-audit §5.5 invariant made testable: a query
    over the per-agent usage projection cannot mutate any usage table
    or extend the ``Purpose`` enumeration in the row store.
    """

    def test_read_does_not_mutate_any_usage_table(self) -> None:
        client, store = self._client()
        completed = datetime.now(timezone.utc) - timedelta(hours=1)
        self._seed_run(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            agent_id=self.AGENT_ID,
            created_at=completed,
        )
        self._seed_call(
            store,
            run_id="r1",
            org_id=self.ORG_ID,
            created_at=completed,
            purpose="main",
            input_tokens=100,
            output_tokens=50,
            cost_micro_usd=1_000,
        )
        # Snapshot every usage-touching collection.
        before_run_usage = dict(store.run_usage)
        before_calls = list(store.model_call_usage)
        before_user_rollup = dict(store.user_daily_usage)
        before_org_rollup = dict(store.org_daily_usage)
        before_connector_rollup = dict(store.connector_daily_usage)
        before_subagent_rollup = dict(store.subagent_daily_usage)
        before_purpose_rollup = dict(store.purpose_daily_usage)
        before_audit = list(store.audit_log)

        response = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}",
            params={"org_id": self.ORG_ID, "user_id": self.USER_ID, "period": "7d"},
        )
        assert response.status_code == 200

        # Nothing changed.
        assert store.run_usage == before_run_usage
        assert store.model_call_usage == before_calls
        assert store.user_daily_usage == before_user_rollup
        assert store.org_daily_usage == before_org_rollup
        assert store.connector_daily_usage == before_connector_rollup
        assert store.subagent_daily_usage == before_subagent_rollup
        assert store.purpose_daily_usage == before_purpose_rollup
        assert store.audit_log == before_audit


class TestAgentUsageIdentityGate(_AgentUsageMixin):
    """``scoped_identity`` parity with the rest of ``/v1/usage``."""

    def test_400_when_org_id_missing_and_no_service_token(self) -> None:
        client, _ = self._client()
        response = client.get(
            f"/v1/usage/org/agent/{self.AGENT_ID}", params={"period": "7d"}
        )
        assert response.status_code == 400
