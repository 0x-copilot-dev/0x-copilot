"""File-store corruption/backup recovery DRILL — the automated safety net.

Postgres has an automated restore drill (``postgres-restore-drill.yml`` proves
the documented restore procedure actually works). The file store had the *parts*
of a recovery story — ``repair.py`` (:class:`StoreRepair`) plus the live store's
fail-closed reads and ``store_health()`` — but **no automated exercise of the
whole loop end to end**. Each piece was unit-tested in isolation
(``test_repair.py`` writes JSONL by hand; ``test_jsonl_corruption.py`` proves the
store refuses to open). Nothing tied them together into the operator drill that
matters: *seed a real store, corrupt it so the live store is genuinely stuck,
run the repair tool, and prove the store reopens and serves the recovered data.*

That end-to-end loop is what "we can recover a corrupt file store" means as a
control (CLAUDE.md §Compliance reviews: a control counts only when code, config,
tests, and docs all support it — architecture intent is not enough). This module
is that drill. It drives everything through the **real** ``FileRuntimeApiStore``
and its coordinators — no hand-written JSONL for the golden data — so a
regression in the store's open/replay path, the fail-closed contract, the
diagnosis, or the guarded quarantine all fail this one test.

Two scenarios, mirroring the two JSONL failure shapes:

* **Interior corruption** (a torn line with committed data after it): the live
  store *fails closed* on reopen — the "stuck chat" state. The drill then runs
  ``diagnose`` → ``salvage_export`` → ``quarantine_corrupt_tail`` and proves the
  store **reopens** and serves the recoverable prefix, with the conservative
  fail-closed guarantee (everything from the corruption point on is quarantined,
  never trusted back into canonical history) and the run/message/conversation
  records intact.
* **Torn tail** (a partial final append from a crash): benign. The store reopens
  *without any repair* and ``diagnose`` reports it healthy — pinning that the
  drill distinguishes a benign crash-tail from real corruption and never
  quarantines data it does not have to.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.file._jsonl import JsonlCorruptionError, JsonlIo
from runtime_adapters.file.repair import JsonlLineKind, StoreRepair
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
)

_ORG = "org_drill"
_USER = "user_drill"


class RecoveryDrillMixin:
    """Seed a real file store through the coordinators, then corrupt it on disk."""

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    @classmethod
    async def _seed_store(
        cls, store: FileRuntimeApiStore, *, event_count: int
    ) -> tuple[str, str]:
        """Create a conversation + run and append ``event_count`` main events.

        Returns ``(conversation_id, run_id)``. Everything goes through the real
        coordinators + ``append_event`` so ``events.jsonl`` / ``messages.jsonl``
        / ``runs.jsonl`` hold genuine store output, not a hand-built fixture.
        """

        settings = cls._settings()
        event_producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=run_coordinator
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG, user_id=_USER, assistant_id="assistant"
            )
        )
        run = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=_ORG,
                user_id=_USER,
                user_input="Hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        for i in range(event_count):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=_ORG,
                    run_id=run.run_id,
                    conversation_id=conversation.conversation_id,
                    trace_id="trace_drill",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    summary=f"chunk-{i}",
                )
            )
        return conversation.conversation_id, run.run_id

    @staticmethod
    def _content_lines(path: Path) -> list[str]:
        return [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @classmethod
    def _inject_interior_corruption(cls, path: Path) -> int:
        """Corrupt the second-to-last committed line of ``path``.

        Guarantees exactly one valid committed line *after* the malformed one, so
        the failure is unambiguously interior corruption (not a torn tail) no
        matter how many events the store wrote. Returns the good-prefix length
        (number of committed records before the corruption point).
        """

        lines = cls._content_lines(path)
        assert len(lines) >= 3, f"drill needs >=3 committed lines in {path.name}"
        corrupt_index = len(lines) - 2
        lines[corrupt_index] = "{ this line is not valid json"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return corrupt_index


class TestInteriorCorruptionRecoveryDrill(RecoveryDrillMixin):
    async def test_stuck_store_is_diagnosed_salvaged_repaired_and_reopens(
        self, tmp_path
    ) -> None:
        root = tmp_path / "store"

        # --- 1. seed a real store, capture the golden committed history --------
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation_id, run_id = await self._seed_store(store, event_count=4)
        golden_events = await store.list_events_after(
            org_id=_ORG, run_id=run_id, after_sequence=0
        )
        golden_seqs = [e.sequence_no for e in golden_events]
        golden_messages = await store.list_messages(
            org_id=_ORG, conversation_id=conversation_id, limit=50
        )
        assert golden_messages, "seed should have written the user message"
        await store.close()

        events_path = store.layout.events_path(_ORG, conversation_id)
        good_prefix = self._inject_interior_corruption(events_path)
        assert 0 < good_prefix < len(golden_seqs)

        # --- 2. the live store is now STUCK: reopen fails closed ---------------
        stuck = FileRuntimeApiStore(root)
        try:
            await stuck.open()
            raise AssertionError("interior corruption must fail the live open closed")
        except JsonlCorruptionError:
            pass

        # store_health() is a non-raising diagnosis and works even after a
        # failed open — it is how the UI answers "which chat needs repair?".
        health = await stuck.store_health()
        assert not health.healthy
        # store_health() reports only the conversations that need repair.
        assert any(
            c.conversation_id == conversation_id and c.needs_repair
            for c in health.conversations
        )

        # --- 3. diagnose: exception-free, precise verdict ----------------------
        repair = StoreRepair(root)
        diagnosis = repair.diagnose()
        assert not diagnosis.healthy
        (conv_diag,) = diagnosis.conversations
        assert conv_diag.needs_repair
        events_stream = next(
            s
            for s in conv_diag.streams
            if s.relative_path.endswith(store.layout.EVENTS_FILE)
        )
        assert events_stream.malformed_kind is JsonlLineKind.INTERIOR_CORRUPT
        assert events_stream.recovered_records == good_prefix
        # Exactly the one committed line after the corruption survives as an
        # unverified, out-of-canonical-order salvageable tail.
        assert events_stream.salvageable_tail_lines == 1

        # --- 4. salvage_export: recover the good prefix verbatim to a bundle ---
        bundle = tmp_path / "salvage-bundle"
        report = repair.salvage_export(bundle)
        assert report.records_recovered >= good_prefix
        (dropped,) = report.dropped
        assert dropped.reason is JsonlLineKind.INTERIOR_CORRUPT
        recovered_events_file = (
            bundle
            / "conversations"
            / events_path.parent.name
            / store.layout.EVENTS_FILE
        )
        recovered = list(JsonlIo.iter_lines(recovered_events_file))
        # Byte-for-byte the good prefix — never re-serialized or reordered.
        assert len(recovered) == good_prefix
        assert (bundle / "SALVAGE_REPORT.json").exists()

        # --- 5. guarded in-place repair: quarantine the corrupt tail -----------
        # Precondition: the raw stream still fails the fail-closed read contract.
        try:
            list(JsonlIo.iter_lines(events_path))
            raise AssertionError("stream should still be corrupt before quarantine")
        except JsonlCorruptionError:
            pass

        conv_dir = store.layout.conversation_dir(_ORG, conversation_id)
        result = repair.quarantine_corrupt_tail(conv_dir)
        assert result.changed
        (moved,) = result.streams
        assert moved.good_prefix_records == good_prefix
        # The corrupt tail was MOVED aside, never hard-deleted.
        corrupt_sidecars = list((conv_dir / ".corrupt").iterdir())
        assert len(corrupt_sidecars) == 1
        # The canonical stream now reads cleanly as exactly the good prefix.
        assert len(list(JsonlIo.iter_lines(events_path))) == good_prefix

        # --- 6. RECOVERY COMPLETE: the live store reopens and serves data ------
        recovered_store = FileRuntimeApiStore(root)
        await recovered_store.open()  # must NOT raise now
        try:
            events_after = await recovered_store.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=0
            )
            seqs_after = [e.sequence_no for e in events_after]
            # Conservative recovery: exactly the trusted prefix survives, in
            # order, gap-free — everything from the corruption point on is gone
            # from canonical history (preserved only in the .corrupt sidecar).
            assert seqs_after == golden_seqs[:good_prefix]
            assert seqs_after == sorted(seqs_after) == sorted(set(seqs_after))

            # The conversation, its run, and its messages all survived intact.
            conv = await recovered_store.get_conversation(
                org_id=_ORG, user_id=_USER, conversation_id=conversation_id
            )
            assert conv is not None and conv.conversation_id == conversation_id
            run = await recovered_store.get_run(org_id=_ORG, run_id=run_id)
            assert run is not None
            messages_after = await recovered_store.list_messages(
                org_id=_ORG, conversation_id=conversation_id, limit=50
            )
            assert [m.model_dump(mode="json") for m in messages_after] == [
                m.model_dump(mode="json") for m in golden_messages
            ]

            # The repaired conversation is now healthy per the store's own verdict.
            post_health = await recovered_store.store_health()
            assert post_health.healthy
        finally:
            await recovered_store.close()


class TestTornTailIsBenign(RecoveryDrillMixin):
    async def test_torn_crash_tail_reopens_without_repair(self, tmp_path) -> None:
        root = tmp_path / "store"
        store = FileRuntimeApiStore(root)
        await store.open()
        conversation_id, run_id = await self._seed_store(store, event_count=3)
        committed = len(
            await store.list_events_after(org_id=_ORG, run_id=run_id, after_sequence=0)
        )
        await store.close()

        # Simulate a crash mid-append: a partial final line, no newline. This was
        # never durably committed, so it is a benign torn tail — NOT corruption.
        events_path = store.layout.events_path(_ORG, conversation_id)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write('{"partial": "torn crash write')

        # diagnose classifies it as a torn tail and reports the store healthy.
        diagnosis = StoreRepair(root).diagnose()
        assert diagnosis.healthy
        (conv_diag,) = diagnosis.conversations
        events_stream = next(
            s
            for s in conv_diag.streams
            if s.relative_path.endswith(store.layout.EVENTS_FILE)
        )
        assert events_stream.malformed_kind is JsonlLineKind.TORN_TAIL
        assert events_stream.healthy

        # The live store reopens with NO repair step and drops only the torn tail.
        reopened = FileRuntimeApiStore(root)
        await reopened.open()  # must not raise
        try:
            events = await reopened.list_events_after(
                org_id=_ORG, run_id=run_id, after_sequence=0
            )
            assert len(events) == committed
            # No quarantine sidecar was created — we never touch benign data.
            conv_dir = reopened.layout.conversation_dir(_ORG, conversation_id)
            assert not (conv_dir / ".corrupt").exists()
        finally:
            await reopened.close()
