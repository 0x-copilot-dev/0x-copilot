from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.operations.builtin_catalog import (
    BuiltinOperationCatalog,
    BuiltinOperationCatalogEntry,
    BuiltinOperationCatalogError,
    BuiltinOperationExecution,
    BuiltinOperationKind,
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.tree import (
    OperationNodeStatus,
    OperationTreeEvent,
    OperationTreeProjection,
    OperationUsageRecord,
)
from agent_runtime.observability.usage_attribution_edges import (
    InMemoryUsageAttributionEdgeStore,
    UsageAttributionEdge,
    UsageAttributionRelationship,
)
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactIdCodec,
    EffectStageIdCodec,
    OperationIdCodec,
    ProposalUriCodec,
    WorkspaceTargetRefCodec,
)
from agent_runtime.surfaces_v2.ledger_models import EffectClass, LedgerEventType


class TestBuiltinOperationCatalog:
    def test_checked_in_inventory_has_expected_model_visible_tools(self) -> None:
        entries = DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()

        assert {entry.tool_name for entry in entries} == {
            "ask_a_question",
            "auth_mcp",
            "call_mcp_tool",
            "edit",
            "execute",
            "glob",
            "grep",
            "ls",
            "load_mcp_server",
            "load_prior_tool_result",
            "load_skill",
            "publish_artifact",
            "read",
            "run_code_mode",
            "run_in_sandbox",
            "stage_rowset_write",
            "suggest_mcp_connector",
            "task",
            "web_search",
            "write",
            "write_todos",
        }
        publish = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_tool_name(
            "publish_artifact"
        )
        assert publish is not None
        assert publish.key == ("artifact", "publish")
        assert DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*publish.key) is not None
        loader = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_tool_name("load_tool_spec")
        assert loader is not None
        assert DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*loader.key) is not None
        assert all(
            DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*entry.key) is not None
            for entry in entries
        )

    def test_dynamic_or_unknown_tool_uses_existing_safe_default(self) -> None:
        for tool_name in ("dynamic_tool", "unregistered_tool"):
            descriptor = DEFAULT_BUILTIN_OPERATION_CATALOG.descriptor_or_safe_default(
                tool_name=tool_name,
                descriptors=DEFAULT_OPERATION_DESCRIPTORS,
            )

            assert descriptor.descriptor.effect_class is EffectClass.UNKNOWN
            assert descriptor.unknown_arguments_tighten_to_unknown is True

    def test_dynamic_tool_cannot_be_declared_model_visible(self) -> None:
        with pytest.raises(ValidationError, match="loaded descriptors"):
            BuiltinOperationCatalogEntry.model_validate(
                {
                    "tool_name": "unsafe_dynamic_tool",
                    "capability": "dynamic-tool",
                    "op": "invoke",
                    "source": "test",
                    "kind": BuiltinOperationKind.DYNAMIC_TOOL,
                    "execution": BuiltinOperationExecution.GATEWAY,
                    "model_visible": True,
                }
            )

    def test_framework_aliases_resolve_to_the_reviewed_operation(self) -> None:
        for name, key in (
            ("read_file", ("workspace", "read")),
            ("write_file", ("workspace", "write")),
            ("edit_file", ("workspace", "edit")),
        ):
            entry = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_model_tool_name(name)

            assert entry is not None
            assert entry.key == key

    def test_duplicate_framework_alias_fails_closed(self) -> None:
        first = BuiltinOperationCatalogEntry(
            tool_name="one",
            model_tool_aliases=("framework_tool",),
            capability="builtin",
            op="one",
            source="test",
            kind=BuiltinOperationKind.BUILTIN,
            execution=BuiltinOperationExecution.PURE,
            model_visible=True,
        )
        second = first.model_copy(update={"tool_name": "two", "op": "two"})

        with pytest.raises(BuiltinOperationCatalogError, match="model-visible"):
            BuiltinOperationCatalog((first, second))

    def test_duplicate_catalog_identity_fails_closed(self) -> None:
        entry = BuiltinOperationCatalogEntry(
            tool_name="one",
            capability="builtin",
            op="one",
            source="test",
            kind=BuiltinOperationKind.BUILTIN,
            execution=BuiltinOperationExecution.PURE,
            model_visible=True,
        )

        with pytest.raises(BuiltinOperationCatalogError, match="duplicate builtin"):
            BuiltinOperationCatalog(
                (entry, entry.model_copy(update={"tool_name": "two"}))
            )


class TestUsageAttributionEdges:
    def test_edges_are_immutable_and_append_idempotently(self) -> None:
        edge = UsageAttributionEdge(
            usage_record_id="usage-1",
            operation_id=_operation_id(),
            artifact_id=_artifact_id(),
            relationship=UsageAttributionRelationship.PRODUCED,
        )
        store = InMemoryUsageAttributionEdgeStore()

        assert store.append(edge) is True
        assert store.append(edge) is False
        assert store.list_for_operation(edge.operation_id) == (edge,)
        with pytest.raises(ValidationError):
            edge.artifact_id = _artifact_id()  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("relationship", "artifact_id", "stage_id", "error"),
        [
            (UsageAttributionRelationship.PRODUCED, None, None, "requires artifact_id"),
            (UsageAttributionRelationship.REVISED, None, None, "requires artifact_id"),
            (UsageAttributionRelationship.SHAPED, None, None, "requires artifact_id"),
            (UsageAttributionRelationship.PROPOSED, None, None, "requires stage_id"),
        ],
    )
    def test_edges_require_a_real_immutable_target(
        self,
        relationship: UsageAttributionRelationship,
        artifact_id: str | None,
        stage_id: str | None,
        error: str,
    ) -> None:
        with pytest.raises(ValidationError, match=error):
            UsageAttributionEdge(
                usage_record_id="usage-1",
                operation_id=_operation_id(),
                artifact_id=artifact_id,
                stage_id=stage_id,
                relationship=relationship,
            )


class TestOperationTreeProjection:
    def test_replay_is_deterministic_and_deduplicates_usage_by_record(self) -> None:
        operation_id = _operation_id()
        stage_id = _stage_id()
        artifact_id = _artifact_id()
        started_at = datetime(2026, 7, 25, 9, 30, tzinfo=UTC)
        events = (
            _event(
                sequence_no=4,
                event_type=LedgerEventType.OPERATION_COMPLETED,
                occurred_at=started_at + timedelta(seconds=3),
                payload={
                    "v": 1,
                    "operation_id": operation_id,
                    "outcome": "staged",
                },
            ),
            _event(
                sequence_no=2,
                event_type=LedgerEventType.OPERATION_CLASSIFIED,
                occurred_at=started_at + timedelta(seconds=1),
                payload={
                    "v": 1,
                    "operation_id": operation_id,
                    "effect_class": "internal_reversible",
                    "basis": "descriptor",
                    "confidence": 1.0,
                },
            ),
            _event(
                sequence_no=3,
                event_type=LedgerEventType.EFFECT_STAGED,
                occurred_at=started_at + timedelta(seconds=2),
                payload={
                    "v": 1,
                    "stage_id": stage_id,
                    "operation_id": operation_id,
                    "executor": "workspace",
                    "target_ref": WorkspaceTargetRefCodec.format("grant", "token"),
                    "target_digest": "a" * 64,
                    "proposal_ref": ProposalUriCodec.format(stage_id, 1),
                    "proposal_digest": "b" * 64,
                    "policy": "ask",
                },
            ),
            _event(
                sequence_no=1,
                event_type=LedgerEventType.OPERATION_REQUESTED,
                occurred_at=started_at,
                payload={
                    "v": 1,
                    "operation_id": operation_id,
                    "producer": "model",
                    "capability": "artifact",
                    "op": "publish",
                    "args_digest": "c" * 64,
                },
            ),
        )
        usage = OperationUsageRecord(
            usage_record_id="usage-1",
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
            cost_micro_usd=23,
        )
        edges = (
            UsageAttributionEdge(
                usage_record_id=usage.usage_record_id,
                operation_id=operation_id,
                artifact_id=artifact_id,
                relationship=UsageAttributionRelationship.PRODUCED,
            ),
            UsageAttributionEdge(
                usage_record_id=usage.usage_record_id,
                operation_id=operation_id,
                stage_id=stage_id,
                relationship=UsageAttributionRelationship.PROPOSED,
            ),
        )

        forward = OperationTreeProjection.fold(
            events,
            attribution_edges=edges,
            usage_records=(usage,),
        )
        replayed = OperationTreeProjection.fold(
            tuple(reversed(events)),
            attribution_edges=tuple(reversed(edges)),
            usage_records=(usage,),
        )

        assert forward == replayed
        assert len(forward.nodes) == 1
        node = forward.nodes[0]
        assert node.status is OperationNodeStatus.STAGED
        assert node.artifact_ids == (artifact_id,)
        assert node.stage_ids == (stage_id,)
        assert node.usage_totals.model_dump() == {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "cost_micro_usd": 23,
        }

    def test_invalid_or_orphaned_events_do_not_invent_operation_state(self) -> None:
        tree = OperationTreeProjection.fold(
            (
                _event(
                    sequence_no=1,
                    event_type=LedgerEventType.OPERATION_COMPLETED,
                    occurred_at=datetime(2026, 7, 25, tzinfo=UTC),
                    payload={
                        "v": 1,
                        "operation_id": _operation_id(),
                        "outcome": "succeeded",
                    },
                ),
                _event(
                    sequence_no=2,
                    event_type=LedgerEventType.OPERATION_REQUESTED,
                    occurred_at=datetime(2026, 7, 25, tzinfo=UTC),
                    payload={"v": 1, "operation_id": "not-an-operation-id"},
                ),
            )
        )

        assert tree.nodes == ()


def _event(
    *,
    sequence_no: int,
    event_type: LedgerEventType,
    occurred_at: datetime,
    payload: dict[str, object],
) -> OperationTreeEvent:
    return OperationTreeEvent(
        sequence_no=sequence_no,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
    )


def _operation_id() -> str:
    return OperationIdCodec.format(uuid4())


def _artifact_id() -> str:
    return ArtifactIdCodec.format(uuid4())


def _stage_id() -> str:
    return EffectStageIdCodec.format(uuid4())
