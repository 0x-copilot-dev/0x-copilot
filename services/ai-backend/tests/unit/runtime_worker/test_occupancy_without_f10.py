"""The Context Occupancy Ledger records on a DEFAULT deployment (design §3.1).

This is the test whose absence let the ledger ship inert. Every other occupancy
test injects an F10 binding or a sink directly, so all of them passed against a
seam that recorded nothing in production: ``FeatureModeSet.f10`` ships ``OFF``,
``ModelInvocationComposer.compose`` therefore returns ``None``, and
``ModelInvocationMiddleware.awrap_model_call`` took its no-binding early return
before any capture ran. 8,916 unit tests, three adversarial review lenses and a
mutation-verifying confirmation pass all went green over that.

So this test injects nothing. It drives a real queued run through the real
worker, the real Deep Agents graph and the real streaming executor — only the
concrete chat model is the deterministic fake — with F10 left at its shipped
default, and asserts a row lands. Reverting the un-gating fix makes it fail.

It deliberately asserts on the *durable row*, not on a spy: the failure being
guarded against was that nothing reached the store, which a call-count
assertion on a collaborator would not have caught either.
"""

from __future__ import annotations

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.control_plane.feature_modes import FeatureMode, FeatureModeSet
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.observability.context_occupancy import GraphScope
from agent_runtime.observability.context_origin import UNDECLARED_CONTEXT_LABEL
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest
from runtime_worker.dependencies import DefaultRuntimeDependenciesFactory
from runtime_worker.loop import RuntimeWorker


class OccupancyWithoutF10Mixin:
    """Drive one real run under the shipped feature-mode defaults."""

    ORG_ID = "org_occupancy"
    USER_ID = "user_occupancy"

    @staticmethod
    def settings() -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_MAX_RETRIES": "1",
                "RUNTIME_MAX_PARALLEL_RUNS": "2",
            }
        )

    @classmethod
    async def drive_run(cls, store: InMemoryRuntimeApiStore) -> str:
        cfg = cls.settings()
        producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        runs = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=cfg,
            model_resolver=ModelConfigResolver(cfg),
        )
        conversations = ConversationCoordinator(
            persistence=store, settings=cfg, run_coordinator=runs
        )
        conversation = await conversations.create_conversation(
            CreateConversationRequest(
                org_id=cls.ORG_ID, user_id=cls.USER_ID, assistant_id="assistant_1"
            )
        )
        created = await runs.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=cls.ORG_ID,
                user_id=cls.USER_ID,
                user_input="Say hello.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        worker = RuntimeWorker(
            persistence=store,
            event_store=store,
            queue=store,
            settings=cfg,
            mcp_discovery_cache=(
                DefaultRuntimeDependenciesFactory.build_default_discovery_cache()
            ),
        )
        processed = await worker.run_until_idle()
        assert processed == 1, "the run did not execute"
        return created.run_id


class TestF10IsOffByDefault:
    """Pins the premise. If this ever fails, the test below stops proving anything."""

    def test_the_shipped_default_leaves_f10_off(self) -> None:
        # The whole point of the test below is that occupancy works with F10 at
        # its DEFAULT. If a later change turns F10 on by default, this fails and
        # forces someone to re-establish coverage of the F10-off path rather than
        # letting it silently stop being exercised.
        assert FeatureModeSet().f10 is FeatureMode.OFF


class TestOccupancyRecordsWithoutF10(OccupancyWithoutF10Mixin):
    async def test_a_default_deployment_persists_an_occupancy_row(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()

        run_id = await self.drive_run(store)

        rows = await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id)
        assert rows, (
            "no occupancy row was persisted on a default deployment — the ledger "
            "is inert, which is the exact regression this test exists to catch"
        )

    async def test_the_row_carries_a_real_decomposition(self, monkeypatch) -> None:
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()

        run_id = await self.drive_run(store)
        row = (await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id))[0]

        # Tenant and scope are the two fields a reader cannot recover later.
        assert row.org_id == self.ORG_ID
        assert row.graph_scope == GraphScope.ROOT.value
        assert row.estimated_input_tokens > 0

        segments = (row.segments_json or {}).get("segments") or []
        assert segments, "a row with no segments is a total, not an attribution"

        # Attribution is real, not just an UNDECLARED bucket with a total on it.
        # Our own composed tools must resolve to their declared owners.
        declared = {
            str(segment.get("label"))
            for segment in segments
            if str(segment.get("label")) != UNDECLARED_CONTEXT_LABEL
        }
        assert any(label.startswith("agent_runtime.") for label in declared), (
            f"no first-party origin attributed; labels={sorted(declared)}"
        )

    async def test_no_provider_reconciliation_is_claimed_without_f10(
        self, monkeypatch
    ) -> None:
        # Honesty check on the documented limit. There is no
        # ``_ProviderLifecycleCallback`` outside F10, so the row must leave
        # ``provider_input_tokens`` unset rather than inventing one — and
        # ``unattributed_delta`` must stay 0 rather than reporting the entire
        # estimate as drift against a total nobody reported.
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()

        run_id = await self.drive_run(store)
        row = (await store.list_context_occupancy(org_id=self.ORG_ID, run_id=run_id))[0]

        assert row.provider_input_tokens is None
        assert row.unattributed_delta == 0

    async def test_the_run_still_completes_when_the_sink_raises(
        self, monkeypatch
    ) -> None:
        # §6.4 on the newly-live path. The un-gating means this code now runs on
        # every model call of every deployment, so its fail-open property is no
        # longer theoretical.
        monkeypatch.setenv("RUNTIME_FAKE_MODEL", "1")
        store = InMemoryRuntimeApiStore()

        async def exploding_append(*args: object, **kwargs: object) -> None:
            raise RuntimeError("the occupancy store is down")

        monkeypatch.setattr(store, "append_context_occupancy", exploding_append)

        run_id = await self.drive_run(store)

        names = [event.event_type for event in store.events_by_run[run_id]]
        assert "run_failed" not in names, names
        assert "run_completed" in names, names
