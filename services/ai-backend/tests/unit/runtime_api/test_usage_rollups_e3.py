"""Seeded multi-run usage rollup proof (PRD-E3 D3.3, DoD usage item).

Two users × two conversations × three runs, with per-call rows spanning the
purposes ``main`` / ``subagent_work`` / ``view_shaping`` / ``shape_request``.
Per-user, per-conversation and per-run totals each equal the independent sum of
the seeded rows; ``by_call`` rows carry the new ``purpose`` + ``surface_id``
axes; and ``/v1/usage/org/purpose`` buckets the v2 shaping purposes. Asserted at
the runtime_api boundary — the facade re-asserts passthrough (T7/T8).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    UsageAttributionEdge,
    UsageAttributionRelationship,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import ConversationRecord

_ORG = "org_e3u"
_USER_1 = "user_1"
_USER_2 = "user_2"
_NOW = datetime.now(timezone.utc) - timedelta(hours=1)


def _headers(
    user_id: str = _USER_1,
    *,
    scopes: str = "runtime:use,audit:read",
) -> dict[str, str]:
    return {
        "x-enterprise-org-id": _ORG,
        "x-enterprise-user-id": user_id,
        "x-enterprise-permission-scopes": scopes,
    }


class _Call:
    """One seeded per-call row's fixed inputs (tokens sum to total)."""

    def __init__(
        self,
        *,
        run_id: str,
        conversation_id: str,
        user_id: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        surface_id: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.purpose = purpose
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.surface_id = surface_id


# r1, r2 → user_1 / conv-1; r3 → user_2 / conv-2. Purposes span the v2 set.
_CALLS: tuple[_Call, ...] = (
    _Call(
        run_id="r1",
        conversation_id="conv-1",
        user_id=_USER_1,
        purpose="main",
        input_tokens=100,
        output_tokens=50,
    ),
    _Call(
        run_id="r1",
        conversation_id="conv-1",
        user_id=_USER_1,
        purpose="view_shaping",
        input_tokens=10,
        output_tokens=5,
        surface_id="record://s1",
    ),
    _Call(
        run_id="r1",
        conversation_id="conv-1",
        user_id=_USER_1,
        purpose="shape_request",
        input_tokens=20,
        output_tokens=8,
        surface_id="record://s2",
    ),
    _Call(
        run_id="r2",
        conversation_id="conv-1",
        user_id=_USER_1,
        purpose="main",
        input_tokens=40,
        output_tokens=20,
    ),
    _Call(
        run_id="r2",
        conversation_id="conv-1",
        user_id=_USER_1,
        purpose="subagent_work",
        input_tokens=30,
        output_tokens=15,
    ),
    _Call(
        run_id="r3",
        conversation_id="conv-2",
        user_id=_USER_2,
        purpose="main",
        input_tokens=200,
        output_tokens=100,
    ),
    _Call(
        run_id="r3",
        conversation_id="conv-2",
        user_id=_USER_2,
        purpose="view_shaping",
        input_tokens=15,
        output_tokens=7,
        surface_id="record://s3",
    ),
)

_RUN_META = {
    "r1": ("conv-1", _USER_1),
    "r2": ("conv-1", _USER_1),
    "r3": ("conv-2", _USER_2),
}


def _seeded_client_and_store() -> tuple[TestClient, InMemoryRuntimeApiStore]:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )
    for conversation_id, user_id in {
        conversation_id: user_id for conversation_id, user_id in _RUN_META.values()
    }.items():
        store.conversations[conversation_id] = ConversationRecord(
            conversation_id=conversation_id,
            org_id=_ORG,
            user_id=user_id,
            assistant_id="assistant-1",
            created_at=_NOW - timedelta(hours=1),
            updated_at=_NOW,
        )
    # Per-call rows.
    for index, call in enumerate(_CALLS):
        store.model_call_usage.append(
            RuntimeModelCallUsageRecord(
                id=f"call-{index}",
                org_id=_ORG,
                run_id=call.run_id,
                conversation_id=call.conversation_id,
                trace_id=f"trace-{call.run_id}",
                user_id=call.user_id,
                purpose=call.purpose,
                surface_id=call.surface_id,
                model_provider="openai",
                model_name="gpt-5.4-mini",
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cached_input_tokens=0,
                total_tokens=call.input_tokens + call.output_tokens,
                duration_ms=500,
                created_at=_NOW,
            )
        )
    # Per-run rollup rows = the independent sum of that run's calls.
    for run_id, (conversation_id, user_id) in _RUN_META.items():
        calls = [c for c in _CALLS if c.run_id == run_id]
        store.run_usage[run_id] = RuntimeRunUsageRecord(
            id=run_id,
            org_id=_ORG,
            user_id=user_id,
            conversation_id=conversation_id,
            run_id=run_id,
            model_provider="openai",
            model_name="gpt-5.4-mini",
            input_tokens=sum(c.input_tokens for c in calls),
            output_tokens=sum(c.output_tokens for c in calls),
            cached_input_tokens=0,
            total_tokens=sum(c.input_tokens + c.output_tokens for c in calls),
            chunk_count=1,
            duration_ms=1500,
            started_at=_NOW - timedelta(seconds=2),
            completed_at=_NOW,
            status="completed",
        )
    ports = RuntimeAdapterFactory.from_store(store)
    client = TestClient(RuntimeApiAppFactory.create_app(ports=ports, settings=settings))
    client.headers.update(_headers())
    return client, store


def _seeded_client() -> TestClient:
    return _seeded_client_and_store()[0]


def _sum(calls, field: str) -> int:
    return sum(getattr(c, field) for c in calls)


class TestPerRunRollup:
    def test_run_total_equals_independent_sum(self) -> None:
        client = _seeded_client()
        r1_calls = [c for c in _CALLS if c.run_id == "r1"]
        response = client.get("/v1/usage/runs/r1")
        assert response.status_code == 200
        total = response.json()["total"]
        assert total["input"] == _sum(r1_calls, "input_tokens")  # 130
        assert total["output"] == _sum(r1_calls, "output_tokens")  # 63
        assert total["total"] == _sum(r1_calls, "input_tokens") + _sum(
            r1_calls, "output_tokens"
        )

    def test_by_call_carries_purpose_and_surface_id(self) -> None:
        client = _seeded_client()
        response = client.get("/v1/usage/runs/r1")
        by_call = response.json()["by_call"]
        assert len(by_call) == 3
        by_purpose = {row["purpose"]: row for row in by_call}
        assert set(by_purpose) == {"main", "view_shaping", "shape_request"}
        # ``main`` is not normalized to ``run`` — the usage-row query dimension.
        assert by_purpose["main"]["surface_id"] is None
        assert by_purpose["view_shaping"]["surface_id"] == "record://s1"
        assert by_purpose["shape_request"]["surface_id"] == "record://s2"

    async def test_call_edges_and_operation_totals_dedupe_canonical_usage(self) -> None:
        client, store = _seeded_client_and_store()
        # Give every r1 call an independent canonical price. Multiple edges
        # must not multiply call-0's 500 micro-USD cost in either projection.
        costs = {"call-0": 500, "call-1": 50, "call-2": 20}
        store.model_call_usage = [
            row.model_copy(update={"cost_micro_usd": costs.get(row.id)})
            for row in store.model_call_usage
        ]
        store.run_usage["r1"] = store.run_usage["r1"].model_copy(
            update={"cost_micro_usd": sum(costs.values())}
        )
        # Two target links for the same canonical provider invocation.  The
        # operation total must count call-0 once, and the run total must remain
        # the independently seeded run-usage total.
        assert await store.append_usage_attribution_edge(
            org_id=_ORG,
            edge=UsageAttributionEdge(
                edge_id="edge-artifact",
                usage_record_id="call-0",
                operation_id="operation-1",
                artifact_id="artifact-1",
                relationship=UsageAttributionRelationship.PRODUCED,
                created_at=_NOW,
            ),
        )
        assert await store.append_usage_attribution_edge(
            org_id=_ORG,
            edge=UsageAttributionEdge(
                edge_id="edge-stage",
                usage_record_id="call-0",
                operation_id="operation-1",
                stage_id="stage-1",
                relationship=UsageAttributionRelationship.PROPOSED,
                created_at=_NOW,
            ),
        )
        # A no-edge historical row (call-1) remains visible and contributes to
        # the canonical run total exactly as it did before attribution existed.
        run_response = client.get("/v1/usage/runs/r1")
        assert run_response.status_code == 200
        body = run_response.json()
        assert body["total"]["input"] == 130
        assert body["total"]["output"] == 63
        assert body["total"]["cost_micro_usd"] == 570
        assert len(body["by_call"]) == 3
        call_0 = next(row for row in body["by_call"] if row["id"] == "call-0")
        assert {edge["edge_id"] for edge in call_0["attribution_edges"]} == {
            "edge-artifact",
            "edge-stage",
        }
        operation = body["by_operation"]
        assert len(operation) == 1
        assert operation[0]["operation_id"] == "operation-1"
        assert operation[0]["total"] == {
            "input": 100,
            "output": 50,
            "cached_input": 0,
            "total": 150,
            "runs_count": 1,
            "cost_micro_usd": 500,
        }

        calls_response = client.get(
            "/v1/usage/runs/r1/calls",
        )
        assert calls_response.status_code == 200
        assert calls_response.json()["run_id"] == "r1"
        assert [row["id"] for row in calls_response.json()["calls"]] == [
            "call-0",
            "call-1",
            "call-2",
        ]

    def test_foreign_user_gets_404_for_run_and_conversation_usage(self) -> None:
        client = _seeded_client()
        run_response = client.get("/v1/usage/runs/r1", headers=_headers(_USER_2))
        conversation_response = client.get(
            "/v1/usage/conversations/conv-1",
            params={"period": "30d"},
            headers=_headers(_USER_2),
        )
        assert run_response.status_code == 404
        assert conversation_response.status_code == 404


class TestPerUserRollup:
    def test_user_1_total(self) -> None:
        client = _seeded_client()
        user_1_calls = [c for c in _CALLS if c.user_id == _USER_1]
        response = client.get(
            "/v1/usage/me",
            params={"period": "30d"},
        )
        assert response.status_code == 200
        total = response.json()["total"]
        assert total["runs_count"] == 2  # r1 + r2
        assert total["input"] == _sum(user_1_calls, "input_tokens")  # 200
        assert total["output"] == _sum(user_1_calls, "output_tokens")  # 98

    def test_user_2_total_isolated_from_user_1(self) -> None:
        client = _seeded_client()
        user_2_calls = [c for c in _CALLS if c.user_id == _USER_2]
        response = client.get(
            "/v1/usage/me",
            params={"period": "30d"},
            headers=_headers(_USER_2),
        )
        total = response.json()["total"]
        assert total["runs_count"] == 1  # r3 only
        assert total["input"] == _sum(user_2_calls, "input_tokens")  # 215


class TestPerConversationRollup:
    def test_conversation_total_equals_sum_of_its_runs(self) -> None:
        client = _seeded_client()
        conv_1_calls = [c for c in _CALLS if c.conversation_id == "conv-1"]
        response = client.get(
            "/v1/usage/conversations/conv-1",
            params={"period": "30d"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"]["input"] == _sum(conv_1_calls, "input_tokens")  # 200
        assert body["total"]["output"] == _sum(conv_1_calls, "output_tokens")  # 98
        assert body["total"]["runs_count"] == 2
        assert {row["run_id"] for row in body["by_run"]} == {"r1", "r2"}

    def test_conversation_does_not_leak_across_conversations(self) -> None:
        client = _seeded_client()
        conv_2_calls = [c for c in _CALLS if c.conversation_id == "conv-2"]
        response = client.get(
            "/v1/usage/conversations/conv-2",
            params={"period": "30d"},
            headers=_headers(_USER_2),
        )
        body = response.json()
        assert body["total"]["input"] == _sum(conv_2_calls, "input_tokens")  # 215
        assert {row["run_id"] for row in body["by_run"]} == {"r3"}


class TestOrgPurposeRollup:
    def test_v2_purposes_bucket(self) -> None:
        client = _seeded_client()
        response = client.get("/v1/usage/org/purpose", params={"period": "30d"})
        assert response.status_code == 200
        by_purpose = {row["purpose"]: row for row in response.json()["rows"]}
        # The v2 shaping purposes flow through as string dimensions untouched.
        assert "view_shaping" in by_purpose
        assert "shape_request" in by_purpose
        assert "subagent_work" in by_purpose
        vs_calls = [c for c in _CALLS if c.purpose == "view_shaping"]
        assert by_purpose["view_shaping"]["call_count"] == len(vs_calls)  # 2
        assert by_purpose["view_shaping"]["input"] == _sum(vs_calls, "input_tokens")
        assert by_purpose["shape_request"]["call_count"] == 1
