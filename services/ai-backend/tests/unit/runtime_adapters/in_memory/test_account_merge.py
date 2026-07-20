"""In-memory account-merge re-key tests (account-linking PRD §6.3/§6.4).

Seeds an ABSORBED account, a SURVIVOR account, and a DECOY third account,
runs the re-keyer, and asserts absorbed rows now belong to the survivor
while the decoy and the audit chain stay byte-identical. Event
``sequence_no`` ordering must survive the merge untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.persistence.records import UsageDailyUserRow
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_adapters.in_memory.account_merge import InMemoryAccountMergeRekeyer
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RuntimeApiEventType,
    RuntimeEventDraft,
    WorkspaceDefaultsRecord,
)


class _MergeSeedMixin:
    """Builders for seeding one full account into the in-memory store."""

    _ABSORBED_ORG = "org_absorbed"
    _ABSORBED_USER = "user_absorbed"
    _SURVIVOR_ORG = "org_survivor"
    _SURVIVOR_USER = "user_survivor"
    _DECOY_ORG = "org_decoy"
    _DECOY_USER = "user_decoy"

    @staticmethod
    def _settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )

    def _run_coordinator(self, store: InMemoryRuntimeApiStore) -> RunCoordinator:
        settings = self._settings()
        return RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=RuntimeEventProducer(
                persistence=store, event_store=store, on_event_appended=None
            ),
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )

    def _rekeyer(self) -> InMemoryAccountMergeRekeyer:
        return InMemoryAccountMergeRekeyer(
            absorbed_org_id=self._ABSORBED_ORG,
            absorbed_user_id=self._ABSORBED_USER,
            survivor_org_id=self._SURVIVOR_ORG,
            survivor_user_id=self._SURVIVOR_USER,
        )

    async def _seed_account(
        self,
        store: InMemoryRuntimeApiStore,
        *,
        org_id: str,
        user_id: str,
        events: int = 3,
    ) -> tuple[str, str]:
        """Create one conversation + run + events; return ``(conversation_id, run_id)``."""

        coordinator = self._run_coordinator(store)
        conversation = await store.create_conversation(
            CreateConversationRequest(
                org_id=org_id,
                user_id=user_id,
                assistant_id="assistant",
                title=f"chat-{org_id}",
                idempotency_key=f"idem-{org_id}",
            )
        )
        run = await coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=org_id,
                user_id=user_id,
                user_input="hello",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        for index in range(events):
            await store.append_event(
                RuntimeEventDraft(
                    org_id=org_id,
                    run_id=run.run_id,
                    conversation_id=conversation.conversation_id,
                    trace_id=f"trace-{org_id}",
                    source=StreamEventSource.MAIN_AGENT,
                    event_type=RuntimeApiEventType.MODEL_DELTA,
                    summary=f"event-{index}",
                )
            )
        return conversation.conversation_id, run.run_id


class TestInMemoryAccountMergeRekey(_MergeSeedMixin):
    """Absorbed rows move to the survivor; the decoy account stays put."""

    async def test_absorbed_rows_move_and_decoy_untouched(self) -> None:
        store = InMemoryRuntimeApiStore()
        absorbed_conv, absorbed_run = await self._seed_account(
            store, org_id=self._ABSORBED_ORG, user_id=self._ABSORBED_USER
        )
        _survivor_conv, _survivor_run = await self._seed_account(
            store, org_id=self._SURVIVOR_ORG, user_id=self._SURVIVOR_USER
        )
        decoy_conv, decoy_run = await self._seed_account(
            store, org_id=self._DECOY_ORG, user_id=self._DECOY_USER
        )
        decoy_conversation_before = store.conversations[decoy_conv]
        decoy_run_before = store.runs[decoy_run]
        absorbed_sequences_before = [
            e.sequence_no for e in store.events_by_run[absorbed_run]
        ]
        audit_log_before = list(store.audit_log)

        rekeyer = self._rekeyer()
        rekeyer.rekey_store(store)

        # Conversation, run, and message rows belong to the survivor now —
        # ids untouched, tenancy rewritten, nested run context included.
        conversation = store.conversations[absorbed_conv]
        assert conversation.org_id == self._SURVIVOR_ORG
        assert conversation.user_id == self._SURVIVOR_USER
        run = store.runs[absorbed_run]
        assert run.run_id == absorbed_run
        assert run.org_id == self._SURVIVOR_ORG
        assert run.user_id == self._SURVIVOR_USER
        assert run.runtime_context.org_id == self._SURVIVOR_ORG
        assert run.runtime_context.user_id == self._SURVIVOR_USER
        assert all(m.org_id != self._ABSORBED_ORG for m in store.messages.values())
        # Survivor-scoped reads see the moved conversation.
        assert (
            await store.get_conversation(
                org_id=self._SURVIVOR_ORG,
                user_id=self._SURVIVOR_USER,
                conversation_id=absorbed_conv,
            )
            is not None
        )

        # Event ordering is untouched — sequence_nos are byte-identical.
        assert [
            e.sequence_no for e in store.events_by_run[absorbed_run]
        ] == absorbed_sequences_before

        # Decoy account is byte-identical.
        assert store.conversations[decoy_conv] == decoy_conversation_before
        assert store.runs[decoy_run] == decoy_run_before
        assert store.runs[decoy_run].org_id == self._DECOY_ORG

        # The audit chain is never rewritten by the re-key.
        assert store.audit_log == audit_log_before

        # Counts cover the moved structures.
        assert rekeyer.tables["agent_conversations"] == 1
        assert rekeyer.tables["agent_runs"] == 1
        assert rekeyer.tables["agent_messages"] >= 1

    async def test_second_rekey_is_noop(self) -> None:
        store = InMemoryRuntimeApiStore()
        await self._seed_account(
            store, org_id=self._ABSORBED_ORG, user_id=self._ABSORBED_USER
        )
        self._rekeyer().rekey_store(store)

        second = self._rekeyer()
        second.rekey_store(store)
        assert second.tables == {}
        assert second.warnings == []

    async def test_idempotency_key_collision_drops_absorbed_entry(self) -> None:
        """Same idempotency key on both accounts → survivor entry wins."""

        store = InMemoryRuntimeApiStore()
        for org_id, user_id in (
            (self._ABSORBED_ORG, self._ABSORBED_USER),
            (self._SURVIVOR_ORG, self._SURVIVOR_USER),
        ):
            await store.create_conversation(
                CreateConversationRequest(
                    org_id=org_id,
                    user_id=user_id,
                    assistant_id="assistant",
                    idempotency_key="shared-key",
                )
            )
        survivor_key = (self._SURVIVOR_ORG, self._SURVIVOR_USER, "shared-key")
        survivor_conversation_id = store._conversation_idempotency[survivor_key]

        rekeyer = self._rekeyer()
        rekeyer.rekey_store(store)

        assert store._conversation_idempotency[survivor_key] == (
            survivor_conversation_id
        )
        assert any("idempotency" in warning for warning in rekeyer.warnings)
        # Both conversation ROWS still exist — only the dedup key dropped.
        assert len(store.conversations) == 2

    async def test_daily_rollup_collision_sum_merges(self) -> None:
        day = datetime(2026, 7, 1, tzinfo=timezone.utc)
        store = InMemoryRuntimeApiStore()

        def _row(org_id: str, user_id: str, tokens: int) -> UsageDailyUserRow:
            return UsageDailyUserRow(
                org_id=org_id,
                user_id=user_id,
                day=day,
                model_provider="openai",
                model_name="gpt-5.4-mini",
                runs_count=1,
                input_tokens=tokens,
                output_tokens=tokens,
                cached_input_tokens=0,
                total_tokens=2 * tokens,
                cost_micro_usd=100,
            )

        absorbed_key = (
            self._ABSORBED_ORG,
            self._ABSORBED_USER,
            day.isoformat(),
            "openai",
            "gpt-5.4-mini",
        )
        survivor_key = (
            self._SURVIVOR_ORG,
            self._SURVIVOR_USER,
            day.isoformat(),
            "openai",
            "gpt-5.4-mini",
        )
        store.user_daily_usage[absorbed_key] = _row(
            self._ABSORBED_ORG, self._ABSORBED_USER, 10
        )
        store.user_daily_usage[survivor_key] = _row(
            self._SURVIVOR_ORG, self._SURVIVOR_USER, 5
        )

        rekeyer = self._rekeyer()
        rekeyer.rekey_store(store)

        assert absorbed_key not in store.user_daily_usage
        merged = store.user_daily_usage[survivor_key]
        assert merged.org_id == self._SURVIVOR_ORG
        assert merged.user_id == self._SURVIVOR_USER
        assert merged.input_tokens == 15
        assert merged.total_tokens == 30
        assert merged.runs_count == 2
        assert merged.cost_micro_usd == 200
        assert any("SUM-merged" in warning for warning in rekeyer.warnings)

    async def test_workspace_defaults_survivor_wins(self) -> None:
        store = InMemoryRuntimeApiStore()
        store.workspace_defaults[self._ABSORBED_ORG] = WorkspaceDefaultsRecord(
            org_id=self._ABSORBED_ORG
        )
        survivor_row = WorkspaceDefaultsRecord(org_id=self._SURVIVOR_ORG)
        store.workspace_defaults[self._SURVIVOR_ORG] = survivor_row

        rekeyer = self._rekeyer()
        rekeyer.rekey_store(store)

        assert self._ABSORBED_ORG not in store.workspace_defaults
        assert store.workspace_defaults[self._SURVIVOR_ORG] == survivor_row
        assert any("workspace_defaults" in warning for warning in rekeyer.warnings)
