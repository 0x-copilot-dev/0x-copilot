from __future__ import annotations

import pytest

from agent_runtime.api.effect_commit_queue import RuntimeEffectCommitOutbox
from agent_runtime.effects.contracts import EffectCommitCommand
from agent_runtime.effects.executor import EffectExecutionScope
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore


def _command(*, run_id: str = "run_effect_queue") -> EffectCommitCommand:
    return EffectCommitCommand(
        run_id=run_id,
        stage_id="stg_123e4567-e89b-42d3-a456-426614174000",
        revision=1,
        decision_ledger_id="rabc\u00b71",
        proposal_digest="a" * 64,
        target_digest="b" * 64,
        idempotency_key="effect-approval-1",
    )


@pytest.mark.asyncio
async def test_outbox_enqueues_trusted_scope_and_stable_command_id() -> None:
    store = InMemoryRuntimeApiStore()
    outbox = RuntimeEffectCommitOutbox(
        queue=store,
        scope=EffectExecutionScope(
            org_id="org_a",
            user_id="user_a",
            conversation_id="conv_a",
            run_id="run_effect_queue",
            owner_ref="principal://user_a",
        ),
    )

    command = _command()
    await outbox.enqueue_after_decision(command)
    await outbox.enqueue_after_decision(command)

    first, second = store.effect_commit_commands
    assert first.command_id == second.command_id
    assert first.org_id == "org_a"
    assert first.user_id == "user_a"
    assert first.conversation_id == "conv_a"
    assert first.run_id == command.run_id
    assert first.stage_id == command.stage_id
    assert first.proposal_digest == command.proposal_digest
    assert first.target_digest == command.target_digest


@pytest.mark.asyncio
async def test_outbox_rejects_a_command_for_another_run() -> None:
    outbox = RuntimeEffectCommitOutbox(
        queue=InMemoryRuntimeApiStore(),
        scope=EffectExecutionScope(
            org_id="org_a",
            user_id="user_a",
            conversation_id="conv_a",
            run_id="run_effect_queue",
            owner_ref="principal://user_a",
        ),
    )

    with pytest.raises(ValueError, match="trusted outbox scope"):
        await outbox.enqueue_after_decision(_command(run_id="run_other"))


@pytest.mark.asyncio
async def test_outbox_fails_closed_without_a_trusted_conversation() -> None:
    outbox = RuntimeEffectCommitOutbox(
        queue=InMemoryRuntimeApiStore(),
        scope=EffectExecutionScope(
            org_id="org_a",
            user_id="user_a",
            conversation_id=None,
            run_id="run_effect_queue",
            owner_ref="principal://user_a",
        ),
    )

    with pytest.raises(ValueError, match="requires a conversation"):
        await outbox.enqueue_after_decision(_command())
