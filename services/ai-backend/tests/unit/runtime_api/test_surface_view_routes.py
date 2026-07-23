"""``SurfaceViewCoordinator`` — the PRD-B3 view-lifecycle endpoints.

Real ``InMemoryRuntimeApiStore`` + ``RuntimeEventProducer`` (the projector
allow-lists run exactly as in production); a fake ``SpecCompletionPort`` where a
shaping model is needed — never a live model. Pins:

* regenerate returns the re-derived view + a ledger id;
* view-preference appends a ``view.preference`` event with ``actor: user``;
* pinning a tier with no derivation is a 409;
* a cross-tenant / unknown surface is a 404;
* flag-off mirrors A3 gating (no surfaces ⇒ 404, nothing appended);
* the preference survives a store rebuild (reload DoD, server half).
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.api.surface_view_coordinator import SurfaceViewCoordinator
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.ledger_models import ViewKeep
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
    RunRecord,
    RuntimeApiEventType,
)

_FLAG_ON = {"SURFACES_V2": "true"}

_VALID_CANDIDATE: dict[str, object] = {
    "spec_version": 1,
    "archetype": "record",
    "title_path": "issue.title",
}


class _FakeCompletion:
    """Returns a pre-canned valid candidate; never a live model."""

    async def complete(self, *, system: str, user: str):
        from agent_runtime.capabilities.surfaces.generator import SpecCompletionResult

        return SpecCompletionResult(
            candidate=dict(_VALID_CANDIDATE),
            raw_text=json.dumps(_VALID_CANDIDATE),
            model="openai:gpt-5.4-mini",
            input_tokens=100,
            output_tokens=40,
        )


class SurfaceViewMixin:
    ORG = "org_123"
    USER = "user_123"

    async def _setup(
        self,
        *,
        environ: dict[str, str] | None = None,
    ) -> tuple[
        InMemoryRuntimeApiStore, RuntimeEventProducer, SurfaceViewCoordinator, RunRecord
    ]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        model_resolver = ModelConfigResolver(settings)
        producer = RuntimeEventProducer(persistence=store, event_store=store)
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=settings,
            model_resolver=model_resolver,
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=self.ORG, user_id=self.USER, title="Surfaces"
            )
        )
        run_response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=self.ORG,
                user_id=self.USER,
                user_input="Read the issue.",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        coordinator = SurfaceViewCoordinator(
            persistence=store,
            event_store=store,
            event_producer=producer,
            completion=_FakeCompletion(),
            environ=environ if environ is not None else dict(_FLAG_ON),
        )
        return store, producer, coordinator, store.runs[run_response.run_id]

    @staticmethod
    async def _append_surface(
        producer: RuntimeEventProducer,
        run: RunRecord,
        *,
        surface_id: str = "record://linear/get_issue/issue-1",
        connector: str = "linear",
        op: str = "get_issue",
        tier: str = "shaped",
        basis: str = "registry",
    ) -> None:
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": surface_id,
                "kind": "record",
                "source": {"connector": connector, "op": op},
                "title": "ENG-1 Fix",
                "payload_ref": "call:call_1",
            },
        )
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.VIEW_DERIVED,
            payload={
                "v": 1,
                "surface_id": surface_id,
                "tier": tier,
                "basis": basis,
            },
        )

    @staticmethod
    async def _append_content(
        producer: RuntimeEventProducer,
        run: RunRecord,
        *,
        surface_id: str = "record://linear/get_issue/issue-1",
    ) -> None:
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.TOOL_RESULT,
            payload={
                "surface": {
                    "surface_uri": surface_id,
                    "archetype": "record",
                    "state": {"data": {"issue": {"id": "ENG-1", "title": "Fix"}}},
                }
            },
        )


class TestRegenerate(SurfaceViewMixin):
    async def test_regenerate_returns_derivation_and_ledger_id(self) -> None:
        _store, producer, coordinator, run = await self._setup()
        await self._append_surface(producer, run)  # linear/get_issue = builtin hit
        await self._append_content(producer, run)

        response = await coordinator.regenerate_view(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id="record://linear/get_issue/issue-1",
        )

        # A curated builtin spec exists ⇒ shaped/registry, no generation needed.
        assert response.tier.value == "shaped"
        assert response.basis.value == "registry"
        assert response.ledger_id.startswith("r")

    async def test_regenerate_generates_for_non_builtin_and_meters(self) -> None:
        # A non-builtin surface with a stored payload ⇒ the coordinator shapes it
        # (fake completion) and emits a shaped/generated view + a usage.recorded
        # ledger event with purpose=view_shaping.
        store, producer, coordinator, run = await self._setup()
        await self._append_surface(
            producer,
            run,
            surface_id="record://customsrv/custom_tool/x",
            connector="customsrv",
            op="custom_tool",
            tier="generic",
            basis="schema",
        )
        await self._append_content(
            producer, run, surface_id="record://customsrv/custom_tool/x"
        )

        response = await coordinator.regenerate_view(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id="record://customsrv/custom_tool/x",
        )

        assert response.tier.value == "shaped"
        assert response.basis.value == "generated"
        events = await store.list_events_after(
            org_id=self.ORG, run_id=run.run_id, after_sequence=0
        )
        usage = [
            e for e in events if e.event_type == RuntimeApiEventType.USAGE_RECORDED
        ]
        assert usage, "shaping recorded a usage.recorded ledger event"
        assert usage[-1].payload["purpose"] == "view_shaping"
        assert usage[-1].payload["surface_id"] == "record://customsrv/custom_tool/x"

    async def test_unknown_surface_404(self) -> None:
        _store, _producer, coordinator, run = await self._setup()
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.regenerate_view(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id="record://ghost/none/x",
            )
        assert exc.value.http_status == 404

    async def test_cross_tenant_surface_404(self) -> None:
        _store, producer, coordinator, run = await self._setup()
        await self._append_surface(producer, run)
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.regenerate_view(
                org_id=self.ORG,
                user_id="someone_else",
                run_id=run.run_id,
                surface_id="record://linear/get_issue/issue-1",
            )
        assert exc.value.http_status == 404

    async def test_flag_off_matches_a3_gating(self) -> None:
        # SURFACES_V2 off ⇒ no v2 surfaces ⇒ 404, nothing appended (byte-identical).
        store, producer, coordinator, run = await self._setup(environ={})
        await self._append_surface(producer, run)
        before = len(
            await store.list_events_after(
                org_id=self.ORG, run_id=run.run_id, after_sequence=0
            )
        )
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.regenerate_view(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id="record://linear/get_issue/issue-1",
            )
        assert exc.value.http_status == 404
        after = len(
            await store.list_events_after(
                org_id=self.ORG, run_id=run.run_id, after_sequence=0
            )
        )
        assert before == after  # no v2 event appended


class TestViewPreference(SurfaceViewMixin):
    async def test_preference_appends_ledger_event_actor_user(self) -> None:
        store, producer, coordinator, run = await self._setup()
        await self._append_surface(producer, run)

        response = await coordinator.set_view_preference(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id="record://linear/get_issue/issue-1",
            keep=ViewKeep.GENERIC,
        )

        assert response.keep is ViewKeep.GENERIC
        assert response.ledger_id.startswith("r")
        events = await store.list_events_after(
            org_id=self.ORG, run_id=run.run_id, after_sequence=0
        )
        prefs = [
            e for e in events if e.event_type == RuntimeApiEventType.VIEW_PREFERENCE
        ]
        assert len(prefs) == 1
        assert prefs[0].payload["keep"] == "generic"
        assert prefs[0].payload["actor"] == "user"

    async def test_preference_unavailable_tier_409(self) -> None:
        # A surface that only ever derived generic ⇒ pinning shaped is a 409.
        _store, producer, coordinator, run = await self._setup()
        await self._append_surface(
            producer,
            run,
            surface_id="record://unknownsrv/mystery/x",
            connector="unknownsrv",
            op="mystery",
            tier="generic",
            basis="schema",
        )
        with pytest.raises(RuntimeApiError) as exc:
            await coordinator.set_view_preference(
                org_id=self.ORG,
                user_id=self.USER,
                run_id=run.run_id,
                surface_id="record://unknownsrv/mystery/x",
                keep=ViewKeep.SHAPED,
            )
        assert exc.value.http_status == 409

    async def test_replay_after_preference_shows_generic(self) -> None:
        # DoD (reload, server half): the preference is a ledger event, so a fresh
        # fold over the rebuilt event log reproduces the pinned tier.
        store, producer, coordinator, run = await self._setup()
        await self._append_surface(producer, run)  # shaped/registry
        await coordinator.set_view_preference(
            org_id=self.ORG,
            user_id=self.USER,
            run_id=run.run_id,
            surface_id="record://linear/get_issue/issue-1",
            keep=ViewKeep.GENERIC,
        )

        # Rebuild the projection from scratch over the persisted events.
        events = await store.list_events_after(
            org_id=self.ORG, run_id=run.run_id, after_sequence=0
        )
        state = SurfaceStoreProjection.fold(run.run_id, events)
        surface = next(
            s
            for s in state.surfaces
            if s.surface_id == "record://linear/get_issue/issue-1"
        )
        assert surface.view is not None
        assert surface.view.preference == "generic"
