"""Conformance tests for immutable usage-attribution edge adapters."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.persistence.records import (
    RuntimeModelCallUsageRecord,
    UsageAttributionEdge,
    UsageAttributionRelationship,
)
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore

_CREATED_AT = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
_ORG_A = "org-a"
_ORG_B = "org-b"


def _usage_record(
    *, usage_id: str, org_id: str = _ORG_A
) -> RuntimeModelCallUsageRecord:
    return RuntimeModelCallUsageRecord(
        id=usage_id,
        org_id=org_id,
        run_id="run-a",
        conversation_id="conversation-a",
        trace_id="trace-a",
        user_id="user-a",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
        duration_ms=15,
        cost_micro_usd=29,
        created_at=_CREATED_AT,
    )


def _produced_edge(*, usage_id: str, edge_id: str) -> UsageAttributionEdge:
    return UsageAttributionEdge(
        edge_id=edge_id,
        usage_record_id=usage_id,
        operation_id="operation-a",
        artifact_id="artifact-a",
        relationship=UsageAttributionRelationship.PRODUCED,
        created_at=_CREATED_AT,
    )


class TestInMemoryUsageAttributionEdges:
    async def test_append_is_immutable_idempotent_and_tenant_scoped(self) -> None:
        store = InMemoryRuntimeApiStore()
        first_usage = _usage_record(usage_id="usage-first")
        retry_usage = _usage_record(usage_id="usage-retry")
        foreign_usage = _usage_record(usage_id="usage-foreign", org_id=_ORG_B)
        await store.record_model_call_usage(first_usage)
        await store.record_model_call_usage(retry_usage)
        await store.record_model_call_usage(foreign_usage)

        produced = _produced_edge(usage_id=first_usage.id, edge_id="edge-produced")
        proposed = UsageAttributionEdge(
            edge_id="edge-proposed",
            usage_record_id=first_usage.id,
            operation_id="operation-a",
            stage_id="stage-a",
            relationship=UsageAttributionRelationship.PROPOSED,
            created_at=_CREATED_AT,
        )
        retry = _produced_edge(usage_id=retry_usage.id, edge_id="edge-retry")

        assert await store.append_usage_attribution_edge(org_id=_ORG_A, edge=produced)
        # A redelivered edge with a different transport ID has the same natural
        # identity and is not persisted twice.
        assert not await store.append_usage_attribution_edge(
            org_id=_ORG_A,
            edge=produced.model_copy(update={"edge_id": "edge-produced-retry"}),
        )
        assert await store.append_usage_attribution_edge(org_id=_ORG_A, edge=proposed)
        # A provider retry is a distinct canonical usage record and therefore a
        # distinct edge, even with the same operation and artifact.
        assert await store.append_usage_attribution_edge(org_id=_ORG_A, edge=retry)

        edges = await store.list_usage_attribution_edges_for_usage_records(
            org_id=_ORG_A,
            usage_record_ids=(first_usage.id, retry_usage.id),
        )
        assert {edge.edge_id for edge in edges} == {
            "edge-produced",
            "edge-proposed",
            "edge-retry",
        }
        assert (
            sum(
                record.total_tokens
                for record in store.model_call_usage
                if record.org_id == _ORG_A
            )
            == 36
        )
        assert (
            sum(
                record.cost_micro_usd or 0
                for record in store.model_call_usage
                if record.org_id == _ORG_A
            )
            == 58
        )

        with pytest.raises(LookupError, match="in-tenant usage record"):
            await store.append_usage_attribution_edge(
                org_id=_ORG_A,
                edge=_produced_edge(usage_id=foreign_usage.id, edge_id="edge-foreign"),
            )
        assert (
            await store.list_usage_attribution_edges_for_usage_records(
                org_id=_ORG_B,
                usage_record_ids=(first_usage.id, retry_usage.id),
            )
            == ()
        )

        with pytest.raises(ValidationError):
            produced.artifact_id = "artifact-mutated"  # type: ignore[misc]


class TestFileUsageAttributionEdges:
    async def test_reopen_preserves_append_only_edges_and_natural_dedupe(
        self, tmp_path
    ) -> None:
        root = tmp_path / "usage-attribution-store"
        store = FileRuntimeApiStore(root)
        await store.open()
        usage = _usage_record(usage_id="usage-file")
        edge = _produced_edge(usage_id=usage.id, edge_id="edge-file")
        await store.record_model_call_usage(usage)
        assert await store.append_usage_attribution_edge(org_id=_ORG_A, edge=edge)
        await store.close()

        reopened = FileRuntimeApiStore(root)
        await reopened.open()
        edges = await reopened.list_usage_attribution_edges_for_usage_records(
            org_id=_ORG_A,
            usage_record_ids=(usage.id,),
        )
        assert edges == (edge,)
        assert not await reopened.append_usage_attribution_edge(
            org_id=_ORG_A,
            edge=edge.model_copy(update={"edge_id": "edge-file-redelivery"}),
        )
        assert len(reopened.model_call_usage) == 1
        assert reopened.model_call_usage[0] == usage
        await reopened.close()
