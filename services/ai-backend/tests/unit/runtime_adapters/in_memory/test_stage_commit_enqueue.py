"""In-memory queue round-trip for the PRD-D2 ``stage_commit_requested`` command.

The command must enqueue, then claim back with the right command_type + run_id +
payload so the worker's ``_runtime_stage_commit_command`` can rebuild it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.persistence.constants import Values as PersistenceValues
from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
from runtime_api.schemas import RuntimeStageCommitCommand

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _command() -> RuntimeStageCommitCommand:
    return RuntimeStageCommitCommand(
        stage_id="stage_abc",
        run_id="run_1",
        org_id="org_acme",
        user_id="user_sarah",
        conversation_id="conv_1",
        rev=2,
        decision_seq=7,
    )


class TestEnqueueRoundtrip:
    async def test_enqueue_then_claim_rebuilds_command(self) -> None:
        store = InMemoryRuntimeApiStore()
        command = _command()
        await store.enqueue_stage_commit(command)

        # Recorded in the typed list AND on the outbox queue.
        assert store.stage_commit_commands == [command]

        claim = await store.claim_next(
            worker_id="w1",
            lock_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
        assert claim is not None
        assert claim.command_type == (
            PersistenceValues.EventType.STAGE_COMMIT_REQUESTED
        )
        assert claim.run_id == "run_1"
        # The payload round-trips into the command the worker decode expects. The
        # worker's ``_command_payload`` strips the internal ``command_type`` and a
        # ``None`` ``approval_id`` (not a field of this command) before validating.
        rebuilt = RuntimeStageCommitCommand.model_validate(
            {
                k: v
                for k, v in claim.payload.items()
                if k != "command_type" and not (k == "approval_id" and v is None)
            }
        )
        assert rebuilt.stage_id == "stage_abc"
        assert rebuilt.rev == 2
        assert rebuilt.decision_seq == 7
        assert rebuilt.user_id == "user_sarah"
