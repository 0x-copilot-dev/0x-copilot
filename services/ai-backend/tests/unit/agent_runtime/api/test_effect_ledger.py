"""Runtime adapter tests for universal-effect structural stage facts."""

from __future__ import annotations

import pytest

from agent_runtime.api.effect_ledger import RuntimeEffectLedger
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.effects.contracts import EffectStageScope
from agent_runtime.effects.errors import EffectStageIdempotencyConflict
from agent_runtime.execution.contracts import AgentRuntimeContext
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord

pytestmark = pytest.mark.anyio

_ORG = "org_effect_ledger"
_USER = "user_effect_ledger"
_RUN = "run_effect_ledger"
_OWNER = "principal://users/user-effect-ledger"
_STAGE = "stg_00000000-0000-4000-8000-000000000001"
_OPERATION = "op_00000000-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _run() -> RunRecord:
    return RunRecord(
        run_id=_RUN,
        conversation_id="conv_effect_ledger",
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_effect_ledger",
        trace_id="trace_effect_ledger",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_effect_ledger",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


def _staged_payload(*, display_target: str = "Demo target") -> dict[str, object]:
    return {
        "v": 1,
        "stage_id": _STAGE,
        "operation_id": _OPERATION,
        "executor": "mcp",
        "capability": "demo-capability",
        "op": "mutate",
        "target_ref": "mcp-target://capability/target-token",
        "target_digest": "b" * 64,
        "display_target": display_target,
        "proposal_kind": "canonical_arguments",
        "proposal_ref": f"proposal://{_STAGE}/revisions/1",
        "proposal_content_ref": (
            "artifact://art_00000000-0000-4000-8000-000000000001/revisions/1"
        ),
        "proposal_digest": "a" * 64,
        "proposal_media_type": "application/json",
        "precondition_ref": "precondition://targets/current-token",
        "precondition_digest": "c" * 64,
        "effect_class": "external_reversible",
        "policy_snapshot_ref": "policy://runs/effect-ledger/snapshot-1",
        "policy": "ask",
        "agent_hold": True,
        "safe_summary_ref": "summary://stages/effect-ledger/1",
        "owner_ref": _OWNER,
        "author_actor": "system",
        "author_ref": "principal://agents/assistant-1",
        "created_at": "2026-07-24T00:00:00+00:00",
    }


class TestRuntimeEffectLedger:
    async def test_replays_same_semantic_event_and_returns_structural_row(self) -> None:
        store = InMemoryRuntimeApiStore()
        run = _run()
        store.runs[run.run_id] = run
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        ledger = RuntimeEffectLedger(
            event_producer=producer,
            run=run,
            owner_ref=_OWNER,
        )
        scope = EffectStageScope(run_id=_RUN, owner_ref=_OWNER)

        first = await ledger.append_stage_event(
            scope=scope,
            event_type="effect.staged",
            payload=_staged_payload(),
            idempotency_key="stage-once",
            request_fingerprint="f" * 64,
        )
        replay = await ledger.append_stage_event(
            scope=scope,
            event_type="effect.staged",
            payload=_staged_payload(),
            idempotency_key="stage-once",
            request_fingerprint="f" * 64,
        )

        assert replay == first
        assert first.ledger_id == "rrun\u00b7001"
        assert first.payload["proposal_content_ref"].startswith("artifact://")
        events = await ledger.list_stage_events(scope=scope, stage_id=_STAGE)
        assert events == (first,)

    async def test_changed_fingerprint_for_same_key_fails_closed(self) -> None:
        store = InMemoryRuntimeApiStore()
        run = _run()
        store.runs[run.run_id] = run
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        ledger = RuntimeEffectLedger(
            event_producer=producer,
            run=run,
            owner_ref=_OWNER,
        )
        scope = EffectStageScope(run_id=_RUN, owner_ref=_OWNER)
        await ledger.append_stage_event(
            scope=scope,
            event_type="effect.staged",
            payload=_staged_payload(),
            idempotency_key="same-key",
            request_fingerprint="f" * 64,
        )

        with pytest.raises(EffectStageIdempotencyConflict):
            await ledger.append_stage_event(
                scope=scope,
                event_type="effect.staged",
                payload=_staged_payload(),
                idempotency_key="same-key",
                request_fingerprint="g" * 64,
            )

    async def test_scope_and_unsafe_payload_fail_before_append(self) -> None:
        store = InMemoryRuntimeApiStore()
        run = _run()
        store.runs[run.run_id] = run
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        ledger = RuntimeEffectLedger(
            event_producer=producer,
            run=run,
            owner_ref=_OWNER,
        )
        scope = EffectStageScope(run_id=_RUN, owner_ref=_OWNER)

        with pytest.raises(ValueError, match="scope"):
            await ledger.list_stage_events(
                scope=EffectStageScope(
                    run_id=_RUN,
                    owner_ref="principal://users/other-user",
                ),
                stage_id=_STAGE,
            )
        payload = _staged_payload()
        payload["proposal_content_ref"] = "file:///tmp/proposal.json"
        with pytest.raises(ValueError):
            await ledger.append_stage_event(
                scope=scope,
                event_type="effect.staged",
                payload=payload,
                idempotency_key="unsafe-content",
                request_fingerprint="h" * 64,
            )
        raw_payload = _staged_payload()
        raw_payload["raw_proposal_body"] = "delete everything"
        with pytest.raises(ValueError):
            await ledger.append_stage_event(
                scope=scope,
                event_type="effect.staged",
                payload=raw_payload,
                idempotency_key="raw-body",
                request_fingerprint="i" * 64,
            )
        assert store.events_by_run.get(_RUN, []) == []
