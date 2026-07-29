"""HTTP route tests for the Context Occupancy Ledger read API (design §7).

Four things are worth failing a build over here, and each has its own class:

- **Projection fidelity.** A stored row's rollups and its segment decomposition
  reach the client unchanged, ``free_tokens`` stays ``None`` rather than ``0``
  when the window is unknown, and a segment this build cannot parse costs the
  caller that segment and nothing else.
- **Scope isolation (§6.2).** ``graph_scope`` actually filters, and the applied
  filter is echoed, because root and subagent snapshots describe different
  windows and a client that sums an unfiltered series reports utilization no
  model ever saw.
- **Tenant isolation.** Cross-org, cross-user, and unknown subjects are all
  ``200`` + empty and therefore indistinguishable — a ``404`` for the
  cross-tenant case would make this endpoint an existence oracle for run ids in
  other organizations.
- **Posture.** ``runtime:use`` is required under RBAC enforcement, identity
  resolution matches ``/v1/usage/*`` (400 without one), and the read writes
  nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import (
    RuntimeContextGraphScope,
    RuntimeContextOccupancyRecord,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import ConversationRecord, RunRecord


_SERVICE_TOKEN = "context-occupancy-service-token"


class ContextOccupancyFixtureMixin:
    """Store seeding + client construction shared by every case below.

    Rows are poked straight into the in-memory store rather than driven through
    a real run: this suite is about the *read* contract, and a fake that
    reproduces measurement would test the capture seam instead — which
    ``tests/unit/agent_runtime/observability`` already owns.
    """

    ORG_ID = "org_occupancy_a"
    OTHER_ORG_ID = "org_occupancy_b"
    USER_ID = "user_occupancy_1"
    OTHER_USER_ID = "user_occupancy_2"
    CONVERSATION_ID = "conv_occupancy_1"
    RUN_ID = "run_occupancy_1"

    #: A single well-formed stored segment. Written as a literal dict — not as a
    #: dump of the observability contract — so the read path is exercised
    #: against the JSON that is actually in the column, exactly as an older
    #: writer would have left it.
    SEGMENT_TOOL: dict[str, Any] = {
        "segment_class": "tools",
        "label": "agent_runtime.capabilities.backends:publish_artifact",
        "lifecycle": "resident",
        "third_party": False,
        "detail": "publish_artifact",
        "byte_count": 2_600,
        "estimated_tokens": 650,
        "item_count": 1,
        "cache_eligibility": "stable_prefix",
        "counter_source": "tokenizer",
    }
    SEGMENT_MESSAGES: dict[str, Any] = {
        "segment_class": "messages",
        "label": "agent_runtime.conversation:user",
        "lifecycle": "per_turn",
        "third_party": False,
        "detail": "messages[0..3]",
        "byte_count": 1_200,
        "estimated_tokens": 300,
        "item_count": 4,
        "cache_eligibility": None,
        "counter_source": "heuristic",
    }

    def client(self) -> tuple[TestClient, InMemoryRuntimeApiStore]:
        """Build the real app over an in-memory store, like the usage suites."""

        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "in_memory",
            }
        )
        ports = RuntimeAdapterFactory.from_store(store)
        app = RuntimeApiAppFactory.create_app(ports=ports, settings=settings)
        return TestClient(app), store

    def seed_conversation(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        conversation_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> ConversationRecord:
        """Insert the conversation the read path resolves before any row."""

        record = ConversationRecord(
            conversation_id=conversation_id or self.CONVERSATION_ID,
            org_id=org_id or self.ORG_ID,
            user_id=user_id or self.USER_ID,
            assistant_id="assistant_occupancy",
        )
        store.conversations[record.conversation_id] = record
        return record

    def seed_run(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        run_id: str | None = None,
        org_id: str | None = None,
        user_id: str | None = None,
        conversation_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RunRecord:
        """Insert the run the read path resolves before any row."""

        run_id = run_id or self.RUN_ID
        org_id = org_id or self.ORG_ID
        user_id = user_id or self.USER_ID
        conversation_id = conversation_id or self.CONVERSATION_ID
        record = RunRecord(
            run_id=run_id,
            conversation_id=conversation_id,
            org_id=org_id,
            user_id=user_id,
            user_message_id=f"msg-{run_id}",
            trace_id=f"trace-{run_id}",
            model_provider="openai",
            model_name="gpt-5.4-mini",
            runtime_context=AgentRuntimeContext(
                user_id=user_id,
                org_id=org_id,
                roles=["employee"],
                run_id=run_id,
                trace_id=f"trace-{run_id}",
                model_profile={
                    "provider": "openai",
                    "model_name": "gpt-5.4-mini",
                    "max_input_tokens": 128000,
                    "timeout_seconds": 30,
                    "temperature": 0,
                    "supports_streaming": True,
                },
            ),
            created_at=created_at or datetime.now(timezone.utc),
        )
        store.runs[run_id] = record
        return record

    def seed_snapshot(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        model_call_id: str,
        created_at: datetime,
        org_id: str | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
        graph_scope: RuntimeContextGraphScope = RuntimeContextGraphScope.ROOT,
        attempt_ordinal: int = 1,
        context_window_tokens: int | None = 200_000,
        estimated_input_tokens: int = 950,
        provider_input_tokens: int | None = 1_000,
        undeclared_tokens: int = 0,
        segments: tuple[dict[str, Any], ...] | None = None,
    ) -> RuntimeContextOccupancyRecord:
        """Insert one measured occupancy row keyed by its attempt."""

        record = RuntimeContextOccupancyRecord.from_measurement(
            org_id=org_id or self.ORG_ID,
            run_id=run_id or self.RUN_ID,
            conversation_id=conversation_id or self.CONVERSATION_ID,
            model_call_id=model_call_id,
            attempt_ordinal=attempt_ordinal,
            graph_scope=graph_scope,
            provider="openai",
            model_family="gpt-5.4-mini",
            context_window_tokens=context_window_tokens,
            estimated_input_tokens=estimated_input_tokens,
            provider_input_tokens=provider_input_tokens,
            cached_input_tokens=400,
            cache_creation_input_tokens=0,
            undeclared_tokens=undeclared_tokens,
            segments=segments
            if segments is not None
            else (self.SEGMENT_TOOL, self.SEGMENT_MESSAGES),
            created_at=created_at,
        )
        store.context_occupancy[record.idempotency_key] = record
        return record

    def run_params(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        graph_scope: str | None = None,
    ) -> dict[str, str]:
        """Query params matching the ``/v1/usage/*`` identity convention."""

        params = {
            "org_id": org_id or self.ORG_ID,
            "user_id": user_id or self.USER_ID,
        }
        if graph_scope is not None:
            params["graph_scope"] = graph_scope
        return params

    @staticmethod
    def run_path(run_id: str) -> str:
        return f"/v1/agent/runs/{run_id}/context/occupancy"

    @staticmethod
    def conversation_path(conversation_id: str) -> str:
        return f"/v1/agent/conversations/{conversation_id}/context/occupancy"


class TestRunOccupancySeriesProjection(ContextOccupancyFixtureMixin):
    """The stored row reaches the client intact, decomposition included."""

    def test_returns_series_oldest_first_with_full_projection(self) -> None:
        client, store = self.client()
        base = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.seed_conversation(store)
        self.seed_run(store)
        # Seeded newest-first so a reader that trusts insertion order fails.
        self.seed_snapshot(
            store,
            model_call_id="call_2",
            created_at=base + timedelta(seconds=30),
        )
        self.seed_snapshot(store, model_call_id="call_1", created_at=base)

        response = client.get(self.run_path(self.RUN_ID), params=self.run_params())

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["run_id"] == self.RUN_ID
        assert body["graph_scope"] is None
        assert [row["model_call_id"] for row in body["snapshots"]] == [
            "call_1",
            "call_2",
        ]
        first = body["snapshots"][0]
        assert first["schema_version"] == 1
        assert first["graph_scope"] == "root"
        assert first["provider"] == "openai"
        assert first["model_family"] == "gpt-5.4-mini"
        assert first["context_window_tokens"] == 200_000
        assert first["estimated_input_tokens"] == 950
        assert first["provider_input_tokens"] == 1_000
        assert first["cached_input_tokens"] == 400
        assert first["undeclared_tokens"] == 0
        # provider (1000) - estimated (950); signed, derived at write time.
        assert first["unattributed_delta"] == 50
        # window (200000) - provider total (1000), within this scope only.
        assert first["free_tokens"] == 199_000
        assert first["unreadable_segment_count"] == 0
        # Stored order is preserved rather than re-sorted: the capture seam
        # already canonicalizes segments before they reach a column, so a reader
        # that re-ordered them would be inventing a second ordering authority.
        assert [segment["label"] for segment in first["segments"]] == [
            "agent_runtime.capabilities.backends:publish_artifact",
            "agent_runtime.conversation:user",
        ]
        tool_segment = first["segments"][0]
        assert tool_segment["segment_class"] == "tools"
        assert tool_segment["lifecycle"] == "resident"
        assert tool_segment["estimated_tokens"] == 650
        assert tool_segment["cache_eligibility"] == "stable_prefix"
        assert tool_segment["counter_source"] == "tokenizer"

    def test_never_leaks_the_tenant_identifier_onto_the_wire(self) -> None:
        """``org_id`` is a scoping input, never a response field."""

        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(self.run_path(self.RUN_ID), params=self.run_params())

        assert response.status_code == 200
        assert "org_id" not in response.text

    def test_unknown_context_window_yields_null_free_tokens_not_zero(self) -> None:
        """A model absent from the pricing catalog has no denominator (§4.5)."""

        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
            context_window_tokens=None,
        )

        response = client.get(self.run_path(self.RUN_ID), params=self.run_params())

        snapshot = response.json()["snapshots"][0]
        assert snapshot["context_window_tokens"] is None
        assert snapshot["free_tokens"] is None

    def test_unreadable_segment_is_counted_and_totals_stay_exact(self) -> None:
        """A newer writer's segment costs that segment, not the whole read.

        The rollups are stored columns rather than sums over the list, so a
        decomposition this build cannot fully parse still reports exact totals —
        which is a strictly better answer than a 500.
        """

        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
            segments=(
                self.SEGMENT_TOOL,
                {**self.SEGMENT_MESSAGES, "future_field_this_build_lacks": 7},
            ),
        )

        response = client.get(self.run_path(self.RUN_ID), params=self.run_params())

        assert response.status_code == 200, response.text
        snapshot = response.json()["snapshots"][0]
        assert snapshot["unreadable_segment_count"] == 1
        assert [segment["label"] for segment in snapshot["segments"]] == [
            "agent_runtime.capabilities.backends:publish_artifact"
        ]
        assert snapshot["estimated_input_tokens"] == 950

    def test_retried_call_appears_as_two_snapshots(self) -> None:
        """Retries do not overwrite — a second attempt is a second row (§6.3)."""

        client, store = self.client()
        base = datetime.now(timezone.utc)
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(store, model_call_id="call_1", created_at=base)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=base + timedelta(seconds=1),
            attempt_ordinal=2,
        )

        body = client.get(self.run_path(self.RUN_ID), params=self.run_params()).json()

        assert [row["attempt_ordinal"] for row in body["snapshots"]] == [1, 2]
        assert {row["model_call_id"] for row in body["snapshots"]} == {"call_1"}


class TestRunOccupancyGraphScopeFilter(ContextOccupancyFixtureMixin):
    """Scopes are separate windows and the applied filter is visible (§6.2)."""

    def _seed_both_scopes(self, store: InMemoryRuntimeApiStore) -> None:
        base = datetime.now(timezone.utc)
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="root_call",
            created_at=base,
            graph_scope=RuntimeContextGraphScope.ROOT,
        )
        self.seed_snapshot(
            store,
            model_call_id="child_call",
            created_at=base + timedelta(seconds=1),
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
        )

    def test_unfiltered_returns_both_scopes_and_echoes_no_filter(self) -> None:
        client, store = self.client()
        self._seed_both_scopes(store)

        body = client.get(self.run_path(self.RUN_ID), params=self.run_params()).json()

        assert body["graph_scope"] is None
        assert {row["graph_scope"] for row in body["snapshots"]} == {
            "root",
            "subagent",
        }

    @pytest.mark.parametrize("scope", ["root", "subagent"])
    def test_filter_returns_only_that_scope_and_echoes_it(self, scope: str) -> None:
        client, store = self.client()
        self._seed_both_scopes(store)

        body = client.get(
            self.run_path(self.RUN_ID),
            params=self.run_params(graph_scope=scope),
        ).json()

        assert body["graph_scope"] == scope
        assert [row["graph_scope"] for row in body["snapshots"]] == [scope]

    def test_unknown_graph_scope_is_rejected_at_the_edge(self) -> None:
        """A typo is refused rather than silently widened to an all-scopes read.

        The status is ``400`` because this service maps
        ``RequestValidationError`` there rather than to FastAPI's default 422;
        what matters is that an unrecognised scope never falls through to
        ``graph_scope=None``, which would return both windows and invite a
        cross-scope sum (§6.2).
        """

        client, store = self.client()
        self._seed_both_scopes(store)

        response = client.get(
            self.run_path(self.RUN_ID),
            params=self.run_params(graph_scope="orchestrator"),
        )

        assert response.status_code == 400


class TestRunOccupancyTenantIsolation(ContextOccupancyFixtureMixin):
    """Absence is one answer, so the endpoint is not an existence oracle."""

    def test_cross_org_caller_gets_empty_not_another_tenants_rows(self) -> None:
        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(
            self.run_path(self.RUN_ID),
            params=self.run_params(org_id=self.OTHER_ORG_ID, user_id="user_x"),
        )

        assert response.status_code == 200
        assert response.json()["snapshots"] == []

    def test_same_run_id_in_two_tenants_does_not_cross_over(self) -> None:
        """Run ids are opaque; nothing stops two tenants minting the same one."""

        client, store = self.client()
        now = datetime.now(timezone.utc)
        for org_id, user_id, call_id in (
            (self.ORG_ID, self.USER_ID, "call_org_a"),
            (self.OTHER_ORG_ID, self.OTHER_USER_ID, "call_org_b"),
        ):
            self.seed_conversation(
                store,
                conversation_id=f"conv_{org_id}",
                org_id=org_id,
                user_id=user_id,
            )
            self.seed_run(
                store,
                run_id=f"{self.RUN_ID}_{org_id}",
                org_id=org_id,
                user_id=user_id,
                conversation_id=f"conv_{org_id}",
            )
            self.seed_snapshot(
                store,
                model_call_id=call_id,
                created_at=now,
                org_id=org_id,
                run_id=f"{self.RUN_ID}_{org_id}",
                conversation_id=f"conv_{org_id}",
            )

        # Org A asks for its own run with org B's identity: empty, not org A's row.
        response = client.get(
            self.run_path(f"{self.RUN_ID}_{self.ORG_ID}"),
            params=self.run_params(
                org_id=self.OTHER_ORG_ID, user_id=self.OTHER_USER_ID
            ),
        )

        assert response.status_code == 200
        assert response.json()["snapshots"] == []

    def test_other_user_in_same_org_gets_empty(self) -> None:
        """Owner scoping matches ``/v1/usage/runs/{run_id}``, which 404s here."""

        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(
            self.run_path(self.RUN_ID),
            params=self.run_params(user_id=self.OTHER_USER_ID),
        )

        assert response.status_code == 200
        assert response.json()["snapshots"] == []

    def test_unknown_run_is_empty_rather_than_404(self) -> None:
        """Indistinguishable from the cross-tenant case, by design."""

        client, _ = self.client()

        response = client.get(
            self.run_path("run_that_never_existed"),
            params=self.run_params(),
        )

        assert response.status_code == 200
        assert response.json() == {
            "run_id": "run_that_never_existed",
            "graph_scope": None,
            "snapshots": [],
        }

    def test_known_run_without_measurements_is_empty(self) -> None:
        """A run measured before this ledger existed reports nothing, not zero."""

        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)

        body = client.get(self.run_path(self.RUN_ID), params=self.run_params()).json()

        assert body["snapshots"] == []


class TestConversationLatestOccupancy(ContextOccupancyFixtureMixin):
    """ "What is in context right now" — newest root-scope snapshot only."""

    def test_returns_newest_root_snapshot_and_its_run(self) -> None:
        client, store = self.client()
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.seed_conversation(store)
        self.seed_run(store, created_at=base)
        self.seed_snapshot(store, model_call_id="older", created_at=base)
        self.seed_snapshot(
            store,
            model_call_id="newest",
            created_at=base + timedelta(seconds=10),
        )

        response = client.get(
            self.conversation_path(self.CONVERSATION_ID),
            params=self.run_params(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["conversation_id"] == self.CONVERSATION_ID
        assert body["run_id"] == self.RUN_ID
        assert body["snapshot"]["model_call_id"] == "newest"
        assert body["snapshot"]["graph_scope"] == "root"

    def test_ignores_a_more_recent_subagent_snapshot(self) -> None:
        """A child window that has since been discarded is not "what is in context"."""

        client, store = self.client()
        base = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.seed_conversation(store)
        self.seed_run(store, created_at=base)
        self.seed_snapshot(store, model_call_id="root_call", created_at=base)
        self.seed_snapshot(
            store,
            model_call_id="child_call",
            created_at=base + timedelta(seconds=30),
            graph_scope=RuntimeContextGraphScope.SUBAGENT,
        )

        body = client.get(
            self.conversation_path(self.CONVERSATION_ID),
            params=self.run_params(),
        ).json()

        assert body["snapshot"]["model_call_id"] == "root_call"

    def test_falls_back_to_an_older_run_when_the_newest_was_not_measured(self) -> None:
        client, store = self.client()
        base = datetime.now(timezone.utc) - timedelta(hours=1)
        self.seed_conversation(store)
        self.seed_run(store, run_id="run_old", created_at=base)
        self.seed_run(store, run_id="run_new", created_at=base + timedelta(minutes=5))
        self.seed_snapshot(
            store,
            model_call_id="only_measured_call",
            created_at=base,
            run_id="run_old",
        )

        body = client.get(
            self.conversation_path(self.CONVERSATION_ID),
            params=self.run_params(),
        ).json()

        assert body["run_id"] == "run_old"
        assert body["snapshot"]["model_call_id"] == "only_measured_call"

    def test_cross_org_caller_gets_no_snapshot(self) -> None:
        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(
            self.conversation_path(self.CONVERSATION_ID),
            params=self.run_params(org_id=self.OTHER_ORG_ID, user_id="user_x"),
        )

        assert response.status_code == 200
        assert response.json() == {
            "conversation_id": self.CONVERSATION_ID,
            "run_id": None,
            "snapshot": None,
        }

    def test_other_user_in_same_org_gets_no_snapshot(self) -> None:
        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(
            self.conversation_path(self.CONVERSATION_ID),
            params=self.run_params(user_id=self.OTHER_USER_ID),
        )

        assert response.status_code == 200
        assert response.json()["snapshot"] is None

    def test_unknown_conversation_is_empty_rather_than_404(self) -> None:
        client, _ = self.client()

        response = client.get(
            self.conversation_path("conv_that_never_existed"),
            params=self.run_params(),
        )

        assert response.status_code == 200
        assert response.json()["snapshot"] is None

    def test_does_not_shadow_the_existing_conversation_context_route(self) -> None:
        """``/context`` keeps serving the window summary; occupancy is beside it.

        The design writes both endpoints at ``.../context``; that path was
        already taken, and a second registration on it would be dead code. This
        pins that the pre-existing route still answers with its own shape.
        """

        client, store = self.client()
        self.seed_conversation(store)

        response = client.get(
            f"/v1/agent/conversations/{self.CONVERSATION_ID}/context",
            params=self.run_params(),
        )

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"model", "current", "breakdown"}


class TestContextOccupancyIsReadOnly(ContextOccupancyFixtureMixin):
    """Occupancy is an observation lane, never the money tracker (§6.1)."""

    def test_reads_mutate_nothing(self) -> None:
        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )
        before_occupancy = dict(store.context_occupancy)
        before_calls = list(store.model_call_usage)
        before_run_usage = dict(store.run_usage)
        before_audit = list(store.audit_log)

        assert (
            client.get(self.run_path(self.RUN_ID), params=self.run_params()).status_code
            == 200
        )
        assert (
            client.get(
                self.conversation_path(self.CONVERSATION_ID),
                params=self.run_params(),
            ).status_code
            == 200
        )

        assert store.context_occupancy == before_occupancy
        assert store.model_call_usage == before_calls
        assert store.run_usage == before_run_usage
        assert store.audit_log == before_audit


class TestContextOccupancyIdentityGate(ContextOccupancyFixtureMixin):
    """``scoped_identity`` parity with ``/v1/usage/*``."""

    def test_run_route_400s_without_identity(self) -> None:
        client, _ = self.client()

        response = client.get(self.run_path(self.RUN_ID))

        assert response.status_code == 400

    def test_conversation_route_400s_without_identity(self) -> None:
        client, _ = self.client()

        response = client.get(self.conversation_path(self.CONVERSATION_ID))

        assert response.status_code == 400


class TestContextOccupancyScopeGuard(ContextOccupancyFixtureMixin):
    """``runtime:use`` is required, and enforced when RBAC enforces."""

    @pytest.fixture(autouse=True)
    def _enforce_rbac(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", _SERVICE_TOKEN)
        yield

    def _headers(self, *scopes: str) -> dict[str, str]:
        return {
            "x-enterprise-service-token": _SERVICE_TOKEN,
            "x-enterprise-org-id": self.ORG_ID,
            "x-enterprise-user-id": self.USER_ID,
            "x-enterprise-roles": "employee",
            "x-enterprise-permission-scopes": ",".join(scopes),
            "x-enterprise-connector-scopes": "{}",
        }

    def test_caller_without_runtime_use_is_refused_on_the_run_route(self) -> None:
        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(self.run_path(self.RUN_ID), headers=self._headers())

        assert response.status_code == 403

    def test_caller_without_runtime_use_is_refused_on_the_conversation_route(
        self,
    ) -> None:
        client, store = self.client()
        self.seed_conversation(store)

        response = client.get(
            self.conversation_path(self.CONVERSATION_ID),
            headers=self._headers("audit:read"),
        )

        assert response.status_code == 403

    def test_scoped_caller_is_admitted_and_identity_comes_from_headers(self) -> None:
        """Trusted headers win over query params, exactly as ``scoped_identity`` promises."""

        client, store = self.client()
        self.seed_conversation(store)
        self.seed_run(store)
        self.seed_snapshot(
            store,
            model_call_id="call_1",
            created_at=datetime.now(timezone.utc),
        )

        response = client.get(
            self.run_path(self.RUN_ID),
            headers=self._headers("runtime:use"),
            # An attacker-supplied tenant on the query string must be ignored.
            params={"org_id": self.OTHER_ORG_ID, "user_id": self.OTHER_USER_ID},
        )

        assert response.status_code == 200, response.text
        assert [row["model_call_id"] for row in response.json()["snapshots"]] == [
            "call_1"
        ]
