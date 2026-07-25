"""D9 authoritative lifecycle-reference snapshot collector tests."""

from __future__ import annotations

import json

import pytest

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.persistence.records import (
    LegalHoldReasonCode,
    LegalHoldRecord,
    LegalHoldScope,
)
from agent_runtime.surfaces_v2.lifecycle_reference_snapshots import (
    LifecycleReferenceConformanceError,
    LifecycleReferenceConformanceGate,
    LifecycleReferenceCoverageReason,
    LifecycleReferenceCoverageState,
    LifecycleReferenceFamily,
    LifecycleReferenceSnapshotCollector,
    LifecycleReferenceSnapshotError,
    LifecycleReferenceSnapshotScope,
    default_lifecycle_reference_owner_capabilities,
)
from agent_runtime.surfaces_v2.lifecycle_refs import (
    LifecycleReferenceOwner,
    LifecycleReferenceRegistry,
)
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord, RuntimeApiEventType

_ORG = "org_lifecycle"
_USER = "user_lifecycle"
_CONVERSATION = "conv_lifecycle"
_RUN = "run_lifecycle"
_ARTIFACT = "art_018f47a6-7b2c-7b10-8f21-12345678b002"


def _run() -> RunRecord:
    return RunRecord(
        run_id=_RUN,
        conversation_id=_CONVERSATION,
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_lifecycle",
        trace_id="trace_lifecycle",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_lifecycle",
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


async def _seed_artifact_event(
    store: InMemoryRuntimeApiStore,
    *,
    surface_id: str | None = None,
) -> None:
    run = _run()
    store.runs[run.run_id] = run
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.ARTIFACT_CREATED,
        payload={
            "v": 1,
            "artifact_id": _ARTIFACT,
            "kind": "document",
            "revision": 1,
            "content_ref": f"artifact://{_ARTIFACT}/revisions/1",
            "content_digest": "a" * 64,
            "author": "model",
        },
    )
    if surface_id is not None:
        await producer.append_api_event(
            run=run,
            source=StreamEventSource.SYSTEM,
            event_type=RuntimeApiEventType.SURFACE_CREATED,
            payload={
                "v": 1,
                "surface_id": surface_id,
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue"},
                "title": "safe title",
                "payload_ref": "call:call_lifecycle",
            },
        )


def _scope(**updates: object) -> LifecycleReferenceSnapshotScope:
    values: dict[str, object] = {
        "org_id": _ORG,
        "user_id": _USER,
        "conversation_id": _CONVERSATION,
        "run_id": _RUN,
    }
    values.update(updates)
    return LifecycleReferenceSnapshotScope(**values)


class TestLifecycleReferenceConformanceGate:
    def test_shipped_registry_and_every_owner_strategy_are_launch_conformant(
        self,
    ) -> None:
        LifecycleReferenceConformanceGate.validate_current()

    def test_missing_owner_capability_fails_closed(self) -> None:
        capabilities = default_lifecycle_reference_owner_capabilities()
        without_browser = tuple(
            item
            for item in capabilities
            if item.owner is not LifecycleReferenceOwner.BROWSER_AUTHORITY
        )

        with pytest.raises(LifecycleReferenceConformanceError):
            LifecycleReferenceConformanceGate.validate(
                registry=LifecycleReferenceRegistry.default(),
                capabilities=without_browser,
            )

    def test_runtime_api_launches_only_after_static_gate_and_composes_collector(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        app = RuntimeApiAppFactory.create_app(
            ports=RuntimeAdapterFactory.from_store(store),
            settings=RuntimeSettings.load(
                environ={
                    "OPENAI_API_KEY": "sk-test",
                    "RUNTIME_DEFAULT_PROVIDER": "openai",
                    "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                }
            ),
        )

        assert isinstance(
            app.state.lifecycle_reference_snapshot_collector,
            LifecycleReferenceSnapshotCollector,
        )
        assert not any(
            "lifecycle-reference" in getattr(route, "path", "") for route in app.routes
        )


class TestLifecycleReferenceSnapshotCollector:
    async def test_collects_real_event_artifact_and_legal_hold_folds_without_raw_values(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_artifact_event(
            store,
            surface_id=f"artifact-document://{_ARTIFACT}@1",
        )
        await store.create_legal_hold(
            record=LegalHoldRecord(
                org_id=_ORG,
                scope=LegalHoldScope.USER,
                resource_id="user_hold_private_target",
                subject_user_id="user_hold_private_target",
                reason_code=LegalHoldReasonCode.LEGAL_REQUEST,
                created_by_user_id="user_retention_admin",
                create_idempotency_key="hold-create-001",
                create_request_digest="b" * 64,
            ),
            audit_event={"event": "legal_hold.created"},
        )

        snapshot = await LifecycleReferenceSnapshotCollector(
            event_store=store,
            persistence=store,
        ).collect(scope=_scope())

        artifact = next(
            item
            for item in snapshot.owners
            if item.owner is LifecycleReferenceOwner.ARTIFACT_REPOSITORY
        )
        assert artifact.state is LifecycleReferenceCoverageState.COVERED
        assert artifact.artifact_revisions[0].content_ref.reference == (
            f"artifact://{_ARTIFACT}/revisions/1"
        )
        assert artifact.artifact_revisions[0].blob_ref.reference == (
            "artifact-blob://sha256/" + "a" * 64
        )
        assert snapshot.run_context.conversation_id == _CONVERSATION
        assert snapshot.run_context.run_id == _RUN
        assert (
            snapshot.run_context.user_message_ref.reference == "message://msg_lifecycle"
        )
        workspace = next(
            item
            for item in snapshot.owners
            if item.owner is LifecycleReferenceOwner.WORKSPACE_AUTHORITY
        )
        assert workspace.state is LifecycleReferenceCoverageState.UNAVAILABLE
        assert (
            workspace.reason is LifecycleReferenceCoverageReason.OWNER_NOT_MATERIALIZED
        )
        artifact_family = next(
            item
            for item in snapshot.families
            if item.family is LifecycleReferenceFamily.ARTIFACTS_REVISIONS_BLOBS
        )
        # Artifact metadata/revisions fold from the ledger, but artifact-blob
        # is a workspace-owned ref scheme. The combined family stays
        # unavailable until workspace has a bounded authoritative inventory.
        assert artifact_family.state is LifecycleReferenceCoverageState.UNAVAILABLE
        assert artifact_family.owner is LifecycleReferenceOwner.WORKSPACE_AUTHORITY
        assert snapshot.legal_holds.active_count == 1
        assert snapshot.complete is False
        with pytest.raises(LifecycleReferenceSnapshotError):
            snapshot.assert_complete()
        with pytest.raises(LifecycleReferenceSnapshotError):
            snapshot.assert_no_active_legal_holds()

        serialized = json.dumps(snapshot.model_dump(mode="json"))
        assert "user_hold_private_target" not in serialized
        assert "safe title" not in serialized
        assert "content_digest" not in serialized

    async def test_event_window_truncation_is_withheld_not_partial_success(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_artifact_event(
            store, surface_id=f"artifact-document://{_ARTIFACT}@1"
        )
        collector = LifecycleReferenceSnapshotCollector(
            event_store=store,
            persistence=store,
        )

        snapshot = await collector.collect(scope=_scope(event_limit=1))

        assert snapshot.graph is None
        assert snapshot.next_after_sequence == 1
        runtime = next(
            item
            for item in snapshot.owners
            if item.owner is LifecycleReferenceOwner.RUNTIME_EVENT_STORE
        )
        assert runtime.state is LifecycleReferenceCoverageState.WITHHELD
        assert runtime.reason is LifecycleReferenceCoverageReason.EVENT_WINDOW_TRUNCATED
        assert all(item.nodes == () for item in snapshot.owners)

    async def test_unknown_uri_like_surface_id_refuses_snapshot_without_leaking_raw_value(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_artifact_event(store, surface_id="data:secret-private-surface")

        with pytest.raises(LifecycleReferenceSnapshotError) as raised:
            await LifecycleReferenceSnapshotCollector(
                event_store=store,
                persistence=store,
            ).collect(scope=_scope())

        diagnostic_json = json.dumps(
            [item.model_dump(mode="json") for item in raised.value.diagnostics]
        )
        assert "secret" not in diagnostic_json
        assert "private" not in diagnostic_json

    async def test_missing_bounded_event_capability_is_explicitly_unavailable(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_artifact_event(store)

        snapshot = await LifecycleReferenceSnapshotCollector(
            event_store=object(),
            persistence=store,
        ).collect(scope=_scope())

        runtime = next(
            item
            for item in snapshot.owners
            if item.owner is LifecycleReferenceOwner.RUNTIME_EVENT_STORE
        )
        assert runtime.state is LifecycleReferenceCoverageState.UNAVAILABLE
        assert (
            runtime.reason is LifecycleReferenceCoverageReason.EVENT_WINDOW_UNAVAILABLE
        )
        assert snapshot.graph is None
        assert (
            next(
                item
                for item in snapshot.families
                if item.family is LifecycleReferenceFamily.EFFECT_CLAIMS
            ).reason
            is LifecycleReferenceCoverageReason.CLAIM_INVENTORY_UNAVAILABLE
        )

    async def test_scope_mismatch_fails_without_confirming_foreign_run_details(
        self,
    ) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_artifact_event(store)

        with pytest.raises(LifecycleReferenceSnapshotError) as raised:
            await LifecycleReferenceSnapshotCollector(
                event_store=store,
                persistence=store,
            ).collect(scope=_scope(user_id="user_other"))

        assert (
            raised.value.diagnostics[0].code.value == "run_not_found_or_not_authorized"
        )
        assert _RUN not in str(raised.value)
        assert _USER not in str(raised.value)

    async def test_in_memory_adapter_window_is_bounded_and_tenant_scoped(self) -> None:
        store = InMemoryRuntimeApiStore()
        await _seed_artifact_event(
            store, surface_id=f"artifact-document://{_ARTIFACT}@1"
        )

        page = await store.list_lifecycle_reference_events_window(
            org_id=_ORG,
            run_id=_RUN,
            after_sequence=0,
            limit=1,
        )
        assert len(page.events) == 1
        assert page.has_more is True
        assert page.next_after_sequence == 1
        foreign = await store.list_lifecycle_reference_events_window(
            org_id="org_other",
            run_id=_RUN,
            after_sequence=0,
            limit=1,
        )
        assert foreign.events == ()
        assert foreign.has_more is False
