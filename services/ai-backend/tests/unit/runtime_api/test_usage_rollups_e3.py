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
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory

_ORG = "org_e3u"
_USER_1 = "user_1"
_USER_2 = "user_2"
_NOW = datetime.now(timezone.utc) - timedelta(hours=1)


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


def _seeded_client() -> TestClient:
    store = InMemoryRuntimeApiStore()
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
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
    return TestClient(RuntimeApiAppFactory.create_app(ports=ports, settings=settings))


def _sum(calls, field: str) -> int:
    return sum(getattr(c, field) for c in calls)


class TestPerRunRollup:
    def test_run_total_equals_independent_sum(self) -> None:
        client = _seeded_client()
        r1_calls = [c for c in _CALLS if c.run_id == "r1"]
        response = client.get(
            "/v1/usage/runs/r1", params={"org_id": _ORG, "user_id": _USER_1}
        )
        assert response.status_code == 200
        total = response.json()["total"]
        assert total["input"] == _sum(r1_calls, "input_tokens")  # 130
        assert total["output"] == _sum(r1_calls, "output_tokens")  # 63
        assert total["total"] == _sum(r1_calls, "input_tokens") + _sum(
            r1_calls, "output_tokens"
        )

    def test_by_call_carries_purpose_and_surface_id(self) -> None:
        client = _seeded_client()
        response = client.get(
            "/v1/usage/runs/r1", params={"org_id": _ORG, "user_id": _USER_1}
        )
        by_call = response.json()["by_call"]
        assert len(by_call) == 3
        by_purpose = {row["purpose"]: row for row in by_call}
        assert set(by_purpose) == {"main", "view_shaping", "shape_request"}
        # ``main`` is not normalized to ``run`` — the usage-row query dimension.
        assert by_purpose["main"]["surface_id"] is None
        assert by_purpose["view_shaping"]["surface_id"] == "record://s1"
        assert by_purpose["shape_request"]["surface_id"] == "record://s2"


class TestPerUserRollup:
    def test_user_1_total(self) -> None:
        client = _seeded_client()
        user_1_calls = [c for c in _CALLS if c.user_id == _USER_1]
        response = client.get(
            "/v1/usage/me",
            params={"org_id": _ORG, "user_id": _USER_1, "period": "30d"},
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
            params={"org_id": _ORG, "user_id": _USER_2, "period": "30d"},
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
            params={"org_id": _ORG, "user_id": _USER_1, "period": "30d"},
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
            params={"org_id": _ORG, "user_id": _USER_2, "period": "30d"},
        )
        body = response.json()
        assert body["total"]["input"] == _sum(conv_2_calls, "input_tokens")  # 215
        assert {row["run_id"] for row in body["by_run"]} == {"r3"}


class TestOrgPurposeRollup:
    def test_v2_purposes_bucket(self) -> None:
        client = _seeded_client()
        response = client.get(
            "/v1/usage/org/purpose", params={"org_id": _ORG, "period": "30d"}
        )
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
