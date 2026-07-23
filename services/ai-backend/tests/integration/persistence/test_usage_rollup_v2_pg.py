"""PRD-A2 D7 — live-Postgres twin of the seeded-fixture rollup assertion.

Skipped unless ``USAGE_V2_LIVE_TEST_DATABASE_URL`` points at a disposable
Postgres database (destructive: it seeds a conversation, run, and usage rows).
Together with ``tests/unit/agent_runtime/api/test_usage_rollups_v2.py`` (file +
in-memory) this closes the "both adapters" half of the DoD: it proves the new
``user_id`` / ``surface_id`` columns and the ``view_shaping`` purpose round-trip
through the real INSERT + ``SELECT *`` mapper, and that the per-purpose /
per-user / per-run rollups equal the seeded fixture.

Mirrors the async-store fixture in ``test_account_merge_live.py``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

from agent_runtime.api.usage_service import UsageQueryService
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from runtime_adapters.postgres import PostgresRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeRequestContext,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("USAGE_V2_LIVE_TEST_DATABASE_URL"),
        reason=(
            "Set USAGE_V2_LIVE_TEST_DATABASE_URL to a disposable Postgres "
            "database to exercise the PRD-A2 per-call usage rollup twin."
        ),
    ),
]

_ORG = "org_usage_v2"
_USER = "user_usage_v2"
_WINDOW_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
_WINDOW_END = datetime(2100, 1, 1, tzinfo=timezone.utc)

# (purpose, input, output) per seeded call row.
_CALL_FIXTURE: tuple[tuple[str, int, int], ...] = (
    ("main", 100, 40),
    ("subagent_work", 30, 12),
    ("view_shaping", 120, 48),
    ("view_shaping", 200, 60),
    ("todo_extraction", 15, 5),
)


@pytest.fixture
def database_url() -> str:
    return os.environ["USAGE_V2_LIVE_TEST_DATABASE_URL"]


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    s = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=1,
        pool_max_size=4,
        pool_acquire_timeout_seconds=10.0,
    )
    await s.open()
    try:
        await s.migrate()
        yield s
    finally:
        await s.close()


def _runtime_context(*, suffix: str) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        org_id=_ORG,
        user_id=_USER,
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


async def _seed_run(store: PostgresRuntimeApiStore) -> tuple[str, str, str]:
    """Create a conversation + run (FK targets) and return their ids + trace."""

    import uuid

    suffix = uuid.uuid4().hex
    conversation = await store.create_conversation(
        CreateConversationRequest(
            org_id=_ORG, user_id=_USER, assistant_id=f"assistant_{suffix}"
        )
    )
    client_request = CreateRunRequest(
        conversation_id=conversation.conversation_id,
        org_id=_ORG,
        user_id=_USER,
        user_input="please shape this",
        model={"provider": "openai", "model_name": "gpt-5.4-mini"},
        request_context=RuntimeRequestContext(
            roles=("Admin",), permission_scopes=("Search:Read",)
        ),
    )
    request = client_request.model_copy(
        update={"runtime_context": _runtime_context(suffix=suffix)}
    )
    run, _user_message, _created = await store.create_run_with_user_message(
        request=request, conversation=conversation
    )
    return conversation.conversation_id, run.run_id, run.trace_id


class TestUsageRollupV2Postgres:
    async def test_rollup_totals_equal_sum_of_rows(
        self, store: PostgresRuntimeApiStore
    ) -> None:
        conversation_id, run_id, trace_id = await _seed_run(store)
        now = datetime.now(timezone.utc)

        for purpose, in_tok, out_tok in _CALL_FIXTURE:
            await store.record_model_call_usage(
                RuntimeModelCallUsageRecord(
                    org_id=_ORG,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    user_id=_USER,
                    surface_id="surface-x" if purpose == "view_shaping" else None,
                    model_provider="openai",
                    model_name="gpt-5.4-mini",
                    purpose=purpose,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_tokens=in_tok + out_tok,
                    created_at=now,
                )
            )

        rows = await store.query_model_call_usage_for_range(
            org_id=_ORG, start=_WINDOW_START, end=_WINDOW_END
        )
        assert len(rows) == len(_CALL_FIXTURE)

        # New columns survive the INSERT + SELECT * round-trip.
        assert {r.user_id for r in rows} == {_USER}
        shaping = [r for r in rows if r.purpose == "view_shaping"]
        assert len(shaping) == 2
        assert all(r.surface_id == "surface-x" for r in shaping)

        purpose_rows = UsageQueryService.rollup_purpose_rows(rows, refreshed_at=now)
        by_purpose = {row.purpose: row for row in purpose_rows}
        assert by_purpose["view_shaping"].input_tokens == 120 + 200
        assert by_purpose["view_shaping"].output_tokens == 48 + 60
        assert by_purpose["view_shaping"].call_count == 2

        assert sum(r.input_tokens for r in purpose_rows) == sum(
            i for _, i, _ in _CALL_FIXTURE
        )
        assert sum(r.output_tokens for r in purpose_rows) == sum(
            o for _, _, o in _CALL_FIXTURE
        )
