"""A steer must survive the *file* queue, not just the in-memory one.

The file store is the desktop's default backend, and it is the one adapter where
a command is serialised to JSONL and reconstructed by a different process. The
in-memory queue keeps the command object it was handed, so every assertion made
against it is blind to exactly the failure this test is for: a nested
``SteeringMessage`` that JSON-encodes fine and does not validate back — the
message the user typed lost between the accept and the claim, with the run still
reporting success.

So this drives the real durability path end to end: enqueue, restart the store
from the same directory (proving the row is on disk and not in a process's
memory), claim through ``claim_next``, and rebuild the command through
``RuntimeWorker._command_payload`` — the worker's own unwrapper, not a
hand-written copy of it. That distinction matters here: a claim's payload
carries queue-envelope keys (``command_type``, ``approval_id``) that every
command contract forbids, so a test that validated ``claim.payload`` directly
would fail against perfectly good production code and teach the wrong lesson.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.execution.run_steering import SteeringMessage
from agent_runtime.persistence.constants import Values as PersistenceValues
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import RuntimeSteerCommand
from runtime_worker.loop import RuntimeWorker


class FileQueueSteerMixin:
    ORG_ID = "org_1"
    USER_ID = "user_1"
    RUN_ID = "run_1"
    STEER_TEXT = "Actually, only look at EU launches."

    @classmethod
    def command(cls) -> RuntimeSteerCommand:
        return RuntimeSteerCommand(
            run_id=cls.RUN_ID,
            org_id=cls.ORG_ID,
            requested_by_user_id=cls.USER_ID,
            steer=SteeringMessage(
                text=cls.STEER_TEXT, requested_by_user_id=cls.USER_ID
            ),
        )

    @staticmethod
    def lock_expiry() -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=60)


class TestSteerCommandSurvivesTheFileQueue(FileQueueSteerMixin):
    async def test_a_steer_round_trips_through_disk_into_a_claimable_command(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"
        original = self.command()
        accepting = FileRuntimeApiStore(root)
        await accepting.open()
        try:
            await accepting.enqueue_steer(original)
        finally:
            await accepting.close()

        # A *different* store instance over the same directory: this is the
        # in-memory adapter's blind spot, and the desktop's actual topology
        # after a restart.
        replayed = FileRuntimeApiStore(root)
        await replayed.open()
        try:
            claim = await replayed.claim_next(
                worker_id="worker_1", lock_expires_at=self.lock_expiry()
            )
        finally:
            await replayed.close()

        assert claim is not None
        assert claim.command_type == PersistenceValues.EventType.RUN_STEER_REQUESTED
        assert claim.run_id == self.RUN_ID
        rebuilt = RuntimeSteerCommand.model_validate(
            RuntimeWorker._command_payload(claim)
        )
        assert rebuilt.steer.text == self.STEER_TEXT
        assert rebuilt.steer.steer_id == original.steer.steer_id
        assert rebuilt.requested_by_user_id == self.USER_ID
